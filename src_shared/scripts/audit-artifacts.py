#!/usr/bin/env python3
"""Build and validate Scope's lean audit attempt, findings, and report."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

import yaml

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import scope_fingerprint  # noqa: E402
import scope_git  # noqa: E402


import scope_snapshot

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
VALIDATION_PHASES = ("pre_review", "complete")
RECEIPT_TOP_STATUSES = {"completed", "failed", "canceled"}
RECEIPT_ROW_STATUSES = {
    "completed",
    "preflight_failed",
    "not_launched_preflight_barrier",
    "launch_failed",
    "infrastructure_failed_before_review",
    "provider_failed",
    "rate_quota_exhausted",
    "timed_out",
    "canceled",
    "invalid_output",
}


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "audit-policy.yaml"


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping: {path}")
    return value


def _atomic_write_yaml_documents(documents: Sequence[tuple[Path, Mapping[str, Any]]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for path, document in documents:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            )
            temp_path = Path(handle.name)
            try:
                yaml.safe_dump(dict(document), handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            staged.append((temp_path, path))
        for temp_path, path in staged:
            os.replace(temp_path, path)
    finally:
        for temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temp_path, path)
    finally:
        if not handle.closed:
            handle.close()
        temp_path.unlink(missing_ok=True)


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _structured_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _yaml_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True).encode("utf-8")
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return ""
    return value


def _string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        errors.append(f"{label} must be a list of non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label} contains duplicate values")
    return list(value)


def _mapping_list(value: Any, label: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        errors.append(f"{label} must be a list of mappings")
        return []
    return list(value)


def _relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise ValueError(f"{label} must use repository-relative '/' separators")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError(f"{label} must be a normalized relative path: {value!r}")
    return value


def _inside(path: Path, root: Path, label: str, *, must_exist: bool = True) -> Path:
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"cannot resolve {label} {path}: {exc}") from exc
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes working root: {path}") from exc
    return resolved


def _repo_file(repo_root: Path, relative: str, label: str) -> Path:
    normalized = _relative(relative, label)
    path = _inside(repo_root / normalized, repo_root, label)
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {relative}")
    return path


def _epic_file(epic_dir: Path, relative: str, label: str) -> Path:
    normalized = _relative(relative, label)
    path = _inside(epic_dir / normalized, epic_dir, label)
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {relative}")
    return path


def _repo_relative(path: Path, repo_root: Path, label: str) -> str:
    return _inside(path, repo_root, label).relative_to(repo_root.resolve()).as_posix()


def _repo_target_relative(path: Path, repo_root: Path, label: str) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes working root: {path}") from exc


def _policy(path: Path) -> dict[str, Any]:
    policy = _load_yaml(path, "audit policy")
    if policy.get("schema_version") != 3:
        raise ValueError("audit policy schema_version must be 3")
    return policy


def _refinement_handoff_errors(epic_dir: Path, repo_root: Path) -> list[str]:
    script = Path(__file__).resolve().with_name("validate-refinement.py")
    spec = importlib.util.spec_from_file_location("scope_validate_refinement", script)
    if spec is None or spec.loader is None:
        return [f"cannot load refinement validator: {script}"]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        errors = module.RefinementValidator(
            epic_dir,
            "handoff",
            script.parent.parent / "config" / "refinement-policy.yaml",
            repo_root,
        ).validate()
    except (OSError, ValueError) as exc:
        return [f"cannot validate refinement handoff: {exc}"]
    return [f"refinement handoff: {error}" for error in errors]


def _worker_module() -> Any:
    script = Path(__file__).resolve().with_name("scope-worker.py")
    spec = importlib.util.spec_from_file_location("scope_audit_worker", script)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load implementation evidence verifier: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_location(root: Path, argument: str, label: str) -> Path:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", argument],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"worker run {label} is not a Git checkout: {root}")
    raw = Path(result.stdout.strip())
    return (raw if raw.is_absolute() else root / raw).resolve(strict=True)


def _run_roots(run: Mapping[str, Any]) -> tuple[Path, Path]:
    roots: dict[str, Path] = {}
    for field in ("repository_root", "working_root"):
        value = run.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"worker run {field} must be an absolute path")
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"worker run {field} must name a directory")
        if _git_location(path, "--show-toplevel", field) != path:
            raise ValueError(f"worker run {field} is not a Git checkout root")
        roots[field] = path
    if _git_location(
        roots["repository_root"], "--git-common-dir", "repository_root"
    ) != _git_location(roots["working_root"], "--git-common-dir", "working_root"):
        raise ValueError("worker run working_root is not a worktree of repository_root")
    return roots["repository_root"], roots["working_root"]


def _validate_scope_binding(run: Mapping[str, Any]) -> None:
    scope_value = run.get("scope_root")
    if not isinstance(scope_value, str) or not Path(scope_value).is_absolute():
        raise ValueError("worker run scope_root must be an absolute path")
    scope_root = Path(scope_value).resolve(strict=True)
    if not scope_root.is_dir():
        raise ValueError("worker run scope_root must name a directory")
    policy_path = scope_root / "config" / "worker-policy.yaml"
    if not policy_path.is_file():
        raise ValueError("worker run scope_root has no worker policy")
    if run.get("worker_policy_sha256") != _file_sha256(policy_path):
        raise ValueError("worker policy changed since run initialization")
    if run.get("worker_profile") not in {"default", "budget"}:
        raise ValueError("worker run has invalid worker_profile")


def _same_epic_id(left: Any, right: Any) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.strip().casefold() == right.strip().casefold()
    )


def _selected_epic_dir(epic_dir: Path, working_root: Path, epic_id: str) -> Path:
    epic_root = (working_root / "docs" / "epics").resolve(strict=True)
    if epic_dir.is_symlink():
        raise ValueError("selected epic directory must not be a symlink")
    selected = epic_dir.resolve(strict=True)
    if not selected.is_dir() or selected.parent != epic_root:
        raise ValueError("selected epic directory must be a direct child of docs/epics")
    candidates = [
        path.resolve(strict=True)
        for path in epic_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and epic_id.casefold() in path.name.casefold()
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"epic directory resolver is ambiguous for {epic_id}: {len(candidates)} matches"
        )
    if candidates[0] != selected:
        raise ValueError("selected epic directory is foreign to the worker run epic_id")
    return selected


@contextmanager
def _mutation_guard(
    run_path: Path, epic_dir: Path
) -> Iterator[tuple[dict[str, Any], Path, Path]]:
    resolved_run = run_path.resolve(strict=True)
    run = _load_yaml(resolved_run, "worker run")
    if run.get("schema_version") != 2:
        raise ValueError("worker run schema_version must be 2")
    if run.get("command") != "audit_epic":
        raise ValueError("audit mutation requires an audit_epic run")
    if run.get("active_job") is not None or "active_job" not in run:
        raise ValueError("worker run has a recorded active job")
    if not isinstance(run.get("completed_jobs"), list):
        raise ValueError("worker run completed_jobs must be a list")
    repository_root, working_root = _run_roots(run)
    _validate_scope_binding(run)
    epic_id = run.get("epic_id")
    if not isinstance(epic_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", epic_id
    ):
        raise ValueError("worker run epic_id is invalid")
    expected_run = (
        repository_root
        / "tmp_debug"
        / "scope-runs"
        / epic_id
        / "audit_epic"
        / "run.yaml"
    ).resolve(strict=True)
    if resolved_run != expected_run:
        raise ValueError(f"worker run path must be {expected_run}")
    selected_epic = _selected_epic_dir(epic_dir, working_root, epic_id)
    manifest_path = selected_epic / "delivery-manifest.yaml"
    if manifest_path.is_file():
        manifest = _load_yaml(manifest_path, "delivery manifest")
        if not _same_epic_id(run.get("epic_id"), manifest.get("epic_id")):
            raise ValueError("worker run epic_id does not match selected epic")
    state_path = selected_epic / "refinement-state.yaml"
    if state_path.is_file():
        state = _load_yaml(state_path, "refinement state")
        if not _same_epic_id(run.get("epic_id"), state.get("epic_id")):
            raise ValueError("worker run epic_id does not match refinement state")
    with scope_git.mutation_locks([working_root]):
        yield run, working_root, repository_root


def repository_fingerprint(epic_dir: Path, repo_root: Path) -> dict[str, Any]:
    return scope_fingerprint.audit_fingerprint(epic_dir, repo_root)


def _artifact_boundary(epic_dir: Path, repo_root: Path) -> tuple[dict[str, str], str]:
    paths: list[Path] = [
        _epic_file(epic_dir, name, "audit boundary artifact")
        for name in (
            "details.md",
            "acceptance-criteria.md",
            "design.md",
            "delivery-manifest.yaml",
            "implementation-evidence.yaml",
        )
    ]
    for name in ("refinement-state.yaml", "refinement-findings.yaml", "refinement-review.md"):
        if (epic_dir / name).is_file():
            paths.append(_epic_file(epic_dir, name, "authoritative refinement artifact"))
    manifest = _load_yaml(epic_dir / "delivery-manifest.yaml", "delivery manifest")
    for row in manifest.get("artifact_ownership", []):
        if not isinstance(row, dict) or row.get("authority") not in {"canonical", "evidence"}:
            continue
        relative = row.get("path")
        if not isinstance(relative, str):
            continue
        if relative in {"audit-findings.yaml", "epic_audit.md"} or relative.startswith("reviews/audit-"):
            continue
        paths.append(_epic_file(epic_dir, relative, "manifest-owned audit input"))
    for row in manifest.get("documentation_obligations", []):
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            paths.append(
                _repo_file(
                    repo_root,
                    row["path"],
                    "documentation obligation audit input",
                )
            )
    paths.extend(sorted(epic_dir.glob("file-plan-story-*.yaml")))
    hashes = {
        _repo_relative(path, repo_root, "audit boundary artifact"): _file_sha256(path)
        for path in paths
    }
    return hashes, _structured_sha256(hashes)


def _hash_map_errors(
    value: Any,
    repo_root: Path,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or (not value and not allow_empty):
        return [f"{label} must be a path-to-sha256 mapping"]
    for raw_path, expected in value.items():
        try:
            relative = _relative(raw_path, f"{label} path")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if relative == "tmp_debug" or relative.startswith("tmp_debug/"):
            errors.append(f"{label} cannot depend on prunable tmp_debug evidence: {relative}")
            continue
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            errors.append(f"{label} has invalid sha256 for {relative}")
            continue
        try:
            actual = _file_sha256(_repo_file(repo_root, relative, label))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if actual != expected:
            errors.append(f"{label} hash mismatch for {relative}")
    return errors


def _execution_errors(
    row: Any,
    repo_root: Path,
    label: str,
    *,
    require_pass: bool,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return [f"{label} must be a mapping"]
    _string(row.get("command"), f"{label}.command", errors)
    _string(row.get("summary"), f"{label}.summary", errors)
    outcome = row.get("outcome")
    if outcome not in {"pass", "fail", "blocked"}:
        errors.append(f"{label}.outcome must be pass, fail, or blocked")
    values: dict[str, int] = {}
    for field in ("exit_code", "passed", "failed", "errors", "skipped"):
        value = row.get(field)
        if not _is_int(value) or (field != "exit_code" and value < 0):
            errors.append(f"{label}.{field} must be an integer" + (" >= 0" if field != "exit_code" else ""))
        else:
            values[field] = value
    if values.get("skipped", 0) and not isinstance(row.get("skip_reason"), str):
        errors.append(f"{label}.skip_reason is required when skipped is non-zero")
    if outcome == "pass" and values:
        if (
            values.get("exit_code") != 0
            or values.get("passed", 0) <= 0
            or values.get("failed") != 0
            or values.get("errors") != 0
            or values.get("skipped") != 0
        ):
            errors.append(
                f"{label} PASS requires passed>0, exit_code=0, and failed=errors=skipped=0"
            )
    if outcome == "fail" and values:
        if (
            values.get("exit_code") == 0
            and values.get("failed") == 0
            and values.get("errors") == 0
            and values.get("skipped") == 0
        ):
            errors.append(f"{label} FAIL has no failing count or exit code")
    if require_pass and outcome != "pass":
        errors.append(f"{label} must be a strict PASS")
    errors.extend(_hash_map_errors(row.get("evidence_hashes"), repo_root, f"{label}.evidence_hashes"))
    return errors


def verify_implementation_evidence(
    epic_dir: Path,
    repo_root: Path,
    policy: Mapping[str, Any],
    story: str = "",
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    paths = policy.get("paths", {})
    try:
        manifest = _load_yaml(epic_dir / str(paths.get("delivery_manifest")), "delivery manifest")
        evidence = _load_yaml(
            epic_dir / str(paths.get("implementation_evidence")), "implementation evidence"
        )
    except ValueError as exc:
        return [str(exc)], {}, {}
    if manifest.get("schema_version") not in {1, 2, 3}:
        errors.append("delivery manifest schema_version must be 1, 2, or 3")
    if evidence.get("schema_version") != policy.get("implementation_evidence_version"):
        errors.append("implementation evidence schema_version does not match policy")
    epic_id = manifest.get("epic_id")
    if evidence.get("epic_id") != epic_id:
        errors.append("implementation evidence epic_id does not match delivery manifest")
    errors.extend(_refinement_handoff_errors(epic_dir, repo_root))
    try:
        errors.extend(_worker_module().verify_implementation_attribution(epic_dir, repo_root))
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        errors.append(f"cannot verify runner-owned implementation attribution: {exc}")
    if story:
        matching = [
            row
            for row in evidence.get("stories", [])
            if isinstance(row, dict) and row.get("story_id") == story
        ]
        if len(matching) != 1:
            errors.append(f"unknown or duplicate implementation story: {story}")
        elif matching[0].get("status") != "verified":
            errors.append(f"implementation evidence story {story} is not verified")
    attributed = {
        row.get("path")
        for row in evidence.get("attributed_delta", [])
        if isinstance(row, dict) and row.get("state") != "deleted"
    }
    for row in manifest.get("documentation_obligations", []):
        if isinstance(row, dict) and row.get("path") not in attributed:
            errors.append(
                "documentation obligation target is not runner-attributed: "
                f"{row.get('path')}"
            )
    return errors, manifest, evidence


def _review_assignments(policy: Mapping[str, Any], reviewer_set: str) -> list[dict[str, str]]:
    set_policy = policy.get("review", {}).get("sets", {}).get(reviewer_set)
    if not isinstance(set_policy, dict):
        raise ValueError(f"unknown reviewer set: {reviewer_set}")
    return [
        {"provider": provider, "mission": str(policy.get("review", {}).get("mission"))}
        for provider in set_policy.get("providers", [])
    ]


def _initial_findings(epic_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": policy.get("findings_version"),
        "epic_id": epic_id,
        "findings": [],
    }


def _next_attempt(epic_dir: Path) -> tuple[str, Path]:
    numbers = []
    for path in (epic_dir / "reviews").glob("audit-*/audit-attempt.yaml"):
        match = re.fullmatch(r"audit-([0-9]{3})", path.parent.name)
        if match:
            numbers.append(int(match.group(1)))
    attempt_id = f"audit-{max(numbers, default=0) + 1:03d}"
    return attempt_id, epic_dir / "reviews" / attempt_id


def _remediation_errors(
    finding: Mapping[str, Any], repo_root: Path, label: str
) -> list[str]:
    errors: list[str] = []
    remediation = finding.get("remediation")
    if not isinstance(remediation, dict):
        return [f"{label}.remediation is required before targeted verification"]
    if remediation.get("source_attempt_id") != finding.get("first_seen_attempt"):
        errors.append(f"{label}.remediation source_attempt_id does not bind the source attempt")
    source_ids = _string_list(
        remediation.get("source_ids"), f"{label}.remediation.source_ids", errors
    )
    if not source_ids or not set(source_ids).issubset(set(finding.get("source_ids", []))):
        errors.append(f"{label}.remediation source_ids do not bind the finding sources")
    affected = _string_list(
        remediation.get("affected_paths"), f"{label}.remediation.affected_paths", errors
    )
    hashes = remediation.get("affected_path_hashes")
    errors.extend(
        _hash_map_errors(hashes, repo_root, f"{label}.remediation.affected_path_hashes")
    )
    if isinstance(hashes, dict) and set(affected) != set(hashes):
        errors.append(f"{label}.remediation affected paths and hashes differ")
    checks = _mapping_list(
        remediation.get("checks"), f"{label}.remediation.checks", errors
    )
    if not checks:
        errors.append(f"{label}.remediation.checks cannot be empty")
    for index, check in enumerate(checks):
        errors.extend(
            _execution_errors(
                check,
                repo_root,
                f"{label}.remediation.checks[{index}]",
                require_pass=True,
            )
        )
    return errors


def _attempt_paths(
    epic_dir: Path, attempt_dir: Path, repo_root: Path, policy: Mapping[str, Any]
) -> tuple[Path, Path, Path, Path]:
    attempt_dir = _inside(attempt_dir, epic_dir / "reviews", "audit attempt directory")
    return (
        attempt_dir / "audit-attempt.yaml",
        attempt_dir / str(policy.get("paths", {}).get("review_packet")),
        attempt_dir / str(policy.get("paths", {}).get("reviewer_receipt")),
        epic_dir / str(policy.get("paths", {}).get("findings")),
    )


def prepare(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (_, working_root, _):
        policy = _policy(args.policy.resolve())
        errors, manifest, evidence = verify_implementation_evidence(
            epic_dir, working_root, policy
        )
        if errors:
            raise ValueError("implementation evidence is not audit-ready: " + "; ".join(errors))
        fingerprint = repository_fingerprint(epic_dir, working_root)
        artifact_hashes, boundary_sha256 = _artifact_boundary(epic_dir, working_root)
        findings_path = epic_dir / str(policy.get("paths", {}).get("findings"))
        findings = (
            _load_yaml(findings_path, "audit findings")
            if findings_path.is_file()
            else _initial_findings(str(manifest.get("epic_id")), policy)
        )
        finding_rows = findings.get("findings", [])
        targets = list(dict.fromkeys(args.finding))
        target_rows: list[dict[str, Any]] = []
        if args.mode == "targeted":
            if not targets:
                raise ValueError("targeted audit requires at least one --finding")
            by_id = {row.get("id"): row for row in finding_rows if isinstance(row, dict)}
            missing = sorted(set(targets) - set(by_id))
            if missing:
                raise ValueError(f"unknown targeted findings: {missing}")
            invalid = [
                finding_id
                for finding_id in targets
                if by_id[finding_id].get("status") != "remediated_pending_verification"
            ]
            if invalid:
                raise ValueError(f"targeted findings are not ready for verification: {invalid}")
            remediation_errors = [
                error
                for finding_id in targets
                for error in _remediation_errors(
                    by_id[finding_id], working_root, f"audit finding {finding_id}"
                )
            ]
            if remediation_errors:
                raise ValueError("; ".join(remediation_errors))
            target_rows = [
                {
                    "id": finding_id,
                    "fingerprint": by_id[finding_id].get("fingerprint"),
                    "finding_sha256": _structured_sha256(by_id[finding_id]),
                    "title": by_id[finding_id].get("title"),
                    "evidence": by_id[finding_id].get("evidence"),
                    "affected_acceptance_ids": by_id[finding_id].get(
                        "affected_acceptance_ids", []
                    ),
                    "source_candidate_ids": by_id[finding_id].get("source_ids"),
                    "closure_test": by_id[finding_id].get("closure_test"),
                    "remediation": by_id[finding_id].get("remediation"),
                    "remediation_sha256": _structured_sha256(
                        by_id[finding_id].get("remediation")
                    ),
                }
                for finding_id in targets
            ]
        elif targets:
            raise ValueError("full audit cannot name targeted findings")
        for path in sorted((epic_dir / "reviews").glob("audit-*/audit-attempt.yaml")):
            existing = _load_yaml(path, "historical audit attempt")
            if existing.get("status") != "pending":
                continue
            same = (
                existing.get("mode") == args.mode
                and existing.get("target_finding_ids") == targets
                and existing.get("target_findings", []) == target_rows
                and existing.get("repository_fingerprint") == fingerprint
                and existing.get("artifact_hashes") == artifact_hashes
                and existing.get("reviewer_profile") == args.reviewer_profile
                and existing.get("reviewer_set") == args.reviewer_set
            )
            if same:
                print(_repo_relative(path.parent, working_root, "audit attempt"))
                return 0
            raise ValueError(f"a different pending audit attempt already exists: {path.parent.name}")
        terminal_count = 0
        for path in sorted((epic_dir / "reviews").glob("audit-*/audit-attempt.yaml")):
            historical = _load_yaml(path, "historical audit attempt")
            if historical.get("mode") == args.mode and historical.get("status") in {
                "pass",
                "fail",
                "blocked",
            }:
                terminal_count += 1
        limit = int(policy.get("attempt_limits", {}).get(args.mode, 0))
        if terminal_count >= limit:
            raise ValueError(f"{args.mode} audit attempt budget is exhausted")
        attempt_id, attempt_dir = _next_attempt(epic_dir)
        acceptance_ids = manifest.get("acceptance_ids", [])
        gates: list[dict[str, Any]] = []
        assignments: list[dict[str, str]]
        if args.mode == "full":
            for story_row in evidence.get("stories", []):
                if not isinstance(story_row, dict):
                    continue
                for proof in story_row.get("proofs", []):
                    if not isinstance(proof, dict):
                        continue
                    gates.append(
                        {
                            "id": str(proof.get("proof_id")),
                            "kind": "test",
                            "command": proof.get("command"),
                            "status": "pending",
                            "result": None,
                            "authority_id": None,
                        }
                    )
            assignments = _review_assignments(policy, args.reviewer_set)
        else:
            by_id = {row.get("id"): row for row in finding_rows if isinstance(row, dict)}
            providers: list[str] = []
            for finding_id in targets:
                finding = by_id[finding_id]
                gates.append(
                    {
                        "id": f"closure:{finding_id}",
                        "kind": "closure",
                        "command": shlex.join(finding["remediation"]["execution"]["argv"]) if manifest.get("schema_version") == 3 and finding.get("remediation", {}).get("execution") else finding.get("closure_test"),
                        "status": "pending",
                        "result": None,
                        "authority_id": None,
                    }
                )
                providers.extend(
                    provider
                    for provider in finding.get("detected_by", [])
                    if provider in policy.get("review", {}).get("allowed_providers", [])
                )
            assignments = [
                {"provider": provider, "mission": str(policy.get("review", {}).get("mission"))}
                for provider in dict.fromkeys(providers)
            ]
        if len({row["id"] for row in gates}) != len(gates):
            raise ValueError("delivery evidence produced duplicate audit gate IDs")
        packet = {
            "schema_version": policy.get("review_packet_version"),
            "workflow": "audit",
            "epic_id": manifest.get("epic_id"),
            "attempt_id": attempt_id,
            "mode": args.mode,
            "risk_level": manifest.get("risk_level"),
            "required_acceptance_ids": acceptance_ids,
            "target_finding_ids": targets,
            "target_findings": target_rows,
            "artifact_hashes": artifact_hashes,
            "boundary_sha256": boundary_sha256,
            "assignments": assignments,
            "review": {"required_assignments": assignments},
            "gates": [
                {"id": row["id"], "kind": row["kind"], "command": row["command"]}
                for row in gates
            ],
        }
        packet_path = attempt_dir / str(policy.get("paths", {}).get("review_packet"))
        attempt_dir.mkdir(parents=True, exist_ok=False)
        _atomic_write_yaml_documents([(packet_path, packet)])
        if manifest.get("schema_version") == 3:
            packet["replay_snapshot"] = scope_snapshot.retain(working_root, epic_dir, attempt_id)
        attempt = {
            "schema_version": policy.get("attempt_version"),
            "epic_id": manifest.get("epic_id"),
            "attempt_id": attempt_id,
            "mode": args.mode,
            "status": "pending",
            "reason": args.reason,
            "created_at": _now(),
            "updated_at": _now(),
            "repository_fingerprint": fingerprint,
            "artifact_hashes": artifact_hashes,
            "boundary_sha256": boundary_sha256,
            "required_acceptance_ids": acceptance_ids,
            "target_finding_ids": targets,
            "target_findings": target_rows,
            "reviewer_profile": args.reviewer_profile,
            "reviewer_set": args.reviewer_set,
            "gates": gates,
            "review": {
                "required_assignments": assignments,
                "packet_path": _repo_relative(packet_path, working_root, "review packet"),
                "packet_sha256": _file_sha256(packet_path),
                "receipt_path": None,
                "receipt_sha256": None,
            },
            "synthesis": None,
            "authorities": [],
            "decision": {"outcome": None, "reason": ""},
            "report": {
                "path": _repo_target_relative(
                    epic_dir / str(policy.get("paths", {}).get("report")),
                    working_root,
                    "audit report",
                ),
                "sha256": None,
            },
        }
        attempt_path = attempt_dir / "audit-attempt.yaml"
        _atomic_write_yaml_documents([(attempt_path, attempt), (findings_path, findings)])
        print(_repo_relative(attempt_dir, working_root, "audit attempt"))
        return 0


def _current_attempt_boundary(
    attempt: Mapping[str, Any], epic_dir: Path, repo_root: Path
) -> list[str]:
    errors: list[str] = []
    if attempt.get("repository_fingerprint") != repository_fingerprint(epic_dir, repo_root):
        errors.append("repository fingerprint changed after audit preparation")
    hashes, boundary = _artifact_boundary(epic_dir, repo_root)
    if attempt.get("artifact_hashes") != hashes or attempt.get("boundary_sha256") != boundary:
        errors.append("audit artifact boundary changed after preparation")
    return errors


def _require_attempt_epic(run: Mapping[str, Any], attempt: Mapping[str, Any]) -> None:
    if not _same_epic_id(run.get("epic_id"), attempt.get("epic_id")):
        raise ValueError("worker run epic_id does not match audit attempt")


def record_authority(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (run, working_root, _):
        policy = _policy(args.policy.resolve())
        attempt_path = _inside(args.attempt_dir.resolve(), epic_dir / "reviews", "attempt directory") / "audit-attempt.yaml"
        attempt = _load_yaml(attempt_path, "audit attempt")
        _require_attempt_epic(run, attempt)
        errors = _current_attempt_boundary(attempt, epic_dir, working_root)
        if errors:
            raise ValueError("; ".join(errors))
        if args.kind not in policy.get("authority", {}).get("kinds", []):
            raise ValueError(f"unknown audit authority kind: {args.kind}")
        if args.decision != policy.get("authority", {}).get("decision"):
            raise ValueError("audit authority decision must be approved")
        if args.kind == "gate_not_applicable":
            if args.subject not in {row.get("id") for row in attempt.get("gates", [])}:
                raise ValueError(f"unknown gate authority subject: {args.subject}")
            scope = {"epic_id": attempt.get("epic_id"), "gate_id": args.subject}
        else:
            scope = {"epic_id": attempt.get("epic_id"), "finding_fingerprint": args.subject}
        row = {
            "id": args.authority_id,
            "kind": args.kind,
            "source": args.source,
            "decision": args.decision,
            "decided_at": _now(),
            "scope": scope,
            "artifact_hashes": attempt.get("artifact_hashes"),
            "boundary_sha256": attempt.get("boundary_sha256"),
        }
        authorities = attempt.get("authorities")
        if not isinstance(authorities, list):
            raise ValueError("audit attempt authorities must be a list")
        existing = [item for item in authorities if isinstance(item, dict) and item.get("id") == args.authority_id]
        if existing:
            comparable = dict(row)
            comparable["decided_at"] = existing[0].get("decided_at")
            if existing[0] != comparable:
                raise ValueError(f"authority ID already exists with different content: {args.authority_id}")
            print(json.dumps(existing[0], sort_keys=True))
            return 0
        authorities.append(row)
        attempt["updated_at"] = _now()
        _atomic_write_yaml_documents([(attempt_path, attempt)])
        print(json.dumps(row, sort_keys=True))
        return 0


def _authority(
    attempt: Mapping[str, Any], authority_id: Any, kind: str, subject: str
) -> bool:
    scope_key = "gate_id" if kind == "gate_not_applicable" else "finding_fingerprint"
    return any(
        isinstance(row, dict)
        and row.get("id") == authority_id
        and row.get("kind") == kind
        and row.get("decision") == "approved"
        and row.get("scope", {}).get(scope_key) == subject
        and row.get("artifact_hashes") == attempt.get("artifact_hashes")
        and row.get("boundary_sha256") == attempt.get("boundary_sha256")
        for row in attempt.get("authorities", [])
    )


def execute_gates(args: argparse.Namespace) -> int:
    import scope_proofs
    epic = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic) as (run, root, _):
        attempt_dir = _inside(args.attempt_dir.resolve(), epic / "reviews", "attempt directory")
        attempt_path = attempt_dir / "audit-attempt.yaml"
        attempt = _load_yaml(attempt_path, "audit attempt")
        _require_attempt_epic(run, attempt)
        errors = _current_attempt_boundary(attempt, epic, root)
        if errors:
            raise ValueError("; ".join(errors))
        manifest = _load_yaml(epic / "delivery-manifest.yaml", "delivery manifest")
        if manifest.get("schema_version") != 3:
            raise ValueError("execute-gates requires manifest v3; keep older approved epics on their pinned runtime")
        planned = {row["id"]: row for row in manifest["proofs"]}
        proofs = []
        for gate in attempt["gates"]:
            if gate["status"] != "pending":
                continue
            if gate["kind"] == "closure":
                target = next(row for row in attempt["target_findings"] if "closure:" + row["id"] == gate["id"])
                matches = [row for row in manifest["proofs"] if row.get("command") == gate["command"]]
                execution = target["remediation"].get("execution")
                if execution is not None:
                    proof = {"id": gate["id"], "command": gate["command"], "level": target["remediation"].get("proof_level", "unit"), "execution": execution}
                elif matches:
                    proof = {**matches[0], "id": gate["id"]}
                else:
                    raise ValueError(f"closure gate {gate['id']} requires a hash-bound remediation.execution contract")
            else:
                proof = planned[gate["id"]]
            proofs.append(proof)
        evidence = _load_yaml(epic / "implementation-evidence.yaml", "implementation evidence")
        reuse = [proof for row in evidence["stories"] for proof in row["proofs"]]
        receipt = scope_proofs.execute(proofs, root, epic, scope_proofs.policy(Path(run["scope_root"])), reuse=reuse)
        by_id = {row["proof_id"]: row for row in receipt["proofs"]}
        for gate in attempt["gates"]:
            if gate["id"] in by_id:
                result = by_id[gate["id"]]
                gate.update(status=result["outcome"], result=result, recorded_at=_now(), reason=result["summary"] if result["outcome"] == "blocked" else None)
        attempt["proof_execution"] = {key: receipt[key] for key in ("path", "sha256", "duration_seconds")}
        attempt["updated_at"] = _now()
        _atomic_write_yaml_documents([(attempt_path, attempt)])
        print(f"Audit gates: {receipt['status']} ({receipt['path']})")
        return 0 if receipt["status"] == "pass" else 1


def record_gate(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (run, working_root, _):
        policy = _policy(args.policy.resolve())
        attempt_dir = _inside(args.attempt_dir.resolve(), epic_dir / "reviews", "attempt directory")
        attempt_path = attempt_dir / "audit-attempt.yaml"
        attempt = _load_yaml(attempt_path, "audit attempt")
        _require_attempt_epic(run, attempt)
        errors = _current_attempt_boundary(attempt, epic_dir, working_root)
        if errors:
            raise ValueError("; ".join(errors))
        rows = attempt.get("gates")
        if not isinstance(rows, list):
            raise ValueError("audit attempt gates must be a list")
        matches = [row for row in rows if isinstance(row, dict) and row.get("id") == args.gate]
        if len(matches) != 1:
            raise ValueError(f"unknown or duplicate audit gate: {args.gate}")
        gate = matches[0]
        manifest = _load_yaml(epic_dir / "delivery-manifest.yaml", "delivery manifest")
        if manifest.get("schema_version") == 3 and args.status in {"pass", "fail"}:
            raise ValueError("manifest v3 gate results are executor-owned; use execute-gates")
        if gate.get("status") != "pending":
            requested = {
                "status": args.status,
                "authority_id": args.authority_id,
            }
            if gate.get("status") == requested["status"] and gate.get("authority_id") == requested["authority_id"]:
                print(f"Audit gate already recorded: {args.gate}")
                return 0
            raise ValueError(f"audit gate already has terminal status: {args.gate}")
        if args.status not in policy.get("gate_statuses", [] ) or args.status == "pending":
            raise ValueError(f"invalid gate status: {args.status}")
        if args.status == "not_applicable":
            if not args.authority_id or not _authority(
                attempt, args.authority_id, "gate_not_applicable", args.gate
            ):
                raise ValueError("not_applicable requires current hash-bound gate authority")
            if any(value is not None for value in (args.exit_code, args.passed, args.failed, args.errors, args.skipped)):
                raise ValueError("not_applicable gate must not report execution counts")
            result = None
        elif args.status == "blocked":
            if not args.reason:
                raise ValueError("blocked gate requires --reason")
            if any(value is not None for value in (args.exit_code, args.passed, args.failed, args.errors, args.skipped)):
                raise ValueError("blocked unexecuted gate must not report execution counts")
            result = None
        else:
            if any(value is None for value in (args.exit_code, args.passed, args.failed, args.errors, args.skipped)):
                raise ValueError("executed test gate requires exit code and all pass/fail/error/skip counts")
            evidence_hashes: dict[str, str] = {}
            for evidence_path in args.evidence:
                path = _inside(evidence_path.resolve(), working_root, "gate evidence")
                relative = _repo_relative(path, working_root, "gate evidence")
                if relative == "tmp_debug" or relative.startswith("tmp_debug/"):
                    raise ValueError("durable gate evidence cannot live under tmp_debug")
                evidence_hashes[relative] = _file_sha256(path)
            result = {
                "command": gate.get("command"),
                "outcome": args.status,
                "exit_code": args.exit_code,
                "passed": args.passed,
                "failed": args.failed,
                "errors": args.errors,
                "skipped": args.skipped,
                "summary": args.summary,
                "evidence_hashes": evidence_hashes,
            }
            if args.skip_reason:
                result["skip_reason"] = args.skip_reason
            result_errors = _execution_errors(
                result,
                working_root,
                f"audit gate {args.gate}",
                require_pass=args.status == "pass",
            )
            if result_errors:
                raise ValueError("; ".join(result_errors))
        gate.update(
            {
                "status": args.status,
                "result": result,
                "reason": args.reason,
                "authority_id": args.authority_id,
                "recorded_at": _now(),
            }
        )
        attempt["updated_at"] = _now()
        _atomic_write_yaml_documents([(attempt_path, attempt)])
        print(f"Audit gate recorded: gate={args.gate} status={args.status}")
        return 0


def _verify_packet(
    packet_path: Path,
    epic_dir: Path,
    repo_root: Path,
    policy: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    try:
        packet_path = _inside(packet_path, epic_dir / "reviews", "audit review packet")
        packet = _load_yaml(packet_path, "audit review packet")
    except ValueError as exc:
        return [str(exc)], {}
    if packet.get("schema_version") != policy.get("review_packet_version"):
        errors.append("audit review packet schema_version does not match policy")
    if packet.get("workflow") != "audit":
        errors.append("audit review packet workflow must be audit")
    if packet.get("attempt_id") != packet_path.parent.name:
        errors.append("audit review packet attempt_id does not match directory")
    assignments = _mapping_list(packet.get("assignments"), "audit packet assignments", errors)
    keys = [(row.get("provider"), row.get("mission")) for row in assignments]
    if len(keys) != len(set(keys)):
        errors.append("audit review packet has duplicate assignments")
    hashes = packet.get("artifact_hashes")
    if isinstance(hashes, dict) and packet.get("boundary_sha256") != _structured_sha256(hashes):
        errors.append("audit packet boundary_sha256 mismatch")
    return errors, packet


def _verify_receipt(
    epic_dir: Path,
    receipt_path: Path,
    repo_root: Path,
    policy: Mapping[str, Any],
) -> tuple[
    list[str],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
]:
    errors: list[str] = []
    try:
        receipt_path = _inside(receipt_path, epic_dir / "reviews", "audit reviewer receipt")
        receipt = _load_yaml(receipt_path, "audit reviewer receipt")
    except ValueError as exc:
        return [str(exc)], {}, {}, [], [], False
    if receipt.get("schema_version") != policy.get("reviewer_receipt_version"):
        errors.append("audit reviewer receipt schema_version does not match policy")
    if receipt.get("workflow") != "audit":
        errors.append("audit reviewer receipt workflow must be audit")
    if receipt.get("status") not in RECEIPT_TOP_STATUSES:
        errors.append("audit reviewer receipt status is invalid")
    try:
        packet_path = _repo_file(repo_root, receipt.get("packet_path"), "audit review packet")
    except ValueError as exc:
        return [str(exc)], receipt, {}, [], [], False
    packet_errors, packet = _verify_packet(packet_path, epic_dir, repo_root, policy)
    errors.extend(packet_errors)
    if packet_path.parent != receipt_path.parent:
        errors.append("audit receipt and packet must share one attempt directory")
    if receipt.get("packet_sha256") != _file_sha256(packet_path):
        errors.append("audit reviewer receipt packet_sha256 mismatch")
    try:
        template_path = _repo_file(repo_root, receipt.get("template_path"), "audit review template")
        if receipt.get("template_sha256") != _file_sha256(template_path):
            errors.append("audit reviewer receipt template_sha256 mismatch")
    except ValueError as exc:
        errors.append(str(exc))
    identity = receipt.get("git_identity")
    if (
        not isinstance(identity, dict)
        or identity.get("unchanged") is not True
        or identity.get("before") != identity.get("after")
    ):
        errors.append("audit reviewer changed git identity")
    expected_assignments = packet.get("assignments", [])
    manifest = [
        {"provider": row.get("provider"), "mission": row.get("mission")}
        for row in expected_assignments
        if isinstance(row, dict)
    ]
    if receipt.get("assignment_manifest_sha256") != _structured_sha256(manifest):
        errors.append("audit reviewer receipt assignment manifest hash mismatch")
    rows = _mapping_list(receipt.get("assignments"), "audit reviewer assignments", errors)
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    semantic_complete = True
    for index, row in enumerate(rows):
        label = f"audit reviewer assignments[{index}]"
        key = (row.get("provider"), row.get("mission"))
        if key in by_key:
            errors.append(f"duplicate audit reviewer assignment: {key}")
        by_key[key] = row
        if row.get("status") not in RECEIPT_ROW_STATUSES:
            errors.append(f"{label}.status is invalid")
        questions = row.get("questions", [])
        unverified = row.get("unverified_evidence", [])
        if not isinstance(questions, list):
            errors.append(f"{label}.questions must be a list")
            questions = []
        if not isinstance(unverified, list):
            errors.append(f"{label}.unverified_evidence must be a list")
            unverified = []
        if (
            row.get("status") != "completed"
            or row.get("decision") not in {"pass", "findings"}
            or questions
            or unverified
        ):
            semantic_complete = False
        paths = row.get("paths")
        if not isinstance(paths, dict):
            errors.append(f"{label}.paths must be a mapping")
            paths = {}
        if "metadata" in paths:
            errors.append(f"{label}.paths must not contain metadata")
        raw_candidates = row.get("candidates", [])
        raw_verifications = row.get("targeted_verifications", [])
        if not isinstance(raw_candidates, list) or any(not isinstance(item, dict) for item in raw_candidates):
            errors.append(f"{label}.candidates must be a list of mappings")
            raw_candidates = []
        if not isinstance(raw_verifications, list) or any(not isinstance(item, dict) for item in raw_verifications):
            errors.append(f"{label}.targeted_verifications must be a list of mappings")
            raw_verifications = []
        if row.get("status") == "completed" or raw_candidates or raw_verifications:
            try:
                output = _repo_file(repo_root, paths.get("output"), "audit review output")
                if row.get("output_sha256") != _file_sha256(output):
                    errors.append(f"{label}.output_sha256 mismatch")
            except ValueError as exc:
                errors.append(str(exc))
        for raw in raw_candidates:
            enriched = dict(raw)
            enriched.setdefault("provider", row.get("provider"))
            enriched.setdefault("mission", row.get("mission"))
            enriched["output_sha256"] = row.get("output_sha256")
            candidates.append(enriched)
        for raw in raw_verifications:
            enriched = dict(raw)
            enriched.setdefault("provider", row.get("provider"))
            enriched.setdefault("mission", row.get("mission"))
            enriched["output_sha256"] = row.get("output_sha256")
            verifications.append(enriched)
    expected_keys = {(row.get("provider"), row.get("mission")) for row in expected_assignments}
    if set(by_key) != expected_keys:
        errors.append("audit reviewer assignments do not exactly match packet")
    complete = (
        receipt.get("status") == "completed"
        and set(by_key) == expected_keys
        and all(by_key[key].get("status") == "completed" for key in expected_keys)
        and semantic_complete
        and not errors
    )
    return errors, receipt, packet, candidates, verifications, complete


def _review_source_id(attempt_id: str, row: Mapping[str, Any]) -> str:
    source_id = row.get("source_id") or row.get("id")
    if not all(isinstance(value, str) and value for value in (row.get("provider"), row.get("mission"), source_id)):
        raise ValueError("audit candidate requires provider, mission, and source_id")
    return f"review:{attempt_id}:{row['provider']}:{row['mission']}:{source_id}"


def _source_from_candidate(
    attempt_id: str, row: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    source_id = _review_source_id(attempt_id, row)
    severity = row.get("severity")
    category = row.get("category")
    disposition = row.get("disposition")
    if severity not in policy.get("findings", {}).get("severities", []):
        raise ValueError(f"audit candidate {source_id} has invalid severity")
    if category not in policy.get("findings", {}).get("categories", []):
        raise ValueError(f"audit candidate {source_id} has invalid category")
    if disposition not in {"remediation_required", "user_decision", "documentation_decision"}:
        raise ValueError(f"audit candidate {source_id} has invalid reviewer disposition")
    fingerprint = row.get("fingerprint")
    closure = row.get("closure_test")
    evidence_value = row.get("evidence")
    evidence = evidence_value if isinstance(evidence_value, list) else [evidence_value]
    affected = row.get("affected_files", row.get("affected_paths", []))
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError(f"audit candidate {source_id} has no fingerprint")
    if not isinstance(closure, str) or not closure:
        raise ValueError(f"audit candidate {source_id} has no closure_test")
    if any(not isinstance(item, str) or not item for item in evidence):
        raise ValueError(f"audit candidate {source_id} has invalid evidence")
    if not isinstance(affected, list) or any(not isinstance(item, str) for item in affected):
        raise ValueError(f"audit candidate {source_id} has invalid affected paths")
    return source_id, {
        "title": row.get("title") or row.get("impact") or fingerprint,
        "fingerprint": fingerprint,
        "severity": severity,
        "category": category,
        "disposition": disposition,
        "evidence": evidence,
        "affected_paths": affected,
        "affected_acceptance_ids": row.get("affected_acceptance_ids", []),
        "closure_test": closure,
        "detected_by": [row.get("provider")],
    }


def _next_finding_id(rows: Sequence[Mapping[str, Any]]) -> str:
    numbers = []
    for row in rows:
        match = re.fullmatch(r"AF-([0-9]{3})", str(row.get("id", "")))
        if match:
            numbers.append(int(match.group(1)))
    return f"AF-{max(numbers, default=0) + 1:03d}"


def apply_synthesis(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (run, working_root, repository_root):
        policy = _policy(args.policy.resolve())
        attempt_dir = _inside(args.attempt_dir.resolve(), epic_dir / "reviews", "attempt directory")
        attempt_path = attempt_dir / "audit-attempt.yaml"
        attempt = _load_yaml(attempt_path, "audit attempt")
        _require_attempt_epic(run, attempt)
        boundary_errors = _current_attempt_boundary(attempt, epic_dir, working_root)
        if boundary_errors:
            raise ValueError("; ".join(boundary_errors))
        if any(row.get("status") == "pending" for row in attempt.get("gates", []) if isinstance(row, dict)):
            raise ValueError("all audit gates must be recorded before synthesis")
        review = attempt.get("review")
        if not isinstance(review, dict):
            raise ValueError("audit attempt review must be a mapping")
        packet_path = attempt_dir / str(policy.get("paths", {}).get("review_packet"))
        packet_errors, packet = _verify_packet(
            packet_path, epic_dir, working_root, policy
        )
        if packet_errors:
            raise ValueError("invalid audit review packet: " + "; ".join(packet_errors))
        if review.get("packet_sha256") != _file_sha256(packet_path):
            raise ValueError("audit attempt packet hash mismatch")
        if packet.get("target_findings", []) != attempt.get("target_findings", []):
            raise ValueError("audit packet target findings differ from attempt")
        required_assignments = review.get("required_assignments", [])
        candidates: list[dict[str, Any]] = []
        verifications: list[dict[str, Any]] = []
        receipt: dict[str, Any] = {}
        receipt_complete = not required_assignments
        receipt_path = attempt_dir / str(policy.get("paths", {}).get("reviewer_receipt"))
        if receipt_path.is_file():
            receipt_errors, receipt, _, candidates, verifications, receipt_complete = _verify_receipt(
                epic_dir, receipt_path, working_root, policy
            )
            if receipt_errors:
                raise ValueError("invalid audit reviewer receipt: " + "; ".join(receipt_errors))
            review["receipt_path"] = _repo_relative(receipt_path, working_root, "audit receipt")
            review["receipt_sha256"] = _file_sha256(receipt_path)
        elif required_assignments:
            raise ValueError("required audit reviewer receipt is missing")
        findings_path = epic_dir / str(policy.get("paths", {}).get("findings"))
        findings = _load_yaml(findings_path, "audit findings")
        rows = findings.get("findings")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("audit findings must contain a findings list")
        by_id = {row.get("id"): row for row in rows}
        by_fingerprint = {row.get("fingerprint"): row for row in rows}
        verified_targets: set[str] = set()
        if attempt.get("mode") == "targeted":
            target_ids = set(attempt.get("target_finding_ids", []))
            target_rows = attempt.get("target_findings")
            if not isinstance(target_rows, list) or any(
                not isinstance(row, dict) for row in target_rows
            ):
                raise ValueError("targeted audit attempt requires target_findings rows")
            if {row.get("id") for row in target_rows} != target_ids:
                raise ValueError("targeted audit IDs differ from target_findings rows")
            for target in target_rows:
                finding_id = target.get("id")
                finding = by_id.get(finding_id)
                if not isinstance(finding, dict):
                    raise ValueError(f"targeted finding disappeared: {finding_id}")
                if (
                    target.get("fingerprint") != finding.get("fingerprint")
                    or target.get("title") != finding.get("title")
                    or target.get("evidence") != finding.get("evidence")
                    or target.get("affected_acceptance_ids")
                    != finding.get("affected_acceptance_ids", [])
                    or target.get("source_candidate_ids") != finding.get("source_ids")
                    or target.get("closure_test") != finding.get("closure_test")
                    or target.get("remediation") != finding.get("remediation")
                ):
                    raise ValueError(
                        f"targeted finding snapshot differs from current finding: {finding_id}"
                    )
                if target.get("remediation_sha256") != _structured_sha256(
                    finding.get("remediation")
                ):
                    raise ValueError(
                        f"targeted remediation changed after audit preparation: {finding_id}"
                    )
                if target.get("finding_sha256") != _structured_sha256(finding):
                    raise ValueError(
                        f"targeted finding changed after audit preparation: {finding_id} "
                        "(finding_sha256 mismatch)"
                    )
            verification_by_fingerprint: dict[str, list[dict[str, Any]]] = {}
            for verification in verifications:
                fingerprint = str(verification.get("fingerprint"))
                target = next(
                    (
                        row
                        for row in target_rows
                        if row.get("fingerprint") == fingerprint
                    ),
                    None,
                )
                if target is None:
                    raise ValueError(
                        f"targeted audit verification is outside packet: {fingerprint}"
                    )
                if verification.get("source_candidate_ids") != target.get(
                    "source_candidate_ids"
                ):
                    raise ValueError(
                        "targeted audit verification source_candidate_ids differ "
                        f"from packet: {fingerprint}"
                    )
                if verification.get("closure_test") != target.get("closure_test"):
                    raise ValueError(
                        "targeted audit verification closure_test differs from packet: "
                        f"{fingerprint}"
                    )
                verification_by_fingerprint.setdefault(fingerprint, []).append(
                    verification
                )
            required_keys = {
                (row.get("provider"), row.get("mission"))
                for row in required_assignments
                if isinstance(row, dict)
            }
            for finding_id in target_ids:
                finding = by_id.get(finding_id)
                if not isinstance(finding, dict):
                    raise ValueError(f"targeted finding disappeared: {finding_id}")
                closure_gate = next(
                    (row for row in attempt.get("gates", []) if row.get("id") == f"closure:{finding_id}"),
                    None,
                )
                checks = verification_by_fingerprint.get(str(finding.get("fingerprint")), [])
                check_keys = {
                    (row.get("provider"), row.get("mission")) for row in checks
                }
                if (
                    closure_gate
                    and closure_gate.get("status") == "pass"
                    and receipt_complete
                    and check_keys == required_keys
                    and all(row.get("outcome") == "verified" for row in checks)
                ):
                    finding["status"] = "verified"
                    finding["verification"] = {
                        "attempt_id": attempt.get("attempt_id"),
                        "gate_id": closure_gate.get("id"),
                        "receipt_sha256": review.get("receipt_sha256"),
                        "source_ids": [
                            row.get("source_id") or row.get("id") for row in checks
                        ],
                    }
                    verified_targets.add(finding_id)
        sources: dict[str, dict[str, Any]] = {}
        for gate in attempt.get("gates", []):
            if not isinstance(gate, dict) or gate.get("status") not in {"fail", "blocked"}:
                continue
            source_id = f"gate:{gate.get('id')}"
            sources[source_id] = {
                "title": f"Required gate {gate.get('id')} {gate.get('status')}",
                "fingerprint": f"deterministic-{gate.get('id')}",
                "severity": "blocking" if gate.get("status") == "blocked" else "major",
                "category": "testability",
                "disposition": "user_decision" if gate.get("status") == "blocked" else "remediation_required",
                "evidence": list((gate.get("result") or {}).get("evidence_hashes", {}).keys())
                or [gate.get("reason") or gate.get("command")],
                "affected_paths": [],
                "closure_test": gate.get("command"),
                "detected_by": [],
            }
        for candidate in candidates:
            source_id, source = _source_from_candidate(str(attempt.get("attempt_id")), candidate, policy)
            if source_id in sources:
                raise ValueError(f"duplicate audit source ID: {source_id}")
            sources[source_id] = source
        active_ids = (
            set(attempt.get("target_finding_ids", [])) - verified_targets
            if attempt.get("mode") == "targeted"
            else {
                row.get("id")
                for row in rows
                if row.get("status") not in policy.get("findings", {}).get("terminal_statuses", [])
            }
        )
        for finding_id in active_ids:
            row = by_id.get(finding_id)
            if not isinstance(row, dict):
                continue
            sources[f"ledger:{finding_id}"] = {
                "title": row.get("title"),
                "fingerprint": row.get("fingerprint"),
                "severity": row.get("severity"),
                "category": row.get("category"),
                "disposition": row.get("disposition"),
                "evidence": row.get("evidence", []),
                "affected_paths": row.get("affected_paths", []),
                "affected_acceptance_ids": row.get("affected_acceptance_ids", []),
                "closure_test": row.get("closure_test"),
                "detected_by": row.get("detected_by", []),
            }
        # Identical fingerprints are the only admissible merge key. No model is
        # needed to union sources, select maximum severity, or enforce conflicts.
        grouped: dict[str, list[str]] = {}
        for source_id in sorted(sources):
            grouped.setdefault(str(sources[source_id]["fingerprint"]), []).append(source_id)
        severity_order = policy["findings"]["severities"]
        normalized: list[dict[str, Any]] = []
        for fingerprint, source_ids in sorted(grouped.items()):
            selected = [sources[source_id] for source_id in source_ids]
            for field in ("disposition", "category", "closure_test"):
                if len({row.get(field) for row in selected}) != 1:
                    raise ValueError(f"conflicting source {field} values block synthesis: {fingerprint}")
            disposition = selected[0]["disposition"]
            if any(
                _authority(attempt, row.get("id"), "accepted_risk", fingerprint)
                for row in attempt.get("authorities", []) if isinstance(row, dict)
            ):
                disposition = "accepted_risk"
            normalized.append({
                "fingerprint": fingerprint,
                "severity": max((row["severity"] for row in selected), key=severity_order.index),
                "category": selected[0]["category"],
                "disposition": disposition,
                "title": selected[0]["title"],
                "closure_test": selected[0]["closure_test"],
                "source_ids": source_ids,
                **{field: sorted({item for row in selected for item in row.get(field, [])})
                   for field in ("evidence", "affected_paths", "affected_acceptance_ids", "detected_by")},
            })
        status_by_disposition = policy.get("findings", {}).get("status_by_disposition", {})
        for proposal in normalized:
            existing = by_fingerprint.get(proposal["fingerprint"])
            row = existing or {
                "id": _next_finding_id(rows),
                "first_seen_attempt": attempt.get("attempt_id"),
            }
            row.update(
                {
                    **proposal,
                    "status": status_by_disposition.get(proposal["disposition"]),
                    "authority_ref": next(
                        (
                            f"{attempt.get('attempt_id')}:{authority.get('id')}"
                            for authority in attempt.get("authorities", [])
                            if isinstance(authority, dict)
                            and authority.get("kind") == "accepted_risk"
                            and authority.get("scope", {}).get("finding_fingerprint")
                            == proposal["fingerprint"]
                        ),
                        None,
                    ),
                }
            )
            if not existing:
                rows.append(row)
                by_fingerprint[row["fingerprint"]] = row
        attempt["synthesis"] = {
            "method": "deterministic-v1",
            "sources_sha256": _structured_sha256(sources),
            "source_ids": sorted(sources),
            "findings_sha256": _yaml_sha256(findings),
            "applied_at": _now(),
        }
        attempt["updated_at"] = _now()
        _atomic_write_yaml_documents([(findings_path, findings), (attempt_path, attempt)])
        print(
            f"Audit synthesis applied: attempt={attempt.get('attempt_id')} "
            f"sources={len(sources)} receipt_complete={str(receipt_complete).lower()}"
        )
        return 0


class AuditValidator:
    def __init__(
        self,
        epic_dir: Path,
        attempt_dir: Path,
        phase: str,
        policy_path: Path | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.epic_dir = epic_dir.resolve()
        self.attempt_dir = attempt_dir.resolve()
        self.phase = phase
        self.policy_path = (policy_path or _default_policy_path()).resolve()
        self.policy = _policy(self.policy_path)
        self.repo_root = (repo_root or self._infer_repo_root()).resolve()
        self.attempt: dict[str, Any] = {}
        self.findings: dict[str, Any] = {}
        self.receipt_complete = False

    def _infer_repo_root(self) -> Path:
        for candidate in (self.epic_dir, *self.epic_dir.parents):
            if (candidate / ".git").exists():
                return candidate
        return self.epic_dir.parent

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.phase not in VALIDATION_PHASES:
            return [f"unknown audit validation phase: {self.phase}"]
        try:
            _inside(self.epic_dir, self.repo_root, "epic directory")
            _inside(self.attempt_dir, self.epic_dir / "reviews", "attempt directory")
            self.attempt = _load_yaml(self.attempt_dir / "audit-attempt.yaml", "audit attempt")
            self.findings = _load_yaml(
                self.epic_dir / str(self.policy.get("paths", {}).get("findings")),
                "audit findings",
            )
        except ValueError as exc:
            return [str(exc)]
        attempt = self.attempt
        if attempt.get("schema_version") != self.policy.get("attempt_version"):
            errors.append("audit attempt schema_version does not match policy")
        if attempt.get("attempt_id") != self.attempt_dir.name:
            errors.append("audit attempt ID does not match directory")
        if attempt.get("mode") not in self.policy.get("modes", []):
            errors.append("audit attempt mode is invalid")
        if attempt.get("status") not in self.policy.get("attempt_statuses", []):
            errors.append("audit attempt status is invalid")
        if self.findings.get("schema_version") != self.policy.get("findings_version"):
            errors.append("audit findings schema_version does not match policy")
        if self.findings.get("epic_id") != attempt.get("epic_id"):
            errors.append("audit findings epic_id does not match attempt")
        errors.extend(_current_attempt_boundary(attempt, self.epic_dir, self.repo_root))
        packet_path = self.attempt_dir / str(self.policy.get("paths", {}).get("review_packet"))
        packet_errors, packet = _verify_packet(packet_path, self.epic_dir, self.repo_root, self.policy)
        errors.extend(packet_errors)
        review = attempt.get("review")
        if not isinstance(review, dict):
            errors.append("audit attempt review must be a mapping")
            review = {}
        if review.get("packet_sha256") != _file_sha256(packet_path):
            errors.append("audit attempt packet hash mismatch")
        if packet.get("assignments") != review.get("required_assignments"):
            errors.append("audit attempt review assignments differ from packet")
        if packet.get("target_findings", []) != attempt.get("target_findings", []):
            errors.append("audit packet target findings differ from attempt")
        if (
            packet.get("artifact_hashes") != attempt.get("artifact_hashes")
            or packet.get("boundary_sha256") != attempt.get("boundary_sha256")
        ):
            errors.append("audit packet artifact boundary differs from attempt")
        receipt_path = self.attempt_dir / str(self.policy.get("paths", {}).get("reviewer_receipt"))
        required_assignments = review.get("required_assignments", [])
        self.receipt_complete = not required_assignments
        if receipt_path.is_file():
            receipt_errors, _, _, _, _, self.receipt_complete = _verify_receipt(
                self.epic_dir, receipt_path, self.repo_root, self.policy
            )
            errors.extend(receipt_errors)
            if review.get("receipt_sha256") not in {None, _file_sha256(receipt_path)}:
                errors.append("audit attempt receipt hash mismatch")
        elif self.phase == "complete" and required_assignments:
            errors.append("required audit reviewer receipt is missing")
        gate_ids: list[str] = []
        for index, gate in enumerate(_mapping_list(attempt.get("gates"), "audit gates", errors)):
            label = f"audit gates[{index}]"
            gate_id = _string(gate.get("id"), f"{label}.id", errors)
            gate_ids.append(gate_id)
            status = gate.get("status")
            if status not in self.policy.get("gate_statuses", []):
                errors.append(f"{label}.status is invalid")
            if status in {"pass", "fail"}:
                errors.extend(
                    _execution_errors(
                        gate.get("result"),
                        self.repo_root,
                        label,
                        require_pass=status == "pass",
                    )
                )
                if isinstance(gate.get("result"), dict) and gate["result"].get("outcome") != status:
                    errors.append(f"{label}.result outcome differs from gate status")
            elif status == "not_applicable" and not _authority(
                attempt, gate.get("authority_id"), "gate_not_applicable", gate_id
            ):
                errors.append(f"{label} not_applicable lacks current authority")
            elif status == "blocked" and not isinstance(gate.get("reason"), str):
                errors.append(f"{label} blocked requires reason")
            elif status == "pending" and self.phase == "complete":
                errors.append(f"{label} is still pending")
        if len(gate_ids) != len(set(gate_ids)):
            errors.append("audit attempt has duplicate gate IDs")
        errors.extend(self._validate_findings())
        synthesis = attempt.get("synthesis")
        findings_path = self.epic_dir / str(self.policy.get("paths", {}).get("findings"))
        if isinstance(synthesis, dict) and synthesis.get("findings_sha256") != _file_sha256(
            findings_path
        ):
            errors.append("audit findings changed after synthesis")
        if self.phase == "complete":
            if not isinstance(synthesis, dict):
                errors.append("completed audit requires synthesis metadata")
            else:
                if synthesis.get("method") != "deterministic-v1":
                    errors.append("audit synthesis must use deterministic-v1")
                if not SHA256_PATTERN.fullmatch(str(synthesis.get("sources_sha256", ""))):
                    errors.append("audit synthesis sources_sha256 is invalid")
            expected, _ = self.derive_decision()
            if attempt.get("status") != expected or attempt.get("decision", {}).get("outcome") != expected:
                errors.append(f"audit decision is not the mechanically required outcome: {expected}")
            report = attempt.get("report")
            if not isinstance(report, dict):
                errors.append("audit attempt report must be a mapping")
            else:
                try:
                    report_path = _repo_file(self.repo_root, report.get("path"), "audit report")
                    if report.get("sha256") != _file_sha256(report_path):
                        errors.append("audit report hash mismatch")
                except ValueError as exc:
                    errors.append(str(exc))
        return errors

    def _validate_findings(self) -> list[str]:
        errors: list[str] = []
        finding_policy = self.policy.get("findings", {})
        rows = _mapping_list(self.findings.get("findings"), "audit findings", errors)
        ids: list[str] = []
        fingerprints: list[str] = []
        for index, row in enumerate(rows):
            label = f"audit findings[{index}]"
            finding_id = _string(row.get("id"), f"{label}.id", errors)
            fingerprint = _string(row.get("fingerprint"), f"{label}.fingerprint", errors)
            ids.append(finding_id)
            fingerprints.append(fingerprint)
            if not re.fullmatch(finding_policy.get("id_pattern", r"^$"), finding_id):
                errors.append(f"{label}.id does not match policy")
            if row.get("severity") not in finding_policy.get("severities", []):
                errors.append(f"{label}.severity is invalid")
            if row.get("category") not in finding_policy.get("categories", []):
                errors.append(f"{label}.category is invalid")
            disposition = row.get("disposition")
            if disposition not in finding_policy.get("dispositions", []):
                errors.append(f"{label}.disposition is invalid")
            status = row.get("status")
            if status not in finding_policy.get("statuses", []):
                errors.append(f"{label}.status is invalid")
            _string(row.get("title"), f"{label}.title", errors)
            _string_list(row.get("evidence"), f"{label}.evidence", errors)
            _string_list(row.get("affected_paths"), f"{label}.affected_paths", errors)
            _string_list(row.get("source_ids"), f"{label}.source_ids", errors)
            _string(row.get("closure_test"), f"{label}.closure_test", errors)
            if status == "accepted_risk":
                authority_ref = row.get("authority_ref")
                authority_id = authority_ref.split(":", 1)[1] if isinstance(authority_ref, str) and ":" in authority_ref else None
                if not _authority(self.attempt, authority_id, "accepted_risk", fingerprint):
                    errors.append(f"{label} accepted risk lacks current hash-bound authority")
            if status in {"remediated_pending_verification", "verified"}:
                errors.extend(_remediation_errors(row, self.repo_root, label))
            if status == "verified" and not isinstance(row.get("verification"), dict):
                errors.append(f"{label}.verification is required for verified status")
        if len(ids) != len(set(ids)):
            errors.append("audit findings contains duplicate IDs")
        if len(fingerprints) != len(set(fingerprints)):
            errors.append("audit findings contains duplicate fingerprints")
        return errors

    def derive_decision(self) -> tuple[str, str]:
        gates = self.attempt.get("gates", [])
        if any(row.get("status") == "blocked" for row in gates if isinstance(row, dict)):
            return "blocked", "one or more deterministic gates are blocked"
        if any(row.get("status") == "fail" for row in gates if isinstance(row, dict)):
            return "fail", "one or more deterministic gates failed"
        if any(row.get("status") == "pending" for row in gates if isinstance(row, dict)):
            return "blocked", "one or more deterministic gates are pending"
        if not self.receipt_complete:
            return "blocked", "required independent review is incomplete"
        if not isinstance(self.attempt.get("synthesis"), dict):
            return "blocked", "audit synthesis has not been applied"
        active = [
            row
            for row in self.findings.get("findings", [])
            if isinstance(row, dict)
            and row.get("status") not in self.policy.get("findings", {}).get("terminal_statuses", [])
        ]
        if any(row.get("disposition") == "user_decision" for row in active):
            return "blocked", "an active finding requires user authority"
        if active:
            return "fail", "one or more active findings require remediation"
        return "pass", "all required gates, reviewers, and findings are terminal"


def _render_report(attempt: Mapping[str, Any], findings: Mapping[str, Any]) -> str:
    outcome = str(attempt.get("decision", {}).get("outcome", "blocked")).upper()
    lines = [
        f"# Epic Audit: {attempt.get('epic_id')}",
        "",
        f"- Attempt: {attempt.get('attempt_id')}",
        f"- Mode: {attempt.get('mode')}",
        f"- Outcome: {outcome}",
        f"- Reason: {attempt.get('decision', {}).get('reason')}",
        "",
        "## Mechanical gates",
        "",
    ]
    for gate in attempt.get("gates", []):
        result = gate.get("result") or {}
        counts = ""
        if result:
            counts = (
                f" (exit={result.get('exit_code')}, passed={result.get('passed')}, "
                f"failed={result.get('failed')}, errors={result.get('errors')}, "
                f"skipped={result.get('skipped')})"
            )
        lines.append(f"- `{gate.get('id')}`: **{str(gate.get('status')).upper()}**{counts}")
    lines.extend(["", "## Findings", ""])
    scoped = [
        row
        for row in findings.get("findings", [])
        if isinstance(row, dict)
        and (
            row.get("first_seen_attempt") == attempt.get("attempt_id")
            or row.get("id") in attempt.get("target_finding_ids", [])
        )
    ]
    if not scoped:
        lines.append("None.")
    else:
        for row in scoped:
            lines.append(
                f"- `{row.get('id')}` [{row.get('severity')}/{row.get('status')}]: {row.get('title')}"
            )
    lines.extend(["", "## Evidence integrity", ""])
    lines.append(f"- Repository fingerprint: `{attempt.get('repository_fingerprint', {}).get('workspace_sha256')}`")
    lines.append(f"- Artifact boundary: `{attempt.get('boundary_sha256')}`")
    lines.append("")
    return "\n".join(lines)


def finalize(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (run, working_root, _):
        policy = _policy(args.policy.resolve())
        attempt_dir = _inside(args.attempt_dir.resolve(), epic_dir / "reviews", "attempt directory")
        validator = AuditValidator(epic_dir, attempt_dir, "pre_review", args.policy, working_root)
        errors = validator.validate()
        if errors:
            raise ValueError("audit finalization preconditions failed: " + "; ".join(errors))
        _require_attempt_epic(run, validator.attempt)
        outcome, reason = validator.derive_decision()
        attempt = validator.attempt
        attempt["status"] = outcome
        attempt["decision"] = {"outcome": outcome, "reason": reason}
        attempt["updated_at"] = _now()
        report_path = epic_dir / str(policy.get("paths", {}).get("report"))
        report = _render_report(attempt, validator.findings)
        _atomic_write_text(report_path, report)
        attempt["report"] = {
            "path": _repo_relative(report_path, working_root, "audit report"),
            "sha256": _file_sha256(report_path),
        }
        _atomic_write_yaml_documents([(attempt_dir / "audit-attempt.yaml", attempt)])
        completed = AuditValidator(epic_dir, attempt_dir, "complete", args.policy, working_root)
        completed_errors = completed.validate()
        if completed_errors:
            raise ValueError("finalized audit failed validation: " + "; ".join(completed_errors))
        print(f"Audit finalized: attempt={attempt.get('attempt_id')} outcome={outcome.upper()}")
        return 0


def validate_command(args: argparse.Namespace) -> int:
    validator = AuditValidator(
        args.epic_dir, args.attempt_dir, args.phase, args.policy, args.repo_root
    )
    errors = validator.validate()
    if errors:
        print(f"Audit artifact validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Audit artifact validation passed: phase={args.phase} attempt={args.attempt_dir}")
    return 0


def verify_evidence_command(args: argparse.Namespace) -> int:
    policy = _policy(args.policy.resolve())
    repo_root = (args.repo_root or Path.cwd()).resolve()
    errors, _, _ = verify_implementation_evidence(
        args.epic_dir.resolve(), repo_root, policy, args.story
    )
    if errors:
        print(f"Implementation evidence verification failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Implementation evidence verified: epic={args.epic_dir}")
    return 0


def fingerprint_command(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or Path.cwd()).resolve()
    print(json.dumps(repository_fingerprint(args.epic_dir.resolve(), repo_root), sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint_parser = subparsers.add_parser("fingerprint", help="Print the current implementation fingerprint")
    fingerprint_parser.add_argument("epic_dir", type=Path)
    fingerprint_parser.add_argument("--repo-root", type=Path)
    fingerprint_parser.set_defaults(handler=fingerprint_command)

    evidence_parser = subparsers.add_parser("verify-evidence", help="Verify durable implementation evidence")
    evidence_parser.add_argument("epic_dir", type=Path)
    evidence_parser.add_argument("--repo-root", type=Path)
    evidence_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    evidence_parser.add_argument("--story", default="")
    evidence_parser.set_defaults(handler=verify_evidence_command)

    prepare_parser = subparsers.add_parser("prepare", help="Prepare one canonical audit attempt")
    prepare_parser.add_argument("epic_dir", type=Path)
    prepare_parser.add_argument("--run", type=Path, required=True)
    prepare_parser.add_argument("--mode", choices=("full", "targeted"), required=True)
    prepare_parser.add_argument("--finding", action="append", default=[])
    prepare_parser.add_argument("--reason", default="")
    prepare_parser.add_argument("--reviewer-profile", choices=("default", "budget"), default="default")
    prepare_parser.add_argument("--reviewer-set", choices=("standard", "expanded"), default="standard")
    prepare_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    prepare_parser.set_defaults(handler=prepare)

    authority_parser = subparsers.add_parser("record-authority", help="Record hash-bound audit authority")
    authority_parser.add_argument("epic_dir", type=Path)
    authority_parser.add_argument("attempt_dir", type=Path)
    authority_parser.add_argument("--run", type=Path, required=True)
    authority_parser.add_argument("--authority-id", required=True)
    authority_parser.add_argument("--kind", choices=("gate_not_applicable", "accepted_risk"), required=True)
    authority_parser.add_argument("--subject", required=True)
    authority_parser.add_argument("--source", choices=("user", "preapproval"), required=True)
    authority_parser.add_argument("--decision", default="approved")
    authority_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    authority_parser.set_defaults(handler=record_authority)

    gate_parser = subparsers.add_parser("record-gate", help="Record one exact deterministic gate result")
    gate_parser.add_argument("epic_dir", type=Path)
    gate_parser.add_argument("attempt_dir", type=Path)
    gate_parser.add_argument("--run", type=Path, required=True)
    gate_parser.add_argument("--gate", required=True)
    gate_parser.add_argument("--status", choices=("pass", "fail", "blocked", "not_applicable"), required=True)
    gate_parser.add_argument("--exit-code", type=int)
    gate_parser.add_argument("--passed", type=int)
    gate_parser.add_argument("--failed", type=int)
    gate_parser.add_argument("--errors", type=int)
    gate_parser.add_argument("--skipped", type=int)
    gate_parser.add_argument("--summary", default="")
    gate_parser.add_argument("--skip-reason")
    gate_parser.add_argument("--evidence", type=Path, action="append", default=[])
    gate_parser.add_argument("--reason", default="")
    gate_parser.add_argument("--authority-id")
    gate_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    gate_parser.set_defaults(handler=record_gate)

    execute_parser = subparsers.add_parser("execute-gates", help="Execute or reuse current approved proof results")
    execute_parser.add_argument("epic_dir", type=Path)
    execute_parser.add_argument("attempt_dir", type=Path)
    execute_parser.add_argument("--run", type=Path, required=True)
    execute_parser.set_defaults(handler=execute_gates)

    synthesis_parser = subparsers.add_parser("apply-synthesis", help="Synthesize every validated audit source deterministically")
    synthesis_parser.add_argument("epic_dir", type=Path)
    synthesis_parser.add_argument("attempt_dir", type=Path)
    synthesis_parser.add_argument("--run", type=Path, required=True)
    synthesis_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    synthesis_parser.set_defaults(handler=apply_synthesis)

    finalize_parser = subparsers.add_parser("finalize", help="Derive and publish the final audit decision")
    finalize_parser.add_argument("epic_dir", type=Path)
    finalize_parser.add_argument("attempt_dir", type=Path)
    finalize_parser.add_argument("--run", type=Path, required=True)
    finalize_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    finalize_parser.set_defaults(handler=finalize)

    validate_parser = subparsers.add_parser("validate", help="Validate one audit boundary")
    validate_parser.add_argument("epic_dir", type=Path)
    validate_parser.add_argument("attempt_dir", type=Path)
    validate_parser.add_argument("--phase", choices=VALIDATION_PHASES, required=True)
    validate_parser.add_argument("--repo-root", type=Path)
    validate_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    validate_parser.set_defaults(handler=validate_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Audit artifact operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

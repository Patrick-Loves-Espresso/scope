#!/usr/bin/env python3
"""Build and validate Scope's lean refinement artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import scope_git  # noqa: E402


import scope_snapshot

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
FULL_OBJECT_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
PHASES = ("product", "review", "handoff")
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
    return Path(__file__).resolve().parent.parent / "config" / "refinement-policy.yaml"


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
                yaml.safe_dump(
                    dict(document),
                    handle,
                    sort_keys=False,
                    allow_unicode=True,
                )
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
    if path.is_absolute() or value != path.as_posix() or any(
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
    resolved = _inside(path, repo_root, label)
    return resolved.relative_to(repo_root.resolve()).as_posix()


def _policy(path: Path) -> dict[str, Any]:
    policy = _load_yaml(path, "refinement policy")
    if policy.get("schema_version") != 3:
        raise ValueError("refinement policy schema_version must be 3")
    return policy


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
def _mutation_guard(run_path: Path, epic_dir: Path) -> Iterator[tuple[dict[str, Any], Path]]:
    resolved_run = run_path.resolve(strict=True)
    run = _load_yaml(resolved_run, "worker run")
    if run.get("schema_version") != 2:
        raise ValueError("worker run schema_version must be 2")
    if run.get("command") != "epic_refine":
        raise ValueError("refinement mutation requires an epic_refine run")
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
        / "epic_refine"
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
        yield run, working_root


def _hash_map_errors(
    value: Any,
    repo_root: Path,
    label: str,
    *,
    require_nonempty: bool = True,
    check_current: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or (require_nonempty and not value):
        return [f"{label} must be a non-empty path-to-sha256 mapping"]
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
        if check_current:
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
    skipped = values.get("skipped", 0)
    if skipped and not isinstance(row.get("skip_reason"), str):
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
    errors.extend(
        _hash_map_errors(row.get("evidence_hashes"), repo_root, f"{label}.evidence_hashes")
    )
    return errors


def _boundary(paths: Iterable[Path], repo_root: Path) -> tuple[dict[str, str], str]:
    hashes: dict[str, str] = {}
    for path in sorted({item.resolve() for item in paths}, key=lambda item: str(item)):
        relative = _repo_relative(path, repo_root, "approval artifact")
        if relative == "tmp_debug" or relative.startswith("tmp_debug/"):
            raise ValueError(f"approval boundary cannot include tmp_debug: {relative}")
        hashes[relative] = _file_sha256(path)
    if not hashes:
        raise ValueError("approval boundary cannot be empty")
    return hashes, _structured_sha256(hashes)


import scope_proofs


class RefinementValidator:
    def __init__(
        self,
        epic_dir: Path,
        phase: str,
        policy_path: Path | None = None,
        repo_root: Path | None = None,
        review_ready_only: bool = False,
    ) -> None:
        self.epic_dir = epic_dir.resolve()
        self.phase = phase
        self.policy_path = (policy_path or _default_policy_path()).resolve()
        self.policy = _policy(self.policy_path)
        self.repo_root = (repo_root or self._infer_repo_root()).resolve()
        self.review_ready_only = review_ready_only
        self.errors: list[str] = []
        self.manifest: dict[str, Any] = {}
        self.state: dict[str, Any] = {}
        self.findings: dict[str, Any] = {}
        self.authorities: dict[str, dict[str, Any]] = {}

    def _infer_repo_root(self) -> Path:
        current = self.epic_dir
        for candidate in (current, *current.parents):
            if (candidate / ".git").exists():
                return candidate
        return self.epic_dir.parents[2] if len(self.epic_dir.parents) >= 3 else self.epic_dir.parent

    def validate(self) -> list[str]:
        self.errors = []
        if self.phase not in PHASES:
            return [f"unknown refinement phase: {self.phase}"]
        try:
            _inside(self.epic_dir, self.repo_root, "epic directory")
        except ValueError as exc:
            return [str(exc)]
        phase_policy = self.policy.get("phases", {}).get(self.phase, {})
        required = phase_policy.get("required_artifacts", [])
        for name in required:
            if not (self.epic_dir / name).is_file():
                self.errors.append(f"missing required artifact for {self.phase}: {name}")
        for name in self.policy.get("obsolete_artifacts", []):
            if (self.epic_dir / str(name)).exists():
                self.errors.append(f"obsolete duplicate lifecycle artifact must be removed: {name}")
        try:
            self.manifest = _load_yaml(self.epic_dir / "delivery-manifest.yaml", "delivery manifest")
        except ValueError as exc:
            self.errors.append(str(exc))
            return self.errors
        state_path = self.epic_dir / "refinement-state.yaml"
        if state_path.is_file():
            try:
                self.state = _load_yaml(state_path, "refinement state")
            except ValueError as exc:
                self.errors.append(str(exc))
                return self.errors
        elif self.phase == "product":
            self.state = _initial_state(
                str(self.manifest.get("epic_id", "")),
                int(self.policy.get("refinement_state_version", 0)),
            )
        else:
            self.errors.append(f"missing refinement state: {state_path}")
            return self.errors
        if (self.epic_dir / "refinement-findings.yaml").is_file():
            try:
                self.findings = _load_yaml(
                    self.epic_dir / "refinement-findings.yaml", "refinement findings"
                )
            except ValueError as exc:
                self.errors.append(str(exc))
        self._validate_state()
        self._validate_manifest()
        if self.findings:
            self._validate_findings()
        if self.phase in {"review", "handoff"}:
            self._require_gate("product_contract")
            if not self.review_ready_only:
                self._validate_completed_reviews()
                self._require_no_blockers()
        if self.phase == "handoff":
            if self.state.get("status") != "approved":
                self.errors.append("refinement handoff state must be approved")
            self._require_gate("final_handoff")
        return self.errors

    def _validate_state(self) -> None:
        path = self.epic_dir / "refinement-state.yaml"
        if self.state.get("schema_version") != self.policy.get("refinement_state_version"):
            self.errors.append(f"{path} schema_version does not match policy")
        epic_id = _string(self.state.get("epic_id"), f"{path}.epic_id", self.errors)
        if self.state.get("status") not in self.policy.get("state", {}).get("statuses", []):
            self.errors.append(f"{path}.status is not allowed by policy")
        rows = _mapping_list(self.state.get("user_decisions"), f"{path}.user_decisions", self.errors)
        ids: list[str] = []
        allowed_kinds = set(self.policy.get("state", {}).get("authority_kinds", []))
        allowed_sources = set(self.policy.get("state", {}).get("authority_sources", []))
        for index, row in enumerate(rows):
            label = f"{path}.user_decisions[{index}]"
            authority_id = _string(row.get("id"), f"{label}.id", self.errors)
            ids.append(authority_id)
            kind = row.get("kind")
            if kind not in allowed_kinds:
                self.errors.append(f"{label}.kind is not allowed by policy")
            if row.get("source") not in allowed_sources:
                self.errors.append(f"{label}.source is not allowed by policy")
            _string(row.get("decision"), f"{label}.decision", self.errors)
            _string(row.get("decided_at"), f"{label}.decided_at", self.errors)
            scope = row.get("scope")
            if not isinstance(scope, dict) or scope.get("epic_id") != epic_id:
                self.errors.append(f"{label}.scope must bind the current epic")
            hashes = row.get("artifact_hashes")
            self.errors.extend(
                _hash_map_errors(
                    hashes,
                    self.repo_root,
                    f"{label}.artifact_hashes",
                    check_current=kind == "accepted_risk",
                )
            )
            if isinstance(hashes, dict):
                expected_boundary = _structured_sha256(hashes)
                if row.get("boundary_sha256") != expected_boundary:
                    self.errors.append(f"{label}.boundary_sha256 does not match artifact_hashes")
            if authority_id:
                self.authorities[authority_id] = row
        if len(ids) != len(set(ids)):
            self.errors.append(f"{path}.user_decisions contains duplicate IDs")
        completed = _string_list(
            self.state.get("completed_review_ids"),
            f"{path}.completed_review_ids",
            self.errors,
        )
        active = self.state.get("active_findings")
        if not isinstance(active, dict) or active.get("path") != "refinement-findings.yaml":
            self.errors.append(f"{path}.active_findings must reference refinement-findings.yaml")
        if self.state.get("status") == "approved" and not any(
            row.get("kind") == "final_handoff" and row.get("decision") == "approved"
            for row in rows
        ):
            self.errors.append(f"{path}.status approved requires a final_handoff authority")
        for review_id in completed:
            if not (self.epic_dir / "reviews" / review_id / "review-packet.yaml").is_file():
                self.errors.append(f"completed review does not exist: {review_id}")

    def _validate_manifest(self) -> None:
        path = self.epic_dir / "delivery-manifest.yaml"
        policy = self.policy.get("manifest", {})
        manifest_version = self.manifest.get("schema_version")
        compatible_versions = self.policy.get(
            "compatible_delivery_manifest_versions",
            [self.policy.get("delivery_manifest_version")],
        )
        if manifest_version not in compatible_versions:
            self.errors.append(f"{path} schema_version does not match policy")
        epic_id = _string(self.manifest.get("epic_id"), f"{path}.epic_id", self.errors)
        if not _same_epic_id(epic_id, self.state.get("epic_id")):
            self.errors.append("delivery-manifest.yaml and refinement-state.yaml epic_id differ")
        if self.manifest.get("risk_level") not in self.policy.get("risk_levels", []):
            self.errors.append(f"{path}.risk_level is not allowed by policy")
        if self.manifest.get("author_provider") not in self.policy.get("author_providers", []):
            self.errors.append(f"{path}.author_provider is not allowed by policy")
        capabilities = _string_list(self.manifest.get("capabilities"), f"{path}.capabilities", self.errors)
        unknown_capabilities = sorted(set(capabilities) - set(self.policy.get("capabilities", [])))
        if unknown_capabilities:
            self.errors.append(f"{path}.capabilities contains unknown values: {unknown_capabilities}")
        acceptance_ids = _string_list(
            self.manifest.get("acceptance_ids"), f"{path}.acceptance_ids", self.errors
        )
        if not acceptance_ids:
            self.errors.append(f"{path}.acceptance_ids cannot be empty")
        acceptance_path = self.epic_dir / "acceptance-criteria.md"
        if acceptance_path.is_file():
            text = acceptance_path.read_text(encoding="utf-8")
            for acceptance_id in acceptance_ids:
                if acceptance_id not in text:
                    self.errors.append(
                        f"acceptance ID {acceptance_id} is absent from acceptance-criteria.md"
                    )
        dependencies = _mapping_list(
            self.manifest.get("dependencies"), f"{path}.dependencies", self.errors
        )
        dependency_ids: list[str] = []
        for index, row in enumerate(dependencies):
            label = f"{path}.dependencies[{index}]"
            dependency_ids.append(_string(row.get("epic_id"), f"{label}.epic_id", self.errors))
            commit = row.get("commit")
            if not isinstance(commit, str) or not FULL_OBJECT_ID_PATTERN.fullmatch(commit):
                self.errors.append(f"{label}.commit must be a full 40- or 64-hex object ID")
        if len(dependency_ids) != len(set(dependency_ids)):
            self.errors.append(f"{path}.dependencies contains duplicate epic_id values")
        ownership = _mapping_list(
            self.manifest.get("artifact_ownership"), f"{path}.artifact_ownership", self.errors
        )
        owned_paths: list[str] = []
        for index, row in enumerate(ownership):
            label = f"{path}.artifact_ownership[{index}]"
            try:
                owned = _relative(row.get("path"), f"{label}.path")
                _epic_file(self.epic_dir, owned, f"{label}.path")
                owned_paths.append(owned)
            except ValueError as exc:
                self.errors.append(str(exc))
            _string(row.get("owner"), f"{label}.owner", self.errors)
            if row.get("authority") not in policy.get("artifact_authorities", []):
                self.errors.append(f"{label}.authority is not allowed by policy")
        if len(owned_paths) != len(set(owned_paths)):
            self.errors.append(f"{path}.artifact_ownership contains duplicate paths")
        decisions = _mapping_list(self.manifest.get("decisions"), f"{path}.decisions", self.errors)
        decision_ids: list[str] = []
        for index, row in enumerate(decisions):
            label = f"{path}.decisions[{index}]"
            decision_id = _string(row.get("id"), f"{label}.id", self.errors)
            decision_ids.append(decision_id)
            _string(row.get("statement"), f"{label}.statement", self.errors)
            status = row.get("status")
            if status not in policy.get("decision_statuses", []):
                self.errors.append(f"{label}.status is not allowed by policy")
            authority_id = row.get("authority_id")
            if status == "decided":
                authority = self.authorities.get(authority_id)
                if not authority or authority.get("kind") != "product_decision":
                    self.errors.append(f"{label}.authority_id must reference product_decision authority")
                elif authority.get("scope", {}).get("decision_id") != decision_id:
                    self.errors.append(f"{label}.authority scope does not bind decision {decision_id}")
                elif row.get("statement") != authority.get("decision"):
                    self.errors.append(
                        f"{label}.statement must equal the authority's exact decision text"
                    )
            elif authority_id not in {None, ""}:
                self.errors.append(f"{label}.authority_id must be null while pending")
        if len(decision_ids) != len(set(decision_ids)):
            self.errors.append(f"{path}.decisions contains duplicate IDs")
        proofs = _mapping_list(self.manifest.get("proofs"), f"{path}.proofs", self.errors)
        if manifest_version == 3 and self.phase in {"review", "handoff"} and not proofs:
            self.errors.append(f"{path}: executable handoff requires proof obligations")
        proof_ids: list[str] = []
        for index, row in enumerate(proofs):
            label = f"{path}.proofs[{index}]"
            proof_id = _string(row.get("id"), f"{label}.id", self.errors)
            proof_ids.append(proof_id)
            if manifest_version == 3 and row.get("classification") != "external_blocked":
                try:
                    scope_proofs.execution(row, self.repo_root, scope_proofs.policy(), require_cwd=row.get("classification") != "implementation_created")
                except (ValueError, TypeError) as exc:
                    self.errors.append(f"{label}: {exc}")
            classification = row.get("classification")
            if classification not in policy.get("proof_classifications", []):
                self.errors.append(f"{label}.classification is not allowed by policy")
            if row.get("level") not in policy.get("proof_levels", []):
                self.errors.append(f"{label}.level is not allowed by policy")
            _string(row.get("expected_result"), f"{label}.expected_result", self.errors)
            if classification == "existing_runnable":
                command = _string(row.get("command"), f"{label}.command", self.errors)
                evidence = row.get("baseline_evidence")
                self.errors.extend(
                    _execution_errors(evidence, self.repo_root, f"{label}.baseline_evidence", require_pass=False)
                )
                if isinstance(evidence, dict) and command and evidence.get("command") != command:
                    self.errors.append(f"{label}.baseline_evidence.command differs from planned command")
            elif classification == "implementation_created":
                _string(row.get("path"), f"{label}.path", self.errors)
                _string(row.get("command"), f"{label}.command", self.errors)
                if row.get("baseline_evidence") not in (None, {}):
                    self.errors.append(f"{label} implementation-created proof must not be run during refinement")
            elif classification == "external_blocked":
                _string(row.get("blocker"), f"{label}.blocker", self.errors)
                _string(row.get("substitute"), f"{label}.substitute", self.errors)
        if len(proof_ids) != len(set(proof_ids)):
            self.errors.append(f"{path}.proofs contains duplicate IDs")
        stories = _mapping_list(self.manifest.get("stories"), f"{path}.stories", self.errors)
        story_ids: list[str] = []
        assigned_acceptance: list[str] = []
        assigned_proofs: list[str] = []
        for index, row in enumerate(stories):
            label = f"{path}.stories[{index}]"
            story_id = _string(row.get("id"), f"{label}.id", self.errors)
            story_ids.append(story_id)
            acceptance_refs = _string_list(row.get("acceptance_ids"), f"{label}.acceptance_ids", self.errors)
            proof_refs = _string_list(row.get("proof_ids"), f"{label}.proof_ids", self.errors)
            assigned_acceptance.extend(acceptance_refs)
            assigned_proofs.extend(proof_refs)
            unknown_acceptance = sorted(set(acceptance_refs) - set(acceptance_ids))
            unknown_proofs = sorted(set(proof_refs) - set(proof_ids))
            if unknown_acceptance:
                self.errors.append(f"{label} references unknown acceptance IDs: {unknown_acceptance}")
            if unknown_proofs:
                self.errors.append(f"{label} references unknown proof IDs: {unknown_proofs}")
            try:
                plan_relative = _relative(row.get("plan_path"), f"{label}.plan_path")
                if not PurePosixPath(plan_relative).name.startswith("file-plan-story-"):
                    self.errors.append(f"{label}.plan_path must name file-plan-story-*.yaml")
                plan = _load_yaml(_epic_file(self.epic_dir, plan_relative, f"{label}.plan_path"), "story plan")
                if plan.get("epic_id") != epic_id or plan.get("story_id") != story_id:
                    self.errors.append(f"{label}.plan_path epic_id/story_id does not match manifest")
                if plan.get("acceptance_ids") != acceptance_refs:
                    self.errors.append(f"{label}.plan_path acceptance_ids differ from manifest")
                if plan.get("proof_ids") != proof_refs:
                    self.errors.append(f"{label}.plan_path proof_ids differ from manifest")
            except ValueError as exc:
                if self.phase in {"review", "handoff"}:
                    self.errors.append(str(exc))
        if manifest_version == 3:
            try:
                scope_proofs.groups(self.manifest, scope_proofs.policy()["maximum_stories_per_group"])
            except (ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"invalid story dependency graph: {exc}")
        if len(story_ids) != len(set(story_ids)):
            self.errors.append(f"{path}.stories contains duplicate IDs")
        if manifest_version == 1:
            documentation_obligations = self.manifest.get(
                "documentation_obligations", []
            )
            if documentation_obligations != []:
                self.errors.append(
                    f"{path}.documentation_obligations requires schema_version 2"
                )
                documentation_obligations = []
        else:
            if "documentation_obligations" not in self.manifest:
                self.errors.append(
                    f"{path}.documentation_obligations is required for schema_version 2"
                )
            documentation_obligations = _mapping_list(
                self.manifest.get("documentation_obligations"),
                f"{path}.documentation_obligations",
                self.errors,
            )
        obligation_ids: list[str] = []
        obligation_path_owners: dict[str, str] = {}
        expected_obligation_fields = {"id", "story", "path", "requirement_ref"}
        obligation_pattern = policy.get(
            "documentation_obligation_id_pattern", r"^$"
        )
        design_path = self.epic_dir / "design.md"
        design_text = (
            design_path.read_text(encoding="utf-8") if design_path.is_file() else ""
        )
        for index, row in enumerate(documentation_obligations):
            label = f"{path}.documentation_obligations[{index}]"
            if set(row) != expected_obligation_fields:
                self.errors.append(
                    f"{label} fields must be exactly {sorted(expected_obligation_fields)}"
                )
            obligation_id = _string(row.get("id"), f"{label}.id", self.errors)
            obligation_ids.append(obligation_id)
            if obligation_id and not re.fullmatch(obligation_pattern, obligation_id):
                self.errors.append(f"{label}.id does not match policy")
            story_ref = _string(row.get("story"), f"{label}.story", self.errors)
            if story_ref and story_ref not in set(story_ids):
                self.errors.append(f"{label}.story references unknown story: {story_ref}")
            try:
                relative = _relative(row.get("path"), f"{label}.path")
                target = _inside(
                    self.repo_root / relative,
                    self.repo_root,
                    f"{label}.path",
                    must_exist=False,
                )
                if target.exists() and not target.is_file():
                    self.errors.append(f"{label}.path is not a file: {relative}")
                prior_owner = obligation_path_owners.get(relative)
                if prior_owner is not None and prior_owner != story_ref:
                    self.errors.append(
                        f"{label}.path has conflicting owner stories: "
                        f"{prior_owner}, {story_ref}"
                    )
                elif story_ref:
                    obligation_path_owners[relative] = story_ref
            except ValueError as exc:
                self.errors.append(str(exc))
            requirement_ref = _string(
                row.get("requirement_ref"),
                f"{label}.requirement_ref",
                self.errors,
            )
            if obligation_id and requirement_ref and obligation_id not in requirement_ref:
                self.errors.append(
                    f"{label}.requirement_ref must contain {obligation_id}"
                )
            if obligation_id and not re.search(
                rf"^###\s+{re.escape(obligation_id)}(?:\s*:|\s*$)",
                design_text,
                flags=re.MULTILINE,
            ):
                self.errors.append(
                    f"{label}.id has no matching design.md heading: ### {obligation_id}"
                )
        if len(obligation_ids) != len(set(obligation_ids)):
            self.errors.append(f"{path}.documentation_obligations contains duplicate IDs")
        if self.phase in {"review", "handoff"}:
            for name, expected, assigned in (
                ("acceptance", acceptance_ids, assigned_acceptance),
                ("proof", proof_ids, assigned_proofs),
            ):
                if sorted(expected) != sorted(assigned) or len(assigned) != len(set(assigned)):
                    self.errors.append(f"every {name} ID must be assigned to exactly one story")
            required_owned = {
                "details.md",
                "acceptance-criteria.md",
                "design.md",
                "delivery-manifest.yaml",
                *(row.get("plan_path") for row in stories if isinstance(row.get("plan_path"), str)),
            }
            missing_owned = sorted(required_owned - set(owned_paths))
            if missing_owned:
                self.errors.append(f"artifact_ownership is missing canonical paths: {missing_owned}")

    def _validate_findings(self) -> None:
        path = self.epic_dir / "refinement-findings.yaml"
        if self.findings.get("schema_version") != self.policy.get("findings_version"):
            self.errors.append(f"{path} schema_version does not match policy")
        if not _same_epic_id(
            self.findings.get("epic_id"), self.manifest.get("epic_id")
        ):
            self.errors.append(f"{path}.epic_id does not match delivery manifest")
        finding_policy = self.policy.get("findings", {})
        rows = _mapping_list(self.findings.get("findings"), f"{path}.findings", self.errors)
        ids: list[str] = []
        fingerprints: list[str] = []
        for index, row in enumerate(rows):
            label = f"{path}.findings[{index}]"
            finding_id = _string(row.get("id"), f"{label}.id", self.errors)
            fingerprint = _string(row.get("fingerprint"), f"{label}.fingerprint", self.errors)
            ids.append(finding_id)
            fingerprints.append(fingerprint)
            if not re.fullmatch(finding_policy.get("id_pattern", r"^$"), finding_id):
                self.errors.append(f"{label}.id does not match policy")
            if row.get("severity") not in finding_policy.get("severities", []):
                self.errors.append(f"{label}.severity is not allowed")
            if row.get("category") not in finding_policy.get("categories", []):
                self.errors.append(f"{label}.category is not allowed")
            status = row.get("status")
            if status not in finding_policy.get("statuses", []):
                self.errors.append(f"{label}.status is not allowed")
            _string(row.get("title"), f"{label}.title", self.errors)
            _string_list(row.get("evidence"), f"{label}.evidence", self.errors)
            _string_list(
                row.get("affected_acceptance_ids"),
                f"{label}.affected_acceptance_ids",
                self.errors,
            )
            source_ids = _string_list(
                row.get("source_candidate_ids"), f"{label}.source_candidate_ids", self.errors
            )
            if not source_ids:
                self.errors.append(f"{label}.source_candidate_ids cannot be empty")
            _string(row.get("closure_test"), f"{label}.closure_test", self.errors)
            resolution = row.get("resolution")
            if status in finding_policy.get("closure_statuses", []):
                if not isinstance(resolution, dict):
                    self.errors.append(f"{label}.resolution is required for {status}")
                else:
                    affected = _string_list(
                        resolution.get("affected_paths"), f"{label}.resolution.affected_paths", self.errors
                    )
                    hashes = resolution.get("affected_path_hashes")
                    self.errors.extend(
                        _hash_map_errors(hashes, self.repo_root, f"{label}.resolution.affected_path_hashes")
                    )
                    if isinstance(hashes, dict) and set(affected) != set(hashes):
                        self.errors.append(f"{label}.resolution affected paths and hashes differ")
                    resolution_sources = _string_list(
                        resolution.get("source_candidate_ids"),
                        f"{label}.resolution.source_candidate_ids",
                        self.errors,
                    )
                    if not set(resolution_sources).issubset(source_ids):
                        self.errors.append(f"{label}.resolution references unknown source candidate IDs")
                    checks = _mapping_list(
                        resolution.get("checks"), f"{label}.resolution.checks", self.errors
                    )
                    if not checks:
                        self.errors.append(f"{label}.resolution.checks cannot be empty")
                    for check_index, check in enumerate(checks):
                        self.errors.extend(
                            _execution_errors(
                                check,
                                self.repo_root,
                                f"{label}.resolution.checks[{check_index}]",
                                require_pass=True,
                            )
                        )
                    if status == "verified":
                        verification = resolution.get("verification")
                        if not isinstance(verification, dict):
                            self.errors.append(f"{label}.resolution.verification is required for verified")
                        else:
                            _string(verification.get("review_id"), f"{label}.verification.review_id", self.errors)
                            receipt_hash = verification.get("receipt_sha256")
                            if not isinstance(receipt_hash, str) or not SHA256_PATTERN.fullmatch(receipt_hash):
                                self.errors.append(f"{label}.verification.receipt_sha256 is invalid")
            elif status == "deferred":
                completed = self.state.get("completed_review_ids", [])
                review_id = row.get("deferred_review_id")
                minimum = int(self.policy["review"]["minor_optional_from_review"])
                if row.get("severity") != "minor" or review_id not in completed or completed.index(review_id) + 1 < minimum:
                    self.errors.append(f"{label}: only minor findings after {minimum} completed reviews may be deferred")
                else:
                    receipt_path = self.epic_dir / "reviews" / review_id / "reviewer-receipt.yaml"
                    if not receipt_path.is_file() or row.get("deferred_receipt_sha256") != _file_sha256(receipt_path):
                        self.errors.append(f"{label}: deferred review receipt is missing or stale")
                    else:
                        receipt_errors, _, packet, _, verifications, complete = _verify_receipt(
                            self.epic_dir,
                            receipt_path,
                            self.repo_root,
                            self.policy,
                            check_template_current=False,
                        )
                        self.errors.extend(receipt_errors)
                        if not complete or packet.get("review_kind") != "targeted" or not any(
                            check.get("fingerprint") == fingerprint and check.get("outcome") == "still_open"
                            for check in verifications
                        ):
                            self.errors.append(f"{label}: deferred finding requires a completed still-open targeted review")
            elif status == "accepted_risk":
                if not isinstance(resolution, dict):
                    self.errors.append(f"{label}.resolution is required for accepted_risk")
                else:
                    authority = self.authorities.get(resolution.get("authority_id"))
                    if (
                        not authority
                        or authority.get("kind") != "accepted_risk"
                        or authority.get("decision") != "approved"
                        or authority.get("scope", {}).get("finding_fingerprint") != fingerprint
                    ):
                        self.errors.append(f"{label}.resolution requires current hash-bound accepted-risk authority")
            elif resolution not in (None, {}):
                self.errors.append(f"{label}.resolution must be null while open")
            if status == "verified" and isinstance(resolution, dict):
                verification = resolution.get("verification")
                if isinstance(verification, dict):
                    review_id = verification.get("review_id")
                    receipt_path = self.epic_dir / "reviews" / str(review_id) / "reviewer-receipt.yaml"
                    receipt_errors, _, packet, _, verifications, complete = _verify_receipt(
                        self.epic_dir,
                        receipt_path,
                        self.repo_root,
                        self.policy,
                        check_template_current=False,
                    )
                    self.errors.extend(receipt_errors)
                    if not complete or packet.get("review_kind") != "targeted":
                        self.errors.append(
                            f"{label}.resolution.verification must reference a complete targeted review"
                        )
                    targeted = {
                        row.get("fingerprint"): row
                        for row in packet.get("target_findings", [])
                        if isinstance(row, dict)
                    }
                    if fingerprint not in targeted:
                        self.errors.append(
                            f"{label}.resolution.verification packet did not target this fingerprint"
                        )
                    stored_sources = verification.get("sources")
                    if not isinstance(stored_sources, list):
                        self.errors.append(
                            f"{label}.resolution.verification.sources must be a list"
                        )
                        stored_sources = []
                    actual_sources = [
                        {
                            "provider": row.get("provider"),
                            "mission": row.get("mission"),
                            "evidence": row.get("evidence"),
                            "source_candidate_ids": row.get("source_candidate_ids", []),
                            "output_sha256": row.get("output_sha256"),
                        }
                        for row in verifications
                        if row.get("fingerprint") == fingerprint
                        and row.get("outcome") == "verified"
                    ]
                    required_assignments = targeted.get(fingerprint, {}).get(
                        "required_assignments", []
                    )
                    expected_keys = {
                        (row.get("provider"), row.get("mission"))
                        for row in required_assignments
                        if isinstance(row, dict)
                    }
                    actual_keys = {
                        (row.get("provider"), row.get("mission"))
                        for row in actual_sources
                    }
                    if (
                        stored_sources != actual_sources
                        or actual_keys != expected_keys
                        or len(actual_sources) != len(expected_keys)
                    ):
                        self.errors.append(
                            f"{label}.resolution.verification sources differ from the actual receipt"
                        )
                    if receipt_path.is_file() and verification.get("receipt_sha256") != _file_sha256(receipt_path):
                        self.errors.append(
                            f"{label}.resolution.verification receipt hash does not match the actual receipt"
                        )
        if len(ids) != len(set(ids)):
            self.errors.append(f"{path}.findings contains duplicate IDs")
        if len(fingerprints) != len(set(fingerprints)):
            self.errors.append(f"{path}.findings contains duplicate fingerprints")

    def _gate_paths(self, gate: str) -> list[Path]:
        boundary = self.policy.get("gate_boundaries", {}).get(gate)
        if not isinstance(boundary, dict):
            raise ValueError(f"unknown gate boundary: {gate}")
        paths = [_epic_file(self.epic_dir, name, f"{gate} artifact") for name in boundary.get("paths", [])]
        if boundary.get("include_story_plans"):
            paths.extend(sorted(self.epic_dir.glob("file-plan-story-*.yaml")))
        if boundary.get("include_owned_artifacts"):
            for row in self.manifest.get("artifact_ownership", []):
                if not isinstance(row, dict) or row.get("authority") not in {"canonical", "evidence"}:
                    continue
                relative = row.get("path")
                if isinstance(relative, str) and not relative.startswith("reviews/"):
                    paths.append(_epic_file(self.epic_dir, relative, f"{gate} owned artifact"))
        if boundary.get("include_completed_reviews"):
            for review_id in self.state.get("completed_review_ids", []):
                review_dir = self.epic_dir / "reviews" / review_id
                for name in ("review-packet.yaml", "reviewer-receipt.yaml"):
                    paths.append(_epic_file(review_dir, name, f"completed review {review_id}"))
                receipt = _load_yaml(review_dir / "reviewer-receipt.yaml", "reviewer receipt")
                for row in receipt.get("assignments", []):
                    output = row.get("paths", {}).get("output") if isinstance(row, dict) else None
                    if isinstance(output, str):
                        paths.append(_repo_file(self.repo_root, output, "review output"))
        if gate == "final_handoff":
            for finding in self.findings.get("findings", []):
                if not isinstance(finding, dict):
                    continue
                resolution = finding.get("resolution")
                if not isinstance(resolution, dict):
                    continue
                hash_maps = [resolution.get("affected_path_hashes")]
                hash_maps.extend(
                    check.get("evidence_hashes")
                    for check in resolution.get("checks", [])
                    if isinstance(check, dict)
                )
                for hashes in hash_maps:
                    if not isinstance(hashes, dict):
                        continue
                    for relative in hashes:
                        paths.append(
                            _repo_file(
                                self.repo_root,
                                relative,
                                "final handoff resolution evidence",
                            )
                        )
        return paths

    def _require_gate(self, gate: str) -> None:
        try:
            hashes, boundary_hash = _boundary(self._gate_paths(gate), self.repo_root)
        except ValueError as exc:
            self.errors.append(str(exc))
            return
        matches = [
            row
            for row in self.authorities.values()
            if row.get("kind") == gate
            and row.get("decision") == self.policy.get("state", {}).get("gate_decision")
            and row.get("scope", {}).get("gate") == gate
            and row.get("artifact_hashes") == hashes
            and row.get("boundary_sha256") == boundary_hash
        ]
        if not matches:
            self.errors.append(f"missing or stale hash-bound {gate} approval")

    def _require_no_blockers(self) -> None:
        pending = [
            row.get("id")
            for row in self.manifest.get("decisions", [])
            if isinstance(row, dict) and row.get("status") == "pending"
        ]
        if pending:
            self.errors.append(f"unresolved product decisions block final approval: {pending}")
        blockers = [
            row.get("id")
            for row in self.findings.get("findings", [])
            if isinstance(row, dict)
            and row.get("status") in self.policy.get("findings", {}).get("blocking_statuses", [])
        ]
        if blockers:
            self.errors.append(f"open refinement findings block final approval: {blockers}")

    def _validate_completed_reviews(self) -> None:
        review_ids = self.state.get("completed_review_ids", [])
        if not isinstance(review_ids, list) or not review_ids:
            self.errors.append("at least one completed independent review is required")
            return
        latest_packet: dict[str, Any] | None = None
        full_seen = False
        for review_id in review_ids:
            review_dir = self.epic_dir / "reviews" / str(review_id)
            receipt_path = review_dir / "reviewer-receipt.yaml"
            receipt_errors, _, packet, _, _, complete = _verify_receipt(
                self.epic_dir,
                receipt_path,
                self.repo_root,
                self.policy,
                check_template_current=False,
            )
            self.errors.extend(receipt_errors)
            if not complete:
                self.errors.append(f"completed review ID {review_id} does not have a complete receipt")
            if packet.get("review_kind") == "full":
                full_seen = True
            latest_packet = packet
        if not full_seen:
            self.errors.append("at least one completed full review is required")
        if latest_packet:
            latest_hashes = latest_packet.get("artifact_hashes")
            current_inputs = (
                {
                    path: digest
                    for path, digest in latest_hashes.items()
                    if PurePosixPath(path).name
                    not in {"refinement-state.yaml", "refinement-findings.yaml"}
                }
                if isinstance(latest_hashes, dict)
                else latest_hashes
            )
            self.errors.extend(
                _hash_map_errors(
                    current_inputs,
                    self.repo_root,
                    "latest review packet artifact_hashes",
                )
            )


def _verify_packet(
    packet_path: Path,
    epic_dir: Path,
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    check_current: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    try:
        packet_path = _inside(packet_path, epic_dir / "reviews", "review packet")
        packet = _load_yaml(packet_path, "review packet")
    except ValueError as exc:
        return [str(exc)], {}
    if packet.get("schema_version") != policy.get("review_packet_version"):
        errors.append("review packet schema_version does not match policy")
    if packet.get("workflow") != "refinement":
        errors.append("review packet workflow must be refinement")
    if packet.get("review_id") != packet_path.parent.name:
        errors.append("review packet review_id does not match directory")
    if packet.get("review_kind") not in policy.get("review", {}).get("kinds", []):
        errors.append("review packet review_kind is not allowed")
    assignments = _mapping_list(packet.get("assignments"), "review packet assignments", errors)
    keys: list[tuple[Any, Any]] = []
    for row in assignments:
        key = (row.get("provider"), row.get("mission"))
        keys.append(key)
        if row.get("provider") not in policy.get("review", {}).get("allowed_providers", []):
            errors.append(f"review packet has unsupported provider: {row.get('provider')}")
        if row.get("mission") not in policy.get("review", {}).get("missions", []):
            errors.append(f"review packet has unsupported mission: {row.get('mission')}")
    if not assignments or len(keys) != len(set(keys)):
        errors.append("review packet assignments must be non-empty and unique")
    targets = _mapping_list(
        packet.get("target_findings"), "review packet target_findings", errors
    )
    if packet.get("review_kind") == "targeted":
        required_union: set[tuple[Any, Any]] = set()
        if not targets:
            errors.append("targeted review packet must contain target_findings")
        for index, target in enumerate(targets):
            required = _mapping_list(
                target.get("required_assignments"),
                f"review packet target_findings[{index}].required_assignments",
                errors,
            )
            required_keys = [
                (row.get("provider"), row.get("mission")) for row in required
            ]
            if (
                not required
                or len(required_keys) != len(set(required_keys))
                or not set(required_keys).issubset(keys)
            ):
                errors.append(
                    f"review packet target_findings[{index}].required_assignments "
                    "must be non-empty, unique, and assigned"
                )
            required_union.update(required_keys)
        if targets and set(keys) != required_union:
            errors.append(
                "targeted review packet assignments must exactly equal target requirements"
            )
    elif targets:
        errors.append("full review packet must not contain target_findings")
    hashes = packet.get("artifact_hashes")
    if isinstance(hashes, dict) and packet.get("boundary_sha256") != _structured_sha256(hashes):
        errors.append("review packet boundary_sha256 does not match artifact_hashes")
    if check_current:
        errors.extend(
            _hash_map_errors(hashes, repo_root, "review packet artifact_hashes")
        )
    return errors, packet


def _verify_receipt(
    epic_dir: Path,
    receipt_path: Path,
    repo_root: Path,
    policy: Mapping[str, Any],
    *,
    check_packet_current: bool = False,
    check_template_current: bool = True,
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
        receipt_path = _inside(receipt_path, epic_dir / "reviews", "reviewer receipt")
        receipt = _load_yaml(receipt_path, "reviewer receipt")
    except ValueError as exc:
        return [str(exc)], {}, {}, [], [], False
    if receipt.get("schema_version") != policy.get("reviewer_receipt_version"):
        errors.append("reviewer receipt schema_version does not match policy")
    if receipt.get("workflow") != "refinement":
        errors.append("reviewer receipt workflow must be refinement")
    if receipt.get("status") not in RECEIPT_TOP_STATUSES:
        errors.append("reviewer receipt status is invalid")
    packet_relative = receipt.get("packet_path")
    try:
        packet_path = _repo_file(repo_root, packet_relative, "review packet")
    except ValueError as exc:
        errors.append(str(exc))
        return errors, receipt, {}, [], [], False
    packet_errors, packet = _verify_packet(
        packet_path,
        epic_dir,
        repo_root,
        policy,
        check_current=check_packet_current,
    )
    errors.extend(packet_errors)
    if packet_path.parent != receipt_path.parent:
        errors.append("reviewer receipt and packet must share one review directory")
    if receipt.get("packet_sha256") != _file_sha256(packet_path):
        errors.append("reviewer receipt packet_sha256 mismatch")
    for field in ("reviewer_profile", "reviewer_set"):
        if receipt.get(field) != packet.get(field):
            errors.append(f"reviewer receipt {field} does not match review packet")
    if check_template_current:
        template_path = receipt.get("template_path")
        try:
            template = _repo_file(repo_root, template_path, "review template")
            if receipt.get("template_sha256") != _file_sha256(template):
                errors.append("reviewer receipt template_sha256 mismatch")
        except ValueError as exc:
            errors.append(str(exc))
    identity = receipt.get("git_identity")
    if (
        not isinstance(identity, dict)
        or identity.get("unchanged") is not True
        or identity.get("before") != identity.get("after")
    ):
        errors.append("reviewer receipt git identity changed during read-only review")
    expected_assignments = packet.get("assignments", [])
    manifest = [
        {"provider": row.get("provider"), "mission": row.get("mission")}
        for row in expected_assignments
        if isinstance(row, dict)
    ]
    if receipt.get("assignment_manifest_sha256") != _structured_sha256(manifest):
        errors.append("reviewer receipt assignment_manifest_sha256 mismatch")
    rows = _mapping_list(receipt.get("assignments"), "reviewer receipt assignments", errors)
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    unresolved_questions = False
    for index, row in enumerate(rows):
        label = f"reviewer receipt assignments[{index}]"
        key = (row.get("provider"), row.get("mission"))
        if key in by_key:
            errors.append(f"duplicate reviewer receipt assignment: {key}")
        by_key[key] = row
        if row.get("status") not in RECEIPT_ROW_STATUSES:
            errors.append(f"{label}.status is invalid")
        paths = row.get("paths")
        if not isinstance(paths, dict):
            errors.append(f"{label}.paths must be a mapping")
            paths = {}
        if "metadata" in paths:
            errors.append(f"{label}.paths must not contain a metadata sidecar")
        raw_candidates = row.get("candidates", [])
        raw_verifications = row.get("targeted_verifications", [])
        if not isinstance(raw_candidates, list) or any(not isinstance(item, dict) for item in raw_candidates):
            errors.append(f"{label}.candidates must be a list of mappings")
            raw_candidates = []
        if not isinstance(raw_verifications, list) or any(
            not isinstance(item, dict) for item in raw_verifications
        ):
            errors.append(f"{label}.targeted_verifications must be a list of mappings")
            raw_verifications = []
        questions = row.get("questions", [])
        if not isinstance(questions, list):
            errors.append(f"{label}.questions must be a list")
            questions = []
        if questions or row.get("decision") == "user_decision_required":
            unresolved_questions = True
            if packet.get("review_kind") == "full" and not any(
                candidate.get("requires_user") is True for candidate in raw_candidates
            ):
                errors.append(
                    f"{label} user question requires a requires_user finding candidate"
                )
        output_relative = paths.get("output")
        if (
            row.get("status") == "completed"
            or raw_candidates
            or raw_verifications
            or row.get("output_sha256") not in (None, "")
        ):
            try:
                output_path = _repo_file(repo_root, output_relative, "review output")
                if output_path.parent != receipt_path.parent:
                    errors.append(f"{label}.review output is outside its review directory")
                if row.get("output_sha256") != _file_sha256(output_path):
                    errors.append(f"{label}.output_sha256 mismatch")
            except ValueError as exc:
                errors.append(str(exc))
        for raw in raw_candidates:
            enriched = dict(raw)
            if (raw.get("provider"), raw.get("mission")) != key:
                errors.append(
                    f"{label}.candidate provider/mission differs from its assignment"
                )
            enriched["provider"], enriched["mission"] = key
            enriched["output_sha256"] = row.get("output_sha256")
            candidates.append(enriched)
        for raw in raw_verifications:
            enriched = dict(raw)
            if (raw.get("provider"), raw.get("mission")) != key:
                errors.append(
                    f"{label}.targeted verification provider/mission differs "
                    "from its assignment"
                )
            enriched["provider"], enriched["mission"] = key
            enriched["output_sha256"] = row.get("output_sha256")
            verifications.append(enriched)
    expected_keys = {(row.get("provider"), row.get("mission")) for row in expected_assignments}
    if set(by_key) != expected_keys:
        errors.append("reviewer receipt assignments do not exactly match packet")
    complete = (
        receipt.get("status") == "completed"
        and set(by_key) == expected_keys
        and all(by_key[key].get("status") == "completed" for key in expected_keys)
        and not unresolved_questions
        and not errors
    )
    return errors, receipt, packet, candidates, verifications, complete


def _next_finding_id(rows: Sequence[Mapping[str, Any]]) -> str:
    numbers = []
    for row in rows:
        match = re.fullmatch(r"RF-([0-9]{3})", str(row.get("id", "")))
        if match:
            numbers.append(int(match.group(1)))
    return f"RF-{max(numbers, default=0) + 1:03d}"


def _candidate_source_id(review_id: str, row: Mapping[str, Any]) -> str:
    source_id = row.get("source_id") or row.get("id")
    if not all(isinstance(value, str) and value for value in (row.get("provider"), row.get("mission"), source_id)):
        raise ValueError("review candidate requires provider, mission, and source_id")
    return f"review:{review_id}:{row['provider']}:{row['mission']}:{source_id}"


def _review_boundary(epic_dir: Path) -> list[Path]:
    paths = [
        _epic_file(epic_dir, name, "review artifact")
        for name in (
            "details.md",
            "acceptance-criteria.md",
            "design.md",
            "delivery-manifest.yaml",
            "refinement-state.yaml",
        )
    ]
    manifest = _load_yaml(epic_dir / "delivery-manifest.yaml", "delivery manifest")
    excluded = {"refinement-findings.yaml", "refinement-review.md"}
    for row in manifest.get("artifact_ownership", []):
        if not isinstance(row, dict) or row.get("authority") not in {"canonical", "evidence"}:
            continue
        relative = row.get("path")
        if not isinstance(relative, str) or relative in excluded or relative.startswith("reviews/"):
            continue
        paths.append(_epic_file(epic_dir, relative, "manifest-owned review artifact"))
    paths.extend(sorted(epic_dir.glob("file-plan-story-*.yaml")))
    return paths


def _assignments(
    manifest: Mapping[str, Any], policy: Mapping[str, Any], reviewer_set: str
) -> list[dict[str, str]]:
    set_policy = policy.get("review", {}).get("sets", {}).get(reviewer_set)
    if not isinstance(set_policy, dict):
        raise ValueError(f"unknown reviewer set: {reviewer_set}")
    rows = [
        {"provider": provider, "mission": "semantic_core"}
        for provider in set_policy.get("semantic_providers", [])
    ]
    if manifest.get("risk_level") in policy.get("review", {}).get("specialist_risks", []):
        rows.append(
            {"provider": str(manifest.get("author_provider")), "mission": "capability_specialist"}
        )
    if len({(row["provider"], row["mission"]) for row in rows}) != len(rows):
        raise ValueError("review topology produced duplicate assignments")
    return rows


def _required_assignments(
    source_ids: Sequence[str], assignments: Sequence[Mapping[str, str]]
) -> list[dict[str, str]]:
    available = {
        (assignment["provider"], assignment["mission"]): assignment
        for assignment in assignments
    }
    resolved: set[tuple[str, str]] = set()
    for source_id in source_ids:
        if source_id.startswith("review:"):
            parts = source_id.split(":")
            if len(parts) != 5 or not all(parts):
                raise ValueError(f"malformed review source candidate ID: {source_id}")
            key = (parts[2], parts[3])
        else:
            parts = source_id.split("/")
            if len(parts) != 4 or not all(parts):
                raise ValueError(f"malformed migrated source candidate ID: {source_id}")
            key = (parts[1], parts[2])
        if key not in available:
            raise ValueError(
                f"source candidate reviewer is absent from targeted topology: {source_id}"
            )
        resolved.add(key)
    if not resolved:
        raise ValueError("targeted finding has no source candidate reviewer")
    return [
        {"provider": assignment["provider"], "mission": assignment["mission"]}
        for assignment in assignments
        if (assignment["provider"], assignment["mission"]) in resolved
    ]


def _initial_findings(epic_id: str, version: int) -> dict[str, Any]:
    return {"schema_version": version, "epic_id": epic_id, "findings": []}


def _initial_state(epic_id: str, version: int) -> dict[str, Any]:
    return {
        "schema_version": version,
        "epic_id": epic_id,
        "status": "drafting",
        "user_decisions": [],
        "completed_review_ids": [],
        "active_findings": {"path": "refinement-findings.yaml"},
    }


def record_authority(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (_, working_root):
        policy = _policy(args.policy.resolve())
        manifest = _load_yaml(epic_dir / "delivery-manifest.yaml", "delivery manifest")
        state_path = epic_dir / "refinement-state.yaml"
        state = (
            _load_yaml(state_path, "refinement state")
            if state_path.is_file()
            else _initial_state(
                str(manifest.get("epic_id", "")),
                int(policy.get("refinement_state_version", 0)),
            )
        )
        if not _same_epic_id(state.get("epic_id"), manifest.get("epic_id")):
            raise ValueError("refinement state and delivery manifest epic_id differ")
        kind = args.gate or args.kind
        if kind in {"product_contract", "final_handoff"}:
            phase = "product" if kind == "product_contract" else "review"
            validator = RefinementValidator(epic_dir, phase, args.policy, working_root)
            errors = validator.validate()
            if kind == "product_contract":
                errors = [error for error in errors if "missing or stale hash-bound product_contract" not in error]
            if errors:
                raise ValueError("authority preconditions failed: " + "; ".join(errors))
            paths = validator._gate_paths(kind)
            scope = {"epic_id": state.get("epic_id"), "gate": kind}
            if args.decision != policy.get("state", {}).get("gate_decision"):
                raise ValueError(f"{kind} authority decision must be approved")
        elif kind == "product_decision":
            if not args.subject:
                raise ValueError("product_decision authority requires --subject decision ID")
            paths = [
                _epic_file(epic_dir, name, "product decision boundary")
                for name in policy["gate_boundaries"]["product_contract"]["paths"]
            ]
            scope = {
                "epic_id": state.get("epic_id"),
                "decision_id": args.subject,
            }
        elif kind == "accepted_risk":
            if not args.subject:
                raise ValueError("accepted_risk authority requires --subject finding fingerprint")
            if args.decision != "approved":
                raise ValueError("accepted_risk authority decision must be approved")
            paths = [
                _epic_file(epic_dir, name, "accepted-risk boundary")
                for name in policy["gate_boundaries"]["accepted_risk"]["paths"]
            ]
            scope = {
                "epic_id": state.get("epic_id"),
                "finding_fingerprint": args.subject,
            }
        else:
            raise ValueError(f"unknown authority kind: {kind}")
        hashes, boundary_sha256 = _boundary(paths, working_root)
        row = {
            "id": args.authority_id,
            "kind": kind,
            "source": args.source,
            "decision": args.decision,
            "decided_at": _now(),
            "scope": scope,
            "artifact_hashes": hashes,
            "boundary_sha256": boundary_sha256,
        }
        decisions = state.get("user_decisions")
        if not isinstance(decisions, list):
            raise ValueError("refinement-state.yaml user_decisions must be a list")
        existing = [item for item in decisions if isinstance(item, dict) and item.get("id") == args.authority_id]
        if existing:
            comparable = dict(row)
            comparable["decided_at"] = existing[0].get("decided_at")
            if existing[0] != comparable:
                raise ValueError(f"authority ID already exists with different content: {args.authority_id}")
            print(json.dumps(existing[0], sort_keys=True))
            return 0
        decisions.append(row)
        if kind == "product_contract":
            state["status"] = "product_approved"
        elif kind == "final_handoff":
            state["status"] = "approved"
        findings_path = epic_dir / "refinement-findings.yaml"
        documents: list[tuple[Path, Mapping[str, Any]]] = [(state_path, state)]
        if not findings_path.exists():
            documents.append(
                (
                    findings_path,
                    _initial_findings(
                        str(state.get("epic_id", "")),
                        int(policy.get("findings_version", 0)),
                    ),
                )
            )
        _atomic_write_yaml_documents(documents)
        print(json.dumps(row, sort_keys=True))
        return 0


def create_review_packet(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (_, working_root):
        policy = _policy(args.policy.resolve())
        validator = RefinementValidator(
            epic_dir,
            "review",
            args.policy,
            working_root,
            review_ready_only=True,
        )
        errors = validator.validate()
        if errors:
            raise ValueError("review packet preconditions failed: " + "; ".join(errors))
        findings_path = epic_dir / "refinement-findings.yaml"
        if findings_path.is_file():
            findings = _load_yaml(findings_path, "refinement findings")
        else:
            findings = _initial_findings(
                str(validator.manifest.get("epic_id")), int(policy.get("findings_version", 0))
            )
        reviews_dir = epic_dir / "reviews"
        existing = sorted(reviews_dir.glob("refine-*/review-packet.yaml")) if reviews_dir.exists() else []
        packets = [_load_yaml(path, "historical review packet") for path in existing]
        limit_key = "maximum_full_reviews" if args.kind == "full" else "maximum_targeted_reviews"
        completed_ids = set(validator.state.get("completed_review_ids", []))
        completed_kind_count = sum(
            packet.get("review_kind") == args.kind
            and packet.get("review_id") in completed_ids
            for packet in packets
        )
        if completed_kind_count >= int(
            policy.get("review", {}).get(limit_key, 0)
        ):
            raise ValueError(f"{args.kind} review budget is exhausted")
        for packet_path, packet in zip(existing, packets):
            if packet.get("review_kind") != args.kind:
                continue
            receipt_path = packet_path.parent / "reviewer-receipt.yaml"
            if not receipt_path.is_file():
                raise ValueError(
                    f"a {args.kind} review packet is still pending: {packet.get('review_id')}"
                )
            receipt_errors, receipt, verified_packet, _, _, _ = _verify_receipt(
                epic_dir, receipt_path, working_root, policy
            )
            if receipt_errors:
                structural_errors, receipt, verified_packet, _, _, complete = _verify_receipt(
                    epic_dir,
                    receipt_path,
                    working_root,
                    policy,
                    check_template_current=False,
                )
                if structural_errors:
                    raise ValueError(
                        f"historical review receipt is invalid: {packet.get('review_id')}: "
                        + "; ".join(dict.fromkeys([*receipt_errors, *structural_errors]))
                    )
                retry_errors: list[str] = []
                review_id = str(packet.get("review_id", ""))
                rows = receipt["assignments"]
                semantic_fields = (
                    "candidates",
                    "targeted_verifications",
                    "questions",
                    "unverified_evidence",
                    "covered_acceptance_ids",
                )
                if receipt.get("status") not in {"failed", "canceled"} or complete:
                    retry_errors.append("receipt is not terminal incomplete")
                if verified_packet != packet:
                    retry_errors.append("receipt is not bound to the selected review packet")
                if any(row.get("status") == "completed" for row in rows):
                    retry_errors.append("receipt contains a completed assignment")
                if any(
                    row.get(field) not in (None, [], "")
                    for row in rows
                    for field in semantic_fields
                ) or any(row.get("decision") not in (None, "") for row in rows):
                    retry_errors.append("receipt contains semantic reviewer output")
                if review_id in completed_ids:
                    retry_errors.append("review was already applied")

                try:
                    template_relative = _relative(
                        receipt.get("template_path"),
                        "historical reviewer receipt template_path",
                    )
                    template_sha = receipt.get("template_sha256")
                    if not isinstance(template_sha, str) or not SHA256_PATTERN.fullmatch(
                        template_sha
                    ):
                        raise ValueError("historical reviewer receipt template_sha256 is invalid")
                    template_path = working_root / template_relative
                    if template_path.is_symlink():
                        raise ValueError("historical review template path must not be a symlink")
                    if template_path.exists() and not template_path.is_file():
                        raise ValueError("historical review template path must be a file or missing")
                    _inside(
                        template_path,
                        working_root,
                        "historical review template",
                        must_exist=False,
                    )
                except ValueError as exc:
                    retry_errors.append(str(exc))
                if retry_errors:
                    raise ValueError(
                        f"historical review receipt is invalid: {review_id}: "
                        + "; ".join(dict.fromkeys(retry_errors))
                    )
        assignments = _assignments(validator.manifest, policy, args.reviewer_set)
        targets = list(dict.fromkeys(args.target_fingerprint))
        target_rows: list[dict[str, Any]] = []
        if args.kind == "targeted":
            if not targets:
                raise ValueError("targeted review requires --target-fingerprint")
            by_fingerprint = {
                row.get("fingerprint"): row
                for row in findings.get("findings", [])
                if isinstance(row, dict)
            }
            missing = sorted(set(targets) - set(by_fingerprint))
            if missing:
                raise ValueError(f"unknown targeted fingerprints: {missing}")
            for fingerprint in targets:
                row = by_fingerprint[fingerprint]
                if row.get("status") != "corrected":
                    raise ValueError(f"targeted finding is not corrected: {fingerprint}")
                source_ids = row.get("source_candidate_ids", [])
                target_rows.append(
                    {
                        "fingerprint": fingerprint,
                        "finding_sha256": _structured_sha256(row),
                        "source_candidate_ids": source_ids,
                        "closure_test": row.get("closure_test"),
                        "required_assignments": _required_assignments(
                            source_ids, assignments
                        ),
                    }
                )
            required_keys = {
                (item["provider"], item["mission"])
                for target in target_rows
                for item in target["required_assignments"]
            }
            assignments = [
                row
                for row in assignments
                if (row["provider"], row["mission"]) in required_keys
            ]
        elif targets:
            raise ValueError("full review cannot name target fingerprints")
        numbers = []
        for path in existing:
            match = re.fullmatch(r"refine-([0-9]{3})", path.parent.name)
            if match:
                numbers.append(int(match.group(1)))
        review_id = f"refine-{max(numbers, default=0) + 1:03d}"
        review_dir = reviews_dir / review_id
        boundary_paths = _review_boundary(epic_dir)
        if args.kind == "targeted":
            boundary_paths.append(
                _epic_file(
                    epic_dir,
                    "refinement-findings.yaml",
                    "targeted review findings",
                )
            )
        hashes, boundary_sha256 = _boundary(boundary_paths, working_root)
        packet = {
            "schema_version": policy.get("review_packet_version"),
            "workflow": "refinement",
            "epic_id": validator.manifest.get("epic_id"),
            "review_id": review_id,
            "review_kind": args.kind,
            "reviewer_profile": args.reviewer_profile,
            "reviewer_set": args.reviewer_set,
            "artifact_hashes": hashes,
            "boundary_sha256": boundary_sha256,
            "assignments": assignments,
            "target_findings": target_rows,
        }
        packet_path = review_dir / "review-packet.yaml"
        if validator.manifest.get("schema_version") == 3:
            packet["replay_snapshot"] = scope_snapshot.retain(working_root, epic_dir, review_id)
        _atomic_write_yaml_documents([(packet_path, packet)])
        print(_repo_relative(packet_path, working_root, "review packet"))
        return 0


def apply_review_receipt(args: argparse.Namespace) -> int:
    epic_dir = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic_dir) as (_, working_root):
        policy = _policy(args.policy.resolve())
        errors, receipt, packet, candidates, verifications, complete = _verify_receipt(
            epic_dir,
            args.receipt.resolve(),
            working_root,
            policy,
            check_packet_current=True,
        )
        if errors:
            raise ValueError("invalid reviewer receipt: " + "; ".join(errors))
        findings_path = epic_dir / "refinement-findings.yaml"
        state_path = epic_dir / "refinement-state.yaml"
        state = _load_yaml(state_path, "refinement state")
        findings = (
            _load_yaml(findings_path, "refinement findings")
            if findings_path.is_file()
            else _initial_findings(str(state.get("epic_id")), int(policy.get("findings_version", 0)))
        )
        rows = findings.get("findings")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("refinement findings must contain a findings list")
        by_fingerprint = {row.get("fingerprint"): row for row in rows}
        packet_targets = {
            row.get("fingerprint"): row
            for row in packet.get("target_findings", [])
            if isinstance(row, dict)
        }
        severity_order = policy.get("findings", {}).get("severities", [])
        review_id = str(packet.get("review_id"))
        for candidate in candidates:
            source_id = _candidate_source_id(review_id, candidate)
            fingerprint = candidate.get("fingerprint")
            severity = candidate.get("severity")
            category = candidate.get("category")
            if not isinstance(fingerprint, str) or not fingerprint:
                raise ValueError(f"review candidate {source_id} has no fingerprint")
            if severity not in severity_order or category not in policy.get("findings", {}).get("categories", []):
                raise ValueError(f"review candidate {source_id} has invalid severity/category")
            evidence_value = candidate.get("evidence")
            evidence = evidence_value if isinstance(evidence_value, list) else [evidence_value]
            if any(not isinstance(item, str) or not item for item in evidence):
                raise ValueError(f"review candidate {source_id} has invalid evidence")
            affected = candidate.get("affected_manifest_ids", candidate.get("affected_acceptance_ids", []))
            if not isinstance(affected, list) or any(not isinstance(item, str) for item in affected):
                raise ValueError(f"review candidate {source_id} has invalid affected acceptance IDs")
            requires_user = candidate.get("requires_user")
            if not isinstance(requires_user, bool):
                raise ValueError(f"review candidate {source_id} requires boolean requires_user")
            title = candidate.get("required_correction") or candidate.get("title")
            closure = candidate.get("closure_test")
            impact = candidate.get("impact")
            if not all(isinstance(value, str) and value for value in (title, closure, impact)):
                raise ValueError(f"review candidate {source_id} is incomplete")
            existing = by_fingerprint.get(fingerprint)
            if existing:
                stable_values = {
                    "category": category,
                    "title": title,
                    "impact": impact,
                    "owner": "user" if requires_user else "architect",
                    "closure_test": closure,
                }
                conflicts = [
                    field
                    for field, value in stable_values.items()
                    if existing.get(field) not in (None, "")
                    and existing.get(field) != value
                ]
                if conflicts:
                    raise ValueError(
                        f"review candidates conflict for fingerprint {fingerprint}: "
                        + ", ".join(conflicts)
                    )
                for field, value in stable_values.items():
                    if existing.get(field) in (None, ""):
                        existing[field] = value
                existing["severity"] = severity_order[
                    max(severity_order.index(existing.get("severity")), severity_order.index(severity))
                ]
                existing["evidence"] = list(dict.fromkeys([*existing.get("evidence", []), *evidence]))
                existing["affected_acceptance_ids"] = list(
                    dict.fromkeys([*existing.get("affected_acceptance_ids", []), *affected])
                )
                existing["source_candidate_ids"] = list(
                    dict.fromkeys([*existing.get("source_candidate_ids", []), source_id])
                )
                if existing.get("status") in {"verified", "accepted_risk"}:
                    existing["status"] = "open"
                    existing["resolution"] = None
            else:
                existing = {
                    "id": _next_finding_id(rows),
                    "fingerprint": fingerprint,
                    "severity": severity,
                    "category": category,
                    "status": "open",
                    "title": title,
                    "evidence": evidence,
                    "affected_acceptance_ids": affected,
                    "impact": impact,
                    "owner": "user" if requires_user else "architect",
                    "closure_test": closure,
                    "source_candidate_ids": [source_id],
                    "resolution": None,
                }
                rows.append(existing)
                by_fingerprint[fingerprint] = existing
        for fingerprint, target in packet_targets.items():
            finding = by_fingerprint.get(fingerprint)
            if not finding:
                raise ValueError(f"targeted verification references missing finding: {fingerprint}")
            if target.get("finding_sha256") != _structured_sha256(finding):
                raise ValueError(
                    f"targeted finding changed after packet creation: {fingerprint} "
                    "(finding_sha256 mismatch)"
                )
        verification_groups: dict[str, list[dict[str, Any]]] = {}
        for verification in verifications:
            fingerprint = verification.get("fingerprint")
            if fingerprint not in packet_targets:
                raise ValueError(f"targeted verification is outside packet: {fingerprint}")
            target = packet_targets[fingerprint]
            if verification.get("source_candidate_ids") != target.get(
                "source_candidate_ids"
            ):
                raise ValueError(
                    f"targeted verification source_candidate_ids differ from packet: {fingerprint}"
                )
            if verification.get("closure_test") != target.get("closure_test"):
                raise ValueError(
                    f"targeted verification closure_test differs from packet: {fingerprint}"
                )
            outcome = verification.get("outcome")
            if outcome not in {"verified", "still_open"}:
                raise ValueError(f"targeted verification has invalid outcome: {fingerprint}")
            verification_groups.setdefault(str(fingerprint), []).append(verification)
        if packet.get("review_kind") == "targeted" and set(verification_groups) != set(packet_targets):
            raise ValueError("targeted receipt must verify every packet fingerprint")
        for fingerprint in packet_targets:
            finding = by_fingerprint.get(fingerprint)
            if not finding:
                raise ValueError(f"targeted verification references missing finding: {fingerprint}")
            checks = verification_groups.get(str(fingerprint), [])
            keys = {(row.get("provider"), row.get("mission")) for row in checks}
            target_assignments = packet_targets[fingerprint].get(
                "required_assignments", []
            )
            expected_assignments = {
                (row.get("provider"), row.get("mission"))
                for row in target_assignments
                if isinstance(row, dict)
            }
            if keys != expected_assignments:
                raise ValueError(f"targeted verification assignments are incomplete: {fingerprint}")
            if all(row.get("outcome") == "verified" for row in checks):
                if finding.get("status") != "corrected" or not isinstance(
                    finding.get("resolution"), dict
                ):
                    raise ValueError(
                        f"finding must have inline correction evidence before verification: {fingerprint}"
                    )
                finding["status"] = "verified"
                finding["resolution"]["verification"] = {
                    "review_id": review_id,
                    "sources": [
                        {
                            "provider": row.get("provider"),
                            "mission": row.get("mission"),
                            "evidence": row.get("evidence"),
                            "source_candidate_ids": row.get("source_candidate_ids", []),
                            "output_sha256": row.get("output_sha256"),
                        }
                        for row in checks
                    ],
                    "receipt_sha256": _file_sha256(args.receipt.resolve()),
                }
            else:
                finding["status"] = "corrected"
                if isinstance(finding.get("resolution"), dict):
                    finding["resolution"].pop("verification", None)
        completed = state.get("completed_review_ids")
        if not isinstance(completed, list):
            raise ValueError("refinement-state.yaml completed_review_ids must be a list")
        if complete and review_id not in completed:
            completed.append(review_id)
        # Count completed semantic reviews only. Infrastructure repairs are the same review.
        if complete and len(completed) >= int(policy["review"]["minor_optional_from_review"]):
            for row in rows:
                if row.get("severity") == "minor" and row.get("status") in {"open", "corrected"}:
                    row["status"] = "deferred"
                    row["deferred_review_id"] = review_id
                    row["deferred_receipt_sha256"] = _file_sha256(args.receipt.resolve())
        state["status"] = (
            "ready_for_final_approval"
            if complete
            and not any(
                row.get("status") in policy.get("findings", {}).get("blocking_statuses", [])
                for row in rows
            )
            else "in_review"
        )
        _atomic_write_yaml_documents([(findings_path, findings), (state_path, state)])
        print(f"Refinement review applied: review={review_id} complete={str(complete).lower()}")
        return 0


def baseline_proofs(args: argparse.Namespace) -> int:
    epic = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic) as (run, root):
        path = epic / "delivery-manifest.yaml"
        manifest = _load_yaml(path, "delivery manifest")
        if manifest.get("schema_version") != 3:
            raise ValueError("baseline executor requires a new manifest v3")
        proofs = [row for row in manifest["proofs"] if row["classification"] == "existing_runnable"]
        receipt = scope_proofs.execute(proofs, root, epic, scope_proofs.policy(Path(run["scope_root"])))
        by_id = {row["proof_id"]: row for row in receipt["proofs"]}
        for proof in proofs:
            proof["baseline_evidence"] = by_id[proof["id"]]
        _atomic_write_yaml_documents([(path, manifest)])
        print(f"Baseline proofs: {receipt['status']} ({receipt['path']})")
        return 0 if receipt["status"] == "pass" else 1


def render_summary(args: argparse.Namespace) -> int:
    epic = args.epic_dir.resolve()
    with _mutation_guard(args.run, epic) as (_, root):
        validator = RefinementValidator(epic, "review", args.policy, root)
        errors = validator.validate()
        if errors:
            raise ValueError("refinement is not ready for summary: " + "; ".join(errors))
        state, findings = validator.state, validator.findings
        lines = [f"# Refinement review — {state['epic_id']}", "",
                 f"Completed semantic reviews: {len(state['completed_review_ids'])}.", "",
                 "| Finding | Severity | Status | Required correction |", "| --- | --- | --- | --- |"]
        for row in findings.get("findings", []):
            title = str(row["title"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row['id']} | {row['severity']} | {row['status']} | {title} |")
        lines += ["", "Deferred minor findings remain unverified and optional; they are visible at final-handoff approval.",
                  "The manifest, review receipts, and finding ledger remain the source evidence.", ""]
        (epic / "refinement-review.md").write_text("\n".join(lines), encoding="utf-8")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    validator = RefinementValidator(args.epic_dir, args.phase, args.policy, args.repo_root)
    errors = validator.validate()
    if errors:
        print(f"Refinement validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Refinement validation passed: phase={args.phase} epic={args.epic_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("baseline-proofs", baseline_proofs), ("render-summary", render_summary)):
        selected = subparsers.add_parser(name)
        selected.add_argument("epic_dir", type=Path)
        selected.add_argument("--run", type=Path, required=True)
        selected.add_argument("--policy", type=Path, default=_default_policy_path())
        selected.set_defaults(handler=handler)

    validate_parser = subparsers.add_parser("validate", help="Validate one refinement boundary")
    validate_parser.add_argument("epic_dir", type=Path)
    validate_parser.add_argument("--phase", choices=PHASES, required=True)
    validate_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    validate_parser.add_argument("--repo-root", type=Path)
    validate_parser.set_defaults(handler=validate_command)

    authority_parser = subparsers.add_parser("record-authority", help="Record one hash-bound user decision")
    authority_parser.add_argument("epic_dir", type=Path)
    authority_parser.add_argument("--run", type=Path, required=True)
    authority_parser.add_argument("--authority-id", required=True)
    selector = authority_parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--gate", choices=("product_contract", "final_handoff"))
    selector.add_argument("--kind", choices=("product_decision", "accepted_risk"))
    authority_parser.add_argument("--source", choices=("user", "preapproval"), required=True)
    authority_parser.add_argument("--decision", required=True)
    authority_parser.add_argument("--subject")
    authority_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    authority_parser.set_defaults(handler=record_authority)

    packet_parser = subparsers.add_parser("create-review-packet", help="Create one immutable review packet")
    packet_parser.add_argument("epic_dir", type=Path)
    packet_parser.add_argument("--run", type=Path, required=True)
    packet_parser.add_argument("--kind", choices=("full", "targeted"), required=True)
    packet_parser.add_argument("--reviewer-profile", choices=("default", "budget"), default="default")
    packet_parser.add_argument("--reviewer-set", choices=("standard", "expanded"), default="standard")
    packet_parser.add_argument("--target-fingerprint", action="append", default=[])
    packet_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    packet_parser.set_defaults(handler=create_review_packet)

    apply_parser = subparsers.add_parser("apply-review-receipt", help="Apply every valid reviewer source")
    apply_parser.add_argument("epic_dir", type=Path)
    apply_parser.add_argument("receipt", type=Path)
    apply_parser.add_argument("--run", type=Path, required=True)
    apply_parser.add_argument("--policy", type=Path, default=_default_policy_path())
    apply_parser.set_defaults(handler=apply_review_receipt)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError) as exc:
        print(f"Refinement artifact operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

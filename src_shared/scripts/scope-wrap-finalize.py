#!/usr/bin/env python3
"""Seal, prepare, commit, and merge one completed Scope epic exactly."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping, Sequence

sys.dont_write_bytecode = True

import yaml

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import scope_codegraph  # noqa: E402
import scope_fingerprint  # noqa: E402
import scope_git  # noqa: E402


ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
FINGERPRINT_DRIFT = "repository fingerprint changed after audit preparation"


class WrapError(ValueError):
    """Raised when an epic cannot be finalized exactly and safely."""


def _same_epic_id(left: Any, right: Any) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.strip().casefold() == right.strip().casefold()
    )


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    value: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in value:
            raise WrapError(f"duplicate YAML key: {key!r}")
        value[key] = loader.construct_object(value_node, deep=deep)
    return value


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise WrapError(f"missing or symlinked {label}: {path}")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WrapError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WrapError(f"{label} must be a YAML mapping: {path}")
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise WrapError(f"missing or symlinked {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WrapError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WrapError(f"{label} must be a JSON object: {path}")
    return value


def _dump_yaml(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True).encode("utf-8")


def _exact_keys(
    value: Any, required: set[str], label: str, optional: set[str] | None = None
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) - (optional or set()) != required:
        raise WrapError(f"{label} fields are incomplete or unsupported")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise WrapError(f"{label} must be a SHA-256 digest")
    return value


def _validate_seal_shape(seal: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    _exact_keys(
        seal,
        {
            "schema_version",
            "epic_id",
            "active_epic_path",
            "implemented_epic_path",
            "audit",
            "summary",
            "summary_job",
            "workspace",
        },
        "delivery seal",
    )
    if seal.get("schema_version") != policy["seal_schema_version"]:
        raise WrapError("delivery seal schema version is invalid")
    epic_id = seal.get("epic_id")
    if not isinstance(epic_id, str) or ID_PATTERN.fullmatch(epic_id) is None:
        raise WrapError("delivery seal epic_id is invalid")
    active = seal.get("active_epic_path")
    archived = seal.get("implemented_epic_path")
    if not isinstance(active, str) or not isinstance(archived, str):
        raise WrapError("delivery seal epic paths are invalid")
    scope_fingerprint.relative_path(active, "delivery seal active epic path")
    scope_fingerprint.relative_path(archived, "delivery seal archived epic path")
    active_path = PurePosixPath(active)
    archived_path = PurePosixPath(archived)
    implemented_root = PurePosixPath(str(policy["paths"]["implemented_root"]))
    if (
        active_path.parent != PurePosixPath("docs/epics")
        or archived_path.parent != implemented_root
        or archived_path.name != active_path.name
    ):
        raise WrapError("delivery seal epic paths are not canonical")

    audit = _exact_keys(
        seal.get("audit"),
        {
            "attempt_path",
            "attempt_sha256",
            "attempt_id",
            "repository_workspace_sha256",
            "boundary_sha256",
        },
        "delivery seal audit",
    )
    attempt = audit.get("attempt_path")
    if not isinstance(attempt, str):
        raise WrapError("delivery seal audit attempt path is invalid")
    scope_fingerprint.relative_path(attempt, "delivery seal audit attempt path")
    if not isinstance(audit.get("attempt_id"), str):
        raise WrapError("delivery seal audit attempt ID is invalid")
    for field in ("attempt_sha256", "repository_workspace_sha256", "boundary_sha256"):
        _sha256(audit.get(field), f"delivery seal audit {field}")

    summary = _exact_keys(
        seal.get("summary"), {"path", "sha256"}, "delivery seal summary"
    )
    if summary.get("path") != policy["paths"]["summary"]:
        raise WrapError("delivery seal summary path is invalid")
    _sha256(summary.get("sha256"), "delivery seal summary hash")

    provenance = seal.get("summary_job")
    if isinstance(provenance, dict) and provenance.get("method") == "deterministic-v1":
        _exact_keys(provenance, {"method", "summary_sha256", "inputs_sha256"}, "summary provenance")
        for field in ("summary_sha256", "inputs_sha256"):
            _sha256(provenance.get(field), f"summary provenance {field}")
        if provenance["summary_sha256"] != summary["sha256"]:
            raise WrapError("summary provenance does not bind summary")
    else:
        job = _exact_keys(
            seal.get("summary_job"),
            {"job_id", "result_sha256", "after_snapshot_sha256"},
            "delivery seal summary job",
        )
        if not isinstance(job.get("job_id"), str) or not job["job_id"]:
            raise WrapError("delivery seal summary job ID is invalid")
        _sha256(job.get("result_sha256"), "delivery seal summary result hash")
        _sha256(job.get("after_snapshot_sha256"), "delivery seal summary snapshot hash")

    workspace = _exact_keys(
        seal.get("workspace"),
        {"head", "tree", "changes", "workspace_sha256"},
        "delivery seal workspace",
    )
    for field in ("head", "tree"):
        if not isinstance(workspace.get(field), str) or scope_git.COMMIT_PATTERN.fullmatch(
            str(workspace[field])
        ) is None:
            raise WrapError(f"delivery seal workspace {field} is invalid")
    changes = workspace.get("changes")
    if not isinstance(changes, list):
        raise WrapError("delivery seal workspace changes must be a list")
    for row in changes:
        change = _exact_keys(
            row,
            {"path", "status", "content_sha256", "mode"},
            "delivery seal workspace change",
            {"old_path"},
        )
        if not isinstance(change.get("path"), str):
            raise WrapError("sealed workspace path is invalid")
        scope_fingerprint.relative_path(change["path"], "sealed workspace path")
        if "old_path" in change:
            if not isinstance(change.get("old_path"), str):
                raise WrapError("sealed workspace old path is invalid")
            scope_fingerprint.relative_path(
                change["old_path"], "sealed workspace old path"
            )
        if not isinstance(change.get("status"), str) or len(change["status"]) != 2:
            raise WrapError("sealed workspace status is invalid")
        content = change.get("content_sha256")
        mode = change.get("mode")
        if content is None and mode is None:
            continue
        _sha256(content, "sealed workspace content hash")
        if mode not in {"100644", "100755", "120000"}:
            raise WrapError("sealed workspace mode is invalid")
    _sha256(workspace.get("workspace_sha256"), "delivery seal workspace hash")
    payload = {key: workspace[key] for key in ("head", "tree", "changes")}
    if scope_fingerprint.structured_sha256(payload) != workspace["workspace_sha256"]:
        raise WrapError("delivery seal workspace hash is stale")


def _atomic_write(
    path: Path, payload: bytes, *, temporary_directory: Path | None = None
) -> None:
    if path.is_symlink():
        raise WrapError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = temporary_directory or path.parent
    if temporary_parent.is_symlink():
        raise WrapError(f"Scope temporary directory must not be a symlink: {temporary_parent}")
    temporary_parent.mkdir(parents=True, exist_ok=True)
    if temporary_parent.resolve(strict=True) != temporary_parent.absolute():
        raise WrapError(f"Scope temporary directory escapes its selected path: {temporary_parent}")
    temporary = temporary_parent / f".{path.name}.scope-tmp"
    if temporary.is_symlink() or (temporary.exists() and not temporary.is_file()):
        raise WrapError(f"invalid Scope temporary file: {temporary}")
    temporary.unlink(missing_ok=True)
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o644)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _default_policy() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "wrap-policy.yaml"


def _default_audit_policy() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "audit-policy.yaml"


def _policy(path: Path) -> dict[str, Any]:
    value = _load_yaml(path.resolve(), "wrap policy")
    paths = value.get("paths")
    labels = value.get("labels")
    if value.get("schema_version") != 1 or value.get("seal_schema_version") != 1:
        raise WrapError("wrap policy schema versions must be 1")
    if value.get("prepare_schema_version") != 1:
        raise WrapError("wrap prepare schema version must be 1")
    if not isinstance(paths, dict) or not isinstance(labels, dict):
        raise WrapError("wrap policy paths and labels must be mappings")
    required_paths = {
        "seal",
        "summary",
        "reviews",
        "audit_attempt",
        "prepare_state",
        "implemented_root",
        "codegraph_policy",
    }
    if set(paths) != required_paths:
        raise WrapError("wrap policy paths are incomplete or unsupported")
    for name, raw in paths.items():
        if not isinstance(raw, str):
            raise WrapError(f"wrap policy path {name} must be a string")
        scope_fingerprint.relative_path(raw, f"wrap policy path {name}")
    if set(labels) != {"closure", "merge"} or any(
        not isinstance(value, str) or value.count("{epic_id}") != 1
        for value in labels.values()
    ):
        raise WrapError("wrap labels must each contain one {epic_id} token")
    pattern = value.get("audit_attempt_pattern")
    if not isinstance(pattern, str):
        raise WrapError("wrap audit attempt pattern must be a string")
    re.compile(pattern)
    branch = value.get("worktree_branch")
    if not isinstance(branch, str) or branch.count("{epic_id}") != 1:
        raise WrapError("wrap worktree branch must contain one {epic_id} token")
    return value


def _relative(root: Path, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise WrapError(f"{label} is outside the selected Git root") from exc


def _safe_child(root: Path, relative: str, label: str, *, required: bool = True) -> Path:
    normalized = scope_fingerprint.relative_path(relative, label)
    selected = root.resolve(strict=True)
    candidate = selected.joinpath(*PurePosixPath(normalized).parts)
    current = selected
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.is_symlink():
            raise WrapError(f"symlinked {label}: {current}")
        if not current.exists():
            break
    if required and not candidate.exists():
        raise WrapError(f"missing {label}: {candidate}")
    if candidate.exists():
        try:
            candidate.resolve(strict=True).relative_to(selected)
        except ValueError as exc:
            raise WrapError(f"{label} escapes the selected Git root") from exc
    return candidate


def _canonical_run(
    run_path: Path, policy: Mapping[str, Any]
) -> tuple[dict[str, Any], Path, Path]:
    if run_path.is_symlink():
        raise WrapError("implement run must not be a symlink")
    resolved = run_path.resolve(strict=True)
    run = _load_yaml(resolved, "implement run")
    if run.get("schema_version") != 2 or run.get("command") != "implement":
        raise WrapError("delivery finalization requires a lean implement run")
    epic_id = run.get("epic_id")
    if not isinstance(epic_id, str) or ID_PATTERN.fullmatch(epic_id) is None:
        raise WrapError("implement run epic_id is invalid")
    repository_value = run.get("repository_root")
    working_value = run.get("working_root")
    if not isinstance(repository_value, str) or not isinstance(working_value, str):
        raise WrapError("implement run roots are invalid")
    repository_root = Path(repository_value).resolve(strict=True)
    working_root = Path(working_value).resolve(strict=True)
    scope_git.require_linked_worktree(repository_root, working_root)
    expected = (
        repository_root
        / "tmp_debug"
        / "scope-runs"
        / epic_id
        / "implement"
        / "run.yaml"
    )
    if resolved != expected:
        raise WrapError(f"implement run path must be {expected}")
    if run.get("active_job") is not None or not isinstance(run.get("completed_jobs"), list):
        raise WrapError("implement run is active or malformed")
    expected_branch = str(policy["worktree_branch"]).format(epic_id=epic_id)
    if scope_git.git(working_root, "symbolic-ref", "--short", "HEAD") != expected_branch:
        raise WrapError(f"implement worktree must be on branch {expected_branch}")
    return run, repository_root, working_root


def _active_epic(epic_dir: Path, run: Mapping[str, Any], working_root: Path) -> Path:
    if epic_dir.is_symlink():
        raise WrapError("epic directory must not be a symlink")
    selected = epic_dir.resolve(strict=True)
    if selected.parent != working_root / "docs" / "epics":
        raise WrapError("active epic must be a direct child of docs/epics")
    manifest = _load_yaml(selected / "delivery-manifest.yaml", "delivery manifest")
    if not _same_epic_id(manifest.get("epic_id"), run.get("epic_id")):
        raise WrapError("delivery manifest epic_id differs from the implement run")
    return selected


def _reject_active_runs(repository_root: Path, roots: Sequence[Path]) -> None:
    selected = {str(root.resolve(strict=True)) for root in roots}
    runtime = repository_root / "tmp_debug" / "scope-runs"
    if not runtime.exists():
        return
    for path in sorted(runtime.glob("*/*/run.yaml")):
        if path.is_symlink():
            raise WrapError(f"symlinked Scope run blocks finalization: {path}")
        try:
            run = _load_yaml(path, "Scope run")
        except WrapError:
            raise
        if str(run.get("working_root")) in selected and isinstance(run.get("active_job"), dict):
            raise WrapError(f"active Scope job blocks finalization: {path}")


def _latest_attempt(epic_dir: Path, policy: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    paths = policy["paths"]
    reviews = _safe_child(epic_dir, str(paths["reviews"]), "audit reviews")
    pattern = re.compile(str(policy["audit_attempt_pattern"]))
    attempts = [
        child / str(paths["audit_attempt"])
        for child in reviews.iterdir()
        if child.is_dir() and not child.is_symlink() and pattern.fullmatch(child.name)
    ]
    if not attempts:
        raise WrapError("no audit attempt exists")
    latest = sorted(attempts, key=lambda path: path.parent.name)[-1]
    attempt = _load_yaml(latest, "latest audit attempt")
    if attempt.get("status") != "pass" or attempt.get("decision", {}).get("outcome") != "pass":
        raise WrapError("latest audit attempt is not PASS")
    return latest, attempt


def _audit_module() -> Any:
    path = Path(__file__).resolve().with_name("audit-artifacts.py")
    spec = importlib.util.spec_from_file_location("scope_wrap_audit_artifacts", path)
    if spec is None or spec.loader is None:
        raise WrapError("cannot load the installed audit validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_audit(
    epic_dir: Path,
    working_root: Path,
    attempt_path: Path,
    attempt: Mapping[str, Any],
    summary_relative: str,
    seal_relative: str,
    audit_policy: Path,
) -> None:
    current = scope_fingerprint.audit_fingerprint(
        epic_dir,
        working_root,
        extra_excluded=(summary_relative, seal_relative),
    )
    if current != attempt.get("repository_fingerprint", {}):
        raise WrapError("workspace differs from the PASS audit beyond the delivery summary")
    previous_optional_locks = os.environ.get("GIT_OPTIONAL_LOCKS")
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        module = _audit_module()
        validator = module.AuditValidator(
            epic_dir, attempt_path.parent, "complete", audit_policy, working_root
        )
        errors = validator.validate()
    finally:
        if previous_optional_locks is None:
            os.environ.pop("GIT_OPTIONAL_LOCKS", None)
        else:
            os.environ["GIT_OPTIONAL_LOCKS"] = previous_optional_locks
    unexpected = [error for error in errors if error != FINGERPRINT_DRIFT]
    if unexpected:
        raise WrapError("latest PASS audit is invalid: " + "; ".join(unexpected))


def _summary_inputs(epic: Path, summary: Path) -> dict[str, str]:
    inputs = {name: scope_fingerprint.file_sha256(epic / name)
              for name in ("delivery-manifest.yaml", "implementation-evidence.yaml", "audit-findings.yaml", "epic_audit.md")}
    return {"method": "deterministic-v1", "summary_sha256": scope_fingerprint.file_sha256(summary),
            "inputs_sha256": scope_fingerprint.structured_sha256(inputs)}


def render_summary(args: argparse.Namespace) -> dict[str, Any]:
    policy = _policy(args.policy)
    run, repository_root, root = _canonical_run(args.run, policy)
    epic = _active_epic(args.epic_dir, run, root)
    with scope_git.mutation_locks([root]):
        _reject_active_runs(repository_root, [root])
        attempt_path, attempt = _latest_attempt(epic, policy)
        summary = _safe_child(epic, str(policy["paths"]["summary"]), "implementation summary", required=False)
        _validate_audit(epic, root, attempt_path, attempt, summary.relative_to(root).as_posix(),
                        (epic / policy["paths"]["seal"]).relative_to(root).as_posix(), args.audit_policy.resolve())
        manifest = _load_yaml(epic / "delivery-manifest.yaml", "delivery manifest")
        if manifest.get("schema_version") != 3:
            raise WrapError("deterministic summary requires manifest v3; keep older epics on their pinned runtime")
        evidence = _load_yaml(epic / "implementation-evidence.yaml", "implementation evidence")
        findings = _load_yaml(epic / "audit-findings.yaml", "audit findings")
        lines = [f"# Implementation summary — {run['epic_id']}", "", f"Audit: PASS ({attempt['attempt_id']}).", "",
                 "| Story | Status | Executed proofs | Declared unavailable proofs |", "| --- | --- | --- | --- |"]
        for row in evidence["stories"]:
            lines.append(
                f"| {row['story_id']} | {row['status']} | "
                f"{', '.join(p['proof_id'] for p in row['proofs'])} | "
                f"{', '.join(row.get('external_blocked_proof_ids', []))} |"
            )
        lines += ["", "## Findings", ""]
        lines.extend(f"- {row['id']}: {row['status']} — {row['title']}" for row in findings.get("findings", []))
        lines += ["", "## Changed files", ""]
        lines.extend(f"- {row['path']}" for row in evidence["attributed_delta"])
        lines += ["", "Proof counts, raw logs, and context fingerprints are in implementation-evidence.yaml.",
                  "The approved manifest and audit receipts define the delivered scope.", ""]
        _atomic_write(summary, "\n".join(lines).encode("utf-8"), temporary_directory=scope_git.runtime_directory(root, "scope-wrap"))
        run["delivery_summary"] = _summary_inputs(epic, summary)
        _atomic_write(args.run, _dump_yaml(run))
        return {"status": "rendered", "summary": str(summary), **run["delivery_summary"]}


def _summary_job(
    run: Mapping[str, Any], repository_root: Path, summary_relative: str, summary: Path
) -> dict[str, str]:
    manifest = _load_yaml(summary.parent / "delivery-manifest.yaml", "delivery manifest")
    if manifest.get("schema_version") == 3:
        provenance = run.get("delivery_summary")
        if not isinstance(provenance, dict) or provenance != _summary_inputs(summary.parent, summary):
            raise WrapError("deterministic delivery summary is missing or stale; run render-summary")
        return provenance
    jobs = run["completed_jobs"]
    if not jobs or not isinstance(jobs[-1], dict):
        raise WrapError("implement run has no completed delivery-summary job")
    row = jobs[-1]
    if (
        row.get("phase") != "delivery_summary"
        or row.get("status") != "completed"
        or row.get("changed_paths") != [summary_relative]
    ):
        raise WrapError("last implement job is not the exact delivery-summary write")
    job_id = row.get("job_id")
    result_sha256 = row.get("result_sha256")
    snapshot_sha256 = row.get("after_snapshot_sha256")
    if not isinstance(job_id, str) or not isinstance(result_sha256, str):
        raise WrapError("delivery-summary receipt is incomplete")
    if not isinstance(snapshot_sha256, str):
        raise WrapError(
            "delivery summary is not publication-bound; resume implement to republish it"
        )
    result = (
        repository_root
        / "tmp_debug"
        / "scope-runs"
        / str(run["epic_id"])
        / "implement"
        / "jobs"
        / job_id
        / "result.json"
    )
    if Path(str(row.get("result_path", ""))).resolve(strict=True) != result:
        raise WrapError("delivery-summary result path is not canonical")
    if scope_fingerprint.file_sha256(result) != result_sha256:
        raise WrapError("delivery-summary result hash is stale")
    result_doc = _load_json(result, "delivery-summary result")
    if (
        result_doc.get("job_id") != job_id
        or result_doc.get("status") != "completed"
        or result_doc.get("changed_paths") != [summary_relative]
        or result_doc.get("payload", {}).get("kind") != "implementation"
    ):
        raise WrapError("delivery-summary result contract is invalid")
    snapshot_path = result.parent / "after-snapshot.json"
    if scope_fingerprint.file_sha256(snapshot_path) != snapshot_sha256:
        raise WrapError("delivery-summary after-snapshot hash is stale")
    snapshot = _load_json(snapshot_path, "delivery-summary after snapshot")
    matches = [
        value
        for value in snapshot.get("entries", [])
        if isinstance(value, dict) and value.get("path") == summary_relative
    ]
    if len(matches) != 1 or matches[0].get("sha256") != scope_fingerprint.file_sha256(summary):
        raise WrapError("delivery-summary snapshot does not bind the current summary")
    return {
        "job_id": job_id,
        "result_sha256": result_sha256,
        "after_snapshot_sha256": snapshot_sha256,
    }


def _workspace(working_root: Path, seal_relative: str) -> dict[str, Any]:
    def excluded(relative: str) -> bool:
        return relative == seal_relative

    fingerprint = scope_fingerprint.workspace_fingerprint(
        working_root, exclude=excluded, include_mode=True
    )
    temporary = [
        row["path"]
        for row in fingerprint["changes"]
        if row["path"] == "tmp_debug" or row["path"].startswith("tmp_debug/")
    ]
    if temporary:
        raise WrapError(f"tracked temporary paths cannot be sealed: {temporary}")
    return fingerprint


def _reject_ignored_attributed_paths(
    run: Mapping[str, Any], working_root: Path
) -> None:
    attributed = {
        path
        for row in run.get("completed_jobs", [])
        if isinstance(row, dict)
        for path in row.get("changed_paths", [])
        if isinstance(path, str)
    }
    ignored: list[str] = []
    for relative in sorted(attributed):
        scope_fingerprint.relative_path(relative, "worker-attributed path")
        tracked = scope_git.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            working_root,
        )
        if tracked.returncode == 0:
            continue
        if tracked.returncode != 1:
            raise WrapError(f"cannot determine tracked status for {relative}")
        result = scope_git.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            working_root,
        )
        if result.returncode not in {0, 1}:
            raise WrapError(f"cannot determine ignored status for {relative}")
        if result.returncode == 0 and (
            (working_root / relative).exists() or (working_root / relative).is_symlink()
        ):
            ignored.append(relative)
    if ignored:
        raise WrapError(
            f"ignored worker-attributed paths cannot be sealed or staged: {ignored}"
        )


def _seal_payload(
    epic_dir: Path,
    run: Mapping[str, Any],
    repository_root: Path,
    working_root: Path,
    policy: Mapping[str, Any],
    audit_policy: Path,
) -> dict[str, Any]:
    paths = policy["paths"]
    active_relative = _relative(working_root, epic_dir, "active epic")
    implemented_relative = (
        PurePosixPath(str(paths["implemented_root"])) / epic_dir.name
    ).as_posix()
    summary = _safe_child(epic_dir, str(paths["summary"]), "implementation summary")
    summary_relative = _relative(working_root, summary, "implementation summary")
    seal_relative = (
        PurePosixPath(active_relative) / str(paths["seal"])
    ).as_posix()
    attempt_path, attempt = _latest_attempt(epic_dir, policy)
    _validate_audit(
        epic_dir,
        working_root,
        attempt_path,
        attempt,
        summary_relative,
        seal_relative,
        audit_policy,
    )
    job = _summary_job(run, repository_root, summary_relative, summary)
    _reject_ignored_attributed_paths(run, working_root)
    fingerprint = _workspace(working_root, seal_relative)
    return {
        "schema_version": policy["seal_schema_version"],
        "epic_id": run["epic_id"],
        "active_epic_path": active_relative,
        "implemented_epic_path": implemented_relative,
        "audit": {
            "attempt_path": attempt_path.relative_to(epic_dir).as_posix(),
            "attempt_sha256": scope_fingerprint.file_sha256(attempt_path),
            "attempt_id": attempt.get("attempt_id"),
            "repository_workspace_sha256": attempt.get(
                "repository_fingerprint", {}
            ).get("workspace_sha256"),
            "boundary_sha256": attempt.get("boundary_sha256"),
        },
        "summary": {
            "path": str(paths["summary"]),
            "sha256": scope_fingerprint.file_sha256(summary),
        },
        "summary_job": job,
        "workspace": fingerprint,
    }


def seal_delivery(args: argparse.Namespace) -> dict[str, Any]:
    policy = _policy(args.policy)
    run, repository_root, working_root = _canonical_run(args.run, policy)
    epic_dir = _active_epic(args.epic_dir, run, working_root)
    with scope_git.mutation_locks([working_root]):
        _reject_active_runs(repository_root, [working_root])
        payload = _seal_payload(
            epic_dir,
            run,
            repository_root,
            working_root,
            policy,
            args.audit_policy.resolve(),
        )
        _validate_seal_shape(payload, policy)
        seal_path = epic_dir / str(policy["paths"]["seal"])
        encoded = _dump_yaml(payload)
        status = "sealed"
        if seal_path.exists() or seal_path.is_symlink():
            if (
                seal_path.is_symlink()
                or scope_fingerprint.path_mode(seal_path) != "100644"
                or seal_path.read_bytes() != encoded
            ):
                raise WrapError("existing delivery seal is stale; refusing to overwrite it")
            status = "already_sealed"
        else:
            _atomic_write(
                seal_path,
                encoded,
                temporary_directory=scope_git.runtime_directory(
                    working_root, "scope-wrap"
                ),
            )
        return {
            "status": status,
            "seal_path": _relative(working_root, seal_path, "delivery seal"),
            "seal_sha256": scope_fingerprint.file_sha256(seal_path),
            "audit_attempt": payload["audit"]["attempt_path"],
            "workspace_sha256": payload["workspace"]["workspace_sha256"],
        }


def _verify_seal_files(
    epic_dir: Path, policy: Mapping[str, Any]
) -> tuple[dict[str, Any], Path, Path]:
    seal_path = _safe_child(epic_dir, str(policy["paths"]["seal"]), "delivery seal")
    seal = _load_yaml(seal_path, "delivery seal")
    if scope_fingerprint.path_mode(seal_path) != "100644":
        raise WrapError("delivery seal mode must be 100644")
    _validate_seal_shape(seal, policy)
    if seal_path.read_bytes() != _dump_yaml(seal):
        raise WrapError("delivery seal is not canonically encoded")
    manifest = _load_yaml(epic_dir / "delivery-manifest.yaml", "delivery manifest")
    if not _same_epic_id(manifest.get("epic_id"), seal.get("epic_id")):
        raise WrapError("delivery seal epic_id differs from the delivery manifest")
    summary_data = seal.get("summary")
    audit_data = seal.get("audit")
    if not isinstance(summary_data, dict) or not isinstance(audit_data, dict):
        raise WrapError("delivery seal summary and audit must be mappings")
    summary = _safe_child(epic_dir, str(summary_data.get("path")), "sealed summary")
    attempt = _safe_child(epic_dir, str(audit_data.get("attempt_path")), "sealed audit attempt")
    if scope_fingerprint.file_sha256(summary) != summary_data.get("sha256"):
        raise WrapError("implementation summary differs from the delivery seal")
    if scope_fingerprint.file_sha256(attempt) != audit_data.get("attempt_sha256"):
        raise WrapError("audit attempt differs from the delivery seal")
    attempt_doc = _load_yaml(attempt, "sealed audit attempt")
    if (
        attempt_doc.get("status") != "pass"
        or attempt_doc.get("decision", {}).get("outcome") != "pass"
        or not _same_epic_id(attempt_doc.get("epic_id"), seal.get("epic_id"))
        or attempt_doc.get("attempt_id") != audit_data.get("attempt_id")
        or attempt_doc.get("repository_fingerprint", {}).get("workspace_sha256")
        != audit_data.get("repository_workspace_sha256")
        or attempt_doc.get("boundary_sha256") != audit_data.get("boundary_sha256")
    ):
        raise WrapError("sealed audit facts are stale or invalid")
    return seal, seal_path, attempt


def _bind_seal_to_run(seal: Mapping[str, Any], run: Mapping[str, Any]) -> None:
    if not _same_epic_id(seal.get("epic_id"), run.get("epic_id")):
        raise WrapError("delivery seal belongs to a different implement run")


def _verify_active(
    epic_dir: Path,
    working_root: Path,
    policy: Mapping[str, Any],
    audit_policy: Path,
) -> tuple[dict[str, Any], Path]:
    seal, seal_path, attempt_path = _verify_seal_files(epic_dir, policy)
    if _relative(working_root, epic_dir, "active epic") != seal.get("active_epic_path"):
        raise WrapError("delivery seal belongs to a different active epic path")
    seal_relative = _relative(working_root, seal_path, "delivery seal")
    summary_relative = (
        PurePosixPath(str(seal["active_epic_path"])) / str(seal["summary"]["path"])
    ).as_posix()
    attempt_doc = _load_yaml(attempt_path, "sealed audit attempt")
    _validate_audit(
        epic_dir,
        working_root,
        attempt_path,
        attempt_doc,
        summary_relative,
        seal_relative,
        audit_policy,
    )
    if _workspace(working_root, seal_relative) != seal.get("workspace", {}):
        raise WrapError("workspace differs from the exact sealed delivery")
    return seal, seal_path


def verify_delivery(args: argparse.Namespace) -> dict[str, Any]:
    policy = _policy(args.policy)
    working_root = scope_git.top_level(args.repo_root.resolve())
    epic_dir = args.epic_dir.resolve(strict=True)
    seal, seal_path = _verify_active(
        epic_dir, working_root, policy, args.audit_policy.resolve()
    )
    return {
        "status": "verified",
        "seal_path": _relative(working_root, seal_path, "delivery seal"),
        "seal_sha256": scope_fingerprint.file_sha256(seal_path),
        "audit_attempt": seal["audit"]["attempt_path"],
        "workspace_sha256": seal["workspace"]["workspace_sha256"],
    }


def _prepare_path(run_path: Path, policy: Mapping[str, Any]) -> Path:
    return run_path.resolve(strict=True).parent / str(policy["paths"]["prepare_state"])


def _tree_paths(working_root: Path, revision: str, path: str) -> set[str]:
    raw = scope_git.git(
        working_root,
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        revision,
        "--",
        path,
        binary=True,
    )
    assert isinstance(raw, bytes)
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in raw.split(b"\0")
        if value
    }


def _expected_staged_paths(
    working_root: Path,
    seal: Mapping[str, Any],
    archived_epic: Path,
    seal_name: str,
) -> set[str]:
    active = str(seal["active_epic_path"])
    archived = str(seal["implemented_epic_path"])
    baseline = str(seal["workspace"]["head"])
    tracked = _tree_paths(working_root, baseline, active)
    expected: set[str] = set()
    for relative in tracked:
        expected.add(relative)
        mapped = archived + relative[len(active) :]
        if (working_root / mapped).exists() or (working_root / mapped).is_symlink():
            expected.add(mapped)
    for row in seal["workspace"]["changes"]:
        for field in ("path", "old_path"):
            relative = row.get(field)
            if not isinstance(relative, str):
                continue
            if relative == active or relative.startswith(active + "/"):
                if relative in tracked or field == "old_path":
                    expected.add(relative)
                mapped = archived + relative[len(active) :]
                if (working_root / mapped).exists() or (working_root / mapped).is_symlink():
                    expected.add(mapped)
            else:
                expected.add(relative)
    expected.add((PurePosixPath(archived) / seal_name).as_posix())
    if archived_epic != working_root / archived:
        raise WrapError("archive path differs from the delivery seal")
    return expected


def _staged_paths(working_root: Path, baseline: str) -> set[str]:
    raw = scope_git.git(
        working_root,
        "diff",
        "--cached",
        "--no-renames",
        "--name-only",
        "-z",
        baseline,
        "--",
        binary=True,
    )
    assert isinstance(raw, bytes)
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in raw.split(b"\0")
        if value
    }


def _stage_prepared(
    working_root: Path,
    seal: Mapping[str, Any],
    archived_epic: Path,
    seal_name: str,
) -> str:
    baseline = str(seal["workspace"]["head"])
    expected = _expected_staged_paths(
        working_root, seal, archived_epic, seal_name
    )
    changed_raw = scope_git.git(
        working_root,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        baseline,
        "--",
        binary=True,
    )
    untracked_raw = scope_git.git(
        working_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        binary=True,
    )
    assert isinstance(changed_raw, bytes) and isinstance(untracked_raw, bytes)
    present = {
        value.decode("utf-8", errors="surrogateescape")
        for value in (changed_raw + untracked_raw).split(b"\0")
        if value
    }
    if present != expected:
        raise WrapError(
            f"workspace paths differ from the sealed archival delta; expected={sorted(expected)}, actual={sorted(present)}"
        )
    if (
        _staged_paths(working_root, baseline) == expected
        and scope_git.run(["git", "diff", "--quiet", "--"], working_root).returncode == 0
        and not str(scope_git.git(working_root, "ls-files", "--others", "--exclude-standard"))
    ):
        return str(scope_git.git(working_root, "write-tree")).lower()
    selected = sorted(expected)
    for offset in range(0, len(selected), 256):
        scope_git.git(
            working_root,
            "--literal-pathspecs",
            "add",
            "-A",
            "--",
            *selected[offset : offset + 256],
        )
    if scope_git.run(["git", "diff", "--quiet", "--"], working_root).returncode != 0:
        raise WrapError("prepare left unstaged tracked changes")
    untracked = str(
        scope_git.git(working_root, "ls-files", "--others", "--exclude-standard")
    )
    if untracked:
        raise WrapError("prepare left untracked files outside the sealed delta")
    actual = _staged_paths(working_root, baseline)
    if actual != expected:
        raise WrapError(
            f"staged paths differ from the sealed archival delta; expected={sorted(expected)}, actual={sorted(actual)}"
        )
    return str(scope_git.git(working_root, "write-tree")).lower()


def _archive_files_valid(
    archived_epic: Path, working_root: Path, policy: Mapping[str, Any]
) -> tuple[dict[str, Any], Path]:
    seal, seal_path, _ = _verify_seal_files(archived_epic, policy)
    if _relative(working_root, archived_epic, "archived epic") != seal.get(
        "implemented_epic_path"
    ):
        raise WrapError("archived epic path differs from the delivery seal")
    active = str(seal["active_epic_path"])
    archived = str(seal["implemented_epic_path"])
    changed = {
        relative
        for row in seal.get("workspace", {}).get("changes", [])
        if isinstance(row, dict)
        for relative in (row.get("path"), row.get("old_path"))
        if isinstance(relative, str)
    }
    for row in seal.get("workspace", {}).get("changes", []):
        relative = row.get("path")
        if not isinstance(relative, str):
            raise WrapError("sealed workspace contains an invalid path")
        target = working_root / relative
        if relative == active or relative.startswith(active + "/"):
            target = working_root / (
                archived + relative[len(active) :]
            )
        if (
            scope_fingerprint.path_identity(target) != row.get("content_sha256")
            or scope_fingerprint.path_mode(target) != row.get("mode")
        ):
            raise WrapError(f"archived workspace content differs from seal: {relative}")
    baseline = str(seal["workspace"]["head"])
    for relative in sorted(_tree_paths(working_root, baseline, active) - changed):
        target = working_root / (archived + relative[len(active) :])
        blob = scope_git.git(
            working_root, "show", f"{baseline}:{relative}", binary=True
        )
        assert isinstance(blob, bytes)
        metadata = str(scope_git.git(working_root, "ls-tree", baseline, "--", relative))
        expected = scope_fingerprint.sha256_bytes(
            (b"symlink\0" if metadata.startswith("120000 ") else b"") + blob
        )
        expected_mode = metadata.split(maxsplit=1)[0]
        if (
            scope_fingerprint.path_identity(target) != expected
            or scope_fingerprint.path_mode(target) != expected_mode
        ):
            raise WrapError(f"archived tracked content changed after sealing: {relative}")
    return seal, seal_path


def _exact_closure(
    working_root: Path,
    commit: str,
    base: str,
    subject: str,
    expected_tree: str | None = None,
) -> bool:
    parents = str(
        scope_git.git(working_root, "show", "-s", "--format=%P", commit)
    ).lower().split()
    return (
        parents == [base]
        and scope_git.git(working_root, "show", "-s", "--format=%s", commit)
        == subject
        and (expected_tree is None or scope_git.tree(working_root, commit) == expected_tree)
        and not scope_git.status(working_root)
    )


def prepare_archival(args: argparse.Namespace) -> dict[str, Any]:
    policy = _policy(args.policy)
    run, repository_root, working_root = _canonical_run(args.run, policy)
    main_root = scope_git.top_level(args.main_root.resolve())
    if main_root != repository_root:
        raise WrapError("--main-root differs from the implement repository root")
    name = args.epic_dir.name
    active_epic = working_root / "docs" / "epics" / name
    archived_epic = working_root / str(policy["paths"]["implemented_root"]) / name
    with scope_git.mutation_locks([working_root, main_root]):
        _reject_active_runs(repository_root, [working_root, main_root])
        if active_epic.exists() and archived_epic.exists():
            raise WrapError("both active and archived epic directories exist")
        if active_epic.exists():
            selected = _active_epic(active_epic, run, working_root)
            seal, seal_path = _verify_active(
                selected, working_root, policy, args.audit_policy.resolve()
            )
            if scope_git.head(working_root) != seal["workspace"]["head"]:
                raise WrapError("worktree HEAD differs from the delivery seal")
            if scope_git.status(main_root):
                raise WrapError("main root must be clean before wrap preparation")
            archived_epic.parent.mkdir(parents=True, exist_ok=True)
            if archived_epic.parent.is_symlink():
                raise WrapError("implemented epic root must not be a symlink")
            selected.rename(archived_epic)
            seal_path = archived_epic / seal_path.name
        elif archived_epic.exists():
            seal, seal_path = _archive_files_valid(archived_epic, working_root, policy)
            if scope_git.status(main_root):
                raise WrapError("main root must be clean before wrap preparation")
        else:
            raise WrapError("neither active nor archived epic directory exists")
        _bind_seal_to_run(seal, run)
        base = str(seal["workspace"]["head"])
        current_worktree = scope_git.head(working_root)
        closure_label = str(policy["labels"]["closure"]).format(epic_id=run["epic_id"])
        if current_worktree != base and not _exact_closure(
            working_root, current_worktree, base, closure_label
        ):
            raise WrapError("worktree HEAD differs from the delivery seal")
        staged_tree = _stage_prepared(
            working_root, seal, archived_epic, str(policy["paths"]["seal"])
        )
        if current_worktree != base and scope_git.tree(
            working_root, current_worktree
        ) != staged_tree:
            raise WrapError("resumable closure tree differs from the archived delivery")
        main_head = scope_git.head(main_root)
        payload = {
            "schema_version": policy["prepare_schema_version"],
            "epic_id": run["epic_id"],
            "working_root": str(working_root),
            "main_root": str(main_root),
            "archived_epic_path": str(seal["implemented_epic_path"]),
            "seal_sha256": scope_fingerprint.file_sha256(seal_path),
            "worktree_head": str(seal["workspace"]["head"]),
            "staged_tree": staged_tree,
            "main_head": main_head,
            "main_branch": str(scope_git.git(main_root, "symbolic-ref", "--short", "HEAD")),
            "closure_label": closure_label,
            "merge_label": str(policy["labels"]["merge"]).format(epic_id=run["epic_id"]),
        }
        state_path = _prepare_path(args.run, policy)
        encoded = _dump_yaml(payload)
        status = "prepared"
        if state_path.is_symlink():
            raise WrapError("wrap preparation state must not be a symlink")
        if state_path.exists():
            if state_path.read_bytes() == encoded:
                status = "already_prepared"
            else:
                _atomic_write(state_path, encoded)
        else:
            _atomic_write(state_path, encoded)
        return {"status": status, **{key: payload[key] for key in (
            "archived_epic_path", "staged_tree", "main_head", "main_branch", "worktree_head",
            "closure_label", "merge_label", "seal_sha256",
        )}}


def _matching_merge(
    main_root: Path, approved_main: str, closure_commit: str, subject: str
) -> str | None:
    expected_tree = scope_git.merge_tree(main_root, approved_main, closure_commit)
    current = scope_git.head(main_root)
    candidates = [current]
    if current != approved_main:
        raw = str(
            scope_git.git(
                main_root,
                "rev-list",
                "--first-parent",
                f"{approved_main}..{current}",
            )
        )
        candidates.extend(line for line in raw.splitlines() if line)
    for commit in dict.fromkeys(candidates):
        parents = str(
            scope_git.git(main_root, "show", "-s", "--format=%P", commit)
        ).lower().split()
        if (
            parents == [approved_main, closure_commit]
            and scope_git.tree(main_root, commit) == expected_tree
            and scope_git.git(main_root, "show", "-s", "--format=%s", commit)
            == subject
        ):
            return commit
    return None


def _codegraph(main_root: Path, policy: Mapping[str, Any], override: Path | None) -> dict[str, Any]:
    policy_path = override or (
        Path(__file__).resolve().parent.parent
        / "config"
        / str(policy["paths"]["codegraph_policy"])
    )
    return scope_codegraph.prepare(scope_codegraph.load_policy(policy_path), main_root)


def commit_merge(args: argparse.Namespace) -> dict[str, Any]:
    policy = _policy(args.policy)
    run, repository_root, working_root = _canonical_run(args.run, policy)
    main_root = scope_git.top_level(args.main_root.resolve())
    if main_root != repository_root:
        raise WrapError("--main-root differs from the implement repository root")
    archived_epic = args.epic_dir.resolve(strict=True)
    approved_tree = scope_git.full_commit(args.approved_staged_tree, "approved staged tree")
    approved_main = scope_git.full_commit(args.approved_main_head, "approved main HEAD")
    approved_branch = args.approved_main_branch
    if not isinstance(approved_branch, str) or not approved_branch:
        raise WrapError("approved main branch must be non-empty")
    closure_label = str(policy["labels"]["closure"]).format(epic_id=run["epic_id"])
    merge_label = str(policy["labels"]["merge"]).format(epic_id=run["epic_id"])
    with scope_git.mutation_locks([working_root, main_root]):
        _reject_active_runs(repository_root, [working_root, main_root])
        seal, seal_path = _archive_files_valid(archived_epic, working_root, policy)
        _bind_seal_to_run(seal, run)
        state = _load_yaml(_prepare_path(args.run, policy), "wrap preparation state")
        if (
            state.get("schema_version") != policy["prepare_schema_version"]
            or state.get("epic_id") != run["epic_id"]
            or state.get("working_root") != str(working_root)
            or state.get("main_root") != str(main_root)
            or state.get("archived_epic_path") != seal["implemented_epic_path"]
            or state.get("worktree_head") != seal["workspace"]["head"]
            or state.get("staged_tree") != approved_tree
            or state.get("main_head") != approved_main
            or state.get("main_branch") != approved_branch
            or state.get("seal_sha256") != scope_fingerprint.file_sha256(seal_path)
            or state.get("closure_label") != closure_label
            or state.get("merge_label") != merge_label
        ):
            raise WrapError("approval does not match the prepared closure state")
        if scope_git.git(main_root, "symbolic-ref", "--short", "HEAD") != approved_branch:
            raise WrapError("main branch changed after approval")
        base = scope_git.full_commit(str(seal["workspace"]["head"]), "sealed worktree HEAD")
        current_worktree = scope_git.head(working_root)
        if current_worktree == base:
            if str(scope_git.git(working_root, "write-tree")).lower() != approved_tree:
                raise WrapError("staged tree changed after approval")
            if scope_git.run(["git", "diff", "--quiet", "--"], working_root).returncode != 0:
                raise WrapError("unstaged changes appeared after approval")
            if str(
                scope_git.git(
                    working_root, "ls-files", "--others", "--exclude-standard"
                )
            ):
                raise WrapError("untracked changes appeared after approval")
            closure_commit = scope_git.commit_index(
                working_root, base, approved_tree, closure_label
            )
        else:
            closure_commit = current_worktree
            if not _exact_closure(
                working_root, closure_commit, base, closure_label, approved_tree
            ):
                raise WrapError("worktree is not at the exact resumable closure commit")
        existing_merge = _matching_merge(
            main_root, approved_main, closure_commit, merge_label
        )
        if existing_merge is not None:
            if scope_git.status(main_root):
                raise WrapError("main root must be clean before completing wrap recovery")
            merge_commit = existing_merge
            status = "already_merged"
        else:
            if scope_git.head(main_root) != approved_main:
                raise WrapError("main HEAD changed after approval")
            merge_commit = scope_git.merge_exact(
                main_root, closure_commit, approved_main, merge_label
            )
            status = "merged"
    codegraph = _codegraph(main_root, policy, args.codegraph_policy)
    return {
        "status": status,
        "closure_commit": closure_commit,
        "merge_commit": merge_commit,
        "archived_epic_dir": str(archived_epic),
        "codegraph": codegraph,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("render-summary", help="Render the delivery summary from validated evidence")
    summary.add_argument("epic_dir", type=Path)
    summary.add_argument("--run", type=Path, required=True)
    summary.add_argument("--policy", type=Path, default=_default_policy())
    summary.add_argument("--audit-policy", type=Path, default=_default_audit_policy())
    summary.set_defaults(handler=render_summary)

    seal = subparsers.add_parser("seal", help="Seal one validated delivery-summary overlay")
    seal.add_argument("epic_dir", type=Path)
    seal.add_argument("--run", type=Path, required=True)
    seal.add_argument("--policy", type=Path, default=_default_policy())
    seal.add_argument("--audit-policy", type=Path, default=_default_audit_policy())
    seal.set_defaults(handler=seal_delivery)

    verify = subparsers.add_parser("verify", help="Verify one durable delivery seal")
    verify.add_argument("epic_dir", type=Path)
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--policy", type=Path, default=_default_policy())
    verify.add_argument("--audit-policy", type=Path, default=_default_audit_policy())
    verify.set_defaults(handler=verify_delivery)

    prepare = subparsers.add_parser("prepare", help="Archive and stage the exact sealed delivery")
    prepare.add_argument("epic_dir", type=Path)
    prepare.add_argument("--run", type=Path, required=True)
    prepare.add_argument("--main-root", type=Path, required=True)
    prepare.add_argument("--policy", type=Path, default=_default_policy())
    prepare.add_argument("--audit-policy", type=Path, default=_default_audit_policy())
    prepare.set_defaults(handler=prepare_archival)

    merge = subparsers.add_parser("commit-merge", help="Commit and merge one approved prepared tree")
    merge.add_argument("epic_dir", type=Path)
    merge.add_argument("--run", type=Path, required=True)
    merge.add_argument("--main-root", type=Path, required=True)
    merge.add_argument("--approved-staged-tree", required=True)
    merge.add_argument("--approved-main-head", required=True)
    merge.add_argument("--approved-main-branch", required=True)
    merge.add_argument("--policy", type=Path, default=_default_policy())
    merge.add_argument("--codegraph-policy", type=Path)
    merge.set_defaults(handler=commit_merge)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(args.handler(args), sort_keys=True))
        return 0
    except (OSError, WrapError, scope_git.GitError, scope_codegraph.CodeGraphPolicyError) as exc:
        print(f"Scope wrap finalization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

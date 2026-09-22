#!/usr/bin/env python3
"""Launch one bounded Scope worker with a small, recoverable lifecycle."""

from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

from filelock import FileLock, Timeout as FileLockTimeout
from jsonschema import Draft202012Validator
import psutil
import yaml

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import scope_codegraph  # noqa: E402
import scope_proofs  # noqa: E402


SCHEMA_VERSION = 2
IMPLEMENTATION_EVIDENCE_VERSION = 2
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
WORKER_PROFILES = {"default": "workers", "budget": "workers_on_budget"}
ROLE_PHASES = {
    "refinement": {"product", "design_handoff", "correction"},
    "implementation": {
        "story",
        "audit_remediation",
        "debugging",
    },
    "diagnostic": {"investigate"},
}
ROLE_COMMANDS = {
    "refinement": {"epic_refine"},
    "implementation": {"implement"},
    "diagnostic": {"epic_refine", "implement", "audit_epic"},
}
ROLE_PROMPTS = {
    "refinement": "refinement-worker.md",
    "implementation": "implementation-worker.md",
    "diagnostic": "diagnostic-worker.md",
}
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


class WorkerError(RuntimeError):
    """Base class for a controlled worker failure."""


class ContractError(WorkerError):
    """A job, result, policy, or path contract is invalid."""


class InfrastructureError(WorkerError):
    """The provider or local worker infrastructure failed."""


class WorkerTimeout(InfrastructureError):
    """The provider exceeded its hard timeout."""


class WorkerCancelled(InfrastructureError):
    """The worker was explicitly cancelled."""


class ActiveWorkerError(InfrastructureError):
    """The working root already has active mutation work."""


class DuplicateKeyError(ValueError):
    """YAML contains a duplicate mapping key."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def atomic_write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write(path, yaml.safe_dump(dict(value), sort_keys=False))


def load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"missing {label}: {path}")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, DuplicateKeyError) as exc:
        raise ContractError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain a mapping: {path}")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must contain an object: {path}")
    return value


def validate_against_schema(value: Any, schema: Mapping[str, Any], label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    details = []
    for error in errors[:12]:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    raise ContractError(f"invalid {label}: {'; '.join(details)}")


def _run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 30,
    check: bool = False,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if environment is None else {**os.environ, **environment},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InfrastructureError(f"unable to run {' '.join(args)}: {exc}") from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise InfrastructureError(
            f"command failed ({completed.returncode}): {' '.join(args)}: {detail}"
        )
    return completed


def _canonical_directory(value: Any, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ContractError(f"{field} must be a non-empty absolute directory")
    path = Path(value)
    if not path.is_absolute():
        raise ContractError(f"{field} must be absolute: {value}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{field} cannot be resolved: {value}: {exc}") from exc
    if not resolved.is_dir():
        raise ContractError(f"{field} is not a directory: {resolved}")
    return resolved


def _canonical_file_parent(value: Any, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ContractError(f"{field} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ContractError(f"{field} must be absolute: {value}")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{field} parent cannot be resolved: {value}: {exc}") from exc
    if not parent.is_dir():
        raise ContractError(f"{field} parent is not a directory: {parent}")
    return parent / path.name


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_normcase(path), _normcase(root))) == _normcase(root)
    except ValueError:
        return False


def normalize_relative_path(value: Any, field: str, *, allow_root: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty repository-relative path")
    if "\\" in value or value.startswith(("/", "//")) or WINDOWS_DRIVE_PATTERN.match(value):
        raise ContractError(f"{field} must be a forward-slash repository-relative path: {value}")
    parts = value.split("/")
    if any(part in {"", ".."} for part in parts):
        raise ContractError(f"{field} contains an empty or parent component: {value}")
    if "." in parts:
        if allow_root and value == ".":
            return value
        raise ContractError(f"{field} contains a current-directory component: {value}")
    return value


def _validate_scoped_path(root: Path, value: Any, field: str, *, allow_root: bool) -> str:
    relative = normalize_relative_path(value, field, allow_root=allow_root)
    candidate = root if relative == "." else root / relative
    if not is_within(candidate.resolve(strict=False), root):
        raise ContractError(f"{field} escapes working_root through a symlink: {value}")
    return relative


def _path_in_scope(path: str, scopes: Sequence[str]) -> bool:
    return any(
        scope == "." or path == scope or path.startswith(f"{scope.rstrip('/')}/")
        for scope in scopes
    )


def _git_root(path: Path) -> Path:
    return Path(
        _run(["git", "rev-parse", "--show-toplevel"], cwd=path, check=True).stdout.strip()
    ).resolve(strict=True)


def _git_common_dir(path: Path) -> Path:
    raw = Path(
        _run(["git", "rev-parse", "--git-common-dir"], cwd=path, check=True).stdout.strip()
    )
    return (raw if raw.is_absolute() else path / raw).resolve(strict=True)


def _validate_repository_relationship(repository_root: Path, working_root: Path) -> None:
    if _normcase(_git_root(repository_root)) != _normcase(repository_root):
        raise ContractError(f"repository_root is not a Git checkout root: {repository_root}")
    if _normcase(_git_root(working_root)) != _normcase(working_root):
        raise ContractError(f"working_root is not a Git checkout/worktree root: {working_root}")
    if _normcase(_git_common_dir(repository_root)) != _normcase(_git_common_dir(working_root)):
        raise ContractError("working_root is not a worktree of repository_root")


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError(f"cannot hash {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def _structured_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _json_document_sha256(value: Any) -> str:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _sha256_bytes(encoded)


def installed_config_path(scope_root: Path, name: str) -> Path:
    return scope_root / "config" / name


def load_codegraph_policy(scope_root: Path) -> dict[str, Any]:
    try:
        policy = scope_codegraph.load_policy(
            installed_config_path(scope_root, "codegraph-policy.yaml")
        )
    except scope_codegraph.CodeGraphPolicyError as exc:
        raise ContractError(str(exc)) from exc
    unknown = sorted(set(policy["worker_roles"]) - set(ROLE_PHASES))
    if unknown:
        raise ContractError(f"CodeGraph policy contains unknown roles: {unknown}")
    return policy


def load_policy(scope_root: Path) -> dict[str, Any]:
    policy = load_yaml(installed_config_path(scope_root, "worker-policy.yaml"), "worker policy")
    if policy.get("schema_version") != 2:
        raise ContractError("worker policy schema_version must be 2")
    provider = policy.get("provider")
    if provider not in {"codex", "claude"}:
        raise ContractError("worker policy provider must be codex or claude")
    for profile_name, section_name in WORKER_PROFILES.items():
        section = policy.get(section_name)
        if not isinstance(section, dict):
            raise ContractError(f"worker policy missing {section_name}")
        for role, expected_phases in ROLE_PHASES.items():
            row = section.get(role)
            phases = row.get("phases") if isinstance(row, dict) else None
            if not isinstance(phases, dict) or set(phases) != expected_phases:
                raise ContractError(
                    f"worker policy {section_name}.{role}.phases must be {sorted(expected_phases)}"
                )
            for phase, selected in phases.items():
                if not isinstance(selected, dict) or set(selected) != {"model", "reasoning_effort"}:
                    raise ContractError(
                        f"worker policy {section_name}.{role}.{phase} must contain model and reasoning_effort"
                    )
                if not all(isinstance(selected[key], str) and selected[key] for key in selected):
                    raise ContractError("worker model and effort must be non-empty strings")
    settings = policy.get("provider_settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("executable"), str):
        raise ContractError("worker policy provider_settings.executable is required")
    runtime = policy.get("runtime")
    roles = runtime.get("roles") if isinstance(runtime, dict) else None
    lifecycle = runtime.get("lifecycle") if isinstance(runtime, dict) else None
    if not isinstance(roles, dict) or not isinstance(lifecycle, dict):
        raise ContractError("worker policy runtime.roles and runtime.lifecycle are required")
    for role in ROLE_PHASES:
        row = roles.get(role)
        if not isinstance(row, dict):
            raise ContractError(f"worker runtime missing role: {role}")
        if not isinstance(row.get("timeout_seconds"), int) or row["timeout_seconds"] <= 0:
            raise ContractError(f"worker runtime {role}.timeout_seconds must be positive")
    positive = (
        "poll_interval_seconds",
        "heartbeat_interval_seconds",
        "termination_grace_seconds",
        "log_tail_lines",
        "log_tail_characters",
    )
    if any(not isinstance(lifecycle.get(field), (int, float)) or lifecycle[field] <= 0 for field in positive):
        raise ContractError("worker runtime lifecycle values must be positive")
    if not isinstance(lifecycle.get("normal_exit_grace_seconds"), (int, float)) or lifecycle["normal_exit_grace_seconds"] < 0:
        raise ContractError("normal_exit_grace_seconds must be non-negative")
    return policy


def _provider_policy(
    policy: Mapping[str, Any], role: str, phase: str, provider: str, profile: str
) -> dict[str, Any]:
    if policy["provider"] != provider:
        raise ContractError(f"installed worker policy is for {policy['provider']}, not {provider}")
    section = WORKER_PROFILES.get(profile)
    if section is None:
        raise ContractError(f"unsupported worker profile: {profile}")
    selected = policy[section][role]["phases"][phase]
    runtime = policy["runtime"]
    return {
        **selected,
        **runtime["roles"][role],
        **runtime["lifecycle"],
        "worker_profile": profile,
        "executable": policy["provider_settings"]["executable"],
        "permission_mode": policy["provider_settings"].get("permission_mode", "dontAsk"),
    }


def runtime_root(repository_root: Path) -> Path:
    return repository_root / "tmp_debug" / "scope-runs"


def run_directory_from_identity(repository_root: Path, epic_id: str, command: str) -> Path:
    return runtime_root(repository_root) / epic_id / command


def run_directory(job: Mapping[str, Any], repository_root: Path) -> Path:
    return run_directory_from_identity(repository_root, str(job["epic_id"]), str(job["command"]))


def job_directory(job: Mapping[str, Any], repository_root: Path) -> Path:
    return run_directory(job, repository_root) / "jobs" / str(job["job_id"])


def mutation_lock_path(working_root: Path) -> Path:
    return working_root / "tmp_debug" / "scope-mutation.lock"


def run_state_lock_path(run_path: Path) -> Path:
    return run_path.with_name("run-state.lock")


def _validate_runner_path(path: Path, root: Path, field: str) -> Path:
    """Reject runner-owned paths that escape through any symlink component."""
    lexical = path.absolute()
    lexical_root = root.absolute()
    try:
        relative = lexical.relative_to(lexical_root)
    except ValueError as exc:
        raise ContractError(f"{field} must be beneath {root}: {path}") from exc
    current = lexical_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{field} contains a symlink component: {current}")
    if not is_within(lexical.resolve(strict=False), root):
        raise ContractError(f"{field} escapes {root}: {path}")
    return lexical


def _validated_mutation_lock_path(working_root: Path) -> Path:
    return _validate_runner_path(
        mutation_lock_path(working_root), working_root, "mutation lock path"
    )


def _validated_state_lock_path(run_path: Path, repository_root: Path) -> Path:
    return _validate_runner_path(
        run_state_lock_path(run_path), repository_root, "worker state lock path"
    )


def _worker_policy_identity(scope_root: Path) -> str:
    return _sha256_file(installed_config_path(scope_root, "worker-policy.yaml"))


def _load_run(run_path: Path) -> tuple[dict[str, Any], Path, Path]:
    run = load_yaml(run_path, "worker run")
    if run.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(
            "worker run uses an obsolete schema; archive its runtime directory and start a lean run"
        )
    repository_root = _canonical_directory(run.get("repository_root"), "run.repository_root")
    working_root = _canonical_directory(run.get("working_root"), "run.working_root")
    _validate_repository_relationship(repository_root, working_root)
    _validate_runner_path(run_path, repository_root, "worker run path")
    expected = run_directory_from_identity(repository_root, str(run.get("epic_id")), str(run.get("command"))) / "run.yaml"
    if _normcase(run_path) != _normcase(expected):
        raise ContractError(f"worker run path must be {expected}")
    if run.get("worker_profile") not in WORKER_PROFILES:
        raise ContractError("worker run has invalid worker_profile")
    if not isinstance(run.get("completed_jobs"), list) or "active_job" not in run:
        raise ContractError("worker run has invalid job state")
    ids = [row.get("job_id") for row in run["completed_jobs"] if isinstance(row, dict)]
    if len(ids) != len(run["completed_jobs"]) or len(set(ids)) != len(ids):
        raise ContractError("worker run has invalid or duplicate completed jobs")
    return run, repository_root, working_root


def _assert_run_binding(
    run: Mapping[str, Any], job: Mapping[str, Any], worker_profile: str
) -> None:
    for field in ("repository_root", "working_root", "epic_id", "command"):
        if str(run.get(field)) != str(job[field]):
            raise ContractError(f"worker run {field} does not match the job")
    if run.get("worker_profile") != worker_profile:
        raise ContractError("worker run worker_profile does not match the CLI")
    scope_root = Path(str(job["scope_root"]))
    if not isinstance(run.get("scope_root"), str) or _normcase(Path(run["scope_root"])) != _normcase(scope_root):
        raise ContractError("worker run scope_root does not match the job; run init first")
    if run.get("worker_policy_sha256") != _worker_policy_identity(scope_root):
        raise ContractError("worker policy changed since run initialization")


def _write_run(path: Path, run: dict[str, Any]) -> None:
    run["updated_at"] = utc_now()
    atomic_write_yaml(path, run)


def _compact_codegraph(state: Mapping[str, Any]) -> dict[str, Any]:
    return scope_codegraph.receipt(state)


def _expanded_codegraph(
    compact: Mapping[str, Any], policy: Mapping[str, Any], working_root: Path
) -> dict[str, Any]:
    return {
        **compact,
        "project_root": str(working_root),
        "index_path": str(working_root / str(policy["index_directory"])),
        "minimum_version": str(policy["minimum_version"]),
        "query_commands": list(policy["query_commands"]),
        "explore_max_files": int(policy["explore_max_files"]),
        "affected_depth": int(policy["affected"]["depth"]),
        "affected_test_filters": list(policy["affected"]["test_filters"]),
    }


def initialize_run(
    repository_root_value: Path | str,
    working_root_value: Path | str,
    epic_id: str,
    command: str,
    worker_profile: str,
    scope_root_value: Path | str,
) -> dict[str, Any]:
    repository_root = _canonical_directory(repository_root_value, "repository_root")
    working_root = _canonical_directory(working_root_value, "working_root")
    scope_root = _canonical_directory(scope_root_value, "scope_root")
    _validate_repository_relationship(repository_root, working_root)
    if not JOB_ID_PATTERN.fullmatch(epic_id):
        raise ContractError("epic_id contains unsupported characters")
    if command not in set().union(*ROLE_COMMANDS.values()):
        raise ContractError(f"invalid worker command: {command}")
    if worker_profile not in WORKER_PROFILES:
        raise ContractError(f"unsupported worker profile: {worker_profile}")
    load_policy(scope_root)
    policy_identity = _worker_policy_identity(scope_root)
    codegraph_policy = load_codegraph_policy(scope_root)
    run_path = run_directory_from_identity(repository_root, epic_id, command) / "run.yaml"
    _validate_runner_path(run_path, repository_root, "worker run path")
    run_path.parent.mkdir(parents=True, exist_ok=True)
    state_guard = FileLock(str(_validated_state_lock_path(run_path, repository_root)))
    try:
        state_guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        raise ActiveWorkerError("worker run state transition is active") from exc
    selected_lock = _validated_mutation_lock_path(working_root)
    selected_lock.parent.mkdir(parents=True, exist_ok=True)
    guard = FileLock(str(selected_lock))
    try:
        guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        state_guard.release()
        raise ActiveWorkerError(f"working-root mutation lock is held: {selected_lock}") from exc
    try:
        if run_path.exists():
            run, _, _ = _load_run(run_path)
            for field, expected in {
                "epic_id": epic_id,
                "command": command,
                "repository_root": str(repository_root),
                "working_root": str(working_root),
                "worker_profile": worker_profile,
            }.items():
                if str(run.get(field)) != str(expected):
                    raise ContractError(f"existing run {field} does not match")
            changed = False
            if run.get("scope_root") is None or run.get("worker_policy_sha256") is None:
                if run.get("active_job") is not None:
                    raise ActiveWorkerError(
                        "cannot bind an active run to its Scope installation"
                    )
                run["scope_root"] = str(scope_root)
                run["worker_policy_sha256"] = policy_identity
                changed = True
            if _normcase(Path(str(run.get("scope_root")))) != _normcase(scope_root):
                raise ContractError("existing run scope_root does not match")
            if run.get("worker_policy_sha256") != policy_identity:
                raise ContractError("worker policy changed since run initialization")
            if changed:
                _write_run(run_path, run)
            created = False
        else:
            state = scope_codegraph.prepare(codegraph_policy, working_root)
            now = utc_now()
            run = {
                "schema_version": SCHEMA_VERSION,
                "epic_id": epic_id,
                "command": command,
                "repository_root": str(repository_root),
                "working_root": str(working_root),
                "scope_root": str(scope_root),
                "worker_policy_sha256": policy_identity,
                "worker_profile": worker_profile,
                "created_at": now,
                "updated_at": now,
                "codegraph": _compact_codegraph(state),
                "active_job": None,
                "completed_jobs": [],
            }
            atomic_write_yaml(run_path, run)
            created = True
    finally:
        guard.release()
        state_guard.release()
    return {"status": "initialized", "created": created, "run": str(run_path)}


def load_job(
    job_path: Path, *, verify_artifact_hashes: bool = True
) -> tuple[dict[str, Any], Path, Path, Path]:
    job = load_yaml(job_path, "worker job")
    scope_root = _canonical_directory(job.get("scope_root"), "scope_root")
    schema = load_json(installed_config_path(scope_root, "worker-job.schema.json"), "worker job schema")
    validate_against_schema(job, schema, "worker job")
    repository_root = _canonical_directory(job["repository_root"], "repository_root")
    working_root = _canonical_directory(job["working_root"], "working_root")
    _validate_repository_relationship(repository_root, working_root)
    _validate_runner_path(job_path, repository_root, "worker job path")
    if job["phase"] not in ROLE_PHASES[job["role"]] or job["command"] not in ROLE_COMMANDS[job["role"]]:
        raise ContractError("worker role, phase, and command are incompatible")
    job["read_scope"] = [
        _validate_scoped_path(working_root, value, f"read_scope[{index}]", allow_root=True)
        for index, value in enumerate(job["read_scope"])
    ]
    job["write_scope"] = [
        _validate_scoped_path(working_root, value, f"write_scope[{index}]", allow_root=True)
        for index, value in enumerate(job["write_scope"])
    ]
    if job["role"] == "implementation":
        evidence_relative = _validate_scoped_path(
            working_root,
            job["implementation_evidence_path"],
            "implementation_evidence_path",
            allow_root=False,
        )
        evidence_path = working_root / evidence_relative
        if evidence_path.name != "implementation-evidence.yaml":
            raise ContractError(
                "implementation_evidence_path must name implementation-evidence.yaml"
            )
        try:
            epic_root = (working_root / "docs" / "epics").resolve(strict=True)
            evidence_parent = evidence_path.parent.resolve(strict=True)
        except OSError as exc:
            raise ContractError(
                f"implementation evidence parent cannot be resolved: {evidence_path.parent}"
            ) from exc
        if evidence_parent.parent != epic_root or evidence_path.is_symlink():
            raise ContractError(
                "implementation_evidence_path must be a regular file target in a direct epic directory"
            )
        job["implementation_evidence_path"] = evidence_relative
    artifact_paths: set[str] = set()
    for index, artifact in enumerate(job["artifacts"]):
        relative = _validate_scoped_path(working_root, artifact["path"], f"artifacts[{index}].path", allow_root=False)
        if relative in artifact_paths:
            raise ContractError(f"duplicate artifact path: {relative}")
        artifact_paths.add(relative)
        target = working_root / relative
        if verify_artifact_hashes and (
            not target.is_file() or _sha256_file(target) != artifact["sha256"]
        ):
            raise ContractError(f"artifact is missing or hash-mismatched: {relative}")
        if not _path_in_scope(relative, job["read_scope"]):
            raise ContractError(f"artifact is outside read_scope: {relative}")
        artifact["path"] = relative
    decision_ids: set[str] = set()
    for index, decision in enumerate(job["decision_refs"]):
        if decision["id"] in decision_ids:
            raise ContractError(f"duplicate decision id: {decision['id']}")
        decision_ids.add(decision["id"])
        relative = _validate_scoped_path(working_root, decision["path"], f"decision_refs[{index}].path", allow_root=False)
        target = working_root / relative
        if not target.is_file() or _sha256_file(target) != decision["sha256"]:
            raise ContractError(f"decision source is missing or hash-mismatched: {relative}")
        if not _path_in_scope(relative, job["read_scope"]):
            raise ContractError(f"decision source is outside read_scope: {relative}")
        decision["path"] = relative
    raw_result_path = Path(job["result_path"])
    if not raw_result_path.is_absolute():
        raise ContractError("result_path must be absolute")
    _validate_runner_path(raw_result_path, repository_root, "worker result path")
    result_path = _canonical_file_parent(raw_result_path, "result_path")
    expected = job_directory(job, repository_root) / "result.json"
    if _normcase(result_path) != _normcase(expected):
        raise ContractError(f"result_path must be {expected}")
    job.update(
        repository_root=str(repository_root),
        working_root=str(working_root),
        scope_root=str(scope_root),
        result_path=str(result_path),
    )
    return job, repository_root, working_root, scope_root


def _git_bytes(working_root: Path, args: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=working_root, capture_output=True, check=False)
    except OSError as exc:
        raise InfrastructureError(f"unable to inspect Git state: {exc}") from exc
    if result.returncode != 0:
        raise InfrastructureError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def _git_ignored_paths(working_root: Path, paths: Sequence[str]) -> list[str]:
    if not paths:
        return []
    payload = b"\0".join(
        path.encode("utf-8", errors="surrogateescape") for path in paths
    ) + b"\0"
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=working_root,
            input=payload,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise InfrastructureError(f"unable to classify ignored paths: {exc}") from exc
    if result.returncode not in {0, 1}:
        raise InfrastructureError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git check-ignore failed"
        )
    return sorted(
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    )


def _is_safe_ignored_test_artifact(relative: str) -> bool:
    """Return whether an ignored path is a known nondeliverable test artifact."""
    parts = PurePosixPath(relative).parts
    if "__pycache__" in parts or ".pytest_cache" in parts:
        return True
    name = parts[-1] if parts else ""
    return name == ".coverage" or name.startswith(".coverage.")


def _index_entries(working_root: Path) -> dict[str, str]:
    entries: dict[str, list[str]] = {}
    for raw in _git_bytes(working_root, ["ls-files", "--stage", "-z"]).split(b"\0"):
        if not raw:
            continue
        decoded = raw.decode("utf-8", errors="surrogateescape")
        try:
            metadata, path = decoded.split("\t", 1)
        except ValueError as exc:
            raise InfrastructureError("unexpected git ls-files output") from exc
        entries.setdefault(path, []).append(metadata)
    return {path: "\0".join(values) for path, values in entries.items()}


def _lexical_within(path: Path, parent: Path) -> bool:
    try:
        if (
            os.path.commonpath(
                (os.path.normcase(str(path)), os.path.normcase(str(parent)))
            )
            == os.path.normcase(str(parent))
        ):
            return True
        if len(path.parts) < len(parent.parts):
            return False
        prefix = Path(*path.parts[: len(parent.parts)])
        if prefix.is_symlink() or parent.is_symlink():
            return False
        if parent.is_dir():
            return prefix.is_dir() and os.path.samefile(prefix, parent)
        return (
            len(path.parts) == len(parent.parts)
            and prefix.name == parent.name
            and prefix.parent.is_dir()
            and parent.parent.is_dir()
            and os.path.samefile(prefix.parent, parent.parent)
        )
    except ValueError:
        return False
    except OSError:
        return False


def capture_snapshot(
    working_root: Path, *, excluded: Sequence[Path] = ()
) -> dict[str, Any]:
    exclusions = [path.absolute() for path in excluded]
    exclusions.append(working_root / ".git")
    index = _index_entries(working_root)
    paths = set(index)
    external_paths: dict[str, Path] = {}
    for current, directories, filenames in os.walk(working_root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in directories:
            candidate = (current_path / name).absolute()
            if any(_lexical_within(candidate, selected) for selected in exclusions):
                continue
            relative = candidate.relative_to(working_root).as_posix()
            if (current_path / name).is_symlink():
                paths.add(relative)
            else:
                paths.add(relative)
                kept.append(name)
        directories[:] = kept
        for name in filenames:
            candidate = (current_path / name).absolute()
            if any(_lexical_within(candidate, selected) for selected in exclusions):
                continue
            relative = candidate.relative_to(working_root).as_posix()
            if name == ".DS_Store" and relative not in index:
                try:
                    if stat.S_ISREG(candidate.lstat().st_mode):
                        continue
                except FileNotFoundError:
                    continue
            paths.add(relative)
    git_common = _git_common_dir(working_root)
    git_config = git_common / "config"
    if git_config.exists() or git_config.is_symlink():
        external_paths[".git/config"] = git_config
    hooks_root = git_common / "hooks"
    if hooks_root.exists() or hooks_root.is_symlink():
        external_paths[".git/hooks"] = hooks_root
        if hooks_root.is_dir() and not hooks_root.is_symlink():
            for current, directories, filenames in os.walk(
                hooks_root, followlinks=False
            ):
                current_path = Path(current)
                for name in directories:
                    candidate = current_path / name
                    relative = candidate.relative_to(hooks_root).as_posix()
                    external_paths[f".git/hooks/{relative}"] = candidate
                for name in filenames:
                    candidate = current_path / name
                    relative = candidate.relative_to(hooks_root).as_posix()
                    external_paths[f".git/hooks/{relative}"] = candidate
    paths.update(external_paths)
    rows: list[dict[str, Any]] = []
    for relative in sorted(paths):
        path = external_paths.get(relative, working_root / relative)
        try:
            info = path.lstat()
        except FileNotFoundError:
            kind, digest, mode = "deleted", None, None
        else:
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
                digest = _sha256_bytes(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
                digest = _sha256_file(path)
            elif stat.S_ISDIR(info.st_mode):
                kind, digest = "directory", None
            else:
                kind, digest = "other", None
        rows.append(
            {"path": relative, "kind": kind, "sha256": digest, "mode": mode, "index": index.get(relative, "")}
        )
    head = _run(["git", "rev-parse", "HEAD"], cwd=working_root, check=True).stdout.strip()
    return {"schema_version": 1, "head": head, "entries": rows}


def snapshot_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    before_rows = {row["path"]: row for row in before.get("entries", [])}
    after_rows = {row["path"]: row for row in after.get("entries", [])}
    return sorted(
        path
        for path in before_rows.keys() | after_rows.keys()
        if before_rows.get(path) != after_rows.get(path)
    )


def _escaping_symlinks(snapshot: Mapping[str, Any], working_root: Path, scopes: Sequence[str]) -> list[str]:
    escaped = []
    for row in snapshot.get("entries", []):
        relative = row.get("path")
        if row.get("kind") != "symlink" or not isinstance(relative, str) or not _path_in_scope(relative, scopes):
            continue
        if not is_within((working_root / relative).resolve(strict=False), working_root):
            escaped.append(relative)
    return sorted(escaped)


def _read_identity(working_root: Path) -> dict[str, str]:
    head = _git_bytes(working_root, ["rev-parse", "HEAD"])
    index = _git_bytes(working_root, ["ls-files", "--stage", "-z"])
    diff = _git_bytes(working_root, ["diff", "--no-ext-diff", "--binary", "HEAD", "--"])
    status = _git_bytes(working_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    return {
        "head": head.decode().strip(),
        "index_sha256": _sha256_bytes(index),
        "worktree_sha256": _sha256_bytes(diff + b"\0" + status),
    }


def process_identity(process: psutil.Process | None = None) -> dict[str, Any]:
    selected = process or psutil.Process()
    return {"pid": selected.pid, "create_time": selected.create_time()}


def identity_state(identity: Any) -> str:
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int) or not isinstance(identity.get("create_time"), (int, float)):
        return "missing"
    try:
        process = psutil.Process(identity["pid"])
        if abs(process.create_time() - float(identity["create_time"])) > 0.01:
            return "dead"
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return "dead"
        return "alive"
    except psutil.NoSuchProcess:
        return "dead"
    except (psutil.AccessDenied, OSError):
        return "unknown"


def _group_state(group: Any) -> str:
    if os.name == "nt":
        return "unsupported"
    if not isinstance(group, dict) or not isinstance(group.get("pgid"), int):
        return "missing"
    try:
        os.killpg(group["pgid"], 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except (PermissionError, OSError):
        return "unknown"


def _terminate_identity(identity: Any, grace: float) -> None:
    if identity_state(identity) != "alive":
        return
    try:
        process = psutil.Process(identity["pid"])
        descendants = process.children(recursive=True)
        for child in descendants:
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([*descendants, process], timeout=grace)
        for selected in alive:
            selected.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return


def _terminate_lifecycle(active: Mapping[str, Any], grace: float) -> None:
    for identity in [*active.get("provider_descendants", []), active.get("provider_process")]:
        _terminate_identity(identity, grace)
    group = active.get("provider_process_group")
    if os.name != "nt" and _group_state(group) == "alive":
        pgid = int(group["pgid"])
        for selected_signal in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, selected_signal)
            except (ProcessLookupError, PermissionError, OSError):
                return
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and _group_state(group) == "alive":
                time.sleep(0.05)


def _active_state(active: Mapping[str, Any]) -> str:
    states = [identity_state(active.get("runner_process")), identity_state(active.get("provider_process"))]
    group = _group_state(active.get("provider_process_group"))
    if "alive" in states or group == "alive":
        return "alive"
    if "unknown" in states or group == "unknown":
        return "unknown"
    return "dead"


def provider_preflight(provider: str, selected: Mapping[str, Any]) -> dict[str, Any]:
    executable = str(selected["executable"])
    binary = shutil.which(executable)
    if binary is None and Path(executable).is_file():
        binary = str(Path(executable).resolve())
    if binary is None:
        raise InfrastructureError(f"{provider} CLI not found: {executable}")
    version = _run([binary, "--version"], check=True)
    lines = (version.stdout or version.stderr).strip().splitlines()
    if not lines:
        raise InfrastructureError(f"{provider} CLI returned no version")
    help_args = [binary, "exec", "--help"] if provider == "codex" else [binary, "--help"]
    help_result = _run(help_args, check=True)
    help_text = (help_result.stdout or "") + (help_result.stderr or "")
    required = (
        ("--output-schema", "--output-last-message", "--ignore-user-config")
        if provider == "codex"
        else ("--print", "--json-schema", "--no-session-persistence", "--permission-mode")
    )
    missing = [flag for flag in required if flag not in help_text]
    if missing:
        raise InfrastructureError(f"{provider} CLI lacks required flags: {', '.join(missing)}")
    if provider == "claude":
        auth = _run([binary, "auth", "status", "--json"], check=False)
        try:
            value = json.loads(auth.stdout)
        except json.JSONDecodeError as exc:
            raise InfrastructureError("Claude auth status was not JSON") from exc
        if auth.returncode != 0 or not isinstance(value, dict) or value.get("loggedIn") is not True:
            raise InfrastructureError("Claude CLI is not authenticated")
    return {"executable": binary, "version": lines[0]}


def build_codex_command(
    executable: str,
    selected: Mapping[str, Any],
    working_root: Path,
    access: str,
    result_schema: Path,
    provider_result: Path,
    codegraph: Mapping[str, Any],
) -> list[str]:
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--cd",
        str(working_root),
    ]
    if codegraph.get("status") == "ready" and access == "read-only":
        command.extend(("--add-dir", str(codegraph["index_path"])))
    command.extend(
        (
            "--model",
            str(selected["model"]),
            "-c",
            f'model_reasoning_effort="{selected["reasoning_effort"]}"',
            "--sandbox",
            access,
            "--output-schema",
            str(result_schema),
            "--output-last-message",
            str(provider_result),
            "--json",
            "-",
        )
    )
    return command


def codex_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Project the canonical result contract onto OpenAI's supported schema subset."""
    projected = json.loads(json.dumps(schema))
    projected.pop("allOf", None)

    def remove_unsupported_array_keyword(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("uniqueItems", None)
            for child in value.values():
                remove_unsupported_array_keyword(child)
        elif isinstance(value, list):
            for child in value:
                remove_unsupported_array_keyword(child)

    remove_unsupported_array_keyword(projected)
    return projected


def claude_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Project the contract onto Claude CLI/API's supported schema subset."""
    projected = json.loads(json.dumps(schema))
    projected.pop("$schema", None)
    # Anthropic rejects top-level allOf. Its conditional status rules (including
    # the untyped array constraints) remain enforced by validate_result locally.
    projected.pop("allOf", None)
    return projected


def _claude_tools(job: Mapping[str, Any], access: str, codegraph: Mapping[str, Any]) -> tuple[str, str, str]:
    tools = ["Read", "Glob", "Grep"]
    allowed = list(tools)
    if access == "workspace-write":
        tools.extend(("Write", "Edit"))
        for scope in job["write_scope"]:
            patterns = ["**"] if scope == "." else [scope, f"{scope.rstrip('/')}/**"]
            for pattern in patterns:
                allowed.extend((f"Write({pattern})", f"Edit({pattern})"))
    commands = list(dict.fromkeys([row["command"] for row in job["required_validations"]] + job.get("allowed_commands", [])))
    if commands:
        tools.append("Bash")
        allowed.extend(f"Bash({command})" for command in commands)
    if codegraph.get("status") == "ready":
        if "Bash" not in tools:
            tools.append("Bash")
        allowed.extend(
            f"Bash({codegraph['executable']} {command}:*)"
            for command in codegraph["query_commands"]
        )
    denied = [
        "Bash(git commit *)",
        "Bash(git push *)",
        "Bash(git reset *)",
        "Bash(git clean *)",
        "Bash(scope:*)",
        "Task",
        "Agent",
        "NotebookEdit",
    ]
    evidence_path = job.get("implementation_evidence_path")
    if isinstance(evidence_path, str):
        denied.extend((f"Write({evidence_path})", f"Edit({evidence_path})"))
    return ",".join(tools), ",".join(allowed), ",".join(denied)


def build_claude_command(
    executable: str,
    selected: Mapping[str, Any],
    job: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    access: str,
    codegraph: Mapping[str, Any],
) -> list[str]:
    tools, allowed, denied = _claude_tools(job, access, codegraph)
    return [
        executable,
        "--print",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-chrome",
        "--no-session-persistence",
        "--model",
        str(selected["model"]),
        "--effort",
        str(selected["reasoning_effort"]),
        "--permission-mode",
        str(selected["permission_mode"]),
        "--tools",
        tools,
        "--allowedTools",
        allowed,
        "--disallowedTools",
        denied,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(claude_output_schema(result_schema), separators=(",", ":")),
    ]


def render_prompt(
    job: Mapping[str, Any], job_path: Path, scope_root: Path, provider: str, codegraph: Mapping[str, Any]
) -> str:
    prompt_path = scope_root / "workers" / ROLE_PROMPTS[str(job["role"])]
    if not prompt_path.is_file():
        raise ContractError(f"missing worker role prompt: {prompt_path}")
    repository_instruction = (
        "Claude safe mode does not auto-load project instructions. Read CLAUDE.md once if it exists, otherwise AGENTS.md once."
        if provider == "claude"
        else "Follow the AGENTS.md instructions supplied by Codex; do not load another checkout's instructions."
    )
    return (
        f"{prompt_path.read_text(encoding='utf-8').rstrip()}\n\n"
        "## Runtime assignment\n\n"
        f"Provider: {provider}\nJob packet: {job_path}\nWorking root: {job['working_root']}\n"
        f"Result path (runner controlled): {job['result_path']}\n"
        + (
            f"Implementation evidence (runner controlled): {job['implementation_evidence_path']}\n"
            if isinstance(job.get("implementation_evidence_path"), str)
            else ""
        )
        + f"\n{repository_instruction}\n\n"
        f"{scope_codegraph.prompt_instructions(codegraph)}\n"
        "Read the job packet. Do not edit the runner-controlled result path. Return one strict JSON object matching schema v2, without Markdown fences or commentary. Report each required validation command exactly once.\n"
    )


def _creation_flags() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}


def _tail(path: Path, lines: int, chars: int) -> str:
    if not path.is_file():
        return ""
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    return text[-chars:]


def execute_provider(
    command: Sequence[str],
    prompt: str,
    *,
    working_root: Path,
    stdout_path: Path,
    stderr_path: Path,
    run_path: Path,
    run: dict[str, Any],
    cancellation_path: Path,
    selected: Mapping[str, Any],
) -> tuple[int, bool, bool]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=working_root,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                **_creation_flags(),
            )
        except OSError as exc:
            raise InfrastructureError(f"unable to launch provider: {exc}") from exc
        ps_process = psutil.Process(process.pid)
        active = run["active_job"]
        active["provider_process"] = process_identity(ps_process)
        active["provider_process_group"] = (
            {"pgid": os.getpgid(process.pid)} if os.name != "nt" else {"supported": False}
        )
        _write_run(run_path, run)
        assert process.stdin is not None
        try:
            process.stdin.write(prompt.encode())
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        start = time.monotonic()
        last_heartbeat = start
        recorded: list[dict[str, Any]] = []
        timed_out = False
        cancelled = False
        while process.poll() is None:
            if cancellation_path.is_file():
                cancelled = True
                try:
                    process.wait(
                        timeout=min(
                            float(selected["poll_interval_seconds"]),
                            float(selected["termination_grace_seconds"]),
                        )
                    )
                except subprocess.TimeoutExpired:
                    _terminate_lifecycle(
                        active, float(selected["termination_grace_seconds"])
                    )
                break
            if time.monotonic() - start >= float(selected["timeout_seconds"]):
                timed_out = True
                _terminate_lifecycle(active, float(selected["termination_grace_seconds"]))
                break
            try:
                descendants = [process_identity(child) for child in ps_process.children(recursive=True)]
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                descendants = []
            known = {(row["pid"], row["create_time"]) for row in recorded}
            for row in descendants:
                if (row["pid"], row["create_time"]) not in known:
                    recorded.append(row)
            active["provider_descendants"] = recorded
            if time.monotonic() - last_heartbeat >= float(selected["heartbeat_interval_seconds"]):
                active["last_heartbeat_at"] = utc_now()
                _write_run(run_path, run)
                last_heartbeat = time.monotonic()
            time.sleep(float(selected["poll_interval_seconds"]))
        cancelled = cancelled or cancellation_path.is_file()
        try:
            return_code = process.wait(timeout=float(selected["termination_grace_seconds"]))
        except subprocess.TimeoutExpired:
            _terminate_lifecycle(active, float(selected["termination_grace_seconds"]))
            return_code = process.wait(timeout=float(selected["termination_grace_seconds"]))
    if not timed_out and not cancelled:
        deadline = time.monotonic() + float(selected["normal_exit_grace_seconds"])
        while time.monotonic() < deadline and (
            _group_state(active.get("provider_process_group")) == "alive"
            or any(identity_state(row) == "alive" for row in active.get("provider_descendants", []))
        ):
            time.sleep(0.05)
        if _group_state(active.get("provider_process_group")) == "alive" or any(
            identity_state(row) == "alive" for row in active.get("provider_descendants", [])
        ):
            _terminate_lifecycle(active, float(selected["termination_grace_seconds"]))
            raise InfrastructureError("provider exited while a descendant remained alive")
    return return_code, timed_out, cancelled


def _provider_result(provider: str, provider_result_path: Path, stdout_path: Path) -> tuple[dict[str, Any], Any, Any]:
    if provider == "codex":
        result = load_json(provider_result_path, "Codex worker result")
        usage: Any = None
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "usage" in event:
                usage = event["usage"]
        return result, usage, None
    envelope = load_json(stdout_path, "Claude worker envelope")
    if envelope.get("is_error") is True or envelope.get("terminal_reason") == "api_error":
        raise InfrastructureError(str(envelope.get("result") or envelope.get("error") or "Claude execution failed"))
    candidate = envelope.get("structured_output", envelope.get("result"))
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ContractError("Claude result is not structured JSON") from exc
    if not isinstance(candidate, dict):
        raise ContractError("Claude envelope has no structured result")
    fallback = envelope.get("fallback_used")
    return candidate, envelope.get("modelUsage"), fallback if isinstance(fallback, bool) else None


def validate_result(result: Mapping[str, Any], job: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    validate_against_schema(result, schema, "worker result")
    if result["job_id"] != job["job_id"]:
        raise ContractError("worker result job_id does not match")
    if result["payload"]["kind"] != job["role"]:
        raise ContractError("worker result payload kind does not match role")
    changed = [normalize_relative_path(value, f"changed_paths[{index}]") for index, value in enumerate(result["changed_paths"])]
    if len(changed) != len(set(changed)):
        raise ContractError("worker result contains duplicate changed paths")
    validations: dict[str, Mapping[str, Any]] = {}
    for row in result["validations"]:
        if row["command"] in validations:
            raise ContractError(f"duplicate validation result: {row['command']}")
        validations[row["command"]] = row
    required = {row["command"] for row in job["required_validations"]}
    allowed = required | set(job.get("allowed_commands", []))
    if set(validations) - allowed:
        raise ContractError(f"undeclared validation results: {sorted(set(validations) - allowed)}")
    if result["status"] == "completed":
        missing = required - set(validations)
        if missing:
            raise ContractError(f"missing required validations: {sorted(missing)}")
        failed = [command for command, row in validations.items() if row["exit_code"] != 0]
        if failed:
            raise ContractError(f"completed result has failed validations: {failed}")
        if any(row["severity"] == "blocking" for row in result["issues"]):
            raise ContractError("completed result has a blocking issue")
    executor_owned = False
    if job["role"] == "implementation":
        _, manifest = _manifest_for_implementation_job(job, Path(job["working_root"]))
        executor_owned = manifest.get("schema_version") == 3
        if executor_owned and result["payload"]["proof_evidence"]:
            raise ContractError("manifest v3 proof_evidence is runner-owned; workers return an empty array")
    if job["role"] == "implementation" and result["status"] == "completed" and not executor_owned:
        proof_ids: set[str] = set()
        for proof in result["payload"]["proof_evidence"]:
            if proof["proof_id"] in proof_ids:
                raise ContractError(f"duplicate proof evidence: {proof['proof_id']}")
            proof_ids.add(proof["proof_id"])
            evidence = _validate_scoped_path(Path(job["working_root"]), proof["evidence_path"], "proof evidence path", allow_root=False)
            evidence_target = Path(job["working_root"]) / evidence
            temporary_root = Path(job["working_root"]) / "tmp_debug"
            if (
                evidence == "tmp_debug"
                or evidence.startswith("tmp_debug/")
                or is_within(evidence_target.resolve(strict=False), temporary_root)
            ):
                raise ContractError("proof evidence may not use temporary tmp_debug paths")
            target = evidence_target
            if not target.is_file() or _sha256_file(target) != proof["evidence_sha256"]:
                raise ContractError(f"proof evidence is missing or stale: {evidence}")
            if (
                proof["exit_code"] != 0
                or proof["passed"] <= 0
                or proof["failed"]
                or proof["errors"]
                or proof["skipped"]
            ):
                raise ContractError(f"proof did not pass cleanly: {proof['proof_id']}")
        required_proofs = set(job["required_proof_ids"])
        if proof_ids != required_proofs:
            raise ContractError(
                "implementation proof IDs do not match the job; "
                f"required={sorted(required_proofs)}, actual={sorted(proof_ids)}"
            )
    if (
        job["role"] == "implementation"
        and result["status"] != "completed"
        and not executor_owned
        and changed
    ):
        raise ContractError(
            "non-completed implementation result must not leave changed paths"
        )
    if job["role"] == "refinement":
        authored: list[str] = []
        for index, value in enumerate(result["payload"]["authored_artifacts"]):
            relative = normalize_relative_path(
                value, f"payload.authored_artifacts[{index}]"
            )
            if relative in authored:
                raise ContractError(f"duplicate authored artifact: {relative}")
            if not _path_in_scope(relative, job["write_scope"]):
                raise ContractError(f"authored artifact is outside write_scope: {relative}")
            authored.append(relative)
        if not set(authored) <= set(result["changed_paths"]):
            raise ContractError("authored artifacts must be reported as changed paths")
        job_decisions = {row["id"] for row in job["decision_refs"]}
        reported_decisions = set(result["payload"]["decision_refs"])
        if not reported_decisions <= job_decisions:
            raise ContractError("refinement result cites decisions absent from the job")


def _validate_decision_refs_current(job: Mapping[str, Any]) -> None:
    for decision in job["decision_refs"]:
        target = Path(job["working_root"]) / decision["path"]
        if not target.is_file() or _sha256_file(target) != decision["sha256"]:
            raise ContractError(
                f"decision source changed while the job ran: {decision['path']}"
            )


def _snapshot_entries(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["path"]): row
        for row in snapshot.get("entries", [])
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }


def _evidence_state(snapshot: Mapping[str, Any], relative: str) -> dict[str, Any]:
    row = _snapshot_entries(snapshot).get(relative)
    if row is None or row.get("kind") == "deleted":
        return {"state": "deleted", "sha256": None, "mode": None}
    kind = str(row.get("kind"))
    if kind == "symlink":
        state = "symlink"
    else:
        state = "present"
    return {
        "state": state,
        "kind": kind,
        "sha256": row.get("sha256"),
        "mode": row.get("mode"),
    }


def _current_path_state(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"state": "deleted", "sha256": None, "mode": None}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return {
            "state": "symlink",
            "kind": "symlink",
            "sha256": _sha256_bytes(
                os.readlink(path).encode("utf-8", errors="surrogateescape")
            ),
            "mode": mode,
        }
    if stat.S_ISREG(info.st_mode):
        return {
            "state": "present",
            "kind": "file",
            "sha256": _sha256_file(path),
            "mode": mode,
        }
    if stat.S_ISDIR(info.st_mode):
        kind = "directory"
    else:
        kind = "other"
    return {"state": "present", "kind": kind, "sha256": None, "mode": mode}


def _manifest_for_implementation_job(
    job: Mapping[str, Any], working_root: Path
) -> tuple[Path, dict[str, Any]]:
    evidence_relative = str(job["implementation_evidence_path"])
    manifest_relative = str(
        Path(evidence_relative).parent / "delivery-manifest.yaml"
    )
    matches = [
        row
        for row in job["artifacts"]
        if isinstance(row, Mapping) and row.get("path") == manifest_relative
    ]
    if len(matches) != 1:
        raise ContractError(
            "implementation job must hash-bind the evidence target's delivery-manifest.yaml"
        )
    manifest_path = working_root / manifest_relative
    if not manifest_path.is_file() or _sha256_file(manifest_path) != matches[0].get(
        "sha256"
    ):
        raise ContractError("delivery manifest changed while the implementation job ran")
    manifest = load_yaml(manifest_path, "delivery manifest")
    if manifest.get("epic_id") != job.get("epic_id"):
        raise ContractError("delivery manifest epic_id does not match the job")
    return manifest_path, manifest


def _manifest_proof_owners(
    manifest: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, str]]:
    stories = manifest.get("stories")
    proofs = manifest.get("proofs")
    if not isinstance(stories, list) or not all(isinstance(row, Mapping) for row in stories):
        raise ContractError("delivery manifest stories must be a list of mappings")
    if not isinstance(proofs, list) or not all(isinstance(row, Mapping) for row in proofs):
        raise ContractError("delivery manifest proofs must be a list of mappings")
    proof_by_id: dict[str, Mapping[str, Any]] = {}
    for row in proofs:
        proof_id = row.get("id")
        if not isinstance(proof_id, str) or not proof_id or proof_id in proof_by_id:
            raise ContractError("delivery manifest has invalid or duplicate proof IDs")
        proof_by_id[proof_id] = row
    owner_by_proof: dict[str, str] = {}
    story_ids: set[str] = set()
    for row in stories:
        story_id = row.get("id")
        proof_ids = row.get("proof_ids")
        if (
            not isinstance(story_id, str)
            or not story_id
            or story_id in story_ids
            or not isinstance(proof_ids, list)
        ):
            raise ContractError("delivery manifest has invalid or duplicate story IDs")
        story_ids.add(story_id)
        for proof_id in proof_ids:
            if proof_id not in proof_by_id or proof_id in owner_by_proof:
                raise ContractError(
                    "each delivery proof must exist and have exactly one story owner"
                )
            owner_by_proof[str(proof_id)] = story_id
    if set(owner_by_proof) != set(proof_by_id):
        raise ContractError("every delivery proof must have exactly one story owner")
    return list(stories), proof_by_id, owner_by_proof


def _baseline_identity(working_root: Path, head: str) -> dict[str, str]:
    tree = _run(
        ["git", "rev-parse", f"{head}^{{tree}}"], cwd=working_root, check=True
    ).stdout.strip()
    return {"head": head, "tree": tree}


def _attributed_delta(validated_jobs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    first: dict[str, Mapping[str, Any]] = {}
    latest: dict[str, Mapping[str, Any]] = {}
    for job in validated_jobs:
        rows = job.get("changed_paths")
        if not isinstance(rows, list):
            raise ContractError("implementation evidence job changed_paths must be a list")
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                raise ContractError("implementation evidence has an invalid changed path")
            path = normalize_relative_path(
                str(row["path"]), "implementation evidence changed path"
            )
            before = row.get("before")
            after = row.get("after")
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                raise ContractError("implementation evidence path states must be mappings")
            _validate_evidence_state(before)
            _validate_evidence_state(after)
            first.setdefault(path, dict(before))
            latest[path] = dict(after)
    return [
        {"path": path, **dict(latest[path])}
        for path in sorted(latest)
        if first[path] != latest[path]
    ]


def _validate_evidence_state(value: Mapping[str, Any]) -> None:
    state = value.get("state")
    if state not in {"present", "deleted", "symlink"}:
        raise ContractError("implementation evidence path state is invalid")
    mode = value.get("mode")
    if mode is not None and (not isinstance(mode, int) or isinstance(mode, bool) or mode < 0):
        raise ContractError("implementation evidence path mode is invalid")
    sha256 = value.get("sha256")
    if state == "deleted":
        if sha256 is not None:
            raise ContractError("deleted implementation evidence path cannot have a hash")
        return
    kind = value.get("kind")
    if state == "symlink" and kind != "symlink":
        raise ContractError("symlink implementation evidence state has the wrong kind")
    if state == "present" and kind not in {"file", "directory", "other"}:
        raise ContractError("present implementation evidence state has an invalid kind")
    if kind in {"file", "symlink"}:
        if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
            raise ContractError("implementation evidence content hash is invalid")
    elif sha256 is not None:
        raise ContractError("non-file implementation evidence state cannot have a hash")


def _attribution_hash(evidence: Mapping[str, Any]) -> str:
    return _structured_sha256(
        {
            "baseline": evidence.get("baseline"),
            "validated_jobs": evidence.get("validated_jobs"),
            "attributed_delta": evidence.get("attributed_delta"),
            "stories": evidence.get("stories"),
            **({"proof_checkpoint": evidence["proof_checkpoint"]} if "proof_checkpoint" in evidence else {}),
            "validated_workspace_sha256": evidence.get(
                "validated_workspace_sha256"
            ),
        }
    )


def _workspace_snapshot_sha256(
    snapshot: Mapping[str, Any], evidence_relative: str
) -> str:
    epic_relative = Path(evidence_relative).parent.as_posix()

    def included(relative: str) -> bool:
        if _is_safe_ignored_test_artifact(relative):
            return False
        if relative in {evidence_relative, f"{epic_relative}/reviews/proofs"}:
            return False
        if relative in {"tmp_debug", ".codegraph"}:
            return False
        if relative.startswith(("tmp_debug/", ".codegraph/")):
            return False
        if relative in {
            f"{epic_relative}/audit-findings.yaml",
            f"{epic_relative}/epic_audit.md",
        }:
            return False
        return not relative.startswith((f"{epic_relative}/reviews/audit-", f"{epic_relative}/reviews/proofs/"))

    entries = [
        dict(row)
        for row in snapshot.get("entries", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("path"), str)
        and included(str(row["path"]))
    ]
    entries.sort(key=lambda row: str(row["path"]))
    return _structured_sha256({"head": snapshot.get("head"), "entries": entries})


def _validate_implementation_workspace_continuity(
    job: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> None:
    if job.get("role") != "implementation":
        return
    evidence_relative = str(job["implementation_evidence_path"])
    evidence_path = Path(str(job["working_root"])) / evidence_relative
    if not evidence_path.is_file():
        return
    evidence = load_yaml(evidence_path, "implementation evidence")
    jobs = evidence.get("validated_jobs")
    if not isinstance(jobs, list):
        raise ContractError("implementation evidence validated_jobs must be a list")
    if not jobs:
        return
    expected = evidence.get("validated_workspace_sha256")
    actual = _workspace_snapshot_sha256(snapshot, evidence_relative)
    if expected != actual:
        raise ContractError(
            "workspace drifted after the last runner-validated implementation job"
        )


def _new_implementation_evidence(
    job: Mapping[str, Any], working_root: Path, before: Mapping[str, Any]
) -> dict[str, Any]:
    head = str(before.get("head", ""))
    if not head:
        raise ContractError("implementation snapshot is missing its baseline HEAD")
    return {
        "schema_version": IMPLEMENTATION_EVIDENCE_VERSION,
        "epic_id": job["epic_id"],
        "baseline": _baseline_identity(working_root, head),
        "validated_jobs": [],
        "attributed_delta": [],
        "stories": [],
        "validated_workspace_sha256": "",
        "attribution_sha256": "",
    }


def _load_implementation_evidence_for_promotion(
    path: Path,
    job: Mapping[str, Any],
    working_root: Path,
    before: Mapping[str, Any],
) -> dict[str, Any]:
    if path.is_file():
        evidence = load_yaml(path, "implementation evidence")
        if evidence.get("schema_version") != IMPLEMENTATION_EVIDENCE_VERSION:
            raise ContractError("implementation evidence must use schema_version 2")
        jobs = evidence.get("validated_jobs")
        if not isinstance(jobs, list):
            raise ContractError("implementation evidence validated_jobs must be a list")
        if not jobs and evidence.get("epic_id") in {"EPIC-ID", job.get("epic_id")} and (
            not isinstance(evidence.get("baseline"), Mapping)
            or evidence.get("baseline", {}).get("head") == "pending"
        ):
            evidence = _new_implementation_evidence(job, working_root, before)
        elif evidence.get("epic_id") != job.get("epic_id"):
            raise ContractError("implementation evidence epic_id does not match the job")
    else:
        evidence = _new_implementation_evidence(job, working_root, before)
    jobs = evidence.get("validated_jobs")
    if not isinstance(jobs, list):
        raise ContractError("implementation evidence validated_jobs must be a list")
    baseline = evidence.get("baseline")
    if not isinstance(baseline, Mapping) or baseline.get("head") != before.get("head"):
        raise ContractError("implementation HEAD differs from the durable evidence baseline")
    if jobs:
        if evidence.get("attributed_delta") != _attributed_delta(jobs):
            raise ContractError("implementation evidence attributed_delta is stale")
        if evidence.get("attribution_sha256") != _attribution_hash(evidence):
            raise ContractError("implementation evidence attribution_sha256 is stale")
    return evidence


def promote_implementation_evidence(
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    actual_paths: Sequence[str],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    result_sha256: str,
    checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically promote runner-observed implementation changes into durable evidence."""
    if job.get("role") != "implementation" or job.get("phase") == "delivery_summary":
        raise ContractError("this job phase does not publish implementation evidence")
    if result.get("status") != "completed":
        raise ContractError("only a completed implementation job may publish evidence")
    working_root = Path(str(job["working_root"]))
    _, manifest = _manifest_for_implementation_job(job, working_root)
    manifest_stories, proof_by_id, owner_by_proof = _manifest_proof_owners(manifest)
    evidence_path = working_root / str(job["implementation_evidence_path"])
    evidence = _load_implementation_evidence_for_promotion(
        evidence_path, job, working_root, before
    )
    proof_rows: list[dict[str, Any]] = []
    for proof in (checkpoint["proofs"] if checkpoint is not None else result["payload"]["proof_evidence"]):
        proof_id = str(proof["proof_id"])
        planned = proof_by_id.get(proof_id)
        story_id = owner_by_proof.get(proof_id)
        if planned is None or story_id is None:
            raise ContractError(f"implementation result cites unknown proof: {proof_id}")
        if isinstance(planned.get("command"), str) and proof["command"] != planned["command"]:
            raise ContractError(f"implementation proof command differs from plan: {proof_id}")
        proof_rows.append(
            {
                "proof_id": proof_id,
                "story_id": story_id,
                "command": proof["command"],
                "outcome": proof.get("outcome", "pass"),
                "exit_code": proof["exit_code"],
                "passed": proof["passed"],
                "failed": proof["failed"],
                "errors": proof["errors"],
                "skipped": proof["skipped"],
                "summary": (
                    proof["summary"] if checkpoint is not None else
                    f"{proof['passed']} passed, {proof['failed']} failed, "
                    f"{proof['errors']} errors, {proof['skipped']} skipped"
                ),
                "evidence_hashes": proof["evidence_hashes"] if checkpoint is not None else {
                    proof["evidence_path"]: proof["evidence_sha256"]
                },
                **({"context": proof["context"]} if checkpoint is not None else {}),
                "source_job_id": job["job_id"],
                "source_result_sha256": result_sha256,
            }
        )
    job_record = {
        "job_id": job["job_id"],
        "phase": job["phase"],
        "result_sha256": result_sha256,
        "proof_ids": sorted(row["proof_id"] for row in proof_rows),
        "changed_paths": [
            {
                "path": path,
                "before": _evidence_state(before, path),
                "after": _evidence_state(after, path),
            }
            for path in sorted(actual_paths)
        ],
    }
    validated_jobs = evidence["validated_jobs"]
    existing = [
        row
        for row in validated_jobs
        if isinstance(row, Mapping) and row.get("job_id") == job["job_id"]
    ]
    if existing:
        if len(existing) != 1 or existing[0] != job_record:
            raise ContractError(
                f"implementation evidence conflicts with completed job {job['job_id']}"
            )
        return evidence
    validated_jobs.append(job_record)

    prior_stories = {
        row.get("story_id"): row
        for row in evidence.get("stories", [])
        if isinstance(row, Mapping) and isinstance(row.get("story_id"), str)
    }
    promoted_by_id = {row["proof_id"]: row for row in proof_rows}
    stories: list[dict[str, Any]] = []
    for manifest_story in manifest_stories:
        story_id = str(manifest_story["id"])
        planned_ids = [str(value) for value in manifest_story.get("proof_ids", [])]
        external_blocked_ids = [
            proof_id
            for proof_id in planned_ids
            if proof_by_id[proof_id].get("classification") == "external_blocked"
        ]
        executable_ids = [
            proof_id for proof_id in planned_ids if proof_id not in external_blocked_ids
        ]
        prior = prior_stories.get(story_id, {})
        prior_proofs = {
            row.get("proof_id"): dict(row)
            for row in prior.get("proofs", [])
            if isinstance(row, Mapping) and isinstance(row.get("proof_id"), str)
        }
        prior_proofs.update(
            {
                proof_id: promoted_by_id[proof_id]
                for proof_id in planned_ids
                if proof_id in promoted_by_id
            }
        )
        ordered = [
            prior_proofs[proof_id]
            for proof_id in executable_ids
            if proof_id in prior_proofs
        ]
        stories.append(
            {
                "story_id": story_id,
                "status": "verified" if len(ordered) == len(executable_ids) and all(row.get("outcome") == "pass" for row in ordered) else "pending",
                "proofs": ordered,
                "external_blocked_proof_ids": external_blocked_ids,
            }
        )
    evidence["stories"] = stories
    if checkpoint is not None:
        evidence["proof_checkpoint"] = {key: checkpoint[key] for key in ("path", "sha256", "status", "duration_seconds")}
    evidence["attributed_delta"] = _attributed_delta(validated_jobs)
    evidence["validated_workspace_sha256"] = _workspace_snapshot_sha256(
        after, str(job["implementation_evidence_path"])
    )
    evidence["attribution_sha256"] = _attribution_hash(evidence)
    atomic_write_yaml(evidence_path, evidence)
    return evidence


def _promotion_is_current(
    evidence_path: Path, job_id: str, result_sha256: str
) -> bool:
    if not evidence_path.is_file():
        return False
    try:
        evidence = load_yaml(evidence_path, "implementation evidence")
    except WorkerError:
        return False
    return any(
        isinstance(row, Mapping)
        and row.get("job_id") == job_id
        and row.get("result_sha256") == result_sha256
        for row in evidence.get("validated_jobs", [])
    )


def _git_changed_paths(working_root: Path) -> set[str]:
    raw = _git_bytes(
        working_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    chunks = raw.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if not chunk:
            continue
        if len(chunk) < 4:
            raise ContractError("unexpected git status record")
        status = chunk[:2].decode("ascii", errors="replace")
        paths.add(chunk[3:].decode("utf-8", errors="surrogateescape"))
        if "R" in status or "C" in status:
            if index >= len(chunks):
                raise ContractError("truncated git rename status record")
            paths.add(chunks[index].decode("utf-8", errors="surrogateescape"))
            index += 1
    return paths


def verify_implementation_attribution(epic_dir: Path, working_root: Path) -> list[str]:
    """Return deterministic evidence-attribution errors for audit preparation."""
    errors: list[str] = []
    try:
        root = working_root.resolve(strict=True)
        selected_epic = epic_dir.resolve(strict=True)
        evidence_path = selected_epic / "implementation-evidence.yaml"
        manifest = load_yaml(selected_epic / "delivery-manifest.yaml", "delivery manifest")
        evidence = load_yaml(evidence_path, "implementation evidence")
        if evidence.get("schema_version") != IMPLEMENTATION_EVIDENCE_VERSION:
            return ["implementation evidence schema_version must be 2"]
        if evidence.get("epic_id") != manifest.get("epic_id"):
            errors.append("implementation evidence epic_id differs from the delivery manifest")
        manifest_stories, proof_by_id, owner_by_proof = _manifest_proof_owners(manifest)
        validated_jobs = evidence.get("validated_jobs")
        if not isinstance(validated_jobs, list):
            return errors + ["implementation evidence validated_jobs must be a list"]
        job_ids = [row.get("job_id") for row in validated_jobs if isinstance(row, Mapping)]
        if len(job_ids) != len(validated_jobs) or len(set(job_ids)) != len(job_ids):
            errors.append("implementation evidence has invalid or duplicate job IDs")
        for row in validated_jobs:
            if isinstance(row, Mapping) and (
                not isinstance(row.get("result_sha256"), str)
                or not SHA256_PATTERN.fullmatch(str(row.get("result_sha256")))
            ):
                errors.append(
                    f"implementation evidence job has invalid result hash: {row.get('job_id')}"
                )
        if any(row.get("phase") == "delivery_summary" for row in validated_jobs if isinstance(row, Mapping)):
            errors.append("delivery_summary must not be promoted as audited implementation evidence")
        derived_delta = _attributed_delta(validated_jobs)
        if evidence.get("attributed_delta") != derived_delta:
            errors.append("implementation attributed_delta does not match validated jobs")
        if evidence.get("attribution_sha256") != _attribution_hash(evidence):
            errors.append("implementation attribution_sha256 is stale")
        current_snapshot = capture_snapshot(root)
        if evidence.get("validated_workspace_sha256") != _workspace_snapshot_sha256(
            current_snapshot,
            evidence_path.relative_to(root).as_posix(),
        ):
            errors.append(
                "workspace differs from the last runner-validated implementation snapshot"
            )
        baseline = evidence.get("baseline")
        current_head = _run(["git", "rev-parse", "HEAD"], cwd=root, check=True).stdout.strip()
        if not isinstance(baseline, Mapping) or baseline.get("head") != current_head:
            errors.append("implementation evidence baseline HEAD differs from the workspace")
        elif baseline.get("tree") != _baseline_identity(root, current_head)["tree"]:
            errors.append("implementation evidence baseline tree differs from the workspace")
        for row in derived_delta:
            path = normalize_relative_path(row.get("path"), "attributed_delta.path")
            expected = {key: value for key, value in row.items() if key != "path"}
            if _current_path_state(root / path) != expected:
                errors.append(f"implementation attributed path is stale: {path}")
        epic_relative = selected_epic.relative_to(root).as_posix()
        excluded = {
            str(evidence_path.relative_to(root).as_posix()),
            f"{epic_relative}/audit-findings.yaml",
            f"{epic_relative}/epic_audit.md",
        }
        current_git_paths = {
            path
            for path in _git_changed_paths(root)
            if path not in excluded
            and path != "tmp_debug"
            and not path.startswith("tmp_debug/")
            and not path.startswith((f"{epic_relative}/reviews/audit-", f"{epic_relative}/reviews/proofs/"))
        }
        expected_git_paths = {
            str(row["path"])
            for row in derived_delta
            if row.get("kind") != "directory"
        }
        if current_git_paths != expected_git_paths:
            errors.append(
                "current Git delta differs from runner-attributed paths: "
                f"current={sorted(current_git_paths)}, attributed={sorted(expected_git_paths)}"
            )
        jobs_by_id = {
            str(row.get("job_id")): row
            for row in validated_jobs
            if isinstance(row, Mapping)
        }
        stories_by_id = {
            row.get("story_id"): row
            for row in evidence.get("stories", [])
            if isinstance(row, Mapping)
        }
        for manifest_story in manifest_stories:
            story_id = str(manifest_story["id"])
            planned_ids = [str(value) for value in manifest_story.get("proof_ids", [])]
            external_blocked_ids = [
                proof_id
                for proof_id in planned_ids
                if proof_by_id[proof_id].get("classification") == "external_blocked"
            ]
            executable_ids = [
                proof_id for proof_id in planned_ids if proof_id not in external_blocked_ids
            ]
            story = stories_by_id.get(story_id)
            if not isinstance(story, Mapping) or story.get("status") != "verified":
                errors.append(f"implementation evidence story is not verified: {story_id}")
                continue
            rows = story.get("proofs")
            if not isinstance(rows, list):
                errors.append(f"implementation evidence story proofs are invalid: {story_id}")
                continue
            by_id = {
                row.get("proof_id"): row
                for row in rows
                if isinstance(row, Mapping)
            }
            if story.get("external_blocked_proof_ids", []) != external_blocked_ids:
                errors.append(
                    f"implementation external-blocked proof set differs from the manifest: {story_id}"
                )
            if set(by_id) != set(executable_ids):
                errors.append(f"implementation proof set differs from the manifest: {story_id}")
                continue
            for proof_id in executable_ids:
                proof = by_id[proof_id]
                planned = proof_by_id[proof_id]
                source_job = jobs_by_id.get(str(proof.get("source_job_id")))
                if (
                    source_job is None
                    or proof_id not in source_job.get("proof_ids", [])
                    or source_job.get("result_sha256") != proof.get("source_result_sha256")
                ):
                    errors.append(f"implementation proof provenance is invalid: {proof_id}")
                if owner_by_proof[proof_id] != story_id:
                    errors.append(f"implementation proof has the wrong story owner: {proof_id}")
                if isinstance(planned.get("command"), str) and proof.get("command") != planned.get("command"):
                    errors.append(f"implementation proof command differs from the plan: {proof_id}")
                if manifest.get("schema_version") == 3:
                    try:
                        identity, _ = scope_proofs.context(planned, root, selected_epic, scope_proofs.policy())
                        if not scope_proofs.valid_record(proof, root, identity):
                            errors.append(f"implementation proof is stale or failed at final workspace: {proof_id}")
                    except (ValueError, OSError) as exc:
                        errors.append(f"invalid execution context for {proof_id}: {exc}")
                hashes = proof.get("evidence_hashes")
                if not isinstance(hashes, Mapping) or not hashes:
                    errors.append(f"implementation proof evidence hashes are missing: {proof_id}")
                    continue
                for relative, expected_hash in hashes.items():
                    try:
                        normalized = normalize_relative_path(relative, "proof evidence path")
                        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
                            raise ContractError("invalid proof evidence sha256")
                        if _sha256_file(root / normalized) != expected_hash:
                            raise ContractError("proof evidence hash mismatch")
                    except WorkerError as exc:
                        errors.append(f"implementation proof evidence is stale: {proof_id}: {exc}")
    except (WorkerError, OSError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def _finalize_result(
    run_path: Path,
    run: dict[str, Any],
    active: Mapping[str, Any],
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    actual_paths: Sequence[str],
    model_usage: Any,
    provider_fallback: Any,
    before_snapshot: Mapping[str, Any] | None = None,
    after_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result_path = Path(job["result_path"])
    atomic_write_json(result_path, result)
    result_sha256 = _sha256_file(result_path)
    try:
        if (
            job["role"] == "implementation"
            and job["phase"] != "delivery_summary"
            and result["status"] == "completed"
        ):
            if before_snapshot is None or after_snapshot is None:
                raise ContractError(
                    "implementation evidence promotion requires both snapshots"
                )
            _, manifest = _manifest_for_implementation_job(job, Path(job["working_root"]))
            checkpoint = None
            if manifest.get("schema_version") == 3:
                selected = [row for row in manifest["proofs"] if row["id"] in job["required_proof_ids"]]
                if {row["id"] for row in selected} != set(job["required_proof_ids"]):
                    raise ContractError("unknown required proof ID")
                checkpoint = scope_proofs.execute(scope_proofs.executable(selected), Path(job["working_root"]),
                    (Path(job["working_root"]) / job["implementation_evidence_path"]).parent,
                    scope_proofs.policy(Path(job["scope_root"])), cancellation=Path(active["cancellation_path"]) if active.get("cancellation_path") else None)
                if any("proof changed its source" in row["summary"] for row in checkpoint["proofs"]):
                    raise ContractError("proof mutated deliverable source; changes retained but cannot be attributed to the author")
                after_snapshot = capture_snapshot(Path(job["working_root"]))
            promote_implementation_evidence(
                job,
                result,
                actual_paths,
                before_snapshot,
                after_snapshot,
                result_sha256,
                checkpoint,
            )
    except (WorkerError, ValueError, OSError) as exc:
        result_path.unlink(missing_ok=True)
        raise ContractError(str(exc)) from exc
    row = {
        "job_id": job["job_id"],
        "phase": job["phase"],
        "provider": active["provider"],
        "requested_model": active["requested_model"],
        "reasoning_effort": active["reasoning_effort"],
        "worker_profile": active["worker_profile"],
        "status": result["status"],
        "summary": result["summary"],
        "result_path": str(result_path),
        "result_sha256": result_sha256,
        "changed_paths": sorted(actual_paths),
        "started_at": active["started_at"],
        "completed_at": utc_now(),
    }
    if job["role"] == "implementation" and job["phase"] != "delivery_summary" and result["status"] == "completed" and checkpoint is not None:
        row["checkpoint"] = {key: checkpoint[key] for key in ("path", "sha256", "status", "duration_seconds")}
        if checkpoint["status"] != "pass":
            row["status"] = "verification_failed"
    after_snapshot_path = active.get("after_snapshot")
    if isinstance(after_snapshot_path, str) and Path(after_snapshot_path).is_file():
        row["after_snapshot_sha256"] = _sha256_file(Path(after_snapshot_path))
    if model_usage is not None:
        row["modelUsage"] = model_usage
    if isinstance(provider_fallback, bool):
        row["provider_reported_fallback"] = provider_fallback
    run["completed_jobs"].append(row)
    run["active_job"] = None
    _write_run(run_path, run)
    return row


def _interrupt(
    run_path: Path, run: dict[str, Any], reason: str, changed_paths: Sequence[str]
) -> dict[str, Any]:
    active = run.get("active_job")
    if not isinstance(active, dict):
        raise ContractError("worker run has no active job to interrupt")
    row = {
        "job_id": active["job_id"],
        "phase": active["phase"],
        "provider": active["provider"],
        "requested_model": active["requested_model"],
        "reasoning_effort": active["reasoning_effort"],
        "worker_profile": active["worker_profile"],
        "status": "interrupted",
        "reason": reason,
        "changed_paths": sorted(changed_paths),
        "started_at": active["started_at"],
        "completed_at": utc_now(),
    }
    run["completed_jobs"].append(row)
    run["active_job"] = None
    _write_run(run_path, run)
    return row


def _snapshot_paths(active: Mapping[str, Any]) -> tuple[Path | None, Path | None]:
    before = active.get("before_snapshot")
    after = active.get("after_snapshot")
    return (Path(before) if isinstance(before, str) else None, Path(after) if isinstance(after, str) else None)


def _snapshot_exclusions(
    active: Mapping[str, Any], run_path: Path, working_root: Path
) -> list[Path]:
    return [
        Path(str(active["stdout_path"])).parent,
        run_path,
        run_state_lock_path(run_path),
        mutation_lock_path(working_root),
    ]


def _snapshots_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return left.get("head") == right.get("head") and not snapshot_delta(left, right)


def _validate_attribution(
    job: Mapping[str, Any], result: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[str]:
    if before.get("head") != after.get("head"):
        raise ContractError("worker changed Git HEAD")
    actual = snapshot_delta(before, after)
    sensitive = [
        path
        for path in actual
        if path == ".git/config"
        or path == ".git/hooks"
        or path.startswith(".git/hooks/")
    ]
    if sensitive:
        raise ContractError(f"worker changed protected Git configuration: {sensitive}")
    evidence_path = job.get("implementation_evidence_path")
    if isinstance(evidence_path, str) and evidence_path in actual:
        raise ContractError(
            f"worker changed runner-owned implementation evidence: {evidence_path}"
        )
    if isinstance(evidence_path, str):
        proof_prefix = f"{Path(evidence_path).parent.as_posix()}/reviews/proofs"
        if any(path == proof_prefix or path.startswith(proof_prefix + "/") for path in actual):
            raise ContractError("worker changed runner-owned proof evidence")
    if job.get("role") == "implementation":
        ignored = _git_ignored_paths(Path(str(job["working_root"])), actual)
        unsafe_ignored = [path for path in ignored if not _is_safe_ignored_test_artifact(path)]
        if unsafe_ignored:
            raise ContractError(
                "implementation worker changed ignored paths that cannot be delivered: "
                f"{unsafe_ignored}"
            )
        safe_ignored = {path for path in ignored if _is_safe_ignored_test_artifact(path)}
        actual = [path for path in actual if path not in safe_ignored]
    else:
        safe_ignored = set()
    declared = sorted(path for path in result["changed_paths"] if path not in safe_ignored)
    if declared != actual:
        raise ContractError(f"declared changed paths do not match actual paths; declared={declared}, actual={actual}")
    outside = [path for path in actual if not _path_in_scope(path, job["write_scope"])]
    if outside:
        raise ContractError(f"worker wrote outside write_scope: {outside}")
    escaped = _escaping_symlinks(after, Path(job["working_root"]), job["write_scope"])
    if escaped:
        raise ContractError(f"worker created escaping symlinks: {escaped}")
    return actual


def _ensure_no_cross_run_active(repository_root: Path, working_root: Path, current: Path) -> None:
    for candidate in runtime_root(repository_root).glob("*/*/run.yaml"):
        if _normcase(candidate) == _normcase(current):
            continue
        try:
            other = load_yaml(candidate, "worker run")
        except WorkerError:
            continue
        active = other.get("active_job")
        if str(other.get("working_root")) == str(working_root) and isinstance(active, dict) and active.get("access") == "workspace-write":
            raise ActiveWorkerError(f"another run has unfinished mutation job {active.get('job_id')}: {candidate}")


def run_worker(args: argparse.Namespace) -> int:
    job_path = args.job.absolute()
    job, repository_root, working_root, scope_root = load_job(job_path)
    if args.role != job["role"] or _normcase(_canonical_directory(args.cwd, "cwd")) != _normcase(working_root):
        raise ContractError("CLI role/cwd does not match the worker job")
    result_path = _canonical_file_parent(args.result, "result")
    if _normcase(result_path) != _normcase(Path(job["result_path"])):
        raise ContractError("--result does not match job.result_path")
    expected_access = "workspace-write" if job["write_scope"] else "read-only"
    if args.access != expected_access:
        raise ContractError(f"job requires --access {expected_access}")
    if job["role"] == "implementation":
        _, manifest = _manifest_for_implementation_job(job, working_root)
        if manifest.get("schema_version") == 3:
            if job["phase"] in {"epic_verify", "delivery_summary"}:
                raise ContractError("manifest v3 uses deterministic proof and summary commands")
            if job["phase"] == "story":
                groups = scope_proofs.groups(manifest, scope_proofs.policy(scope_root)["maximum_stories_per_group"])
                if job.get("story_ids") not in groups:
                    raise ContractError("story_ids must match one dependency-connected story group")
                expected = {proof_id for row in manifest["stories"] if row["id"] in job["story_ids"] for proof_id in row["proof_ids"]}
                if set(job["required_proof_ids"]) != expected:
                    raise ContractError("story group must include the exact union of its proof IDs")
                evidence_path = working_root / job["implementation_evidence_path"]
                evidence = load_yaml(evidence_path, "implementation evidence") if evidence_path.is_file() else {}
                verified = {row["story_id"] for row in evidence.get("stories", []) if row.get("status") == "verified"}
                pending = next((group for group in groups if not set(group) <= verified), None)
                if job["story_ids"] != pending:
                    raise ContractError("story group is not the first incomplete dependency-ready group")
            if job["phase"] == "debugging":
                evidence = load_yaml(working_root / job["implementation_evidence_path"], "implementation evidence")
                checkpoint = evidence.get("proof_checkpoint", {})
                receipt_path = working_root / checkpoint.get("path", "")
                if checkpoint.get("status") != "fail" or not receipt_path.is_file() or _sha256_file(receipt_path) != checkpoint.get("sha256"):
                    raise ContractError("debugging requires a current failed runner proof checkpoint")
                receipt = load_json(receipt_path, "proof checkpoint")
                if set(job["required_proof_ids"]) != {row["proof_id"] for row in receipt["proofs"]}:
                    raise ContractError("debugging must rerun the complete failed checkpoint proof set")
    policy = load_policy(scope_root)
    selected = _provider_policy(policy, job["role"], job["phase"], args.provider, args.worker_profile)
    provider = provider_preflight(args.provider, selected)
    run_path = run_directory(job, repository_root) / "run.yaml"
    _validate_runner_path(run_path, repository_root, "worker run path")
    job_dir = job_directory(job, repository_root)
    _validate_runner_path(job_dir, repository_root, "worker job directory")
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "provider.stdout"
    stderr_path = job_dir / "provider.stderr"
    provider_result_path = job_dir / "provider-result.json"
    codex_schema_path = job_dir / "codex-output-schema.json"
    cancellation_path = job_dir / "cancel.yaml"
    before_path = job_dir / "before-snapshot.json"
    after_path = job_dir / "after-snapshot.json"
    recovery_snapshot_path = job_dir / "recovery-current-snapshot.json"
    stale_outputs = [
        result_path,
        stdout_path,
        stderr_path,
        provider_result_path,
        codex_schema_path,
        cancellation_path,
        before_path,
        after_path,
        recovery_snapshot_path,
    ]
    stale = [str(path) for path in stale_outputs if path.exists() or path.is_symlink()]
    if stale:
        raise ContractError(
            f"fresh job runtime contains stale outputs; use a new job_id: {stale}"
        )
    state_guard = FileLock(str(_validated_state_lock_path(run_path, repository_root)))
    try:
        state_guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        raise ActiveWorkerError("worker run state transition is active") from exc
    state_locked = True
    try:
        run, _, _ = _load_run(run_path)
        _assert_run_binding(run, job, args.worker_profile)
        if run["active_job"] is not None:
            raise ActiveWorkerError("worker run already has an active job")
        if any(row["job_id"] == job["job_id"] for row in run["completed_jobs"]):
            raise ContractError("job_id was already used; recovery requires a fresh job id")
    except BaseException:
        state_guard.release()
        state_locked = False
        raise
    lock: FileLock | None = None
    if args.access == "workspace-write":
        try:
            selected_lock = _validated_mutation_lock_path(working_root)
            selected_lock.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(selected_lock))
        except BaseException:
            state_guard.release()
            state_locked = False
            raise
        try:
            lock.acquire(timeout=0)
        except FileLockTimeout as exc:
            state_guard.release()
            state_locked = False
            raise ActiveWorkerError(f"working-root mutation lock is held: {selected_lock}") from exc
        try:
            _ensure_no_cross_run_active(repository_root, working_root, run_path)
        except BaseException:
            lock.release()
            state_guard.release()
            state_locked = False
            raise
    try:
        codegraph_policy = load_codegraph_policy(scope_root)
        if job["command"] == "implement" and args.access == "workspace-write":
            prior = _expanded_codegraph(run.get("codegraph", {}), codegraph_policy, working_root)
            state = scope_codegraph.sync(codegraph_policy, working_root, prior)
            run["codegraph"] = _compact_codegraph(state)
        codegraph = _expanded_codegraph(run.get("codegraph", {}), codegraph_policy, working_root)
        result_schema_path = installed_config_path(scope_root, "worker-result.schema.json")
        result_schema = load_json(result_schema_path, "worker result schema")
        if args.provider == "codex":
            atomic_write_json(codex_schema_path, codex_output_schema(result_schema))
            provider_schema_path = codex_schema_path
        else:
            provider_schema_path = result_schema_path
        prompt = render_prompt(job, job_path, scope_root, args.provider, codegraph)
        command = (
            build_codex_command(provider["executable"], selected, working_root, args.access, provider_schema_path, provider_result_path, codegraph)
            if args.provider == "codex"
            else build_claude_command(provider["executable"], selected, job, result_schema, args.access, codegraph)
        )
        excluded = [
            job_dir,
            run_path,
            run_state_lock_path(run_path),
            mutation_lock_path(working_root),
        ]
        if args.access == "workspace-write":
            before = capture_snapshot(working_root, excluded=excluded)
            _validate_implementation_workspace_continuity(job, before)
            escaped = _escaping_symlinks(before, working_root, job["write_scope"])
            if escaped:
                raise ContractError(f"write_scope contains escaping symlinks: {escaped}")
            atomic_write_json(before_path, before)
            read_identity = None
        else:
            before = None
            read_identity = _read_identity(working_root)
        active = {
            "job_id": job["job_id"],
            "role": job["role"],
            "phase": job["phase"],
            "provider": args.provider,
            "requested_model": selected["model"],
            "reasoning_effort": selected["reasoning_effort"],
            "worker_profile": args.worker_profile,
            "access": args.access,
            "job_path": str(job_path),
            "job_sha256": _sha256_file(job_path),
            "result_path": str(result_path),
            "provider_result_path": str(provider_result_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "cancellation_path": str(cancellation_path),
            "before_snapshot": str(before_path) if before is not None else None,
            "after_snapshot": str(after_path) if before is not None else None,
            "read_identity_before": read_identity,
            "started_at": utc_now(),
            "runner_process": process_identity(),
            "provider_process": None,
            "provider_process_group": None,
            "provider_descendants": [],
        }
        if job["role"] == "implementation" and job["phase"] == "debugging":
            used = int(run.get("pre_audit_debugging_jobs", 0))
            maximum = scope_proofs.policy(scope_root)["maximum_pre_audit_debugging_jobs"]
            if used >= maximum:
                raise ContractError(f"pre-audit debugging budget exhausted ({maximum}); changes retained")
            run["pre_audit_debugging_jobs"] = used + 1
        run["active_job"] = active
        _write_run(run_path, run)
        state_guard.release()
        state_locked = False
        try:
            return_code, timed_out, cancelled = execute_provider(
                command,
                prompt,
                working_root=working_root,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                run_path=run_path,
                run=run,
                cancellation_path=cancellation_path,
                selected=selected,
            )
        except WorkerError as exc:
            changed: list[str] = []
            if before is not None:
                after = capture_snapshot(working_root, excluded=excluded)
                atomic_write_json(after_path, after)
                changed = snapshot_delta(before, after)
            _interrupt(run_path, run, str(exc), changed)
            raise
        if before is not None:
            after = capture_snapshot(working_root, excluded=excluded)
            atomic_write_json(after_path, after)
            actual = snapshot_delta(before, after)
        else:
            after = None
            actual = []
            if _read_identity(working_root) != read_identity:
                _interrupt(run_path, run, "read-only worker changed Git tree identity", [])
                raise ContractError("read-only worker changed Git tree identity")
        if timed_out or cancelled or return_code != 0:
            reason = "timeout" if timed_out else "cancelled" if cancelled else f"provider exited {return_code}"
            _interrupt(run_path, run, reason, actual)
            if timed_out:
                raise WorkerTimeout(reason)
            if cancelled:
                raise WorkerCancelled(reason)
            raise InfrastructureError(
                f"{reason}; stdout={_tail(stdout_path, int(selected['log_tail_lines']), int(selected['log_tail_characters']))}; stderr={_tail(stderr_path, int(selected['log_tail_lines']), int(selected['log_tail_characters']))}"
            )
        try:
            if _sha256_file(job_path) != active["job_sha256"]:
                raise ContractError("job packet changed while the worker was running")
            result, usage, fallback = _provider_result(args.provider, provider_result_path, stdout_path)
            validate_result(result, job, result_schema)
            _validate_decision_refs_current(job)
            if before is not None and after is not None:
                actual = _validate_attribution(job, result, before, after)
            elif result["changed_paths"]:
                raise ContractError("read-only worker reported changed paths")
            row = _finalize_result(
                run_path,
                run,
                active,
                job,
                result,
                actual,
                usage,
                fallback,
                before,
                after,
            )
        except WorkerError as exc:
            _interrupt(run_path, run, str(exc), actual)
            raise
        print(json.dumps(row, sort_keys=True))
        return 1 if row["status"] == "verification_failed" else 0
    finally:
        if state_locked:
            state_guard.release()
        if lock is not None:
            lock.release()


def classify_run(run_path: Path) -> dict[str, Any]:
    run, _, _ = _load_run(run_path)
    active = run.get("active_job")
    if not isinstance(active, dict):
        return {
            "status": "idle",
            "run": str(run_path),
            "last_job": run["completed_jobs"][-1] if run["completed_jobs"] else None,
        }
    state = _active_state(active)
    return {
        "status": "active" if state == "alive" else "identity_unproven" if state == "unknown" else "recovery_required",
        "run": str(run_path),
        "job_id": active.get("job_id"),
        "provider_state": identity_state(active.get("provider_process")),
        "runner_state": identity_state(active.get("runner_process")),
    }


def _recover_result(run_path: Path, run: dict[str, Any], working_root: Path) -> dict[str, Any]:
    active = run["active_job"]
    job_path = Path(active["job_path"])
    if _sha256_file(job_path) != active["job_sha256"]:
        return _interrupt(run_path, run, "job packet changed during recovery", [])
    job, _, _, scope_root = load_job(job_path, verify_artifact_hashes=False)
    _assert_run_binding(run, job, str(active["worker_profile"]))
    result_schema = load_json(installed_config_path(scope_root, "worker-result.schema.json"), "worker result schema")
    try:
        result, usage, fallback = _provider_result(
            active["provider"],
            Path(active["provider_result_path"]),
            Path(active["stdout_path"]),
        )
        validate_result(result, job, result_schema)
    except WorkerError as exc:
        changed: list[str] = []
        if active.get("access") == "workspace-write":
            before_path, _ = _snapshot_paths(active)
            if before_path is not None and before_path.is_file():
                before_for_error = load_json(before_path, "before snapshot")
                current_for_error = capture_snapshot(
                    working_root,
                    excluded=_snapshot_exclusions(active, run_path, working_root),
                )
                changed = snapshot_delta(before_for_error, current_for_error)
        return _interrupt(run_path, run, str(exc), changed)
    before: dict[str, Any] | None = None
    provider_after: dict[str, Any] | None = None
    if active["access"] == "workspace-write":
        before_path, after_path = _snapshot_paths(active)
        exclusions = _snapshot_exclusions(active, run_path, working_root)
        current = capture_snapshot(working_root, excluded=exclusions)
        recovery_path = Path(str(active["stdout_path"])).parent / "recovery-current-snapshot.json"
        if before_path is None or not before_path.is_file():
            atomic_write_json(recovery_path, current)
            return _interrupt(
                run_path, run, "write-job recovery is missing its before snapshot", []
            )
        before = load_json(before_path, "before snapshot")
        if after_path is None:
            after_path = Path(str(active["stdout_path"])).parent / "after-snapshot.json"
        if not after_path.is_file():
            atomic_write_json(after_path, current)
            return _interrupt(
                run_path,
                run,
                "write-job recovery captured a missing after snapshot; publication refused",
                snapshot_delta(before, current),
            )
        provider_after = load_json(after_path, "after snapshot")
        if not _snapshots_match(provider_after, current):
            evidence_relative = job.get("implementation_evidence_path")
            promoted = (
                job.get("role") == "implementation"
                and job.get("phase") != "delivery_summary"
                and isinstance(evidence_relative, str)
                and snapshot_delta(provider_after, current) == [evidence_relative]
                and _promotion_is_current(
                    working_root / evidence_relative,
                    str(job["job_id"]),
                    _json_document_sha256(result),
                )
            )
            if not promoted:
                atomic_write_json(recovery_path, current)
                return _interrupt(
                    run_path,
                    run,
                    "repository drifted after the recorded worker snapshot; publication refused",
                    snapshot_delta(before, current),
                )
    try:
        _validate_decision_refs_current(job)
        if active["access"] == "workspace-write":
            assert before is not None and provider_after is not None
            actual = _validate_attribution(job, result, before, provider_after)
        else:
            if _read_identity(working_root) != active.get("read_identity_before"):
                raise ContractError("read-only tree identity changed before recovery")
            actual = []
            if result["changed_paths"]:
                raise ContractError("read-only worker reported changed paths")
        return _finalize_result(
            run_path,
            run,
            active,
            job,
            result,
            actual,
            usage,
            fallback,
            before,
            provider_after,
        )
    except WorkerError as exc:
        changed: list[str] = []
        if before is not None:
            current = capture_snapshot(
                working_root,
                excluded=_snapshot_exclusions(active, run_path, working_root),
            )
            changed = snapshot_delta(before, current)
        return _interrupt(run_path, run, str(exc), changed)


def recover_run(run_path: Path) -> dict[str, Any]:
    _, repository_root, _ = _load_run(run_path)
    state_guard = FileLock(
        str(_validated_state_lock_path(run_path, repository_root))
    )
    try:
        state_guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        raise ActiveWorkerError("worker run state transition is active") from exc
    lock: FileLock | None = None
    try:
        run, _, working_root = _load_run(run_path)
        active = run.get("active_job")
        if not isinstance(active, dict):
            return {"status": "idle", "run": str(run_path)}
        state = _active_state(active)
        if state != "dead":
            raise ActiveWorkerError(f"recovery refused while worker state is {state}")
        if active["access"] == "workspace-write":
            lock = FileLock(str(_validated_mutation_lock_path(working_root)))
            try:
                lock.acquire(timeout=0)
            except FileLockTimeout as exc:
                raise ActiveWorkerError("recovery refused while mutation lock is held") from exc
        run, _, working_root = _load_run(run_path)
        current_active = run.get("active_job")
        if not isinstance(current_active, dict) or _active_state(current_active) != "dead":
            raise ActiveWorkerError("worker lifecycle changed during recovery")
        return _recover_result(run_path, run, working_root)
    finally:
        if lock is not None:
            lock.release()
        state_guard.release()


def cancel_run(run_path: Path, job_id: str, reason: str) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or "\n" in reason:
        raise ContractError("cancellation reason must be one non-empty line of at most 500 characters")
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise ContractError("cancellation job_id is invalid")
    _, repository_root, _ = _load_run(run_path)
    state_guard = FileLock(
        str(_validated_state_lock_path(run_path, repository_root))
    )
    try:
        state_guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        raise ActiveWorkerError("worker run state transition is active") from exc
    try:
        run, _, working_root = _load_run(run_path)
        active = run.get("active_job")
        if not isinstance(active, dict) or active.get("job_id") != job_id:
            raise ContractError(f"cancel target is stale; active job is {active.get('job_id') if isinstance(active, dict) else None}")
    finally:
        state_guard.release()
    cancellation = Path(active["cancellation_path"])
    atomic_write_yaml(cancellation, {"schema_version": 1, "job_id": job_id, "reason": reason, "requested_at": utc_now()})
    _terminate_lifecycle(active, 3.0)
    if identity_state(active.get("runner_process")) == "alive":
        try:
            state_guard.acquire(timeout=0)
        except FileLockTimeout as exc:
            raise ActiveWorkerError("worker run state transition is active") from exc
        try:
            current, _, _ = _load_run(run_path)
            current_active = current.get("active_job")
            if not isinstance(current_active, dict) or current_active.get("job_id") != job_id:
                raise ContractError("cancel target completed before cancellation was recorded")
        finally:
            state_guard.release()
        return {"status": "cancellation_requested", "job_id": active["job_id"], "run": str(run_path)}
    try:
        state_guard.acquire(timeout=0)
    except FileLockTimeout as exc:
        raise ActiveWorkerError("worker run state transition is active") from exc
    lock: FileLock | None = None
    try:
        run, _, working_root = _load_run(run_path)
        current_active = run.get("active_job")
        if not isinstance(current_active, dict) or current_active.get("job_id") != job_id:
            raise ContractError("cancel target became stale before finalization")
        active = current_active
        if active["access"] == "workspace-write":
            lock = FileLock(str(_validated_mutation_lock_path(working_root)))
            try:
                lock.acquire(timeout=0)
            except FileLockTimeout as exc:
                raise ActiveWorkerError("cancel terminated provider but mutation lock remains held") from exc
        changed: list[str] = []
        before_path, after_path = _snapshot_paths(active)
        if before_path and before_path.is_file():
            before = load_json(before_path, "before snapshot")
            if not (after_path and after_path.is_file()):
                after = capture_snapshot(
                    working_root,
                    excluded=_snapshot_exclusions(active, run_path, working_root),
                )
                if after_path:
                    atomic_write_json(after_path, after)
            else:
                after = load_json(after_path, "after snapshot")
            changed = snapshot_delta(before, after)
        row = _interrupt(run_path, run, f"cancelled: {reason}", changed)
        return {"status": "cancelled", "job": row, "run": str(run_path)}
    finally:
        if lock is not None:
            lock.release()
        state_guard.release()


def preflight(args: argparse.Namespace) -> int:
    scope_root = _canonical_directory(args.scope_root, "scope_root")
    if args.phase not in ROLE_PHASES[args.role]:
        raise ContractError("--phase is incompatible with --role")
    selected = _provider_policy(load_policy(scope_root), args.role, args.phase, args.provider, args.worker_profile)
    print(json.dumps(provider_preflight(args.provider, selected), sort_keys=True))
    return 0


def init_run(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            initialize_run(
                args.repository_root,
                args.working_root,
                args.epic_id,
                args.command,
                args.worker_profile,
                args.scope_root,
            ),
            sort_keys=True,
        )
    )
    return 0


def story_groups(args: argparse.Namespace) -> int:
    manifest = load_yaml(args.epic_dir / "delivery-manifest.yaml", "delivery manifest")
    print(json.dumps(scope_proofs.groups(manifest, scope_proofs.policy(args.scope_root)["maximum_stories_per_group"])))
    return 0


def verify_proofs(args: argparse.Namespace) -> int:
    run_path = args.run.resolve(strict=True)
    run, repository_root, root = _load_run(run_path)
    if run["command"] != "implement" or run["active_job"] is not None:
        raise ContractError("verify-proofs requires an idle implement run")
    epic = args.epic_dir.resolve(strict=True)
    if epic.parent != root / "docs/epics":
        raise ContractError("epic must be a direct working-root epic directory")
    scope_root = Path(run["scope_root"])
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = load_yaml(manifest_path, "delivery manifest")
    if manifest.get("schema_version") != 3:
        raise ContractError("this proof executor requires a newly approved manifest v3; use the pinned Scope version for an older epic")
    spec = importlib.util.spec_from_file_location("proof_handoff_validator", Path(__file__).with_name("validate-refinement.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with FileLock(str(_validated_state_lock_path(run_path, repository_root)), timeout=0), FileLock(str(_validated_mutation_lock_path(root)), timeout=0):
        run, _, _ = _load_run(run_path)
        if run["active_job"] is not None:
            raise ActiveWorkerError("implementation run became active")
        _ensure_no_cross_run_active(repository_root, root, run_path)
        errors = module.RefinementValidator(epic, "handoff", repo_root=root).validate()
        if errors:
            raise ContractError("invalid approved handoff: " + "; ".join(errors))
        evidence_relative = (epic / "implementation-evidence.yaml").relative_to(root).as_posix()
        job = {"job_id": "proofs-" + str(time.time_ns()), "role": "implementation", "phase": "proof_checkpoint",
               "epic_id": manifest["epic_id"], "working_root": str(root),
               "implementation_evidence_path": evidence_relative,
               "artifacts": [{"path": manifest_path.relative_to(root).as_posix(), "sha256": _sha256_file(manifest_path)}]}
        if manifest["epic_id"] != run["epic_id"]:
            raise ContractError("proof epic differs from run")
        before = capture_snapshot(root)
        _validate_implementation_workspace_continuity(job, before)
        checkpoint = scope_proofs.execute(
            scope_proofs.executable(manifest["proofs"]),
            root,
            epic,
            scope_proofs.policy(scope_root),
        )
        result = {"status": "completed", "payload": {"proof_evidence": []}}
        if any("proof changed its source" in row["summary"] for row in checkpoint["proofs"]):
            raise ContractError("proof mutated source; stop and attribute changes before retrying")
        promote_implementation_evidence(job, result, [], before, capture_snapshot(root), checkpoint["sha256"], checkpoint)
        run["last_proof_checkpoint"] = {key: checkpoint[key] for key in ("path", "sha256", "status", "duration_seconds")}
        _write_run(run_path, run)
    print(json.dumps(run["last_proof_checkpoint"], sort_keys=True))
    return 0 if checkpoint["status"] == "pass" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="subcommand", required=True)
    proofs_parser = commands.add_parser("verify-proofs", help="Execute the complete approved epic proof set")
    proofs_parser.add_argument("epic_dir", type=Path)
    proofs_parser.add_argument("--run", type=Path, required=True)
    proofs_parser.set_defaults(handler=verify_proofs)
    groups_parser = commands.add_parser("story-groups", help="Show dependency-connected implementation groups")
    groups_parser.add_argument("epic_dir", type=Path)
    groups_parser.add_argument("--scope-root", type=Path, default=Path(__file__).resolve().parent.parent)
    groups_parser.set_defaults(handler=story_groups)
    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    preflight_parser.add_argument("--role", choices=tuple(ROLE_PHASES), required=True)
    preflight_parser.add_argument("--phase", required=True)
    preflight_parser.add_argument("--worker-profile", choices=tuple(WORKER_PROFILES), default="default")
    preflight_parser.add_argument("--scope-root", type=Path, default=Path(__file__).resolve().parent.parent)
    preflight_parser.set_defaults(handler=preflight)
    init_parser = commands.add_parser("init")
    init_parser.add_argument("--repository-root", type=Path, required=True)
    init_parser.add_argument("--working-root", type=Path, required=True)
    init_parser.add_argument("--epic-id", required=True)
    init_parser.add_argument("--command", choices=tuple(sorted(set().union(*ROLE_COMMANDS.values()))), required=True)
    init_parser.add_argument("--worker-profile", choices=tuple(WORKER_PROFILES), default="default")
    init_parser.add_argument("--scope-root", type=Path, default=Path(__file__).resolve().parent.parent)
    init_parser.set_defaults(handler=init_run)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    run_parser.add_argument("--role", choices=tuple(ROLE_PHASES), required=True)
    run_parser.add_argument("--job", type=Path, required=True)
    run_parser.add_argument("--result", type=Path, required=True)
    run_parser.add_argument("--cwd", type=Path, required=True)
    run_parser.add_argument("--access", choices=("read-only", "workspace-write"), required=True)
    run_parser.add_argument("--worker-profile", choices=tuple(WORKER_PROFILES), default="default")
    run_parser.set_defaults(handler=run_worker)
    for name in ("status", "recover"):
        selected = commands.add_parser(name)
        selected.add_argument("--run", type=Path, required=True)
    cancel_parser = commands.add_parser("cancel")
    cancel_parser.add_argument("--run", type=Path, required=True)
    cancel_parser.add_argument("--job-id", required=True)
    cancel_parser.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "status":
            print(json.dumps(classify_run(args.run.absolute()), sort_keys=True))
            return 0
        if args.subcommand == "recover":
            print(json.dumps(recover_run(args.run.absolute()), sort_keys=True))
            return 0
        if args.subcommand == "cancel":
            print(
                json.dumps(
                    cancel_run(args.run.absolute(), args.job_id, args.reason),
                    sort_keys=True,
                )
            )
            return 0
        return int(args.handler(args))
    except WorkerTimeout as exc:
        print(f"Scope worker timeout: {exc}", file=sys.stderr)
        return 124
    except ActiveWorkerError as exc:
        print(f"Scope worker active: {exc}", file=sys.stderr)
        return 3
    except WorkerError as exc:
        print(f"Scope worker failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Scope worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

from filelock import FileLock
import psutil
import pytest
import yaml
from jsonschema import Draft7Validator, Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "src_shared/scripts/scope-worker.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lean_scope_worker", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_module()


def _command(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _command("git", "init", "-b", "main", cwd=path)
    _command("git", "config", "user.email", "scope@example.test", cwd=path)
    _command("git", "config", "user.name", "Scope Test", cwd=path)
    (path / ".gitignore").write_text("tmp_debug/\n.codegraph/\nignored/\n", encoding="utf-8")
    (path / "README.md").write_text("artifact\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src/value.txt").write_text("before\n", encoding="utf-8")
    epic = path / "docs/epics/gd-001"
    epic.mkdir(parents=True)
    (epic / "delivery-manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "epic_id": "gd-001",
                "stories": [],
                "proofs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _command("git", "add", ".", cwd=path)
    _command("git", "commit", "-m", "baseline", cwd=path)
    return path.resolve()


def _scope(path: Path, provider: str = "codex") -> Path:
    (path / "config").mkdir(parents=True)
    (path / "workers").mkdir()
    for name in ("worker-job.schema.json", "worker-result.schema.json", "codegraph-policy.yaml", "execution-policy.yaml"):
        shutil.copy2(REPO_ROOT / "src_shared/config" / name, path / "config" / name)
    shutil.copy2(REPO_ROOT / f"src_{provider}/config/worker-policy.yaml", path / "config/worker-policy.yaml")
    for source in (REPO_ROOT / "src_shared/workers").glob("*.md"):
        shutil.copy2(source, path / "workers" / source.name)
    return path.resolve()


def _ready(root: Path) -> dict:
    return {
        "status": "ready",
        "reason": "ready",
        "project_root": str(root),
        "index_path": str(root / ".codegraph"),
        "version": "1.5.0",
        "minimum_version": "1.5.0",
        "executable": "codegraph",
        "initialized": True,
        "synced": True,
        "query_commands": ["status", "query"],
        "explore_max_files": 8,
        "affected_depth": 3,
        "affected_test_filters": ["tests/**/*.py"],
    }


def _initialize(monkeypatch: pytest.MonkeyPatch, repo: Path, scope: Path, command: str = "implement") -> Path:
    monkeypatch.setattr(RUNNER.scope_codegraph, "prepare", lambda policy, root: _ready(root))
    response = RUNNER.initialize_run(repo, repo, "gd-001", command, "default", scope)
    return Path(response["run"])


def _job(repo: Path, scope: Path, *, role: str = "implementation", write_scope: list[str] | None = None) -> tuple[dict, Path]:
    command = "epic_refine" if role == "refinement" else "implement"
    phase = {"implementation": "story", "refinement": "design_handoff", "diagnostic": "investigate"}[role]
    job_id = f"gd-001-{role}-001"
    job_dir = repo / "tmp_debug/scope-runs/gd-001" / command / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    manifest = repo / "docs/epics/gd-001/delivery-manifest.yaml"
    job = {
        "schema_version": 2,
        "job_id": job_id,
        "command": command,
        "role": role,
        "phase": phase,
        "epic_id": "gd-001",
        "repository_root": str(repo),
        "working_root": str(repo),
        "scope_root": str(scope),
        "read_scope": ["."],
        "write_scope": (["src"] if role in {"implementation", "refinement"} else []) if write_scope is None else write_scope,
        "artifacts": [
            {"kind": "input", "path": "README.md", "sha256": RUNNER._sha256_file(repo / "README.md")},
            {
                "kind": "delivery_manifest",
                "path": "docs/epics/gd-001/delivery-manifest.yaml",
                "sha256": RUNNER._sha256_file(manifest),
            },
        ],
        "decision_refs": [],
        "required_validations": [],
        "required_proof_ids": [],
        "result_path": str(job_dir / "result.json"),
    }
    if role == "implementation":
        job["implementation_evidence_path"] = (
            "docs/epics/gd-001/implementation-evidence.yaml"
        )
    path = job_dir / "job.yaml"
    path.write_text(yaml.safe_dump(job, sort_keys=False), encoding="utf-8")
    return job, path


def _result(job: dict, changed: list[str]) -> dict:
    payload = (
        {"kind": "implementation", "notes": "done", "proof_evidence": []}
        if job["role"] == "implementation"
        else {"kind": "refinement", "authored_artifacts": changed, "decision_refs": []}
    )
    return {
        "schema_version": 2,
        "job_id": job["job_id"],
        "status": "completed",
        "summary": "done",
        "changed_paths": changed,
        "validations": [],
        "questions": [],
        "issues": [],
        "payload": payload,
    }


def _fake_writer(path: Path) -> Path:
    path.write_text(
        """from pathlib import Path
import json, sys
result_path, target, relative, job_id = sys.argv[1:]
Path(target).parent.mkdir(parents=True, exist_ok=True)
Path(target).write_text('after\\n', encoding='utf-8')
result = {'schema_version': 2, 'job_id': job_id, 'status': 'completed', 'summary': 'done',
          'changed_paths': [relative], 'validations': [], 'questions': [], 'issues': [],
          'payload': {'kind': 'implementation', 'notes': 'done', 'proof_evidence': []}}
Path(result_path).write_text(json.dumps(result), encoding='utf-8')
""",
        encoding="utf-8",
    )
    return path


def _orphaned_write_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    include_after: bool,
    artifact_is_output: bool = False,
) -> tuple[Path, Path, dict, Path]:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    job, job_path = _job(repo, scope)
    if artifact_is_output:
        job["artifacts"].append({
            "kind": "input_output",
            "path": "src/value.txt",
            "sha256": RUNNER._sha256_file(repo / "src/value.txt"),
        })
        job_path.write_text(yaml.safe_dump(job, sort_keys=False), encoding="utf-8")
    job_dir = job_path.parent
    exclusions = [
        job_dir,
        run_path,
        RUNNER.run_state_lock_path(run_path),
        RUNNER.mutation_lock_path(repo),
    ]
    before = RUNNER.capture_snapshot(repo, excluded=exclusions)
    before_path = job_dir / "before-snapshot.json"
    RUNNER.atomic_write_json(before_path, before)
    (repo / "src/value.txt").write_text("worker\n", encoding="utf-8")
    after_path = job_dir / "after-snapshot.json"
    if include_after:
        RUNNER.atomic_write_json(
            after_path, RUNNER.capture_snapshot(repo, excluded=exclusions)
        )
    provider_result = job_dir / "provider-result.json"
    RUNNER.atomic_write_json(provider_result, _result(job, ["src/value.txt"]))
    stdout = job_dir / "provider.stdout"
    stdout.write_text("", encoding="utf-8")
    dead = {"pid": 99999999, "create_time": 1.0}
    run = yaml.safe_load(run_path.read_text())
    run["active_job"] = {
        "job_id": job["job_id"],
        "role": job["role"],
        "phase": job["phase"],
        "provider": "codex",
        "requested_model": "gpt-5.6-terra",
        "reasoning_effort": "max",
        "worker_profile": "default",
        "access": "workspace-write",
        "job_path": str(job_path),
        "job_sha256": RUNNER._sha256_file(job_path),
        "result_path": job["result_path"],
        "provider_result_path": str(provider_result),
        "stdout_path": str(stdout),
        "stderr_path": str(job_dir / "provider.stderr"),
        "cancellation_path": str(job_dir / "cancel.yaml"),
        "before_snapshot": str(before_path),
        "after_snapshot": str(after_path),
        "read_identity_before": None,
        "started_at": RUNNER.utc_now(),
        "runner_process": dead,
        "provider_process": dead,
        "provider_process_group": None,
        "provider_descendants": [],
    }
    RUNNER.atomic_write_yaml(run_path, run)
    return repo, run_path, job, after_path


def test_cli_contains_only_lean_lifecycle_commands() -> None:
    parser = RUNNER.build_parser()
    help_text = parser.format_help()
    for command in ("preflight", "init", "run", "status", "recover", "cancel"):
        assert command in help_text
    for removed in ("operate", "set-profile", "resolve-incident"):
        assert removed not in help_text


def test_platform_worker_policies_are_self_contained() -> None:
    for platform in ("src_codex", "src_claude"):
        policy = RUNNER.load_policy(REPO_ROOT / platform)
        assert set(policy["runtime"]) == {"roles", "lifecycle"}
        assert "trusted_operations" not in policy


def test_mutation_lock_has_one_shared_working_root_path(tmp_path: Path) -> None:
    assert RUNNER.mutation_lock_path(tmp_path) == tmp_path / "tmp_debug/scope-mutation.lock"


def test_initialize_creates_small_v2_run_without_mirrors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    run = yaml.safe_load(run_path.read_text())
    assert run["schema_version"] == 2
    assert run["active_job"] is None
    assert run["completed_jobs"] == []
    assert run["scope_root"] == str(scope)
    assert run["worker_policy_sha256"] == RUNNER._sha256_file(scope / "config/worker-policy.yaml")
    for removed in ("decisions", "command_baseline_manifest", "attributed_manifest", "active_operation", "operation_receipts", "failed_jobs", "unattributed_change_incidents", "profile_transitions"):
        assert removed not in run
    assert run_path.stat().st_size < 10_000


def test_run_binding_rejects_profile_root_scope_and_policy_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    job, _ = _job(repo, scope)
    run = yaml.safe_load(run_path.read_text())
    RUNNER._assert_run_binding(run, job, "default")
    with pytest.raises(RUNNER.ContractError, match="worker_profile"):
        RUNNER._assert_run_binding(run, job, "budget")
    changed = dict(job)
    changed["working_root"] = str(tmp_path)
    with pytest.raises(RUNNER.ContractError, match="working_root"):
        RUNNER._assert_run_binding(run, changed, "default")
    (scope / "config/worker-policy.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    with pytest.raises(RUNNER.ContractError, match="policy changed"):
        RUNNER._assert_run_binding(run, job, "default")


def test_read_only_start_is_serialized_by_per_run_state_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    job, job_path = _job(repo, scope, role="diagnostic")
    monkeypatch.setattr(RUNNER, "provider_preflight", lambda provider, selected: {"executable": sys.executable, "version": "fake"})
    guard = FileLock(str(RUNNER.run_state_lock_path(run_path)))
    guard.acquire()
    try:
        with pytest.raises(RUNNER.ActiveWorkerError, match="state transition"):
            RUNNER.run_worker(SimpleNamespace(
                job=job_path, role="diagnostic", cwd=repo,
                result=Path(job["result_path"]), provider="codex",
                worker_profile="default", access="read-only",
            ))
    finally:
        guard.release()


def test_initialize_refuses_held_mutation_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    lock = FileLock(str(RUNNER.mutation_lock_path(repo)))
    lock.acquire()
    try:
        with pytest.raises(RUNNER.ActiveWorkerError):
            _initialize(monkeypatch, repo, scope)
    finally:
        lock.release()


def test_obsolete_run_fails_with_archive_instruction(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    path = repo / "tmp_debug/scope-runs/gd-001/implement/run.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(RUNNER.ContractError, match="archive"):
        RUNNER._load_run(path)


def test_snapshot_detects_tracked_untracked_and_ignored_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n")
    (repo / "new.txt").write_text("new\n")
    (repo / "ignored").mkdir()
    (repo / "ignored/secret.txt").write_text("secret\n")
    after = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(before, after) == ["ignored", "ignored/secret.txt", "new.txt", "src/value.txt"]


def test_snapshot_ignores_only_untracked_regular_ds_store(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    metadata = repo / ".DS_Store"
    metadata.write_text("finder-before", encoding="utf-8")
    before = RUNNER.capture_snapshot(repo)
    metadata.write_text("finder-after", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    assert ".DS_Store" not in {row["path"] for row in after["entries"]}
    assert RUNNER.snapshot_delta(before, after) == []

    _command("git", "add", "-f", ".DS_Store", cwd=repo)
    _command("git", "commit", "-m", "track metadata", cwd=repo)
    tracked = RUNNER.capture_snapshot(repo)
    metadata.write_text("tracked-change", encoding="utf-8")
    assert RUNNER.snapshot_delta(tracked, RUNNER.capture_snapshot(repo)) == [
        ".DS_Store"
    ]


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_snapshot_keeps_ds_store_symlink_and_escape_detection(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    target = tmp_path / "outside"
    target.write_text("outside", encoding="utf-8")
    os.symlink(target, repo / ".DS_Store")
    snapshot = RUNNER.capture_snapshot(repo)
    row = next(row for row in snapshot["entries"] if row["path"] == ".DS_Store")
    assert row["kind"] == "symlink"
    assert RUNNER._escaping_symlinks(snapshot, repo, ["."]) == [".DS_Store"]


def test_exclusion_prefixes_follow_filesystem_identity(tmp_path: Path) -> None:
    lower = tmp_path / "gd-001"
    upper = tmp_path / "GD-001"
    lower.mkdir()
    try:
        upper.mkdir()
    except FileExistsError:
        child = lower / "jobs"
        child.mkdir()
        assert os.path.samefile(lower, upper)
        assert RUNNER._lexical_within(child, upper)
    else:
        assert not os.path.samefile(lower, upper)
        assert not RUNNER._lexical_within(lower / "jobs", upper)


def test_snapshot_attributes_empty_directory_create_chmod_rename_and_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    before = RUNNER.capture_snapshot(repo)
    empty = repo / "empty"
    empty.mkdir()
    created = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(before, created) == ["empty"]
    empty.chmod(0o700)
    chmodded = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(created, chmodded) == ["empty"]
    empty.rename(repo / "renamed")
    renamed = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(chmodded, renamed) == ["empty", "renamed"]
    (repo / "renamed").rmdir()
    deleted = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(renamed, deleted) == ["renamed"]


def test_snapshot_does_not_exclude_codegraph_during_worker_execution(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".codegraph").mkdir()
    database = repo / ".codegraph/codegraph.db"
    database.write_text("before", encoding="utf-8")
    before = RUNNER.capture_snapshot(repo)
    database.write_text("after", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    assert RUNNER.snapshot_delta(before, after) == [".codegraph/codegraph.db"]


def test_root_scope_matches_every_repository_path() -> None:
    assert RUNNER._path_in_scope("nested/value.py", ["."])
    assert RUNNER._path_in_scope("value.py", ["."])


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_snapshot_detects_escaping_symlink_in_write_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    os.symlink(tmp_path, repo / "src/escape")
    snapshot = RUNNER.capture_snapshot(repo)
    assert RUNNER._escaping_symlinks(snapshot, repo, ["src"]) == ["src/escape"]


def test_snapshot_protects_git_config_and_hooks(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    before = RUNNER.capture_snapshot(repo)
    git_common = Path(_command("git", "rev-parse", "--git-common-dir", cwd=repo))
    if not git_common.is_absolute():
        git_common = repo / git_common
    with (git_common / "config").open("a", encoding="utf-8") as handle:
        handle.write("\n# unauthorized\n")
    hook = git_common / "hooks/scope-test"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    actual = RUNNER.snapshot_delta(before, after)
    assert ".git/config" in actual
    assert ".git/hooks/scope-test" in actual
    result = {"changed_paths": actual}
    job = {"write_scope": ["."]}
    with pytest.raises(RUNNER.ContractError, match="protected Git configuration"):
        RUNNER._validate_attribution(job, result, before, after)


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_runner_owned_runtime_rejects_symlink_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo / "tmp_debug")
    monkeypatch.setattr(RUNNER.scope_codegraph, "prepare", lambda policy, root: _ready(root))
    with pytest.raises(RUNNER.ContractError, match="symlink component"):
        RUNNER.initialize_run(repo, repo, "gd-001", "implement", "default", scope)


def test_end_to_end_write_job_uses_one_result_and_small_completed_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    job, job_path = _job(repo, scope)
    fake = _fake_writer(tmp_path / "fake.py")
    monkeypatch.setattr(RUNNER.scope_codegraph, "sync", lambda policy, root, prior: prior)
    monkeypatch.setattr(RUNNER, "provider_preflight", lambda provider, selected: {"executable": sys.executable, "version": "fake"})
    monkeypatch.setattr(
        RUNNER,
        "build_codex_command",
        lambda executable, selected, working_root, access, schema, output, codegraph: [
            sys.executable, str(fake), str(output), str(repo / "src/value.txt"), "src/value.txt", job["job_id"]
        ],
    )
    result = RUNNER.run_worker(SimpleNamespace(
        job=job_path, role="implementation", cwd=repo, result=Path(job["result_path"]),
        provider="codex", worker_profile="default", access="workspace-write",
    ))
    assert result == 0
    run = yaml.safe_load(run_path.read_text())
    assert run["active_job"] is None
    assert run["completed_jobs"][0]["status"] == "completed"
    assert run["completed_jobs"][0]["changed_paths"] == ["src/value.txt"]
    assert run["completed_jobs"][0]["after_snapshot_sha256"] == RUNNER._sha256_file(
        Path(run["completed_jobs"][0]["result_path"]).parent / "after-snapshot.json"
    )
    assert "metadata_path" not in run["completed_jobs"][0]
    assert Path(job["result_path"]).is_file()
    assert not (Path(job["result_path"]).parent / "metadata.yaml").exists()
    evidence_path = repo / job["implementation_evidence_path"]
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 2
    assert evidence["validated_jobs"][0]["job_id"] == job["job_id"]
    assert evidence["attributed_delta"] == [
        {
            "path": "src/value.txt",
            "state": "present",
            "kind": "file",
            "sha256": RUNNER._sha256_file(repo / "src/value.txt"),
            "mode": (repo / "src/value.txt").stat().st_mode & 0o777,
        }
    ]
    assert RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    ) == []


def test_worker_cannot_write_runner_owned_implementation_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope, write_scope=["."])
    before = RUNNER.capture_snapshot(repo)
    evidence_path = repo / job["implementation_evidence_path"]
    evidence_path.write_text("worker-authored\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, [job["implementation_evidence_path"]])
    with pytest.raises(RUNNER.ContractError, match="runner-owned implementation evidence"):
        RUNNER._validate_attribution(job, result, before, after)


def test_implementation_worker_rejects_ignored_changed_paths_immediately(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope, write_scope=["."])
    before = RUNNER.capture_snapshot(repo)
    ignored = repo / "ignored/deliverable.txt"
    ignored.parent.mkdir()
    ignored.write_text("cannot be committed normally\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    actual = RUNNER.snapshot_delta(before, after)
    result = _result(job, actual)
    with pytest.raises(RUNNER.ContractError, match="ignored paths"):
        RUNNER._validate_attribution(job, result, before, after)


def test_implementation_worker_ignores_only_known_gitignored_test_artifacts(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope, write_scope=["."])
    with (repo / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("\n__pycache__/\n.coverage*\n.env\n")
    _command("git", "add", ".gitignore", cwd=repo)
    _command("git", "commit", "-m", "ignore test artifacts", cwd=repo)
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n", encoding="utf-8")
    (repo / "src/__pycache__").mkdir()
    (repo / "src/__pycache__/value.pyc").write_bytes(b"cache")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache/.gitignore").write_text("*\n", encoding="utf-8")
    (repo / ".pytest_cache/state").write_text("cache", encoding="utf-8")
    (repo / ".coverage").write_text("coverage", encoding="utf-8")
    assert subprocess.run(
        ["git", "check-ignore", ".pytest_cache"], cwd=repo, check=False
    ).returncode == 1
    assert subprocess.run(
        ["git", "check-ignore", ".pytest_cache/state"], cwd=repo, check=False
    ).returncode == 0
    after = RUNNER.capture_snapshot(repo)
    result = _result(
        job,
        [
            "src/value.txt",
            "src/__pycache__",
            "src/__pycache__/value.pyc",
            ".pytest_cache",
            ".pytest_cache/state",
            ".coverage",
        ],
    )
    assert RUNNER._validate_attribution(job, result, before, after) == [
        "src/value.txt"
    ]

    (repo / ".env").write_text("secret=true\n", encoding="utf-8")
    unsafe = RUNNER.capture_snapshot(repo)
    with pytest.raises(RUNNER.ContractError, match=r"\.env"):
        RUNNER._validate_attribution(job, result, before, unsafe)


def test_implementation_attribution_reports_deliverable_files_not_parent_directories(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope, write_scope=["."])
    before = RUNNER.capture_snapshot(repo)
    source = repo / "src/disambiguation/evaluation/value.py"
    test = repo / "tests/unit/disambiguation/test_value.py"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    test.write_text("from src.disambiguation.evaluation.value import VALUE\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(
        job,
        [
            "src/disambiguation/evaluation/value.py",
            "tests/unit/disambiguation/test_value.py",
        ],
    )
    assert RUNNER._validate_attribution(job, result, before, after) == [
        "src/disambiguation/evaluation/value.py",
        "tests/unit/disambiguation/test_value.py",
    ]


def test_optional_allowed_validation_failure_requires_a_visible_major_issue(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["allowed_commands"] = ["pytest -q"]
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    result = _result(job, [])
    result["validations"] = [
        {"command": "pytest -q", "exit_code": 0, "summary": "1 passed"}
    ]
    RUNNER.validate_result(result, job, schema)

    result["validations"][0]["exit_code"] = 1
    with pytest.raises(RUNNER.ContractError, match="surfaced as a major issue"):
        RUNNER.validate_result(result, job, schema)
    result["issues"] = [
        {
            "severity": "major",
            "message": "Optional environment probe failed",
            "evidence": ["pytest -q exited 1"],
        }
    ]
    RUNNER.validate_result(result, job, schema)

    job["required_validations"] = [
        {"command": "pytest -q", "purpose": "required test"}
    ]
    with pytest.raises(RUNNER.ContractError, match="failed validations.*required"):
        RUNNER.validate_result(result, job, schema)

    job["required_validations"] = []
    result["validations"] = [
        {"command": "unknown", "exit_code": 0, "summary": "passed"}
    ]
    with pytest.raises(RUNNER.ContractError, match="undeclared validation"):
        RUNNER.validate_result(result, job, schema)


def test_evidence_promotion_is_idempotent_and_tracks_reversion(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    result_sha = RUNNER._json_document_sha256(result)
    first = RUNNER.promote_implementation_evidence(
        job, result, ["src/value.txt"], before, after, result_sha
    )
    replay = RUNNER.promote_implementation_evidence(
        job, result, ["src/value.txt"], before, after, result_sha
    )
    assert replay == first
    assert len(first["validated_jobs"]) == 1
    with pytest.raises(RUNNER.ContractError, match="conflicts with completed job"):
        RUNNER.promote_implementation_evidence(
            job, result, ["src/value.txt"], before, after, "sha256:" + "f" * 64
        )

    second_job = {**job, "job_id": "gd-001-implementation-002"}
    second_result = _result(second_job, ["src/value.txt"])
    before_revert = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("before\n", encoding="utf-8")
    after_revert = RUNNER.capture_snapshot(repo)
    final = RUNNER.promote_implementation_evidence(
        second_job,
        second_result,
        ["src/value.txt"],
        before_revert,
        after_revert,
        RUNNER._json_document_sha256(second_result),
    )
    assert len(final["validated_jobs"]) == 2
    assert final["attributed_delta"] == []
    assert RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    ) == []


def test_first_promotion_initializes_the_checked_in_evidence_template(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    template = (
        REPO_ROOT
        / "src_shared/skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml"
    )
    shutil.copy2(template, repo / job["implementation_evidence_path"])
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    evidence = RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value.txt"],
        before,
        after,
        RUNNER._json_document_sha256(result),
    )
    assert evidence["epic_id"] == "gd-001"
    assert evidence["baseline"]["head"] == before["head"]
    assert evidence["baseline"]["tree"] != "pending"


def test_attribution_verifier_rejects_unattributed_git_delta(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value.txt"],
        before,
        after,
        RUNNER._json_document_sha256(result),
    )
    (repo / "unattributed.txt").write_text("outside worker\n", encoding="utf-8")
    errors = RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    )
    assert any("current Git delta differs" in error for error in errors)


def test_filtered_workspace_hash_rejects_ignored_drift_between_jobs_and_at_audit(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("changed\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value.txt"],
        before,
        after,
        RUNNER._json_document_sha256(result),
    )
    RUNNER._validate_implementation_workspace_continuity(
        job, RUNNER.capture_snapshot(repo)
    )

    ignored = repo / "ignored/out-of-band.txt"
    ignored.parent.mkdir()
    ignored.write_text("not in git status\n", encoding="utf-8")
    assert "ignored/out-of-band.txt" not in RUNNER._git_changed_paths(repo)
    with pytest.raises(RUNNER.ContractError, match="workspace drifted"):
        RUNNER._validate_implementation_workspace_continuity(
            job, RUNNER.capture_snapshot(repo)
        )
    errors = RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    )
    assert any("last runner-validated" in error for error in errors)


def test_filtered_workspace_hash_ignores_empty_generated_directories(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    evidence_relative = "docs/epics/gd-001/implementation-evidence.yaml"
    before = RUNNER.capture_snapshot(repo)

    (repo / "plugins").mkdir()
    after_directory = RUNNER.capture_snapshot(repo)
    assert RUNNER._workspace_snapshot_sha256(
        before, evidence_relative
    ) == RUNNER._workspace_snapshot_sha256(after_directory, evidence_relative)

    (repo / "plugins/runtime.txt").write_text("unexpected\n", encoding="utf-8")
    after_file = RUNNER.capture_snapshot(repo)
    assert RUNNER._workspace_snapshot_sha256(
        before, evidence_relative
    ) != RUNNER._workspace_snapshot_sha256(after_file, evidence_relative)


def test_evidence_promotion_records_deleted_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").unlink()
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    evidence = RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value.txt"],
        before,
        after,
        RUNNER._json_document_sha256(result),
    )
    assert evidence["attributed_delta"] == [
        {"path": "src/value.txt", "state": "deleted", "sha256": None, "mode": None}
    ]
    assert RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    ) == []


def test_delivery_summary_is_not_promoted_as_audited_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["phase"] = "delivery_summary"
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("summary side effect\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    with pytest.raises(RUNNER.ContractError, match="does not publish"):
        RUNNER.promote_implementation_evidence(
            job,
            result,
            ["src/value.txt"],
            before,
            after,
            RUNNER._json_document_sha256(result),
        )


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_evidence_promotion_records_symlink_target_hash(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    before = RUNNER.capture_snapshot(repo)
    os.symlink("value.txt", repo / "src/value-link")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value-link"])
    evidence = RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value-link"],
        before,
        after,
        RUNNER._json_document_sha256(result),
    )
    row = evidence["attributed_delta"][0]
    assert row["state"] == "symlink"
    assert row["sha256"] == RUNNER._sha256_bytes(b"value.txt")
    assert RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    ) == []


def test_fresh_job_rejects_stale_runtime_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    _initialize(monkeypatch, repo, scope)
    job, job_path = _job(repo, scope)
    (job_path.parent / "provider-result.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(RUNNER, "provider_preflight", lambda provider, selected: {"executable": sys.executable, "version": "fake"})
    with pytest.raises(RUNNER.ContractError, match="stale outputs"):
        RUNNER.run_worker(SimpleNamespace(
            job=job_path, role="implementation", cwd=repo,
            result=Path(job["result_path"]), provider="codex",
            worker_profile="default", access="workspace-write",
        ))


def test_out_of_scope_write_is_preserved_as_interrupted_not_published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    run_path = _initialize(monkeypatch, repo, scope)
    job, job_path = _job(repo, scope)
    fake = _fake_writer(tmp_path / "fake.py")
    monkeypatch.setattr(RUNNER.scope_codegraph, "sync", lambda policy, root, prior: prior)
    monkeypatch.setattr(RUNNER, "provider_preflight", lambda provider, selected: {"executable": sys.executable, "version": "fake"})
    monkeypatch.setattr(
        RUNNER,
        "build_codex_command",
        lambda executable, selected, working_root, access, schema, output, codegraph: [
            sys.executable, str(fake), str(output), str(repo / "outside.txt"), "outside.txt", job["job_id"]
        ],
    )
    with pytest.raises(RUNNER.ContractError, match="outside write_scope"):
        RUNNER.run_worker(SimpleNamespace(
            job=job_path, role="implementation", cwd=repo, result=Path(job["result_path"]),
            provider="codex", worker_profile="default", access="workspace-write",
        ))
    run = yaml.safe_load(run_path.read_text())
    assert run["active_job"] is None
    assert run["completed_jobs"][0]["status"] == "interrupted"
    assert run["completed_jobs"][0]["changed_paths"] == ["outside.txt"]
    assert not Path(job["result_path"]).exists()


def test_recovery_publishes_only_when_current_snapshot_matches_recorded_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run_path, job, _ = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=True
    )
    row = RUNNER.recover_run(run_path)
    assert row["status"] == "completed"
    assert Path(job["result_path"]).is_file()


def test_explicit_recovery_revalidates_a_legacy_idle_interrupted_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run_path, job, _ = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=True
    )
    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    RUNNER._interrupt(
        run_path,
        run,
        "completed result has failed validations: ['optional probe']",
        ["src/value.txt"],
    )
    interrupted = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    interrupted["completed_jobs"][-1].pop("recovery")
    RUNNER.atomic_write_yaml(run_path, interrupted)

    row = RUNNER.recover_run(run_path, job["job_id"])

    assert row["status"] == "completed"
    final = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    assert [item["job_id"] for item in final["completed_jobs"]] == [job["job_id"]]
    assert Path(job["result_path"]).is_file()


def test_recovery_replays_an_already_promoted_evidence_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, run_path, job, after_path = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=True
    )
    active = yaml.safe_load(run_path.read_text(encoding="utf-8"))["active_job"]
    before = json.loads(Path(active["before_snapshot"]).read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    result = json.loads(Path(active["provider_result_path"]).read_text(encoding="utf-8"))
    RUNNER.atomic_write_json(Path(job["result_path"]), result)
    RUNNER.promote_implementation_evidence(
        job,
        result,
        ["src/value.txt"],
        before,
        after,
        RUNNER._sha256_file(Path(job["result_path"])),
    )

    row = RUNNER.recover_run(run_path)
    assert row["status"] == "completed"
    evidence = yaml.safe_load(
        (repo / job["implementation_evidence_path"]).read_text(encoding="utf-8")
    )
    assert [item["job_id"] for item in evidence["validated_jobs"]] == [job["job_id"]]


def test_recovery_rehashes_decisions_but_allows_artifacts_that_are_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run_path, job, _ = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=True, artifact_is_output=True
    )
    row = RUNNER.recover_run(run_path)
    assert row["status"] == "completed"
    assert Path(job["result_path"]).is_file()


def test_recovery_refuses_publication_after_post_snapshot_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, run_path, job, _ = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=True
    )
    (repo / "src/value.txt").write_text("later drift\n", encoding="utf-8")
    row = RUNNER.recover_run(run_path)
    assert row["status"] == "interrupted"
    assert "drifted" in row["reason"]
    assert not Path(job["result_path"]).exists()
    assert (run_path.parent / "jobs" / job["job_id"] / "recovery-current-snapshot.json").is_file()


def test_recovery_captures_missing_after_snapshot_and_never_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, run_path, job, after_path = _orphaned_write_run(
        monkeypatch, tmp_path, include_after=False
    )
    row = RUNNER.recover_run(run_path)
    assert row["status"] == "interrupted"
    assert "missing after snapshot" in row["reason"]
    assert after_path.is_file()
    assert not Path(job["result_path"]).exists()


def test_implementation_proof_rejects_failing_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    evidence = repo / "README.md"
    result = _result(job, [])
    result["payload"]["proof_evidence"] = [{
        "proof_id": "P-1", "command": "pytest", "exit_code": 0,
        "passed": 3, "failed": 1, "errors": 0, "skipped": 0,
        "evidence_path": "README.md", "evidence_sha256": RUNNER._sha256_file(evidence),
    }]
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    with pytest.raises(RUNNER.ContractError, match="did not pass cleanly"):
        RUNNER.validate_result(result, job, schema)


def test_completed_implementation_requires_exact_proof_ids_and_positive_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["required_proof_ids"] = ["P-1", "P-2"]
    result = _result(job, [])
    result["payload"]["proof_evidence"] = [{
        "proof_id": "P-1", "command": "pytest", "exit_code": 0,
        "passed": 1, "failed": 0, "errors": 0, "skipped": 0,
        "evidence_path": "README.md", "evidence_sha256": RUNNER._sha256_file(repo / "README.md"),
    }]
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    with pytest.raises(RUNNER.ContractError, match="proof IDs do not match"):
        RUNNER.validate_result(result, job, schema)
    job["required_proof_ids"] = ["P-1"]
    result["payload"]["proof_evidence"][0]["passed"] = 0
    with pytest.raises(RUNNER.ContractError, match="did not pass cleanly"):
        RUNNER.validate_result(result, job, schema)


def test_runner_promotes_proof_with_manifest_story_and_result_provenance(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    manifest_path = repo / "docs/epics/gd-001/delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["stories"] = [{"id": "STORY-1", "proof_ids": ["PROOF-1"]}]
    manifest["proofs"] = [{"id": "PROOF-1", "command": "pytest -q"}]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _command("git", "add", str(manifest_path.relative_to(repo)), cwd=repo)
    _command("git", "commit", "-m", "add proof plan", cwd=repo)

    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["required_proof_ids"] = ["PROOF-1"]
    before = RUNNER.capture_snapshot(repo)
    (repo / "src/value.txt").write_text("proved\n", encoding="utf-8")
    after = RUNNER.capture_snapshot(repo)
    result = _result(job, ["src/value.txt"])
    result["payload"]["proof_evidence"] = [
        {
            "proof_id": "PROOF-1",
            "command": "pytest -q",
            "exit_code": 0,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "evidence_path": "README.md",
            "evidence_sha256": RUNNER._sha256_file(repo / "README.md"),
        }
    ]
    result_sha = RUNNER._json_document_sha256(result)
    evidence = RUNNER.promote_implementation_evidence(
        job, result, ["src/value.txt"], before, after, result_sha
    )
    proof = evidence["stories"][0]["proofs"][0]
    assert evidence["stories"][0]["status"] == "verified"
    assert proof["story_id"] == "STORY-1"
    assert proof["source_job_id"] == job["job_id"]
    assert proof["source_result_sha256"] == result_sha
    assert RUNNER.verify_implementation_attribution(
        repo / "docs/epics/gd-001", repo
    ) == []


def test_proof_evidence_rejects_tmp_debug_even_when_hash_matches(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["required_proof_ids"] = ["P-1"]
    evidence = repo / "tmp_debug/proof.txt"
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text("1 passed", encoding="utf-8")
    result = _result(job, [])
    result["payload"]["proof_evidence"] = [{
        "proof_id": "P-1", "command": "pytest", "exit_code": 0,
        "passed": 1, "failed": 0, "errors": 0, "skipped": 0,
        "evidence_path": "tmp_debug/proof.txt", "evidence_sha256": RUNNER._sha256_file(evidence),
    }]
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    with pytest.raises(RUNNER.ContractError, match="temporary tmp_debug"):
        RUNNER.validate_result(result, job, schema)


def test_refinement_result_paths_and_decisions_are_job_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope, role="refinement")
    job["decision_refs"] = [{"id": "PDR-1", "path": "README.md", "sha256": RUNNER._sha256_file(repo / "README.md")}]
    result = _result(job, ["src/value.txt"])
    result["payload"]["decision_refs"] = ["PDR-2"]
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    with pytest.raises(RUNNER.ContractError, match="decisions absent"):
        RUNNER.validate_result(result, job, schema)
    result["payload"]["decision_refs"] = ["PDR-1"]
    result["payload"]["authored_artifacts"] = ["outside.txt"]
    with pytest.raises(RUNNER.ContractError, match="outside write_scope"):
        RUNNER.validate_result(result, job, schema)


def test_decision_refs_are_rehashed_before_publication(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    job["decision_refs"] = [{"id": "PDR-1", "path": "README.md", "sha256": RUNNER._sha256_file(repo / "README.md")}]
    (repo / "README.md").write_text("changed authority\n", encoding="utf-8")
    with pytest.raises(RUNNER.ContractError, match="decision source changed"):
        RUNNER._validate_decision_refs_current(job)

def test_transport_contract_fails_loud_instead_of_repairing(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    job, _ = _job(repo, scope)
    result = _result(job, [])
    result["question_discovery"] = None
    schema = json.loads((scope / "config/worker-result.schema.json").read_text())
    with pytest.raises(RUNNER.ContractError):
        RUNNER.validate_result(result, job, schema)
    assert "question_discovery" in result


def test_claude_parser_records_raw_model_usage_and_explicit_fallback(tmp_path: Path) -> None:
    stdout = tmp_path / "claude.json"
    result = {"schema_version": 2}
    usage = {"claude-opus-5": {"inputTokens": 12}}
    stdout.write_text(json.dumps({"structured_output": result, "modelUsage": usage, "fallback_used": True}))
    parsed, parsed_usage, fallback = RUNNER._provider_result("claude", tmp_path / "unused", stdout)
    assert parsed == result
    assert parsed_usage == usage
    assert fallback is True


def test_codex_command_uses_openai_compatible_result_schema(tmp_path: Path) -> None:
    canonical = json.loads(
        (REPO_ROOT / "src_shared/config/worker-result.schema.json").read_text()
    )
    schema_path = tmp_path / "codex-output-schema.json"
    RUNNER.atomic_write_json(schema_path, RUNNER.codex_output_schema(canonical))
    command = RUNNER.build_codex_command(
        "codex",
        {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        tmp_path,
        "read-only",
        schema_path,
        tmp_path / "result.json",
        {},
    )
    passed_path = Path(command[command.index("--output-schema") + 1])
    assert passed_path == schema_path
    schema = json.loads(passed_path.read_text())

    unsupported = {
        "allOf",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "not",
        "oneOf",
        "then",
        "uniqueItems",
    }

    def assert_supported(value: object) -> None:
        if isinstance(value, dict):
            assert not unsupported.intersection(value)
            if "const" in value:
                expected = (
                    "boolean"
                    if isinstance(value["const"], bool)
                    else "integer"
                    if isinstance(value["const"], int)
                    else "number"
                    if isinstance(value["const"], float)
                    else "string"
                    if isinstance(value["const"], str)
                    else "null"
                )
                assert value.get("type") == expected
            if "enum" in value:
                assert "type" in value
            if value.get("type") == "object" and "properties" in value:
                assert value.get("additionalProperties") is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                assert_supported(child)
        elif isinstance(value, list):
            for child in value:
                assert_supported(child)

    assert_supported(schema)


def test_claude_command_adapts_schema_without_mutating_canonical_contract() -> None:
    canonical = json.loads(
        (REPO_ROOT / "src_shared/config/worker-result.schema.json").read_text()
    )
    before = json.dumps(canonical, sort_keys=True)
    command = RUNNER.build_claude_command(
        "claude", {"model": "claude-opus-5", "reasoning_effort": "high", "permission_mode": "dontAsk"},
        {"required_validations": []}, canonical, "read-only", {},
    )
    projected = json.loads(command[command.index("--json-schema") + 1])
    assert "$schema" not in projected
    assert not {"allOf", "anyOf", "oneOf"}.intersection(projected)
    Draft7Validator.check_schema(projected)
    assert json.dumps(canonical, sort_keys=True) == before
    assert projected["properties"]["changed_paths"]["uniqueItems"] is True


@pytest.mark.parametrize("role", ["refinement", "implementation", "diagnostic"])
@pytest.mark.parametrize("case,valid", [
    ("completed", True), ("needs_user", True), ("blocked", True), ("failed", True),
    ("completed_with_question", False), ("needs_user_without_question", False),
    ("blocked_without_issue", False), ("failed_with_minor_only", False),
    ("duplicate_changed_paths", False), ("extra_property", False),
    ("invalid_payload", False), ("questions_not_array", False),
])
def test_claude_transport_and_local_validation_preserve_result_constraints(role: str, case: str, valid: bool) -> None:
    canonical = json.loads(
        (REPO_ROOT / "src_shared/config/worker-result.schema.json").read_text()
    )
    payloads = {
        "refinement": {"kind": "refinement", "authored_artifacts": [], "decision_refs": []},
        "implementation": {"kind": "implementation", "notes": "done", "proof_evidence": []},
        "diagnostic": {"kind": "diagnostic", "cause": "known", "evidence": [], "recommended_action": "retry"},
    }
    result = {
        "schema_version": 2, "job_id": "schema-check", "status": "completed", "summary": "done",
        "changed_paths": [], "validations": [], "questions": [], "issues": [], "payload": payloads[role],
    }
    if case in {"needs_user", "blocked", "failed"}:
        result["status"] = case
    if case in {"needs_user", "completed_with_question"}:
        result["questions"] = [{"id": "Q1", "question": "Which?", "reason": "Missing input", "evidence": ["docs/spec.md"]}]
    if case in {"blocked", "failed"}:
        result["issues"] = [{"severity": "blocking", "message": "Missing input", "evidence": ["docs/spec.md"]}]
    if case == "needs_user_without_question":
        result["status"] = "needs_user"
    if case == "blocked_without_issue":
        result["status"] = "blocked"
    if case == "failed_with_minor_only":
        result["status"] = "failed"
        result["issues"] = [{"severity": "minor", "message": "Minor", "evidence": ["docs/spec.md"]}]
    if case == "duplicate_changed_paths":
        result["changed_paths"] = ["src/a.py", "src/a.py"]
    if case == "extra_property":
        result["approved"] = True
    if case == "invalid_payload":
        result["payload"] = {"kind": role}
    if case == "questions_not_array":
        result["questions"] = None
    assert Draft202012Validator(canonical).is_valid(result) is valid
    local_only = case in {
        "completed_with_question", "needs_user_without_question", "blocked_without_issue", "failed_with_minor_only",
    }
    assert Draft7Validator(RUNNER.claude_output_schema(canonical)).is_valid(result) is (valid or local_only)
    if not valid:
        # The runner must reject malformed responses even when the API's schema
        # subset cannot express the status-dependent rules.
        with pytest.raises(RUNNER.ContractError, match="worker result"):
            RUNNER.validate_result(result, {}, canonical)


def test_status_reports_live_worker_as_active(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    run_path = repo / "tmp_debug/scope-runs/gd-001/implement/run.yaml"
    run_path.parent.mkdir(parents=True)
    now = RUNNER.utc_now()
    run = {
        "schema_version": 2, "epic_id": "gd-001", "command": "implement",
        "repository_root": str(repo), "working_root": str(repo), "worker_profile": "default",
        "created_at": now, "updated_at": now, "codegraph": {}, "completed_jobs": [],
        "active_job": {"job_id": "job", "runner_process": RUNNER.process_identity(), "provider_process": None, "provider_process_group": None},
    }
    run_path.write_text(yaml.safe_dump(run, sort_keys=False))
    assert RUNNER.classify_run(run_path)["status"] == "active"


def test_host_process_supervisor_death_maps_observed_provider_state_to_recovery(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    run_path = repo / "tmp_debug/scope-runs/gd-001/implement/run.yaml"
    run_path.parent.mkdir(parents=True)
    now = RUNNER.utc_now()
    dead = {"pid": 99999999, "create_time": 1.0}
    run = {
        "schema_version": 2, "epic_id": "gd-001", "command": "implement",
        "repository_root": str(repo), "working_root": str(repo), "worker_profile": "default",
        "created_at": now, "updated_at": now, "codegraph": {}, "completed_jobs": [],
        "active_job": {"job_id": "job", "runner_process": dead, "provider_process": dead, "provider_process_group": None},
    }
    run_path.write_text(yaml.safe_dump(run, sort_keys=False))
    assert RUNNER.classify_run(run_path)["status"] == "recovery_required"


def test_cancel_terminates_provider_process_tree_identity(tmp_path: Path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=os.name != "nt")
    identity = RUNNER.process_identity(psutil.Process(process.pid))
    active = {
        "provider_process": identity,
        "provider_descendants": [],
        "provider_process_group": {"pgid": os.getpgid(process.pid)} if os.name != "nt" else {"supported": False},
    }
    RUNNER._terminate_lifecycle(active, 0.2)
    process.wait(timeout=3)
    assert RUNNER.identity_state(identity) == "dead"


def test_cancel_requires_matching_active_job_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    run_path = repo / "tmp_debug/scope-runs/gd-001/implement/run.yaml"
    run_path.parent.mkdir(parents=True)
    now = RUNNER.utc_now()
    dead = {"pid": 99999999, "create_time": 1.0}
    job_dir = run_path.parent / "jobs/job-current"
    job_dir.mkdir(parents=True)
    run = {
        "schema_version": 2, "epic_id": "gd-001", "command": "implement",
        "repository_root": str(repo), "working_root": str(repo), "worker_profile": "default",
        "created_at": now, "updated_at": now, "codegraph": {}, "completed_jobs": [],
        "active_job": {
            "job_id": "job-current", "runner_process": dead, "provider_process": dead,
            "provider_process_group": None, "provider_descendants": [], "access": "read-only",
            "cancellation_path": str(job_dir / "cancel.yaml"),
        },
    }
    run_path.write_text(yaml.safe_dump(run, sort_keys=False))
    with pytest.raises(RUNNER.ContractError, match="stale"):
        RUNNER.cancel_run(run_path, "job-old", "redirect")
    assert not (job_dir / "cancel.yaml").exists()


def test_provider_exit_rechecks_cancel_marker_before_exit_classification(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    run_path = repo / "tmp_debug/run.yaml"
    cancel = repo / "tmp_debug/cancel.yaml"
    stdout = repo / "tmp_debug/stdout"
    stderr = repo / "tmp_debug/stderr"
    script = tmp_path / "cancel_then_exit.py"
    script.write_text(
        "from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text('cancelled')\n",
        encoding="utf-8",
    )
    active = {
        "job_id": "job", "provider_process": None, "provider_process_group": None,
        "provider_descendants": [],
    }
    run = {"active_job": active, "updated_at": RUNNER.utc_now()}
    selected = {
        "timeout_seconds": 5, "termination_grace_seconds": 0.2,
        "normal_exit_grace_seconds": 0.1, "heartbeat_interval_seconds": 1,
        "poll_interval_seconds": 0.05,
    }
    code, timed_out, cancelled = RUNNER.execute_provider(
        [sys.executable, str(script), str(cancel)], "",
        working_root=repo, stdout_path=stdout, stderr_path=stderr,
        run_path=run_path, run=run, cancellation_path=cancel, selected=selected,
    )
    assert code == 0
    assert timed_out is False
    assert cancelled is True


def test_codegraph_sync_is_not_called_for_refinement_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    scope = _scope(tmp_path / "scope")
    _initialize(monkeypatch, repo, scope, command="epic_refine")
    called = []
    monkeypatch.setattr(RUNNER.scope_codegraph, "sync", lambda *args: called.append(args))
    assert called == []


def test_v3_failed_checkpoint_retains_changes_and_debugging_budget_survives_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shlex
    repo = _repo(tmp_path / 'repo')
    scope = _scope(tmp_path / 'scope')
    manifest_path = repo / 'docs/epics/gd-001/delivery-manifest.yaml'
    argv = [sys.executable, '-c', "print('1 failed in 0.01s')"]
    manifest = {'schema_version': 3, 'epic_id': 'gd-001', 'stories': [{'id': 'S1', 'depends_on': [], 'proof_ids': ['P1']}],
                'proofs': [{'id': 'P1', 'command': shlex.join(argv), 'level': 'unit', 'execution': {'argv': argv, 'cwd': '.', 'parser': 'pytest', 'environment': [], 'fresh': False}}]}
    manifest_path.write_text(yaml.safe_dump(manifest))
    _command('git', 'add', '.', cwd=repo)
    _command('git', 'commit', '-m', 'proof contract', cwd=repo)
    run_path = _initialize(monkeypatch, repo, scope)
    template, _ = _job(repo, scope)
    fake = _fake_writer(tmp_path / 'author.py')
    fake.write_text(fake.read_text().replace("write_text('after\\n'", "write_text(job_id"))
    monkeypatch.setattr(RUNNER.scope_codegraph, 'sync', lambda policy, root, prior: prior)
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda provider, selected: {'executable': sys.executable, 'version': 'fixture'})
    for index in range(4):
        job = {**template, 'job_id': f'group-{index}', 'phase': 'story' if index == 0 else 'debugging', 'required_proof_ids': ['P1'], 'story_ids': ['S1']}
        directory = run_path.parent / 'jobs' / job['job_id']
        directory.mkdir()
        job['result_path'] = str(directory / 'result.json')
        job_path = directory / 'job.yaml'
        job_path.write_text(yaml.safe_dump(job))
        monkeypatch.setattr(RUNNER, 'build_codex_command', lambda executable, selected, working_root, access, schema, output, codegraph:
                            [sys.executable, str(fake), str(output), str(repo / 'src/value.txt'), 'src/value.txt', job['job_id']])
        args = SimpleNamespace(job=job_path, role='implementation', cwd=repo, result=Path(job['result_path']), provider='codex', worker_profile='default', access='workspace-write')
        if index < 3:
            assert RUNNER.run_worker(args) == 1
            run = yaml.safe_load(run_path.read_text())
            assert run['active_job'] is None
            assert run['completed_jobs'][-1]['status'] == 'verification_failed'
            assert (repo / 'src/value.txt').read_text() == job['job_id']
            evidence = yaml.safe_load((repo / job['implementation_evidence_path']).read_text())
            assert evidence['stories'][0]['status'] == 'pending'
            assert RUNNER.recover_run(run_path)['status'] == 'idle'
        else:
            with pytest.raises(RUNNER.ContractError, match='budget exhausted'):
                RUNNER.run_worker(args)
    assert yaml.safe_load(run_path.read_text())['pre_audit_debugging_jobs'] == 2
    assert (repo / 'src/value.txt').read_text() == 'group-2'

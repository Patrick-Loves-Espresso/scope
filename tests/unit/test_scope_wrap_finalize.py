from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

from filelock import FileLock
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "src_shared/scripts/scope-wrap-finalize.py"
POLICY = REPO_ROOT / "src_shared/config/wrap-policy.yaml"
AUDIT_POLICY = REPO_ROOT / "src_shared/config/audit-policy.yaml"


def _load_module():
    spec = importlib.util.spec_from_file_location("scope_wrap_finalize_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FINALIZER = _load_module()


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _PassingValidator:
    def __init__(self, *_args, **_kwargs):
        pass

    def validate(self) -> list[str]:
        return [FINALIZER.FINGERPRINT_DRIFT]


@pytest.fixture(autouse=True)
def _audit_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        FINALIZER,
        "_audit_module",
        lambda: SimpleNamespace(AuditValidator=_PassingValidator),
    )


def _arguments(command: str, epic: Path, run: Path, main: Path | None = None, **values):
    common = {
        "epic_dir": epic,
        "run": run,
        "policy": POLICY,
        "audit_policy": AUDIT_POLICY,
    }
    if main is not None:
        common["main_root"] = main
    common.update(values)
    return argparse.Namespace(**common)


def _fixture(
    tmp_path: Path,
    *,
    epic_name: str = "E-001",
    manifest_id: str = "E-001",
    extra_baseline_file: bool = False,
) -> tuple[Path, Path, Path, Path]:
    main = tmp_path / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.name", "Scope Test")
    _git(main, "config", "user.email", "scope@example.test")
    (main / ".gitignore").write_text(
        "tmp_debug/\n.codegraph/\nignored/\n", encoding="utf-8"
    )
    (main / "src").mkdir()
    (main / "src/value.py").write_text("VALUE = 1\n", encoding="utf-8")
    epic = main / "docs/epics" / epic_name
    epic.mkdir(parents=True)
    (epic / "details.md").write_text("# E-001\n", encoding="utf-8")
    if extra_baseline_file:
        (epic / "obsolete.md").write_text("obsolete\n", encoding="utf-8")
    (epic / "delivery-manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "epic_id": manifest_id}, sort_keys=False),
        encoding="utf-8",
    )
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "base")
    _git(main, "branch", "epic/E-001")

    work = tmp_path / "wip/E-001"
    work.parent.mkdir()
    _git(main, "worktree", "add", "-q", str(work), "epic/E-001")
    epic = work / "docs/epics" / epic_name
    (work / "src/value.py").write_text("VALUE = 2\n", encoding="utf-8")
    (epic / "implementation-evidence.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (epic / "audit-findings.yaml").write_text(
        "schema_version: 1\nepic_id: E-001\nfindings: []\n", encoding="utf-8"
    )
    (epic / "epic_audit.md").write_text("# PASS\n", encoding="utf-8")
    attempt_dir = epic / "reviews/audit-001"
    attempt_dir.mkdir(parents=True)
    fingerprint = FINALIZER.scope_fingerprint.audit_fingerprint(epic, work)
    attempt = {
        "schema_version": 1,
        "epic_id": "E-001",
        "attempt_id": "audit-001",
        "status": "pass",
        "decision": {"outcome": "pass", "reason": "verified"},
        "repository_fingerprint": fingerprint,
        "boundary_sha256": "sha256:" + "1" * 64,
    }
    (attempt_dir / "audit-attempt.yaml").write_text(
        yaml.safe_dump(attempt, sort_keys=False), encoding="utf-8"
    )

    summary = epic / "implementation-summary.md"
    summary.write_text("# Delivery summary\n", encoding="utf-8")
    summary_relative = summary.relative_to(work).as_posix()
    job_id = "E-001-delivery-summary-001"
    job_dir = main / f"tmp_debug/scope-runs/E-001/implement/jobs/{job_id}"
    job_dir.mkdir(parents=True)
    result = {
        "schema_version": 2,
        "job_id": job_id,
        "status": "completed",
        "summary": "summary written",
        "changed_paths": [summary_relative],
        "validations": [],
        "questions": [],
        "issues": [],
        "payload": {"kind": "implementation", "notes": "", "proof_evidence": []},
    }
    result_path = job_dir / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    snapshot = {
        "schema_version": 1,
        "head": _git(work, "rev-parse", "HEAD"),
        "entries": [
            {
                "path": summary_relative,
                "kind": "file",
                "sha256": FINALIZER.scope_fingerprint.file_sha256(summary),
                "mode": 0o644,
                "index": "",
            }
        ],
    }
    (job_dir / "after-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    run = main / "tmp_debug/scope-runs/E-001/implement/run.yaml"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "epic_id": "E-001",
                "command": "implement",
                "repository_root": str(main),
                "working_root": str(work),
                "active_job": None,
                "completed_jobs": [
                    {
                        "job_id": job_id,
                        "phase": "delivery_summary",
                        "status": "completed",
                        "result_path": str(result_path),
                        "result_sha256": FINALIZER.scope_fingerprint.file_sha256(result_path),
                        "after_snapshot_sha256": FINALIZER.scope_fingerprint.file_sha256(
                            job_dir / "after-snapshot.json"
                        ),
                        "changed_paths": [summary_relative],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return main, work, epic, run


def _seal(main: Path, work: Path, epic: Path, run: Path) -> dict:
    return FINALIZER.seal_delivery(_arguments("seal", epic, run))


def _verify(work: Path, epic: Path) -> dict:
    return FINALIZER.verify_delivery(
        argparse.Namespace(
            epic_dir=epic,
            repo_root=work,
            policy=POLICY,
            audit_policy=AUDIT_POLICY,
        )
    )


def _refresh_audit_fingerprint(work: Path, epic: Path) -> None:
    attempt_path = epic / "reviews/audit-001/audit-attempt.yaml"
    attempt = yaml.safe_load(attempt_path.read_text(encoding="utf-8"))
    attempt["repository_fingerprint"] = FINALIZER.scope_fingerprint.audit_fingerprint(
        epic,
        work,
        extra_excluded=(
            (epic / "implementation-summary.md").relative_to(work).as_posix(),
            (epic / "delivery-seal.yaml").relative_to(work).as_posix(),
        ),
    )
    attempt_path.write_text(yaml.safe_dump(attempt, sort_keys=False), encoding="utf-8")


def test_seal_is_byte_idempotent_and_verify_survives_pruned_runtime(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    first = _seal(main, work, epic, run)
    seal = epic / "delivery-seal.yaml"
    content = seal.read_bytes()

    second = _seal(main, work, epic, run)
    assert first["status"] == "sealed"
    assert second["status"] == "already_sealed"
    assert seal.read_bytes() == content
    assert set(yaml.safe_load(content)) == {
        "schema_version",
        "epic_id",
        "active_epic_path",
        "implemented_epic_path",
        "audit",
        "summary",
        "summary_job",
        "workspace",
    }

    shutil.rmtree(main / "tmp_debug")
    shutil.rmtree(work / "tmp_debug")
    verified = FINALIZER.verify_delivery(
        argparse.Namespace(
            epic_dir=epic,
            repo_root=work,
            policy=POLICY,
            audit_policy=AUDIT_POLICY,
        )
    )
    assert verified["status"] == "verified"
    assert not (work / "tmp_debug").exists()


def test_verify_cli_writes_neither_installed_bytecode_nor_git_index(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    fsmonitor_marker = tmp_path / "fsmonitor-ran"
    fsmonitor = tmp_path / "fsmonitor"
    fsmonitor.write_text(
        f"#!/bin/sh\ntouch '{fsmonitor_marker}'\necho\n", encoding="utf-8"
    )
    fsmonitor.chmod(0o755)
    _git(work, "config", "core.fsmonitor", str(fsmonitor))
    installed = tmp_path / "installed-scripts"
    shutil.copytree(
        REPO_ROOT / "src_shared/scripts",
        installed,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (installed / "audit-artifacts.py").write_text(
        "class AuditValidator:\n"
        "    def __init__(self, *args, **kwargs): pass\n"
        "    def validate(self): return ['repository fingerprint changed after audit preparation']\n",
        encoding="utf-8",
    )
    installed_before = {
        path.relative_to(installed).as_posix(): path.read_bytes()
        for path in installed.rglob("*")
        if path.is_file()
    }
    index = Path(_git(work, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = work / index
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)

    result = subprocess.run(
        [
            sys.executable,
            str(installed / "scope-wrap-finalize.py"),
            "verify",
            str(epic),
            "--repo-root",
            str(work),
            "--policy",
            str(POLICY),
            "--audit-policy",
            str(AUDIT_POLICY),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert installed_before == {
        path.relative_to(installed).as_posix(): path.read_bytes()
        for path in installed.rglob("*")
        if path.is_file()
    }
    assert not list(installed.rglob("__pycache__"))
    assert index_before == (index.read_bytes(), index.stat().st_mtime_ns)
    assert not fsmonitor_marker.exists()


def test_seal_rejects_workspace_drift_and_forged_summary_job(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    (work / "outside.txt").write_text("drift\n", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="differs from the PASS audit"):
        _seal(main, work, epic, run)
    (work / "outside.txt").unlink()

    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    run_doc["completed_jobs"][-1]["phase"] = "story"
    run.write_text(yaml.safe_dump(run_doc, sort_keys=False), encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="delivery-summary write"):
        _seal(main, work, epic, run)


def test_seal_requires_the_runner_bound_after_snapshot(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    run_doc["completed_jobs"][-1].pop("after_snapshot_sha256")
    run.write_text(yaml.safe_dump(run_doc, sort_keys=False), encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="resume implement to republish"):
        _seal(main, work, epic, run)

    main, work, epic, run = _fixture(tmp_path / "tampered")
    snapshot = next(run.parent.glob("jobs/*/after-snapshot.json"))
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="after-snapshot hash is stale"):
        _seal(main, work, epic, run)


def test_modes_and_canonical_seal_are_bound(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path / "audit-mode")
    _git(work, "config", "core.filemode", "false")
    (work / "src/value.py").chmod(0o755)
    with pytest.raises(FINALIZER.WrapError, match="differs from the PASS audit"):
        _seal(main, work, epic, run)

    main, work, epic, run = _fixture(tmp_path / "verify-mode")
    _seal(main, work, epic, run)
    _git(work, "config", "core.filemode", "false")
    (work / "src/value.py").chmod(0o755)
    with pytest.raises(FINALIZER.WrapError, match="workspace differs"):
        _verify(work, epic)

    (work / "src/value.py").chmod(0o644)
    seal = epic / "delivery-seal.yaml"
    seal.chmod(0o755)
    with pytest.raises(FINALIZER.WrapError, match="seal mode"):
        _verify(work, epic)


def test_verify_rejects_noncanonical_seal_fields(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    seal = epic / "delivery-seal.yaml"
    seal.write_text(seal.read_text(encoding="utf-8") + "tampered: true\n", encoding="utf-8")

    with pytest.raises(FINALIZER.WrapError, match="fields are incomplete or unsupported"):
        _verify(work, epic)


def test_tracked_path_matching_ignore_rules_is_not_rejected(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    exclude = Path(_git(work, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = work / exclude
    exclude.write_text(exclude.read_text(encoding="utf-8") + "src/value.py\n", encoding="utf-8")
    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    run_doc["completed_jobs"].insert(
        0,
        {
            "job_id": "story-001",
            "phase": "story",
            "status": "completed",
            "changed_paths": ["src/value.py"],
        },
    )
    run.write_text(yaml.safe_dump(run_doc, sort_keys=False), encoding="utf-8")

    assert _seal(main, work, epic, run)["status"] == "sealed"


def test_seal_recovers_from_an_interrupted_atomic_replace(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    temporary_dir = work / "tmp_debug/scope-wrap"
    code = """
import importlib.util
import os
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location("interrupted_wrap", Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.os.replace = lambda *_args: os._exit(23)
module._atomic_write(Path(sys.argv[2]), b"partial", temporary_directory=Path(sys.argv[3]))
"""
    interrupted = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPT), str(epic / "delivery-seal.yaml"), str(temporary_dir)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert interrupted.returncode == 23
    assert (temporary_dir / ".delivery-seal.yaml.scope-tmp").is_file()

    assert _seal(main, work, epic, run)["status"] == "sealed"
    assert not (temporary_dir / ".delivery-seal.yaml.scope-tmp").exists()


def test_seal_rejects_symlinked_runtime_directory_without_escape(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "tmp_debug").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FINALIZER.scope_git.GitError, match="must not be a symlink"):
        _seal(main, work, epic, run)
    assert not (outside / "scope-mutation.lock").exists()


def test_seal_rejects_ignored_attributed_paths_and_wrong_worktree_branch(
    tmp_path: Path,
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    ignored = work / "ignored/cache.txt"
    ignored.parent.mkdir()
    ignored.write_text("cache\n", encoding="utf-8")
    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    run_doc["completed_jobs"].insert(
        0,
        {
            "job_id": "story-001",
            "phase": "story",
            "status": "completed",
            "changed_paths": ["ignored/cache.txt"],
        },
    )
    run.write_text(yaml.safe_dump(run_doc, sort_keys=False), encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="ignored worker-attributed"):
        _seal(main, work, epic, run)

    ignored.unlink()
    _git(work, "switch", "-q", "-c", "wrong-branch")
    with pytest.raises(FINALIZER.WrapError, match="must be on branch epic/E-001"):
        _seal(main, work, epic, run)


def test_seal_and_verify_reject_tampering_and_symlinks(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    summary = epic / "implementation-summary.md"
    summary.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="summary differs"):
        FINALIZER.verify_delivery(
            argparse.Namespace(
                epic_dir=epic,
                repo_root=work,
                policy=POLICY,
                audit_policy=AUDIT_POLICY,
            )
        )
    summary.unlink()
    outside = tmp_path / "outside-summary.md"
    outside.write_text("outside\n", encoding="utf-8")
    summary.symlink_to(outside)
    with pytest.raises(FINALIZER.WrapError, match="symlinked sealed summary"):
        FINALIZER.verify_delivery(
            argparse.Namespace(
                epic_dir=epic,
                repo_root=work,
                policy=POLICY,
                audit_policy=AUDIT_POLICY,
            )
        )


def test_verify_rejects_audit_tamper_and_latest_non_pass_attempt(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    attempt = epic / "reviews/audit-001/audit-attempt.yaml"
    attempt.write_text(attempt.read_text(encoding="utf-8") + "tampered: true\n", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="audit attempt differs"):
        FINALIZER.verify_delivery(
            argparse.Namespace(
                epic_dir=epic,
                repo_root=work,
                policy=POLICY,
                audit_policy=AUDIT_POLICY,
            )
        )

    main, work, epic, run = _fixture(tmp_path / "later")
    later = epic / "reviews/audit-002/audit-attempt.yaml"
    later.parent.mkdir()
    later.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "epic_id": "E-001",
                "attempt_id": "audit-002",
                "status": "fail",
                "decision": {"outcome": "fail"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(FINALIZER.WrapError, match="latest audit attempt is not PASS"):
        _seal(main, work, epic, run)


def test_verify_rejects_a_later_failed_audit_created_after_sealing(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    later = epic / "reviews/audit-002/audit-attempt.yaml"
    later.parent.mkdir()
    later.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "epic_id": "E-001",
                "attempt_id": "audit-002",
                "status": "fail",
                "decision": {"outcome": "fail"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(FINALIZER.WrapError, match="workspace differs from the exact sealed delivery"):
        _verify(work, epic)


def test_seal_refuses_busy_worktree(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    lock_path = work / "tmp_debug/scope-mutation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    with lock:
        with pytest.raises(FINALIZER.scope_git.GitError, match="busy"):
            _seal(main, work, epic, run)


def test_seal_refuses_cross_run_active_job(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    other = main / "tmp_debug/scope-runs/E-999/audit_epic/run.yaml"
    other.parent.mkdir(parents=True)
    other.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "epic_id": "E-999",
                "command": "audit_epic",
                "working_root": str(work),
                "active_job": {"job_id": "active"},
                "completed_jobs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(FINALIZER.WrapError, match="active Scope job"):
        _seal(main, work, epic, run)


def test_prepare_archives_and_stages_exact_sealed_tree_idempotently(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    hook_marker = tmp_path / "post-index-change-ran"
    hook = main / ".git/hooks/post-index-change"
    hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    assert not hook_marker.exists()
    hook.unlink()
    archived = work / "docs/epics/_implemented/E-001"

    assert prepared["status"] == "prepared"
    assert archived.is_dir()
    assert not epic.exists()
    assert _git(work, "write-tree") == prepared["staged_tree"]
    assert "src/value.py" in _git(work, "diff", "--cached", "--name-only")

    repeated = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    assert repeated["status"] == "already_prepared"
    assert repeated["staged_tree"] == prepared["staged_tree"]


def test_prepare_refreshes_runtime_state_after_clean_main_drift(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    first = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    (main / "main-update.txt").write_text("new main\n", encoding="utf-8")
    _git(main, "add", "main-update.txt")
    _git(main, "commit", "-q", "-m", "main update")

    refreshed = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))

    assert refreshed["status"] == "prepared"
    assert refreshed["main_head"] != first["main_head"]
    state = yaml.safe_load((run.parent / "wrap-preparation.yaml").read_text(encoding="utf-8"))
    assert state["main_head"] == refreshed["main_head"]


def test_prepare_resumes_after_directory_move_before_state_write(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    archived = work / "docs/epics/_implemented/E-001"
    archived.parent.mkdir(parents=True)
    epic.rename(archived)

    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    assert prepared["status"] == "prepared"
    assert archived.is_dir()


def test_prepare_supports_audited_internal_rename_and_deletion(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(tmp_path, extra_baseline_file=True)
    (epic / "details.md").rename(epic / "renamed.md")
    (epic / "obsolete.md").unlink()
    _refresh_audit_fingerprint(work, epic)
    _seal(main, work, epic, run)

    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    assert (archived / "renamed.md").is_file()
    assert not (archived / "details.md").exists()
    assert not (archived / "obsolete.md").exists()
    assert FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))["status"] == (
        "already_prepared"
    )
    assert prepared["staged_tree"] == _git(work, "write-tree")


def test_prepare_preserves_descriptive_epic_directory_and_casefold_id(tmp_path: Path) -> None:
    main, work, epic, run = _fixture(
        tmp_path, epic_name="e-001-descriptive-epic", manifest_id="e-001"
    )
    _seal(main, work, epic, run)

    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    assert prepared["archived_epic_path"] == (
        "docs/epics/_implemented/e-001-descriptive-epic"
    )
    assert prepared["main_branch"] == "main"


def test_prepare_does_not_stage_injected_unrelated_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    original = FINALIZER._stage_prepared
    unrelated = work / "unrelated.txt"

    def inject(*args, **kwargs):
        unrelated.write_text("do not stage\n", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(FINALIZER, "_stage_prepared", inject)
    with pytest.raises(FINALIZER.WrapError, match="workspace paths differ"):
        FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))

    assert unrelated.read_text(encoding="utf-8") == "do not stage\n"
    assert "unrelated.txt" not in _git(work, "diff", "--cached", "--name-only")


def test_commit_merge_uses_approved_tree_and_is_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    monkeypatch.setattr(
        FINALIZER, "_codegraph", lambda *_args, **_kwargs: {"status": "ready"}
    )
    args = _arguments(
        "commit-merge",
        archived,
        run,
        main,
        approved_staged_tree=prepared["staged_tree"],
        approved_main_head=prepared["main_head"],
        approved_main_branch="main",
        codegraph_policy=None,
    )
    merged = FINALIZER.commit_merge(args)
    repeated = FINALIZER.commit_merge(args)

    assert merged["status"] == "merged"
    assert repeated["status"] == "already_merged"
    assert _git(main, "show", "-s", "--format=%P", merged["merge_commit"]).split() == [
        prepared["main_head"],
        merged["closure_commit"],
    ]
    assert _git(main, "show", "-s", "--format=%s", merged["merge_commit"]) == (
        "merge(E-001): complete epic"
    )
    (main / "dirty-after-merge.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="main root must be clean"):
        FINALIZER.commit_merge(args)


def test_commit_merge_recovers_when_codegraph_fails_after_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    args = _arguments(
        "commit-merge",
        archived,
        run,
        main,
        approved_staged_tree=prepared["staged_tree"],
        approved_main_head=prepared["main_head"],
        approved_main_branch="main",
        codegraph_policy=None,
    )
    calls = 0

    def flaky_codegraph(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated CodeGraph failure")
        return {"status": "ready"}

    monkeypatch.setattr(FINALIZER, "_codegraph", flaky_codegraph)
    with pytest.raises(RuntimeError, match="CodeGraph failure"):
        FINALIZER.commit_merge(args)
    merged_head = _git(main, "rev-parse", "HEAD")

    recovered = FINALIZER.commit_merge(args)
    assert recovered["status"] == "already_merged"
    assert recovered["merge_commit"] == merged_head
    assert recovered["codegraph"] == {"status": "ready"}


def test_commit_merge_rejects_unapproved_tree_or_main_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    monkeypatch.setattr(
        FINALIZER, "_codegraph", lambda *_args, **_kwargs: {"status": "ready"}
    )
    args = _arguments(
        "commit-merge",
        archived,
        run,
        main,
        approved_staged_tree="f" * 40,
        approved_main_head=prepared["main_head"],
        approved_main_branch="main",
        codegraph_policy=None,
    )
    with pytest.raises(FINALIZER.WrapError, match="approval does not match"):
        FINALIZER.commit_merge(args)

    args.approved_staged_tree = prepared["staged_tree"]
    args.approved_main_branch = "other"
    with pytest.raises(FINALIZER.WrapError, match="approval does not match"):
        FINALIZER.commit_merge(args)

    args.approved_main_branch = "main"
    untracked = work / "after-approval.txt"
    untracked.write_text("drift\n", encoding="utf-8")
    with pytest.raises(FINALIZER.WrapError, match="untracked changes appeared"):
        FINALIZER.commit_merge(args)
    untracked.unlink()

    (main / "drift.txt").write_text("drift\n", encoding="utf-8")
    _git(main, "add", "drift.txt")
    _git(main, "commit", "-q", "-m", "main drift")
    with pytest.raises(FINALIZER.WrapError, match="main HEAD changed"):
        FINALIZER.commit_merge(args)


def test_commit_merge_resumes_after_closure_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    args = _arguments(
        "commit-merge",
        archived,
        run,
        main,
        approved_staged_tree=prepared["staged_tree"],
        approved_main_head=prepared["main_head"],
        approved_main_branch="main",
        codegraph_policy=None,
    )
    exact_merge = FINALIZER.scope_git.merge_exact
    monkeypatch.setattr(
        FINALIZER.scope_git,
        "merge_exact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FINALIZER.scope_git.GitError("simulated interruption")
        ),
    )
    with pytest.raises(FINALIZER.scope_git.GitError, match="interruption"):
        FINALIZER.commit_merge(args)
    assert _git(work, "show", "-s", "--format=%s", "HEAD") == (
        "wrap(E-001): finalize delivery"
    )

    (main / "main-after-closure.txt").write_text("main drift\n", encoding="utf-8")
    _git(main, "add", "main-after-closure.txt")
    _git(main, "commit", "-q", "-m", "main after closure")
    with pytest.raises(FINALIZER.WrapError, match="main HEAD changed"):
        FINALIZER.commit_merge(args)
    refreshed = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    args.approved_staged_tree = refreshed["staged_tree"]
    args.approved_main_head = refreshed["main_head"]
    args.approved_main_branch = refreshed["main_branch"]

    monkeypatch.setattr(FINALIZER.scope_git, "merge_exact", exact_merge)
    monkeypatch.setattr(
        FINALIZER, "_codegraph", lambda *_args, **_kwargs: {"status": "ready"}
    )
    assert FINALIZER.commit_merge(args)["status"] == "merged"


def test_commit_merge_rejects_forged_matching_parents_and_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, work, epic, run = _fixture(tmp_path)
    _seal(main, work, epic, run)
    prepared = FINALIZER.prepare_archival(_arguments("prepare", epic, run, main))
    archived = work / "docs/epics/_implemented/E-001"
    closure = FINALIZER.scope_git.commit_index(
        work,
        prepared["worktree_head"],
        prepared["staged_tree"],
        prepared["closure_label"],
    )
    wrong_tree = _git(main, "rev-parse", f"{prepared['main_head']}^{{tree}}")
    forged = _git(
        main,
        "commit-tree",
        wrong_tree,
        "-p",
        prepared["main_head"],
        "-p",
        closure,
        "-m",
        prepared["merge_label"],
    )
    _git(main, "update-ref", "refs/heads/main", forged, prepared["main_head"])
    assert FINALIZER._matching_merge(
        main, prepared["main_head"], closure, prepared["merge_label"]
    ) is None

    monkeypatch.setattr(
        FINALIZER, "_codegraph", lambda *_args, **_kwargs: {"status": "ready"}
    )
    args = _arguments(
        "commit-merge",
        archived,
        run,
        main,
        approved_staged_tree=prepared["staged_tree"],
        approved_main_head=prepared["main_head"],
        approved_main_branch="main",
        codegraph_policy=None,
    )
    with pytest.raises(FINALIZER.WrapError, match="main HEAD changed"):
        FINALIZER.commit_merge(args)


def test_delivery_overlay_uses_real_completed_audit_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_tests_path = Path(__file__).with_name("test_audit_artifacts.py")
    spec = importlib.util.spec_from_file_location("scope_wrap_real_audit_fixture", audit_tests_path)
    assert spec is not None and spec.loader is not None
    audit_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_tests)
    repo, epic, audit_run = audit_tests._fixture(tmp_path)
    attempt = audit_tests._prepare(epic, audit_run)
    assert audit_tests._record_pass(epic, attempt, audit_run, epic / "proof.txt") == 0
    audit_tests._receipt(repo, attempt)
    assert audit_tests.AUDIT.main(
        [
            "apply-synthesis",
            str(epic),
            str(attempt),
            "--run",
            str(audit_run),
        ]
    ) == 0
    assert audit_tests.AUDIT.main(
        ["finalize", str(epic), str(attempt), "--run", str(audit_run)]
    ) == 0
    summary = epic / "implementation-summary.md"
    summary.write_text("# Summary\n", encoding="utf-8")
    monkeypatch.setattr(FINALIZER, "_audit_module", lambda: audit_tests.AUDIT)
    attempt_path = attempt / "audit-attempt.yaml"
    FINALIZER._validate_audit(
        epic,
        repo,
        attempt_path,
        yaml.safe_load(attempt_path.read_text(encoding="utf-8")),
        summary.relative_to(repo).as_posix(),
        (epic / "delivery-seal.yaml").relative_to(repo).as_posix(),
        AUDIT_POLICY,
    )

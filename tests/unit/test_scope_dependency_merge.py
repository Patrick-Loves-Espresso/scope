from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

from filelock import FileLock
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src_shared/scripts/scope-dependency-merge.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scope_dependency_merge", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MERGE = _load_module()


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(
    tmp_path: Path, *, tree_neutral: bool = False
) -> tuple[Path, Path, Path, Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Scope Test")
    _git(root, "config", "user.email", "scope@example.test")
    (root / ".gitignore").write_text("tmp_debug/\n", encoding="utf-8")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")

    _git(root, "switch", "-q", "-c", "dependency")
    if tree_neutral:
        _git(root, "commit", "-q", "--allow-empty", "-m", "dependency")
    else:
        (root / "dependency.txt").write_text("dependency\n", encoding="utf-8")
        _git(root, "add", "dependency.txt")
        _git(root, "commit", "-q", "-m", "dependency")
    dependency_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-q", "main")
    epic_dir = root / "docs/epics/E-001"
    epic_dir.mkdir(parents=True)
    (epic_dir / "delivery-manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "epic_id": "E-001",
                "dependencies": [
                    {"epic_id": "E-000", "commit": dependency_commit}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "handoff")
    _git(root, "branch", "epic/E-001")
    work = tmp_path / "wip/E-001"
    work.parent.mkdir()
    _git(root, "worktree", "add", "-q", str(work), "epic/E-001")
    epic_dir = work / "docs/epics/E-001"

    run_path = root / "tmp_debug/scope-runs/E-001/implement/run.yaml"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "epic_id": "E-001",
                "command": "implement",
                "repository_root": str(root),
                "working_root": str(work),
                "active_job": None,
                "completed_jobs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root, work, epic_dir, run_path, dependency_commit


def test_merges_only_the_manifest_pinned_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    before = _git(work, "rev-parse", "HEAD")
    hook_marker = root / "hook-ran"
    hook = root / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign-git-dir"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(root / ".git/hooks"))

    head = MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)
    for name in (
        "GIT_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    ):
        monkeypatch.delenv(name)

    assert head == _git(work, "rev-parse", "HEAD")
    assert _git(work, "show", "-s", "--format=%P", head).split() == [
        before,
        dependency_commit,
    ]
    assert _git(work, "show", "-s", "--format=%s", head) == (
        "merge(E-001): integrate E-000 implementation baseline"
    )
    assert not hook_marker.exists()


def test_exact_tree_neutral_dependency_is_still_parent_verified(tmp_path: Path) -> None:
    _root, work, epic_dir, run_path, dependency_commit = _fixture(
        tmp_path, tree_neutral=True
    )

    head = MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    assert dependency_commit in _git(work, "show", "-s", "--format=%P", head).split()


def test_local_branch_merge_options_cannot_replace_the_exact_merge_strategy(
    tmp_path: Path,
) -> None:
    _root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    _git(work, "config", "branch.epic/E-001.mergeOptions", "-s ours")

    head = MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    assert _git(work, "show", f"{head}:dependency.txt") == "dependency"


def test_git_replace_ref_cannot_change_the_pinned_dependency_object(tmp_path: Path) -> None:
    root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    _git(root, "switch", "-q", "-c", "attacker")
    (root / "attacker.txt").write_text("attacker\n", encoding="utf-8")
    _git(root, "add", "attacker.txt")
    _git(root, "commit", "-q", "-m", "attacker replacement")
    attacker = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", "-q", "main")
    _git(root, "replace", dependency_commit, attacker)

    head = MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    assert _git(work, "show", f"{head}:dependency.txt") == "dependency"
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{head}:attacker.txt"],
        cwd=work,
        capture_output=True,
    ).returncode != 0


def test_git_graft_cannot_make_the_dependency_look_integrated(tmp_path: Path) -> None:
    root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    before = _git(work, "rev-parse", "HEAD")
    (root / ".git/info/grafts").write_text(
        f"{before} {dependency_commit}\n", encoding="utf-8"
    )

    head = MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    assert head != "already_integrated"
    assert _git(work, "--no-replace-objects", "show", "-s", "--format=%P", head).split() == [
        before,
        dependency_commit,
    ]


def test_already_integrated_dependency_is_a_noop(tmp_path: Path) -> None:
    _root, _work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    assert (
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)
        == "already_integrated"
    )


def test_rejects_commit_that_does_not_match_manifest_pin(tmp_path: Path) -> None:
    _root, work, epic_dir, run_path, _dependency_commit = _fixture(tmp_path)
    wrong = _git(work, "rev-parse", "HEAD")

    with pytest.raises(MERGE.MergeError, match="does not match"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", wrong)


def test_rejects_dirty_or_started_implementation(tmp_path: Path) -> None:
    _root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    (work / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(MERGE.MergeError, match="clean worktree"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)
    (work / "dirty.txt").unlink()

    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    run["completed_jobs"] = [{"job_id": "story-001"}]
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with pytest.raises(MERGE.MergeError, match="precede implementation worker"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)


def test_rejects_invalid_dependency_id_and_busy_root(tmp_path: Path) -> None:
    _root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    with pytest.raises(MERGE.MergeError, match="ID is invalid"):
        MERGE.merge_dependency(
            run_path, epic_dir, "../attacker", dependency_commit
        )

    lock = FileLock(str(work / "tmp_debug/scope-mutation.lock"))
    with lock:
        with pytest.raises(MERGE.MergeError, match="busy"):
            MERGE.merge_dependency(
                run_path, epic_dir, "E-000", dependency_commit
            )


def test_rejects_foreign_repository_and_wrong_worktree_branch(tmp_path: Path) -> None:
    root, work, epic_dir, run_path, dependency_commit = _fixture(tmp_path)
    run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    run["working_root"] = str(root)
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with pytest.raises(MERGE.MergeError, match="isolated linked worktree"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q", "-b", "epic/E-001")
    _git(foreign, "config", "user.name", "Scope Test")
    _git(foreign, "config", "user.email", "scope@example.test")
    (foreign / "base.txt").write_text("foreign\n", encoding="utf-8")
    _git(foreign, "add", ".")
    _git(foreign, "commit", "-q", "-m", "foreign")
    run["working_root"] = str(foreign)
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with pytest.raises(MERGE.MergeError, match="does not belong"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

    run["working_root"] = str(work)
    run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    _git(work, "switch", "-q", "-c", "wrong")
    with pytest.raises(MERGE.MergeError, match="must be on branch epic/E-001"):
        MERGE.merge_dependency(run_path, epic_dir, "E-000", dependency_commit)

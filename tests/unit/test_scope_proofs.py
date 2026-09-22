from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src_shared/scripts"))
import scope_proofs as proofs
import scope_snapshot


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Scope Test")
    git(tmp_path, "config", "user.email", "scope@example.test")
    (tmp_path / ".gitignore").write_text("tmp_debug/\n.env\n__pycache__/\n")
    (tmp_path / "source.txt").write_text("original")
    epic = tmp_path / "docs/epics/E-1"
    epic.mkdir(parents=True)
    (epic / "details.md").write_text("approved")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    return tmp_path, epic


def command(code: str = "print('1 passed in 0.01s')", *, fresh: bool = False, parser: str = "pytest") -> dict:
    argv = [sys.executable, "-c", code]
    return {"id": "P1", "level": "unit", "command": shlex.join(argv),
            "execution": {"argv": argv, "cwd": ".", "parser": parser, "environment": [], "fresh": fresh}}


@pytest.mark.parametrize(("output", "expected"), [
    ("=== 14 passed, 2 warnings in 0.1s ===", {"passed": 14, "failed": 0, "errors": 0, "skipped": 0}),
    ("2 failed, 1 passed, 3 errors, 4 skipped in 1.2s", {"passed": 1, "failed": 2, "errors": 3, "skipped": 4}),
    ("1 passed, 2 xfailed, 1 xpassed, 3 deselected in 0.2s", {"passed": 1, "failed": 1, "errors": 0, "skipped": 5}),
])
def test_pytest_counts(output: str, expected: dict) -> None:
    assert proofs.counts(output, "pytest", 0) == expected


@pytest.mark.parametrize("output", ["no tests ran", "1 passed in 1s\n1 failed in 1s", "1 passed, 2 passed in 1s"])
def test_ambiguous_or_missing_counts_are_rejected(output: str) -> None:
    with pytest.raises(ValueError):
        proofs.counts(output, "pytest", 0)


@pytest.mark.parametrize("value", [{"passed": True, "failed": 0, "errors": 0, "skipped": 0}, {"passed": 1}, []])
def test_json_counts_require_exact_nonnegative_integers(value: object) -> None:
    with pytest.raises(ValueError):
        proofs.counts("SCOPE_RESULT " + json.dumps(value), "scope-json", 0)


def test_project_json_and_exit_code_adapters() -> None:
    expected = {"passed": 2, "failed": 0, "errors": 0, "skipped": 0}
    assert proofs.counts("native output\nSCOPE_RESULT " + json.dumps(expected), "scope-json", 0) == expected
    assert proofs.counts("", "exit-code", 2)["failed"] == 1
    assert proofs.counts("", "exit-code", 0)["passed"] == 1
    with pytest.raises(ValueError):
        proofs.counts("", "unknown", 0)


def test_execute_deduplicates_and_retains_raw_evidence(project: tuple[Path, Path]) -> None:
    root, epic = project
    first = command()
    result = proofs.execute([first, {**first, "id": "P2"}], root, epic, proofs.policy())
    assert result["status"] == "pass"
    assert [row["reused"] for row in result["proofs"]] == [False, True]
    assert (root / result["path"]).is_file()
    assert "1 passed" in (root / next(iter(result["proofs"][0]["evidence_hashes"]))).read_text()
    assert result["duration_seconds"] >= 0


@pytest.mark.parametrize("change", ["source", "untracked", "env", "argv", "cwd", "log", "fresh"])
def test_reuse_invalidates_for_context_changes(project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    root, epic = project
    proof = command()
    proof["execution"]["environment"] = ["SCOPE_TEST_INPUT"]
    initial = proofs.execute([proof], root, epic, proofs.policy())
    cached = initial["proofs"]
    assert proofs.execute([proof], root, epic, proofs.policy(), reuse=cached)["proofs"][0]["reused"]
    if change == "source":
        (root / "source.txt").write_text("changed")
    elif change == "untracked":
        (root / "new.py").write_text("new")
    elif change == "env":
        monkeypatch.setenv("SCOPE_TEST_INPUT", "new secret")
    elif change == "argv":
        proof = command("print('2 passed in 0.01s')")
    elif change == "cwd":
        proof["execution"]["cwd"] = "docs"
    elif change == "log":
        (root / next(iter(cached[0]["evidence_hashes"]))).write_text("tampered")
    else:
        proof["execution"]["fresh"] = True
    result = proofs.execute([proof], root, epic, proofs.policy(), reuse=cached)
    assert not result["proofs"][0]["reused"]
    assert "new secret" not in (root / result["path"]).read_text()


@pytest.mark.parametrize("code", ["print('1 failed in 0.1s')", "print('1 skipped in 0.1s')", "print('1 passed in 0.1s'); raise SystemExit(2)", "print('summary missing')"])
def test_exit_and_counts_both_must_pass(project: tuple[Path, Path], code: str) -> None:
    root, epic = project
    result = proofs.execute([command(code)], root, epic, proofs.policy())
    assert result["status"] == "fail"
    assert result["proofs"][0]["outcome"] != "pass"


def test_source_mutation_and_timeout_fail_loud(project: tuple[Path, Path]) -> None:
    root, epic = project
    changed = command("from pathlib import Path; Path('source.txt').write_text('mutated'); print('1 passed in 1s')")
    result = proofs.execute([changed], root, epic, proofs.policy())
    assert result["status"] == "fail" and "changed its source" in result["proofs"][0]["summary"]
    config = {**proofs.policy(), "proof_timeout_seconds": 0.05}
    result = proofs.execute([command("import time; time.sleep(10)")], root, epic, config)
    assert result["status"] == "fail" and result["duration_seconds"] < 5


@pytest.mark.parametrize("field,value", [("argv", ["bash", "-c", "true"]), ("cwd", "../"), ("parser", "guess"), ("environment", ["BAD=NAME"]), ("fresh", "false")])
def test_invalid_execution_contracts(project: tuple[Path, Path], field: str, value: object) -> None:
    root, _ = project
    proof = command()
    proof["execution"][field] = value
    with pytest.raises(ValueError):
        proofs.execution(proof, root, proofs.policy())


def test_execution_rejects_machine_bound_env_executable_and_prior_run_output(
    project: tuple[Path, Path],
) -> None:
    root, _ = project
    proof = command()
    proof["execution"]["argv"] = [
        "/usr/bin/env",
        "PYTHONDONTWRITEBYTECODE=1",
        "/Users/example/.pyenv/versions/3.13/bin/python",
        "-m",
        "pytest",
    ]
    proof["command"] = shlex.join(proof["execution"]["argv"])
    with pytest.raises(ValueError, match="portable"):
        proofs.execution(proof, root, proofs.policy())

    proof["execution"]["argv"] = [
        "python",
        "-m",
        "pytest",
        f"--junitxml={root}/tmp_debug/scope-runs/E-001/epic_refine/result.xml",
    ]
    proof["command"] = shlex.join(proof["execution"]["argv"])
    with pytest.raises(ValueError, match="prior Scope run"):
        proofs.execution(proof, root, proofs.policy())


def test_external_freshness_and_test_counts_are_mandatory(project: tuple[Path, Path]) -> None:
    root, _ = project
    proof = command(parser="exit-code")
    with pytest.raises(ValueError, match="test proofs require counts"):
        proofs.execution(proof, root, proofs.policy())
    proof["level"] = "operational"
    with pytest.raises(ValueError, match="fresh"):
        proofs.execution(proof, root, proofs.policy())
    proof["execution"]["fresh"] = True
    assert proofs.execution(proof, root, proofs.policy())["parser"] == "exit-code"


def test_groups_are_connected_capped_and_reject_cycles() -> None:
    manifest = {"stories": [{"id": "A"}, {"id": "B", "depends_on": ["A"]}, {"id": "C", "depends_on": ["B"]}, {"id": "D", "depends_on": ["C"]}, {"id": "E"}]}
    assert proofs.groups(manifest, 3) == [["A", "B", "C"], ["D"], ["E"]]
    manifest["stories"][0]["depends_on"] = ["D"]
    with pytest.raises(ValueError, match="cycle"):
        proofs.groups(manifest, 3)


def test_replay_retains_content_without_changing_index_or_head(project: tuple[Path, Path]) -> None:
    root, epic = project
    (root / "source.txt").write_text("staged")
    git(root, "add", "source.txt")
    staged = git(root, "write-tree")
    (root / "source.txt").write_text("working")
    (root / "new.txt").write_text("untracked")
    (root / ".env").write_text("SECRET=local")
    head = git(root, "rev-parse", "HEAD")
    result = scope_snapshot.retain(root, epic, "refine-001")
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "write-tree") == staged
    assert git(root, "show", result["ref"] + ":source.txt") == "working"
    assert git(root, "show", result["ref"] + ":new.txt") == "untracked"
    assert ".env" not in git(root, "ls-tree", "-r", "--name-only", result["ref"]).splitlines()
    assert (root / "new.txt").read_text() == "untracked"


def test_replay_preserves_tracked_changes_under_ignored_parent(project: tuple[Path, Path]) -> None:
    root, epic = project
    directory = root / ".scope"
    directory.mkdir()
    (directory / "tracked.txt").write_text("original")
    (directory / "deleted.txt").write_text("original")
    git(root, "add", ".scope")
    git(root, "commit", "-m", "Track historical Scope files")
    with (root / ".gitignore").open("a") as stream:
        stream.write(".scope/\n")
    (directory / "tracked.txt").write_text("working")
    (directory / "deleted.txt").unlink()
    (directory / "ignored.txt").write_text("must not enter snapshot")
    (root / "new.txt").write_text("new")
    (root / "source.txt").write_text("staged")
    git(root, "add", "source.txt")
    staged = git(root, "write-tree")
    head = git(root, "rev-parse", "HEAD")

    result = scope_snapshot.retain(root, epic, "refine-ignored")

    assert git(root, "show", result["ref"] + ":.scope/tracked.txt") == "working"
    assert git(root, "show", result["ref"] + ":new.txt") == "new"
    retained = git(root, "ls-tree", "-r", "--name-only", result["ref"]).splitlines()
    assert ".scope/deleted.txt" not in retained
    assert ".scope/ignored.txt" not in retained
    assert git(root, "write-tree") == staged
    assert git(root, "rev-parse", "HEAD") == head
    assert (directory / "ignored.txt").read_text() == "must not enter snapshot"


def test_invalid_policy_and_missing_execution_are_rejected(tmp_path: Path, project: tuple[Path, Path]) -> None:
    config = tmp_path / 'custom/config'
    config.mkdir(parents=True)
    path = config / 'execution-policy.yaml'
    path.write_text('schema_version: 9')
    with pytest.raises(ValueError, match='policy'):
        proofs.policy(config.parent)
    import yaml
    path.write_text(yaml.safe_dump({**proofs.policy(), 'maximum_stories_per_group': 0}))
    with pytest.raises(ValueError, match='positive'):
        proofs.policy(config.parent)
    with pytest.raises(ValueError, match='execution'):
        proofs.execution({'id': 'P'}, project[0], proofs.policy())
    proof = command()
    proof['execution']['argv'] = []
    with pytest.raises(ValueError, match='argv'):
        proofs.execution(proof, project[0], proofs.policy())
    proof = command()
    proof['command'] = 'other'
    with pytest.raises(ValueError, match='display form'):
        proofs.execution(proof, project[0], proofs.policy())
    proof = command()
    proof['execution']['cwd'] = 'future'
    with pytest.raises(ValueError, match='cwd'):
        proofs.execution(proof, project[0], proofs.policy())
    assert proofs.execution(proof, project[0], proofs.policy(), require_cwd=False)['cwd'] == 'future'


def test_unavailable_command_cancellation_and_symlink_output(project: tuple[Path, Path]) -> None:
    import threading
    root, epic = project
    proof = command()
    proof['execution']['argv'] = ['scope-test-executable-does-not-exist']
    proof['command'] = proof['execution']['argv'][0]
    result = proofs.execute([proof], root, epic, proofs.policy())
    assert result['status'] == 'fail' and result['proofs'][0]['exit_code'] == -1
    cancel = root / 'tmp_debug/cancel'
    cancel.parent.mkdir(exist_ok=True)
    cancel.write_text('cancel')
    with pytest.raises(ValueError, match='cancelled'):
        proofs.execute([command()], root, epic, proofs.policy(), cancellation=cancel)
    cancel.unlink()
    timer = threading.Timer(0.15, lambda: cancel.write_text('cancel'))
    timer.start()
    try:
        with pytest.raises(ValueError, match='cancelled'):
            proofs.execute([command('import time; time.sleep(10)')], root, epic,
                           {**proofs.policy(), 'proof_poll_interval_seconds': 0.05}, cancellation=cancel)
    finally:
        timer.join()
    output = epic / 'reviews/proofs'
    import shutil
    shutil.rmtree(output)
    output.symlink_to(root / 'tmp_debug', target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        proofs.execute([command()], root, epic, proofs.policy())


def test_incomplete_receipt_and_duplicate_json_are_never_reused(project: tuple[Path, Path]) -> None:
    root, epic = project
    result = proofs.execute([command()], root, epic, proofs.policy())
    row = result['proofs'][0]
    assert not proofs.valid_record({**row, 'evidence_hashes': {'../outside': 'sha256:bad'}}, root, row['context'])
    with pytest.raises(ValueError, match='exactly one'):
        proofs.counts('SCOPE_RESULT {}\nSCOPE_RESULT {}', 'scope-json', 0)
    with pytest.raises(ValueError, match='duplicate story'):
        proofs.groups({'stories': [{'id': 'A'}, {'id': 'A'}]}, 3)
    with pytest.raises(ValueError, match='depends_on'):
        proofs.groups({'stories': [{'id': 'A', 'depends_on': 'B'}]}, 3)


def test_snapshot_retries_and_unusual_paths_preserve_exact_content(project: tuple[Path, Path]) -> None:
    root, epic = project
    for name in (' leading.txt', ':literal', 'line\nbreak.txt'):
        (root / name).write_text(name)
    (root / 'source.txt').unlink()
    first = scope_snapshot.retain(root, epic, 'refine-002')
    assert scope_snapshot.retain(root, epic, 'refine-002') == first
    for name in (' leading.txt', ':literal', 'line\nbreak.txt'):
        assert git(root, 'show', first['ref'] + ':' + name) == name.strip()
    assert 'source.txt' not in git(root, 'ls-tree', '-r', '--name-only', first['ref'])
    (root / 'new.txt').write_text('later')
    with pytest.raises(ValueError, match='never overwrite'):
        scope_snapshot.retain(root, epic, 'refine-002')
    with pytest.raises(ValueError, match='review ID'):
        scope_snapshot.retain(root, epic, '../escape')


def test_timeout_stops_proof_process_group(project: tuple[Path, Path]) -> None:
    import psutil
    root, epic = project
    (root / 'tmp_debug').mkdir(exist_ok=True)
    code = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path('tmp_debug/child.pid').write_text(str(child.pid)); time.sleep(30)"
    result = proofs.execute([command(code)], root, epic, {**proofs.policy(), 'proof_timeout_seconds': 0.5})
    assert result['status'] == 'fail'
    pid = int((root / 'tmp_debug/child.pid').read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE

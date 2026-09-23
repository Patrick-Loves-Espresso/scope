"""The verification runner: JUnit counts, criterion checks, evidence coverage, and size."""

from __future__ import annotations

from pathlib import Path

import psutil
import yaml

from conftest import EPIC, EPIC_DIR, git, scope
import scope_verify

TEST = "from greet import greet\n\n\ndef test_greets_by_name():\n    assert greet('Ada') == 'Hello, Ada!'\n"
SOURCE = 'def greet(name):\n    if not name:\n        raise ValueError("empty")\n    return f"Hello, {name}!"\n'


def build(root: Path, test: str = TEST, source: str = SOURCE, commit: bool = True) -> None:
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src" / "greet.py").write_text(source)
    (root / "tests" / "test_greet.py").write_text(test)
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feat: greet")


def set_plan(root: Path, old: str, new: str) -> None:
    plan = root / EPIC_DIR / "plan.md"
    plan.write_text(plan.read_text().replace(old, new))


def run(root: Path, milestone: str = "final", expect: int = 0) -> dict:
    return scope("scope_verify.py", "run", "--epic", EPIC, "--milestone", milestone, cwd=root, expect=expect)


def test_code_lines_exclude_comments_docstrings_and_blank_lines(tmp_path):
    python = '"""Module doc."""\n\n# comment\nimport os  # trailing\n\n\ndef f():\n    """Doc."""\n    return os.sep\n'
    assert scope_verify.code_line_numbers(python, "m.py") == {4, 7, 9}
    assert scope_verify.code_line_numbers("// c\nconst a = 1;\n/* b\n c */\n", "m.js") == {2}
    assert scope_verify.code_line_numbers("one\n\ntwo\n", "notes.unknownext") == {1, 3}
    (tmp_path / "m.py").write_text(python)
    assert scope("scope_verify.py", "lines", str(tmp_path / "m.py"), cwd=tmp_path) == {str(tmp_path / "m.py"): 3}


def test_junit_cases_and_criterion_matching(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_a" name="test_ok"/>'
        '<testcase classname="tests.test_a" name="test_ok_more"><failure/></testcase>'
        '<testcase classname="tests.test_a" name="test_param[x]"/><testcase classname="tests.test_a" name="test_param[y]">'
        '<skipped message="needs db"/></testcase>'
        '<testcase classname="tests.test_a.TestK" name="test_m"><error/></testcase>'
        '<testcase classname="t" name="test_skip"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    cases = scope_verify.junit_cases(xml)
    assert [case["status"] for case in cases] == ["passed", "failed", "passed", "skipped", "error", "skipped"]
    status = lambda refs, codes=None: scope_verify.criterion_status(refs, cases, codes or {})  # noqa: E731
    assert status(["tests/test_a.py::test_ok"]) == "passed"
    assert status(["tests/test_a.py::test_param"]) == "skipped"
    assert status(["tests/test_a.py::TestK::test_m"]) == "failed"
    assert status(["tests/test_a.py::test_absent"]) == "missing"
    assert status([]) == "missing"
    assert status(["command:lint"], {"lint": 0}) == "passed_by_exit_code"
    assert status(["command:lint"], {"lint": 2}) == "failed"
    assert status(["command:other"], {"lint": 0}) == "missing"
    go = tmp_path / "go.xml"
    go.write_text('<testsuite><testcase classname="example.com/p" name="TestGreet"/></testsuite>')
    assert scope_verify.criterion_status(["example.com/p.TestGreet"], scope_verify.junit_cases(go), {}) == "passed"


def test_final_run_records_counts_and_commits_the_record(planned, fake):
    build(
        planned,
        TEST + "\n\ndef test_rejects_empty_name():\n    import pytest\n\n    with pytest.raises(ValueError):"
        "\n        greet('')\n",
    )
    record = run(planned)
    assert record["outcome"] == "passed" and record["commands"][0]["counts"]["passed"] == 2
    assert git(planned, "log", "-1", "--format=%s") == f"verify({EPIC}): final verification record"
    assert scope("scope_verify.py", "covers", "--epic", EPIC, cwd=planned)["covered"]
    (planned / EPIC_DIR / "review.md").write_text("# review\n")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "review: evidence only")
    assert scope("scope_verify.py", "covers", "--epic", EPIC, cwd=planned)["covered"]
    (planned / "src" / "other.py").write_text("x = 1\n")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "feat: unreviewed change")
    uncovered = scope("scope_verify.py", "covers", "--epic", EPIC, cwd=planned, expect=1)
    assert "src/other.py" in uncovered["reason"]


def test_failures_missing_tests_and_unexplained_skips_fail_the_run(planned, fake):
    build(
        planned,
        TEST + "\n\ndef test_bad():\n    assert False\n\n\nimport pytest\n\n\n"
        "@pytest.mark.skip\ndef test_skipped():\n    pass\n",
    )
    record = run(planned, expect=1)
    problems = " | ".join(record["problems"])
    assert "1 failed" in problems and "AC-002: mapped tests missing" in problems
    assert record["acceptance"]["AC-001"] == "passed"
    assert yaml.safe_load((planned / EPIC_DIR / "verification.yaml").read_text())["runs"][-1]["outcome"] == "failed"
    assert "latest run failed" in scope("scope_verify.py", "covers", "--epic", EPIC, cwd=planned, expect=1)["reason"]


def test_milestone_runs_require_only_done_criteria(planned, fake):
    build(planned)
    set_plan(planned, "criteria: [AC-001], status: todo", "criteria: [AC-001], status: done")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan: S1 done")
    record = run(planned, milestone="M1")
    assert record["acceptance"] == {"AC-001": "passed", "AC-002": "pending (missing)"}
    assert (
        "not final or remediation"
        in scope("scope_verify.py", "covers", "--epic", EPIC, cwd=planned, expect=1)["reason"]
    )


def test_missing_junit_gaps_and_dirty_trees(planned, fake):
    build(planned)
    set_plan(planned, "--junitxml={junit}", "--junitxml={junit}; rm -f {junit}")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan: broken reporter")
    assert any("JUnit output missing" in p for p in run(planned, expect=1)["problems"])
    set_plan(planned, " --junitxml={junit}; rm -f {junit}", "")
    assert "commit every change" in run(planned, expect=1)["error"]
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan: no reporter")
    assert run(planned, expect=1)["commands"][0]["gap"] == "no JUnit output; exit code only"


def test_run_needs_validation_commands(planned, fake):
    set_plan(planned, "validation:", "no_validation:")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan: none")
    assert "declares no validation" in run(planned, expect=1)["error"]


def test_commands_time_out_and_their_children_are_stopped(tmp_path):
    command = f"sleep 30 & echo $! > {tmp_path}/child.pid; wait"
    row, _, problems = scope_verify._run_command(tmp_path, {"id": "slow", "command": command}, tmp_path, timeout=0.5)
    assert row["exit_code"] is None and problems == ["slow: timed out after 0.5s"]
    assert (
        not psutil.pid_exists(int((tmp_path / "child.pid").read_text()))
        or psutil.Process(int((tmp_path / "child.pid").read_text())).status() == psutil.STATUS_ZOMBIE
    )


def test_a_stale_report_is_never_reused(tmp_path):
    writer = {
        "id": "unit",
        "command": 'printf \'<testsuite><testcase classname="t" name="ok"/></testsuite>\' > {junit}',
    }
    assert scope_verify._run_command(tmp_path, writer, tmp_path, timeout=10)[2] == []
    silent = {"id": "unit", "command": "true # {junit}"}
    assert "JUnit output missing" in scope_verify._run_command(tmp_path, silent, tmp_path, timeout=10)[2][0]


def test_duplicate_validation_ids_are_refused_by_the_runner(planned, fake):
    set_plan(planned, "  - id: lint\n", "  - id: unit\n")
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan: duplicate ids")
    assert "validation ids must be unique" in run(planned, expect=1)["error"]


def worktree_with_code(planned: Path, source: str) -> Path:
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan")
    worktree = planned / "wip" / EPIC
    git(planned, "worktree", "add", "-q", "-b", f"epic/{EPIC}", str(worktree))
    build(worktree, source=source)
    return worktree


def test_size_counts_added_code_lines_against_done_stories(planned, fake):
    source = "".join(f"def f{i}(value):\n    return value + {i}\n\n\n" for i in range(10))
    worktree = worktree_with_code(planned, source)
    set_plan(worktree, "criteria: [AC-001], status: todo", "criteria: [AC-001], status: done")
    (worktree / "README.md").write_text("outside the planned paths\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "S1 done")
    size = scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)
    assert (size["actual_loc"], size["planned_loc"], size["over"]) == (20, 6, True)
    assert size["new_modules"] == 1 and size["scope_warnings"] == ["README.md"]


def test_accepted_growth_rebaselines_the_size_check(planned, fake):
    worktree = worktree_with_code(planned, "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(10)))
    set_plan(worktree, "criteria: [AC-001], status: todo", "criteria: [AC-001], status: done")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "S1 done")
    scope(
        "scope_launch.py",
        "review",
        "--host",
        "claude",
        "--workflow",
        "implement",
        "--mission",
        "check",
        "--epic",
        EPIC,
        "--context",
        "S1 grew",
        "--size",
        cwd=worktree,
    )
    assert scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)["accepted_baseline"] == [20, 6]
    greet = worktree / "src" / "greet.py"
    greet.write_text(greet.read_text() + "\n\ndef g():\n    return 1\n")
    git(worktree, "commit", "-q", "-am", "code without a finished story")
    assert scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)["over"]
    set_plan(worktree, "criteria: [AC-002], status: todo", "criteria: [AC-002], status: done")
    greet.write_text(greet.read_text() + "\n\ndef h():\n    return 2\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "S2 done")
    size = scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)
    assert (size["actual_loc"], size["planned_loc"], size["over"]) == (24, 12, False)


def test_module_limits_are_reported(planned, fake, monkeypatch):
    big = "".join(f"value_{i} = {i}\n" for i in range(460))
    worktree = worktree_with_code(planned, big)
    module = scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)["modules"][0]
    assert module["over_limit"] and module["over_target"] and not module["grew_over_limit"]
    git(worktree, "commit", "-q", "--allow-empty", "-m", "noop")
    git(planned, "merge", "-q", "--ff-only", f"epic/{EPIC}")
    (worktree / "src" / "greet.py").write_text(big + "extra = 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "grow")
    grown = scope("scope_verify.py", "size", "--epic", EPIC, cwd=worktree)["modules"][0]
    assert grown["grew_over_limit"] and not grown["over_limit"]


def test_coverage_without_runs(planned):
    assert scope_verify.coverage(planned, planned / EPIC_DIR)["reason"] == "no verification run recorded"


def test_a_missing_shell_is_a_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    row, _, problems = scope_verify._run_command(tmp_path, {"id": "x", "command": "true"}, tmp_path, timeout=5)
    assert row["exit_code"] is None and problems[0].startswith("x: could not start sh")

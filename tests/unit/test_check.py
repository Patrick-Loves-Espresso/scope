"""Criteria and plan structure, the Gate 1 record, Gate 2, the pinned merge, and the waiver."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import CRITERIA, EPIC, EPIC_DIR, PLAN, git, scope
from test_lifecycle import audit, implement, refine


def check(root: Path, command: str, *extra: str, expect: int = 0) -> dict:
    return scope("scope_check.py", command, "--epic", EPIC, *extra, cwd=root, expect=expect)


def write(root: Path, name: str, text: str) -> None:
    (root / EPIC_DIR / name).write_text(text)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (CRITERIA.replace('  rationale: "One small greeting function"\n', ""), "size_estimate needs"),
        (CRITERIA.replace("### AC-001", "### Greets").replace("### AC-002", "### Rejects"), "no criteria"),
        (CRITERIA.replace("AC-002", "AC-001"), "duplicate criterion AC-001"),
        (CRITERIA.replace("- Localized greetings\n", ""), "'Not building' section"),
    ],
)
def test_criteria_format_errors(project, text, error):
    write(project, "acceptance-criteria.md", text)
    assert any(error in item for item in check(project, "criteria", expect=1)["errors"])


def test_open_questions_block_approval_and_a_missing_file_is_an_error(project):
    assert "does not exist" in check(project, "criteria", expect=1)["error"]
    write(
        project,
        "acceptance-criteria.md",
        CRITERIA.replace(
            "## Open product questions\n\nNone", "## Open product questions\n\n- Should names be trimmed?"
        ),
    )
    assert check(project, "criteria")["open_questions"]
    assert "open product questions remain" in check(project, "approve", "--source", "x", expect=1)["error"]


def test_changed_criteria_need_renewed_approval_with_diff_and_delta(project):
    write(project, "acceptance-criteria.md", CRITERIA)
    record = check(project, "approve", "--source", "Test user in chat")["acceptance_criteria"]
    assert record["commit"] == git(project, "rev-parse", "HEAD~1")
    assert check(project, "criteria")["approval"]["status"] == "approved"
    write(
        project,
        "acceptance-criteria.md",
        CRITERIA.replace("production_loc: 12", "production_loc: 40")
        + "\n### AC-003: Greets in capitals\n\nShouting.\n",
    )
    changed = check(project, "criteria", expect=3)["approval"]
    assert changed["status"] == "changed" and changed["delta"] == "2 → 3 criteria, estimate 12 → 40 LoC"
    assert "+### AC-003" in changed["diff"]


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("## Dependencies", "## Deps", "missing section '## Dependencies'"),
        ("production_loc: 12\n  files: 1", "production_loc: -1\n  files: 1", "estimate needs"),
        ("production_paths: [src/]", "production_paths: []", "production_paths must list"),
        ("{id: S2, title: Reject empty names", "{id: S1, title: Reject empty names", "duplicate story S1"),
        ("milestone: M2, ", "", "S2: needs title, milestone"),
        (
            "complexity: 1, estimate_loc: 6, criteria: [AC-002]",
            "complexity: 9, estimate_loc: 6, criteria: [AC-002]",
            "complexity 9 is above 7",
        ),
        (
            "complexity: 1, estimate_loc: 6, criteria: [AC-002]",
            "complexity: 11, estimate_loc: 6, criteria: [AC-002]",
            "complexity 0-10",
        ),
        ("criteria: [AC-002], status: todo", "criteria: [AC-002], status: started", "status must be todo or done"),
        ("criteria: [AC-002]", "criteria: [AC-009]", "AC-002 is delivered by no story"),
        ("criteria: [AC-002]", "criteria: [AC-002, AC-009]", "unknown criterion AC-009"),
        ("  - id: lint\n    type: check", "  - id: lint\n    type: vibe", "validation lint: needs id"),
        ("validation:", "checks:", "validation: no commands declared"),
        ('  AC-002: ["tests/test_greet.py::test_rejects_empty_name", "command:lint"]\n', "", "AC-002: no tests mapped"),
        ('"command:lint"', '"command:typo"', "unknown validation command"),
        ("docs:\n  - {target", "documents:\n  - {target", "docs: declare"),
        ("story: S1, change", "story: S7, change", "needs a target and an owner story"),
    ],
)
def test_plan_structure_errors(project, old, new, error):
    write(project, "acceptance-criteria.md", CRITERIA)
    assert old in PLAN
    write(project, "plan.md", PLAN.replace(old, new))
    assert any(error in item for item in check(project, "plan", expect=1)["errors"])


def test_plan_complexity_exception_and_estimate_warning(project):
    write(project, "acceptance-criteria.md", CRITERIA)
    write(
        project,
        "plan.md",
        PLAN.replace(
            "complexity: 1, estimate_loc: 6, criteria: [AC-002]",
            "complexity: 8, complexity_exception: one migration, estimate_loc: 9, criteria: [AC-002]",
        ),
    )
    result = check(project, "plan")
    assert result["errors"] == [] and result["warnings"] == ["story estimates sum to 15, plan estimate is 12"]
    (project / EPIC_DIR / "plan.md").unlink()
    assert "does not exist" in check(project, "plan", expect=1)["error"]


@pytest.fixture
def audited(project: Path, fake: Path) -> Path:
    refine(project)
    worktree = implement(project)
    audit(worktree)
    return worktree


def test_gate2_blocks_on_changed_criteria_stale_review_and_uncovered_head(audited):
    assert check(audited, "gate2")["ready"]
    criteria = audited / "docs/epics/_implemented/DEMO-001-greeting/acceptance-criteria.md"
    criteria.write_text(criteria.read_text() + "\n")
    git(audited, "commit", "-q", "-am", "criteria edit")
    problems = " | ".join(check(audited, "gate2", expect=1)["problems"])
    assert "acceptance criteria are changed" in problems and "changed after the last audit review" in problems
    assert "verification does not cover" in problems


def test_merge_refuses_moved_branch_dirty_main_and_the_main_checkout(audited, project):
    head = git(audited, "rev-parse", "HEAD")
    assert "not the approved" in check(audited, "merge", "--commit", "0" * 40, "--approver", "u", expect=1)["error"]
    assert "from the epic worktree" in check(project, "merge", "--commit", head, "--approver", "u", expect=1)["error"]
    (project / "pytest.ini").write_text("[pytest]\n")
    assert "uncommitted changes" in check(audited, "merge", "--commit", head, "--approver", "u", expect=1)["error"]
    git(project, "checkout", "-q", "pytest.ini")


def test_merge_conflict_is_aborted(audited, project):
    (project / "src").mkdir()
    (project / "src" / "greet.py").write_text("conflicting = True\n")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "main moved")
    head = git(audited, "rev-parse", "HEAD")
    assert (
        "merge failed and was aborted"
        in check(audited, "merge", "--commit", head, "--approver", "u", expect=1)["error"]
    )
    assert git(project, "status", "--porcelain") == ""


def test_merge_refuses_when_gate2_fails(audited):
    (audited / "src" / "extra.py").write_text("x = 1\n")
    git(audited, "add", "-A")
    git(audited, "commit", "-q", "-m", "unreviewed")
    head = git(audited, "rev-parse", "HEAD")
    assert "Gate 2 checks fail" in check(audited, "merge", "--commit", head, "--approver", "u", expect=1)["error"]


def test_waiver_only_for_an_incomplete_audit_and_never_a_pass(project, fake, monkeypatch):
    refine(project)
    worktree = implement(project)
    monkeypatch.setenv("FAKE_UNAVAILABLE", "claude,codex")
    monkeypatch.setenv("FAKE_AUDIT_FINDER", "nobody")
    scope(
        "scope_launch.py",
        "review",
        "--host",
        "claude",
        "--workflow",
        "audit",
        "--mission",
        "full",
        "--epic",
        EPIC,
        cwd=worktree,
    )
    assert "audit incomplete" in " ".join(check(worktree, "gate2", expect=1)["problems"])
    check(
        worktree,
        "waive",
        "--missing",
        "second independent audit review",
        "--approver",
        "Test user",
        "--reason",
        "providers down before release",
    )
    gate = check(worktree, "gate2")
    assert gate["ready"] and "Verdict: INCOMPLETE (waived: missing: second independent audit review" in gate["summary"]
    monkeypatch.setenv("FAKE_UNAVAILABLE", "")
    scope(
        "scope_launch.py",
        "review",
        "--host",
        "claude",
        "--workflow",
        "audit",
        "--mission",
        "full",
        "--epic",
        EPIC,
        "--providers",
        "claude",
        cwd=worktree,
    )
    assert (
        "nothing to waive"
        in check(worktree, "waive", "--missing", "x", "--approver", "u", "--reason", "r", expect=1)["error"]
    )


def test_gate2_blocks_while_audit_findings_are_open(project, fake):
    refine(project)
    worktree = implement(project)
    scope(
        "scope_launch.py",
        "review",
        "--host",
        "claude",
        "--workflow",
        "audit",
        "--mission",
        "full",
        "--epic",
        EPIC,
        cwd=worktree,
    )
    assert "audit findings still open" in " ".join(check(worktree, "gate2", expect=1)["problems"])

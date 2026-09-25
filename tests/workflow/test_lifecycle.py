"""Black-box test of the whole lifecycle: refine, implement, audit, wrap.

The orchestrator steps follow the four command prompts; the workers and
reviewers are fake provider CLIs that write real files and commits.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from conftest import EPIC, calls, git, scope

ACTIVE = "docs/epics/DEMO-001-greeting"
ARCHIVED = "docs/epics/_implemented/DEMO-001-greeting"


def work(root: Path, role: str, task: str, host: str = "claude") -> dict:
    return scope("scope_launch.py", "work", "--host", host, "--role", role, "--epic", EPIC, "--task", task, cwd=root)


def review(root: Path, workflow: str, mission: str, *extra: str) -> dict:
    return scope(
        "scope_launch.py",
        "review",
        "--host",
        "claude",
        "--workflow",
        workflow,
        "--mission",
        mission,
        "--epic",
        EPIC,
        *extra,
        cwd=root,
    )


def status(root: Path, workflow: str) -> dict:
    return scope("scope_review.py", "status", "--epic", EPIC, "--workflow", workflow, cwd=root)


def refine(root: Path) -> None:
    """/epic_refine up to a settled review and the final commit."""
    assert work(root, "planner", "Draft acceptance-criteria.md from details.md.")["status"] == "done"
    criteria = scope("scope_check.py", "criteria", "--epic", EPIC, cwd=root)
    assert criteria["errors"] == [] and criteria["approval"]["status"] == "not_approved"
    assert not criteria["open_questions"]
    approval = scope("scope_check.py", "approve", "--epic", EPIC, "--source", "Test user in chat", cwd=root)
    assert approval["acceptance_criteria"]["blob"] == git(root, "hash-object", f"{ACTIVE}/acceptance-criteria.md")
    assert work(root, "planner", "Write plan.md for the approved acceptance criteria.")["status"] == "done"
    assert scope("scope_check.py", "plan", "--epic", EPIC, cwd=root)["errors"] == []
    full = review(root, "refine", "full")
    assert full["summary"]["pending"] == {"needs_disposition": ["R1.claude.1"]}
    assert full["summary"]["providers_completed"] == ["claude", "codex"]
    assert work(root, "planner", "Resolve the open findings in review.md.")["status"] == "done"
    assert status(root, "refine")["pending"] == {"needs_verification": ["R1.claude.1"]}
    verified = review(root, "refine", "verify")
    assert verified["summary"]["settled"], verified["summary"]
    assert scope("scope_check.py", "criteria", "--epic", EPIC, cwd=root)["approval"]["status"] == "approved"


def implement(root: Path) -> Path:
    """/implement: worktree, one implementer, final verification."""
    assert status(root, "refine")["settled"]
    worktree = root / "wip" / EPIC
    git(root, "worktree", "add", "-q", "-b", f"epic/{EPIC}", str(worktree))
    job = work(
        worktree, "implementer", "Implement plan.md, continuing from its story status and the git log.", host="codex"
    )
    assert job["status"] == "done" and job["warnings"] == [], job
    assert len(job["commits"]) >= 4
    final = scope("scope_verify.py", "run", "--epic", EPIC, "--milestone", "final", cwd=worktree)
    assert final["outcome"] == "passed", final["problems"]
    assert final["acceptance"] == {"AC-001": "passed", "AC-002": "passed_by_exit_code"}
    return worktree


def audit(worktree: Path) -> None:
    """/audit_epic: full audit, one fix, remediation verification, verify pass."""
    assert scope("scope_verify.py", "covers", "--epic", EPIC, cwd=worktree)["covered"]
    full = review(worktree, "audit", "full")
    assert full["summary"]["pending"] == {"needs_disposition": ["A1.codex.1"]}
    assert work(worktree, "implementer", "Resolve the open audit findings in review.md.")["status"] == "done"
    assert not status(worktree, "audit")["fresh"]
    remediation = scope("scope_verify.py", "run", "--epic", EPIC, "--milestone", "remediation", cwd=worktree)
    assert remediation["outcome"] == "passed"
    verified = review(worktree, "audit", "verify")
    assert verified["reviewers"][0]["provider"] == "codex"
    assert verified["summary"]["settled"], verified["summary"]


def wrap(root: Path, worktree: Path) -> None:
    """/wrap_epic: Gate 2 summary, pinned merge with trailers, worktree removed."""
    gate = scope("scope_check.py", "gate2", "--epic", EPIC, cwd=worktree)
    assert gate["ready"], gate["problems"]
    assert gate["commit"] in gate["summary"] and "Verdict: passed" in gate["summary"]
    assert "(plan 12 LoC / 1 files; Gate 1 12 LoC / 1 files)" in gate["summary"]
    plan_lines = len((worktree / ARCHIVED / "plan.md").read_text().splitlines())
    assert f"- plan.md: {plan_lines} lines" in gate["summary"]
    merged = scope(
        "scope_check.py", "merge", "--epic", EPIC, "--commit", gate["commit"], "--approver", "Test user", cwd=worktree
    )
    assert merged["worktree_removed"] and merged["branch"] == "main"
    message = git(root, "log", "-1", "--format=%B")
    assert f"Scope-Approved-Commit: {gate['commit']}" in message and "Scope-Approved-By: Test user" in message
    assert git(root, "rev-parse", "HEAD^2") == gate["commit"]


def test_full_lifecycle(project: Path, fake: Path) -> None:
    refine(project)
    worktree = implement(project)
    audit(worktree)
    wrap(project, worktree)

    archived = project / ARCHIVED
    record = yaml.safe_load((archived / "verification.yaml").read_text())
    assert [run["milestone"] for run in record["runs"]] == ["M1", "M2", "final", "remediation"]
    assert all(run["outcome"] == "passed" for run in record["runs"])
    assert (archived / "approvals.yaml").is_file() and not (project / ACTIVE).exists()
    assert (project / "docs/architecture/05-building-blocks.md").is_file()
    review_text = (archived / "review.md").read_text()
    assert "- R1.claude.1 · claude: verified" in review_text and "- A1.codex.1 · codex: verified" in review_text

    jobs = calls(fake)
    claude_reviews = [c["args"] for c in jobs if c["provider"] == "claude" and "Reviewer" in c["first_line"]]
    assert claude_reviews and all("--safe-mode" in args and "--disallowedTools" in args for args in claude_reviews)
    assert all(args[args.index("--disallowedTools") + 1].startswith("Write,Edit") for args in claude_reviews)
    codex_reviews = [c["args"] for c in jobs if c["provider"] == "codex" and "Reviewer" in c["first_line"]]
    assert all(args[args.index("--sandbox") + 1] == "read-only" for args in codex_reviews)
    codex_worker = next(c["args"] for c in jobs if c["provider"] == "codex" and "Implementer" in c["first_line"])
    assert codex_worker[codex_worker.index("--sandbox") + 1] == "workspace-write" and "--add-dir" in codex_worker

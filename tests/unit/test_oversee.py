"""The overseer's drift audit: the project's audit reviewers, read-only, on one filled prompt."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from conftest import EPIC, SCRIPTS, calls, git, scope

TEMPLATE = SCRIPTS.parent / "commands" / "overseer" / "drift-audit.md"
FILLED = "# Drift audit: DEMO-001 (gate1)\n\nAudit DEMO-001 against the program. Return a report.\n"


def audit(root: Path, prompt: Path, *extra: str, epic: str = EPIC, expect: int = 0) -> dict:
    return scope("scope_oversee.py", "audit", "--name", "demo-program", "--epic", epic, "--checkpoint", "gate1",
                 "--prompt", str(prompt), *extra, cwd=root, expect=expect)


@pytest.fixture
def prompt(tmp_path: Path) -> Path:
    path = tmp_path / "gate1-prompt.md"
    path.write_text(FILLED)
    return path


def test_both_auditors_review_the_same_prompt_read_only(project: Path, fake: Path, prompt: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MESSAGE", "# Report\n\nF1 major: the plan moves scope.")
    head = git(project, "rev-parse", "HEAD")
    result = audit(project, prompt)
    assert [(row["name"], row["status"]) for row in result["auditors"]] == [("claude", "completed"),
                                                                             ("codex", "completed")]
    assert result["commit"] == head and git(project, "rev-parse", "HEAD") == head  # nothing committed
    assert git(project, "status", "--porcelain") == ""
    logs = Path(result["logs"])
    assert logs.parent == project / "tmp_debug" / "scope-runs" / "oversight-demo-program"
    assert logs.name.endswith(f"-{EPIC}-gate1")
    for row in result["auditors"]:
        assert Path(row["report"]).read_text() == "# Report\n\nF1 major: the plan moves scope.\n"
    sent = (logs / "prompt-auditor-claude.md").read_text()
    assert sent.startswith(FILLED.rstrip())
    assert f"- Repository root: `{project}`" in sent and "read CLAUDE.md once" in sent
    assert "follow the AGENTS.md instructions" in (logs / "prompt-auditor-codex.md").read_text()
    by_provider = {call["provider"]: call["args"] for call in calls(fake)}
    claude = by_provider["claude"]
    assert claude[claude.index("--tools") + 1] == "Read,Glob,Grep,Bash"
    assert "Write" in claude[claude.index("--disallowedTools") + 1]
    codex = by_provider["codex"]
    assert codex[codex.index("--sandbox") + 1] == "read-only"


def test_an_unfilled_template_is_refused(project: Path, fake: Path, tmp_path: Path) -> None:
    raw = tmp_path / "raw.md"
    raw.write_text(TEMPLATE.read_text())
    result = audit(project, raw, expect=1)
    assert "fill the template's placeholders first" in result["error"] and "<EPIC>" in result["error"]
    assert calls(fake) == []


@pytest.mark.parametrize("extra, epic, message", [
    (("--reviewers", "gemini"), EPIC, "unknown reviewers ['gemini']"),
    ((), "DEMO-404", "no epic folder for DEMO-404"),
])
def test_unknown_reviewers_and_epics_are_refused(project: Path, fake: Path, prompt: Path, extra: tuple,
                                                 epic: str, message: str) -> None:
    assert message in audit(project, prompt, *extra, epic=epic, expect=1)["error"]
    assert calls(fake) == []


def test_the_oversight_name_is_a_slug(project: Path, fake: Path, prompt: Path) -> None:
    result = scope("scope_oversee.py", "audit", "--name", "Signal Program", "--epic", EPIC, "--checkpoint", "plan",
                   "--prompt", str(prompt), cwd=project, expect=1)
    assert "lowercase letters, digits and hyphens" in result["error"]


def test_a_missing_prompt_file_is_refused(project: Path, fake: Path, tmp_path: Path) -> None:
    assert "no prompt file" in audit(project, tmp_path / "absent.md", expect=1)["error"]


def test_a_failed_mandatory_auditor_is_retried_once(project: Path, fake: Path, prompt: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MESSAGE", "# Report\n\nNo drift.")
    monkeypatch.setenv("FAKE_FAIL_ONCE", "codex")
    rows = {row["name"]: row for row in audit(project, prompt)["auditors"]}
    assert rows["codex"]["status"] == "completed"
    assert rows["codex"]["retried_after"].startswith("failed: ")
    assert rows["claude"]["retried_after"] is None
    assert [call["provider"] for call in calls(fake)].count("codex") == 2


def test_an_auditor_that_keeps_failing_is_reported_without_a_report(project: Path, fake: Path, prompt: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MESSAGE", "# Report\n\nNo drift.")
    monkeypatch.setenv("FAKE_FAIL", "codex")
    rows = {row["name"]: row for row in audit(project, prompt)["auditors"]}
    assert rows["claude"]["status"] == "completed"
    assert rows["codex"]["status"] == "failed" and rows["codex"]["report"] is None
    assert "provider crashed" in rows["codex"]["error"]


def test_one_named_auditor_and_a_cross_epic_check(project: Path, fake: Path, prompt: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MESSAGE", "# Report\n\nThe two plans agree.")
    (project / "docs" / "epics" / "DEMO-002-farewell").mkdir()
    (project / "docs" / "epics" / "DEMO-002-farewell" / "details.md").write_text("# DEMO-002: Farewell\n")
    result = audit(project, prompt, "--reviewers", "codex", epic=f"{EPIC},DEMO-002")
    assert [row["name"] for row in result["auditors"]] == ["codex"]
    assert result["epics"] == [EPIC, "DEMO-002"]
    assert Path(result["logs"]).name.endswith(f"-{EPIC}+DEMO-002-gate1")


def test_the_template_names_the_placeholders_the_command_fills() -> None:
    placeholders = set(re.findall(r"<[A-Z][A-Z0-9-]+>", TEMPLATE.read_text()))
    assert placeholders == {"<EPIC>", "<CHECKPOINT>", "<STATE>", "<FOLDER>", "<SOURCES>", "<OTHER-EPICS>",
                            "<EPIC-SPECIFIC-CHECKS>"}

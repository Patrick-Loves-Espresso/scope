"""Workers, reviewers, fallback, adjudication routing, and the harvested provider flags."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from conftest import EPIC, EPIC_DIR, calls, git, scope
import scope_providers as providers


def review(root: Path, workflow: str, mission: str, *extra: str, expect: int = 0) -> dict:
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
        expect=expect,
    )


def work(root: Path, role: str = "planner", task: str = "Write plan.md", expect: int = 0) -> dict:
    return scope(
        "scope_launch.py",
        "work",
        "--host",
        "claude",
        "--role",
        role,
        "--epic",
        EPIC,
        "--task",
        task,
        cwd=root,
        expect=expect,
    )


def rows(result: dict) -> list[tuple]:
    return [(row["name"], row["status"]) for row in result["reviewers"]]


def reviewers(root: Path, *args: str, expect: int = 0) -> dict:
    return scope("scope_reviewers.py", *args, cwd=root, expect=expect)


def request(root: Path, workflow: str, *changes: str, expect: int = 0) -> dict:
    return reviewers(root, "epic", "--epic", EPIC, "--workflow", workflow, *changes, "--requested", "Patrick, in chat",
                     expect=expect)


def test_an_unavailable_reviewer_is_replaced_only_by_a_recorded_request(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_UNAVAILABLE", "codex")
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "completed"), ("codex", "unavailable")]
    assert not result["summary"]["complete"] and result["summary"]["missing_reviews"] == ["codex"]
    assert [c["provider"] for c in calls(fake)].count("opencode") == 0
    replaced = request(planned, "refine", "--replace", "codex", "opencode", "moonshot/kimi-v3", "high")
    assert [entry["name"] for entry in replaced["reviewers"]] == ["claude", "kimi-v3"]
    assert replaced["replaced"] == {"codex": "kimi-v3"} and replaced["source"] == "epic"
    rerun = review(planned, "refine", "full", "--reviewers", "kimi-v3")
    assert rows(rerun) == [("kimi-v3", "completed")]
    assert rerun["summary"]["complete"] and rerun["summary"]["reviewers_completed"] == ["claude", "kimi-v3"]
    text = (planned / EPIC_DIR / "review.md").read_text()
    assert "## refine reviewers · " in text and "- requested: Patrick, in chat" in text
    assert "- reviewer kimi-v3 · opencode · moonshot/kimi-v3 · high · mandatory" in text
    assert "- replaced codex by kimi-v3" in text and "- reviewer kimi-v3 · moonshot/kimi-v3/high · completed" in text
    opencode = next(c["args"] for c in calls(fake) if c["provider"] == "opencode")
    assert opencode[:4] == ["run", "--pure", "--agent", "plan"] and "moonshot/kimi-v3" in opencode


def test_invalid_review_output_is_recorded(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_INVALID", "codex")
    result = review(planned, "refine", "full")
    assert ("codex", "invalid_output") in rows(result)
    assert "error: missing or invalid DECISION line" in (planned / EPIC_DIR / "review.md").read_text()


def test_one_replacement_does_not_cover_another_missing_reviewer(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_UNAVAILABLE", "claude,codex")
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "unavailable"), ("codex", "unavailable")]
    request(planned, "refine", "--replace", "claude", "opencode", "moonshot/kimi-v3", "high")
    rerun = review(planned, "refine", "full", "--reviewers", "kimi-v3")
    assert rows(rerun) == [("kimi-v3", "completed")]
    assert not rerun["summary"]["complete"] and rerun["summary"]["missing_reviews"] == ["codex"]


def test_a_failed_review_is_recorded_and_not_replaced_automatically(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "codex")
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "completed"), ("codex", "failed")]
    assert [c["provider"] for c in calls(fake)].count("opencode") == 0
    assert "provider crashed" in (planned / EPIC_DIR / "review.md").read_text()


def test_rejection_is_checked_by_the_raiser_then_adjudicated_by_the_adjudicator(planned, fake, monkeypatch):
    review(planned, "refine", "full")
    monkeypatch.setenv("FAKE_DISPOSITION", "rejected — out of scope for this epic")
    work(planned, task="Resolve the open findings in review.md.")
    monkeypatch.setenv("FAKE_VERIFY_OUTCOME", "maintained")
    checked = review(planned, "refine", "verify")
    assert checked["reviewers"][0]["name"] == "claude"
    assert checked["summary"]["pending"] == {"needs_adjudication": ["R1.claude.1"]}
    adjudicated = review(planned, "refine", "adjudicate")
    assert [(row["name"], row["role"]) for row in adjudicated["reviewers"]] == [("glm-5-3", "adjudicator")]
    assert adjudicated["summary"]["settled"]
    text = (planned / EPIC_DIR / "review.md").read_text()
    assert "- adjudicator glm-5-3 · zai/glm-5.3/high · completed · done · " in text
    assert "- R1.claude.1 · glm-5-3: rejection_upheld — examined from scratch" in text
    assert review(planned, "refine", "verify", expect=1)["error"] == "no findings need the verify mission"


def test_finding_upheld_reopens_the_finding(planned, fake, monkeypatch):
    review(planned, "refine", "full")
    monkeypatch.setenv("FAKE_DISPOSITION", "rejected — out of scope")
    work(planned, task="Resolve the open findings in review.md.")
    monkeypatch.setenv("FAKE_VERIFY_OUTCOME", "maintained")
    review(planned, "refine", "verify")
    monkeypatch.setenv("FAKE_ADJUDICATION", "finding_upheld")
    result = review(planned, "refine", "adjudicate")
    assert result["summary"]["pending"] == {"needs_disposition": ["R1.claude.1"]}


def write_review(root: Path, body: str) -> None:
    (root / EPIC_DIR / "review.md").write_text("# DEMO-001: Review\n\n" + body)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test: review state")


def test_adjudication_always_goes_to_the_adjudicator_and_needs_one(planned, fake):
    sha = git(planned, "rev-parse", "HEAD")
    write_review(
        planned,
        f"## refine 1 · full · t\n- commit: {sha}\n- reviewer claude · m/high · completed · x\n"
        "- reviewer codex · m/high · completed · x\n\n"
        "### R1.claude.1 · major · scope\n- evidence: e\n- correction: c\n- closure: x\n- disposition: rejected — no\n\n"
        "### R1.codex.1 · major · scope\n- evidence: e\n- correction: c\n- closure: x\n"
        "- disposition: duplicate of R1.claude.1\n\n"
        "## refine 2 · verify · t\n- R1.claude.1 · claude: maintained — still wrong\n",
    )
    explicit = review(planned, "refine", "adjudicate", "--reviewers", "codex", expect=1)
    assert "goes to the adjudicator" in explicit["error"]
    judged = review(planned, "refine", "adjudicate")
    assert [(row["name"], row["role"]) for row in judged["reviewers"]] == [("glm-5-3", "adjudicator")]
    (planned / ".scope" / "reviewers.yaml").unlink()
    error = review(planned, "refine", "full", expect=1)["error"]
    assert "no adjudicator is set for the refine workflow" in error and "/scope_reviewers" in error


def test_check_records_size_and_diagnosis_records_an_outcome(planned, fake):
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "test: plan")
    checked = review(planned, "implement", "check", "--context", "S1 grew", "--size")
    assert checked["reviewers"][0]["name"] == "codex" and checked["size"]["planned_loc"] == 0
    text = (planned / EPIC_DIR / "review.md").read_text()
    assert "- size: actual=0 planned=0" in text and "#### Rationale (codex)" in text
    review(planned, "refine", "full")
    diagnosed = review(planned, "refine", "diagnose", "--finding", "R1.claude.1")
    assert diagnosed["reviewers"][0]["decision"] == "diagnosed"
    assert "- R1.claude.1 · codex: diagnosis" in (planned / EPIC_DIR / "review.md").read_text()


@pytest.mark.parametrize(
    ("workflow", "mission", "extra", "message"),
    [
        ("implement", "full", (), "has no full mission"),
        ("implement", "check", (), "--context is required"),
        ("refine", "diagnose", ("--finding", "R9.claude.1"), "--finding must name"),
        ("refine", "adjudicate", (), "no findings need"),
    ],
)
def test_review_argument_errors(planned, fake, workflow, mission, extra, message):
    assert message in review(planned, workflow, mission, *extra, expect=1)["error"]


def test_audit_review_requires_a_clean_tree(planned, fake):
    assert "commit or discard" in review(planned, "audit", "full", expect=1)["error"]


def test_worker_status_warnings_and_failures(planned, fake, monkeypatch):
    (planned / "stray.txt").write_text("x")
    result = work(planned, task="Write plan.md for the approved acceptance criteria.")
    assert result["status"] == "done" and "stray.txt" in result["warnings"][0]
    assert result["usage"]["cost_usd"] == 0.01
    monkeypatch.setenv("FAKE_MESSAGE", "I stopped without a status line.")
    assert work(planned)["status"] == "unknown"
    monkeypatch.setenv("FAKE_FAIL", "claude")
    assert work(planned)["status"] == "failed"
    monkeypatch.setenv("FAKE_UNAVAILABLE", "claude")
    assert "claude --version failed" in work(planned, expect=1)["error"]


def test_implementer_leaving_changes_uncommitted_is_warned(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_MESSAGE", "Done.\nSTATUS: done")
    (planned / "src").mkdir()
    (planned / "src" / "wip.py").write_text("x = 1\n")
    result = work(planned, role="implementer", task="Implement plan.md")
    assert result["status"] == "done" and "uncommitted changes remain" in result["warnings"][0]


def test_show_preflights_each_reviewer_and_the_adjudicator(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_UNAVAILABLE", "codex")
    shown = reviewers(planned, "show", "--preflight")
    assert shown["refine"]["preflight"] == {
        "claude": "ready", "codex": "codex --version failed", "adjudicator": "ready"}
    assert shown["audit"]["source"] == "project" and shown["audit"]["problems"] == []


def test_a_cli_older_than_the_policy_minimum_is_refused(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_VERSION", "2.1.278")  # the policy requires claude 2.1.280; codex has no minimum
    old = "claude CLI claude 2.1.278 is older than 2.1.280; update it"
    shown = reviewers(planned, "show", "--preflight")
    assert shown["refine"]["preflight"] == {"claude": old, "codex": "ready", "adjudicator": "ready"}
    assert work(planned, expect=1)["error"] == old
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "unavailable"), ("codex", "completed")]
    assert result["summary"]["missing_reviews"] == ["claude"]
    monkeypatch.setenv("FAKE_VERSION", "2.1.280")
    assert work(planned)["status"] == "done"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("2.1.1000 (Claude Code)", None),  # numeric, not string, comparison
        ("2.1.280", None),
        ("2.1.279 (Claude Code)", "claude CLI 2.1.279 (Claude Code) is older than 2.1.280; update it"),
        ("", "claude CLI (no version printed) is older than 2.1.280; update it"),
    ],
)
def test_preflight_compares_the_version_numerically(tmp_path, monkeypatch, printed, expected):
    probe(
        tmp_path,
        monkeypatch,
        "claude",
        f'case "$1" in --version) echo "{printed}";; --help) echo --print --safe-mode --no-session-persistence '
        '--permission-mode;; auth) echo "{\\"loggedIn\\": true}";; esac\n',
    )
    assert providers.preflight("claude", "m", 1, "2.1.280") == expected


def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, script: str) -> None:
    bin_dir = tmp_path / "probe-bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin:/bin")


@pytest.mark.parametrize(
    ("name", "script", "expected"),
    [
        ("claude", 'case "$1" in --version) echo 1;; --help) echo --print;; esac\n', "claude CLI lacks --safe-mode"),
        (
            "claude",
            'case "$1" in --version) echo 1;; --help) echo --print --safe-mode --no-session-persistence '
            '--permission-mode;; auth) echo "{\\"loggedIn\\": false}";; esac\n',
            "not authenticated",
        ),
        (
            "claude",
            'case "$1" in --version) echo 1;; --help) echo --print --safe-mode --no-session-persistence '
            "--permission-mode;; auth) echo nope;; esac\n",
            "not authenticated",
        ),
        ("codex", 'case "$1" in --version) echo 1;; exec) echo --sandbox;; esac\n', "codex CLI lacks"),
        ("opencode", 'case "$1" in --version) echo 1;; models) echo other/model;; esac\n', "does not list model"),
        ("opencode", "exit 3\n", "opencode --version failed"),
        ("opencode", "sleep 5\n", "preflight failed"),
    ],
)
def test_preflight_failures(tmp_path, monkeypatch, name, script, expected):
    probe(tmp_path, monkeypatch, name, script)
    assert expected in providers.preflight(name, "m", timeout=1)


def test_preflight_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert providers.preflight("codex", "m", 1) == "codex CLI not found on PATH"


def test_commands_keep_the_harvested_read_only_and_write_flags(tmp_path):
    common = {
        "model": "m",
        "effort": "high",
        "root": tmp_path,
        "output_path": tmp_path / "o",
        "prompt": "P",
    }
    claude = providers.command("claude", write=False, read_only_commands=["git diff"], add_dirs=[tmp_path], **common)
    assert "--safe-mode" in claude and claude[claude.index("--allowedTools") + 1] == "Read,Glob,Grep,Bash(git diff:*)"
    assert claude[claude.index("--output-format") + 1] == "text" and "--add-dir" in claude
    writer = providers.command("claude", write=True, **common)
    assert "Bash(git push *)" in writer[writer.index("--disallowedTools") + 1]
    codex = providers.command("codex", write=True, add_dirs=[tmp_path], **common)
    assert codex[-2:] == ["--json", "-"] and codex[codex.index("--sandbox") + 1] == "workspace-write"
    assert providers.command("codex", write=False, **common)[-1] == "-"
    assert providers.command("opencode", write=False, **common)[-1] == "P"
    with pytest.raises(ValueError):
        providers.command("opencode", write=True, **common)
    with pytest.raises(ValueError):
        providers.command("other", write=False, **common)


def test_run_kills_a_hung_provider_tree_and_reports_launch_errors(tmp_path):
    script = tmp_path / "hang.py"
    script.write_text("import subprocess, time\nsubprocess.Popen(['sleep', '30'])\ntime.sleep(30)\n")
    started = time.monotonic()
    result = providers.run(
        [sys.executable, str(script)],
        provider="opencode",
        prompt="",
        cwd=tmp_path,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
        timeout=1,
    )
    assert result["timed_out"] and time.monotonic() - started < 15
    missing = providers.run(
        [str(tmp_path / "absent")],
        provider="opencode",
        prompt="",
        cwd=tmp_path,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
        timeout=1,
    )
    assert missing["exit_code"] is None and missing["error"]


def test_final_message_reads_each_output_channel(tmp_path):
    stdout, output = tmp_path / "stdout", tmp_path / "output"
    stdout.write_text('not json\n{"type": "turn.completed", "usage": {"input_tokens": 5}}\n')
    output.write_text("final")
    assert providers.final_message("codex", stdout, output) == ("final", {"input_tokens": 5})
    assert providers.final_message("codex", stdout, tmp_path / "missing")[0] == ""
    stdout.write_text('{"result": "done", "is_error": true, "total_cost_usd": 1.5, "usage": {}}')
    assert providers.final_message("claude", stdout, output) == (
        "done",
        {"usage": {}, "cost_usd": 1.5, "is_error": True},
    )
    stdout.write_text("[1, 2]")
    assert providers.final_message("claude", stdout, output) == ("[1, 2]", None)
    stdout.write_text("plain review text")
    assert providers.final_message("opencode", stdout, output) == ("plain review text", None)


def test_a_hung_worker_times_out(planned, fake, monkeypatch, capsys):
    import argparse

    import scope_launch

    policy = scope_launch.load_policy()
    policy["timeouts_seconds"]["planner"] = 1
    monkeypatch.setattr(scope_launch, "load_policy", lambda: policy)
    monkeypatch.setenv("FAKE_HANG", "claude")
    monkeypatch.chdir(planned)
    args = argparse.Namespace(root=None, host="claude", role="planner", epic=EPIC, task="Write plan.md")
    with pytest.raises(SystemExit):
        scope_launch.cmd_work(args)
    assert '"status": "timed_out"' in capsys.readouterr().out


def test_a_provider_ignoring_sigterm_is_killed(tmp_path):
    script = tmp_path / "stubborn.py"
    script.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n")
    started = time.monotonic()
    result = providers.run(
        [sys.executable, str(script)],
        provider="shell",
        prompt="",
        cwd=tmp_path,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
        timeout=1,
    )
    assert result["timed_out"] and result["exit_code"] == -9 and time.monotonic() - started < 20


def test_user_decisions_are_recorded_and_reopen_upheld_findings(planned, fake):
    assert scope("scope_review.py", "status", "--epic", EPIC, "--workflow", "refine", cwd=planned)["fresh"] is False
    review(planned, "refine", "full")
    decided = scope(
        "scope_review.py",
        "decide",
        "--epic",
        EPIC,
        "--finding",
        "R1.claude.1",
        "--outcome",
        "finding_upheld",
        "--note",
        "Trim names, the user said",
        cwd=planned,
    )
    assert decided["pending"] == {"needs_disposition": ["R1.claude.1"]}
    text = (planned / EPIC_DIR / "review.md").read_text()
    assert "- R1.claude.1 · user: finding_upheld — Trim names, the user said" in text
    closed = scope(
        "scope_review.py",
        "decide",
        "--epic",
        EPIC,
        "--finding",
        "R1.claude.1",
        "--outcome",
        "rejection_upheld",
        "--note",
        "Accept the risk",
        cwd=planned,
    )
    assert closed["findings"]["R1.claude.1"]["state"] == "closed_rejected"
    assert (
        "unknown finding"
        in scope(
            "scope_review.py",
            "decide",
            "--epic",
            EPIC,
            "--finding",
            "R9.claude.1",
            "--outcome",
            "rejection_upheld",
            "--note",
            "x",
            cwd=planned,
            expect=1,
        )["error"]
    )


def test_changed_paths_keep_their_first_character_for_modified_tracked_files(planned, fake, monkeypatch):
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan")
    (planned / "pytest.ini").write_text("[pytest]\npythonpath = src\n# edited\n")
    monkeypatch.setenv("FAKE_MESSAGE", "Done.\nSTATUS: done")
    result = work(planned)
    assert result["changed"] == ["pytest.ini"]
    assert result["warnings"] == [f"planner changed files outside {EPIC_DIR}/: ['pytest.ini']"]


def test_codex_reviewers_get_the_codegraph_index_when_it_exists(planned, fake):
    (planned / ".codegraph").mkdir()
    review(planned, "refine", "full")
    codex = next(c["args"] for c in calls(fake) if c["provider"] == "codex")
    claude = next(c["args"] for c in calls(fake) if c["provider"] == "claude")
    assert codex[codex.index("--add-dir") + 1] == str(planned / ".codegraph") and "--add-dir" not in claude


def test_explicit_reviewers_must_belong_to_the_epic_and_keep_independence(planned, fake, monkeypatch):
    review(planned, "refine", "full")
    monkeypatch.setenv("FAKE_DISPOSITION", "rejected — out of scope")
    work(planned, task="Resolve the open findings in review.md.")
    unknown = review(planned, "refine", "verify", "--reviewers", "gemini", expect=1)
    assert "unknown reviewers ['gemini']" in unknown["error"]
    assert "may not verify" in review(planned, "refine", "verify", "--reviewers", "codex", expect=1)["error"]
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan")
    checked = review(planned, "implement", "check", "--context", "x", "--reviewers", "claude")
    assert checked["reviewers"][0]["name"] == "claude"


def test_a_failed_mandatory_review_is_retried_once(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_ONCE", "codex")
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "completed"), ("codex", "completed")]
    assert result["reviewers"][1]["retried_after"].startswith("failed: transient provider error")
    assert "  - first attempt: failed: transient provider error" in (planned / EPIC_DIR / "review.md").read_text()
    assert [c["provider"] for c in calls(fake)].count("codex") == 2


def test_implement_checks_are_not_retried_and_follow_this_epics_audit_reviewers(planned, fake, monkeypatch):
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan")
    monkeypatch.setenv("FAKE_FAIL_ONCE", "codex")
    checked = review(planned, "implement", "check", "--context", "S1 grew")
    assert rows(checked) == [("codex", "failed")]
    request(planned, "audit", "--replace", "codex", "opencode", "moonshot/kimi-v3", "xhigh")
    assert rows(review(planned, "implement", "check", "--context", "S1 grew")) == [("kimi-v3", "completed")]


def test_a_replaced_raisers_findings_are_verified_by_its_replacement(planned, fake, monkeypatch):
    review(planned, "refine", "full")
    work(planned, task="Resolve the open findings in review.md.")
    monkeypatch.setenv("FAKE_UNAVAILABLE", "claude")
    failed = review(planned, "refine", "verify")
    assert rows(failed) == [("claude", "unavailable")]
    assert failed["summary"]["pending"] == {"needs_verification": ["R1.claude.1"]}
    monkeypatch.setenv("FAKE_UNAVAILABLE", "")
    request(planned, "refine", "--replace", "claude", "opencode", "moonshot/kimi-v3", "high")
    replaced = review(planned, "refine", "verify")
    assert rows(replaced) == [("kimi-v3", "completed")]
    assert "- R1.claude.1 · claude: verified — [by kimi-v3]" in (planned / EPIC_DIR / "review.md").read_text()
    assert replaced["summary"]["findings_closed"] and replaced["summary"]["missing_reviews"] == ["kimi-v3"]


def test_a_removed_raisers_findings_are_verified_by_a_remaining_reviewer(planned, fake, monkeypatch):
    monkeypatch.setenv("FAKE_REFINE_FINDER", "codex")
    review(planned, "refine", "full")
    work(planned, task="Resolve the open findings in review.md.")
    removed = request(planned, "refine", "--remove", "codex")
    assert [entry["name"] for entry in removed["reviewers"]] == ["claude"]
    assert "one reviewer only: no second opinion on the work" in removed["warnings"]
    verified = review(planned, "refine", "verify")
    assert rows(verified) == [("claude", "completed")] and verified["summary"]["settled"]
    assert "- R1.codex.1 · codex: verified — [by claude]" in (planned / EPIC_DIR / "review.md").read_text()


def test_a_requested_recheck_resends_closed_findings_to_their_raiser(planned, fake, monkeypatch):
    review(planned, "refine", "full")
    work(planned, task="Resolve the open findings in review.md.")
    assert review(planned, "refine", "verify")["summary"]["settled"]
    assert "no findings need" in review(planned, "refine", "verify", expect=1)["error"]
    assert "applies to the verify" in review(planned, "refine", "full", "--recheck", expect=1)["error"]
    assert (
        "no findings need" in review(planned, "refine", "verify", "--recheck", "--finding", "R1.x.9", expect=1)["error"]
    )
    confirmed = review(planned, "refine", "verify", "--recheck", "--finding", "R1.claude.1")
    assert confirmed["reviewers"][0]["name"] == "claude" and confirmed["summary"]["settled"]
    monkeypatch.setenv("FAKE_VERIFY_OUTCOME", "still_open")
    reopened = review(planned, "refine", "verify", "--recheck")
    assert reopened["summary"]["pending"] == {"needs_disposition": ["R1.claude.1"]}

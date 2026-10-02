"""User-chosen reviewers and adjudicator, per-epic requests, OpenCode isolation, and reviewer metrics (U14–U25)."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
import yaml

from conftest import EPIC, EPIC_DIR, calls, git
from scope_common import reviewer_name
from test_launch import request, review, reviewers, rows, write_review


@pytest.mark.parametrize(
    ("model", "name"),
    [("claude-opus-5-5", "claude-opus-5-5"), ("gpt-6-astra", "gpt-6-astra"), ("zai/glm-5.3", "glm-5-3"),
     ("meta/muse-spark-1.3-contributor", "muse-spark-1-3-contributor")],
)
def test_reviewer_names_derive_from_the_model_string(model, name):
    assert reviewer_name(model) == name


def test_set_changes_one_workflow_and_warns_about_a_duplicated_adjudicator(project, fake):
    result = reviewers(project, "set", "--workflow", "refine", "--add-optional", "opencode", "moonshot/kimi-v3", "high",
                       "--adjudicator", "codex", "codex", "high")
    assert result["refine"]["preflight"] == {"claude": "ready", "codex": "ready", "kimi-v3": "ready",
                                             "adjudicator": "ready"}
    assert result["refine"]["warnings"] == ["the adjudicator codex has the same model and effort as a reviewer: it "
                                            "will judge findings its own model raised"]
    data = yaml.safe_load((project / ".scope" / "reviewers.yaml").read_text())
    assert data["refine"]["reviewers"][-1] == {"cli": "opencode", "model": "moonshot/kimi-v3", "effort": "high",
                                               "optional": True}
    assert data["refine"]["adjudicator"] == {"cli": "codex", "model": "codex", "effort": "high"}
    assert data["audit"]["adjudicator"]["model"] == "zai/glm-5.3" and "audit" not in result


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        (("--add", "gemini", "g-3", "high"), "the CLI must be one of claude, codex, opencode"),
        (("--add", "opencode", "glm-5.3", "high"), "exact provider/model string"),
        (("--add", "codex", "gpt-6-sol", "max"), "max effort only when the user explicitly asks for it"),
        (("--add", "opencode", "other/codex", "high"), "two reviewers would both be named codex"),
        (("--add", "opencode", "zai/4.5", "high"), "no reviewer name can be derived"),
        (("--remove", "gemini"), "no reviewer 'gemini'"),
        (("--remove", "claude", "--remove", "codex", "--add-optional", "opencode", "zai/glm-5.3", "high"),
         "at least one mandatory reviewer is required"),
    ],
)
def test_set_refuses_unusable_reviewers(project, fake, changes, error):
    assert error in reviewers(project, "set", *changes, expect=1)["error"]


def test_max_needs_an_explicit_request_and_the_defaults_have_no_adjudicator(project, fake):
    chosen = reviewers(project, "set", "--workflow", "audit", "--replace", "codex", "codex", "gpt-6-sol", "max",
                       "--allow-max")
    assert chosen["audit"]["reviewers"][1] == {"name": "gpt-6-sol", "cli": "codex", "model": "gpt-6-sol",
                                               "effort": "max", "optional": False}
    (project / ".scope" / "reviewers.yaml").unlink()
    shown = reviewers(project, "show", expect=1)
    assert shown["refine"]["source"] == "default"
    assert [entry["name"] for entry in shown["refine"]["reviewers"]] == ["claude-opus-5-5", "gpt-6-astra"]
    assert shown["refine"]["problems"] == ["no adjudicator: the user must choose its CLI, exact model, and effort"]
    assert "no adjudicator" in reviewers(project, "set", "--add-optional", "opencode", "zai/glm-5.3", "high",
                                         expect=1)["error"]
    created = reviewers(project, "set", "--adjudicator", "opencode", "zai/glm-5.3", "high")
    assert created["audit"]["adjudicator"]["name"] == "glm-5-3" and created["audit"]["source"] == "project"
    no_change = reviewers(project, "epic", "--epic", EPIC, "--workflow", "refine", "--requested", "x", expect=1)
    assert no_change["error"] == "no change requested"
    duplicate = request(project, "refine", "--add", "codex", "gpt-6-astra", "xhigh", expect=1)["error"]
    assert duplicate == "two reviewers would both be named gpt-6-astra"


def test_opencode_reviewers_run_in_parallel_with_private_data_directories(planned, fake, tmp_path):
    request(planned, "refine", "--add", "opencode", "moonshot/kimi-v3", "high",
            "--add", "opencode", "meta/muse-spark-1.3-contributor", "high")
    assert reviewers(planned, "show", "--epic", EPIC)["refine"]["source"] == "epic"
    result = review(planned, "refine", "full")
    assert rows(result) == [("claude", "completed"), ("codex", "completed"), ("kimi-v3", "completed"),
                            ("muse-spark-1-3-contributor", "completed")]
    runs = [entry for entry in calls(fake) if entry["provider"] == "opencode"]
    homes = {entry["data_home"] for entry in runs}
    assert len(runs) == 2 and len(homes) == 2 and all(entry["auth"] for entry in runs)
    assert str(tmp_path / "xdg-data") not in homes and not any(Path(home).exists() for home in homes)
    codex = next(entry for entry in calls(fake) if entry["provider"] == "codex")
    assert codex["data_home"] == str(tmp_path / "xdg-data")


def test_an_optional_reviewer_never_blocks_and_is_not_retried(planned, fake, monkeypatch):
    request(planned, "refine", "--add-optional", "opencode", "moonshot/kimi-v3", "high")
    monkeypatch.setenv("FAKE_FAIL", "kimi-v3")
    monkeypatch.setenv("FAKE_REFINE_FINDER", "nobody")
    failed = review(planned, "refine", "full")
    assert rows(failed)[-1] == ("kimi-v3", "failed") and failed["summary"]["settled"]
    assert [entry["name"] for entry in calls(fake)].count("kimi-v3") == 1
    text = (planned / EPIC_DIR / "review.md").read_text()
    assert "- reviewer kimi-v3 · moonshot/kimi-v3/high · failed · - · " in text and " · optional" in text
    monkeypatch.setenv("FAKE_FAIL", "")
    monkeypatch.setenv("FAKE_MINOR_FINDER", "kimi-v3")
    found = review(planned, "refine", "full")["summary"]
    assert found["findings"]["R2.kimi-v3.1"]["state"] == "optional_minor" and found["settled"]
    assert found["reviewers"][-1] == {"name": "kimi-v3", "cli": "opencode", "model": "moonshot/kimi-v3",
                                      "effort": "high", "optional": True}


REVIEWED = """## refine 1 · full · t
- commit: {sha}
- reviewer claude · claude/high · completed · changes_required · 100s
- reviewer codex · codex/high · completed · changes_required · 200s

### R1.claude.1 · major · tests
- evidence: e
- correction: c
- closure: x
- disposition: fixed — added

### R1.codex.1 · major · tests
- evidence: e
- correction: c
- closure: x
- disposition: duplicate of R1.claude.1

### R1.codex.2 · minor · docs
- evidence: e
- correction: c
- closure: x
- disposition: rejected — not needed

## refine 2 · verify · t
- commit: {sha}
- reviewer claude · claude/high · completed · done · 30s
- reviewer codex · codex/high · failed · - · 40s
- R1.claude.1 · claude: verified — ok
- R1.codex.2 · codex: maintained — still needed

## refine 3 · adjudicate · t
- commit: {sha}
- adjudicator glm-5-3 · zai/glm-5.3/high · completed · done · 50s
- R1.codex.2 · glm-5-3: rejection_upheld — fine as is

## refine 4 · verify · t
- commit: {sha}
- reviewer codex · codex/high · completed · done · 20s
- R1.claude.1 · codex: verified — ok
"""


def test_metrics_count_unique_findings_value_and_the_adjudicator(planned, fake):
    write_review(planned, REVIEWED.format(sha=git(planned, "rev-parse", "HEAD")))
    report = reviewers(planned, "metrics", "--epic", EPIC, "--workflow", "refine")
    claude, codex = report["reviewers"]
    assert claude == {"name": "claude", "models": ["claude/high"], "runs": "2", "completed": 2, "time": "2m 10s",
                      "blocking": "0 (0)", "major": "1 (0)", "minor": "0 (0)", "fixed": 1, "rejected": 0}
    assert (codex["runs"], codex["time"], codex["major"], codex["minor"], codex["fixed"], codex["rejected"]) == (
        "3 (1 failed)", "4m 20s", "1 (0)", "1 (1)", 1, 1)
    assert report["adjudicators"] == [{"name": "glm-5-3", "models": ["zai/glm-5.3/high"], "runs": "1", "completed": 1,
                                       "findings": 1, "time": "0m 50s"}]
    assert "| codex | codex/high | 3 (1 failed) | 4m 20s | 0 (0) | 1 (0) | 1 (1) | 1 | 1 |" in report["table"]
    archived = planned / "docs" / "epics" / "_implemented" / "DEMO-000-earlier"
    archived.mkdir(parents=True)
    shutil.copy(planned / EPIC_DIR / "review.md", archived / "review.md")
    everything = reviewers(planned, "metrics", "--all")
    assert everything["epics"] == 2 and everything["refine"]["reviewers"][0]["runs"] == "4"
    assert everything["audit"]["reviewers"] == [] and "| none | - | 0 | 0 | 0m 00s |" in everything["audit"]["table"]
    assert reviewers(planned, "metrics", "--epic", EPIC, expect=1)["error"] == "give --epic and --workflow, or --all"


def test_a_check_falls_back_to_a_reviewer_on_the_hosts_cli_when_no_other_exists(planned, fake):
    git(planned, "add", "-A")
    git(planned, "commit", "-q", "-m", "plan")
    request(planned, "audit", "--remove", "codex")
    assert rows(review(planned, "implement", "check", "--context", "S1 grew")) == [("claude", "completed")]

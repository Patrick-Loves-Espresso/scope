"""review.md parsing and the per-finding state rules (plan §4.3 step 5, §4.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

import scope_review as reviews
from scope_common import ScopeError

SHA = "a" * 40


def finding(fid: str, severity: str = "major", category: str = "correctness", disposition: str = "open") -> str:
    return (
        f"### {fid} · {severity} · {category}\n- evidence: e\n- correction: c\n- closure: x\n"
        f"- disposition: {disposition}\n"
    )


def round_(workflow: str, number: int, mission: str, *reviewers: str) -> str:
    lines = [f"## {workflow} {number} · {mission} · 2026-09-23T00:00:00Z", f"- commit: {SHA}"]
    lines += [
        f"- reviewer {name} · m/high · {status} · approve"
        for name, status in (entry.split(":") if ":" in entry else (entry, "completed") for entry in reviewers)
    ]
    return "\n".join(lines) + "\n"


def outcome(fid: str, by: str, what: str) -> str:
    return f"- {fid} · {by}: {what} — note\n"


def parsed(tmp_path: Path, *parts: str) -> reviews.Review:
    path = tmp_path / "review.md"
    path.write_text("# X: Review\n\n" + "\n".join(parts), encoding="utf-8")
    return reviews.parse(path)


def state(tmp_path: Path, *parts: str, workflow: str = "refine") -> dict[str, str]:
    return reviews.states(parsed(tmp_path, *parts), workflow)


def test_parse_reads_rounds_findings_outcomes_and_ignores_comments(tmp_path):
    review = parsed(
        tmp_path,
        "<!-- ### R9.claude.1 · major · tests -->",
        round_("refine", 1, "full", "claude", "codex:unavailable"),
        finding("R1.claude.1"),
        "#### Suggestions (claude)\n\n- polish\n",
        round_("refine", 2, "verify", "claude"),
        outcome("R1.claude.1", "claude", "verified"),
        outcome("R7.claude.1", "claude", "verified"),
    )
    assert list(review.findings) == ["R1.claude.1"]
    assert review.rounds[0]["commit"] == SHA
    assert [r["status"] for r in review.rounds[0]["reviewers"]] == ["completed", "unavailable"]
    assert review.findings["R1.claude.1"].outcomes == [("claude", "verified", "note", 2)]
    assert [row["provider"] for row in review.rounds[0]["reviewers"] if row["status"] == "completed"] == ["claude"]


@pytest.mark.parametrize(
    ("severity", "category", "disposition", "expected"),
    [
        ("major", "correctness", "open", "needs_disposition"),
        ("major", "correctness", "fixed — x", "needs_verification"),
        ("minor", "correctness", "fixed — x", "closed_unverified"),
        ("major", "correctness", "rejected — x", "needs_rejection_check"),
        ("minor", "correctness", "rejected — x", "accepted_tradeoff"),
        ("minor", "security", "rejected — x", "needs_rejection_check"),
        ("minor", "data_integrity", "rejected — x", "needs_rejection_check"),
        ("minor", "correctness", "disproportionate — adds a cache", "needs_rejection_check"),
        ("major", "product_decision", "open", "needs_user"),
    ],
)
def test_first_disposition_states(tmp_path, severity, category, disposition, expected):
    result = state(
        tmp_path, round_("refine", 1, "full", "claude"), finding("R1.claude.1", severity, category, disposition)
    )
    assert result == {"R1.claude.1": expected}


def test_minors_ride_along_only_when_a_pass_runs_anyway(tmp_path):
    result = state(
        tmp_path,
        round_("refine", 1, "full", "claude"),
        finding("R1.claude.1", "major", disposition="fixed"),
        finding("R1.claude.2", "minor", disposition="fixed"),
        finding("R1.claude.3", "minor", disposition="rejected"),
    )
    assert result == {
        "R1.claude.1": "needs_verification",
        "R1.claude.2": "needs_verification",
        "R1.claude.3": "needs_rejection_check",
    }


@pytest.mark.parametrize(
    ("disposition", "events", "expected"),
    [
        ("fixed", [("claude", "verified")], "closed"),
        ("fixed", [("claude", "still_open")], "needs_verification"),
        ("rejected", [("claude", "rejection_accepted")], "closed_rejected"),
        ("rejected", [("claude", "maintained")], "needs_adjudication"),
        ("rejected", [("claude", "maintained"), ("codex", "rejection_upheld")], "closed_rejected"),
        ("rejected", [("claude", "maintained"), ("codex", "product_scope")], "needs_user"),
        ("open", [("claude", "maintained"), ("codex", "finding_upheld")], "needs_disposition"),
        ("fixed", [("claude", "maintained"), ("codex", "finding_upheld")], "needs_verification"),
        ("open", [("claude", "still_open"), ("claude", "still_open")], "needs_disposition"),
        ("open", [("claude", "still_open"), ("claude", "still_open"), ("codex", "diagnosis")], "needs_disposition"),
        ("open", [("claude", "still_open"), ("codex", "diagnosis"), ("claude", "still_open")], "blocked"),
        ("open", [("claude", "still_open"), ("user", "rejection_upheld")], "closed_rejected"),
        ("rejected", [("claude", "still_open")], "needs_rejection_check"),
    ],
)
def test_outcome_transitions(tmp_path, disposition, events, expected):
    history = "".join(outcome("R1.claude.1", by, what) for by, what in events)
    result = state(
        tmp_path,
        round_("refine", 1, "full", "claude"),
        finding("R1.claude.1", "minor", disposition=disposition),
        round_("refine", 2, "verify", "claude"),
        history,
    )
    assert result["R1.claude.1"] == expected


def test_product_decision_needs_the_user_once(tmp_path):
    parts = [round_("audit", 1, "full", "claude"), finding("A1.claude.1", category="product_decision")]
    assert state(tmp_path, *parts, workflow="audit") == {"A1.claude.1": "needs_user"}
    decided = parts[:1] + [
        finding("A1.claude.1", category="product_decision", disposition="fixed"),
        outcome("A1.claude.1", "user", "finding_upheld"),
    ]
    assert state(tmp_path, *decided, workflow="audit") == {"A1.claude.1": "needs_verification"}


def test_duplicates_share_raisers_and_verification(tmp_path):
    parts = [
        round_("audit", 1, "full", "claude", "codex"),
        finding("A1.claude.1", disposition="fixed"),
        finding("A1.codex.1", disposition="duplicate of A1.claude.1"),
        round_("audit", 2, "verify", "claude"),
    ]
    review = parsed(tmp_path, *parts, outcome("A1.claude.1", "claude", "verified"))
    assert reviews.raisers(review, "A1.claude.1") == {"claude", "codex"}
    assert reviews.states(review, "audit") == {"A1.claude.1": "needs_verification", "A1.codex.1": "duplicate"}
    assert reviews.pending_providers(review, "A1.claude.1", "needs_verification") == {"codex"}
    done = parsed(
        tmp_path, *parts, outcome("A1.claude.1", "claude", "verified"), outcome("A1.claude.1", "codex", "verified")
    )
    assert set(reviews.states(done, "audit").values()) <= reviews.CLOSED


def test_duplicate_of_unknown_or_duplicate_needs_disposition(tmp_path):
    result = state(
        tmp_path,
        round_("refine", 1, "full", "claude"),
        finding("R1.claude.1", disposition="duplicate of R1.x.9"),
        finding("R1.claude.2", disposition="duplicate of R1.claude.1"),
    )
    assert result == {"R1.claude.1": "needs_disposition", "R1.claude.2": "needs_disposition"}


def test_parse_findings_accepts_none_and_well_formed_findings():
    assert reviews.parse_findings("DECISION: approve\n\n## Findings\n\nNone.\n") == []
    text = (
        "## Findings\n\n### F1\n- severity: minor\n- category: invented\n- evidence: a\n  continued\n"
        "- correction: b\n- closure: c\n"
    )
    assert reviews.parse_findings(text) == [
        {"severity": "minor", "category": "other", "evidence": "a continued", "correction": "b", "closure": "c"}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "DECISION: approve\n",
        "## Findings\n\nSome prose instead of findings.\n",
        "## Findings\n\n### F1\n- severity: huge\n- evidence: a\n- correction: b\n- closure: c\n",
        "## Findings\n\n### F1\n- severity: major\n- evidence: a\n",
    ],
)
def test_parse_findings_rejects_malformed_output(text):
    with pytest.raises(ValueError):
        reviews.parse_findings(text)


def test_parse_outcomes_takes_only_assigned_findings_and_valid_outcomes():
    allowed = {"R1.claude.1": reviews.FIX_OUTCOMES}
    text = "- R1.claude.1: verified — fine\n- R1.claude.9: verified — not assigned\n"
    assert reviews.parse_outcomes(text, allowed) == {"R1.claude.1": ("verified", "fine")}
    with pytest.raises(ValueError, match="R1.claude.1"):
        reviews.parse_outcomes("- R1.claude.1: rejection_upheld — wrong kind\n", allowed)
    assert reviews.decision("text\nDECISION: done\n") == "done" and reviews.decision("none") is None


def test_append_reopens_findings_and_rejects_unknown_ids(tmp_path):
    path = tmp_path / "review.md"
    reviews.append(path, "X", [round_("refine", 1, "full", "claude"), finding("R1.claude.1", disposition="fixed — y")])
    reviews.append(path, "X", ["## refine 2 · verify · t"], {"R1.claude.1": "still_open by claude"})
    assert "- disposition: open (reopened: still_open by claude)" in path.read_text(encoding="utf-8")
    assert reviews.parse(path).findings["R1.claude.1"].disposition == "open"
    with pytest.raises(ScopeError):
        reviews.append(path, "X", [], {"R1.claude.9": "x"})
    assert reviews.next_round(reviews.parse(path), "refine") == 3
    assert "### R1.claude.1 · major" in reviews.finding_block(reviews.parse(path), "R1.claude.1")


def test_waiver_lines_are_collected(tmp_path):
    review = parsed(tmp_path, round_("audit", 1, "full", "claude"), "## audit waiver · t\n- missing: codex audit\n")
    assert review.waiver == ["missing: codex audit"]


def test_renewed_rejection_needs_every_raiser_again(tmp_path):
    parts = [
        round_("audit", 1, "full", "claude", "codex"),
        finding("A1.claude.1", disposition="rejected — again"),
        finding("A1.codex.1", disposition="duplicate of A1.claude.1"),
        round_("audit", 2, "verify", "claude"),
        outcome("A1.claude.1", "claude", "rejection_accepted"),
        outcome("A1.claude.1", "codex", "maintained"),
        round_("audit", 3, "adjudicate", "opencode"),
        outcome("A1.claude.1", "opencode", "finding_upheld"),
        round_("audit", 4, "verify", "codex"),
        outcome("A1.claude.1", "codex", "rejection_accepted"),
    ]
    review = parsed(tmp_path, *parts)
    assert reviews.states(review, "audit")["A1.claude.1"] == "needs_rejection_check"
    assert reviews.pending_providers(review, "A1.claude.1", "needs_rejection_check") == {"claude"}


def test_a_duplicate_keeps_its_own_requirements(tmp_path):
    result = state(
        tmp_path,
        round_("audit", 1, "full", "claude", "codex"),
        finding("A1.claude.1", "minor", disposition="fixed"),
        finding("A1.codex.1", "major", "security", disposition="duplicate of A1.claude.1"),
        workflow="audit",
    )
    assert result == {"A1.claude.1": "needs_verification", "A1.codex.1": "duplicate"}
    rejected = state(
        tmp_path,
        round_("audit", 1, "full", "claude", "codex"),
        finding("A1.claude.1", "minor", disposition="rejected"),
        finding("A1.codex.1", "minor", "security", disposition="duplicate of A1.claude.1"),
        workflow="audit",
    )
    assert rejected["A1.claude.1"] == "needs_rejection_check"


def test_any_mandatory_check_makes_minors_ride_along(tmp_path):
    result = state(
        tmp_path,
        round_("refine", 1, "full", "claude"),
        finding("R1.claude.1", "minor", "security", disposition="rejected"),
        finding("R1.claude.2", "minor", disposition="fixed"),
        finding("R1.claude.3", "minor", disposition="rejected"),
    )
    assert set(result.values()) == {"needs_rejection_check", "needs_verification"}


def test_diagnosis_counts_failed_rounds_not_reviewer_answers(tmp_path):
    parts = [
        round_("audit", 1, "full", "claude", "codex"),
        finding("A1.claude.1", disposition="open"),
        finding("A1.codex.1", disposition="duplicate of A1.claude.1"),
        round_("audit", 2, "verify", "claude", "codex"),
        outcome("A1.claude.1", "claude", "still_open"),
        outcome("A1.claude.1", "codex", "still_open"),
    ]
    assert state(tmp_path, *parts, workflow="audit")["A1.claude.1"] == "needs_disposition"
    again = [*parts, round_("audit", 3, "verify", "claude"), outcome("A1.claude.1", "claude", "still_open")]
    assert state(tmp_path, *again, workflow="audit")["A1.claude.1"] == "needs_diagnosis"


def test_fallback_rows_and_waivers_are_parsed(tmp_path):
    review = parsed(
        tmp_path,
        "## audit 1 · full · t\n- commit: " + SHA + "\n"
        "- reviewer claude · m/high · unavailable · -\n- reviewer opencode · m/high · completed · approve"
        " · fallback for claude\n",
        "## audit waiver · t\n- missing: codex\n- reason: down\n",
    )
    rows = review.rounds[0]["reviewers"]
    assert rows[0]["decision"] is None and rows[1]["fallback_for"] == "claude"
    assert reviews.waived(review) == {"codex"}
    assert reviews._succeeded(review.rounds[0], set())
    assert not reviews._succeeded({"reviewers": [rows[0]]}, set())


@pytest.mark.parametrize(
    "line",
    [
        "- R1.claude.1: verified — the fix holds",
        "- R1.claude.1: verified. the fix holds",
        "- R1.claude.1: verified: the fix holds",
        "- R1.claude.1 – Verified – the fix holds",
        "- **R1.claude.1**: `verified` — the fix holds",
        "* R1.claude.1 verified, the fix holds",
        "R1.claude.1: VERIFIED — the fix holds",
    ],
)
def test_verdicts_tolerate_markdown_and_separators(line):
    assert reviews.parse_outcomes(line, {"R1.claude.1": reviews.FIX_OUTCOMES}) == {
        "R1.claude.1": ("verified", "the fix holds")
    }


def test_verdicts_stay_strict_about_ids_and_outcome_words():
    allowed = {"R1.claude.1": reviews.FIX_OUTCOMES}
    with pytest.raises(ValueError):
        reviews.parse_outcomes("- R1.claude.10: verified — other finding", allowed)
    with pytest.raises(ValueError):
        reviews.parse_outcomes("- R1.claude.1: looks verified to me", allowed)
    text = "- R1.claude.1: still_open — missing test\n- R1.claude.1: verified — later mention"
    assert reviews.parse_outcomes(text, allowed)["R1.claude.1"][0] == "still_open"


def test_decision_and_fields_tolerate_markdown():
    assert reviews.decision("**DECISION:** Changes_Required\n") == "changes_required"
    assert reviews.decision("## Decision: approve") == "approve"
    text = ("## Findings\n\n### F1\n- **Severity**: Major.\n- **category:** `Security`\n- evidence: a\n"
            "- **correction:** b\n- closure: c\n")
    assert reviews.parse_findings(text) == [
        {"severity": "major", "category": "security", "evidence": "a", "correction": "b", "closure": "c"}
    ]

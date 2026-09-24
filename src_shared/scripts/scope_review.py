#!/usr/bin/env python3
"""Read and write an epic's review.md: reviewer rounds, findings, and their state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from scope_common import ScopeError, commit_paths, emit, find_epic, git, load_policy, now, repo_root, run_cli

SEVERITIES = ("blocking", "major", "minor")
MAJOR = {"blocking", "major"}
SENSITIVE = {"security", "data_integrity"}
CATEGORIES = {
    "correctness", "feasibility", "scope", "proportionality", "over_engineering", "tests",
    "docs", "security", "data_integrity", "product_decision", "other",
}
FIX_OUTCOMES = {"verified", "still_open"}
REJECTION_OUTCOMES = {"rejection_accepted", "maintained"}
ADJUDICATION_OUTCOMES = {"rejection_upheld", "finding_upheld", "product_scope"}
REOPENING = {"still_open", "finding_upheld"}
REVIEWING = {"full", "verify", "adjudicate"}
EVIDENCE = {"review.md", "verification.yaml"}
CLOSED = {"closed", "closed_unverified", "closed_rejected", "accepted_tradeoff", "duplicate"}
PREFIX = {"refine": "R", "audit": "A"}

ROUND = re.compile(r"^## (refine|implement|audit) (\d+) · (\w+) · (\S+)$")
REVIEWER = re.compile(r"^- reviewer (\S+) · (\S+) · (\w+) · (\S+)(?: · fallback for (\w+))?")
COMMIT = re.compile(r"^- commit: ([0-9a-f]{40})$")
SIZE = re.compile(r"^- size: actual=(\d+) planned=(\d+)")
FINDING = re.compile(r"^### ([RA]\d+\.[a-z]+\.\d+) · (\w+) · (\w+)\s*$")
DISPOSITION = re.compile(r"^- disposition: (open|fixed|rejected|disproportionate|duplicate of (\S+))")
OUTCOME = re.compile(r"^- ([RA]\d+\.[a-z]+\.\d+) · (\w+): (\w+) — (.*)$")
# Reviewer output is parsed leniently about Markdown decoration and separators, strictly about content.
FIELD = re.compile(r"^[-*]\s*[*_`]*(severity|category|evidence|correction|closure)[*_`]*\s*:[*_`]*\s*(.*)$", re.I)
VERDICT_ID = re.compile(r"^[\s>#*_`-]*([RA]\d+\.[a-z]+\.\d+)(?!\d)[*_`]*(.*)$")
HEADER = """# {epic}: Review

Scope's runner appends reviewer rounds here. Authors edit a finding's
`disposition` line (`fixed — <what changed>`, `rejected — <reason>`,
`disproportionate — <the mechanism a minor fix would add>`, or
`duplicate of <finding id>`) and may raise a minor finding's severity to major.
"""


@dataclass
class Finding:
    id: str
    severity: str
    category: str
    disposition: str = "open"
    duplicate_of: str | None = None
    lines: list[str] = field(default_factory=list)
    outcomes: list[tuple[str, str, str, int]] = field(default_factory=list)  # (by, outcome, note, round)

    @property
    def raised_by(self) -> str:
        return self.id.split(".")[1]


@dataclass
class Review:
    rounds: list[dict[str, Any]]
    findings: dict[str, Finding]
    waiver: list[str]


def parse(path: Path) -> Review:
    rounds: list[dict[str, Any]] = []
    findings: dict[str, Finding] = {}
    waiver: list[str] = []
    current: Finding | None = None
    in_waiver = False
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.DOTALL) if path.is_file() else ""
    for line in text.splitlines():
        if line.startswith("## "):
            current, in_waiver = None, line.startswith("## audit waiver")
            if match := ROUND.match(line):
                rounds.append({"workflow": match[1], "number": int(match[2]), "mission": match[3],
                               "reviewers": [], "commit": None, "size": None})
            continue
        if in_waiver and line.startswith("- "):
            waiver.append(line[2:])
        if rounds and current is None and (match := REVIEWER.match(line)):
            rounds[-1]["reviewers"].append({"provider": match[1], "model": match[2], "status": match[3],
                                            "decision": None if match[4] == "-" else match[4],
                                            "fallback_for": match[5]})
        elif rounds and current is None and (match := COMMIT.match(line)):
            rounds[-1]["commit"] = match[1]
        elif rounds and current is None and (match := SIZE.match(line)):
            rounds[-1]["size"] = (int(match[1]), int(match[2]))
        elif match := FINDING.match(line):
            current = findings[match[1]] = Finding(match[1], match[2], match[3])
        elif line.startswith("#"):
            current = None
        elif match := OUTCOME.match(line):
            if match[1] in findings:
                number = rounds[-1]["number"] if rounds else 0
                findings[match[1]].outcomes.append((match[2], match[3], match[4], number))
        elif current is not None:
            current.lines.append(line)
            if match := DISPOSITION.match(line):
                current.disposition = "duplicate" if match[2] else match[1]
                current.duplicate_of = match[2]
    return Review(rounds, findings, waiver)


def raisers(review: Review, finding_id: str) -> set[str]:
    """The provider that raised a finding plus every provider whose finding duplicates it."""
    return {review.findings[finding_id].raised_by} | {
        f.raised_by for f in review.findings.values() if f.duplicate_of == finding_id
    }


def _latest(events: list[tuple], kinds: set[str]) -> dict[str, str]:
    return {event[0]: event[1] for event in events if event[1] in kinds}


def _since_reopen(events: list[tuple]) -> list[tuple]:
    """Outcomes after the finding was last reopened; earlier checks concern an earlier fix or rejection."""
    reopened = max((i for i, event in enumerate(events) if event[1] in REOPENING), default=-1)
    return events[reopened + 1:]


def _requirements(review: Review, finding: Finding) -> tuple[str, set[str]]:
    """Severity and categories a finding must satisfy, including those of its duplicates."""
    group = [finding, *(f for f in review.findings.values() if f.duplicate_of == finding.id)]
    return next(s for s in SEVERITIES if any(f.severity == s for f in group)), {f.category for f in group}


def _state(review: Review, finding: Finding, pass_needed: bool) -> str:
    if finding.disposition == "duplicate":
        target = review.findings.get(finding.duplicate_of or "")
        return "duplicate" if target and target.disposition != "duplicate" else "needs_disposition"
    events, raised = finding.outcomes, raisers(review, finding.id)
    severity, categories = _requirements(review, finding)
    kinds, recent = [event[1] for event in events], _since_reopen(events)
    decisive = [(event[0], event[1]) for event in recent if event[1] in ADJUDICATION_OUTCOMES]
    if decisive and decisive[-1] == ("user", "rejection_upheld"):
        return "closed_rejected"
    user_decided = any(event[0] == "user" for event in events)
    disputed = bool(decisive) and decisive[-1][1] == "product_scope"
    if ("product_decision" in categories and not user_decided) or disputed:
        return "needs_user"
    if finding.disposition == "open":
        diagnosed = max((i for i, kind in enumerate(kinds) if kind == "diagnosis"), default=-1)
        if diagnosed >= 0 and "still_open" in kinds[diagnosed:]:
            return "blocked"
        failed_rounds = {event[3] for event in events if event[1] == "still_open"}
        return "needs_diagnosis" if diagnosed < 0 and len(failed_rounds) >= 2 else "needs_disposition"
    if finding.disposition == "fixed":
        latest = _latest(recent, FIX_OUTCOMES)
        if all(latest.get(provider) == "verified" for provider in raised):
            return "closed"
        return "needs_verification" if severity in MAJOR or kinds or pass_needed else "closed_unverified"
    if decisive and decisive[-1][1] == "rejection_upheld":
        return "closed_rejected"
    latest = _latest(recent, REJECTION_OUTCOMES)
    if any(outcome == "maintained" for outcome in latest.values()):
        return "needs_adjudication"
    if all(latest.get(provider) == "rejection_accepted" for provider in raised):
        return "closed_rejected"
    checked = severity in MAJOR or bool(categories & SENSITIVE) or finding.disposition == "disproportionate"
    return "needs_rejection_check" if checked or kinds or pass_needed else "accepted_tradeoff"


def states(review: Review, workflow: str) -> dict[str, str]:
    scoped = [f for key, f in review.findings.items() if key.startswith(PREFIX[workflow])]
    pass_needed = any(_state(review, f, False) in ("needs_verification", "needs_rejection_check") for f in scoped)
    return {f.id: _state(review, f, pass_needed) for f in scoped}


def pending_providers(review: Review, finding_id: str, state: str) -> set[str]:
    """Raising providers that still have to verify a fix or check a rejection."""
    fixed = state == "needs_verification"
    closing, kinds = ("verified", FIX_OUTCOMES) if fixed else ("rejection_accepted", REJECTION_OUTCOMES)
    latest = _latest(_since_reopen(review.findings[finding_id].outcomes), kinds)
    return {provider for provider in raisers(review, finding_id) if latest.get(provider) != closing}


def waived(review: Review) -> set[str]:
    """Providers whose missing audit review the user explicitly waived."""
    return {line.split(":", 1)[1].strip() for line in review.waiver if line.startswith("missing:")}


def _succeeded(entry: dict[str, Any], waived_providers: set[str]) -> bool:
    """Every reviewer in the round completed, was replaced by a completed fallback, or was waived."""
    rows = entry["reviewers"]
    covered = waived_providers | {row["fallback_for"] for row in rows if row["status"] == "completed"}
    return any(row["status"] == "completed" for row in rows) and all(
        row["status"] == "completed" or row["provider"] in covered for row in rows)


def _unchanged(root: Path, epic: Path, workflow: str, base: str, head: str = "HEAD") -> bool:
    """Nothing but evidence changed from base to head (for HEAD, the working tree too); refinement looks
    only at the epic folder and also allows the approved criteria and their record."""
    scope = ["--", str(epic.relative_to(root))] if workflow == "refine" else []
    changed = set(git(root, "diff", "--name-only", base, head, *scope).splitlines())
    if head == "HEAD":
        changed |= {line[3:] for line in git(root, "status", "--porcelain", *scope).splitlines()}
    allowed = EVIDENCE | ({"acceptance-criteria.md", "approvals.yaml"} if workflow == "refine" else set())
    return all(Path(path).name in allowed and path.startswith("docs/epics/") for path in changed)


def coverage(review: Review, workflow: str, root: Path, epic: Path, standard: list[str]) -> dict[str, Any]:
    """Who reviewed the content of the latest full round (counting full rounds on the same content); fresh when
    nothing but evidence changed since the last successful reviewing round."""
    rounds = [entry for entry in review.rounds if entry["workflow"] == workflow and entry["commit"]]
    skip = waived(review) if workflow == "audit" else set()
    good = [entry for entry in rounds if entry["mission"] in REVIEWING and _succeeded(entry, skip)]
    fulls = [entry for entry in rounds if entry["mission"] == "full"]
    same = [entry for entry in fulls if _unchanged(root, epic, workflow, entry["commit"], fulls[-1]["commit"])]
    completed = {row["provider"] for entry in same for row in entry["reviewers"] if row["status"] == "completed"}
    replaced = {row["fallback_for"] for entry in same for row in entry["reviewers"] if row["status"] == "completed"}
    return {
        "providers_completed": sorted(completed),
        "missing_reviews": [provider for provider in standard if provider not in completed | replaced],
        "complete": len(completed) >= 2,
        "fresh": bool(good) and _unchanged(root, epic, workflow, good[-1]["commit"]),
    }


def summary(review: Review, workflow: str, root: Path, epic: Path) -> dict[str, Any]:
    finding_states = states(review, workflow)
    pending: dict[str, list[str]] = {}
    for key, state in finding_states.items():
        if state not in CLOSED:
            pending.setdefault(state, []).append(key)
    reviewed = coverage(review, workflow, root, epic, load_policy()["standard_reviewers"])
    return {
        "workflow": workflow,
        **reviewed,
        "findings_closed": not pending,
        "settled": reviewed["complete"] and reviewed["fresh"] and not pending,
        "pending": pending,
        "findings": {
            key: {"severity": review.findings[key].severity, "category": review.findings[key].category,
                  "raised_by": sorted(raisers(review, key)), "state": state}
            for key, state in finding_states.items()
        },
        "waiver": review.waiver,
        "waived": sorted(waived(review)),
    }


def next_round(review: Review, workflow: str) -> int:
    return 1 + sum(1 for entry in review.rounds if entry["workflow"] == workflow)


def finding_block(review: Review, finding_id: str) -> str:
    finding = review.findings[finding_id]
    history = [f"- {finding_id} · {event[0]}: {event[1]} — {event[2]}" for event in finding.outcomes]
    header = f"### {finding.id} · {finding.severity} · {finding.category}"
    return "\n".join([header, *finding.lines, *history]).rstrip()


def append(path: Path, epic_id: str, lines: list[str], resets: dict[str, str] | None = None) -> None:
    """Append a round; `resets` maps finding ids to reopen, with the reason."""
    text = path.read_text(encoding="utf-8") if path.is_file() else HEADER.format(epic=epic_id)
    for finding_id, reason in (resets or {}).items():
        block = re.search(rf"^### {re.escape(finding_id)} · .*?(?=^#|\Z)", text, re.MULTILINE | re.DOTALL)
        if block is None:
            raise ScopeError(f"finding {finding_id} not found in review.md")
        reopened = re.sub(r"^- disposition: .*$", f"- disposition: open (reopened: {reason})",
                          block[0], count=1, flags=re.MULTILINE)
        text = text[:block.start()] + reopened + text[block.end():]
    path.write_text(text.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_findings(text: str) -> list[dict[str, str]]:
    """Findings from a full-review output; raises ValueError on malformed output."""
    body = re.search(r"^## Findings\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if body is None:
        raise ValueError("missing ## Findings section")
    if body[1].strip().lower().rstrip(".") == "none":
        return []
    findings = []
    for chunk in re.split(r"^### .*$", body[1], flags=re.MULTILINE)[1:]:
        fields: dict[str, str] = {}
        key = None
        for line in chunk.strip().splitlines():
            if match := FIELD.match(line.strip()):
                key = match[1].lower()
                fields[key] = match[2].strip(" *_`")
            elif key and line.strip():
                fields[key] += " " + line.strip()
        fields["severity"] = fields.get("severity", "").lower().rstrip(".,;")
        fields["category"] = fields.get("category", "").lower().rstrip(".,;")
        if fields["severity"] not in SEVERITIES:
            raise ValueError(f"finding has invalid severity: {fields.get('severity')!r}")
        missing = [name for name in ("evidence", "correction", "closure") if not fields.get(name)]
        if missing:
            raise ValueError(f"finding lacks {', '.join(missing)}")
        if fields["category"] not in CATEGORIES:
            fields["category"] = "other"
        findings.append(fields)
    if not findings:
        raise ValueError("## Findings is neither None nor a list of ### findings")
    return findings


def parse_outcomes(text: str, allowed: dict[str, set[str]]) -> dict[str, tuple[str, str]]:
    """Outcomes for exactly the assigned findings; anything else is ignored."""
    result = {}
    for line in text.splitlines():
        match = VERDICT_ID.match(line.strip())
        if not match or match[1] not in allowed or match[1] in result:
            continue
        words = "|".join(sorted(allowed[match[1]]))
        verdict = re.match(rf"[\s:.,;—–-]*[*_`]*({words})\b[*_`]*[\s:.,;—–-]*(.*)$", match[2], re.I)
        if verdict:
            result[match[1]] = (verdict[1].lower(), verdict[2].strip())
    missing = sorted(set(allowed) - set(result))
    if missing:
        raise ValueError(f"no valid outcome for {', '.join(missing)}")
    return result


def decision(text: str) -> str | None:
    match = re.search(r"^[\s>#*_`-]*DECISION[*_`\s]*:[*_`\s]*([A-Za-z_]+)", text, re.MULTILINE | re.IGNORECASE)
    return match[1].lower() if match else None


def cmd_status(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    epic = find_epic(root, args.epic)
    emit(summary(parse(epic / "review.md"), args.workflow, root, epic))


def cmd_decide(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    epic = find_epic(root, args.epic)
    path = epic / "review.md"
    review = parse(path)
    if args.finding not in review.findings:
        raise ScopeError(f"unknown finding {args.finding}")
    workflow = "refine" if args.finding.startswith("R") else "audit"
    number = next_round(review, workflow)
    lines = [f"## {workflow} {number} · decision · {now()}",
             f"- {args.finding} · user: {args.outcome} — {' '.join(args.note.split())}"]
    resets = {args.finding: "the user upheld the finding"} if args.outcome == "finding_upheld" else None
    append(path, args.epic, lines, resets)
    commit_paths(root, [path], f"review({args.epic}): {workflow} round {number} user decision")
    emit(summary(parse(path), workflow, root, epic))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--workflow", choices=("refine", "audit"), required=True)
    decide = commands.add_parser("decide")
    decide.add_argument("--finding", required=True)
    decide.add_argument("--outcome", choices=("finding_upheld", "rejection_upheld"), required=True)
    decide.add_argument("--note", required=True)
    for sub in (status, decide):
        sub.add_argument("--epic", required=True)
        sub.add_argument("--root")
    args = parser.parse_args()
    run_cli(cmd_status if args.command == "status" else cmd_decide, args)


if __name__ == "__main__":
    main()

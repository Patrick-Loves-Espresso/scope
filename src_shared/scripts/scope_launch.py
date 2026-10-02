#!/usr/bin/env python3
"""Launch Scope workers and independent reviewers through provider CLIs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shlex
import sys
from typing import Any

import scope_providers as providers
import scope_review as reviews
import scope_verify
from scope_common import (
    SCOPE_ROOT, ScopeError, base_commit, commit_paths, emit, find_epic, git, load_policy,
    now, repo_root, run_cli, run_dir, section,
)

STATUS = re.compile(r"^STATUS:\s*(done|needs_user|needs_check|blocked)\s*$", re.MULTILINE)
TEMPLATES = SCOPE_ROOT / "skills" / "project-documentation" / "templates-technical-arc42-c4" / "epic"
GOVERNANCE = SCOPE_ROOT / "governance" / "simplicity-and-size.md"
FOCUS = {
    "refine": SCOPE_ROOT / "commands" / "epic_refine" / "reviewer-refinement.md",
    "audit": SCOPE_ROOT / "commands" / "audit_epic" / "reviewer-audit.md",
}
DECISIONS = {
    "full": {"approve", "changes_required"},
    "verify": {"done"},
    "adjudicate": {"done"},
    "check": {"approved", "concerns", "implementation_growth", "scope_growth"},
    "diagnose": {"diagnosed"},
}
REVIEW_MISSIONS = {"full", "verify", "adjudicate", "diagnose"}
MISSIONS = {"refine": REVIEW_MISSIONS, "audit": REVIEW_MISSIONS, "implement": {"check"}}
RECHECKABLE = {"closed", "closed_unverified", "closed_rejected", "accepted_tradeoff"}
REPORTED = ("name", "role", "cli", "model", "effort", "optional", "status", "decision", "error", "duration_seconds",
            "retried_after")
CODEGRAPH = (
    "if `.codegraph/` exists and the `codegraph` CLI is installed, use its read-only queries "
    "(`explore`, `node`, `query`, `callers`, `callees`, `impact`, `affected`) before broad searches and "
    "confirm what matters in the source. Never run `init`, `index`, `sync`, or anything that changes the index."
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip()


def _instructions(host: str) -> str:
    if host == "claude":
        return ("Claude safe mode does not load project instructions: read CLAUDE.md once if it exists, "
                "otherwise AGENTS.md.")
    return "follow the AGENTS.md instructions Codex supplies."


def worker_prompt(role: str, host: str, root: Path, epic: Path, epic_id: str, task: str) -> str:
    runner = f"{shlex.quote(sys.executable)} {shlex.quote(str(SCOPE_ROOT / 'scripts' / 'scope_verify.py'))}"
    lines = [
        _read(SCOPE_ROOT / "workers" / f"{role}.md"), "", "## Assignment", "",
        f"- Epic: `{epic_id}`, folder `{epic.relative_to(root)}`",
        f"- Working root: `{root}`",
        f"- Templates: `{TEMPLATES}`",
        f"- Repository instructions: {_instructions(host)}",
        f"- CodeGraph: {CODEGRAPH}",
    ]
    if role == "implementer":
        lines += [
            f"- Size-check command: `{runner} size --epic {epic_id}`",
            f"- Milestone verification command: `{runner} run --epic {epic_id} --milestone <milestone id>`",
        ]
    lines += ["", "## Task", "", task, "", "## Governance (appended by Scope)", "", _read(GOVERNANCE), ""]
    return "\n".join(lines)


def cmd_work(args: argparse.Namespace) -> None:
    root, policy = repo_root(args.root), load_policy()
    epic = find_epic(root, args.epic)
    selected, timeouts = policy["workers"][args.host][args.role], policy["timeouts_seconds"]
    problem = providers.preflight(args.host, selected["model"], timeouts["preflight"],
                                  policy["min_cli_versions"].get(args.host))
    if problem:
        raise ScopeError(problem)
    out = run_dir(root, args.epic, args.role)
    prompt = worker_prompt(args.role, args.host, root, epic, args.epic, args.task)
    (out / "prompt.md").write_text(prompt, encoding="utf-8")
    add_dirs = []
    if args.host == "codex" and args.role == "implementer":
        add_dirs = [Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")), out.parent]
    argv = providers.command(
        args.host, model=selected["model"], effort=selected["effort"], root=root, write=True,
        output_path=out / "message.md", prompt=prompt, add_dirs=add_dirs,
    )
    start = git(root, "rev-parse", "HEAD")
    result = providers.run(argv, provider=args.host, prompt=prompt, cwd=root, stdout_path=out / "stdout.log",
                           stderr_path=out / "stderr.log", timeout=timeouts[args.role])
    message, usage = providers.final_message(args.host, out / "stdout.log", out / "message.md")
    (out / "message.md").write_text(message, encoding="utf-8")
    statuses = STATUS.findall(message)
    status = statuses[-1] if statuses else "unknown"
    if result["timed_out"]:
        status = "timed_out"
    elif result["exit_code"] != 0 or (usage or {}).get("is_error"):
        status = "failed"
    dirty = git(root, "status", "--porcelain").splitlines()
    changed = sorted(set(git(root, "diff", "--name-only", start, "HEAD").splitlines()) | {line[3:] for line in dirty})
    warnings = []
    folder = f"{epic.relative_to(root)}/"
    if args.role == "planner" and any(not path.startswith(folder) for path in changed):
        warnings.append(f"planner changed files outside {folder}: {[p for p in changed if not p.startswith(folder)]}")
    if args.role == "implementer" and dirty:
        warnings.append(f"uncommitted changes remain: {[line[3:] for line in dirty]}")
    emit({
        "role": args.role, "provider": args.host, "model": selected["model"], "status": status,
        **result, "usage": usage, "commits": git(root, "log", "--oneline", f"{start}..HEAD").splitlines(),
        "changed": changed, "warnings": warnings, "logs": str(out),
        "message_tail": "\n".join(message.strip().splitlines()[-15:]),
    })


NO_ADJUDICATOR = ("no adjudicator is set for the {workflow} workflow; the user must choose one with "
                  "/scope_reviewers (Codex: scope:reviewers)")


def _verifier(raiser: str, chosen: dict[str, Any], raised: set[str]) -> dict[str, Any]:
    """Who checks a raiser's finding: the raiser, else its replacement, else the first mandatory reviewer that did
    not raise it (U18: a removed reviewer's open findings are still checked)."""
    by_name = {entry["name"]: entry for entry in chosen["reviewers"]}
    name = raiser
    while name not in by_name and name in chosen["replaced"]:
        name = chosen["replaced"][name]
    mandatory = [entry for entry in chosen["reviewers"] if not entry["optional"]]
    return by_name.get(name) or next((entry for entry in mandatory if entry["name"] not in raised), mandatory[0])


def _assignments(args: argparse.Namespace, review: reviews.Review, chosen: dict[str, Any]) -> list[dict[str, Any]]:
    by_name = {entry["name"]: entry for entry in chosen["reviewers"]}
    explicit = args.reviewers.split(",") if args.reviewers else []
    unknown = [name for name in explicit if name not in by_name]
    if unknown:
        raise ScopeError(f"unknown reviewers {unknown}; this epic's reviewers: {sorted(by_name)}")
    if args.mission == "full":
        return [{"reviewer": entry, "findings": {}, "credit": {}}
                for entry in ([by_name[name] for name in explicit] or chosen["reviewers"])]
    if args.mission in ("check", "diagnose"):
        if args.mission == "check" and not args.context:
            raise ScopeError("--context is required for a check")
        if args.mission == "diagnose" and args.finding not in review.findings:
            raise ScopeError("--finding must name an existing finding for a diagnosis")
        mandatory = [entry for entry in chosen["reviewers"] if not entry["optional"]]
        other = next((entry for entry in mandatory if entry["cli"] != args.host), mandatory[0])  # L24
        findings = {args.finding: {"diagnosis"}} if args.mission == "diagnose" else {}
        return [{"reviewer": by_name[explicit[0]] if explicit else other, "findings": findings, "credit": {}}]
    finding_states = reviews.states(review, args.workflow, {e["name"] for e in chosen["reviewers"] if e["optional"]})
    if args.mission == "adjudicate":
        if explicit:
            raise ScopeError("adjudication goes to the adjudicator; change it with scope_reviewers.py epic")
        findings = {key: reviews.ADJUDICATION_OUTCOMES for key, state in finding_states.items()
                    if state == "needs_adjudication" and args.finding in (None, key)}
        if not findings:
            raise ScopeError("no findings need the adjudicate mission")
        return [{"reviewer": chosen["adjudicator"], "findings": findings, "credit": {}}]
    grouped: dict[str, dict[str, Any]] = {}
    for finding_id, state in finding_states.items():
        finding = review.findings[finding_id]
        if args.finding not in (None, finding_id):
            continue
        if state in ("needs_verification", "needs_rejection_check"):
            allowed = reviews.FIX_OUTCOMES if state == "needs_verification" else reviews.REJECTION_OUTCOMES
            pending = reviews.pending_providers(review, finding_id, state)
        elif args.recheck and state in RECHECKABLE and finding.disposition != "open":
            allowed = reviews.FIX_OUTCOMES if finding.disposition == "fixed" else reviews.REJECTION_OUTCOMES
            pending = reviews.raisers(review, finding_id)
        else:
            continue
        raised = reviews.raisers(review, finding_id)
        for raiser in sorted(pending):
            verifier = _verifier(raiser, chosen, raised)
            if explicit and explicit[0] not in (raiser, verifier["name"]):
                raise ScopeError(f"{explicit[0]} may not verify {finding_id} for {raiser}; "
                                 f"eligible: {sorted({raiser, verifier['name']})}")
            entry = by_name[explicit[0]] if explicit else verifier
            job = grouped.setdefault(entry["name"], {"reviewer": entry, "findings": {}, "credit": {}})
            job["findings"][finding_id] = allowed
            job["credit"].setdefault(finding_id, []).append(raiser)
    if not grouped:
        raise ScopeError(f"no findings need the {args.mission} mission")
    return list(grouped.values())


def reviewer_prompt(args: argparse.Namespace, root: Path, epic: Path, review: reviews.Review, findings: dict) -> str:
    parts = [_read(SCOPE_ROOT / "workers" / "reviewer.md")]
    if args.mission == "full":
        parts.append(_read(FOCUS[args.workflow]))
    assignment = [
        "## Assignment", "", f"- Mission: {args.mission}", f"- Epic: `{args.epic}`, folder `{epic.relative_to(root)}`",
        f"- Repository root: `{root}`", f"- CodeGraph: {CODEGRAPH}",
    ]
    if args.workflow != "refine":
        assignment.append(f"- Changes under review: `git diff {base_commit(root)}...HEAD`")
    parts.append("\n".join(assignment))
    if findings:
        blocks = "\n\n".join(reviews.finding_block(review, key) for key in findings)
        parts.append(f"## Findings under review\n\n{blocks}")
    if args.mission == "check":
        parts.append(f"## Change to check\n\n{args.context}")
    parts.append("## Governance (appended by Scope)\n\n" + _read(GOVERNANCE))
    return "\n\n".join(parts) + "\n"


def _run_reviewer(args, root, epic, review, policy, out, assignment, suffix: str = "") -> dict[str, Any]:
    spec = assignment["reviewer"]
    name, cli = spec["name"], spec["cli"]
    row = {**spec, "decision": None, "duration_seconds": 0.0, "credit": assignment["credit"]}
    problem = providers.preflight(cli, spec["model"], policy["timeouts_seconds"]["preflight"],
                                  policy["min_cli_versions"].get(cli))
    if problem:
        return {**row, "status": "unavailable", "error": problem}
    prompt = reviewer_prompt(args, root, epic, review, assignment["findings"])
    label = f"{spec['role']}-{name}{suffix}"
    (out / f"prompt-{label}.md").write_text(prompt, encoding="utf-8")
    timeout = policy["timeouts_seconds"]["reviewer"]
    index = root / ".codegraph"  # a read-only Codex sandbox cannot open the CodeGraph database otherwise
    argv = providers.command(cli, model=spec["model"], effort=spec["effort"], root=root, write=False,
                             output_path=out / f"review-{label}.md", prompt=prompt,
                             read_only_commands=policy["reviewer_read_only_commands"],
                             add_dirs=[index] if cli == "codex" and index.is_dir() else [])
    result = providers.run(argv, provider=cli, prompt=prompt, cwd=root, stdout_path=out / f"{label}.stdout",
                           stderr_path=out / f"{label}.stderr", timeout=timeout)
    text, _ = providers.final_message(cli, out / f"{label}.stdout", out / f"review-{label}.md")
    row.update(duration_seconds=result["duration_seconds"], text=text)
    if result["timed_out"] or result["exit_code"] != 0 or not text.strip():
        stderr = (out / f"{label}.stderr").read_text(encoding="utf-8", errors="replace").strip()
        return {**row, "status": "timed_out" if result["timed_out"] else "failed", "error": stderr[-500:]}
    row["decision"] = reviews.decision(text)
    try:
        if row["decision"] not in DECISIONS[args.mission]:
            raise ValueError(f"missing or invalid DECISION line: {row['decision']!r}")
        if args.mission == "full":
            row["findings"] = reviews.parse_findings(text)
        elif args.mission in ("verify", "adjudicate"):
            row["outcomes"] = reviews.parse_outcomes(text, assignment["findings"])
    except ValueError as exc:
        return {**row, "status": "invalid_output", "error": str(exc)}
    return {**row, "status": "completed"}


def _review_with_retry(args, root, epic, review, policy, out, assignment) -> dict[str, Any]:
    """Run one reviewer; in refinement and audit, retry a failed mandatory reviewer or adjudicator once."""
    row = _run_reviewer(args, root, epic, review, policy, out, assignment)
    mandatory = not assignment["reviewer"]["optional"] and args.workflow in ("refine", "audit")
    for attempt in range(1, (policy["mandatory_retries"] if mandatory else 0) + 1):
        if row["status"] == "completed":
            break
        first, spent = f"{row['status']}: {row.get('error') or ''}", row["duration_seconds"]
        row = {**_run_reviewer(args, root, epic, review, policy, out, assignment, f"-retry{attempt}"),
               "retried_after": first}
        row["duration_seconds"] += spent
    return row


def _round_lines(args, number: int, commit: str, rows: list[dict], size: dict | None) -> tuple[list[str], dict]:
    lines, resets = [f"## {args.workflow} {number} · {args.mission} · {now()}", f"- commit: {commit}"], {}
    for row in rows:
        lines.append(f"- {row['role']} {row['name']} · {row['model']}/{row['effort']} · {row['status']} · "
                     f"{row['decision'] or '-'} · {round(row['duration_seconds'])}s"
                     + (" · optional" if row["optional"] else ""))
        if row.get("retried_after"):
            lines.append(f"  - first attempt: {' '.join(row['retried_after'].split())[:300]}")
        if row.get("error"):
            lines.append(f"  - error: {' '.join(row['error'].split())[:300]}")
    if args.mission == "check":
        if size:
            lines.append(f"- size: actual={size['actual_loc']} planned={size['planned_loc']} ratio={size['ratio']}")
        lines.append(f"- context: {' '.join(args.context.split())}")
    for row in (row for row in rows if row["status"] == "completed"):
        name = row["name"]
        for index, finding in enumerate(row.get("findings", []), start=1):
            heading = f"### {reviews.PREFIX[args.workflow]}{number}.{name}.{index}"
            lines += ["", f"{heading} · {finding['severity']} · {finding['category']}",
                      *(f"- {key}: {finding[key]}" for key in ("evidence", "correction", "closure")),
                      "- disposition: open"]
        for finding_id, (outcome, note) in row.get("outcomes", {}).items():
            for credited in row["credit"].get(finding_id) or [name]:  # a replacement checks for the raiser (U18)
                prefix = f"[by {name}] " if credited != name else ""
                lines.append(f"- {finding_id} · {credited}: {outcome} — {prefix}{' '.join(note.split())}")
            if outcome in reviews.REOPENING:
                resets[finding_id] = f"{outcome} by {name}"
        if args.mission == "diagnose":
            lines.append(f"- {args.finding} · {name}: diagnosis — see Diagnosis ({name}) below")
        extra = {"full": "Suggestions", "diagnose": "Diagnosis", "check": "Rationale"}.get(args.mission)
        body = section(row["text"], extra) if extra else None
        if body and body.lower() not in ("none", "- none"):
            lines += ["", f"#### {extra} ({name})", "", body]
    return lines, resets


def cmd_review(args: argparse.Namespace) -> None:
    if args.mission not in MISSIONS[args.workflow]:
        raise ScopeError(f"the {args.workflow} workflow has no {args.mission} mission")
    root, policy = repo_root(args.root), load_policy()
    epic = find_epic(root, args.epic)
    path = epic / "review.md"
    review = reviews.parse(path)
    if args.recheck and args.mission != "verify":
        raise ScopeError("--recheck applies to the verify mission")
    chosen = reviews.settings(review, args.workflow, root, policy)
    if args.workflow != "implement" and not chosen["adjudicator"]:
        raise ScopeError(NO_ADJUDICATOR.format(workflow=args.workflow))  # L30
    assignments = _assignments(args, review, chosen)
    number = reviews.next_round(review, args.workflow)
    if args.workflow == "refine":
        commit_paths(root, [epic], f"refine({args.epic}): plan revision for review round {number}")
    elif git(root, "status", "--porcelain"):
        raise ScopeError("commit or discard every change before a review; the round records the reviewed commit")
    commit = git(root, "rev-parse", "HEAD")
    size = scope_verify.size_report(root, epic, policy) if args.size else None
    out = run_dir(root, args.epic, f"{args.workflow}-{args.mission}")
    run_one = lambda assignment: _review_with_retry(args, root, epic, review, policy, out, assignment)  # noqa: E731
    with ThreadPoolExecutor(max_workers=len(assignments)) as pool:
        rows = list(pool.map(run_one, assignments))
    lines, resets = _round_lines(args, number, commit, rows, size)
    reviews.append(path, args.epic, lines, resets)
    commit_paths(root, [path], f"review({args.epic}): {args.workflow} round {number} {args.mission}")
    updated = reviews.parse(path)
    emit({
        "workflow": args.workflow, "round": number, "mission": args.mission, "reviewed_commit": commit,
        "logs": str(out), "size": size,
        "reviewers": [{key: row.get(key) for key in REPORTED} for row in rows],
        "summary": None if args.workflow == "implement" else reviews.summary(updated, args.workflow, root, epic),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    work = commands.add_parser("work")
    work.add_argument("--role", choices=("planner", "implementer"), required=True)
    work.add_argument("--task", required=True)
    review = commands.add_parser("review")
    review.add_argument("--workflow", choices=tuple(MISSIONS), required=True)
    review.add_argument("--mission", choices=tuple(DECISIONS), required=True)
    review.add_argument("--finding")
    review.add_argument("--context")
    review.add_argument("--size", action="store_true", help="record the current size report (size overrun checks)")
    for sub in (work, review):
        sub.add_argument("--host", choices=("claude", "codex"), required=True)
        sub.add_argument("--epic", required=True)
        sub.add_argument("--root")
    review.add_argument("--reviewers", help="comma list of this epic's reviewers to run (default: all)")
    review.add_argument("--recheck", action="store_true", help="verify: also re-send closed findings to their raiser")
    args = parser.parse_args()
    run_cli({"work": cmd_work, "review": cmd_review}[args.command], args)


if __name__ == "__main__":
    main()

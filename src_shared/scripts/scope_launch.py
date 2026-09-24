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
REPORTED = ("provider", "model", "status", "decision", "error", "duration_seconds", "fallback_for", "retried_after")
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
    problem = providers.preflight(args.host, selected["model"], timeouts["preflight"])
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
        output_path=out / "message.md", prompt=prompt, timeout=timeouts[args.role], add_dirs=add_dirs,
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


def _assignments(args: argparse.Namespace, review: reviews.Review, policy: dict[str, Any]) -> list[dict[str, Any]]:
    standard, fallback = policy["standard_reviewers"], policy["fallback_reviewer"]
    explicit = args.providers.split(",") if args.providers else None
    if args.mission == "full":
        return [{"provider": provider, "findings": {}} for provider in explicit or standard]
    if args.mission in ("check", "diagnose"):
        if args.mission == "check" and not args.context:
            raise ScopeError("--context is required for a check")
        if args.mission == "diagnose" and args.finding not in review.findings:
            raise ScopeError("--finding must name an existing finding for a diagnosis")
        independent = [provider for provider in standard if provider != args.host] or [fallback]
        if explicit and explicit[0] == args.host:
            raise ScopeError(f"a {args.mission} must come from a provider other than the author ({args.host})")
        findings = {args.finding: {"diagnosis"}} if args.mission == "diagnose" else {}
        return [{"provider": (explicit or independent)[0], "findings": findings}]
    grouped: dict[str, dict[str, set[str]]] = {}
    for finding_id, state in reviews.states(review, args.workflow).items():
        finding = review.findings[finding_id]
        if args.finding not in (None, finding_id):
            continue
        if args.mission == "verify" and state in ("needs_verification", "needs_rejection_check"):
            allowed = reviews.FIX_OUTCOMES if state == "needs_verification" else reviews.REJECTION_OUTCOMES
            chosen = reviews.pending_providers(review, finding_id, state)
        elif args.mission == "verify" and args.recheck and state in RECHECKABLE and finding.disposition != "open":
            allowed = reviews.FIX_OUTCOMES if finding.disposition == "fixed" else reviews.REJECTION_OUTCOMES
            chosen = reviews.raisers(review, finding_id)
        elif args.mission == "adjudicate" and state == "needs_adjudication":
            raised = reviews.raisers(review, finding_id)
            uninvolved = [p for p in [*standard, fallback] if p not in raised]
            if not uninvolved:
                raise ScopeError(f"{finding_id}: every reviewer raised it; settle it with an executable check "
                                 "or the user")
            allowed, chosen = reviews.ADJUDICATION_OUTCOMES, {uninvolved[0]}
        else:
            continue
        eligible = reviews.raisers(review, finding_id) if args.mission == "verify" else set(uninvolved)
        if explicit and explicit[0] not in eligible:
            raise ScopeError(f"{explicit[0]} may not {args.mission} {finding_id}; eligible: {sorted(eligible)}")
        for provider in ([explicit[0]] if explicit else sorted(chosen)):
            grouped.setdefault(provider, {})[finding_id] = allowed
    if not grouped:
        raise ScopeError(f"no findings need the {args.mission} mission")
    return [{"provider": provider, "findings": findings} for provider, findings in grouped.items()]


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


def _run_reviewer(args, root, epic, review, policy, out, assignment, name: str = "") -> dict[str, Any]:
    provider = assignment["provider"]
    name = name or provider
    selected = policy["reviewers"]["refine" if args.workflow == "refine" else "audit"][provider]
    row = {"provider": provider, "model": selected["model"], "effort": selected["effort"], "decision": None,
           "fallback_for": assignment.get("fallback_for")}
    problem = providers.preflight(provider, selected["model"], policy["timeouts_seconds"]["preflight"])
    if problem:
        return {**row, "status": "unavailable", "error": problem}
    prompt = reviewer_prompt(args, root, epic, review, assignment["findings"])
    (out / f"prompt-{name}.md").write_text(prompt, encoding="utf-8")
    timeout = policy["timeouts_seconds"]["reviewer"]
    index = root / ".codegraph"  # a read-only Codex sandbox cannot open the CodeGraph database otherwise
    argv = providers.command(provider, model=selected["model"], effort=selected["effort"], root=root, write=False,
                             output_path=out / f"review-{name}.md", prompt=prompt, timeout=timeout,
                             read_only_commands=policy["reviewer_read_only_commands"],
                             add_dirs=[index] if provider == "codex" and index.is_dir() else [])
    result = providers.run(argv, provider=provider, prompt=prompt, cwd=root, stdout_path=out / f"{name}.stdout",
                           stderr_path=out / f"{name}.stderr", timeout=timeout)
    text, _ = providers.final_message(provider, out / f"{name}.stdout", out / f"review-{name}.md")
    row.update(duration_seconds=result["duration_seconds"], text=text)
    if result["timed_out"] or result["exit_code"] != 0 or not text.strip():
        stderr = (out / f"{name}.stderr").read_text(encoding="utf-8", errors="replace").strip()
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
    """Run one reviewer; in refinement and audit, retry a failed Claude or Codex reviewer once."""
    row = _run_reviewer(args, root, epic, review, policy, out, assignment)
    standard = assignment["provider"] in policy["standard_reviewers"] and args.workflow in ("refine", "audit")
    retries = policy["standard_reviewer_retries"] if standard else 0
    for attempt in range(1, retries + 1):
        if row["status"] == "completed":
            break
        first = f"{row['status']}: {row.get('error') or ''}"
        row = {**_run_reviewer(args, root, epic, review, policy, out, assignment,
                               f"{assignment['provider']}-retry{attempt}"), "retried_after": first}
    return row


def _round_lines(args, number: int, commit: str, rows: list[dict], size: dict | None) -> tuple[list[str], dict]:
    lines, resets = [f"## {args.workflow} {number} · {args.mission} · {now()}", f"- commit: {commit}"], {}
    if args.replace:
        lines.append(f"- replacement for {args.replace}, approved by: {' '.join(args.approved_by.split())}")
    for row in rows:
        suffix = f" · fallback for {row['fallback_for']}" if row.get("fallback_for") else ""
        lines.append(f"- reviewer {row['provider']} · {row['model']}/{row['effort']} · {row['status']} · "
                     f"{row['decision'] or '-'}{suffix}")
        if row.get("retried_after"):
            lines.append(f"  - first attempt: {' '.join(row['retried_after'].split())[:300]}")
        if row.get("error"):
            lines.append(f"  - error: {' '.join(row['error'].split())[:300]}")
    if args.mission == "check":
        if size:
            lines.append(f"- size: actual={size['actual_loc']} planned={size['planned_loc']} ratio={size['ratio']}")
        lines.append(f"- context: {' '.join(args.context.split())}")
    for row in (row for row in rows if row["status"] == "completed"):
        provider = row["provider"]
        replaced = row.get("fallback_for")
        credited, note_prefix = (replaced, f"[fallback {provider}] ") if replaced else (provider, "")
        for index, finding in enumerate(row.get("findings", []), start=1):
            heading = f"### {reviews.PREFIX[args.workflow]}{number}.{provider}.{index}"
            lines += ["", f"{heading} · {finding['severity']} · {finding['category']}",
                      *(f"- {key}: {finding[key]}" for key in ("evidence", "correction", "closure")),
                      "- disposition: open"]
        for finding_id, (outcome, note) in row.get("outcomes", {}).items():
            lines.append(f"- {finding_id} · {credited}: {outcome} — {note_prefix}{' '.join(note.split())}")
            if outcome in reviews.REOPENING:
                resets[finding_id] = f"{outcome} by {provider}"
        if args.mission == "diagnose":
            lines.append(f"- {args.finding} · {provider}: diagnosis — see Diagnosis ({provider}) below")
        extra = {"full": "Suggestions", "diagnose": "Diagnosis", "check": "Rationale"}.get(args.mission)
        body = section(row["text"], extra) if extra else None
        if body and body.lower() not in ("none", "- none"):
            lines += ["", f"#### {extra} ({provider})", "", body]
    return lines, resets


def _replacement(args, review: reviews.Review, policy: dict[str, Any], assignments: list[dict]) -> list[dict]:
    """The fallback takes over one standard reviewer's work, only with the user's recorded approval."""
    fallback = policy["fallback_reviewer"]
    if args.replace not in policy["standard_reviewers"] or not args.approved_by:
        raise ScopeError("--replace names claude or codex and needs --approved-by with the user's approval")
    replaced = [{**a, "provider": fallback, "fallback_for": args.replace} for a in assignments
                if a["provider"] == args.replace]
    if not replaced:
        raise ScopeError(f"no {args.mission} work is assigned to {args.replace}")
    if args.mission == "adjudicate" and any(fallback in reviews.raisers(review, key)
                                            for a in replaced for key in a["findings"]):
        raise ScopeError(f"{fallback} raised a finding under adjudication; it cannot replace {args.replace}")
    return replaced


def cmd_review(args: argparse.Namespace) -> None:
    if args.mission not in MISSIONS[args.workflow]:
        raise ScopeError(f"the {args.workflow} workflow has no {args.mission} mission")
    root, policy = repo_root(args.root), load_policy()
    epic = find_epic(root, args.epic)
    path = epic / "review.md"
    review = reviews.parse(path)
    if args.recheck and args.mission != "verify":
        raise ScopeError("--recheck applies to the verify mission")
    assignments = _assignments(args, review, policy)
    if args.replace:
        assignments = _replacement(args, review, policy, assignments)
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


def cmd_preflight(args: argparse.Namespace) -> None:
    policy = load_policy()
    default = [*policy["standard_reviewers"], policy["fallback_reviewer"]]
    names = args.providers.split(",") if args.providers else default
    timeout = policy["timeouts_seconds"]["preflight"]
    emit({name: providers.preflight(name, policy["reviewers"]["audit"][name]["model"], timeout) or "ready"
          for name in names})


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
    review.add_argument("--providers")
    review.add_argument("--recheck", action="store_true", help="verify: also re-send closed findings to their raiser")
    review.add_argument("--replace", help="run the fallback in place of this standard reviewer (needs --approved-by)")
    review.add_argument("--approved-by", help="the user's explicit approval of the replacement")
    commands.add_parser("preflight").add_argument("--providers")
    args = parser.parse_args()
    run_cli({"work": cmd_work, "review": cmd_review, "preflight": cmd_preflight}[args.command], args)


if __name__ == "__main__":
    main()

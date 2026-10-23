#!/usr/bin/env python3
"""Run an overseer's drift audit: the project's audit reviewers, read-only and in parallel, on one filled prompt."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from typing import Any

from scope_common import (
    SCOPE_ROOT, ScopeError, emit, find_epic, git, load_policy, project_settings, repo_root, run_cli, run_dir,
)
from scope_launch import CODEGRAPH, repository_instructions, run_read_only

TEMPLATE = SCOPE_ROOT / "commands" / "overseer" / "drift-audit.md"
CHECKPOINTS = ("gate1", "plan", "merge", "targeted")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9-]+>")
REPORTED = ("name", "cli", "model", "effort", "optional", "status", "error", "duration_seconds", "retried_after",
            "report")


def audit_prompt(text: str, root: Path, cli: str) -> str:
    """The overseer's filled prompt with the assignment every auditor needs."""
    assignment = [
        "## Assignment (appended by Scope)", "",
        f"- Repository root: `{root}`",
        f"- Repository instructions: {repository_instructions(cli)}",
        f"- CodeGraph: {CODEGRAPH}",
        "- Read-only: modify, stage or commit nothing. Return the report as your final answer.",
    ]
    return text.rstrip() + "\n\n" + "\n".join(assignment) + "\n"


def _audit(spec: dict[str, Any], text: str, root: Path, policy: dict[str, Any], out: Path) -> dict[str, Any]:
    """One auditor's report; a failed mandatory auditor is retried as many times as the policy allows."""
    prompt, label = audit_prompt(text, root, spec["cli"]), f"auditor-{spec['name']}"
    row = {**spec, **run_read_only(spec, prompt, root, policy, out, label)}
    for attempt in range(1, (0 if spec["optional"] else policy["mandatory_retries"]) + 1):
        if row["status"] == "completed":
            break
        first, spent = f"{row['status']}: {row.get('error') or ''}", row["duration_seconds"]
        row = {**spec, **run_read_only(spec, prompt, root, policy, out, f"{label}-retry{attempt}"),
               "retried_after": first}
        row["duration_seconds"] += spent
    if row["status"] == "completed":
        report = out / f"report-{spec['name']}.md"
        report.write_text(row["text"].rstrip() + "\n", encoding="utf-8")
        row["report"] = str(report)
    return row


def cmd_audit(args: argparse.Namespace) -> None:
    if not NAME.fullmatch(args.name):
        raise ScopeError("--name takes lowercase letters, digits and hyphens")
    root, policy = repo_root(args.root), load_policy()
    epics = [part.strip() for part in args.epic.split(",") if part.strip()]
    for epic in epics:
        find_epic(root, epic)
    prompt = Path(args.prompt)
    if not prompt.is_file():
        raise ScopeError(f"no prompt file at {prompt}")
    text = prompt.read_text(encoding="utf-8")
    left = sorted(set(PLACEHOLDER.findall(TEMPLATE.read_text(encoding="utf-8"))) & set(PLACEHOLDER.findall(text)))
    if left:
        raise ScopeError(f"fill the template's placeholders first: {', '.join(left)}")
    auditors = project_settings(root, policy)["audit"]["reviewers"]
    by_name = {entry["name"]: entry for entry in auditors}
    if args.reviewers:
        unknown = [name for name in args.reviewers.split(",") if name not in by_name]
        if unknown:
            raise ScopeError(f"unknown reviewers {unknown}; the project's audit reviewers: {sorted(by_name)}")
        auditors = [by_name[name] for name in args.reviewers.split(",")]
    out = run_dir(root, f"oversight-{args.name}", f"{'+'.join(epics)}-{args.checkpoint}")
    with ThreadPoolExecutor(max_workers=len(auditors)) as pool:
        rows = list(pool.map(lambda spec: _audit(spec, text, root, policy, out), auditors))
    emit({
        "oversight": args.name, "epics": epics, "checkpoint": args.checkpoint, "commit": git(root, "rev-parse", "HEAD"),
        "logs": str(out), "auditors": [{key: row.get(key) for key in REPORTED} for row in rows],
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="run the project's audit reviewers on a filled drift-audit prompt")
    audit.add_argument("--name", required=True, help="the oversight's name, as in docs/oversight/<name>.md")
    audit.add_argument("--epic", required=True, help="the audited epic, or a comma list for a cross-epic check")
    audit.add_argument("--checkpoint", choices=CHECKPOINTS, required=True)
    audit.add_argument("--prompt", required=True, help="the filled copy of commands/overseer/drift-audit.md")
    audit.add_argument("--reviewers", help="comma list of the project's audit reviewers to run (default: all)")
    audit.add_argument("--root")
    args = parser.parse_args()
    run_cli({"audit": cmd_audit}[args.command], args)


if __name__ == "__main__":
    main()

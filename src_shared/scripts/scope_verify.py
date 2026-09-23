#!/usr/bin/env python3
"""Run an epic's declared validation on a committed state, record it, and report size."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any
import xml.etree.ElementTree as ElementTree

from pygments.lexers import get_lexer_for_filename
from pygments.token import Comment, String
from pygments.util import ClassNotFound
import yaml

import scope_review as reviews
from scope_common import (
    ScopeError, base_commit, commit_paths, criteria_ids, emit, find_epic, git, load_policy,
    main_root, now, repo_root, run_cli, run_dir, scope_blocks,
)

ALL_CRITERIA = {"final", "remediation"}
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def code_line_numbers(text: str, filename: str) -> set[int]:
    """Line numbers holding code: blank lines, comments, and docstrings excluded."""
    try:
        lexer = get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return {number for number, line in enumerate(text.splitlines(), start=1) if line.strip()}
    lines, number = set(), 1
    for token, value in lexer.get_tokens(text):
        if token not in Comment and token not in String.Doc:
            lines.update(number + offset for offset, part in enumerate(value.split("\n")) if part.strip())
        number += value.count("\n")
    return lines


def _show(root: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True)
    return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else None


def _accepted_baseline(epic: Path) -> tuple[int, int]:
    """(actual, planned) recorded by the last check that classified growth as implementation growth."""
    baseline = (0, 0)
    for entry in reviews.parse(epic / "review.md").rounds:
        decisions = {reviewer["decision"] for reviewer in entry["reviewers"] if reviewer["status"] == "completed"}
        if entry["workflow"] == "implement" and entry["size"] and "implementation_growth" in decisions:
            baseline = entry["size"]
    return baseline


def size_report(root: Path, epic: Path, policy: dict[str, Any]) -> dict[str, Any]:
    plan, limits = scope_blocks(epic / "plan.md"), policy["size"]
    production, tests = plan.get("production_paths") or [], plan.get("test_paths") or []
    base = base_commit(root)
    added: dict[str, set[int]] = {}
    current = None
    diff = git(root, "diff", "-U0", "--no-color", base, "HEAD", "--", *production) if production else ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current:
                added[current] = set()
        elif current and (match := HUNK.match(line)):
            start, count = int(match[1]), int(match[2] if match[2] is not None else 1)
            added[current].update(range(start, start + count))
    modules, actual = [], 0
    for path, numbers in sorted(added.items()):
        head = code_line_numbers(_show(root, "HEAD", path) or "", path)
        before = _show(root, base, path)
        before_lines = len(code_line_numbers(before, path)) if before is not None else 0
        actual += len(numbers & head)
        grew_over = before_lines > limits["module_limit_code_lines"] and len(head) > before_lines
        modules.append({"path": path, "code_lines": len(head), "new": before is None,
                        "over_target": len(head) > limits["module_target_code_lines"],
                        "over_limit": len(head) > limits["module_limit_code_lines"] and not grew_over,
                        "grew_over_limit": grew_over})
    planned = sum(int(story.get("estimate_loc") or 0) for story in plan.get("stories") or []
                  if story.get("status") == "done")
    base_actual, base_planned = _accepted_baseline(epic)
    over = planned > base_planned and actual - base_actual > limits["growth_threshold"] * (planned - base_planned)
    changed = git(root, "diff", "--name-only", base, "HEAD").splitlines()
    expected = [*production, *tests, "docs/"]
    return {
        "actual_loc": actual, "planned_loc": planned, "ratio": round(actual / planned, 2) if planned else None,
        "threshold": limits["growth_threshold"], "accepted_baseline": [base_actual, base_planned], "over": over,
        "estimate": plan.get("estimate"), "new_modules": sum(1 for module in modules if module["new"]),
        "modules": modules,
        "scope_warnings": [path for path in changed if not any(path.startswith(prefix) for prefix in expected)],
    }


def junit_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        status, reason = "passed", None
        for child in case:
            if child.tag in ("failure", "error"):
                status = "failed" if child.tag == "failure" else "error"
            elif child.tag == "skipped":
                status, reason = "skipped", (child.get("message") or child.text or "").strip()
        name = f"{case.get('classname') or ''}.{case.get('name') or ''}".strip(".")
        cases.append({"id": name, "status": status, "reason": reason})
    return cases


def _normalize(reference: str) -> str:
    return re.sub(r"\.py(?=\.|$)", "", reference.replace("::", ".").replace("/", "."))


def criterion_status(references: list[str], cases: list[dict[str, Any]], exit_codes: dict[str, int | None]) -> str:
    statuses = []
    for reference in references:
        if reference.startswith("command:"):
            code = exit_codes.get(reference.split(":", 1)[1])
            statuses.append("missing" if reference.split(":", 1)[1] not in exit_codes
                            else "passed_by_exit_code" if code == 0 else "failed")
            continue
        pattern = re.compile(rf"(^|\.){re.escape(_normalize(reference))}($|\[)")
        matched = [case["status"] for case in cases if pattern.search(case["id"])]
        statuses.append("missing" if not matched else "passed" if set(matched) == {"passed"}
                        else "skipped" if set(matched) <= {"passed", "skipped"} else "failed")
    for worst in ("failed", "missing", "skipped", "passed_by_exit_code"):
        if worst in statuses:
            return worst
    return "passed" if statuses else "missing"


def _run_command(root: Path, entry: dict[str, Any], out: Path, timeout: float) -> tuple[dict[str, Any], list, list]:
    junit = out / f"{entry['id']}.xml"
    row: dict[str, Any] = {"id": entry["id"], "command": entry["command"], "gap": None}
    problems: list[str] = []
    with (out / f"{entry['id']}.log").open("wb") as log:
        try:
            completed = subprocess.run(entry["command"].replace("{junit}", shlex.quote(str(junit))), shell=True,
                                       cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            row["exit_code"] = completed.returncode
        except subprocess.TimeoutExpired:
            row["exit_code"] = None
            problems.append(f"{entry['id']}: timed out after {timeout}s")
    if row["exit_code"] not in (0, None):
        problems.append(f"{entry['id']}: exit code {row['exit_code']}")
    cases: list[dict[str, Any]] = []
    if "{junit}" in entry["command"]:
        try:
            cases = junit_cases(junit)
        except (OSError, ElementTree.ParseError) as exc:
            problems.append(f"{entry['id']}: JUnit output missing or unreadable ({exc})")
        counts = {f"{status}s" if status == "error" else status: sum(1 for case in cases if case["status"] == status)
                  for status in ("passed", "failed", "error", "skipped")}
        row["counts"] = {"tests": len(cases), **counts}
        row["skips"] = [{"test": case["id"], "reason": case["reason"]} for case in cases if case["status"] == "skipped"]
        if counts["failed"] or counts["errors"]:
            problems.append(f"{entry['id']}: {counts['failed']} failed, {counts['errors']} errors")
        problems += [f"{entry['id']}: skip without a reason: {skip['test']}"
                     for skip in row["skips"] if not skip["reason"]]
    elif entry.get("type") == "test":
        row["gap"] = "no JUnit output; exit code only"
    return row, cases, problems


def cmd_run(args: argparse.Namespace) -> None:
    root, policy = repo_root(args.root), load_policy()
    epic = find_epic(root, args.epic)
    if git(root, "status", "--porcelain"):
        raise ScopeError("commit every change first; the runner verifies a committed state")
    plan = scope_blocks(epic / "plan.md")
    if not plan.get("validation"):
        raise ScopeError("plan.md declares no validation commands")
    tested, out = git(root, "rev-parse", "HEAD"), run_dir(root, args.epic, f"verify-{args.milestone}")
    rows, cases, problems = [], [], []
    for entry in plan["validation"]:
        row, found, issues = _run_command(root, entry, out, policy["timeouts_seconds"]["validation_command"])
        rows.append(row)
        cases += found
        problems += issues
    done = {ac for story in plan.get("stories") or [] if story.get("status") == "done"
            for ac in story.get("criteria") or []}
    required = set(criteria_ids(epic)) if args.milestone in ALL_CRITERIA else done
    exit_codes = {row["id"]: row["exit_code"] for row in rows}
    acceptance = {}
    for criterion in criteria_ids(epic):
        status = criterion_status(plan.get("acceptance_tests", {}).get(criterion) or [], cases, exit_codes)
        acceptance[criterion] = status if criterion in required else f"pending ({status})"
        if criterion in required and status not in ("passed", "passed_by_exit_code"):
            problems.append(f"{criterion}: mapped tests {status}")
    size = size_report(root, epic, policy)
    record = {
        "milestone": args.milestone, "tested_commit": tested, "at": now(),
        "outcome": "failed" if problems else "passed", "logs": str(out.relative_to(main_root(root))),
        "commands": rows, "acceptance": acceptance,
        "size": {key: size[key] for key in ("actual_loc", "planned_loc", "ratio")},
        "unavailable_evidence": plan.get("unavailable_evidence") or [], "problems": problems,
    }
    path = epic / "verification.yaml"
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    runs = (existing or {}).get("runs") or []
    path.write_text(yaml.safe_dump({"runs": [*runs, record]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    commit_paths(root, [path], f"verify({args.epic}): {args.milestone} verification record")
    emit(record, 1 if problems else 0)


def coverage(root: Path, epic: Path) -> dict[str, Any]:
    """Whether the latest verification run passed on all criteria and still covers HEAD."""
    path = epic / "verification.yaml"
    runs = ((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("runs") or []) if path.is_file() else []
    if not runs:
        return {"covered": False, "reason": "no verification run recorded"}
    latest = runs[-1]
    changed = git(root, "diff", "--name-only", latest["tested_commit"], "HEAD").splitlines()
    changed += [line[3:] for line in git(root, "status", "--porcelain").splitlines()]
    outside = [p for p in changed if Path(p).name not in reviews.EVIDENCE or not p.startswith("docs/epics/")]
    reason = ("latest run failed" if latest["outcome"] != "passed"
              else f"latest run is the {latest['milestone']} milestone, not final or remediation"
              if latest["milestone"] not in ALL_CRITERIA
              else f"changed since the tested commit: {outside}" if outside else None)
    return {"covered": reason is None, "reason": reason, "tested_commit": latest["tested_commit"], "run": latest}


def cmd_covers(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    result = coverage(root, find_epic(root, args.epic))
    emit(result, 0 if result["covered"] else 1)


def cmd_size(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    emit(size_report(root, find_epic(root, args.epic), load_policy()))


def cmd_lines(args: argparse.Namespace) -> None:
    emit({path: len(code_line_numbers(Path(path).read_text(encoding="utf-8"), path)) for path in args.files})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--milestone", required=True)
    for name in ("run", "size", "covers"):
        sub = run if name == "run" else commands.add_parser(name)
        sub.add_argument("--epic", required=True)
        sub.add_argument("--root")
    commands.add_parser("lines").add_argument("files", nargs="+")
    args = parser.parse_args()
    run_cli({"run": cmd_run, "size": cmd_size, "covers": cmd_covers, "lines": cmd_lines}[args.command], args)


if __name__ == "__main__":
    main()

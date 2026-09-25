#!/usr/bin/env python3
"""Structural checks, the Gate 1 approval record, Gate 2, and the pinned merge."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import difflib
from pathlib import Path
from typing import Any

import yaml

import scope_review as reviews
import scope_verify
from scope_common import (
    CRITERION, ScopeError, base_commit, commit_paths, criteria_ids, emit, find_epic, git, load_policy,
    main_root, now, repo_root, run_cli, scope_blocks, section,
)

PLAN_SECTIONS = ("Purpose", "Approach", "Milestones", "Stories", "Validation", "Documentation obligations",
                 "Dependencies", "Concepts", "Progress log", "Decision log", "Surprises and discoveries")


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def check_criteria(root: Path, epic: Path) -> dict[str, Any]:
    path = epic / "acceptance-criteria.md"
    if not path.is_file():
        raise ScopeError(f"{path.relative_to(root)} does not exist")
    text, errors = path.read_text(encoding="utf-8"), []
    estimate = scope_blocks(path).get("size_estimate") or {}
    if not (_count(estimate.get("production_loc")) and _count(estimate.get("files")) and estimate.get("rationale")):
        errors.append("size_estimate needs production_loc, files, and a one-line rationale")
    ids = criteria_ids(epic)
    if not ids:
        errors.append("no criteria: use headings like '### AC-001: <observable outcome>'")
    errors += [f"duplicate criterion {key}" for key, count in Counter(ids).items() if count > 1]
    if not (section(text, "Not building") or "").strip():
        errors.append("the 'Not building' section is missing or empty")
    questions = section(text, "Open product questions")
    approval: dict[str, Any] = {"status": "not_approved"}
    record = epic / "approvals.yaml"
    if record.is_file():
        approved = yaml.safe_load(record.read_text(encoding="utf-8"))["acceptance_criteria"]
        current = git(root, "hash-object", str(path))  # no -w: the check writes nothing to the repository
        approval = {**approved, "status": "approved" if current == approved["blob"] else "changed"}
        if current != approved["blob"]:
            before = git(root, "cat-file", "-p", approved["blob"])
            before_loc = (scope_blocks(path, before).get("size_estimate") or {}).get("production_loc")
            approval["delta"] = (f"{len(CRITERION.findall(before))} → {len(ids)} criteria, estimate "
                                 f"{before_loc} → {estimate.get('production_loc')} LoC")
            approval["diff"] = "\n".join(difflib.unified_diff(
                before.splitlines(), text.splitlines(), "approved", "current", lineterm=""))
    return {
        "errors": errors, "criteria": ids, "size_estimate": estimate, "approval": approval,
        "open_questions": questions is not None and questions.strip().rstrip(".").lower() != "none",
    }


def check_plan(root: Path, epic: Path, policy: dict[str, Any]) -> dict[str, Any]:
    path = epic / "plan.md"
    if not path.is_file():
        raise ScopeError(f"{path.relative_to(root)} does not exist")
    text, data, errors = path.read_text(encoding="utf-8"), scope_blocks(path), []
    errors += [f"missing section '## {name}'" for name in PLAN_SECTIONS if section(text, name) is None]
    estimate = data.get("estimate") or {}
    if not (_count(estimate.get("production_loc")) and _count(estimate.get("files"))):
        errors.append("estimate needs production_loc and files")
    if not data.get("production_paths"):
        errors.append("production_paths must list where production code lives")
    stories, maximum = data.get("stories") or [], policy["size"]["story_complexity_max"]
    story_ids = [story.get("id") for story in stories]
    errors += ["stories: none declared"] if not stories else []
    errors += [f"duplicate story {key}" for key, count in Counter(story_ids).items() if count > 1]
    for story in stories:
        label = story.get("id", "?")
        if not (story.get("title") and story.get("milestone") and isinstance(story.get("criteria"), list)):
            errors.append(f"{label}: needs title, milestone, and a criteria list")
        if not (_count(story.get("complexity")) and story["complexity"] <= 10 and _count(story.get("estimate_loc"))):
            errors.append(f"{label}: complexity 0-10 and estimate_loc are required")
        elif story["complexity"] > maximum and not story.get("complexity_exception"):
            errors.append(f"{label}: complexity {story['complexity']} is above {maximum}; "
                          "split it or record complexity_exception")
        if story.get("status") not in ("todo", "done"):
            errors.append(f"{label}: status must be todo or done")
    ids = criteria_ids(epic)
    planned = {ac for story in stories for ac in story.get("criteria") or []}
    errors += [f"{ac} is delivered by no story" for ac in ids if ac not in planned]
    errors += [f"unknown criterion {ac} in stories" for ac in sorted(planned - set(ids))]
    commands = data.get("validation") or []
    command_ids = [entry.get("id") for entry in commands]
    errors += ["validation: no commands declared"] if not commands else []
    errors += [f"duplicate validation id {key}" for key, count in Counter(command_ids).items() if count > 1]
    errors += [f"validation {entry.get('id')}: needs id, command, and type test or check" for entry in commands
               if not (entry.get("id") and entry.get("command") and entry.get("type") in ("test", "check"))]
    tests = data.get("acceptance_tests") or {}
    errors += [f"{ac}: no tests mapped in acceptance_tests" for ac in ids if not tests.get(ac)]
    errors += [f"{ac}: unknown validation command in {ref}" for ac, refs in tests.items() for ref in refs or []
               if ref.startswith("command:") and ref.split(":", 1)[1] not in command_ids]
    docs = data.get("docs")
    if not isinstance(docs, list):
        errors.append("docs: declare the documentation obligations (an empty list when there are none)")
    errors += [f"doc obligation {entry}: needs a target and an owner story" for entry in docs or []
               if not (entry.get("target") and entry.get("story") in story_ids)]
    total = sum(int(story.get("estimate_loc") or 0) for story in stories)
    warnings = [f"story estimates sum to {total}, plan estimate is {estimate.get('production_loc')}"] \
        if total != estimate.get("production_loc") else []
    return {"errors": errors, "warnings": warnings, "stories": len(stories), "estimate": estimate}


def gate2(root: Path, epic: Path, policy: dict[str, Any]) -> dict[str, Any]:
    problems = []
    criteria = check_criteria(root, epic)
    if criteria["errors"] or criteria["approval"]["status"] != "approved":
        problems.append(f"acceptance criteria are {criteria['approval']['status']}; renewed Gate 1 approval needed")
    review = reviews.parse(epic / "review.md")
    audit = reviews.summary(review, "audit", root, epic)
    if not audit["findings_closed"]:
        problems.append(f"audit findings still open: {audit['pending']}")
    if not audit["fresh"]:
        problems.append("the branch changed after the last audit review round; review the change")
    unwaived = [provider for provider in audit["missing_reviews"] if provider not in audit["waived"]]
    if not audit["complete"] and (unwaived or not audit["providers_completed"]):
        problems.append(f"audit incomplete: completed {audit['providers_completed']}, missing {unwaived}; retry "
                        "the missing reviewers, or the user may explicitly waive each missing review")
    covered = scope_verify.coverage(root, epic)
    if not covered["covered"]:
        problems.append(f"verification does not cover the branch head: {covered['reason']}")
    head, base = git(root, "rev-parse", "HEAD"), base_commit(root)
    size = scope_verify.size_report(root, epic, policy)
    plan_text = (epic / "plan.md").read_text(encoding="utf-8")
    plan_estimate = size["estimate"] or {}
    waived = f"INCOMPLETE (waiver: {'; '.join(audit['waiver'])})" if audit["waiver"] else "INCOMPLETE"
    verdict = "passed" if audit["complete"] else waived
    closed = {state: [key for key, row in audit["findings"].items() if row["state"] == state]
              for state in ("closed", "closed_unverified", "closed_rejected", "accepted_tradeoff")}
    run = covered.get("run") or {}
    docs = [p for p in git(root, "diff", "--name-only", base, "HEAD", "--", "docs/").splitlines()
            if not p.startswith("docs/epics/")]
    lines = [
        f"## Gate 2: {epic.name}", "", f"**Commit to merge:** `{head}`", "",
        "### Diffstat", "", "```text", git(root, "diff", "--stat", base, "HEAD"), "```", "",
        "### Size against the plan", "",
        f"- Production code lines added: {size['actual_loc']} (plan {plan_estimate.get('production_loc')} LoC / "
        f"{plan_estimate.get('files')} files; Gate 1 {criteria['size_estimate'].get('production_loc')} LoC / "
        f"{criteria['size_estimate'].get('files')} files)",
        f"- New production modules: {size['new_modules']}",
        f"- Modules over the limit: {[m['path'] for m in size['modules'] if m['over_limit'] or m['grew_over_limit']]}",
        f"- Changes outside the planned paths: {size['scope_warnings']}",
        f"- plan.md: {len(plan_text.splitlines())} lines", "",
        "### Concepts (planned and actual)", "", section(plan_text, "Concepts") or "(missing)", "",
        "### Audit", "", f"- Verdict: {verdict}", f"- Reviewers completed: {audit['providers_completed']}",
        *(f"- {state.replace('_', ' ')}: {keys}" for state, keys in closed.items() if keys), "",
        "### Verification", "",
        f"- {run.get('milestone')} run on `{run.get('tested_commit')}`: {run.get('outcome')}",
        *(f"- {row['id']}: exit {row['exit_code']}, {row.get('counts') or row.get('gap') or 'exit code only'}"
          for row in run.get("commands") or []), "",
        "### Documentation changed", "", *(f"- {path}" for path in docs or ["(none)"]), "",
        "### Decision log", "", section(plan_text, "Decision log") or "(empty)", "",
        "### Commits", "", "```text", git(root, "log", "--oneline", f"{base}..HEAD"), "```",
    ]
    return {"ready": not problems, "problems": problems, "commit": head, "summary": "\n".join(lines)}


def cmd_criteria(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    result = check_criteria(root, find_epic(root, args.epic))
    emit(result, 1 if result["errors"] else 3 if result["approval"]["status"] == "changed" else 0)


def cmd_approve(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    epic = find_epic(root, args.epic)
    result = check_criteria(root, epic)
    if result["errors"] or result["open_questions"]:
        raise ScopeError(f"not approvable: {result['errors'] or 'open product questions remain'}")
    path = epic / "acceptance-criteria.md"
    commit_paths(root, [path], f"refine({args.epic}): acceptance criteria for Gate 1")
    relative = str(path.relative_to(root))
    record = {"acceptance_criteria": {
        "blob": git(root, "rev-parse", f"HEAD:{relative}"),
        "commit": git(root, "log", "-1", "--format=%H", "--", relative),
        "approved_by": " ".join(args.source.split()), "approved_on": date.today().isoformat(),
    }}
    approvals = epic / "approvals.yaml"
    approvals.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    commit_paths(root, [approvals], f"refine({args.epic}): approve acceptance criteria (Gate 1)")
    emit(record)


def cmd_plan(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    result = check_plan(root, find_epic(root, args.epic), load_policy())
    emit(result, 1 if result["errors"] else 0)


def cmd_gate2(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    result = gate2(root, find_epic(root, args.epic), load_policy())
    emit(result, 0 if result["ready"] else 1)


def cmd_merge(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    epic, main = find_epic(root, args.epic), main_root(root)
    if main == root:
        raise ScopeError("run the merge from the epic worktree")
    result = gate2(root, epic, load_policy())
    if not result["ready"]:
        raise ScopeError(f"Gate 2 checks fail: {result['problems']}")
    if args.commit != result["commit"]:
        raise ScopeError(f"the branch is at {result['commit']}, not the approved {args.commit}; ask for approval again")
    if git(main, "status", "--porcelain", "--untracked-files=no"):
        raise ScopeError(f"the main checkout {main} has uncommitted changes")
    trailers = (f"Scope-Approved-Commit: {args.commit}\nScope-Approved-By: {' '.join(args.approver.split())}\n"
                f"Scope-Approved-On: {now()}")
    try:
        git(main, "merge", "--no-ff", "--no-edit", "-m", f"merge({args.epic}): {epic.name}", "-m", trailers,
            args.commit)
    except ScopeError as exc:
        git(main, "merge", "--abort", check=False)
        raise ScopeError(f"merge failed and was aborted: {exc}") from exc
    removed = git(main, "worktree", "remove", str(root), check=False)
    emit({"merge_commit": git(main, "rev-parse", "HEAD"), "approved_commit": args.commit,
          "branch": git(main, "branch", "--show-current"), "trailers": trailers.splitlines(),
          "worktree_removed": not root.exists(), "worktree_message": removed})


def cmd_waive(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    epic = find_epic(root, args.epic)
    path = epic / "review.md"
    audit = reviews.summary(reviews.parse(path), "audit", root, epic)
    if audit["complete"]:
        raise ScopeError("the audit is complete; there is nothing to waive")
    if args.missing not in audit["missing_reviews"]:
        raise ScopeError(f"{args.missing} is not a missing review; missing: {audit['missing_reviews']}")
    reviews.append(path, args.epic, [
        f"## audit waiver · {now()}", f"- missing: {args.missing}",
        f"- approved_by: {' '.join(args.approver.split())}", f"- reason: {' '.join(args.reason.split())}",
        "- effect: recorded quality risk; the audit stays incomplete",
    ])
    commit_paths(root, [path], f"audit({args.epic}): record audit waiver")
    emit({"waived": args.missing, "commit": git(root, "rev-parse", "HEAD")})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    subs = {name: commands.add_parser(name) for name in ("criteria", "approve", "plan", "gate2", "merge", "waive")}
    subs["approve"].add_argument("--source", required=True)
    subs["merge"].add_argument("--commit", required=True)
    for name in ("merge", "waive"):
        subs[name].add_argument("--approver", required=True)
    subs["waive"].add_argument("--missing", required=True)
    subs["waive"].add_argument("--reason", required=True)
    for sub in subs.values():
        sub.add_argument("--epic", required=True)
        sub.add_argument("--root")
    args = parser.parse_args()
    handlers = {"criteria": cmd_criteria, "approve": cmd_approve, "plan": cmd_plan, "gate2": cmd_gate2,
                "merge": cmd_merge, "waive": cmd_waive}
    run_cli(handlers[args.command], args)


if __name__ == "__main__":
    main()

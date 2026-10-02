#!/usr/bin/env python3
"""The project's and each epic's reviewers and adjudicator, their preflight, and reviewer metrics."""

from __future__ import annotations

import argparse
import re
from typing import Any

import yaml

import scope_providers as providers
import scope_review as reviews
from scope_common import (
    CLIS, WORKFLOWS, ScopeError, commit_paths, emit, find_epic, load_policy, now, project_settings, repo_root,
    reviewer, reviewers_file, run_cli,
)

SHOWN = ("name", "cli", "model", "effort", "optional")


def _entry(values: list[str], role: str = "reviewer", optional: bool = False) -> dict[str, Any]:
    cli, model, effort = values
    return reviewer({"cli": cli, "model": model, "effort": effort, "optional": optional}, role)


def apply(current: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """The settings after the requested removals, replacements, additions, and adjudicator."""
    chosen = {**current, "reviewers": list(current["reviewers"]), "replaced": dict(current["replaced"])}

    def index(name: str) -> int:
        """A reviewer by name or exact model, or by CLI when exactly one reviewer uses that CLI."""
        for position, entry in enumerate(chosen["reviewers"]):
            if name in (entry["name"], entry["model"]):
                return position
        by_cli = [position for position, entry in enumerate(chosen["reviewers"]) if entry["cli"] == name]
        if len(by_cli) == 1:
            return by_cli[0]
        raise ScopeError(f"no reviewer {name!r}; reviewers: {[entry['name'] for entry in chosen['reviewers']]}")

    for name in args.remove or []:
        chosen["reviewers"].pop(index(name))
    for old, *new in args.replace or []:
        position = index(old)
        gone = chosen["reviewers"][position]["name"]
        entry = _entry(new, optional=chosen["reviewers"][position]["optional"])
        chosen["reviewers"][position] = entry
        chosen["replaced"] = {key: entry["name"] if value == gone else value
                              for key, value in chosen["replaced"].items()}
        chosen["replaced"][gone] = entry["name"]
    chosen["reviewers"] += [_entry(values) for values in args.add or []]
    chosen["reviewers"] += [_entry(values, optional=True) for values in args.add_optional or []]
    if args.adjudicator:
        chosen["adjudicator"] = _entry(args.adjudicator, "adjudicator")
    return chosen


def check(chosen: dict[str, Any], allow_max: bool) -> tuple[list[str], list[str]]:
    """Errors that make the settings unusable, and warnings the user should hear."""
    errors, warnings = [], []
    adjudicator = chosen["adjudicator"]
    for entry in [*chosen["reviewers"], *([adjudicator] if adjudicator else [])]:
        if entry["cli"] not in CLIS:
            errors.append(f"{entry['model']}: the CLI must be one of {', '.join(CLIS)}")
        if entry["cli"] == "opencode" and "/" not in entry["model"]:
            errors.append(f"{entry['model']}: give the exact provider/model string `opencode run --model` takes")
        if not re.fullmatch(reviews.NAME, entry["name"]):
            errors.append(f"{entry['model']}: no reviewer name can be derived from this model string")
        if entry["effort"] == "max" and not allow_max:
            errors.append(f"{entry['model']}: max effort only when the user explicitly asks for it (--allow-max)")
    names = [entry["name"] for entry in chosen["reviewers"]]
    errors += [f"two reviewers would both be named {name}" for name in sorted({n for n in names if names.count(n) > 1})]
    if all(entry["optional"] for entry in chosen["reviewers"]):
        errors.append("at least one mandatory reviewer is required")
    if not adjudicator:
        errors.append("no adjudicator: the user must choose its CLI, exact model, and effort")
    elif any((entry["model"], entry["effort"]) == (adjudicator["model"], adjudicator["effort"])
             for entry in chosen["reviewers"]):
        warnings.append(f"the adjudicator {adjudicator['name']} has the same model and effort as a reviewer: it "
                        "will judge findings its own model raised")
    if len(chosen["reviewers"]) == 1:
        warnings.append("one reviewer only: no second opinion on the work")
    return errors, warnings


def describe(chosen: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """The settings as shown to the user; with a policy, each reviewer's preflight status too."""
    adjudicator = chosen["adjudicator"]
    result = {
        "source": chosen["source"],
        "reviewers": [{key: entry[key] for key in SHOWN} for entry in chosen["reviewers"]],
        "adjudicator": adjudicator and {key: adjudicator[key] for key in SHOWN[:4]},
        "replaced": chosen["replaced"],
    }
    if policy:
        timeout, minimums = policy["timeouts_seconds"]["preflight"], policy["min_cli_versions"]
        status = {}
        for entry in [*chosen["reviewers"], *([adjudicator] if adjudicator else [])]:
            key = "adjudicator" if entry["role"] == "adjudicator" else entry["name"]
            problem = providers.preflight(entry["cli"], entry["model"], timeout, minimums.get(entry["cli"]))
            status[key] = problem or "ready"
        result["preflight"] = status
    return result


def _stored(entry: dict[str, Any]) -> dict[str, Any]:
    return {"cli": entry["cli"], "model": entry["model"], "effort": entry["effort"],
            **({"optional": True} if entry["optional"] else {})}


def cmd_show(args: argparse.Namespace) -> None:
    root, policy = repo_root(args.root), load_policy()
    review = reviews.parse(find_epic(root, args.epic) / "review.md") if args.epic else None
    result = {}
    for workflow in WORKFLOWS:
        chosen = (reviews.settings(review, workflow, root, policy) if review
                  else project_settings(root, policy)[workflow])
        errors, warnings = check(chosen, allow_max=True)
        result[workflow] = {**describe(chosen, policy if args.preflight else None),
                            "problems": errors, "warnings": warnings}
    emit(result, 1 if any(entry["problems"] for entry in result.values()) else 0)


def cmd_set(args: argparse.Namespace) -> None:
    root, policy = repo_root(args.root), load_policy()
    path = reviewers_file(root)
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
    current, result = project_settings(root, policy), {}
    for workflow in WORKFLOWS if args.workflow == "both" else (args.workflow,):
        chosen = apply(current[workflow], args)
        errors, warnings = check(chosen, args.allow_max)
        if errors:
            raise ScopeError(f"{workflow}: {'; '.join(errors)}")
        data[workflow] = {"reviewers": [_stored(entry) for entry in chosen["reviewers"]],
                          "adjudicator": _stored(chosen["adjudicator"])}
        result[workflow] = {**describe({**chosen, "source": "project", "replaced": {}}, policy), "warnings": warnings}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Scope reviewers per workflow, written by /scope_reviewers (Codex: scope:reviewers).\n"
                    + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    emit({"file": str(path), **result})


def cmd_epic(args: argparse.Namespace) -> None:
    if not (args.add or args.add_optional or args.remove or args.replace or args.adjudicator):
        raise ScopeError("no change requested")
    root, policy = repo_root(args.root), load_policy()
    path = find_epic(root, args.epic) / "review.md"
    chosen = apply(reviews.settings(reviews.parse(path), args.workflow, root, policy), args)
    errors, warnings = check(chosen, args.allow_max)
    if errors:
        raise ScopeError("; ".join(errors))
    judge = chosen["adjudicator"]
    lines = [f"## {args.workflow} reviewers · {now()}", f"- requested: {' '.join(args.requested.split())}"]
    lines += [f"- reviewer {e['name']} · {e['cli']} · {e['model']} · {e['effort']} · "
              f"{'optional' if e['optional'] else 'mandatory'}" for e in chosen["reviewers"]]
    lines.append(f"- adjudicator {judge['name']} · {judge['cli']} · {judge['model']} · {judge['effort']}")
    lines += [f"- replaced {old} by {new}" for old, new in chosen["replaced"].items()]
    reviews.append(path, args.epic, lines)
    commit_paths(root, [path], f"review({args.epic}): {args.workflow} reviewers for this epic")
    emit({**describe({**chosen, "source": "epic"}, policy), "warnings": warnings})


def _blank() -> dict[str, Any]:
    return {"models": set(), "runs": 0, "completed": 0, "seconds": 0.0, "blocking": [0, 0], "major": [0, 0],
            "minor": [0, 0], "fixed": 0, "rejected": 0, "findings": 0}


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


def metrics(review_list: list[reviews.Review], workflow: str, adjudicator: dict | None = None) -> dict[str, Any]:
    """Per reviewer: model/effort, runs, time, findings by severity as `n (unique)`, fixed, rejected; per
    adjudicator: runs, findings judged, time. Unique: no other reviewer raised the same finding (duplicate marks)."""
    people: dict[str, dict[str, Any]] = {}
    judges: dict[str, dict[str, Any]] = {}
    if adjudicator:
        model = f"{adjudicator['model']}/{adjudicator['effort']}"
        judges.setdefault(adjudicator["name"], _blank())["models"].add(model)
    for review in review_list:
        for entry in (entry for entry in review.rounds if entry["workflow"] == workflow):
            for row in entry["reviewers"]:
                stats = (judges if row["role"] == "adjudicator" else people).setdefault(row["name"], _blank())
                stats["models"].add(row["model"])
                stats["runs"] += 1
                stats["completed"] += row["status"] == "completed"
                stats["seconds"] += row["seconds"] or 0
        finding_states = reviews.states(review, workflow)
        for key, finding in review.findings.items():
            if not key.startswith(reviews.PREFIX[workflow]):
                continue
            stats = people.setdefault(finding.raised_by, _blank())
            root = review.findings.get(finding.duplicate_of or "", finding)
            group = [root, *(other for other in review.findings.values() if other.duplicate_of == root.id)]
            stats[finding.severity][0] += 1
            stats[finding.severity][1] += {other.raised_by for other in group} == {finding.raised_by}
            state = finding_states.get(root.id)
            stats["fixed"] += root.disposition == "fixed" and state in ("closed", "closed_unverified")
            stats["rejected"] += state in ("closed_rejected", "accepted_tradeoff")
            for by, outcome, _, _ in finding.outcomes:
                if outcome in reviews.ADJUDICATION_OUTCOMES and by != "user":
                    judges.setdefault(by, _blank())["findings"] += 1
    def runs(s: dict[str, Any]) -> str:
        failed = s["runs"] - s["completed"]
        return f"{s['runs']} ({failed} failed)" if failed else str(s["runs"])

    rows = [{"name": name, "models": sorted(s["models"]), "runs": runs(s), "completed": s["completed"],
             "time": _clock(s["seconds"]),
             **{level: f"{s[level][0]} ({s[level][1]})" for level in reviews.SEVERITIES},
             "fixed": s["fixed"], "rejected": s["rejected"]} for name, s in sorted(people.items())]
    judged = [{"name": name, "models": sorted(s["models"]), "runs": runs(s), "completed": s["completed"],
               "findings": s["findings"], "time": _clock(s["seconds"])} for name, s in sorted(judges.items())]
    table = ["| Reviewer | Model/effort | Runs | Time | Blocking | Major | Minor | Fixed | Rejected |",
             "|---|---|---|---|---|---|---|---|---|"]
    table += [f"| {r['name']} | {', '.join(r['models'])} | {r['runs']} | {r['time']} | {r['blocking']} | "
              f"{r['major']} | {r['minor']} | {r['fixed']} | {r['rejected']} |" for r in rows]
    table += ["", "| Adjudicator | Model/effort | Runs | Findings judged | Time |", "|---|---|---|---|---|"]
    table += [f"| {j['name']} | {', '.join(j['models'])} | {j['runs']} | {j['findings']} | {j['time']} |"
              for j in judged] or ["| none | - | 0 | 0 | 0m 00s |"]
    return {"workflow": workflow, "reviewers": rows, "adjudicators": judged, "table": "\n".join(table)}


def cmd_metrics(args: argparse.Namespace) -> None:
    root = repo_root(args.root)
    if args.all:
        base = root / "docs" / "epics"
        paths = [*sorted(base.glob("*/review.md")), *sorted((base / "_implemented").glob("*/review.md"))]
        parsed = [reviews.parse(path) for path in paths]
        emit({"epics": len(paths), **{workflow: metrics(parsed, workflow) for workflow in WORKFLOWS}})
    if not (args.epic and args.workflow):
        raise ScopeError("give --epic and --workflow, or --all")
    review = reviews.parse(find_epic(root, args.epic) / "review.md")
    emit(metrics([review], args.workflow, reviews.settings(review, args.workflow, root, load_policy())["adjudicator"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    show, change, epic, report = (commands.add_parser(name) for name in ("show", "set", "epic", "metrics"))
    show.add_argument("--epic")
    show.add_argument("--preflight", action="store_true")
    change.add_argument("--workflow", choices=("refine", "audit", "both"), default="both")
    epic.add_argument("--epic", required=True)
    epic.add_argument("--workflow", choices=WORKFLOWS, required=True)
    epic.add_argument("--requested", required=True, help="the user's words")
    for sub in (change, epic):
        sub.add_argument("--add", nargs=3, action="append", metavar=("CLI", "MODEL", "EFFORT"))
        sub.add_argument("--add-optional", nargs=3, action="append", metavar=("CLI", "MODEL", "EFFORT"))
        sub.add_argument("--remove", action="append", metavar="NAME")
        sub.add_argument("--replace", nargs=4, action="append", metavar=("NAME", "CLI", "MODEL", "EFFORT"))
        sub.add_argument("--adjudicator", nargs=3, metavar=("CLI", "MODEL", "EFFORT"))
        sub.add_argument("--allow-max", action="store_true", help="the user explicitly asked for max effort")
    report.add_argument("--epic")
    report.add_argument("--workflow", choices=WORKFLOWS)
    report.add_argument("--all", action="store_true", help="every epic folder, active and archived")
    for sub in (show, change, epic, report):
        sub.add_argument("--root")
    args = parser.parse_args()
    run_cli({"show": cmd_show, "set": cmd_set, "epic": cmd_epic, "metrics": cmd_metrics}[args.command], args)


if __name__ == "__main__":
    main()

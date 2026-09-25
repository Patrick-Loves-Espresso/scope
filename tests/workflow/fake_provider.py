"""A scripted stand-in for the claude, codex, opencode, and agy CLIs.

It answers the preflight probes, then plays the planner, implementer, or
reviewer named at the top of the prompt, writing real files and commits in the
sample project. Environment knobs:

- FAKE_STATE: directory for the call log (required)
- FAKE_UNAVAILABLE: comma list of providers whose --version fails
- FAKE_VERSION: version every fake prints (default 9.9.9)
- FAKE_FAIL: comma list of providers that exit 2 on a job
- FAKE_FAIL_ONCE: comma list of providers that exit 2 on their first job only
- FAKE_HANG: comma list of providers that sleep on a job
- FAKE_INVALID: comma list of providers that return malformed reviews
- FAKE_REFINE_FINDER / FAKE_AUDIT_FINDER: provider raising the one major finding
- FAKE_PYTHON: interpreter the sample plan uses for pytest
- FAKE_OVERSIZE: implementer writes far more code than planned
- FAKE_DISPOSITION: disposition line authors write (default: fixed)
- FAKE_VERIFY_OUTCOME / FAKE_ADJUDICATION: override reviewer outcomes
- FAKE_MESSAGE: return this final message instead of doing any work
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from sample import CRITERIA, PLAN

PROVIDER, ARGS = sys.argv[1], sys.argv[2:]


def listed(name: str) -> bool:
    return PROVIDER in os.environ.get(name, "").split(",")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def field(prompt: str, pattern: str) -> str:
    match = re.search(pattern, prompt)
    return match[1] if match else ""


def task_of(prompt: str) -> str:
    return field(prompt, r"(?s)## Task\n\n(.*?)\n\n## Governance")


def planner(prompt: str) -> str:
    folder = Path(field(prompt, r"folder `([^`]+)`"))
    task = task_of(prompt)
    if "Draft acceptance-criteria.md" in task:
        (folder / "acceptance-criteria.md").write_text(CRITERIA)
    elif "Write plan.md" in task:
        (folder / "plan.md").write_text(PLAN.replace("PYTHON", os.environ["FAKE_PYTHON"]))
    elif "Resolve the open findings" in task:
        review = folder / "review.md"
        review.write_text(
            re.sub(
                r"^- disposition: open.*$",
                "- disposition: " + disposition("clarified the approach"),
                review.read_text(),
                flags=re.MULTILINE,
            )
        )
        plan = folder / "plan.md"
        plan.write_text(plan.read_text().replace("## Decision log\n", "## Decision log\n\n- Clarified the approach.\n"))
    return "Done.\nSTATUS: done"


def disposition(what: str) -> str:
    return os.environ.get("FAKE_DISPOSITION") or f"fixed — {what}"


def set_story_done(plan: Path, story: str) -> None:
    text = plan.read_text()
    text = re.sub(rf"(\{{id: {story},.*?)status: todo", r"\1status: done", text)
    plan.write_text(text.replace("## Progress log\n", f"## Progress log\n\n- {story} done\n"))


def runner(prompt: str, label: str, milestone: str = "") -> dict:
    command = field(prompt, rf"- {label}: `([^`]+)`").replace("<milestone id>", milestone)
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def implementer(prompt: str) -> str:
    folder = Path(field(prompt, r"folder `([^`]+)`"))
    task = task_of(prompt)
    if "Resolve the open audit findings" in task:
        review = folder / "review.md"
        review.write_text(
            re.sub(
                r"^- disposition: open.*$",
                "- disposition: " + disposition("added the missing test"),
                review.read_text(),
                flags=re.MULTILINE,
            )
        )
        Path("tests/test_greet.py").write_text(
            Path("tests/test_greet.py").read_text()
            + "\n\ndef test_greets_twice():\n    assert greet('A') == greet('A')\n"
        )
        git("add", "-A")
        git("commit", "-q", "-m", "fix(DEMO-001): address audit findings")
        return "Fixed.\nSTATUS: done"
    Path("src").mkdir(exist_ok=True)
    Path("tests").mkdir(exist_ok=True)
    extra = (
        "".join(f"\n\ndef helper_{i}(value):\n    return value + {i}\n" for i in range(40))
        if os.environ.get("FAKE_OVERSIZE")
        else ""
    )
    Path("src/greet.py").write_text('"""Greetings."""\n\n\ndef greet(name):\n    return f"Hello, {name}!"\n' + extra)
    Path("tests/test_greet.py").write_text(
        "from greet import greet\n\n\ndef test_greets_by_name():\n    assert greet('Ada') == 'Hello, Ada!'\n"
    )
    set_story_done(folder / "plan.md", "S1")
    git("add", "-A")
    git("commit", "-q", "-m", "feat(DEMO-001): S1 greet by name")
    if runner(prompt, "Size-check command")["over"]:
        return "Size grew beyond the plan.\nSTATUS: needs_check"
    assert runner(prompt, "Milestone verification command", "M1")["outcome"] == "passed"
    Path("src/greet.py").write_text(
        Path("src/greet.py")
        .read_text()
        .replace('    return f"Hello', '    if not name:\n        raise ValueError("empty name")\n    return f"Hello')
    )
    Path("tests/test_greet.py").write_text(
        Path("tests/test_greet.py").read_text()
        + (
            "\n\ndef test_rejects_empty_name():\n    import pytest\n\n    with pytest.raises(ValueError):\n"
            "        greet('')\n"
        )
    )
    set_story_done(folder / "plan.md", "S2")
    git("add", "-A")
    git("commit", "-q", "-m", "feat(DEMO-001): S2 reject empty names")
    assert runner(prompt, "Milestone verification command", "M2")["outcome"] == "passed"
    Path("docs/architecture").mkdir(parents=True, exist_ok=True)
    Path("docs/architecture/05-building-blocks.md").write_text("# Building blocks\n\n- `greet`: greets by name.\n")
    plan = folder / "plan.md"
    plan.write_text(
        plan.read_text().replace(
            "Planned: one module, no persisted state.", "Planned: one module, no persisted state. Actual: the same."
        )
    )
    git("add", "-A")
    git("commit", "-q", "-m", "docs(DEMO-001): document greet")
    Path("docs/epics/_implemented").mkdir(exist_ok=True)
    git("mv", str(folder), f"docs/epics/_implemented/{folder.name}")
    git("commit", "-q", "-m", "docs(DEMO-001): finalize documentation and archive epic")
    return "All stories done.\nSTATUS: done"


def reviewer(prompt: str) -> str:
    if listed("FAKE_INVALID"):
        return "I looked around and it seems fine."
    mission = field(prompt, r"- Mission: (\w+)")
    ids = re.findall(r"^### ([RA]\d+\.[a-z]+\.\d+) · ", prompt, re.MULTILINE)
    if mission == "full":
        workflow = "audit" if "## Focus: audit" in prompt else "refine"
        if PROVIDER == os.environ.get(f"FAKE_{workflow.upper()}_FINDER", ""):
            return (
                "DECISION: changes_required\n\n## Findings\n\n### F1\n- severity: major\n- category: tests\n"
                "- evidence: plan.md does not say how names are trimmed\n- correction: state it\n"
                "- closure: the plan says it\n\n## Suggestions\n- Consider a docstring.\n"
            )
        return "DECISION: approve\n\n## Findings\n\nNone\n\n## Suggestions\n- None\n"
    if mission == "verify":
        blocks = prompt.split("### ")
        lines = []
        for finding in ids:
            block = next(b for b in blocks if b.startswith(finding))
            outcome = "verified" if "disposition: fixed" in block else "rejection_accepted"
            outcome = os.environ.get("FAKE_VERIFY_OUTCOME") or outcome
            lines.append(f"- {finding}: {outcome} — checked the repository")
        return "DECISION: done\n\n## Outcomes\n" + "\n".join(lines) + "\n"
    if mission == "adjudicate":
        outcome = os.environ.get("FAKE_ADJUDICATION") or "rejection_upheld"
        return "DECISION: done\n\n## Outcomes\n" + "".join(f"- {i}: {outcome} — examined from scratch\n" for i in ids)
    if mission == "check":
        return "DECISION: implementation_growth\n\n## Rationale\nThe extra code serves AC-001.\n"
    return "DECISION: diagnosed\n\n## Diagnosis\nThe fix touched the wrong module.\n"


def deliver(message: str) -> None:
    if PROVIDER == "codex":
        Path(ARGS[ARGS.index("--output-last-message") + 1]).write_text(message)
        if "--json" in ARGS:
            print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}))
    elif PROVIDER == "claude" and ARGS[ARGS.index("--output-format") + 1] == "json":
        print(
            json.dumps({"result": message, "is_error": False, "total_cost_usd": 0.01, "usage": {"input_tokens": 100}})
        )
    else:
        print(message)


def main() -> None:
    probe = ARGS[:2]
    if ARGS == ["--version"]:
        if listed("FAKE_UNAVAILABLE"):
            sys.exit(1)
        print(f"{PROVIDER} {os.environ.get('FAKE_VERSION', '9.9.9')}")
        return
    if ARGS == ["--help"]:
        print("--print --safe-mode --no-session-persistence --permission-mode")
        return
    if probe == ["auth", "status"]:
        print(json.dumps({"loggedIn": True}))
        return
    if probe == ["exec", "--help"]:
        print("--output-last-message --ignore-user-config --sandbox")
        return
    if ARGS[:1] == ["models"]:
        print("meta/muse-spark-1.3-contributor\ngemini-3.1-pro-high")
        return
    prompt = sys.stdin.read() if PROVIDER in ("claude", "codex") else ARGS[-1]
    with Path(os.environ["FAKE_STATE"], "calls.jsonl").open("a") as log:
        log.write(
            json.dumps(
                {
                    "provider": PROVIDER,
                    "args": ARGS[:-1] if PROVIDER in ("opencode", "agy") else ARGS,
                    "cwd": os.getcwd(),
                    "first_line": prompt.splitlines()[0],
                }
            )
            + "\n"
        )
    if listed("FAKE_HANG"):
        time.sleep(60)
    once = Path(os.environ["FAKE_STATE"], f"failed-once-{PROVIDER}")
    if listed("FAKE_FAIL_ONCE") and not once.exists():
        once.touch()
        print("transient provider error", file=sys.stderr)
        sys.exit(2)
    if listed("FAKE_FAIL"):
        print("provider crashed", file=sys.stderr)
        sys.exit(2)
    if os.environ.get("FAKE_MESSAGE"):
        deliver(os.environ["FAKE_MESSAGE"])
        return
    heading = prompt.splitlines()[0]
    role = planner if "Planner" in heading else implementer if "Implementer" in heading else reviewer
    deliver(role(prompt))


if __name__ == "__main__":
    main()

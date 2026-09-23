"""Shared fixtures: a sample project, fake provider CLIs, and a script runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "src_shared" / "scripts"
FAKE = REPO / "tests" / "workflow" / "fake_provider.py"
EPIC = "DEMO-001"
EPIC_DIR = "docs/epics/DEMO-001-greeting"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "tests" / "workflow"))

from sample import CRITERIA, PLAN  # noqa: E402

DETAILS = """---
epic_id: DEMO-001
title: Greeting
status: draft
---

# DEMO-001: Greeting

Callers need a function that greets a person by name.
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake claude/codex/opencode/agy on PATH; returns the state directory with calls.jsonl."""
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    for name in ("claude", "codex", "opencode", "agy"):
        wrapper = bin_dir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {name} "$@"\n')
        wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(state))
    monkeypatch.setenv("FAKE_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_REFINE_FINDER", "claude")
    monkeypatch.setenv("FAKE_AUDIT_FINDER", "codex")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return state


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository with one epic ready for refinement, isolated from the user's git config."""
    for key, value in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "project"
    epic = root / "docs" / "epics" / "DEMO-001-greeting"
    epic.mkdir(parents=True)
    (epic / "details.md").write_text(DETAILS)
    (root / ".gitignore").write_text("tmp_debug/\nwip/\n__pycache__/\n.claude/\nplugins/\n")
    (root / "pytest.ini").write_text("[pytest]\npythonpath = src\n")
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: sample project")
    return root.resolve()


@pytest.fixture
def planned(project: Path, fake: Path) -> Path:
    """The sample epic with approved criteria and an uncommitted plan, ready for review."""
    epic = project / EPIC_DIR
    (epic / "acceptance-criteria.md").write_text(CRITERIA)
    scope("scope_check.py", "approve", "--epic", EPIC, "--source", "Test user", cwd=project)
    (epic / "plan.md").write_text(PLAN.replace("PYTHON", sys.executable))
    return project


def scope(script: str, *args: str, cwd: Path, expect: int | None = 0) -> dict:
    """Run a Scope script from the source tree and return its JSON output."""
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=cwd, capture_output=True, text=True)
    if expect is not None:
        assert result.returncode == expect, (
            f"{script} {args} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
    return json.loads(result.stdout)


def calls(state: Path) -> list[dict]:
    path = state / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

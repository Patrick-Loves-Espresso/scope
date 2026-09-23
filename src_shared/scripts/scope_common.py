"""Shared helpers for Scope's lifecycle scripts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, NoReturn

import yaml

SCOPE_ROOT = Path(__file__).resolve().parent.parent
SCOPE_BLOCK = re.compile(r"^```yaml scope[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
CRITERION = re.compile(r"^### (AC-\d{3,})\b", re.MULTILINE)


class ScopeError(Exception):
    """A condition the caller must fix; reported as JSON with exit code 1."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_policy() -> dict[str, Any]:
    return yaml.safe_load((SCOPE_ROOT / "config" / "scope-policy.yaml").read_text(encoding="utf-8"))


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ScopeError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def repo_root(value: str | None) -> Path:
    start = Path(value) if value else Path.cwd()
    return Path(git(start, "rev-parse", "--show-toplevel")).resolve()


def main_root(root: Path) -> Path:
    """The main checkout, also when `root` is a linked worktree."""
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).resolve().parent


def base_commit(root: Path) -> str:
    """Where the epic branch forked from the main checkout's branch (HEAD in the main checkout)."""
    main_head = git(main_root(root), "rev-parse", "HEAD")
    return git(root, "merge-base", "HEAD", main_head)


def find_epic(root: Path, epic_id: str) -> Path:
    pattern = re.compile(rf"^{re.escape(epic_id)}(?![A-Za-z0-9])", re.IGNORECASE)
    for base in (root / "docs" / "epics", root / "docs" / "epics" / "_implemented"):
        matches = [
            path for path in sorted(base.glob("*"))
            if path.is_dir() and not path.name.startswith("_") and pattern.match(path.name)
        ]
        if len(matches) > 1:
            raise ScopeError(f"epic {epic_id} is ambiguous: {', '.join(p.name for p in matches)}")
        if matches:
            return matches[0]
    raise ScopeError(f"no epic folder for {epic_id} under {root / 'docs' / 'epics'}")


def scope_blocks(path: Path, text: str | None = None) -> dict[str, Any]:
    """Merge the ```yaml scope``` blocks of a Markdown file (or of `text`) into one mapping."""
    merged: dict[str, Any] = {}
    for block in SCOPE_BLOCK.findall(path.read_text(encoding="utf-8") if text is None else text):
        try:
            data = yaml.safe_load(block) or {}
        except yaml.YAMLError as exc:
            raise ScopeError(f"{path.name}: invalid yaml scope block: {exc}") from exc
        if not isinstance(data, dict):
            raise ScopeError(f"{path.name}: a yaml scope block must be a mapping")
        merged.update(data)
    return merged


def criteria_ids(epic: Path) -> list[str]:
    path = epic / "acceptance-criteria.md"
    return CRITERION.findall(path.read_text(encoding="utf-8")) if path.is_file() else []


def section(text: str, heading: str) -> str | None:
    """Body of the `## heading` section, or None when the heading is absent."""
    match = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def run_dir(root: Path, epic_id: str, name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = main_root(root) / "tmp_debug" / "scope-runs" / epic_id / f"{stamp}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def commit_paths(root: Path, paths: list[Path], message: str) -> str | None:
    """Commit only `paths`; returns the new commit, or None when nothing changed."""
    relative = [str(path.relative_to(root)) for path in paths]
    git(root, "add", "--", *relative)
    if not git(root, "diff", "--cached", "--name-only", "--", *relative):
        return None
    git(root, "commit", "--quiet", "-m", message, "--", *relative)
    return git(root, "rev-parse", "HEAD")


def emit(result: dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(result, indent=2, default=str))
    sys.exit(code)


def run_cli(handler: Any, args: Any) -> None:
    """Run a command handler (which emits its own result); report ScopeError as JSON."""
    try:
        handler(args)
    except ScopeError as exc:
        emit({"error": str(exc)}, 1)

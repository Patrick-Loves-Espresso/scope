"""Provider CLI invocation for Scope workers and reviewers.

The flags are harvested from the proven Scope 2 runners: Claude runs in safe
mode without MCP or session persistence, reviewers are read-only (Claude tool
denials, Codex read-only sandbox, OpenCode plan agent, Antigravity sandbox).
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from typing import Any

import psutil

READ_TOOLS = ["Read", "Glob", "Grep"]
WORKER_TOOLS = "Read,Glob,Grep,Write,Edit,Bash"
WORKER_DENIED = "Bash(git push *),Bash(git merge *),Bash(git rebase *),Bash(git reset --hard *),Task,Agent,NotebookEdit"
REVIEWER_DENIED = "Write,Edit,NotebookEdit,Task,Agent"
CLAUDE_FLAGS = ("--print", "--safe-mode", "--no-session-persistence", "--permission-mode")
CODEX_FLAGS = ("--output-last-message", "--ignore-user-config", "--sandbox")
STDIN_PROVIDERS = {"claude", "codex"}


def _capture(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _version(text: str) -> tuple[int, ...]:
    found = re.search(r"\d+(?:\.\d+)+", text)
    return tuple(int(part) for part in found[0].split(".")) if found else ()


def preflight(provider: str, model: str, timeout: float, minimum: str | None = None) -> str | None:
    """Return why `provider` cannot run `model`, or None when it can."""
    executable = shutil.which(provider)
    if executable is None:
        return f"{provider} CLI not found on PATH"
    try:
        version = _capture([executable, "--version"], timeout)
        if version.returncode != 0:
            return f"{provider} --version failed"
        if minimum and _version(version.stdout) < _version(minimum):
            found = version.stdout.strip() or "(no version printed)"
            return f"{provider} CLI {found} is older than {minimum}; update it"
        if provider == "claude":
            help_text = _capture([executable, "--help"], timeout).stdout
            missing = [flag for flag in CLAUDE_FLAGS if flag not in help_text]
            if missing:
                return f"claude CLI lacks {', '.join(missing)}"
            auth = _capture([executable, "auth", "status", "--json"], timeout)
            try:
                logged_in = json.loads(auth.stdout).get("loggedIn") is True
            except (json.JSONDecodeError, AttributeError):
                logged_in = False
            if not logged_in:
                return "claude CLI is not authenticated"
        elif provider == "codex":
            help_text = _capture([executable, "exec", "--help"], timeout).stdout
            missing = [flag for flag in CODEX_FLAGS if flag not in help_text]
            if missing:
                return f"codex CLI lacks {', '.join(missing)}"
        else:
            catalog = _capture([executable, "models"], timeout)
            listed = re.search(rf"(?<![\w./-]){re.escape(model)}(?![\w./-])", catalog.stdout + catalog.stderr)
            if listed is None:
                return f"{provider} does not list model {model}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{provider} preflight failed: {exc}"
    return None


def command(
    provider: str,
    *,
    model: str,
    effort: str,
    root: Path,
    write: bool,
    output_path: Path,
    prompt: str,
    timeout: float,
    read_only_commands: list[str] = (),
    add_dirs: list[Path] = (),
) -> list[str]:
    """Build the provider command line; stdin providers receive the prompt on stdin."""
    if provider == "claude":
        if write:
            tools, allowed, denied = WORKER_TOOLS, WORKER_TOOLS, WORKER_DENIED
        else:
            tools = ",".join([*READ_TOOLS, "Bash"])
            allowed = ",".join([*READ_TOOLS, *(f"Bash({entry}:*)" for entry in read_only_commands)])
            denied = REVIEWER_DENIED
        result = [
            "claude", "--print", "--safe-mode", "--strict-mcp-config", "--no-chrome",
            "--no-session-persistence", "--model", model, "--effort", effort,
            "--permission-mode", "dontAsk", "--tools", tools, "--allowedTools", allowed,
            "--disallowedTools", denied, "--output-format", "json" if write else "text",
        ]
        for directory in add_dirs:
            result += ["--add-dir", str(directory)]
        return result
    if provider == "codex":
        result = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--cd", str(root)]
        for directory in add_dirs:
            result += ["--add-dir", str(directory)]
        result += [
            "--model", model, "-c", f'model_reasoning_effort="{effort}"',
            "--sandbox", "workspace-write" if write else "read-only",
            "--output-last-message", str(output_path),
        ]
        return result + (["--json", "-"] if write else ["-"])
    if write:
        raise ValueError(f"{provider} is a reviewer-only provider")
    if provider == "opencode":
        return ["opencode", "run", "--pure", "--agent", "plan", "--model", model,
                "--variant", effort, "--dir", str(root), prompt]
    if provider == "agy":
        return ["agy", "--model", model, "--sandbox", "--print-timeout",
                f"{max(1, int(timeout // 60))}m", "--print", prompt]
    raise ValueError(f"unknown provider: {provider}")


def _terminate(process: subprocess.Popen[bytes], grace: float) -> None:
    """Stop the provider and every descendant (process group plus psutil tree)."""
    gone = (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError)
    targets: list[psutil.Process] = []
    with contextlib.suppress(*gone):
        parent = psutil.Process(process.pid)
        targets = [*parent.children(recursive=True), parent]
    if os.name != "nt":
        with contextlib.suppress(*gone):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    for target in targets:
        with contextlib.suppress(*gone):
            target.terminate()
    _, alive = psutil.wait_procs(targets, timeout=grace)
    for target in alive:
        with contextlib.suppress(*gone):
            target.kill()
    process.kill()
    process.wait()


def run(
    argv: list[str],
    *,
    provider: str,
    prompt: str,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run one provider process to completion or timeout."""
    executable = shutil.which(argv[0]) or argv[0]
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                [executable, *argv[1:]], cwd=cwd, stdout=stdout, stderr=stderr,
                stdin=subprocess.PIPE if provider in STDIN_PROVIDERS else subprocess.DEVNULL,
                env={**os.environ, "NO_COLOR": "1"}, **group,
            )
        except OSError as exc:
            return {"exit_code": None, "timed_out": False, "duration_seconds": 0.0, "error": str(exc)}
        try:
            process.communicate(prompt.encode() if provider in STDIN_PROVIDERS else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate(process, grace=5)
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 1),
    }


def final_message(provider: str, stdout_path: Path, output_path: Path) -> tuple[str, Any]:
    """The provider's final message and any usage it reported."""
    if provider == "codex":
        message = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
        usage = None
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and "usage" in event:
                usage = event["usage"]
        return message, usage
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return text, None
    if not isinstance(envelope, dict):
        return text, None
    usage = {"usage": envelope.get("usage"), "cost_usd": envelope.get("total_cost_usd"),
             "is_error": envelope.get("is_error") is True}
    return str(envelope.get("result") or ""), usage

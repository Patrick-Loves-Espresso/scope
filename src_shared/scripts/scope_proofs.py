"""Execute approved project proofs and bind raw results to their execution context.

This module owns execution and counts. It does not prepare fixtures or infer a
test command. Projects may emit the scope-json count object for native tools.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any, Mapping, Sequence
import uuid

import yaml
import psutil

import scope_fingerprint as fingerprint


def executable(proofs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return proof rows that have an approved execution contract.

    External-blocked rows are durable limitations, not commands. Callers retain
    their IDs in implementation evidence while executing only this subset.
    """
    return [proof for proof in proofs if proof.get("classification") != "external_blocked"]


def policy(scope_root: Path | None = None) -> dict[str, Any]:
    path = (scope_root or Path(__file__).resolve().parent.parent) / "config/execution-policy.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid execution policy")
    for field in ("maximum_stories_per_group", "maximum_pre_audit_debugging_jobs", "proof_timeout_seconds", "proof_poll_interval_seconds"):
        if type(value.get(field)) is not int or value[field] <= 0:
            raise ValueError(f"execution policy {field} must be positive")
    return value


def execution(proof: Mapping[str, Any], root: Path, config: Mapping[str, Any], *, require_cwd: bool = True) -> dict[str, Any]:
    value = proof.get("execution")
    if not isinstance(value, dict) or set(value) != {"argv", "cwd", "parser", "environment", "fresh"}:
        raise ValueError(f"proof {proof.get('id')} requires execution argv/cwd/parser/environment/fresh")
    argv = value["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v or "\0" in v for v in argv):
        raise ValueError("proof argv must be a nonempty string array")
    if Path(argv[0]).name.lower() in config["shell_executables"]:
        raise ValueError("proof argv must invoke the project command directly, not a shell")
    command_index = 1
    if Path(argv[0]).name == "env":
        while command_index < len(argv) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[command_index]
        ):
            command_index += 1
        if command_index >= len(argv):
            raise ValueError("proof env invocation is missing its executable")
        if Path(argv[command_index]).is_absolute():
            raise ValueError("proof executable after env must be portable, not an absolute path")
    runtime_root = (root / "tmp_debug" / "scope-runs").resolve()
    for argument in argv[1:]:
        candidate = argument.split("=", 1)[-1]
        path = Path(candidate)
        if path.is_absolute() and path.resolve(strict=False).is_relative_to(runtime_root):
            raise ValueError("proof command must not write into a prior Scope run")
    if proof.get("command") != shlex.join(argv):
        raise ValueError("proof command must equal the display form of execution.argv (shlex.join)")
    cwd = value["cwd"]
    if cwd != ".":
        fingerprint.relative_path(cwd, "proof cwd")
    target = (root / cwd).resolve()
    if not target.is_relative_to(root.resolve()) or (require_cwd and not target.is_dir()):
        raise ValueError("proof cwd is missing or escapes the repository")
    if value["parser"] not in config["parsers"]:
        raise ValueError("unknown proof result parser")
    if value["parser"] == "exit-code" and proof.get("level") not in config["exit_code_levels"]:
        raise ValueError("test proofs require counts; exit-code is only for non-test checks")
    names = value["environment"]
    if not isinstance(names, list) or len(names) != len(set(names)) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names
    ):
        raise ValueError("proof environment must list unique approved variable names")
    if type(value["fresh"]) is not bool:
        raise ValueError("proof fresh must be explicit boolean; external/stateful proofs require true")
    if proof.get("level") in {"live_smoke", "operational"} and not value["fresh"]:
        raise ValueError("live and operational proofs require fresh: true")
    return value


def counts(output: str, parser: str, exit_code: int) -> dict[str, int]:
    fields = ("passed", "failed", "errors", "skipped")
    if parser == "exit-code":
        return dict(zip(fields, (int(exit_code == 0), int(exit_code != 0), 0, 0)))
    if parser == "scope-json":
        # Exactly one structured record. Other output belongs before this line.
        records = [line[len("SCOPE_RESULT "):] for line in output.splitlines() if line.startswith("SCOPE_RESULT ")]
        if len(records) != 1:
            raise ValueError("expected exactly one SCOPE_RESULT JSON count record")
        value = json.loads(records[0])
        if not isinstance(value, dict) or set(value) != set(fields) or any(type(value[f]) is not int or value[f] < 0 for f in fields):
            raise ValueError("invalid SCOPE_RESULT counts")
        return value
    if parser != "pytest":
        raise ValueError("unknown proof parser")
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    summaries = [line.strip("= \t") for line in output.splitlines() if re.fullmatch(
        r"[= \t]*(?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, )?)+ in .+?[= \t]*", line
    )]
    if len(summaries) != 1:
        raise ValueError("pytest output has missing or ambiguous summary counts")
    values = {name: 0 for name in fields}
    seen: set[str] = set()
    for number, word in re.findall(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)", summaries[0]):
        if word in seen:
            raise ValueError("duplicate pytest summary count")
        seen.add(word)
        field = {"error": "errors", "xpassed": "failed", "xfailed": "skipped", "deselected": "skipped"}.get(word, word)
        if field in values:
            values[field] += int(number)
    return values


def context(proof: Mapping[str, Any], root: Path, epic_dir: Path, config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    value = execution(proof, root, config)
    runtime_names = sorted(set(config["environment"]) | set(value["environment"]))
    env = {name: os.environ[name] for name in runtime_names if name in os.environ}
    execution_root = root / value["cwd"]
    search_path = os.pathsep.join(str((execution_root / entry).resolve()) if not Path(entry).is_absolute() else entry
                                  for entry in env.get("PATH", "").split(os.pathsep))
    program = value["argv"][0]
    executable = str(execution_root / program) if os.path.dirname(program) else shutil.which(program, path=search_path)
    executable_path = Path(executable).resolve() if executable else None
    identity = {
        "executable_sha256": fingerprint.file_sha256(executable_path) if executable_path and executable_path.is_file() else None,
        "argv": value["argv"], "cwd": value["cwd"], "parser": value["parser"],
        # Scope's portable runtime variables let the same proof run under Claude,
        # Codex, and CI. Only manifest-approved inputs belong to proof identity;
        # the resolved executable is bound separately above.
        "environment_sha256": fingerprint.structured_sha256(
            {name: env.get(name) for name in sorted(value["environment"])}
        ),
        "source_sha256": fingerprint.audit_fingerprint(epic_dir, root)["workspace_sha256"],
    }
    return identity, env


def valid_record(row: Mapping[str, Any], root: Path, identity: Mapping[str, Any]) -> bool:
    try:
        return (
            row.get("context") == identity and row.get("outcome") == "pass"
            and row.get("exit_code") == 0 and row.get("passed", 0) > 0
            and all(row.get(field) == 0 for field in ("failed", "errors", "skipped"))
            and bool(row.get("evidence_hashes"))
            and all(fingerprint.file_sha256(root / fingerprint.relative_path(path)) == expected
                    for path, expected in row["evidence_hashes"].items())
        )
    except (OSError, ValueError, TypeError):
        return False


def execute(proofs: Sequence[Mapping[str, Any]], root: Path, epic_dir: Path,
            config: Mapping[str, Any], *, reuse: Sequence[Mapping[str, Any]] = (), cancellation: Path | None = None) -> dict[str, Any]:
    """Run a checkpoint, deduplicating only identical local contexts in this call.

    Reuse from another checkpoint must be passed explicitly by its caller. Fresh
    proofs always run. Failure is returned with raw output, never converted to a
    success or discarded because a command wrapper exited zero.
    """
    root, epic_dir = root.resolve(), epic_dir.resolve()
    output_dir = epic_dir / "reviews/proofs" / uuid.uuid4().hex
    if not output_dir.resolve().is_relative_to(root) or any(p.is_symlink() for p in [output_dir.parent, output_dir.parent.parent]):
        raise ValueError("proof output directory escapes repository or is symlinked")
    output_dir.mkdir(parents=True)
    started = time.time()
    rows: list[dict[str, Any]] = []
    available = list(reuse)
    for index, proof in enumerate(proofs):
        if cancellation is not None and cancellation.exists():
            raise ValueError("proof execution cancelled")
        identity, env = context(proof, root, epic_dir, config)
        cached = next((row for row in available if valid_record(row, root, identity)), None)
        if cached is not None and not proof["execution"]["fresh"]:
            rows.append({**cached, "proof_id": proof["id"], "command": proof["command"], "reused": True})
            continue
        log = output_dir / f"{index:03d}.log"
        begin = time.time()
        error = None
        with log.open("wb") as stream:
            try:
                process = subprocess.Popen(identity["argv"], cwd=root / identity["cwd"], env=env,
                                           stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=os.name != "nt")
                try:
                    deadline = time.monotonic() + config["proof_timeout_seconds"]
                    while True:
                        if cancellation is not None and cancellation.exists():
                            raise ValueError("proof execution cancelled")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(identity["argv"], config["proof_timeout_seconds"])
                        try:
                            code = process.wait(timeout=min(remaining, config["proof_poll_interval_seconds"]))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException as exc:
                    # Include detached descendants as well as the direct process group.
                    try:
                        children = psutil.Process(process.pid).children(recursive=True)
                    except (psutil.NoSuchProcess, PermissionError):
                        children = []
                    for child in reversed(children):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    try:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
                    if not isinstance(exc, subprocess.TimeoutExpired):
                        raise
                    code, error = -1, "proof timed out"
            except OSError as exc:
                code, error = -1, str(exc)
        try:
            parsed = counts(log.read_text(encoding="utf-8", errors="replace"), identity["parser"], code)
        except (ValueError, TypeError) as exc:
            parsed, error = {"passed": 0, "failed": 0, "errors": 1, "skipped": 0}, error or str(exc)
        if context(proof, root, epic_dir, config)[0] != identity:
            error = "proof changed its source or execution context; attribute changes and rerun"
        clean = code == 0 and parsed["passed"] > 0 and not any(parsed[k] for k in ("failed", "errors", "skipped")) and error is None
        row = {
            "proof_id": proof["id"], "command": proof["command"], "context": identity,
            "outcome": "pass" if clean else "blocked" if error or not any(parsed.values()) else "fail", "exit_code": code, **parsed,
            "summary": error or ", ".join(f"{parsed[k]} {k}" for k in parsed),
            "evidence_hashes": {log.relative_to(root).as_posix(): fingerprint.file_sha256(log)},
            "started_at": begin, "duration_seconds": round(time.time() - begin, 3), "reused": False,
        }
        rows.append(row)
        available.append(row)
    receipt = {"schema_version": 1, "executor": "scope-proofs-v1", "started_at": started,
               "duration_seconds": round(time.time() - started, 3), "proofs": rows,
               "status": "pass" if all(row["outcome"] == "pass" for row in rows) else "fail"}
    path = output_dir / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {**receipt, "path": path.relative_to(root).as_posix(), "sha256": fingerprint.file_sha256(path)}


def groups(manifest: Mapping[str, Any], limit: int) -> list[list[str]]:
    """Stable topological order, joining only dependency-connected stories."""
    stories = {row["id"]: row for row in manifest["stories"]}
    if len(stories) != len(manifest["stories"]):
        raise ValueError("duplicate story IDs")
    for row in stories.values():
        dependencies = row.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies) or len(dependencies) != len(set(dependencies)):
            raise ValueError("story depends_on must be a unique list of story IDs")
    done: set[str] = set()
    result: list[list[str]] = []
    while len(done) < len(stories):
        group: list[str] = []
        while len(group) < limit:
            ready = [key for key, row in stories.items() if key not in done and set(row.get("depends_on", [])) <= done]
            if group:
                ready = [key for key in ready if set(stories[key].get("depends_on", [])) & set(group)]
            if not ready:
                break
            group.append(ready[0])
            done.add(ready[0])
        if not group:
            raise ValueError("story dependencies contain a cycle or unknown story")
        result.append(group)
    return result

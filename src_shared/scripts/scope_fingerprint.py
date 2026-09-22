"""Content-bound Git workspace fingerprints used by Scope lifecycle commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

import scope_git


PathExclusion = Callable[[str], bool]


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def file_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or symlinked file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def structured_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256_bytes(encoded)


def relative_path(value: str, label: str = "path") -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} must be a normalized repository-relative path")
    return value


def path_identity(path: Path) -> str | None:
    if path.is_symlink():
        return sha256_bytes(b"symlink\0" + os.readlink(path).encode("utf-8"))
    if path.is_file():
        return file_sha256(path)
    return None


def path_mode(path: Path) -> str | None:
    if path.is_symlink():
        return "120000"
    if path.is_file():
        return "100755" if path.lstat().st_mode & 0o111 else "100644"
    return None


def _records(
    root: Path, exclude: PathExclusion, *, include_mode: bool
) -> list[dict[str, Any]]:
    arguments = ["status"]
    if os.name != "nt":
        arguments[:0] = ["-c", "core.filemode=true"]
    raw = scope_git.git(
        root,
        *arguments,
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(raw, bytes)
    chunks = raw.split(b"\0")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if not chunk:
            continue
        if len(chunk) < 4:
            raise ValueError("unexpected Git status record")
        status = chunk[:2].decode("ascii", errors="replace")
        relative = chunk[3:].decode("utf-8", errors="surrogateescape")
        old_path: str | None = None
        if "R" in status or "C" in status:
            if index >= len(chunks):
                raise ValueError("truncated Git rename status record")
            old_path = chunks[index].decode("utf-8", errors="surrogateescape")
            index += 1
        relative_path(relative, "Git status path")
        if old_path is not None:
            relative_path(old_path, "Git status old path")
        if exclude(relative):
            continue
        row: dict[str, Any] = {
            "path": relative,
            "status": status,
            "content_sha256": path_identity(root / relative),
        }
        if include_mode:
            row["mode"] = path_mode(root / relative)
        if old_path is not None:
            row["old_path"] = old_path
        records.append(row)
    records.sort(key=lambda row: (str(row["path"]), str(row.get("old_path", ""))))
    return records


def workspace_fingerprint(
    repo_root: Path,
    *,
    exclude: PathExclusion | None = None,
    include_mode: bool = False,
) -> dict[str, Any]:
    root = scope_git.top_level(repo_root)
    changes = _records(
        root, exclude or (lambda _path: False), include_mode=include_mode
    )
    payload = {
        "head": scope_git.head(root),
        "tree": scope_git.tree(root),
        "changes": changes,
    }
    return {**payload, "workspace_sha256": structured_sha256(payload)}


def audit_fingerprint(
    epic_dir: Path,
    repo_root: Path,
    *,
    extra_excluded: Sequence[str] = (),
) -> dict[str, Any]:
    root = scope_git.top_level(repo_root)
    epic = epic_dir.resolve(strict=True)
    try:
        epic_relative = epic.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("epic directory is outside the Git root") from exc
    selected = {relative_path(value, "extra excluded path") for value in extra_excluded}

    def excluded(relative: str) -> bool:
        if relative in selected:
            return True
        if relative in {"tmp_debug", ".codegraph"} or relative.startswith(("tmp_debug/", ".codegraph/")):
            return True
        if relative in {
            f"{epic_relative}/audit-findings.yaml",
            f"{epic_relative}/epic_audit.md",
            f"{epic_relative}/implementation-evidence.yaml",
        }:
            return True
        return relative.startswith((f"{epic_relative}/reviews/audit-", f"{epic_relative}/reviews/proofs/"))

    return workspace_fingerprint(root, exclude=excluded, include_mode=True)

"""Retain reviewable Git content without changing HEAD or the user's index."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

import scope_fingerprint
import scope_git


def retain(root: Path, epic: Path, review_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", review_id):
        raise ValueError("invalid replay review ID")
    before = scope_fingerprint.audit_fingerprint(epic, root)
    directory = scope_git.runtime_directory(root, "scope-replays")
    directory.mkdir(parents=True, exist_ok=True)
    relative = epic.relative_to(root).as_posix()
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        env = scope_git._environment()
        env.update(GIT_INDEX_FILE=str(Path(temporary) / "index"), GIT_AUTHOR_NAME="Scope",
                   GIT_AUTHOR_EMAIL="scope@localhost", GIT_COMMITTER_NAME="Scope", GIT_COMMITTER_EMAIL="scope@localhost")

        def git(*args: str) -> str:
            result = subprocess.run(["git", "--no-replace-objects", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
                                    cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="surrogateescape", check=False)
            if result.returncode:
                raise ValueError(f"replay snapshot failed: {result.stderr.strip()}")
            return result.stdout if "-z" in args else result.stdout.strip()

        # HEAD seeds unchanged tracked files; add includes new, non-ignored files.
        git("read-tree", "HEAD")
        excluded = ("tmp_debug/", ".codegraph/", f"{relative}/reviews/proofs/", f"{relative}/reviews/audit-")
        pathspec = Path(temporary) / "paths"
        env["GIT_LITERAL_PATHSPECS"] = "1"
        # Update tracked files separately: ignored parents can make add -A reject
        # their explicit paths, while add -u preserves tracked edits/deletions.
        for listing, mode in ((("--cached",), "-u"), (("--others", "--exclude-standard"), "-A")):
            candidates = git("ls-files", "-z", *listing).split("\0")
            selected = sorted({path for path in candidates if path and not path.startswith(excluded)})
            pathspec.write_bytes(b"".join(path.encode("utf-8", errors="surrogateescape") + b"\0" for path in selected))
            if selected:
                git("add", mode, "--pathspec-from-file=" + str(pathspec), "--pathspec-file-nul")
        tree = git("write-tree")
        if scope_fingerprint.audit_fingerprint(epic, root) != before:
            raise ValueError("workspace changed while creating replay snapshot")
        ref = f"refs/scope/replays/{epic.name}/{review_id}"
        prior = git("for-each-ref", "--format=%(objectname)", ref)
        if prior:
            if git("rev-parse", prior + "^{tree}") != tree or git("rev-parse", prior + "^") != before["head"]:
                raise ValueError("existing replay snapshot differs; never overwrite a review")
            commit = prior
        else:
            commit = git("commit-tree", tree, "-p", before["head"], "-m", f"Scope replay {epic.name} {review_id}")
            git("update-ref", ref, commit, "0" * len(commit))
    return {"ref": ref, "commit": commit, "tree": tree, "source_sha256": before["workspace_sha256"]}

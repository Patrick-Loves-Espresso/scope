"""Epic resolution, `yaml scope` block parsing, and the Director exclude."""

from __future__ import annotations

import pytest

from conftest import git
from scope_common import ScopeError, find_epic, repo_root, scope_blocks, section


def test_find_epic_prefers_active_then_archived_and_rejects_ambiguity(tmp_path):
    epics = tmp_path / "docs" / "epics"
    (epics / "_implemented" / "E-12-old").mkdir(parents=True)
    assert find_epic(tmp_path, "e-12").name == "E-12-old"
    (epics / "E-12-new").mkdir()
    (epics / "E-123-other").mkdir()
    assert find_epic(tmp_path, "E-12").name == "E-12-new"
    (epics / "E-12-again").mkdir()
    with pytest.raises(ScopeError, match="ambiguous"):
        find_epic(tmp_path, "E-12")
    with pytest.raises(ScopeError, match="no epic folder"):
        find_epic(tmp_path, "E-9")


@pytest.mark.parametrize(
    ("body", "message"), [("a: [unclosed", "invalid yaml scope block"), ("- a list", "must be a mapping")]
)
def test_scope_blocks_reject_bad_blocks(tmp_path, body, message):
    path = tmp_path / "plan.md"
    path.write_text(f"# Plan\n\n```yaml scope\n{body}\n```\n", encoding="utf-8")
    with pytest.raises(ScopeError, match=message):
        scope_blocks(path)


def test_scope_blocks_merge_and_ignore_plain_yaml(tmp_path):
    path = tmp_path / "plan.md"
    path.write_text("```yaml scope\na: 1\n```\n\n```yaml\nb: 2\n```\n\n```yaml scope\nc: 3\n```\n", encoding="utf-8")
    assert scope_blocks(path) == {"a": 1, "c": 3}
    assert section("## A\n\nbody\n\n## B\n", "A") == "body" and section("## A\n", "C") is None


def test_repo_root_excludes_director_once_for_every_worktree(project):
    exclude = project / ".git" / "info" / "exclude"
    exclude.write_text("# local\nsession-handoff.md", encoding="utf-8")  # no final newline
    worktree = project / "wip" / "DEMO-001"
    git(project, "worktree", "add", "-q", "-b", "epic/DEMO-001", str(worktree))
    assert repo_root(str(worktree)) == worktree.resolve()
    assert repo_root(str(project)) == project
    assert exclude.read_text(encoding="utf-8") == "# local\nsession-handoff.md\n.director/\n"
    for checkout in (project, worktree):
        (checkout / ".director").mkdir()
        (checkout / ".director" / "workstream-id").write_text("w\n")
        assert git(checkout, "status", "--porcelain") == ""

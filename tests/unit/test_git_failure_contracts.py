"""Git failures must not publish an unapproved merge or silently lose changes."""
import subprocess

import pytest

import test_scope_dependency_merge as fixtures

GIT = fixtures.MERGE.scope_git


@pytest.mark.parametrize('failure', ['preview', 'merge', 'tree', 'commit'])
def test_exact_merge_aborts_on_external_git_failure(tmp_path, monkeypatch, failure):
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    before = GIT.head(work)
    real_run = GIT.run
    calls = []
    def run_git(arguments, root, **kwargs):
        calls.append(list(arguments))
        if (failure == 'preview' and 'merge-tree' in arguments or
            failure == 'merge' and 'merge' in arguments and '--no-commit' in arguments or
            failure == 'commit' and 'commit' in arguments):
            return subprocess.CompletedProcess(arguments, 1, '', 'injected disk failure')
        if failure == 'tree' and 'write-tree' in arguments:
            return subprocess.CompletedProcess(arguments, 0, '0' * 40, '')
        return real_run(arguments, root, **kwargs)
    monkeypatch.setattr(GIT, 'run', run_git)
    with pytest.raises(GIT.GitError):
        GIT.merge_exact(work, source, before, 'approved merge')
    assert GIT.head(work) == before
    assert GIT.status(work) == ''
    if failure != 'preview': assert ['git', 'merge', '--abort'] in calls
    assert not (work / 'dependency.txt').exists()


@pytest.mark.parametrize('failure', ['head', 'dirty', 'object'])
def test_exact_merge_rejects_changed_preconditions(tmp_path, failure):
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    before = GIT.head(work)
    expected = before
    if failure == 'head': expected = source
    if failure == 'dirty': (work / 'untracked.txt').write_text('must survive')
    if failure == 'object': source = GIT.tree(work)
    with pytest.raises(GIT.GitError): GIT.merge_exact(work, source, expected, 'approved merge')
    assert GIT.head(work) == before
    if failure == 'dirty': assert (work / 'untracked.txt').read_text() == 'must survive'


@pytest.mark.parametrize('failure', ['head', 'tree', 'commit'])
def test_commit_refuses_changed_approval_or_git_failure(tmp_path, monkeypatch, failure):
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    before = GIT.head(work)
    (work / 'new.txt').write_text('new')
    GIT.git(work, 'add', 'new.txt')
    tree = GIT.git(work, 'write-tree')
    real_run = GIT.run
    if failure == 'commit':
        def fail_commit(arguments, root, **kwargs):
            if 'commit' in arguments: return subprocess.CompletedProcess(arguments, 1, '', 'disk full')
            return real_run(arguments, root, **kwargs)
        monkeypatch.setattr(GIT, 'run', fail_commit)
    with pytest.raises(GIT.GitError):
        GIT.commit_index(work, source if failure == 'head' else before,
                         '0' * 40 if failure == 'tree' else tree, 'approved commit')
    assert GIT.head(work) == before
    assert (work / 'new.txt').read_text() == 'new'
    assert GIT.git(work, 'diff', '--cached', '--name-only') == 'new.txt'


def test_git_reports_launch_and_binary_errors(tmp_path, monkeypatch):
    def no_process(*args, **kwargs): raise OSError('not executable')
    with monkeypatch.context() as patch:
        patch.setattr(GIT.subprocess, 'run', no_process)
        with pytest.raises(GIT.GitError, match='cannot execute Git'): GIT.git(tmp_path, 'status')
    with pytest.raises(GIT.GitError, match='not a git repository'):
        GIT.git(tmp_path, 'status', binary=True)


def test_fingerprint_tracks_rename_and_rejects_outside_epic(tmp_path):
    import scope_fingerprint
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    GIT.git(work, 'mv', 'base.txt', 'renamed.txt')
    value = scope_fingerprint.workspace_fingerprint(work, include_mode=True)
    assert value['changes'] == [{'path': 'renamed.txt', 'old_path': 'base.txt', 'status': 'R ',
        'content_sha256': scope_fingerprint.path_identity(work / 'renamed.txt'), 'mode': '100644'}]
    with pytest.raises(ValueError, match='outside the Git root'):
        scope_fingerprint.audit_fingerprint(tmp_path, work)


def test_fingerprint_preserves_symlink_type_and_target(tmp_path):
    import scope_fingerprint
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    (work / 'link').symlink_to('base.txt')
    row = scope_fingerprint.workspace_fingerprint(work, include_mode=True)['changes'][0]
    assert row['path'] == 'link' and row['mode'] == '120000'
    assert row['content_sha256'] == scope_fingerprint.path_identity(work / 'link')


@pytest.mark.parametrize('value', ['HEAD', 'a' * 39, 'g' * 40, '--all'])
def test_git_mutations_require_full_immutable_object_ids(value):
    with pytest.raises(GIT.GitError, match='full object ID'): GIT.full_commit(value)
    assert GIT.full_commit('A' * 40) == 'a' * 40
    assert GIT.full_commit('B' * 64) == 'b' * 64


def test_git_root_must_be_checkout_root_not_nested_directory(tmp_path):
    main, work, epic, run, source = fixtures._fixture(tmp_path)
    with pytest.raises(GIT.GitError, match='Git root mismatch'): GIT.top_level(epic)
    assert GIT.top_level(work) == work.resolve()


@pytest.mark.parametrize('component', ['', '../outside', '..'])
def test_runtime_directory_rejects_escape_components(tmp_path, component):
    with pytest.raises(GIT.GitError, match='component is invalid|escapes its root'):
        GIT.runtime_directory(tmp_path, component)
    assert not (tmp_path / 'outside').exists()


def test_mutation_lock_and_evidence_hash_refuse_symlink_redirection(tmp_path):
    import scope_fingerprint
    outside = tmp_path / 'external.txt'
    outside.write_text('must remain intact')
    root = tmp_path / 'repo'
    root.mkdir()
    runtime = GIT.runtime_directory(root)
    (runtime / 'scope-mutation.lock').symlink_to(outside)
    with pytest.raises(GIT.GitError, match='lock must not be a symlink'):
        with GIT.mutation_locks([root]): pytest.fail('acquired redirected lock')
    evidence = root / 'evidence.txt'
    evidence.symlink_to(outside)
    with pytest.raises(ValueError, match='symlinked file'): scope_fingerprint.file_sha256(evidence)
    assert outside.read_text() == 'must remain intact'

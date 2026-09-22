"""Index tool failures degrade to repository inspection without changing source."""
import pytest

import test_scope_codegraph as fixtures

CG = fixtures.scope_codegraph


@pytest.mark.parametrize('stage,reason', [('--version', 'version_check_failed'),
    ('check-ignore', 'ignore_check_failed'), ('init', 'lifecycle_command_failed')])
def test_prepare_handles_process_launch_failure(tmp_path, monkeypatch, stage, reason):
    repo = fixtures._repository(tmp_path)
    policy = fixtures._policy()
    policy['executable'] = str(fixtures._fake(tmp_path))
    real_run = CG._run
    def failing_run(command, **kwargs):
        if stage in command: raise OSError('tool became unavailable')
        return real_run(command, **kwargs)
    monkeypatch.setattr(CG, '_run', failing_run)
    state = CG.prepare(policy, repo)
    assert state['status'] in {'unavailable', 'degraded'}
    assert state['reason'] == reason
    assert 'tool became unavailable' in state['error']


@pytest.mark.parametrize('failure', ['root', 'state', 'symlink', 'sync-exit', 'sync-os'])
def test_incremental_sync_rejects_unusable_prepared_state(tmp_path, monkeypatch, failure):
    repo = fixtures._repository(tmp_path)
    policy = fixtures._policy()
    policy['executable'] = str(fixtures._fake(tmp_path))
    state = CG.prepare(policy, repo)
    assert state['status'] == 'ready'
    if failure == 'root': state['project_root'] = str(tmp_path)
    if failure == 'state': state.pop('executable')
    if failure == 'symlink':
        database = repo / '.codegraph/codegraph.db'
        database.unlink()
        database.symlink_to(repo / '.gitignore')
    if failure == 'sync-exit': monkeypatch.setenv('FAKE_CODEGRAPH_SYNC_FAIL', '1')
    if failure == 'sync-os':
        def unavailable(*args, **kwargs): raise OSError('missing binary')
        monkeypatch.setattr(CG, '_run', unavailable)
    result = CG.sync(policy, repo, state)
    assert result['status'] == 'degraded'
    assert result['reason'] == {'root': 'project_root_changed', 'state': 'prepared_state_invalid',
        'symlink': 'index_path_is_symlink', 'sync-exit': 'sync_failed', 'sync-os': 'sync_failed'}[failure]


@pytest.mark.parametrize('failure,reason', [('empty-version', 'version_check_failed'),
    ('invalid-version', 'unparseable_version'), ('disabled-init', 'index_not_initialized')])
def test_codegraph_unusable_version_or_absent_index_stays_unavailable(tmp_path, monkeypatch, failure, reason):
    repo = fixtures._repository(tmp_path)
    policy = fixtures._policy()
    policy['executable'] = str(fixtures._fake(tmp_path))
    if failure == 'empty-version': monkeypatch.setenv('FAKE_CODEGRAPH_VERSION', '')
    if failure == 'invalid-version': monkeypatch.setenv('FAKE_CODEGRAPH_VERSION', 'unknown')
    if failure == 'disabled-init': policy['initialize_if_missing'] = False
    state = CG.prepare(policy, repo)
    assert state['status'] == 'unavailable' and state['reason'] == reason
    assert not (repo / '.codegraph').exists()

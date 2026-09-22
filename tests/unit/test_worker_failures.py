"""Worker transport, interruption and recovery contracts with real run artifacts."""
import json
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest
import yaml

import test_scope_worker as fixtures

RUNNER = fixtures.RUNNER


@pytest.mark.parametrize('provider', ['codex', 'claude'])
@pytest.mark.parametrize('failure', [None, 'missing', 'version', 'flags', 'auth-json', 'auth-denied'])
def test_provider_preflight_requires_usable_cli(tmp_path, monkeypatch, provider, failure):
    # A CLI fixture implements only the public version/help/auth protocol; it never calls a model.
    cli = tmp_path / 'provider'
    cli.write_text('#!' + sys.executable + '\n' +
        'import json, sys\n' +
        f'failure = {failure!r}\n' +
        "if '--version' in sys.argv: print('' if failure == 'version' else 'fixture 1.0')\n" +
        "elif '--help' in sys.argv: print('' if failure == 'flags' else '--output-schema --output-last-message --ignore-user-config --print --json-schema --no-session-persistence --permission-mode')\n" +
        "else: print('invalid' if failure == 'auth-json' else json.dumps({'loggedIn': failure != 'auth-denied'}))\n")
    cli.chmod(0o755)
    selected = {'executable': str(cli) if failure != 'missing' else str(tmp_path / 'absent')}
    expected = {'missing': 'not found', 'version': 'no version', 'flags': 'required flags',
                'auth-json': 'not JSON', 'auth-denied': 'not authenticated'}
    if failure and (provider == 'claude' or not failure.startswith('auth')):
        with pytest.raises(RUNNER.InfrastructureError, match=expected[failure]):
            RUNNER.provider_preflight(provider, selected)
    else:
        assert RUNNER.provider_preflight(provider, selected) == {'executable': str(cli), 'version': 'fixture 1.0'}


@pytest.mark.parametrize('access,scope,graph', [('read-only', [], False), ('workspace-write', ['src'], False),
                                             ('workspace-write', ['.'], True)])
def test_claude_command_limits_tools_and_retains_isolation(tmp_path, access, scope, graph):
    job = {'write_scope': scope, 'required_validations': [{'command': 'pytest -q'}],
           'allowed_commands': ['pytest -q', 'python check.py'], 'implementation_evidence_path': 'evidence.yaml'}
    command = RUNNER.build_claude_command('claude', {'model': 'claude-opus-5-5', 'reasoning_effort': 'high',
        'permission_mode': 'acceptEdits'}, job, {'type': 'object'}, access, fixtures._ready(tmp_path) if graph else {})
    tools = command[command.index('--tools') + 1].split(',')
    allowed = command[command.index('--allowedTools') + 1].split(',')
    denied = command[command.index('--disallowedTools') + 1].split(',')
    assert ('Write' in tools) == (access == 'workspace-write')
    assert allowed.count('Bash(pytest -q)') == 1
    assert 'Bash(python check.py)' in allowed
    assert 'Write(evidence.yaml)' in denied and 'Bash(git push *)' in denied
    assert '--no-session-persistence' in command and '--strict-mcp-config' in command
    if graph:
        assert 'Bash(codegraph query:*)' in allowed
    if scope == ['.']:
        assert 'Write(**)' in allowed


@pytest.mark.parametrize('envelope,message', [({'is_error': True, 'result': 'quota'}, 'quota'),
    ({'terminal_reason': 'api_error', 'error': 'offline'}, 'offline'),
    ({'result': 'not json'}, 'not structured JSON'), ({'result': []}, 'no structured result')])
def test_claude_failed_envelopes_cannot_be_published(tmp_path, envelope, message):
    stdout = tmp_path / 'stdout'
    stdout.write_text(json.dumps(envelope))
    with pytest.raises(RUNNER.WorkerError, match=message):
        RUNNER._provider_result('claude', tmp_path / 'unused', stdout)


def test_codex_usage_ignores_log_noise_and_keeps_final_usage(tmp_path):
    result = tmp_path / 'result.json'
    result.write_text('{"status": "completed"}')
    stdout = tmp_path / 'stdout'
    stdout.write_text('not json\n[]\n{"usage": {"tokens": 12}}\n{"usage": {"tokens": 20}}\n')
    value, usage, fallback = RUNNER._provider_result('codex', result, stdout)
    assert value == {'status': 'completed'} and usage == {'tokens': 20} and fallback is None


@pytest.mark.parametrize('damage,message', [('packet', 'packet changed'), ('before', 'missing its before snapshot'),
                                         ('result', 'invalid Codex worker result')])
def test_recovery_rejects_missing_or_changed_inputs_without_losing_work(tmp_path, monkeypatch, damage, message):
    repo, run_path, job, job_path = fixtures._orphaned_write_run(monkeypatch, tmp_path, include_after=True)
    job_path = Path(yaml.safe_load(run_path.read_text())['active_job']['job_path'])
    if damage == 'packet':
        job_path.write_text(job_path.read_text() + '\n# changed\n')
    elif damage == 'before':
        (job_path.parent / 'before-snapshot.json').unlink()
    else:
        (job_path.parent / 'provider-result.json').write_text('invalid JSON')
    row = RUNNER.recover_run(run_path)
    assert row['status'] == 'interrupted'
    assert message in row['reason']
    assert (repo / 'src/value.txt').read_text() == 'worker\n'
    assert not Path(job['result_path']).exists()
    assert yaml.safe_load(run_path.read_text())['active_job'] is None


@pytest.mark.parametrize('include_after', [False, True])
def test_cancel_orphaned_write_records_delta_and_retains_work(tmp_path, monkeypatch, include_after):
    repo, run_path, job, job_path = fixtures._orphaned_write_run(monkeypatch, tmp_path, include_after=include_after)
    run = yaml.safe_load(run_path.read_text())
    run['active_job']['cancellation_path'] = str(job_path.parent / 'cancel.yaml')
    RUNNER.atomic_write_yaml(run_path, run)
    result = RUNNER.cancel_run(run_path, job['job_id'], 'user redirected work')
    assert result['status'] == 'cancelled'
    assert 'src/value.txt' in result['job']['changed_paths']
    assert (repo / 'src/value.txt').read_text() == 'worker\n'
    assert yaml.safe_load((job_path.parent / 'cancel.yaml').read_text())['reason'] == 'user redirected work'
    assert yaml.safe_load(run_path.read_text())['active_job'] is None


@pytest.mark.parametrize('reason', ['', 'two\nlines', 'x' * 501])
def test_cancel_rejects_invalid_reason_before_touching_run(tmp_path, reason):
    with pytest.raises(RUNNER.ContractError, match='reason'):
        RUNNER.cancel_run(tmp_path / 'missing', 'job', reason)


@pytest.mark.parametrize('failure', ['group', 'proofs', 'order', 'debug-checkpoint', 'debug-proofs'])
def test_v3_job_rejects_invalid_scheduling_before_provider_launch(tmp_path, monkeypatch, failure):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    fixtures._initialize(monkeypatch, repo, scope)
    manifest = repo / 'docs/epics/gd-001/delivery-manifest.yaml'
    argv = [sys.executable, '-c', "print('1 passed')"]
    manifest.write_text(yaml.safe_dump({'schema_version': 3, 'epic_id': 'gd-001',
        'stories': [{'id': 'S1', 'depends_on': [], 'proof_ids': ['P1']}, {'id': 'S2', 'depends_on': [], 'proof_ids': ['P2']}],
        'proofs': [{'id': 'P1', 'command': shlex.join(argv)}, {'id': 'P2', 'command': shlex.join(argv)}]}))
    job, path = fixtures._job(repo, scope)
    job.update(story_ids=['S1'], required_proof_ids=['P1'])
    expected = {'group': 'story_ids', 'proofs': 'exact union', 'order': 'first incomplete',
                'debug-checkpoint': 'failed runner proof checkpoint', 'debug-proofs': 'complete failed checkpoint'}
    if failure == 'group': job['story_ids'] = ['S1', 'S2']
    if failure == 'proofs': job['required_proof_ids'] = []
    if failure == 'order': job.update(story_ids=['S2'], required_proof_ids=['P2'])
    if failure.startswith('debug'):
        job['phase'] = 'debugging'
        checkpoint = repo / 'checkpoint.json'
        checkpoint.write_text(json.dumps({'proofs': [{'proof_id': 'P2'}]}))
        evidence = {'proof_checkpoint': {'path': 'checkpoint.json', 'status': 'fail', 'sha256': RUNNER._sha256_file(checkpoint)}}
        if failure == 'debug-checkpoint': evidence['proof_checkpoint']['status'] = 'pass'
        (repo / job['implementation_evidence_path']).write_text(yaml.safe_dump(evidence))
    path.write_text(yaml.safe_dump(job))
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda *args: pytest.fail('invalid job launched provider'))
    with pytest.raises(RUNNER.ContractError, match=expected[failure]):
        RUNNER.run_worker(SimpleNamespace(job=path, role='implementation', cwd=repo, result=Path(job['result_path']),
            provider='codex', worker_profile='default', access='workspace-write'))


@pytest.mark.parametrize('error,code,prefix', [(RUNNER.WorkerTimeout('slow'), 124, 'timeout'),
    (RUNNER.ActiveWorkerError('busy'), 3, 'active'), (RUNNER.ContractError('invalid'), 1, 'failed'),
    (OSError('unavailable'), 1, 'failed')])
def test_worker_cli_preserves_failure_exit_codes(tmp_path, monkeypatch, capsys, error, code, prefix):
    def fail(*args): raise error
    monkeypatch.setattr(RUNNER, 'classify_run', fail)
    assert RUNNER.main(['status', '--run', str(tmp_path / 'run.yaml')]) == code
    assert f'Scope worker {prefix}:' in capsys.readouterr().err


def test_worker_cli_status_and_recover_idle_run(tmp_path, monkeypatch, capsys):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    run = fixtures._initialize(monkeypatch, repo, scope)
    for command in ('status', 'recover'):
        assert RUNNER.main([command, '--run', str(run)]) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'idle'


@pytest.mark.parametrize('damage', [None, 'legacy', 'policy', 'scope', 'profile', 'active-legacy'])
def test_reinitialization_reuses_only_compatible_run(tmp_path, monkeypatch, damage):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    run_path = fixtures._initialize(monkeypatch, repo, scope)
    run = yaml.safe_load(run_path.read_text())
    if damage in {'legacy', 'active-legacy'}:
        run.pop('scope_root')
        run.pop('worker_policy_sha256')
        if damage == 'active-legacy': run['active_job'] = {'job_id': 'busy'}
    if damage == 'policy': run['worker_policy_sha256'] = 'sha256:' + '0' * 64
    if damage == 'scope': run['scope_root'] = str(tmp_path)
    if damage == 'profile': run['worker_profile'] = 'on_budget'
    RUNNER.atomic_write_yaml(run_path, run)
    if damage not in {None, 'legacy'}:
        with pytest.raises(RUNNER.WorkerError):
            RUNNER.initialize_run(repo, repo, 'gd-001', 'implement', 'default', scope)
        assert yaml.safe_load(run_path.read_text()) == run
    else:
        result = RUNNER.initialize_run(repo, repo, 'gd-001', 'implement', 'default', scope)
        assert result['created'] is False
        rebound = yaml.safe_load(run_path.read_text())
        assert rebound['scope_root'] == str(scope)
        assert rebound['worker_policy_sha256'] == RUNNER._sha256_file(scope / 'config/worker-policy.yaml')


@pytest.mark.parametrize('failure', ['launch', 'timeout', 'cancel', 'exit'])
def test_provider_supervision_handles_os_failure_timeout_and_cancel(tmp_path, failure):
    repo = fixtures._repo(tmp_path / 'repo')
    cancel = tmp_path / 'cancel.yaml'
    command = [sys.executable, '-c', "import sys,time; sys.stdin.read(); time.sleep(5)"]
    if failure == 'launch': command = [str(tmp_path / 'absent')]
    if failure == 'cancel':
        command = [sys.executable, '-c', f"from pathlib import Path; import time; Path({str(cancel)!r}).write_text('cancel'); time.sleep(5)"]
    if failure == 'exit': command = [sys.executable, '-c', 'import sys; sys.exit(7)']
    run = {'active_job': {'job_id': 'job', 'provider_process': None, 'provider_process_group': None, 'provider_descendants': []}}
    selected = {'timeout_seconds': .2, 'termination_grace_seconds': .1, 'normal_exit_grace_seconds': .1,
                'heartbeat_interval_seconds': .03, 'poll_interval_seconds': .01}
    args = dict(working_root=repo, stdout_path=tmp_path / 'out', stderr_path=tmp_path / 'err',
                run_path=tmp_path / 'run.yaml', run=run, cancellation_path=cancel, selected=selected)
    if failure == 'launch':
        with pytest.raises(RUNNER.InfrastructureError, match='unable to launch'):
            RUNNER.execute_provider(command, '', **args)
    else:
        code, timeout, cancelled = RUNNER.execute_provider(command, '', **args)
        assert timeout == (failure == 'timeout')
        assert cancelled == (failure == 'cancel')
        assert RUNNER.identity_state(run['active_job']['provider_process']) == 'dead'
        if failure == 'exit': assert code == 7
        if failure == 'timeout': assert run['active_job'].get('last_heartbeat_at')


@pytest.mark.parametrize('damage,message', [('evidence-name', 'must name'), ('evidence-parent', 'direct epic'),
    ('artifact-hash', 'hash-mismatched'), ('artifact-scope', 'outside read_scope'),
    ('decision-hash', 'decision source'), ('duplicate-decision', 'duplicate decision'),
    ('result-relative', 'absolute'), ('result-location', 'result_path must be')])
def test_job_loading_rejects_unauthorized_inputs_and_outputs(tmp_path, damage, message):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    job, path = fixtures._job(repo, scope)
    if damage == 'evidence-name': job['implementation_evidence_path'] = 'docs/epics/gd-001/forged.yaml'
    if damage == 'evidence-parent': job['implementation_evidence_path'] = 'src/implementation-evidence.yaml'
    if damage == 'artifact-hash': job['artifacts'][0]['sha256'] = 'sha256:' + '0' * 64
    if damage == 'artifact-scope': job['read_scope'] = ['src']
    if 'decision' in damage:
        job['decision_refs'] = [{'id': 'D1', 'path': 'README.md', 'sha256': RUNNER._sha256_file(repo / 'README.md')}]
        if damage == 'decision-hash': job['decision_refs'][0]['sha256'] = 'sha256:' + '0' * 64
        else: job['decision_refs'].append(dict(job['decision_refs'][0]))
    if damage == 'result-relative': job['result_path'] = 'result.json'
    if damage == 'result-location': job['result_path'] = str(path.parent / 'other.json')
    path.write_text(yaml.safe_dump(job))
    with pytest.raises(RUNNER.ContractError, match=message): RUNNER.load_job(path)


def test_worker_cannot_forge_runner_proof_directory(tmp_path):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    job, _ = fixtures._job(repo, scope, write_scope=['.'])
    before = RUNNER.capture_snapshot(repo)
    proof = repo / 'docs/epics/gd-001/reviews/proofs/forged/result.json'
    proof.parent.mkdir(parents=True)
    proof.write_text('{"outcome":"pass"}')
    after = RUNNER.capture_snapshot(repo)
    changed = RUNNER.snapshot_delta(before, after)
    with pytest.raises(RUNNER.ContractError, match='runner-owned proof evidence'):
        RUNNER._validate_attribution(job, fixtures._result(job, changed), before, after)


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'), (['provider'], 'unknown', 'provider must'),
    (['workers'], None, 'missing workers'), (['workers', 'implementation', 'phases'], {}, 'phases must'),
    (['workers', 'implementation', 'phases', 'story'], {}, 'model and reasoning_effort'),
    (['workers', 'implementation', 'phases', 'story', 'model'], '', 'non-empty'),
    (['provider_settings'], {}, 'executable'), (['runtime'], {}, 'roles and runtime.lifecycle'),
    (['runtime', 'roles', 'implementation'], None, 'missing role'),
    (['runtime', 'roles', 'implementation', 'timeout_seconds'], 0, 'positive'),
    (['runtime', 'lifecycle', 'poll_interval_seconds'], 0, 'positive'),
    (['runtime', 'lifecycle', 'normal_exit_grace_seconds'], -1, 'non-negative'),
])
def test_worker_rejects_invalid_runtime_policy(tmp_path, path, value, message):
    from test_evidence_rejection import change
    scope = fixtures._scope(tmp_path / 'scope')
    policy = scope / 'config/worker-policy.yaml'
    doc = yaml.safe_load(policy.read_text())
    change(doc, path, value)
    policy.write_text(yaml.safe_dump(doc))
    with pytest.raises(RUNNER.ContractError, match=message): RUNNER.load_policy(scope)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'cancel', 'launch', 'packet', 'invalid-result'])
def test_failed_write_job_keeps_work_and_records_interruption(tmp_path, monkeypatch, failure):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    policy = scope / 'config/worker-policy.yaml'
    doc = yaml.safe_load(policy.read_text())
    doc['runtime']['roles']['implementation']['timeout_seconds'] = 1
    doc['runtime']['lifecycle'].update(poll_interval_seconds=.01, termination_grace_seconds=.1)
    policy.write_text(yaml.safe_dump(doc))
    run = fixtures._initialize(monkeypatch, repo, scope)
    job, path = fixtures._job(repo, scope)
    monkeypatch.setattr(RUNNER.scope_codegraph, 'sync', lambda policy, root, prior: prior)
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda *args: {'executable': sys.executable, 'version': 'fixture'})
    fake = fixtures._fake_writer(tmp_path / 'author.py')
    script = fake.read_text()
    if failure == 'exit': script += '\nraise SystemExit(7)\n'
    if failure == 'timeout': script += '\nimport time; time.sleep(10)\n'
    if failure == 'cancel': script += "\nPath(result_path).with_name('cancel.yaml').write_text('cancel')\n"
    if failure == 'packet': script += f'\nPath({str(path)!r}).write_text("tampered")\n'
    if failure == 'invalid-result': script += '\nPath(result_path).write_text("invalid JSON")\n'
    fake.write_text(script)
    command = [sys.executable, str(fake)]
    if failure == 'launch': command = [str(tmp_path / 'nonexistent')]
    monkeypatch.setattr(RUNNER, 'build_codex_command', lambda executable, selected, working_root, access, schema, output, graph:
        [*command, str(output), str(repo / 'src/value.txt'), 'src/value.txt', job['job_id']])
    with pytest.raises(RUNNER.WorkerError):
        RUNNER.run_worker(SimpleNamespace(job=path, role='implementation', cwd=repo, result=Path(job['result_path']),
            provider='codex', worker_profile='default', access='workspace-write'))
    state = yaml.safe_load(run.read_text())
    assert state['active_job'] is None
    assert state['completed_jobs'][-1]['status'] == 'interrupted'
    assert not Path(job['result_path']).exists()
    assert (repo / 'src/value.txt').read_text() == ('before\n' if failure == 'launch' else 'after\n')


@pytest.mark.parametrize('damage', ['active', 'reused', 'mutation-lock'])
def test_worker_refuses_overlapping_or_reused_jobs_and_releases_state_lock(tmp_path, monkeypatch, damage):
    from filelock import FileLock
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    run = fixtures._initialize(monkeypatch, repo, scope)
    job, path = fixtures._job(repo, scope)
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda *args: {'executable': sys.executable, 'version': 'fixture'})
    state = yaml.safe_load(run.read_text())
    if damage == 'active': state['active_job'] = {'job_id': 'already-running'}
    if damage == 'reused': state['completed_jobs'] = [{'job_id': job['job_id']}]
    RUNNER.atomic_write_yaml(run, state)
    guard = FileLock(str(RUNNER.mutation_lock_path(repo)))
    if damage == 'mutation-lock': guard.acquire()
    try:
        with pytest.raises(RUNNER.WorkerError):
            RUNNER.run_worker(SimpleNamespace(job=path, role='implementation', cwd=repo, result=Path(job['result_path']),
                provider='codex', worker_profile='default', access='workspace-write'))
    finally: guard.release()
    with FileLock(str(RUNNER.run_state_lock_path(run)), timeout=0):
        assert yaml.safe_load(run.read_text()) == state


@pytest.mark.parametrize('damage,message', [('job-id', 'job_id'), ('role', 'payload kind'),
    ('validation-missing', 'missing required'), ('validation-extra', 'undeclared'),
    ('validation-duplicate', 'duplicate validation'), ('validation-failed', 'failed validations'),
    ('blocking-issue', 'blocking issue')])
def test_result_cannot_claim_completion_with_wrong_identity_or_validation(tmp_path, damage, message):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    job, path = fixtures._job(repo, scope)
    schema = json.loads((scope / 'config/worker-result.schema.json').read_text())
    result = fixtures._result(job, [])
    if damage == 'job-id': result['job_id'] = 'other'
    if damage == 'role': result['payload'] = {'kind': 'diagnostic', 'cause': 'unknown', 'evidence': [], 'recommended_action': 'inspect'}
    if damage.startswith('validation'):
        job['required_validations'] = [{'command': 'pytest', 'expected_result': 'passes'}]
        result['validations'] = [{'command': 'pytest', 'exit_code': 0, 'summary': 'passed'}]
        if damage == 'validation-missing': result['validations'] = []
        if damage == 'validation-extra': result['validations'][0]['command'] = 'other'
        if damage == 'validation-duplicate': result['validations'].append(dict(result['validations'][0]))
        if damage == 'validation-failed': result['validations'][0]['exit_code'] = 1
    if damage == 'blocking-issue': result['issues'] = [{'severity': 'blocking', 'message': 'cannot finish', 'evidence': ['src/value.txt']}]
    with pytest.raises(RUNNER.ContractError, match=message): RUNNER.validate_result(result, job, schema)


@pytest.mark.parametrize('provider', ['codex', 'claude'])
@pytest.mark.parametrize('behavior', ['valid', 'reported-write', 'actual-write'])
def test_read_only_diagnostic_worker_preserves_contract(tmp_path, monkeypatch, provider, behavior):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope', provider=provider)
    run = fixtures._initialize(monkeypatch, repo, scope)
    job, path = fixtures._job(repo, scope, role='diagnostic')
    result = fixtures._result(job, ['src/value.txt'] if behavior == 'reported-write' else [])
    result['payload'] = {'kind': 'diagnostic', 'cause': 'configuration missing', 'evidence': ['README.md'], 'recommended_action': 'configure'}
    script = 'from pathlib import Path; import sys,json; '
    if behavior == 'actual-write': script += "Path('src/value.txt').write_text('unauthorized'); "
    if provider == 'codex': script += f'Path(sys.argv[1]).write_text({json.dumps(result)!r})'
    else: script += f'print({json.dumps({"structured_output": result})!r})'
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda *args: {'executable': sys.executable, 'version': 'fixture'})
    monkeypatch.setattr(RUNNER, 'build_codex_command', lambda executable, selected, root, access, schema, output, graph:
        [sys.executable, '-c', script, str(output)])
    monkeypatch.setattr(RUNNER, 'build_claude_command', lambda *args: [sys.executable, '-c', script])
    args = SimpleNamespace(job=path, role='diagnostic', cwd=repo, result=Path(job['result_path']),
                           provider=provider, worker_profile='default', access='read-only')
    if behavior == 'valid':
        assert RUNNER.run_worker(args) == 0
        assert json.loads(Path(job['result_path']).read_text())['payload']['cause'] == 'configuration missing'
    else:
        with pytest.raises(RUNNER.ContractError, match='read-only'): RUNNER.run_worker(args)
        assert not Path(job['result_path']).exists()
    assert yaml.safe_load(run.read_text())['active_job'] is None


def test_worker_init_and_preflight_cli_bind_scope_and_phase(tmp_path, monkeypatch, capsys):
    repo = fixtures._repo(tmp_path / 'repo')
    scope = fixtures._scope(tmp_path / 'scope')
    monkeypatch.setattr(RUNNER.scope_codegraph, 'prepare', lambda policy, root: fixtures._ready(root))
    assert RUNNER.main(['init', '--repository-root', str(repo), '--working-root', str(repo),
        '--epic-id', 'gd-001', '--command', 'implement', '--scope-root', str(scope)]) == 0
    assert json.loads(capsys.readouterr().out)['created'] is True
    monkeypatch.setattr(RUNNER, 'provider_preflight', lambda provider, selected: {'model': selected['model']})
    args = ['preflight', '--provider', 'codex', '--role', 'implementation', '--scope-root', str(scope), '--phase']
    assert RUNNER.main([*args, 'story']) == 0
    assert json.loads(capsys.readouterr().out)['model'] == 'gpt-6-sol'
    assert RUNNER.main([*args, 'design_handoff']) == 1
    assert 'incompatible' in capsys.readouterr().err


@pytest.mark.parametrize('damage', [None, 'identity', 'reported-write'])
def test_read_only_recovery_rechecks_tree_and_result(tmp_path, monkeypatch, damage):
    repo, run_path, job, _ = fixtures._orphaned_write_run(monkeypatch, tmp_path, include_after=True)
    run = yaml.safe_load(run_path.read_text())
    active = run['active_job']
    job.update(role='diagnostic', phase='investigate', write_scope=[])
    job.pop('implementation_evidence_path')
    path = Path(active['job_path'])
    path.write_text(yaml.safe_dump(job))
    result = fixtures._result(job, ['src/value.txt'] if damage == 'reported-write' else [])
    result['payload'] = {'kind': 'diagnostic', 'cause': 'missing configuration', 'evidence': ['README.md'], 'recommended_action': 'configure'}
    Path(active['provider_result_path']).write_text(json.dumps(result))
    active.update(role='diagnostic', phase='investigate', access='read-only', job_sha256=RUNNER._sha256_file(path),
                  read_identity_before=RUNNER._read_identity(repo))
    RUNNER.atomic_write_yaml(run_path, run)
    if damage == 'identity': (repo / 'src/value.txt').write_text('later change')
    row = RUNNER.recover_run(run_path)
    assert row['status'] == ('completed' if damage is None else 'interrupted')
    if damage:
        assert 'read-only' in row['reason']
        assert not Path(job['result_path']).exists()
    else:
        assert json.loads(Path(job['result_path']).read_text())['payload']['kind'] == 'diagnostic'


def test_cancel_live_runner_requests_acknowledgement_without_publishing(tmp_path, monkeypatch, capsys):
    repo, run_path, job, _ = fixtures._orphaned_write_run(monkeypatch, tmp_path, include_after=True)
    run = yaml.safe_load(run_path.read_text())
    run['active_job']['runner_process'] = RUNNER.process_identity()
    RUNNER.atomic_write_yaml(run_path, run)
    assert RUNNER.main(['cancel', '--run', str(run_path), '--job-id', job['job_id'], '--reason', 'redirect']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'cancellation_requested'
    state = yaml.safe_load(run_path.read_text())
    assert state['active_job']['job_id'] == job['job_id']
    assert state['completed_jobs'] == []
    assert not Path(job['result_path']).exists()


@pytest.mark.parametrize('lock_kind', ['state', 'mutation'])
def test_recovery_refuses_held_locks_without_publishing(tmp_path, monkeypatch, lock_kind):
    from filelock import FileLock
    repo, run_path, job, _ = fixtures._orphaned_write_run(monkeypatch, tmp_path, include_after=True)
    selected = RUNNER.run_state_lock_path(run_path) if lock_kind == 'state' else RUNNER.mutation_lock_path(repo)
    with FileLock(str(selected)):
        with pytest.raises(RUNNER.ActiveWorkerError): RUNNER.recover_run(run_path)
    assert not Path(job['result_path']).exists()
    assert RUNNER.recover_run(run_path)['status'] == 'completed'

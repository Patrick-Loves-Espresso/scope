"""Reviewer routing and read-only constraints fail closed on invalid settings."""
from copy import deepcopy

import pytest
import yaml

import test_scope_reviewer as fixtures
from test_evidence_rejection import change

RUNNER = fixtures.RUNNER


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'),
    (['reviewers', 'audit'], {}, 'exactly'),
    (['reviewers', 'audit', 'codex', 'extra'], True, 'only model/effort'),
    (['reviewers', 'audit', 'codex', 'model'], 'unapproved', 'invalid reviewer model'),
    (['reviewers', 'audit', 'codex', 'reasoning_effort'], 'ultra', 'invalid reviewer effort'),
    (['reviewer_sets', 'audit'], {}, 'standard and expanded'),
    (['reviewer_sets', 'audit', 'standard'], ['codex', 'codex'], 'invalid providers'),
    (['reviewer_sets', 'audit', 'expanded'], ['codex'], 'include the standard'),
])
def test_reviewer_policy_rejects_unsupported_routing(tmp_path, path, value, message):
    policy = yaml.safe_load(fixtures.POLICY_PATH.read_text())
    change(policy, path, value)
    path = tmp_path / 'policy.yaml'
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(RUNNER.ReviewerError, match=message):
        RUNNER.load_policy(path)


@pytest.mark.parametrize('flag,value,message', [
    ('--safe-mode', None, 'missing required flags'),
    ('--permission-mode', 'acceptEdits', 'dontAsk'),
    ('--output-format', 'json', 'output-format text'),
    ('--tools', 'Read,Write', 'tools must be'),
    ('--allowedTools', 'Read,Glob,Grep,Bash', 'exactly match'),
    ('--disallowedTools', 'Write', 'missing write tools'),
    ('--mcp-config', 'server.json', 'forbidden flags'),
])
def test_claude_review_cannot_gain_write_or_network_tools(flag, value, message):
    command = yaml.safe_load(fixtures.POLICY_PATH.read_text())['providers']['claude']['command_args']
    RUNNER._validate_read_only_command('claude', command)
    if flag in command:
        index = command.index(flag)
        if value is None: command.pop(index)
        else: command[index + 1] = value
    else: command.extend([flag, value])
    with pytest.raises(RUNNER.ReviewerError, match=message):
        RUNNER._validate_read_only_command('claude', command)


@pytest.mark.parametrize('backend,command,message', [
    ('codex', ['exec'], 'missing read-only flags'),
    ('codex', ['exec', '--ephemeral', '--ignore-user-config', '--sandbox', 'workspace-write'], 'read-only'),
    ('codex', ['exec', '--ephemeral', '--ignore-user-config', '--sandbox', 'read-only', '--dangerously-bypass-approvals-and-sandbox'], 'bypasses'),
    ('agy', [], 'sandbox'), ('opencode', [], 'pure plan'),
    ('opencode', ['--pure', '--agent', 'build'], 'plan agent'),
])
def test_other_review_backends_cannot_bypass_read_only_mode(backend, command, message):
    with pytest.raises(RUNNER.ReviewerError, match=message):
        RUNNER._validate_read_only_command(backend, command)


@pytest.fixture(scope='module')
def completed_review(tmp_path_factory):
    repo, state, policy = fixtures._environment(tmp_path_factory.mktemp('receipt'))
    packet, template = fixtures._packet(repo, 'refinement', [
        {'provider': provider, 'mission': 'semantic_core'} for provider in ('codex', 'claude')])
    args = fixtures._args(repo, policy, packet, template, 'refinement')
    code, receipt = RUNNER.run_reviewers(args)
    assert code == 0
    receipt_path = packet.parent / 'reviewer-receipt.yaml'
    return args, receipt_path, yaml.safe_load(receipt_path.read_text()), state


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'unsupported schema'), (['workflow'], 'audit', 'workflow'),
    (['reviewer_profile'], 'budget', 'profile'), (['reviewer_set'], 'expanded', 'set'),
    (['packet_sha256'], 'changed', 'packet changed'), (['template_sha256'], 'changed', 'template changed'),
    (['assignment_manifest_sha256'], 'changed', 'assignments changed'),
    (['git_identity', 'unchanged'], False, 'identity changed'),
])
def test_review_resume_rejects_tampered_receipts_before_relaunch(completed_review, path, value, message):
    args, receipt_path, original, state = completed_review
    document = deepcopy(original)
    change(document, path, value)
    before = sum(fixtures._runtime_count(state, provider) for provider in ('claude', 'codex'))
    receipt_path.write_text(yaml.safe_dump(document))
    try:
        with pytest.raises(RUNNER.ReviewerError, match=message):
            RUNNER.run_reviewers(args)
        assert sum(fixtures._runtime_count(state, provider) for provider in ('claude', 'codex')) == before
    finally:
        receipt_path.write_text(yaml.safe_dump(original))


def test_reviewer_launch_failure_publishes_failed_receipt(tmp_path, monkeypatch):
    repo, state, policy = fixtures._environment(tmp_path)
    packet, template = fixtures._packet(repo, 'refinement', [{'provider': 'codex', 'mission': 'semantic_core'}])
    def fail_launch(*args): raise OSError('process limit reached')
    monkeypatch.setattr(RUNNER, 'launch_process', fail_launch)
    code, receipt = RUNNER.run_reviewers(fixtures._args(repo, policy, packet, template, 'refinement'))
    assert code == 1
    assert receipt['assignments'][0]['status'] == 'launch_failed'
    assert 'process limit reached' in receipt['assignments'][0]['error']
    assert fixtures._runtime_count(state, 'codex') == 0


@pytest.mark.parametrize('field,value,message', [('fingerprint', '', 'non-empty'),
    ('source_candidate_ids', [], 'empty'), ('source_candidate_ids', ['C1', 'C1'], 'duplicates'),
    ('closure_test', '', 'non-empty'), ('required_assignments', {}, 'must be a list'),
    ('required_assignments', [], 'at least one')])
def test_targeted_review_requires_named_findings_sources_and_closure(field, value, message):
    target = {'fingerprint': 'one', 'source_candidate_ids': ['C1'], 'closure_test': 'test invariant',
              'required_assignments': [{'provider': 'codex', 'mission': 'semantic_core'}]}
    target[field] = value
    with pytest.raises(RUNNER.ReviewerError, match=message): RUNNER._targeted_contracts([target])


@pytest.mark.parametrize('state,message', [({'status': 'ready', 'project_root': '/other'}, 'another working root'),
    ({'status': 'unknown'}, 'unsupported'), ({'status': 'ready', 'index_path': '/other'}, 'index path'),
    ({'status': 'ready'}, 'no executable')])
def test_review_rejects_wrong_or_unusable_codegraph_state(tmp_path, monkeypatch, state, message):
    from types import SimpleNamespace
    policy = yaml.safe_load(fixtures.CODEGRAPH_POLICY.read_text())
    run = tmp_path / 'run.yaml'
    run.write_text(yaml.safe_dump({'codegraph': state}))
    monkeypatch.setattr(RUNNER.shutil, 'which', lambda name: None)
    with pytest.raises(RUNNER.ReviewerError, match=message):
        RUNNER._codegraph_state_for_run(SimpleNamespace(run=run), tmp_path, policy)


@pytest.mark.parametrize('workflow', ['refinement', 'audit'])
@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'surface'])
def test_review_candidates_require_complete_unique_structured_records(workflow, damage):
    record = fixtures.REFINEMENT_CANDIDATE if workflow == 'refinement' else fixtures.AUDIT_CANDIDATE
    heading = 'Findings' if workflow == 'refinement' else 'Finding Candidates'
    if damage == 'missing': record = record.replace('- closure_test:', '- ignored_field:')
    if damage == 'duplicate': record += '\n' + record
    if damage == 'surface':
        record = record.replace('requires_user: false', 'requires_user: maybe') if workflow == 'refinement' else record.replace('affected_files: [src/main.py]', 'affected_files: []')
    with pytest.raises(RUNNER.ReviewerError):
        RUNNER.extract_candidates(f'## {heading}\n{record}', RUNNER.Assignment('codex', 'semantic_core'))


def test_review_output_does_not_accept_unreadable_or_malformed_validation_policy(tmp_path):
    policy = RUNNER.load_policy(fixtures.POLICY_PATH)
    workflow = RUNNER.workflow_policy(policy, 'refinement')
    assignment = RUNNER.Assignment('codex', 'semantic_core')
    output = tmp_path / 'output.md'
    output.write_bytes(b'\xff' * 1024)
    valid, reason, *_ = RUNNER._validate_output_details(output, workflow, policy, assignment, review_kind='full')
    assert valid is False and 'unreadable' in reason
    output.write_text('content' * 200)
    invalid = dict(workflow, output_validation_patterns=['['])
    with pytest.raises(RUNNER.ReviewerError, match='invalid output validation pattern'):
        RUNNER._validate_output_details(output, invalid, policy, assignment, review_kind='full')


def test_reviewer_cli_failure_is_machine_detectable(tmp_path, capsys):
    assert RUNNER.main(['run', '--workflow', 'refinement', '--repo-root', str(tmp_path),
        '--packet', str(tmp_path / 'missing.yaml')]) == 1
    assert 'Scope reviewer launch failed:' in capsys.readouterr().err


def test_quota_fallback_launch_failure_is_recorded_without_semantic_success(tmp_path, monkeypatch):
    repo, state, policy = fixtures._environment(tmp_path)
    fixtures._configure(state, 'agy', rate_primary=True)
    packet, template = fixtures._packet(repo, 'audit', [{'provider': 'agy', 'mission': 'semantic_core'}])
    launch = RUNNER.launch_process
    def fail_fallback(command, config, paths, root, model):
        if model == 'gemini-3.5-flash-high': raise OSError('fallback binary unavailable')
        return launch(command, config, paths, root, model)
    monkeypatch.setattr(RUNNER, 'launch_process', fail_fallback)
    code, receipt = RUNNER.run_reviewers(fixtures._args(repo, policy, packet, template, 'audit'))
    assert code == 1
    row = receipt['assignments'][0]
    assert row['status'] == 'launch_failed'
    assert row['fallback']['reason'] == 'rate_or_quota_exhausted_before_semantic_output'
    assert 'fallback binary unavailable' in row['error']
    assert fixtures._runtime_count(state, 'agy') == 1


@pytest.mark.parametrize('stage,failure', [('head', 'os'), ('head', 'exit'), ('tree', 'os'), ('tree', 'exit'), ('tree', 'malformed')])
def test_revision_identity_failure_never_returns_trusted_identity(tmp_path, monkeypatch, stage, failure):
    import subprocess
    def git(command, **kwargs):
        current = 'tree' if str(command[-1]).endswith('^{tree}') else 'head'
        if current == stage:
            if failure == 'os': raise OSError('Git unavailable')
            if failure == 'exit': return subprocess.CompletedProcess(command, 128, '', 'bad revision')
            return subprocess.CompletedProcess(command, 0, 'not-an-object-id', '')
        return subprocess.CompletedProcess(command, 0, 'a' * 40, '')
    monkeypatch.setattr(RUNNER.subprocess, 'run', git)
    with pytest.raises(RUNNER.ReviewerError, match='identity'):
        RUNNER._git_revision_identity(tmp_path)


@pytest.mark.parametrize('failure,message', [('version', 'no version'), ('help', 'required reviewer flags'),
    ('auth-json', 'not valid JSON'), ('auth-denied', 'not authenticated'), ('timeout', 'positive integer'),
    ('backend', 'unsupported provider backend')])
def test_reviewer_preflight_rejects_unusable_toolchain(tmp_path, monkeypatch, failure, message):
    import subprocess
    repo, state, path = fixtures._environment(tmp_path)
    policy = RUNNER.load_policy(path)
    config = RUNNER.reviewer_provider_config(policy, 'refinement', 'claude', 'default')
    if failure == 'timeout': policy['preflight_timeout_seconds'] = 0
    if failure == 'backend': config['backend'] = 'unknown'
    def capture(command, timeout, context):
        if 'version' in context: output = '' if failure == 'version' else 'fixture 1.0'
        elif 'help' in context: output = '' if failure == 'help' else ' '.join(config['required_help_flags'])
        else: output = 'invalid' if failure == 'auth-json' else '{"loggedIn": false}'
        return subprocess.CompletedProcess(command, 0, output, '')
    monkeypatch.setattr(RUNNER, '_capture', capture)
    with pytest.raises(RUNNER.ReviewerError, match=message): RUNNER.preflight_provider('claude', config, policy)

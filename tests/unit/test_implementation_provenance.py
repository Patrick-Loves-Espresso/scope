"""Runner-issued proof evidence cannot be forged, reassigned or reused after drift."""
import json
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest
import yaml

import test_scope_worker as fixtures
from test_evidence_rejection import change

RUNNER = fixtures.RUNNER


@pytest.fixture(scope='module')
def implemented(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('provenance')
    repo = fixtures._repo(tmp / 'repo')
    scope = fixtures._scope(tmp / 'scope')
    epic = repo / 'docs/epics/gd-001'
    path = epic / 'delivery-manifest.yaml'
    argv = [sys.executable, '-c', "from pathlib import Path; assert Path('src/value.txt').read_text() == 'after\\n'; print('1 passed in 0.01s')"]
    manifest = {'schema_version': 3, 'epic_id': 'gd-001',
        'stories': [{'id': 'S1', 'depends_on': [], 'proof_ids': ['P1']}],
        'proofs': [{'id': 'P1', 'level': 'unit', 'command': shlex.join(argv),
                    'execution': {'argv': argv, 'cwd': '.', 'parser': 'pytest', 'environment': [], 'fresh': False}}]}
    path.write_text(yaml.safe_dump(manifest))
    fixtures._command('git', 'add', '.', cwd=repo)
    fixtures._command('git', 'commit', '-m', 'proof contract', cwd=repo)
    with pytest.MonkeyPatch.context() as patch:
        run = fixtures._initialize(patch, repo, scope)
        job, job_path = fixtures._job(repo, scope)
        job.update(story_ids=['S1'], required_proof_ids=['P1'])
        job_path.write_text(yaml.safe_dump(job))
        fake = fixtures._fake_writer(tmp / 'author.py')
        patch.setattr(RUNNER.scope_codegraph, 'sync', lambda policy, root, prior: prior)
        patch.setattr(RUNNER, 'provider_preflight', lambda *args: {'executable': sys.executable, 'version': 'fixture'})
        patch.setattr(RUNNER, 'build_codex_command', lambda executable, selected, working_root, access, schema, output, graph:
            [sys.executable, str(fake), str(output), str(repo / 'src/value.txt'), 'src/value.txt', job['job_id']])
        assert RUNNER.run_worker(SimpleNamespace(job=job_path, role='implementation', cwd=repo,
            result=Path(job['result_path']), provider='codex', worker_profile='default', access='workspace-write')) == 0
    assert RUNNER.verify_implementation_attribution(epic, repo) == []
    evidence = epic / 'implementation-evidence.yaml'
    return repo, epic, run, job, evidence.read_text()


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 1, 'schema_version'), (['epic_id'], 'wrong', 'epic_id differs'),
    (['validated_jobs'], {}, 'must be a list'),
    (['validated_jobs', 0, 'result_sha256'], 'invalid', 'invalid result hash'),
    (['validated_jobs', 0, 'phase'], 'delivery_summary', 'must not be promoted'),
    (['attributed_delta'], [], 'does not match validated jobs'),
    (['attribution_sha256'], 'forged', 'attribution_sha256'),
    (['baseline', 'head'], '0' * 40, 'baseline HEAD'),
    (['baseline', 'tree'], '0' * 40, 'baseline tree'),
    (['stories', 0, 'status'], 'pending', 'not verified'),
    (['stories', 0, 'proofs'], {}, 'proofs are invalid'),
    (['stories', 0, 'proofs'], [], 'proof set differs'),
    (['stories', 0, 'proofs', 0, 'source_job_id'], 'other', 'provenance is invalid'),
    (['stories', 0, 'proofs', 0, 'command'], 'true', 'command differs'),
    (['stories', 0, 'proofs', 0, 'passed'], 0, 'stale or failed'),
    (['stories', 0, 'proofs', 0, 'evidence_hashes'], {}, 'hashes are missing'),
    (['stories', 0, 'proofs', 0, 'evidence_hashes'], {'README.md': 'wrong'}, 'invalid proof evidence sha256'),
    (['stories', 0, 'proofs', 0, 'evidence_hashes'], {'README.md': 'sha256:' + '0' * 64}, 'hash mismatch'),
])
def test_attribution_rejects_evidence_tampering(implemented, path, value, message):
    repo, epic, run, job, original = implemented
    evidence = epic / 'implementation-evidence.yaml'
    document = yaml.safe_load(original)
    change(document, path, value)
    evidence.write_text(yaml.safe_dump(document))
    try:
        errors = RUNNER.verify_implementation_attribution(epic, repo)
        assert any(message in error for error in errors), errors
    finally:
        evidence.write_text(original)


def test_v3_model_cannot_supply_its_own_proof_evidence(implemented):
    repo, epic, run, job, original = implemented
    schema = json.loads((Path(job['scope_root']) / 'config/worker-result.schema.json').read_text())
    result = fixtures._result(job, ['src/value.txt'])
    proof = yaml.safe_load(original)['stories'][0]['proofs'][0]
    result['payload']['proof_evidence'] = [{key: proof[key] for key in
        ('proof_id', 'command', 'exit_code', 'passed', 'failed', 'errors', 'skipped')}]
    log, digest = next(iter(proof['evidence_hashes'].items()))
    result['payload']['proof_evidence'][0].update(evidence_path=log, evidence_sha256=digest)
    with pytest.raises(RUNNER.ContractError, match='runner-owned'):
        RUNNER.validate_result(result, job, schema)


@pytest.mark.parametrize('mutation', ['source', 'proof-log', 'execution'])
def test_final_verification_rejects_drift_after_pass(implemented, mutation):
    repo, epic, run, job, original = implemented
    if mutation == 'source': target = repo / 'src/value.txt'
    elif mutation == 'proof-log':
        proof = yaml.safe_load(original)['stories'][0]['proofs'][0]
        target = repo / next(iter(proof['evidence_hashes']))
    else: target = epic / 'delivery-manifest.yaml'
    saved = target.read_bytes()
    try:
        if mutation == 'execution':
            doc = yaml.safe_load(saved)
            doc['proofs'][0]['execution']['cwd'] = '../outside'
            target.write_text(yaml.safe_dump(doc))
        else: target.write_text('tampered')
        errors = RUNNER.verify_implementation_attribution(epic, repo)
        assert any('stale' in error or 'invalid execution context' in error for error in errors), errors
    finally:
        target.write_bytes(saved)
    assert RUNNER.verify_implementation_attribution(epic, repo) == []


def test_group_cli_reports_dependency_groups(implemented, capsys):
    repo, epic, run, job, original = implemented
    assert RUNNER.main(['story-groups', str(epic), '--scope-root', job['scope_root']]) == 0
    assert json.loads(capsys.readouterr().out) == [['S1']]


@pytest.mark.parametrize('mutation,message', [('active', 'idle implement'), ('command', 'worker run path must be'),
    ('legacy', 'newly approved manifest v3'), ('handoff', 'invalid approved handoff')])
def test_proof_checkpoint_requires_idle_run_and_approved_v3_handoff(implemented, mutation, message, capsys):
    repo, epic, run, job, original = implemented
    target = epic / 'delivery-manifest.yaml' if mutation == 'legacy' else run
    saved = target.read_bytes()
    document = yaml.safe_load(saved)
    if mutation == 'active': document['active_job'] = {'job_id': 'running'}
    if mutation == 'command': document['command'] = 'audit_epic'
    if mutation == 'legacy': document['schema_version'] = 2
    target.write_text(yaml.safe_dump(document))
    try:
        assert RUNNER.main(['verify-proofs', str(epic), '--run', str(run)]) == 1
        assert message in capsys.readouterr().err
    finally: target.write_bytes(saved)


@pytest.mark.parametrize('value,message', [({'state': 'invented'}, 'state is invalid'),
    ({'state': 'present', 'kind': 'file', 'mode': True}, 'mode is invalid'),
    ({'state': 'deleted', 'sha256': 'hash'}, 'cannot have a hash'),
    ({'state': 'symlink', 'kind': 'file'}, 'wrong kind'),
    ({'state': 'present', 'kind': 'symlink'}, 'invalid kind'),
    ({'state': 'present', 'kind': 'file', 'sha256': 'bad'}, 'hash is invalid'),
    ({'state': 'present', 'kind': 'directory', 'sha256': 'bad'}, 'cannot have a hash')])
def test_attributed_file_state_cannot_misrepresent_type_mode_or_hash(value, message):
    with pytest.raises(RUNNER.ContractError, match=message): RUNNER._validate_evidence_state(value)

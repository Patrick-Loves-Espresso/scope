"""New manifest contract through real Git, proof subprocesses and artifact gates."""
from pathlib import Path
import json
import shlex
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

import test_validate_refinement as refinement
import test_scope_worker as worker
import test_audit_artifacts as audit
import test_scope_wrap_finalize as wrap


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


@pytest.mark.parametrize("closure", [None, "manifest", "predicate"])
def test_v3_refinement_implementation_audit_and_deterministic_delivery(tmp_path, monkeypatch, closure):
    repo, epic, refine_run = refinement._fixture(tmp_path)
    git = lambda *args: worker._command('git', *args, cwd=repo)
    git('config', 'user.name', 'Scope Test')
    git('config', 'user.email', 'scope@example.test')
    (repo / '.gitignore').write_text('tmp_debug/\n.codegraph/\n')
    (repo / 'src').mkdir()
    (repo / 'src/value.txt').write_text('before\n')
    (repo / 'audit-template.md').write_text('template')
    git('add', '.')
    git('commit', '-m', 'baseline')
    scope = worker._scope(tmp_path / 'installed')
    run_doc = yaml.safe_load(refine_run.read_text())
    run_doc.update(scope_root=str(scope), worker_policy_sha256=worker.RUNNER._sha256_file(scope / 'config/worker-policy.yaml'))
    dump(refine_run, run_doc)
    path = epic / 'delivery-manifest.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest.update(schema_version=3, documentation_obligations=[])
    manifest['stories'][0]['depends_on'] = []
    argv = [sys.executable, '-c', "from pathlib import Path; assert Path('src/value.txt').read_text() == 'after\\n'; print('1 passed in 0.01s')"]
    proof = manifest['proofs'][0]
    proof.update(classification='implementation_created', path='src/value.txt', command=shlex.join(argv), baseline_evidence=None,
                 execution={'argv': argv, 'cwd': '.', 'parser': 'pytest', 'environment': [], 'fresh': False})
    manifest['proofs'].append({
        'id': 'PROOF-EXT', 'classification': 'external_blocked', 'level': 'inspection',
        'blocker': 'Independent external evidence is not authorized or available.',
        'substitute': 'The executable implementation proof covers the local contract only.',
        'expected_result': 'The external claim remains insufficient_evidence until separately authorized.',
    })
    manifest['stories'][0]['proof_ids'].append('PROOF-EXT')
    dump(path, manifest)
    plan_path = epic / manifest['stories'][0]['plan_path']
    plan = yaml.safe_load(plan_path.read_text())
    plan['proof_ids'].append('PROOF-EXT')
    dump(plan_path, plan)
    refinement._approve_product(epic, refine_run)
    assert refinement.VALIDATOR.main(['baseline-proofs', str(epic), '--run', str(refine_run)]) == 0
    packet = refinement._create_packet(epic, refine_run)
    assert yaml.safe_load(packet.read_text())['replay_snapshot']['ref'].startswith('refs/scope/')
    receipt = refinement._receipt(repo, packet)
    assert refinement.VALIDATOR.main(['apply-review-receipt', str(epic), str(receipt), '--run', str(refine_run)]) == 0
    assert refinement.VALIDATOR.main(['render-summary', str(epic), '--run', str(refine_run)]) == 0
    assert refinement.VALIDATOR.main(['record-authority', str(epic), '--run', str(refine_run), '--authority-id', 'FINAL', '--gate', 'final_handoff', '--source', 'user', '--decision', 'approved']) == 0
    git('add', '.')
    git('commit', '-m', 'approved handoff')
    work = tmp_path / 'work'
    git('worktree', 'add', '-b', 'epic/E-001', str(work), 'HEAD')
    epic = work / 'docs/epics/E-001'
    monkeypatch.setattr(worker.RUNNER.scope_codegraph, 'prepare', lambda policy, root: worker._ready(root))
    monkeypatch.setattr(worker.RUNNER.scope_codegraph, 'sync', lambda policy, root, prior: prior)
    run = Path(worker.RUNNER.initialize_run(repo, work, 'E-001', 'implement', 'default', scope)['run'])
    directory = run.parent / 'jobs/group-001'
    directory.mkdir(parents=True)
    manifest_path = epic / 'delivery-manifest.yaml'
    job = {'schema_version': 2, 'job_id': 'group-001', 'command': 'implement', 'role': 'implementation', 'phase': 'story',
           'epic_id': 'E-001', 'repository_root': str(repo), 'working_root': str(work), 'scope_root': str(scope),
           'read_scope': ['.'], 'write_scope': ['src/value.txt'], 'story_ids': ['STORY-001'],
           'artifacts': [{'kind': 'manifest', 'path': manifest_path.relative_to(work).as_posix(), 'sha256': worker.RUNNER._sha256_file(manifest_path)}],
           'decision_refs': [], 'required_validations': [], 'required_proof_ids': ['PROOF-001', 'PROOF-EXT'],
           'result_path': str(directory / 'result.json'), 'implementation_evidence_path': 'docs/epics/E-001/implementation-evidence.yaml'}
    job_path = directory / 'job.yaml'
    dump(job_path, job)
    fake = worker._fake_writer(tmp_path / 'author.py')
    monkeypatch.setattr(worker.RUNNER, 'provider_preflight', lambda provider, selected: {'executable': sys.executable, 'version': 'fixture'})
    monkeypatch.setattr(worker.RUNNER, 'build_codex_command', lambda executable, selected, working_root, access, schema, output, codegraph:
                        [sys.executable, str(fake), str(output), str(work / 'src/value.txt'), 'src/value.txt', job['job_id']])
    args = SimpleNamespace(job=job_path, role='implementation', cwd=work, result=Path(job['result_path']), provider='codex', worker_profile='default', access='workspace-write')
    assert worker.RUNNER.run_worker(args) == 0
    assert json.loads(Path(job['result_path']).read_text())['payload']['proof_evidence'] == []
    implementation_evidence = yaml.safe_load((epic / 'implementation-evidence.yaml').read_text())
    assert implementation_evidence['stories'][0]['status'] == 'verified'
    assert [row['proof_id'] for row in implementation_evidence['stories'][0]['proofs']] == ['PROOF-001']
    assert implementation_evidence['stories'][0]['external_blocked_proof_ids'] == ['PROOF-EXT']
    assert worker.RUNNER.verify_implementation_attribution(epic, work) == []
    evidence_path = epic / 'implementation-evidence.yaml'
    evidence_bytes = evidence_path.read_bytes()
    implementation_evidence['stories'][0]['external_blocked_proof_ids'] = []
    dump(evidence_path, implementation_evidence)
    assert any('external-blocked proof set differs' in error for error in worker.RUNNER.verify_implementation_attribution(epic, work))
    evidence_path.write_bytes(evidence_bytes)
    assert worker.RUNNER.main(['verify-proofs', str(epic), '--run', str(run)]) == 0
    audit_run = Path(worker.RUNNER.initialize_run(repo, work, 'E-001', 'audit_epic', 'default', scope)['run'])
    attempt = audit._prepare(epic, audit_run)
    prepared = yaml.safe_load((attempt / 'audit-attempt.yaml').read_text())
    assert [row['id'] for row in prepared['gates']] == ['PROOF-001']
    assert audit.AUDIT.main(['execute-gates', str(epic), str(attempt), '--run', str(audit_run)]) == 0
    attempt_doc = yaml.safe_load((attempt / 'audit-attempt.yaml').read_text())
    assert attempt_doc['gates'][0]['result']['reused'] is True
    if closure:
        candidate = audit._candidate('C-1', severity='minor')
        candidate['affected_files'] = ['src/value.txt']
        candidate['closure_test'] = proof['command'] if closure == 'manifest' else 'inspect that value equals after'
        audit._receipt(work, attempt, candidates={'claude': [candidate]}, decisions={'claude': 'findings'})
    else:
        audit._receipt(work, attempt)
    assert audit.AUDIT.main(['apply-synthesis', str(epic), str(attempt), '--run', str(audit_run)]) == 0
    assert audit.AUDIT.main(['finalize', str(epic), str(attempt), '--run', str(audit_run)]) == 0
    if closure:
        assert yaml.safe_load((attempt / 'audit-attempt.yaml').read_text())['status'] == 'fail'
        ledger_path = epic / 'audit-findings.yaml'
        ledger = yaml.safe_load(ledger_path.read_text())
        finding = ledger['findings'][0]
        assert finding['affected_acceptance_ids'] == ['AC-001']
        assert finding['title'] == candidate['impact']
        finding['status'] = 'remediated_pending_verification'
        runner_check = yaml.safe_load((epic / 'implementation-evidence.yaml').read_text())['stories'][0]['proofs'][0]
        finding['remediation'] = {'source_attempt_id': 'audit-001', 'source_ids': finding['source_ids'],
            'affected_paths': ['src/value.txt'], 'affected_path_hashes': {'src/value.txt': audit._sha(work / 'src/value.txt')},
            'checks': [runner_check]}
        if closure == 'predicate':
            finding['remediation']['execution'] = proof['execution']
        dump(ledger_path, ledger)
        assert audit.AUDIT.main(['prepare', str(epic), '--run', str(audit_run), '--mode', 'targeted', '--finding', finding['id'], '--reason', 'verify correction']) == 0
        attempt = epic / 'reviews/audit-002'
        assert audit.AUDIT.main(['execute-gates', str(epic), str(attempt), '--run', str(audit_run)]) == 0
        verification = {'fingerprint': finding['fingerprint'], 'outcome': 'verified', 'evidence': 'source and proof checked',
                        'source_candidate_ids': finding['source_ids'], 'closure_test': finding['closure_test']}
        audit._receipt(work, attempt, verifications={'claude': [verification]})
        assert audit.AUDIT.main(['apply-synthesis', str(epic), str(attempt), '--run', str(audit_run)]) == 0
        assert audit.AUDIT.main(['finalize', str(epic), str(attempt), '--run', str(audit_run)]) == 0
    assert yaml.safe_load((attempt / 'audit-attempt.yaml').read_text())['status'] == 'pass'
    rendered = wrap.FINALIZER.render_summary(wrap._arguments('render-summary', epic, run))
    assert rendered['method'] == 'deterministic-v1'
    assert 'PROOF-EXT' in (epic / 'implementation-summary.md').read_text()
    seal = wrap._seal(repo, work, epic, run)
    assert seal['status'] == 'sealed'
    assert wrap._verify(work, epic)['status'] == 'verified'
    (epic / 'implementation-summary.md').write_text('forged summary')
    with pytest.raises(wrap.FINALIZER.WrapError):
        wrap._verify(work, epic)

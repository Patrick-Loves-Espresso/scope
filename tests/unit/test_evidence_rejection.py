"""Reject corrupted handoffs and receipts, rather than accepting plausible PASS text."""
from copy import deepcopy

import pytest
import yaml

import test_audit_artifacts as audit
import test_validate_refinement as refine


def change(document, path, value):
    selected = document
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = deepcopy(value)


@pytest.mark.parametrize('workflow', ['refinement', 'audit'])
@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 999, 'schema_version'),
    (['workflow'], 'other', 'workflow'),
    (['status'], 'invented', 'status'),
    (['packet_sha256'], 'sha256:' + '0' * 64, 'packet_sha256'),
    (['packet_path'], '../escape', 'relative'),
    (['template_sha256'], 'sha256:' + '0' * 64, 'template_sha256'),
    (['template_path'], 'missing-template.md', 'missing'),
    (['git_identity', 'unchanged'], False, 'identity'),
    (['assignment_manifest_sha256'], 'sha256:' + '0' * 64, 'manifest'),
    (['assignments', 0, 'status'], 'invented', 'status'),
    (['assignments', 0, 'questions'], 'not a list', 'questions'),
    (['assignments', 0, 'paths'], None, 'paths'),
    (['assignments', 0, 'paths', 'metadata'], 'sidecar.json', 'metadata'),
    (['assignments', 0, 'paths', 'output'], 'missing-output.md', 'missing'),
    (['assignments', 0, 'output_sha256'], 'sha256:' + '0' * 64, 'output_sha256'),
    (['assignments', 0, 'candidates'], ['not a mapping'], 'candidates'),
    (['assignments', 0, 'targeted_verifications'], 'not a list', 'targeted_verifications'),
    (['assignments'], [], 'assignments'),
])
def test_receipt_tampering_is_rejected(tmp_path, workflow, path, value, message):
    fixture = refine if workflow == 'refinement' else audit
    module = refine.VALIDATOR if workflow == 'refinement' else audit.AUDIT
    repo, epic, run = fixture._fixture(tmp_path)
    if workflow == 'refinement':
        refine._approve_product(epic, run)
        receipt = refine._receipt(repo, refine._create_packet(epic, run))
        policy = module.RefinementValidator(epic, 'review', repo_root=repo).policy
    else:
        receipt = audit._receipt(repo, audit._prepare(epic, run))
        policy = module.AuditValidator(epic, receipt.parent, 'final', repo_root=repo).policy
    assert module._verify_receipt(epic, receipt, repo, policy)[0] == []
    document = yaml.safe_load(receipt.read_text())
    change(document, path, value)
    receipt.write_text(yaml.safe_dump(document))
    errors, *_, complete = module._verify_receipt(epic, receipt, repo, policy)
    assert any(message in error for error in errors), errors
    assert complete is False


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 999, 'schema_version'), (['epic_id'], 'OTHER', 'epic_id differ'),
    (['risk_level'], 'unbounded', 'risk_level'), (['author_provider'], 'unknown', 'author_provider'),
    (['capabilities'], ['telepathy'], 'unknown values'), (['acceptance_ids'], [], 'cannot be empty'),
    (['acceptance_ids'], ['AC-MISSING'], 'absent from acceptance'),
    (['dependencies'], [{'epic_id': 'E-000', 'commit': 'abc'}], 'full 40- or 64-hex'),
    (['dependencies'], [{'epic_id': 'E-000', 'commit': 'a' * 40}] * 2, 'duplicate epic_id'),
    (['artifact_ownership', 0, 'path'], '../escape', 'relative'),
    (['artifact_ownership', 0, 'authority'], 'unknown', 'authority'),
    (['decisions'], [{'id': 'D1', 'statement': 'Pick one', 'status': 'unknown'}], 'status'),
    (['decisions'], [{'id': 'D1', 'statement': 'Pick one', 'status': 'pending', 'authority_id': 'forged'}], 'must be null'),
    (['decisions'], [{'id': 'D1', 'statement': 'Pick one', 'status': 'decided', 'authority_id': 'forged'}], 'product_decision'),
    (['proofs', 0, 'classification'], 'invented', 'classification'),
    (['proofs', 0, 'level'], 'invented', 'level'),
    (['proofs', 0, 'command'], 'different command', 'differs from planned command'),
    (['stories', 0, 'acceptance_ids'], ['UNKNOWN'], 'unknown acceptance'),
    (['stories', 0, 'proof_ids'], ['UNKNOWN'], 'unknown proof'),
    (['stories', 0, 'plan_path'], 'missing.yaml', 'must name file-plan-story'),
    (['stories', 0, 'id'], 'OTHER', 'epic_id/story_id'),
])
def test_manifest_rejects_inconsistent_contracts(tmp_path, path, value, message):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    manifest = epic / 'delivery-manifest.yaml'
    document = yaml.safe_load(manifest.read_text())
    change(document, path, value)
    manifest.write_text(yaml.safe_dump(document))
    errors = refine.VALIDATOR.RefinementValidator(epic, 'review', repo_root=repo, review_ready_only=True).validate()
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('module', [refine.VALIDATOR, audit.AUDIT], ids=['refinement', 'audit'])
@pytest.mark.parametrize('updates,message', [({'outcome': 'invented'}, 'outcome'),
    ({'passed': True}, 'integer'), ({'failed': -1}, 'integer'),
    ({'skipped': 1}, 'skip_reason'), ({'outcome': 'fail'}, 'FAIL'),
    ({'outcome': 'blocked'}, 'strict PASS'), ({'passed': 0}, 'PASS'),
    ({'evidence_hashes': None}, 'mapping'),
    ({'evidence_hashes': {'../escape': 'sha256:' + 'a' * 64}}, 'relative'),
    ({'evidence_hashes': {'tmp_debug/log': 'sha256:' + 'a' * 64}}, 'tmp_debug'),
    ({'evidence_hashes': {'log.txt': 'not a hash'}}, 'sha256'),
    ({'evidence_hashes': {'missing.txt': 'sha256:' + 'a' * 64}}, 'missing'),
    ({'evidence_hashes': {'log.txt': 'sha256:' + 'a' * 64}}, 'hash mismatch')])
def test_execution_evidence_rejects_false_passes_and_unbound_logs(tmp_path, module, updates, message):
    log = tmp_path / 'log.txt'
    log.write_text('1 passed')
    row = {'command': 'pytest', 'summary': '1 passed', 'outcome': 'pass', 'exit_code': 0,
           'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0, 'evidence_hashes': {'log.txt': refine._sha(log)}}
    assert module._execution_errors(row, tmp_path, 'proof', require_pass=True) == []
    row.update(updates)
    errors = module._execution_errors(row, tmp_path, 'proof', require_pass=True)
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('artifact', ['delivery-manifest.yaml', 'refinement-state.yaml', 'refinement-findings.yaml'])
def test_refinement_reports_malformed_yaml_without_traceback(tmp_path, artifact):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    (epic / artifact).write_text('[invalid: yaml')
    errors = refine.VALIDATOR.RefinementValidator(epic, 'review', repo_root=repo).validate()
    assert any('invalid' in error for error in errors)


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'), (['epic_id'], 'OTHER', 'epic_id'),
    (['findings', 0, 'id'], 'bad', 'id does not match'),
    (['findings', 0, 'severity'], 'unknown', 'severity'),
    (['findings', 0, 'category'], 'unknown', 'category'),
    (['findings', 0, 'status'], 'unknown', 'status'),
    (['findings', 0, 'source_candidate_ids'], [], 'cannot be empty'),
    (['findings', 0, 'resolution'], None, 'resolution is required'),
    (['findings', 0, 'resolution', 'affected_paths'], [], 'paths and hashes differ'),
    (['findings', 0, 'resolution', 'source_candidate_ids'], ['unknown'], 'unknown source'),
    (['findings', 0, 'resolution', 'checks'], [], 'checks cannot be empty'),
    (['findings', 0, 'status'], 'verified', 'verification is required'),
    (['findings', 0, 'status'], 'deferred', 'only minor findings'),
    (['findings', 0, 'status'], 'accepted_risk', 'accepted-risk authority'),
    (['findings', 0, 'status'], 'open', 'must be null while open'),
])
def test_refinement_findings_cannot_claim_unearned_closure(tmp_path, path, value, message):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    doc = {'schema_version': 1, 'epic_id': 'E-001', 'findings': [refine._corrected_finding(repo, epic)]}
    target = epic / 'refinement-findings.yaml'
    target.write_text(yaml.safe_dump(doc))
    validator = refine.VALIDATOR.RefinementValidator(epic, 'product', repo_root=repo)
    assert validator.validate() == []
    change(doc, path, value)
    target.write_text(yaml.safe_dump(doc))
    errors = validator.validate()
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'), (['attempt_id'], 'wrong', 'ID does not match'),
    (['mode'], 'unknown', 'mode is invalid'), (['status'], 'unknown', 'status is invalid'),
    (['gates', 0, 'status'], 'unknown', 'status is invalid'),
    (['gates', 0, 'status'], 'not_applicable', 'lacks current authority'),
    (['gates', 0, 'status'], 'blocked', 'blocked requires reason'),
    (['gates', 0, 'status'], 'pass', 'must be a mapping'),
])
def test_audit_attempt_cannot_claim_unsupported_gate_state(tmp_path, path, value, message):
    repo, epic, run = audit._fixture(tmp_path)
    attempt = audit._prepare(epic, run)
    target = attempt / 'audit-attempt.yaml'
    doc = yaml.safe_load(target.read_text())
    validator = audit.AUDIT.AuditValidator(epic, attempt, 'pre_review', repo_root=repo)
    assert validator.validate() == []
    change(doc, path, value)
    if value == 'blocked': doc['gates'][0].pop('reason', None)
    target.write_text(yaml.safe_dump(doc))
    errors = validator.validate()
    assert any(message in error for error in errors), errors


@pytest.fixture(scope='module')
def completed_audit(tmp_path_factory):
    repo, epic, run = audit._fixture(tmp_path_factory.mktemp('audit'))
    attempt = audit._prepare(epic, run)
    assert audit._record_pass(epic, attempt, run, epic / 'proof.txt') == 0
    audit._receipt(repo, attempt)
    assert audit.AUDIT.main(['apply-synthesis', str(epic), str(attempt), '--run', str(run)]) == 0
    assert audit.AUDIT.main(['finalize', str(epic), str(attempt), '--run', str(run)]) == 0
    target = attempt / 'audit-attempt.yaml'
    return repo, epic, attempt, target.read_text()


@pytest.mark.parametrize('path,value,message', [
    (['review', 'receipt_sha256'], 'wrong', 'receipt hash mismatch'),
    (['gates', 0, 'result', 'outcome'], 'fail', 'outcome differs'),
    (['gates', 0, 'status'], 'pending', 'still pending'),
    (['synthesis'], None, 'requires synthesis metadata'),
    (['synthesis', 'method'], 'model', 'deterministic-v1'),
    (['synthesis', 'sources_sha256'], 'wrong', 'sources_sha256'),
    (['decision', 'outcome'], 'fail', 'mechanically required'),
    (['report'], None, 'report must be a mapping'),
    (['report', 'sha256'], 'wrong', 'report hash mismatch'),
    (['report', 'path'], 'missing-report.md', 'missing'),
])
def test_completed_audit_rejects_tampered_decision_and_report(completed_audit, path, value, message):
    repo, epic, attempt, original = completed_audit
    target = attempt / 'audit-attempt.yaml'
    validator = audit.AUDIT.AuditValidator(epic, attempt, 'complete', repo_root=repo)
    assert validator.validate() == []
    doc = yaml.safe_load(original)
    change(doc, path, value)
    target.write_text(yaml.safe_dump(doc))
    try:
        errors = validator.validate()
        assert any(message in error for error in errors), errors
    finally: target.write_text(original)


@pytest.mark.parametrize('module', [refine.VALIDATOR, audit.AUDIT], ids=['refinement', 'audit'])
@pytest.mark.parametrize('damage,message', [('relative', 'absolute'), ('file', 'directory'),
    ('no-policy', 'no worker policy'), ('policy-hash', 'policy changed'), ('profile', 'invalid worker_profile')])
def test_scope_binding_rejects_wrong_install_or_policy(tmp_path, module, damage, message):
    import test_scope_worker as worker
    scope = worker._scope(tmp_path / 'scope')
    run = {'scope_root': str(scope), 'worker_policy_sha256': worker.RUNNER._sha256_file(scope / 'config/worker-policy.yaml'),
           'worker_profile': 'default'}
    if damage == 'relative': run['scope_root'] = 'relative'
    if damage == 'file': run['scope_root'] = str(scope / 'config/worker-policy.yaml')
    if damage == 'no-policy': run['scope_root'] = str(tmp_path)
    if damage == 'policy-hash': run['worker_policy_sha256'] = 'sha256:' + '0' * 64
    if damage == 'profile': run['worker_profile'] = 'unknown'
    with pytest.raises(ValueError, match=message): module._validate_scope_binding(run)


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'), (['status'], 'unknown', 'status'),
    (['user_decisions', 0, 'kind'], 'invented', 'kind'),
    (['user_decisions', 0, 'source'], 'model', 'source'),
    (['user_decisions', 0, 'scope'], {}, 'current epic'),
    (['user_decisions', 0, 'boundary_sha256'], 'wrong', 'boundary_sha256'),
    (['active_findings'], {}, 'must reference'),
    (['status'], 'approved', 'requires a final_handoff'),
    (['completed_review_ids'], ['refine-absent'], 'completed review does not exist'),
])
def test_refinement_state_rejects_forged_authority(tmp_path, path, value, message):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    target = epic / 'refinement-state.yaml'
    document = yaml.safe_load(target.read_text())
    change(document, path, value)
    target.write_text(yaml.safe_dump(document))
    errors = refine.VALIDATOR.RefinementValidator(epic, 'product', repo_root=repo).validate()
    assert any(message in error for error in errors), errors


def test_audit_gate_blocking_is_idempotent_and_cannot_be_rewritten(tmp_path):
    repo, epic, run = audit._fixture(tmp_path)
    attempt = audit._prepare(epic, run)
    gate = yaml.safe_load((attempt / 'audit-attempt.yaml').read_text())['gates'][0]['id']
    args = ['record-gate', str(epic), str(attempt), '--run', str(run), '--gate', gate, '--status', 'blocked', '--summary', 'unavailable']
    assert audit.AUDIT.main(args) == 1
    assert audit.AUDIT.main([*args, '--reason', 'service offline', '--passed', '1']) == 1
    assert audit.AUDIT.main([*args, '--reason', 'service offline']) == 0
    assert audit.AUDIT.main([*args, '--reason', 'service offline']) == 0
    assert audit._record_pass(epic, attempt, run, epic / 'proof.txt') == 1


def test_audit_authority_is_idempotent_but_cannot_be_reassigned(tmp_path):
    repo, epic, run = audit._fixture(tmp_path)
    attempt = audit._prepare(epic, run)
    args = ['record-authority', str(epic), str(attempt), '--run', str(run), '--authority-id', 'AUTH-RISK',
            '--kind', 'accepted_risk', '--subject', 'finding-one', '--source', 'user']
    assert audit.AUDIT.main(args) == 0
    original = (attempt / 'audit-attempt.yaml').read_bytes()
    assert audit.AUDIT.main(args) == 0
    assert (attempt / 'audit-attempt.yaml').read_bytes() == original
    args[args.index('--subject') + 1] = 'finding-two'
    assert audit.AUDIT.main(args) == 1
    assert (attempt / 'audit-attempt.yaml').read_bytes() == original


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema_version'), (['workflow'], 'audit', 'workflow'),
    (['review_id'], 'wrong', 'review_id'), (['review_kind'], 'unknown', 'review_kind'),
    (['review_kind'], 'targeted', 'must contain target_findings'),
    (['assignments'], [], 'non-empty'),
    (['assignments', 0, 'provider'], 'other', 'unsupported provider'),
    (['assignments', 0, 'mission'], 'other', 'unsupported mission'),
    (['target_findings'], [{'fingerprint': 'one'}], 'must not contain target_findings'),
    (['boundary_sha256'], 'wrong', 'boundary_sha256'),
])
def test_refinement_packet_rejects_wrong_review_boundary(tmp_path, path, value, message):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    packet = refine._create_packet(epic, run)
    document = yaml.safe_load(packet.read_text())
    change(document, path, value)
    packet.write_text(yaml.safe_dump(document))
    policy = refine.VALIDATOR.RefinementValidator(epic, 'review', repo_root=repo).policy
    errors, _ = refine.VALIDATOR._verify_packet(packet, epic, repo, policy)
    assert any(message in error for error in errors), errors


def test_refinement_accepted_risk_authority_requires_subject_and_exact_replay(tmp_path):
    repo, epic, run = refine._fixture(tmp_path)
    refine._approve_product(epic, run)
    args = ['record-authority', str(epic), '--run', str(run), '--authority-id', 'RISK',
            '--kind', 'accepted_risk', '--source', 'user', '--decision', 'approved']
    assert refine.VALIDATOR.main(args) == 1
    args.extend(['--subject', 'one'])
    assert refine.VALIDATOR.main(args) == 0
    original = (epic / 'refinement-state.yaml').read_bytes()
    assert refine.VALIDATOR.main(args) == 0
    assert (epic / 'refinement-state.yaml').read_bytes() == original
    args[-1] = 'different-finding'
    assert refine.VALIDATOR.main(args) == 1
    assert (epic / 'refinement-state.yaml').read_bytes() == original


def test_refinement_validation_cli_reports_both_valid_and_invalid_contracts(tmp_path, capsys):
    repo, epic, run = refine._fixture(tmp_path)
    args = ['validate', str(epic), '--phase', 'product', '--repo-root', str(repo)]
    assert refine.VALIDATOR.main(args) == 0
    assert 'validation passed' in capsys.readouterr().out
    (epic / 'delivery-manifest.yaml').unlink()
    assert refine.VALIDATOR.main(args) == 1
    assert 'missing' in capsys.readouterr().err


def test_audit_evidence_cli_checks_story_identity_and_reports_fingerprint(tmp_path, capsys):
    import json
    repo, epic, run = audit._fixture(tmp_path)
    capsys.readouterr()
    args = ['verify-evidence', str(epic), '--repo-root', str(repo)]
    assert audit.AUDIT.main(args) == 0
    assert 'Implementation evidence verified' in capsys.readouterr().out
    assert audit.AUDIT.main([*args, '--story', 'missing']) == 1
    assert 'unknown or duplicate implementation story' in capsys.readouterr().err
    assert audit.AUDIT.main(['fingerprint', str(epic), '--repo-root', str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)['workspace_sha256'].startswith('sha256:')


@pytest.mark.parametrize('damage,message', [('gates', 'all audit gates'), ('review', 'review must be a mapping'),
    ('packet-hash', 'packet hash mismatch'), ('targets', 'target findings differ'),
    ('missing-receipt', 'reviewer receipt is missing'), ('receipt', 'invalid audit reviewer receipt'),
    ('findings', 'findings list')])
def test_synthesis_rejects_incomplete_inputs_without_publishing(tmp_path, damage, message, capsys):
    repo, epic, run = audit._fixture(tmp_path)
    attempt = audit._prepare(epic, run)
    if damage != 'gates': assert audit._record_pass(epic, attempt, run, epic / 'proof.txt') == 0
    receipt = audit._receipt(repo, attempt)
    target = attempt / 'audit-attempt.yaml'
    doc = yaml.safe_load(target.read_text())
    if damage == 'review': doc['review'] = None
    if damage == 'packet-hash': doc['review']['packet_sha256'] = 'wrong'
    if damage == 'targets': doc['target_findings'] = [{'fingerprint': 'unknown'}]
    if damage == 'missing-receipt': receipt.unlink()
    if damage == 'receipt':
        document = yaml.safe_load(receipt.read_text()); document['schema_version'] = 99
        receipt.write_text(yaml.safe_dump(document))
    ledger = epic / 'audit-findings.yaml'
    if damage == 'findings':
        document = yaml.safe_load(ledger.read_text()); document['findings'] = {}
        ledger.write_text(yaml.safe_dump(document))
    target.write_text(yaml.safe_dump(doc))
    before = (target.read_bytes(), ledger.read_bytes())
    assert audit.AUDIT.main(['apply-synthesis', str(epic), str(attempt), '--run', str(run)]) == 1
    assert message in capsys.readouterr().err
    assert (target.read_bytes(), ledger.read_bytes()) == before


@pytest.mark.parametrize('field,value,message', [('provider', '', 'requires provider'),
    ('severity', 'invented', 'severity'), ('category', 'invented', 'category'),
    ('disposition', 'accepted_risk', 'reviewer disposition'), ('fingerprint', '', 'fingerprint'),
    ('closure_test', '', 'closure_test'), ('evidence', None, 'evidence'), ('affected_files', 'src', 'affected paths')])
def test_audit_candidate_cannot_drop_closure_or_self_authorize_risk(field, value, message):
    candidate = audit._candidate('C-1')
    candidate.update(provider='codex', mission='semantic_core')
    candidate[field] = value
    policy = audit.AUDIT._policy(audit.AUDIT._default_policy_path())
    with pytest.raises(ValueError, match=message): audit.AUDIT._source_from_candidate('audit-001', candidate, policy)


@pytest.mark.parametrize('field,value,message', [('id', 'bad', 'id does not match'),
    ('severity', 'invented', 'severity'), ('category', 'invented', 'category'),
    ('disposition', 'invented', 'disposition'), ('status', 'invented', 'status'),
    ('status', 'accepted_risk', 'lacks current hash-bound authority'),
    ('status', 'verified', 'verification is required')])
def test_audit_findings_reject_unsupported_or_unverified_closure(tmp_path, field, value, message):
    repo, epic, run = audit._fixture(tmp_path)
    attempt = audit._prepare(epic, run)
    finding = audit._remediated_audit_finding(repo, epic)
    finding[field] = value
    path = epic / 'audit-findings.yaml'
    document = yaml.safe_load(path.read_text())
    document['findings'] = [finding]
    path.write_text(yaml.safe_dump(document))
    errors = audit.AUDIT.AuditValidator(epic, attempt, 'pre_review', repo_root=repo).validate()
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('field,value,message', [('schema_version', 99, 'schema_version'),
    ('epic_id', 'OTHER', 'epic_id'), ('stories', [], 'unknown or duplicate')])
def test_audit_rejects_wrong_implementation_evidence_identity(tmp_path, field, value, message):
    repo, epic, run = audit._fixture(tmp_path)
    path = epic / 'implementation-evidence.yaml'
    document = yaml.safe_load(path.read_text())
    story = document['stories'][0]['story_id']
    document[field] = value
    path.write_text(yaml.safe_dump(document))
    policy = audit.AUDIT._policy(audit.AUDIT._default_policy_path())
    errors, _, _ = audit.AUDIT.verify_implementation_evidence(epic, repo, policy, story)
    assert any(message in error for error in errors), errors


@pytest.mark.parametrize('passes', [True, False])
def test_baseline_executor_records_actual_existing_proof_outcome(tmp_path, passes):
    import shlex
    import shutil
    import sys
    from pathlib import Path
    repo, epic, run = refine._fixture(tmp_path)
    scope = Path(yaml.safe_load(run.read_text())['scope_root'])
    shutil.copy2(refine.SCRIPT.parent.parent / 'config/execution-policy.yaml', scope / 'config/execution-policy.yaml')
    (repo / '.gitignore').write_text('tmp_debug/\n')
    refine._git(repo, 'config', 'user.name', 'Scope Test')
    refine._git(repo, 'config', 'user.email', 'scope@example.test')
    refine._git(repo, 'add', '.')
    refine._git(repo, 'commit', '-m', 'baseline inputs')
    path = epic / 'delivery-manifest.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['schema_version'] = 3
    argv = [sys.executable, '-c', f"print('1 {'passed' if passes else 'failed'} in 0.01s')"]
    proof = manifest['proofs'][0]
    proof.update(command=shlex.join(argv), execution={'argv': argv, 'cwd': '.', 'parser': 'pytest', 'environment': [], 'fresh': False})
    path.write_text(yaml.safe_dump(manifest))
    assert refine.VALIDATOR.main(['baseline-proofs', str(epic), '--run', str(run)]) == (0 if passes else 1)
    result = yaml.safe_load(path.read_text())['proofs'][0]['baseline_evidence']
    assert result['outcome'] == ('pass' if passes else 'fail')
    assert result['passed'] == int(passes) and result['failed'] == int(not passes)
    for relative, digest in result['evidence_hashes'].items(): assert refine._sha(repo / relative) == digest


def test_refinement_execution_and_summary_reject_unapproved_legacy_handoff(tmp_path, capsys):
    repo, epic, run = refine._fixture(tmp_path)
    assert refine.VALIDATOR.main(['baseline-proofs', str(epic), '--run', str(run)]) == 1
    assert 'new manifest v3' in capsys.readouterr().err
    assert refine.VALIDATOR.main(['render-summary', str(epic), '--run', str(run)]) == 1
    assert 'not ready for summary' in capsys.readouterr().err
    assert not (epic / 'refinement-review.md').exists()

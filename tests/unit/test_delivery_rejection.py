"""Exercise delivery CLI boundaries and reject tampered seals before mutation."""
from copy import deepcopy
import json

import pytest
import yaml

import test_scope_wrap_finalize as wrap
import test_scope_dependency_merge as dependency
from test_evidence_rejection import change


@pytest.fixture(scope='module')
def valid_seal(tmp_path_factory):
    main, work, epic, run = wrap._fixture(tmp_path_factory.mktemp('seal'))
    # The existing wrap fixture supplies its audit boundary; this test targets seal validation.
    with pytest.MonkeyPatch.context() as patch:
        from types import SimpleNamespace
        patch.setattr(wrap.FINALIZER, '_audit_module', lambda: SimpleNamespace(AuditValidator=wrap._PassingValidator))
        wrap._seal(main, work, epic, run)
        seal = yaml.safe_load((epic / 'delivery-seal.yaml').read_text())
    return seal, wrap.FINALIZER._policy(wrap.POLICY)


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 99, 'schema version'), (['epic_id'], '../escape', 'epic_id'),
    (['active_epic_path'], None, 'epic paths'),
    (['implemented_epic_path'], 'docs/epics/_implemented/OTHER', 'canonical'),
    (['audit', 'attempt_path'], None, 'attempt path'), (['audit', 'attempt_id'], None, 'attempt ID'),
    (['audit', 'attempt_sha256'], 'fake', 'sha256'),
    (['summary', 'path'], 'other.md', 'summary path'),
    (['summary_job', 'job_id'], '', 'job ID'),
    (['workspace', 'head'], 'short', 'head'), (['workspace', 'tree'], 'short', 'tree'),
    (['workspace', 'changes'], None, 'list'),
    (['workspace', 'changes', 0, 'path'], None, 'path'),
    (['workspace', 'changes', 0, 'old_path'], None, 'old path'),
    (['workspace', 'changes', 0, 'status'], 'long', 'status'),
    (['workspace', 'changes', 0, 'mode'], '777', 'mode'),
    (['workspace', 'workspace_sha256'], 'sha256:' + '0' * 64, 'workspace hash'),
])
def test_tampered_delivery_seals_are_rejected(valid_seal, path, value, message):
    seal, policy = deepcopy(valid_seal)
    wrap.FINALIZER._validate_seal_shape(seal, policy)
    change(seal, path, value)
    with pytest.raises(wrap.FINALIZER.WrapError, match=message):
        wrap.FINALIZER._validate_seal_shape(seal, policy)


def test_deterministic_seal_provenance_must_bind_summary(valid_seal):
    seal, policy = deepcopy(valid_seal)
    seal['summary_job'] = {'method': 'deterministic-v1', 'summary_sha256': seal['summary']['sha256'],
                           'inputs_sha256': 'sha256:' + 'a' * 64}
    wrap.FINALIZER._validate_seal_shape(seal, policy)
    seal['summary_job']['summary_sha256'] = 'sha256:' + '0' * 64
    with pytest.raises(wrap.FINALIZER.WrapError, match='does not bind summary'):
        wrap.FINALIZER._validate_seal_shape(seal, policy)


def test_wrap_cli_reports_missing_seal_as_failure(tmp_path, capsys):
    assert wrap.FINALIZER.main(['verify', str(tmp_path), '--repo-root', str(tmp_path)]) == 1
    assert 'Scope wrap finalization failed:' in capsys.readouterr().err


def test_wrap_cli_seal_and_verify_real_worktree(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(wrap.FINALIZER, '_audit_module', lambda: SimpleNamespace(AuditValidator=wrap._PassingValidator))
    main, work, epic, run = wrap._fixture(tmp_path)
    assert wrap.FINALIZER.main(['seal', str(epic), '--run', str(run)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'sealed'
    assert wrap.FINALIZER.main(['verify', str(epic), '--repo-root', str(work)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'verified'


@pytest.mark.parametrize('updates,message', [({'schema_version': 1}, 'lean implement run'),
    ({'active_job': {'job_id': 'running'}}, 'worker is active'), ({'completed_jobs': [{}]}, 'precede'),
    ({'working_root': None}, 'roots'), ({'epic_id': 'OTHER'}, 'canonical')])
def test_dependency_merge_rejects_invalid_run_before_git_mutation(tmp_path, updates, message):
    main, work, epic, run, commit = dependency._fixture(tmp_path)
    before = dependency._git(work, 'rev-parse', 'HEAD')
    doc = yaml.safe_load(run.read_text())
    doc.update(updates)
    run.write_text(yaml.safe_dump(doc))
    with pytest.raises(dependency.MERGE.MergeError, match=message):
        dependency.MERGE.merge_dependency(run, epic, 'E-000', commit)
    assert dependency._git(work, 'rev-parse', 'HEAD') == before


def test_dependency_cli_merges_pinned_commit_and_is_idempotent(tmp_path, capsys):
    main, work, epic, run, commit = dependency._fixture(tmp_path)
    args = ['--run', str(run), '--epic-dir', str(epic), '--dependency-epic-id', 'E-000', '--dependency-commit', commit]
    assert dependency.MERGE.main(args) == 0
    assert capsys.readouterr().out.strip() == dependency._git(work, 'rev-parse', 'HEAD')
    assert dependency.MERGE.main(args) == 0
    assert capsys.readouterr().out.strip() == 'already_integrated'
    args[-1] = 'not-a-commit'
    assert dependency.MERGE.main(args) == 1
    assert 'full object ID' in capsys.readouterr().err

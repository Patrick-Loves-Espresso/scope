"""Malformed durable records and inaccessible files fail with actionable errors."""
import pytest
import yaml

import test_scope_worker as worker
import test_scope_wrap_finalize as wrap
import test_scope_reviewer as reviewer
import test_validate_refinement as refine
import test_audit_artifacts as audit
from test_evidence_rejection import change


@pytest.mark.parametrize('load,error', [
    (worker.RUNNER.load_yaml, worker.RUNNER.WorkerError),
    (wrap.FINALIZER._load_yaml, wrap.FINALIZER.WrapError),
    (reviewer.RUNNER.load_yaml, reviewer.RUNNER.ReviewerError),
    (refine.VALIDATOR._load_yaml, ValueError), (audit.AUDIT._load_yaml, ValueError),
])
@pytest.mark.parametrize('content', [None, '[invalid: yaml', '- a list', ''])
def test_durable_yaml_requires_readable_mapping(tmp_path, load, error, content):
    path = tmp_path / 'record.yaml'
    if content is not None: path.write_text(content)
    with pytest.raises(error, match='record|mapping'): load(path, 'record')


@pytest.mark.parametrize('load,error', [
    (worker.RUNNER.load_json, worker.RUNNER.WorkerError),
    (wrap.FINALIZER._load_json, wrap.FINALIZER.WrapError),
])
@pytest.mark.parametrize('content', [None, '{invalid', '[]'])
def test_durable_json_requires_readable_object(tmp_path, load, error, content):
    path = tmp_path / 'record.json'
    if content is not None: path.write_text(content)
    with pytest.raises(error, match='record|object'): load(path, 'record')


@pytest.mark.parametrize('load,error', [
    (worker.RUNNER.load_yaml, worker.RUNNER.WorkerError),
    (wrap.FINALIZER._load_yaml, wrap.FINALIZER.WrapError),
    (refine.VALIDATOR._load_yaml, ValueError), (audit.AUDIT._load_yaml, ValueError),
])
def test_duplicate_durable_yaml_keys_are_not_silently_overwritten(tmp_path, load, error):
    path = tmp_path / 'record.yaml'
    path.write_text('status: failed\nstatus: pass\n')
    with pytest.raises(error, match='duplicate .*key|duplicate key'): load(path, 'record')


@pytest.mark.parametrize('path,value,message', [
    (['schema_version'], 2, 'schema versions'), (['prepare_schema_version'], 2, 'prepare schema'),
    (['paths'], None, 'mappings'), (['paths'], {}, 'incomplete'),
    (['paths', 'seal'], None, 'string'), (['labels', 'closure'], 'no identifier', 'epic_id'),
    (['audit_attempt_pattern'], None, 'pattern'), (['worktree_branch'], 'main', 'epic_id'),
])
def test_wrap_policy_rejects_ambiguous_delivery_paths_and_labels(tmp_path, path, value, message):
    document = yaml.safe_load(wrap.POLICY.read_text())
    change(document, path, value)
    target = tmp_path / 'policy.yaml'
    target.write_text(yaml.safe_dump(document))
    with pytest.raises(wrap.FINALIZER.WrapError, match=message): wrap.FINALIZER._policy(target)


@pytest.mark.parametrize('kind', ['directory', 'file-parent'])
@pytest.mark.parametrize('value', ['', 'relative', 'missing', 'file'])
def test_worker_paths_require_resolvable_absolute_locations(tmp_path, kind, value):
    existing = tmp_path / 'file'
    existing.write_text('data')
    if value == 'missing': value = str(tmp_path / 'missing/child')
    if value == 'file': value = str(existing if kind == 'directory' else existing / 'child')
    operation = worker.RUNNER._canonical_directory if kind == 'directory' else worker.RUNNER._canonical_file_parent
    with pytest.raises(worker.RUNNER.ContractError): operation(value, 'artifact')

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "src_shared/config"
JOB_SCHEMA = json.loads((CONFIG_ROOT / "worker-job.schema.json").read_text())
RESULT_SCHEMA = json.loads((CONFIG_ROOT / "worker-result.schema.json").read_text())
HASH = "sha256:" + "a" * 64


def _errors(schema: dict, value: dict) -> list:
    return list(Draft202012Validator(schema).iter_errors(value))


def _job(role: str = "implementation") -> dict:
    phase = {"implementation": "story", "refinement": "design_handoff", "diagnostic": "investigate"}[role]
    command = "epic_refine" if role == "refinement" else "implement"
    job = {
        "schema_version": 2,
        "job_id": "gd-001-job-001",
        "command": command,
        "role": role,
        "phase": phase,
        "epic_id": "gd-001",
        "repository_root": "/repo",
        "working_root": "/repo",
        "scope_root": "/scope",
        "read_scope": ["."],
        "write_scope": [] if role in {"diagnostic"} else ["."],
        "artifacts": [{"kind": "manifest", "path": "docs/manifest.yaml", "sha256": HASH}],
        "decision_refs": [{"id": "PDR-1", "path": "docs/state.yaml", "sha256": HASH}],
        "required_validations": [] if role in {"diagnostic"} else [{"command": "pytest -q", "purpose": "proof"}],
        "required_proof_ids": [],
        "result_path": "/repo/tmp_debug/scope-runs/gd-001/implement/jobs/gd-001-job-001/result.json",
    }
    if role == "implementation":
        job["implementation_evidence_path"] = (
            "docs/epics/gd-001/implementation-evidence.yaml"
        )
    return job


def _payload(role: str) -> dict:
    if role == "refinement":
        return {"kind": role, "authored_artifacts": ["docs/design.md"], "decision_refs": ["PDR-1"]}
    if role == "implementation":
        return {"kind": role, "notes": "done", "proof_evidence": []}
    if role == "audit":
        return {"kind": role, "findings": []}
    return {"kind": role, "cause": "known", "evidence": [], "recommended_action": "retry"}


def _result(role: str = "implementation") -> dict:
    return {
        "schema_version": 2,
        "job_id": "gd-001-job-001",
        "status": "completed",
        "summary": "completed bounded work",
        "changed_paths": [] if role in {"diagnostic"} else ["src/value.py"],
        "validations": [],
        "questions": [],
        "issues": [],
        "payload": _payload(role),
    }


def test_v2_jobs_validate_for_every_role() -> None:
    for role in ("refinement", "implementation", "diagnostic"):
        assert not _errors(JOB_SCHEMA, _job(role))


def test_job_rejects_v1_and_removed_lifecycle_fields() -> None:
    job = _job()
    job["schema_version"] = 1
    job["recovery_of_job_id"] = "old"
    assert _errors(JOB_SCHEMA, job)


def test_job_accepts_root_write_scope() -> None:
    assert not _errors(JOB_SCHEMA, _job())


def test_implementation_job_requires_runner_owned_evidence_target() -> None:
    job = _job()
    job.pop("implementation_evidence_path")
    assert _errors(JOB_SCHEMA, job)


def test_job_requires_hash_bound_artifacts_and_decisions() -> None:
    for field in ("artifacts", "decision_refs"):
        job = _job()
        job[field][0].pop("sha256")
        assert _errors(JOB_SCHEMA, job)


def test_job_requires_unique_required_proof_ids() -> None:
    job = _job()
    job["required_proof_ids"] = ["P-1", "P-1"]
    assert _errors(JOB_SCHEMA, job)
    job = _job("diagnostic")
    job["required_proof_ids"] = ["P-1"]
    assert _errors(JOB_SCHEMA, job)


def test_read_only_roles_reject_writes_and_validations() -> None:
    job = _job("diagnostic")
    job["write_scope"] = ["docs"]
    job["required_validations"] = [{"command": "pytest", "purpose": "wrong"}]
    assert _errors(JOB_SCHEMA, job)


def test_v2_results_validate_for_every_role() -> None:
    for role in ("refinement", "implementation", "diagnostic"):
        assert not _errors(RESULT_SCHEMA, _result(role))


def test_result_rejects_removed_transport_fields() -> None:
    result = _result()
    result["question_discovery"] = None
    result["phase"] = "story"
    assert _errors(RESULT_SCHEMA, result)


def test_needs_user_requires_at_least_one_question() -> None:
    result = _result("diagnostic")
    result["status"] = "needs_user"
    assert _errors(RESULT_SCHEMA, result)
    result["questions"] = [{"id": "Q1", "question": "Which?", "reason": "product choice", "evidence": ["docs/state.yaml"]}]
    assert not _errors(RESULT_SCHEMA, result)
    result["questions"][0]["evidence"] = []
    assert _errors(RESULT_SCHEMA, result)


def test_blocked_and_failed_require_blocking_issue() -> None:
    for status in ("blocked", "failed"):
        result = _result("diagnostic")
        result["status"] = status
        assert _errors(RESULT_SCHEMA, result)
        result["issues"] = [{"severity": "blocking", "message": "missing input", "evidence": ["docs/state.yaml"]}]
        assert not _errors(RESULT_SCHEMA, result)


def test_counts_exist_only_in_implementation_proof_evidence() -> None:
    result = _result()
    result["payload"]["proof_evidence"] = [{
        "proof_id": "P-1", "command": "pytest -q", "exit_code": 0,
        "passed": 3, "failed": 0, "errors": 0, "skipped": 0,
        "evidence_path": "docs/evidence/p-1.txt", "evidence_sha256": HASH,
    }]
    assert not _errors(RESULT_SCHEMA, result)
    result["validations"] = [{"command": "pytest", "exit_code": 0, "summary": "ok", "passed": 3}]
    assert _errors(RESULT_SCHEMA, result)


def test_retired_audit_payload_is_rejected() -> None:
    result = _result("audit")
    finding = {
        "source_ids": ["review-1:F-1"], "fingerprint": "abc", "severity": "blocking",
        "category": "security", "disposition": "remediation_required", "title": "escape",
        "evidence": ["review output"], "affected_paths": ["src/tool.py"], "closure_test": "pytest -q",
    }
    result["payload"]["findings"] = [finding]
    assert _errors(RESULT_SCHEMA, result)
    for invalid in ("critical", "info"):
        candidate = deepcopy(result)
        candidate["payload"]["findings"][0]["severity"] = invalid
        assert _errors(RESULT_SCHEMA, candidate)
    candidate = deepcopy(result)
    candidate["payload"]["findings"][0]["disposition"] = "not_applicable"
    assert _errors(RESULT_SCHEMA, candidate)
    candidate = deepcopy(result)
    candidate["payload"]["findings"][0]["disposition"] = "false_positive"
    assert _errors(RESULT_SCHEMA, candidate)

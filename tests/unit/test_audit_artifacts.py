from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


SCRIPT = Path(__file__).parents[2] / "src_shared" / "scripts" / "audit-artifacts.py"
SPEC = importlib.util.spec_from_file_location("scope_audit_artifacts", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)

REFINEMENT_SCRIPT = SCRIPT.with_name("validate-refinement.py")
REFINEMENT_SPEC = importlib.util.spec_from_file_location(
    "scope_validate_refinement_for_audit_tests", REFINEMENT_SCRIPT
)
assert REFINEMENT_SPEC and REFINEMENT_SPEC.loader
REFINEMENT = importlib.util.module_from_spec(REFINEMENT_SPEC)
REFINEMENT_SPEC.loader.exec_module(REFINEMENT)


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _sha(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _refinement_receipt(repo: Path, packet_path: Path) -> Path:
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    template = repo / "refinement-template.md"
    template.write_text("template", encoding="utf-8")
    assignments = []
    for assignment in packet["assignments"]:
        provider = assignment["provider"]
        mission = assignment["mission"]
        output = packet_path.parent / f"refinement-review-{provider}.md"
        output.write_text(f"refinement review from {provider}\n", encoding="utf-8")
        assignments.append(
            {
                "provider": provider,
                "mission": mission,
                "status": "completed",
                "paths": {
                    "prompt": _relative(packet_path.parent / f"prompt-{provider}.md", repo),
                    "output": _relative(output, repo),
                    "log": _relative(packet_path.parent / f"log-{provider}.txt", repo),
                },
                "output_sha256": _sha(output),
                "decision": "approved",
                "questions": [],
                "candidates": [],
                "targeted_verifications": [],
            }
        )
    receipt = {
        "schema_version": 2,
        "workflow": "refinement",
        "reviewer_profile": packet["reviewer_profile"],
        "reviewer_set": packet["reviewer_set"],
        "status": "completed",
        "packet_path": _relative(packet_path, repo),
        "packet_sha256": _sha(packet_path),
        "template_path": _relative(template, repo),
        "template_sha256": _sha(template),
        "assignment_manifest_sha256": REFINEMENT._structured_sha256(
            packet["assignments"]
        ),
        "git_identity": {
            "before": {"head": "abc", "tree": "def"},
            "after": {"head": "abc", "tree": "def"},
            "unchanged": True,
        },
        "assignments": assignments,
    }
    path = packet_path.parent / "reviewer-receipt.yaml"
    _dump(path, receipt)
    return path


def _fixture(
    tmp_path: Path,
    epic_directory: str = "E-001",
    *,
    documentation_obligation: bool = False,
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "scope@example.test")
    _git(repo, "config", "user.name", "Scope Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "audit-template.md").write_text("template", encoding="utf-8")

    epic = repo / "docs" / "epics" / epic_directory
    epic.mkdir(parents=True)
    for name, text in {
        "details.md": "# Details\n",
        "acceptance-criteria.md": "# Acceptance\n\n## AC-001\n",
        "design.md": (
            "# Design\n\n### DOC-001\nUpdate the operations guide.\n"
            if documentation_obligation
            else "# Design\n"
        ),
        "refinement-review.md": "# Refinement Review\n",
        "proof.txt": "1 passed\n",
        "native-contract.yaml": "schema_version: 1\n",
    }.items():
        (epic / name).write_text(text, encoding="utf-8")
    documentation_path = repo / "docs" / "operations.md"
    if documentation_obligation:
        documentation_path.write_text("# Operations\n\nPending.\n", encoding="utf-8")
    _dump(
        epic / "file-plan-story-001.yaml",
        {
            "schema_version": 1,
            "epic_id": "E-001",
            "story_id": "STORY-001",
            "acceptance_ids": ["AC-001"],
            "proof_ids": ["PROOF-001"],
        },
    )
    ownership = [
        {"path": name, "owner": "architect", "authority": "canonical"}
        for name in (
            "details.md",
            "acceptance-criteria.md",
            "design.md",
            "delivery-manifest.yaml",
            "file-plan-story-001.yaml",
            "native-contract.yaml",
        )
    ] + [{"path": "proof.txt", "owner": "tests", "authority": "evidence"}]
    manifest = {
        "schema_version": 2 if documentation_obligation else 1,
        "epic_id": "E-001",
        "risk_level": "medium",
        "capabilities": [],
        "author_provider": "codex",
        "acceptance_ids": ["AC-001"],
        "dependencies": [],
        "artifact_ownership": ownership,
        "decisions": [],
        **(
            {
                "documentation_obligations": [
                    {
                        "id": "DOC-001",
                        "story": "STORY-001",
                        "path": "docs/operations.md",
                        "requirement_ref": "design.md DOC-001",
                    }
                ]
            }
            if documentation_obligation
            else {}
        ),
        "stories": [
            {
                "id": "STORY-001",
                "plan_path": "file-plan-story-001.yaml",
                "acceptance_ids": ["AC-001"],
                "proof_ids": ["PROOF-001"],
            }
        ],
        "proofs": [
            {
                "id": "PROOF-001",
                "classification": "implementation_created",
                "level": "unit",
                "path": "tests/unit/test_one.py",
                "command": "pytest -q tests/unit/test_one.py",
                "expected_result": "passes",
            }
        ],
    }
    _dump(epic / "delivery-manifest.yaml", manifest)
    scope_root = tmp_path / "scope-install"
    worker_policy = scope_root / "config" / "worker-policy.yaml"
    worker_policy.parent.mkdir(parents=True)
    worker_policy.write_text("schema_version: 2\n", encoding="utf-8")
    run_binding = {
        "schema_version": 2,
        "epic_id": "E-001",
        "repository_root": str(repo),
        "working_root": str(repo),
        "scope_root": str(scope_root),
        "worker_policy_sha256": _sha(worker_policy),
        "worker_profile": "default",
        "active_job": None,
        "completed_jobs": [],
    }
    refinement_run = (
        repo / "tmp_debug" / "scope-runs" / "E-001" / "epic_refine" / "run.yaml"
    )
    _dump(refinement_run, {**run_binding, "command": "epic_refine"})
    assert REFINEMENT.main([
        "record-authority", str(epic), "--run", str(refinement_run),
        "--authority-id", "AUTH-PRODUCT", "--gate", "product_contract",
        "--source", "user", "--decision", "approved",
    ]) == 0
    assert REFINEMENT.main([
        "create-review-packet", str(epic), "--run", str(refinement_run), "--kind", "full"
    ]) == 0
    refinement_packet = epic / "reviews" / "refine-001" / "review-packet.yaml"
    refinement_receipt = _refinement_receipt(repo, refinement_packet)
    assert REFINEMENT.main([
        "apply-review-receipt", str(epic), str(refinement_receipt),
        "--run", str(refinement_run),
    ]) == 0
    assert REFINEMENT.main([
        "record-authority", str(epic), "--run", str(refinement_run),
        "--authority-id", "AUTH-FINAL", "--gate", "final_handoff",
        "--source", "user", "--decision", "approved",
    ]) == 0
    _git(repo, "add", "audit-template.md", "refinement-template.md", "docs")
    _git(repo, "commit", "-q", "-m", "approved refinement")
    worker = AUDIT._worker_module()
    before = worker.capture_snapshot(repo)
    implementation = repo / "src" / "service.py"
    implementation.parent.mkdir()
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    actual_paths = ["src/service.py"]
    if documentation_obligation:
        documentation_path.write_text(
            "# Operations\n\nThe delivered service is enabled.\n", encoding="utf-8"
        )
        actual_paths.append("docs/operations.md")
    after = worker.capture_snapshot(repo)
    worker.promote_implementation_evidence(
        {
            "job_id": "implementation-story-001",
            "role": "implementation",
            "phase": "story",
            "epic_id": "E-001",
            "working_root": str(repo),
            "implementation_evidence_path": _relative(
                epic / "implementation-evidence.yaml", repo
            ),
            "artifacts": [
                {
                    "path": _relative(epic / "delivery-manifest.yaml", repo),
                    "sha256": _sha(epic / "delivery-manifest.yaml"),
                }
            ],
        },
        {
            "status": "completed",
            "payload": {
                "proof_evidence": [
                    {
                        "proof_id": "PROOF-001",
                        "command": "pytest -q tests/unit/test_one.py",
                        "exit_code": 0,
                        "passed": 1,
                        "failed": 0,
                        "errors": 0,
                        "skipped": 0,
                        "evidence_path": _relative(epic / "proof.txt", repo),
                        "evidence_sha256": _sha(epic / "proof.txt"),
                    }
                ]
            },
        },
        actual_paths,
        before,
        after,
        "sha256:" + "a" * 64,
    )
    run = repo / "tmp_debug" / "scope-runs" / "E-001" / "audit_epic" / "run.yaml"
    _dump(run, {**run_binding, "command": "audit_epic"})
    return repo, epic, run


def _prepare(epic: Path, run: Path, mode: str = "full", *extra: str) -> Path:
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", mode, *extra
    ]) == 0
    return sorted((epic / "reviews").glob("audit-*/audit-attempt.yaml"))[-1].parent


def _record_pass(epic: Path, attempt: Path, run: Path, evidence: Path, *, passed: int = 1) -> int:
    attempt_doc = yaml.safe_load((attempt / "audit-attempt.yaml").read_text(encoding="utf-8"))
    gate_id = attempt_doc["gates"][0]["id"]
    return AUDIT.main([
        "record-gate",
        str(epic),
        str(attempt),
        "--run",
        str(run),
        "--gate",
        gate_id,
        "--status",
        "pass",
        "--exit-code",
        "0",
        "--passed",
        str(passed),
        "--failed",
        "0",
        "--errors",
        "0",
        "--skipped",
        "0",
        "--summary",
        "passed",
        "--evidence",
        str(evidence),
    ])


def _receipt(
    repo: Path,
    attempt: Path,
    *,
    top_status: str = "completed",
    row_statuses: list[str] | None = None,
    candidates: dict[str, list[dict[str, object]]] | None = None,
    verifications: dict[str, list[dict[str, object]]] | None = None,
    decisions: dict[str, str] | None = None,
    questions: dict[str, list[object]] | None = None,
    unverified: dict[str, list[str]] | None = None,
) -> Path:
    packet_path = attempt / "review-packet.yaml"
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    template = repo / "audit-template.md"
    assert template.read_text(encoding="utf-8") == "template"
    statuses = row_statuses or ["completed"] * len(packet["assignments"])
    rows = []
    for assignment, status in zip(packet["assignments"], statuses):
        provider = assignment["provider"]
        mission = assignment["mission"]
        output = attempt / f"review-{provider}.md"
        output.write_text(f"audit review from {provider}\n", encoding="utf-8")
        raw_candidates = [dict(row) for row in (candidates or {}).get(provider, [])]
        for row in raw_candidates:
            row.setdefault("provider", provider)
            row.setdefault("mission", mission)
        raw_verifications = [
            dict(row) for row in (verifications or {}).get(provider, [])
        ]
        for row in raw_verifications:
            row.setdefault("provider", provider)
            row.setdefault("mission", mission)
        rows.append(
            {
                "provider": provider,
                "mission": mission,
                "status": status,
                "paths": {
                    "prompt": _relative(attempt / f"prompt-{provider}.md", repo),
                    "output": _relative(output, repo),
                    "log": _relative(attempt / f"log-{provider}.txt", repo),
                },
                "output_sha256": _sha(output),
                "decision": (decisions or {}).get(provider, "pass"),
                "questions": (questions or {}).get(provider, []),
                "unverified_evidence": (unverified or {}).get(provider, []),
                "candidates": raw_candidates,
                "targeted_verifications": raw_verifications,
            }
        )
    receipt = {
        "schema_version": 2,
        "workflow": "audit",
        "reviewer_profile": "default",
        "reviewer_set": "standard",
        "status": top_status,
        "packet_path": _relative(packet_path, repo),
        "packet_sha256": _sha(packet_path),
        "template_path": _relative(template, repo),
        "template_sha256": _sha(template),
        "assignment_manifest_sha256": AUDIT._structured_sha256(packet["assignments"]),
        "git_identity": {
            "before": {"head": "abc", "tree": "def"},
            "after": {"head": "abc", "tree": "def"},
            "unchanged": True,
        },
        "assignments": rows,
    }
    path = attempt / "reviewer-receipt.yaml"
    _dump(path, receipt)
    return path


def _candidate(
    source_id: str,
    *,
    fingerprint: str = "runtime-defect",
    severity: str = "major",
    disposition: str = "remediation_required",
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "severity": severity,
        "category": "implementation",
        "disposition": disposition,
        "fingerprint": fingerprint,
        "evidence": "src/service.py:10",
        "affected_acceptance_ids": ["AC-001"],
        "affected_files": ["src/service.py"],
        "impact": "wrong result",
        "owner": "implementation",
        "closure_test": "pytest -q tests/unit/test_one.py",
    }


def _remediated_audit_finding(repo: Path, epic: Path) -> dict[str, object]:
    source_id = "review:audit-000:codex:semantic_core:C-1"
    return {
        "id": "AF-001",
        "fingerprint": "runtime-defect",
        "first_seen_attempt": "audit-000",
        "severity": "major",
        "category": "implementation",
        "disposition": "remediation_required",
        "status": "remediated_pending_verification",
        "title": "Runtime defect",
        "evidence": ["src/service.py"],
        "affected_paths": ["src/service.py"],
        "affected_acceptance_ids": ["AC-001"],
        "closure_test": "pytest -q tests/unit/test_one.py",
        "source_ids": [source_id],
        "detected_by": ["codex"],
        "authority_ref": None,
        "remediation": {
            "source_attempt_id": "audit-000",
            "source_ids": [source_id],
            "affected_paths": [_relative(epic / "design.md", repo)],
            "affected_path_hashes": {
                _relative(epic / "design.md", repo): _sha(epic / "design.md")
            },
            "checks": [
                {
                    "command": "pytest -q tests/unit/test_one.py",
                    "outcome": "pass",
                    "exit_code": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                    "summary": "1 passed",
                    "evidence_hashes": {
                        _relative(epic / "proof.txt", repo): _sha(epic / "proof.txt")
                    },
                }
            ],
        },
    }


def test_uncommitted_implementation_evidence_is_fingerprint_bound(tmp_path: Path) -> None:
    repo, epic, _ = _fixture(tmp_path)
    policy = AUDIT._policy(AUDIT._default_policy_path())
    errors, _, _ = AUDIT.verify_implementation_evidence(epic, repo, policy)
    assert errors == []
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout


def test_prepare_creates_only_canonical_attempt_packet_and_findings(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert (attempt / "audit-attempt.yaml").is_file()
    assert (attempt / "review-packet.yaml").is_file()
    assert (epic / "audit-findings.yaml").is_file()
    assert not (attempt / "audit-verification-matrix.yaml").exists()
    assert not (attempt / "synthesis-snapshot.yaml").exists()
    doc = yaml.safe_load((attempt / "audit-attempt.yaml").read_text(encoding="utf-8"))
    assert doc["repository_fingerprint"] == AUDIT.repository_fingerprint(epic, repo)
    assert _relative(epic / "native-contract.yaml", repo) in doc["artifact_hashes"]
    assert _relative(epic / "refinement-state.yaml", repo) in doc["artifact_hashes"]


def test_documentation_obligation_is_attributed_and_bound_to_audit(
    tmp_path: Path,
) -> None:
    repo, epic, run = _fixture(tmp_path, documentation_obligation=True)
    policy = AUDIT._policy(AUDIT._default_policy_path())
    errors, _, _ = AUDIT.verify_implementation_evidence(epic, repo, policy)
    assert errors == []
    attempt = _prepare(epic, run)
    attempt_doc = yaml.safe_load(
        (attempt / "audit-attempt.yaml").read_text(encoding="utf-8")
    )
    assert attempt_doc["artifact_hashes"]["docs/operations.md"] == _sha(
        repo / "docs" / "operations.md"
    )
    (repo / "docs" / "operations.md").write_text(
        "# Operations\n\nTampered.\n", encoding="utf-8"
    )
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 1


def test_documentation_obligation_rejects_an_unattributed_target(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path, documentation_obligation=True)
    evidence_path = epic / "implementation-evidence.yaml"
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    evidence["validated_jobs"][0]["changed_paths"] = [
        row
        for row in evidence["validated_jobs"][0]["changed_paths"]
        if row["path"] != "docs/operations.md"
    ]
    evidence["attributed_delta"] = [
        row
        for row in evidence["attributed_delta"]
        if row["path"] != "docs/operations.md"
    ]
    evidence["attribution_sha256"] = AUDIT._worker_module()._attribution_hash(
        evidence
    )
    _dump(evidence_path, evidence)
    policy = AUDIT._policy(AUDIT._default_policy_path())
    errors, _, _ = AUDIT.verify_implementation_evidence(epic, repo, policy)
    assert (
        "documentation obligation target is not runner-attributed: docs/operations.md"
        in errors
    )


@pytest.mark.parametrize("mutation", ["unapproved", "stale"])
def test_prepare_requires_current_approved_refinement_handoff(
    tmp_path: Path, capsys: object, mutation: str
) -> None:
    _, epic, run = _fixture(tmp_path)
    if mutation == "unapproved":
        state_path = epic / "refinement-state.yaml"
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        state["status"] = "ready_for_final_approval"
        _dump(state_path, state)
    else:
        (epic / "design.md").write_text("# Stale Design\n", encoding="utf-8")
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "full"
    ]) == 1
    assert not list((epic / "reviews").glob("audit-*/audit-attempt.yaml"))
    assert "refinement handoff" in capsys.readouterr().err


def test_audit_boundary_drift_blocks_mutation(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    (epic / "native-contract.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 1


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_mutation_lock_rejects_symlinked_tmp_debug_without_outside_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, epic, run = _fixture(tmp_path)
    outside = tmp_path / "outside-runtime"
    (repo / "tmp_debug").rename(outside)
    os.symlink(outside, repo / "tmp_debug")
    (outside / "scope-mutation.lock").unlink(missing_ok=True)
    before = sorted(path.relative_to(outside) for path in outside.rglob("*"))

    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "full"
    ]) == 1

    assert "symlink" in capsys.readouterr().err
    assert sorted(path.relative_to(outside) for path in outside.rglob("*")) == before
    assert not (outside / "scope-mutation.lock").exists()
    assert not list((epic / "reviews").glob("audit-*/audit-attempt.yaml"))


def test_executable_mode_drift_blocks_audit_mutation(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    (repo / "src" / "service.py").chmod(0o755)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 1


def test_gate_pass_requires_positive_execution_and_zero_skip(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt", passed=0) == 1
    assert _record_pass(epic, attempt, run, epic / "proof.txt", passed=1) == 0


def test_not_applicable_gate_requires_hash_bound_authority(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    gate_id = yaml.safe_load((attempt / "audit-attempt.yaml").read_text(encoding="utf-8"))["gates"][0]["id"]
    base = [
        "record-gate", str(epic), str(attempt), "--run", str(run), "--gate", gate_id,
        "--status", "not_applicable", "--summary", "n/a",
    ]
    assert AUDIT.main(base) == 1
    assert AUDIT.main([
        "record-authority", str(epic), str(attempt), "--run", str(run),
        "--authority-id", "AUTH-NA", "--kind", "gate_not_applicable", "--subject", gate_id,
        "--source", "user",
    ]) == 0
    assert AUDIT.main([*base, "--authority-id", "AUTH-NA"]) == 0


def test_failed_receipt_candidate_is_ingested_and_no_drop_is_enforced(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    _receipt(
        repo,
        attempt,
        top_status="failed",
        row_statuses=["completed", "provider_failed", "provider_failed"],
        candidates={"claude": [_candidate("AUDIT-CANDIDATE-001")]},
        decisions={"claude": "findings"},
    )
    source = "review:audit-001:claude:semantic_core:AUDIT-CANDIDATE-001"
    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run)
    ]) == 0
    findings = yaml.safe_load((epic / "audit-findings.yaml").read_text(encoding="utf-8"))
    assert findings["findings"][0]["source_ids"] == [source]


def test_conservative_max_severity_and_conflicting_dispositions_block(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    _receipt(
        repo,
        attempt,
        candidates={
            "claude": [_candidate("C-1", severity="minor")],
            "codex": [_candidate("C-2", severity="blocking")],
        },
        decisions={"claude": "findings", "codex": "findings"},
    )

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run)
    ]) == 0
    findings = yaml.safe_load((epic / "audit-findings.yaml").read_text(encoding="utf-8"))
    assert findings["findings"][0]["severity"] == "blocking"

    repo2, epic2, run2 = _fixture(tmp_path / "other")
    attempt2 = _prepare(epic2, run2)
    assert _record_pass(epic2, attempt2, run2, epic2 / "proof.txt") == 0
    _receipt(
        repo2,
        attempt2,
        candidates={
            "claude": [_candidate("C-1", disposition="remediation_required")],
            "codex": [_candidate("C-2", disposition="user_decision")],
        },
        decisions={"claude": "findings", "codex": "findings"},
    )

    assert AUDIT.main([
        "apply-synthesis", str(epic2), str(attempt2), "--run", str(run2)
    ]) == 1


def test_accepted_risk_requires_explicit_current_authority(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    _receipt(
        repo,
        attempt,
        candidates={"claude": [_candidate("C-1")]},
        decisions={"claude": "findings"},
    )

    command = [
        "apply-synthesis", str(epic), str(attempt), "--run", str(run)
    ]
    assert AUDIT.main(command) == 0
    findings = yaml.safe_load((epic / "audit-findings.yaml").read_text())
    assert findings["findings"][0]["disposition"] == "remediation_required"
    assert AUDIT.main([
        "record-authority", str(epic), str(attempt), "--run", str(run),
        "--authority-id", "AUTH-RISK", "--kind", "accepted_risk",
        "--subject", "runtime-defect", "--source", "user",
    ]) == 0
    assert AUDIT.main(command) == 0


def test_bare_question_and_unverified_evidence_block_final_pass(tmp_path: Path) -> None:
    for suffix, receipt_kwargs in (
        ("question", {"decisions": {"claude": "blocked"}, "questions": {"claude": [{"q": "choose"}]}}),
        ("unverified", {"decisions": {"claude": "unverified"}, "unverified": {"claude": ["runtime"]}}),
    ):
        repo, epic, run = _fixture(tmp_path / suffix)
        attempt = _prepare(epic, run)
        assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
        _receipt(repo, attempt, **receipt_kwargs)

        assert AUDIT.main([
            "apply-synthesis", str(epic), str(attempt), "--run", str(run)
        ]) == 0
        assert AUDIT.main([
            "finalize", str(epic), str(attempt), "--run", str(run)
        ]) == 0
        doc = yaml.safe_load((attempt / "audit-attempt.yaml").read_text(encoding="utf-8"))
        assert doc["status"] == "blocked"


def test_clean_full_audit_passes_and_second_full_attempt_is_blocked(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    _receipt(repo, attempt)

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run)
    ]) == 0
    assert AUDIT.main(["finalize", str(epic), str(attempt), "--run", str(run)]) == 0
    assert AUDIT.main([
        "validate", str(epic), str(attempt), "--phase", "complete", "--repo-root", str(repo)
    ]) == 0
    assert AUDIT.main(["prepare", str(epic), "--run", str(run), "--mode", "full"]) == 1


def test_failed_review_recovery_allows_one_replacement_full_audit(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    for _ in range(2):
        attempt = _prepare(epic, run)
        assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
        packet = yaml.safe_load((attempt / "review-packet.yaml").read_text(encoding="utf-8"))
        _receipt(
            repo, attempt, top_status="failed",
            row_statuses=["invalid_output"] * len(packet["assignments"]),
        )
        assert AUDIT.main(["apply-synthesis", str(epic), str(attempt), "--run", str(run)]) == 0
        assert AUDIT.main(["finalize", str(epic), str(attempt), "--run", str(run)]) == 0
        assert yaml.safe_load((attempt / "audit-attempt.yaml").read_text())["status"] == "blocked"
    assert AUDIT.main(["prepare", str(epic), "--run", str(run), "--mode", "full"]) == 1


def test_completed_reviewer_still_consumes_full_audit_budget(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    packet = yaml.safe_load((attempt / "review-packet.yaml").read_text(encoding="utf-8"))
    _receipt(
        repo, attempt, top_status="failed",
        row_statuses=["completed", *["invalid_output"] * (len(packet["assignments"]) - 1)],
    )
    assert AUDIT.main(["apply-synthesis", str(epic), str(attempt), "--run", str(run)]) == 0
    assert AUDIT.main(["finalize", str(epic), str(attempt), "--run", str(run)]) == 0
    assert AUDIT.main(["prepare", str(epic), "--run", str(run), "--mode", "full"]) == 1


def test_post_synthesis_findings_tamper_blocks_finalization(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    attempt = _prepare(epic, run)
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    _receipt(repo, attempt)

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run)
    ]) == 0
    findings = yaml.safe_load((epic / "audit-findings.yaml").read_text(encoding="utf-8"))
    findings["tampered"] = True
    _dump(epic / "audit-findings.yaml", findings)
    assert AUDIT.main(["finalize", str(epic), str(attempt), "--run", str(run)]) == 1


def test_targeted_prepare_requires_inline_remediation_evidence(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    finding = {
        "id": "AF-001",
        "fingerprint": "runtime-defect",
        "first_seen_attempt": "audit-000",
        "severity": "major",
        "category": "implementation",
        "disposition": "remediation_required",
        "status": "remediated_pending_verification",
        "title": "Runtime defect",
        "evidence": ["src/service.py"],
        "affected_paths": ["src/service.py"],
        "closure_test": "pytest -q tests/unit/test_one.py",
        "source_ids": ["review:audit-000:codex:semantic_core:C-1"],
        "detected_by": [],
        "authority_ref": None,
    }
    _dump(epic / "audit-findings.yaml", {"schema_version": 1, "epic_id": "E-001", "findings": [finding]})
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "targeted", "--finding", "AF-001"
    ]) == 1
    finding["remediation"] = {
        "source_attempt_id": "audit-000",
        "source_ids": ["review:audit-000:codex:semantic_core:C-1"],
        "affected_paths": [_relative(epic / "design.md", repo)],
        "affected_path_hashes": {
            _relative(epic / "design.md", repo): "sha256:" + "0" * 64
        },
        "checks": [
            {
                "command": "pytest -q tests/unit/test_one.py",
                "outcome": "pass",
                "exit_code": 0,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "summary": "1 passed",
                "evidence_hashes": {
                    _relative(epic / "proof.txt", repo): _sha(epic / "proof.txt")
                },
            }
        ],
    }
    _dump(epic / "audit-findings.yaml", {"schema_version": 1, "epic_id": "E-001", "findings": [finding]})
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "targeted", "--finding", "AF-001"
    ]) == 1
    finding["remediation"] = {
        "source_attempt_id": "audit-000",
        "source_ids": ["review:audit-000:codex:semantic_core:C-1"],
        "affected_paths": [_relative(epic / "design.md", repo)],
        "affected_path_hashes": {_relative(epic / "design.md", repo): _sha(epic / "design.md")},
        "checks": [
            {
                "command": "pytest -q tests/unit/test_one.py",
                "outcome": "pass",
                "exit_code": 0,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "summary": "1 passed",
                "evidence_hashes": {_relative(epic / "proof.txt", repo): _sha(epic / "proof.txt")},
            }
        ],
    }
    _dump(epic / "audit-findings.yaml", {"schema_version": 1, "epic_id": "E-001", "findings": [finding]})
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "targeted", "--finding", "AF-001"
    ]) == 0


def test_targeted_audit_packet_carries_snapshot_and_apply_rejects_drift(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path)
    finding = _remediated_audit_finding(repo, epic)
    findings_path = epic / "audit-findings.yaml"
    _dump(
        findings_path,
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    attempt = _prepare(epic, run, "targeted", "--finding", "AF-001")
    packet = yaml.safe_load((attempt / "review-packet.yaml").read_text(encoding="utf-8"))
    target = packet["target_findings"][0]
    assert target["id"] == "AF-001"
    assert target["fingerprint"] == "runtime-defect"
    assert target["finding_sha256"] == AUDIT._structured_sha256(finding)
    assert target["affected_acceptance_ids"] == ["AC-001"]
    assert target["source_candidate_ids"] == finding["source_ids"]
    assert target["closure_test"] == finding["closure_test"]
    assert target["remediation"] == finding["remediation"]
    assert target["remediation_sha256"] == AUDIT._structured_sha256(
        finding["remediation"]
    )

    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    verification = {
        "fingerprint": "runtime-defect",
        "outcome": "verified",
        "source_candidate_ids": finding["source_ids"],
        "closure_test": finding["closure_test"],
    }
    _receipt(repo, attempt, verifications={"codex": [verification]})
    current = yaml.safe_load(findings_path.read_text(encoding="utf-8"))
    current["findings"][0]["title"] = "Drifted after audit preparation"
    _dump(findings_path, current)

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run),
    ]) == 1
    assert "targeted finding snapshot differs" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_candidate_ids", ["fabricated-source"], "source_candidate_ids"),
        ("closure_test", "stale closure", "closure_test"),
    ],
)
def test_targeted_audit_verification_must_match_packet_identity(
    tmp_path: Path,
    capsys: object,
    field: str,
    value: object,
    message: str,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    finding = _remediated_audit_finding(repo, epic)
    _dump(
        epic / "audit-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    attempt = _prepare(epic, run, "targeted", "--finding", "AF-001")
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    verification = {
        "fingerprint": "runtime-defect",
        "outcome": "verified",
        "source_candidate_ids": finding["source_ids"],
        "closure_test": finding["closure_test"],
    }
    verification[field] = value
    _receipt(repo, attempt, verifications={"codex": [verification]})

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run),
    ]) == 1
    assert message in capsys.readouterr().err


def test_targeted_audit_verification_accepts_exact_packet_identity(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    finding = _remediated_audit_finding(repo, epic)
    findings_path = epic / "audit-findings.yaml"
    _dump(
        findings_path,
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    attempt = _prepare(epic, run, "targeted", "--finding", "AF-001")
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 0
    verification = {
        "fingerprint": "runtime-defect",
        "outcome": "verified",
        "source_candidate_ids": finding["source_ids"],
        "closure_test": finding["closure_test"],
    }
    _receipt(repo, attempt, verifications={"codex": [verification]})

    assert AUDIT.main([
        "apply-synthesis", str(epic), str(attempt), "--run", str(run),
    ]) == 0
    findings = yaml.safe_load(findings_path.read_text(encoding="utf-8"))
    assert findings["findings"][0]["status"] == "verified"


def test_wrong_epic_run_cannot_mutate(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    doc["epic_id"] = "E-999"
    _dump(run, doc)
    assert AUDIT.main(["prepare", str(epic), "--run", str(run), "--mode", "full"]) == 1


def test_mutation_guard_rejects_forged_path_and_recorded_active_job(
    tmp_path: Path, capsys: object
) -> None:
    _, epic, run = _fixture(tmp_path)
    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    forged = run.parents[4] / "forged-run.yaml"
    _dump(forged, run_doc)
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(forged), "--mode", "full"
    ]) == 1
    assert "worker run path must be" in capsys.readouterr().err

    run_doc["active_job"] = {"job_id": "stale-active"}
    _dump(run, run_doc)
    assert AUDIT.main([
        "prepare", str(epic), "--run", str(run), "--mode", "full"
    ]) == 1
    assert "recorded active job" in capsys.readouterr().err


def test_mutation_guard_accepts_one_slugged_epic_and_rejects_ambiguity(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path, "e-001-row-oriented-result-bank")
    attempt = _prepare(epic, run)
    (repo / "docs" / "epics" / "E-001-second-match").mkdir()
    assert _record_pass(epic, attempt, run, epic / "proof.txt") == 1
    assert "resolver is ambiguous" in capsys.readouterr().err


def test_policy_has_no_metrics_capture_matrix_or_false_positive() -> None:
    policy = yaml.safe_load(
        (Path(__file__).parents[2] / "src_shared" / "config" / "audit-policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    text = yaml.safe_dump(policy).lower()
    assert "metrics" not in text
    assert "capture" not in text
    assert "matrix" not in text
    assert "false_positive" not in text
    assert "rejected" not in text

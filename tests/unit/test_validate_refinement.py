from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml


SCRIPT = Path(__file__).parents[2] / "src_shared" / "scripts" / "validate-refinement.py"
SPEC = importlib.util.spec_from_file_location("scope_validate_refinement", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _sha(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _fixture(
    tmp_path: Path, epic_directory: str = "E-001"
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    epic = repo / "docs" / "epics" / epic_directory
    epic.mkdir(parents=True)
    (epic / "details.md").write_text("# Details\n", encoding="utf-8")
    (epic / "acceptance-criteria.md").write_text("# Acceptance\n\n## AC-001\n", encoding="utf-8")
    (epic / "design.md").write_text("# Design\n", encoding="utf-8")
    (epic / "baseline.txt").write_text("1 passed\n", encoding="utf-8")
    plan = {
        "schema_version": 1,
        "epic_id": "E-001",
        "story_id": "STORY-001",
        "acceptance_ids": ["AC-001"],
        "proof_ids": ["PROOF-001"],
    }
    _dump(epic / "file-plan-story-001.yaml", plan)
    manifest = {
        "schema_version": 1,
        "epic_id": "E-001",
        "risk_level": "low",
        "capabilities": [],
        "author_provider": "codex",
        "acceptance_ids": ["AC-001"],
        "dependencies": [],
        "artifact_ownership": [
            {"path": name, "owner": "architect", "authority": "canonical"}
            for name in (
                "details.md",
                "acceptance-criteria.md",
                "design.md",
                "delivery-manifest.yaml",
                "file-plan-story-001.yaml",
            )
        ]
        + [{"path": "baseline.txt", "owner": "tests", "authority": "evidence"}],
        "decisions": [],
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
                "classification": "existing_runnable",
                "level": "unit",
                "command": "pytest -q tests/unit/test_one.py",
                "expected_result": "passes",
                "baseline_evidence": {
                    "command": "pytest -q tests/unit/test_one.py",
                    "outcome": "pass",
                    "exit_code": 0,
                    "passed": 1,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                    "summary": "1 passed",
                    "evidence_hashes": {
                        _relative(epic / "baseline.txt", repo): _sha(epic / "baseline.txt")
                    },
                },
            }
        ],
    }
    _dump(epic / "delivery-manifest.yaml", manifest)
    run = repo / "tmp_debug" / "scope-runs" / "E-001" / "epic_refine" / "run.yaml"
    scope_root = tmp_path / "scope-install"
    worker_policy = scope_root / "config" / "worker-policy.yaml"
    worker_policy.parent.mkdir(parents=True)
    worker_policy.write_text("schema_version: 2\n", encoding="utf-8")
    _dump(
        run,
        {
            "schema_version": 2,
            "epic_id": "E-001",
            "command": "epic_refine",
            "repository_root": str(repo),
            "working_root": str(repo),
            "scope_root": str(scope_root),
            "worker_policy_sha256": _sha(worker_policy),
            "worker_profile": "default",
            "active_job": None,
            "completed_jobs": [],
        },
    )
    return repo, epic, run


def _approve_product(epic: Path, run: Path) -> None:
    assert (
        VALIDATOR.main(
            [
                "record-authority",
                str(epic),
                "--run",
                str(run),
                "--authority-id",
                "AUTH-PRODUCT",
                "--gate",
                "product_contract",
                "--source",
                "user",
                "--decision",
                "approved",
            ]
        )
        == 0
    )


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_mutation_lock_rejects_symlinked_tmp_debug_without_outside_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, epic, run = _fixture(tmp_path)
    outside = tmp_path / "outside-runtime"
    (repo / "tmp_debug").rename(outside)
    os.symlink(outside, repo / "tmp_debug")
    before = sorted(path.relative_to(outside) for path in outside.rglob("*"))

    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run),
        "--authority-id", "AUTH-PRODUCT", "--gate", "product_contract",
        "--source", "user", "--decision", "approved",
    ]) == 1

    assert "symlink" in capsys.readouterr().err
    assert sorted(path.relative_to(outside) for path in outside.rglob("*")) == before
    assert not (outside / "scope-mutation.lock").exists()
    assert not (epic / "refinement-state.yaml").exists()


def _create_packet(epic: Path, run: Path, kind: str = "full", *extra: str) -> Path:
    assert (
        VALIDATOR.main(
            [
                "create-review-packet",
                str(epic),
                "--run",
                str(run),
                "--kind",
                kind,
                *extra,
            ]
        )
        == 0
    )
    return sorted((epic / "reviews").glob("refine-*/review-packet.yaml"))[-1]


def _corrected_finding(repo: Path, epic: Path) -> dict[str, object]:
    source_id = "review:refine-000:codex:semantic_core:C-1"
    return {
        "id": "RF-001",
        "fingerprint": "authority-flow",
        "severity": "major",
        "category": "architecture",
        "status": "corrected",
        "title": "Name the authority owner",
        "evidence": ["design.md"],
        "affected_acceptance_ids": ["AC-001"],
        "impact": "Implementation would guess",
        "owner": "architect",
        "closure_test": "inspect design owner",
        "source_candidate_ids": [source_id],
        "resolution": {
            "affected_paths": [_relative(epic / "design.md", repo)],
            "affected_path_hashes": {
                _relative(epic / "design.md", repo): _sha(epic / "design.md")
            },
            "source_candidate_ids": [source_id],
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
                        _relative(epic / "baseline.txt", repo): _sha(epic / "baseline.txt")
                    },
                }
            ],
        },
    }


def _receipt(
    repo: Path,
    packet_path: Path,
    *,
    top_status: str = "completed",
    row_statuses: list[str] | None = None,
    candidates: dict[str, list[dict[str, object]]] | None = None,
    verifications: dict[str, list[dict[str, object]]] | None = None,
    questions: dict[str, list[object]] | None = None,
) -> Path:
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    template = repo / "review-template.md"
    template.write_text("template", encoding="utf-8")
    rows = []
    statuses = row_statuses or ["completed"] * len(packet["assignments"])
    for assignment, status in zip(packet["assignments"], statuses):
        provider = assignment["provider"]
        mission = assignment["mission"]
        output = packet_path.parent / f"review-{provider}-{mission}.md"
        output.write_text(f"review from {provider}\n", encoding="utf-8")
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
                    "prompt": _relative(packet_path.parent / f"prompt-{provider}.md", repo),
                    "output": _relative(output, repo),
                    "log": _relative(packet_path.parent / f"log-{provider}.txt", repo),
                },
                "output_sha256": _sha(output),
                "decision": "user_decision_required"
                if (questions or {}).get(provider)
                else "approved",
                "questions": (questions or {}).get(provider, []),
                "candidates": raw_candidates,
                "targeted_verifications": raw_verifications,
            }
        )
    receipt = {
        "schema_version": 2,
        "workflow": "refinement",
        "reviewer_profile": packet["reviewer_profile"],
        "reviewer_set": packet["reviewer_set"],
        "status": top_status,
        "packet_path": _relative(packet_path, repo),
        "packet_sha256": _sha(packet_path),
        "template_path": _relative(template, repo),
        "template_sha256": _sha(template),
        "assignment_manifest_sha256": VALIDATOR._structured_sha256(packet["assignments"]),
        "git_identity": {
            "before": {"head": "abc", "tree": "def"},
            "after": {"head": "abc", "tree": "def"},
            "unchanged": True,
        },
        "assignments": rows,
    }
    path = packet_path.parent / "reviewer-receipt.yaml"
    _dump(path, receipt)
    return path


def _stale_failed_receipt(
    repo: Path, epic: Path, run: Path, template_state: str = "changed"
) -> tuple[Path, Path]:
    packet = _create_packet(epic, run)
    assignments = yaml.safe_load(packet.read_text(encoding="utf-8"))["assignments"]
    receipt = _receipt(
        repo,
        packet,
        top_status="failed",
        row_statuses=["provider_failed"] * len(assignments),
    )
    receipt_doc = yaml.safe_load(receipt.read_text(encoding="utf-8"))
    for row in receipt_doc["assignments"]:
        row["decision"] = ""
    _dump(receipt, receipt_doc)
    template = repo / "review-template.md"
    if template_state == "changed":
        template.write_text("new template", encoding="utf-8")
    elif template_state == "missing":
        template.unlink()
    else:
        raise ValueError(f"unsupported template state: {template_state}")
    return packet, receipt


def test_product_validation_allows_absent_state_and_authority_initializes_it(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    assert not (epic / "refinement-state.yaml").exists()
    validator = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo)
    assert validator.validate() == []
    _approve_product(epic, run)
    state = yaml.safe_load((epic / "refinement-state.yaml").read_text(encoding="utf-8"))
    assert state["status"] == "product_approved"
    assert (epic / "refinement-findings.yaml").is_file()


def test_manifest_v1_remains_compatible_as_no_documentation_obligations(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path)
    manifest = yaml.safe_load((epic / "delivery-manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert "documentation_obligations" not in manifest
    assert VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate() == []


def test_manifest_v2_requires_and_validates_documentation_obligations(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path)
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    _dump(manifest_path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("documentation_obligations is required" in error for error in errors)

    manifest["documentation_obligations"] = [
        {
            "id": "DOC-001",
            "story": "STORY-001",
            "path": "docs/architecture/backend/05-building-blocks.md",
            "requirement_ref": "design.md#DOC-001",
        },
        {
            "id": "DOC-002",
            "story": "STORY-001",
            "path": "docs/architecture/backend/05-building-blocks.md",
            "requirement_ref": "design.md#DOC-002",
        },
    ]
    (epic / "design.md").write_text(
        "# Design\n\n### DOC-001: Building blocks\n\n### DOC-002: Runtime\n",
        encoding="utf-8",
    )
    _dump(manifest_path, manifest)
    assert VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate() == []


def test_documentation_obligations_reject_invalid_identity_scope_and_ownership(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path)
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest["documentation_obligations"] = [
        {
            "id": "bad",
            "story": "UNKNOWN",
            "path": "../outside.md",
            "requirement_ref": "",
        }
    ]
    (epic / "design.md").write_text(
        "# Design\n\n### DOC-001: Building blocks\n", encoding="utf-8"
    )
    _dump(manifest_path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("id does not match policy" in error for error in errors)
    assert any("references unknown story" in error for error in errors)
    assert any("normalized relative path" in error for error in errors)
    assert any("requirement_ref must be a non-empty string" in error for error in errors)

    manifest["documentation_obligations"] = [
        {
            "id": "DOC-001",
            "story": "STORY-001",
            "path": "docs/architecture/05-building-blocks.md",
            "requirement_ref": "design.md#DOC-001",
        },
        {
            "id": "DOC-002",
            "story": "STORY-OTHER",
            "path": "docs/architecture/05-building-blocks.md",
            "requirement_ref": "design.md#DOC-002",
        },
    ]
    _dump(manifest_path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("references unknown story" in error for error in errors)
    assert any("conflicting owner stories" in error for error in errors)


def test_documentation_requirement_ref_binds_matching_design_heading(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path)
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest["documentation_obligations"] = [
        {
            "id": "DOC-001",
            "story": "STORY-001",
            "path": "docs/architecture/05-building-blocks.md",
            "requirement_ref": "design.md#DOC-002",
        }
    ]
    (epic / "design.md").write_text(
        "# Design\n\n### DOC-002: Different requirement\n", encoding="utf-8"
    )
    _dump(manifest_path, manifest)

    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("requirement_ref must contain DOC-001" in error for error in errors)
    assert any("no matching design.md heading: ### DOC-001" in error for error in errors)


def test_documentation_target_content_is_not_in_refinement_handoff_hashes(
    tmp_path: Path,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    target = repo / "docs" / "architecture" / "05-building-blocks.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Before implementation\n", encoding="utf-8")
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest["documentation_obligations"] = [
        {
            "id": "DOC-001",
            "story": "STORY-001",
            "path": _relative(target, repo),
            "requirement_ref": "design.md#DOC-001",
        }
    ]
    (epic / "design.md").write_text(
        "# Design\n\n### DOC-001: Building blocks\n", encoding="utf-8"
    )
    _dump(manifest_path, manifest)

    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    packet_doc = yaml.safe_load(packet.read_text(encoding="utf-8"))
    assert _relative(manifest_path, repo) in packet_doc["artifact_hashes"]
    assert _relative(target, repo) not in packet_doc["artifact_hashes"]


def test_product_gate_survives_design_and_manifest_evolution(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    (epic / "design.md").write_text("# Design v2\n", encoding="utf-8")
    manifest = yaml.safe_load((epic / "delivery-manifest.yaml").read_text(encoding="utf-8"))
    manifest["design_revision"] = 2
    _dump(epic / "delivery-manifest.yaml", manifest)
    assert _create_packet(epic, run).is_file()


def test_product_decision_remains_valid_after_answer_is_applied(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["decisions"] = [
        {"id": "PD-001", "statement": "pending", "status": "pending", "authority_id": None}
    ]
    _dump(manifest_path, manifest)
    assert (
        VALIDATOR.main(
            [
                "record-authority",
                str(epic),
                "--run",
                str(run),
                "--authority-id",
                "AUTH-DECISION",
                "--kind",
                "product_decision",
                "--subject",
                "PD-001",
                "--source",
                "user",
                "--decision",
                "option-a",
            ]
        )
        == 0
    )
    (epic / "acceptance-criteria.md").write_text("# Acceptance\n\n## AC-001\nOption A.\n", encoding="utf-8")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["decisions"][0] = {
        "id": "PD-001",
        "statement": "option-a",
        "status": "decided",
        "authority_id": "AUTH-DECISION",
    }
    _dump(manifest_path, manifest)
    assert VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate() == []


def test_decision_statement_must_equal_authority_answer(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    manifest_path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["decisions"] = [
        {"id": "PD-001", "statement": "pending", "status": "pending", "authority_id": None}
    ]
    _dump(manifest_path, manifest)
    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run), "--authority-id", "AUTH-D",
        "--kind", "product_decision", "--subject", "PD-001", "--source", "user",
        "--decision", "option-a",
    ]) == 0
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["decisions"][0] = {
        "id": "PD-001", "statement": "option-b", "status": "decided", "authority_id": "AUTH-D"
    }
    _dump(manifest_path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("exact decision text" in error for error in errors)


def test_create_packet_rejects_malformed_story_boundary(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    plan = yaml.safe_load((epic / "file-plan-story-001.yaml").read_text(encoding="utf-8"))
    plan["proof_ids"] = []
    _dump(epic / "file-plan-story-001.yaml", plan)
    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full"
    ]) == 1


def test_review_packet_binds_owned_inputs_and_apply_rejects_drift(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    packet_doc = yaml.safe_load(packet.read_text(encoding="utf-8"))
    assert _relative(epic / "refinement-state.yaml", repo) in packet_doc["artifact_hashes"]
    assert _relative(epic / "baseline.txt", repo) in packet_doc["artifact_hashes"]
    receipt = _receipt(repo, packet)
    (epic / "design.md").write_text("# changed during review\n", encoding="utf-8")
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 1


def test_completed_review_remains_portable_after_installed_template_disappears(
    tmp_path: Path,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    receipt = _receipt(repo, packet)
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 0
    (repo / "review-template.md").unlink()
    errors = VALIDATOR.RefinementValidator(
        epic, "review", repo_root=repo
    ).validate()
    assert not any("review template" in error for error in errors)


def test_final_handoff_boundary_includes_transitive_resolution_evidence(
    tmp_path: Path,
) -> None:
    repo, epic, _ = _fixture(tmp_path)
    external = repo / "docs/evidence/correction.md"
    external.parent.mkdir(parents=True)
    external.write_text("verified correction\n", encoding="utf-8")
    (epic / "refinement-review.md").write_text("# Review\n", encoding="utf-8")
    finding = _corrected_finding(repo, epic)
    external_relative = _relative(external, repo)
    finding["resolution"]["affected_paths"].append(external_relative)
    finding["resolution"]["affected_path_hashes"][external_relative] = _sha(external)
    finding["resolution"]["checks"][0]["evidence_hashes"] = {
        external_relative: _sha(external)
    }
    _dump(
        epic / "refinement-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    validator = VALIDATOR.RefinementValidator(epic, "review", repo_root=repo)
    validator.manifest = yaml.safe_load((epic / "delivery-manifest.yaml").read_text())
    validator.findings = yaml.safe_load(
        (epic / "refinement-findings.yaml").read_text()
    )
    assert external.resolve() in {
        path.resolve() for path in validator._gate_paths("final_handoff")
    }


def test_targeted_packet_binds_findings_and_apply_rejects_target_row_drift(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(repo, epic)
    source_id = finding["source_candidate_ids"][0]
    findings_path = epic / "refinement-findings.yaml"
    _dump(
        findings_path,
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    findings_path.write_text(
        findings_path.read_text(encoding="utf-8") + "# retained formatting\n",
        encoding="utf-8",
    )
    findings_before = findings_path.read_bytes()
    packet_path = _create_packet(
        epic,
        run,
        "targeted",
        "--target-fingerprint",
        "authority-flow",
    )
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    findings_relative = _relative(findings_path, repo)
    assert findings_path.read_bytes() == findings_before
    assert packet["artifact_hashes"][findings_relative] == _sha(findings_path)
    assert packet["target_findings"] == [
        {
            "fingerprint": "authority-flow",
            "finding_sha256": VALIDATOR._structured_sha256(finding),
            "source_candidate_ids": [source_id],
            "closure_test": "inspect design owner",
            "required_assignments": [
                {"provider": "codex", "mission": "semantic_core"}
            ],
        }
    ]
    verification = {
        "fingerprint": "authority-flow",
        "outcome": "verified",
        "evidence": "closure confirmed",
        "source_candidate_ids": [source_id],
        "closure_test": "inspect design owner",
    }
    receipt_path = _receipt(
        repo,
        packet_path,
        verifications={provider: [verification] for provider in ("claude", "codex")},
    )

    findings = yaml.safe_load(findings_path.read_text(encoding="utf-8"))
    findings["findings"][0]["title"] = "Drifted after packet creation"
    _dump(findings_path, findings)
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    packet["artifact_hashes"][findings_relative] = _sha(findings_path)
    packet["boundary_sha256"] = VALIDATOR._structured_sha256(packet["artifact_hashes"])
    _dump(packet_path, packet)
    receipt = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    receipt["packet_sha256"] = _sha(packet_path)
    _dump(receipt_path, receipt)

    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt_path), "--run", str(run)
    ]) == 1
    assert "finding_sha256 mismatch" in capsys.readouterr().err


def test_targeted_packet_rejects_unroutable_source_id(
    tmp_path: Path, capsys: object
) -> None:
    _, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(run.parents[4], epic)
    finding["source_candidate_ids"] = ["malformed-source"]
    finding["resolution"]["source_candidate_ids"] = ["malformed-source"]
    _dump(
        epic / "refinement-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )

    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "targeted",
        "--target-fingerprint", "authority-flow",
    ]) == 1
    assert "malformed migrated source candidate ID" in capsys.readouterr().err


def test_targeted_packet_rejects_extra_non_origin_assignment(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(repo, epic)
    _dump(
        epic / "refinement-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    packet_path = _create_packet(
        epic, run, "targeted", "--target-fingerprint", "authority-flow"
    )
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    packet["assignments"].append(
        {"provider": "claude", "mission": "semantic_core"}
    )
    _dump(packet_path, packet)

    errors, _ = VALIDATOR._verify_packet(
        packet_path,
        epic,
        repo,
        VALIDATOR._policy(VALIDATOR._default_policy_path()),
    )
    assert any(
        "assignments must exactly equal target requirements" in error
        for error in errors
    )


def test_targeted_receipt_cannot_spoof_assignment_identity(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(repo, epic)
    source_ids = [
        "review:refine-000:claude:semantic_core:C-1",
        "review:refine-000:codex:semantic_core:C-1",
    ]
    finding["source_candidate_ids"] = source_ids
    finding["resolution"]["source_candidate_ids"] = source_ids
    _dump(
        epic / "refinement-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    packet = _create_packet(
        epic, run, "targeted", "--target-fingerprint", "authority-flow"
    )
    verification = {
        "fingerprint": "authority-flow",
        "outcome": "verified",
        "evidence": "closure confirmed",
        "source_candidate_ids": source_ids,
        "closure_test": "inspect design owner",
    }
    receipt = _receipt(
        repo,
        packet,
        verifications={provider: [verification] for provider in ("claude", "codex")},
    )
    receipt_doc = yaml.safe_load(receipt.read_text(encoding="utf-8"))
    receipt_doc["assignments"][0]["targeted_verifications"][0]["provider"] = "codex"
    _dump(receipt, receipt_doc)

    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 1
    assert "provider/mission differs from its assignment" in capsys.readouterr().err


def test_targeted_apply_uses_each_finding_required_assignment_subset(
    tmp_path: Path,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    full_packet = _create_packet(epic, run)
    full_receipt = _receipt(repo, full_packet)
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(full_receipt), "--run", str(run)
    ]) == 0

    codex_finding = _corrected_finding(repo, epic)
    codex_source_id = codex_finding["source_candidate_ids"][0]
    claude_finding = _corrected_finding(repo, epic)
    claude_source_id = "review:refine-000:claude:semantic_core:C-2"
    claude_finding.update(
        {
            "id": "RF-002",
            "fingerprint": "schema-flow",
            "source_candidate_ids": [claude_source_id],
        }
    )
    claude_finding["resolution"]["source_candidate_ids"] = [claude_source_id]
    findings_path = epic / "refinement-findings.yaml"
    _dump(
        findings_path,
        {
            "schema_version": 1,
            "epic_id": "E-001",
            "findings": [codex_finding, claude_finding],
        },
    )
    targeted_packet = _create_packet(
        epic,
        run,
        "targeted",
        "--target-fingerprint",
        "authority-flow",
        "--target-fingerprint",
        "schema-flow",
    )
    targeted_receipt = _receipt(
        repo,
        targeted_packet,
        verifications={
            "codex": [
                {
                    "fingerprint": "authority-flow",
                    "outcome": "verified",
                    "evidence": "closure confirmed",
                    "source_candidate_ids": [codex_source_id],
                    "closure_test": "inspect design owner",
                }
            ],
            "claude": [
                {
                    "fingerprint": "schema-flow",
                    "outcome": "verified",
                    "evidence": "closure confirmed",
                    "source_candidate_ids": [claude_source_id],
                    "closure_test": "inspect design owner",
                }
            ],
        },
    )
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(targeted_receipt), "--run", str(run)
    ]) == 0
    assert VALIDATOR.RefinementValidator(epic, "review", repo_root=repo).validate() == []

    (epic / "refinement-review.md").write_text("# Refinement Review\n", encoding="utf-8")
    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run),
        "--authority-id", "AUTH-FINAL", "--gate", "final_handoff",
        "--source", "user", "--decision", "approved",
    ]) == 0
    assert VALIDATOR.RefinementValidator(epic, "handoff", repo_root=repo).validate() == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_candidate_ids", ["fabricated-source"], "source_candidate_ids"),
        ("closure_test", "stale closure", "closure_test"),
    ],
)
def test_targeted_verification_must_match_packet_correction_identity(
    tmp_path: Path,
    capsys: object,
    field: str,
    value: object,
    message: str,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(repo, epic)
    _dump(
        epic / "refinement-findings.yaml",
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    packet = _create_packet(
        epic,
        run,
        "targeted",
        "--target-fingerprint",
        "authority-flow",
    )
    verification = {
        "fingerprint": "authority-flow",
        "outcome": "verified",
        "evidence": "closure confirmed",
        "source_candidate_ids": finding["source_candidate_ids"],
        "closure_test": finding["closure_test"],
    }
    verification[field] = value
    receipt = _receipt(
        repo,
        packet,
        verifications={provider: [verification] for provider in ("claude", "codex")},
    )
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 1
    assert message in capsys.readouterr().err


def test_incomplete_receipt_does_not_consume_review_budget_but_pending_does(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    first = _create_packet(epic, run)
    _receipt(repo, first, top_status="failed", row_statuses=["timed_out", "provider_failed"])
    second = _create_packet(epic, run)
    assert second.parent.name == "refine-002"
    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full"
    ]) == 1


@pytest.mark.parametrize("template_state", ["changed", "missing"])
def test_stale_template_failed_receipt_allows_next_packet_without_rewriting_history(
    tmp_path: Path, template_state: str
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet, receipt = _stale_failed_receipt(
        repo, epic, run, template_state=template_state
    )
    before = {
        path: path.read_bytes()
        for path in (
            packet,
            receipt,
            epic / "refinement-state.yaml",
            epic / "refinement-findings.yaml",
        )
    }

    assert _create_packet(epic, run).parent.name == "refine-002"
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("violation", ["top_completed", "row_completed", "candidate"])
def test_stale_template_retry_rejects_completed_or_semantic_receipt(
    tmp_path: Path,
    violation: str,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet, receipt = _stale_failed_receipt(repo, epic, run)
    document = yaml.safe_load(receipt.read_text(encoding="utf-8"))
    if violation == "top_completed":
        document["status"] = "completed"
    elif violation == "row_completed":
        document["assignments"][0]["status"] = "completed"
    else:
        row = document["assignments"][0]
        row["candidates"] = [
            {
                "source_id": "C-1",
                "provider": row["provider"],
                "mission": row["mission"],
            }
        ]
    _dump(receipt, document)

    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full"
    ]) == 1
    assert not (packet.parent.parent / "refine-002").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("packet", None),
        ("assignment_manifest_sha256", "sha256:" + "0" * 64),
        ("git_identity", {"before": {}, "after": {}, "unchanged": False}),
    ],
)
def test_stale_template_retry_rejects_other_receipt_tampering(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet, receipt = _stale_failed_receipt(repo, epic, run)
    document = yaml.safe_load(receipt.read_text(encoding="utf-8"))
    if field == "packet":
        packet_document = yaml.safe_load(packet.read_text(encoding="utf-8"))
        packet_document["boundary_sha256"] = "sha256:" + "0" * 64
        _dump(packet, packet_document)
        document["packet_sha256"] = _sha(packet)
    else:
        document[field] = value
    _dump(receipt, document)

    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full"
    ]) == 1
    assert not (packet.parent.parent / "refine-002").exists()


def test_stale_template_retry_rejects_completed_review_id(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet, _ = _stale_failed_receipt(repo, epic, run)
    state_path = epic / "refinement-state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["completed_review_ids"] = ["refine-001"]
    _dump(state_path, state)
    policy = yaml.safe_load(VALIDATOR._default_policy_path().read_text(encoding="utf-8"))
    policy["review"]["maximum_full_reviews"] = 2
    policy_path = tmp_path / "refinement-policy.yaml"
    _dump(policy_path, policy)

    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full",
        "--policy", str(policy_path),
    ]) == 1
    assert not (packet.parent.parent / "refine-002").exists()


def test_failed_receipt_candidate_is_not_dropped(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    candidate = {
        "source_id": "RF-CANDIDATE-001",
        "severity": "major",
        "category": "architecture",
        "fingerprint": "authority-flow",
        "evidence": "design.md lacks an owner",
        "affected_manifest_ids": ["AC-001"],
        "impact": "implementation would guess",
        "required_correction": "name the owner",
        "closure_test": "inspect design owner",
        "requires_user": False,
    }
    receipt = _receipt(
        repo,
        packet,
        top_status="failed",
        row_statuses=["completed", "timed_out"],
        candidates={"claude": [candidate]},
    )
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 0
    findings = yaml.safe_load((epic / "refinement-findings.yaml").read_text(encoding="utf-8"))
    assert findings["findings"][0]["fingerprint"] == "authority-flow"
    state = yaml.safe_load((epic / "refinement-state.yaml").read_text(encoding="utf-8"))
    assert state["completed_review_ids"] == []


def test_existing_fingerprint_can_fill_a_missing_stable_field(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    finding = _corrected_finding(repo, epic)
    finding.pop("impact")
    findings_path = epic / "refinement-findings.yaml"
    _dump(
        findings_path,
        {"schema_version": 1, "epic_id": "E-001", "findings": [finding]},
    )
    packet = _create_packet(epic, run)
    candidate = {
        "source_id": "C-1",
        "severity": "major",
        "category": finding["category"],
        "fingerprint": finding["fingerprint"],
        "evidence": "fresh evidence",
        "affected_manifest_ids": ["AC-001"],
        "impact": "Implementation would guess",
        "required_correction": finding["title"],
        "closure_test": finding["closure_test"],
        "requires_user": False,
    }
    receipt = _receipt(repo, packet, candidates={"codex": [candidate]})

    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 0
    merged = yaml.safe_load(findings_path.read_text(encoding="utf-8"))["findings"][0]
    assert merged["impact"] == "Implementation would guess"
    assert merged["status"] == "corrected"
    assert merged["title"] == finding["title"]


def test_duplicate_fingerprint_with_conflicting_correction_semantics_fails(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    candidate = {
        "source_id": "C-1",
        "severity": "major",
        "category": "architecture",
        "fingerprint": "authority-flow",
        "evidence": "design.md lacks an owner",
        "affected_manifest_ids": ["AC-001"],
        "impact": "implementation would guess",
        "required_correction": "name the owner",
        "closure_test": "inspect design owner",
        "requires_user": False,
    }
    conflicting = {**candidate, "source_id": "C-2", "closure_test": "run another check"}
    receipt = _receipt(
        repo,
        packet,
        candidates={"claude": [candidate], "codex": [conflicting]},
    )
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 1
    assert "conflict for fingerprint authority-flow" in capsys.readouterr().err
    findings = yaml.safe_load((epic / "refinement-findings.yaml").read_text(encoding="utf-8"))
    assert findings["findings"] == []


def test_bare_reviewer_question_is_rejected_and_not_completed(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    packet = _create_packet(epic, run)
    receipt = _receipt(repo, packet, questions={"claude": [{"question": "Choose?"}]})
    assert VALIDATOR.main([
        "apply-review-receipt", str(epic), str(receipt), "--run", str(run)
    ]) == 1
    state = yaml.safe_load((epic / "refinement-state.yaml").read_text(encoding="utf-8"))
    assert state["completed_review_ids"] == []


def test_wrong_epic_run_cannot_mutate(tmp_path: Path) -> None:
    _, epic, run = _fixture(tmp_path)
    doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    doc["epic_id"] = "E-999"
    _dump(run, doc)
    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run), "--authority-id", "AUTH-X",
        "--gate", "product_contract", "--source", "user", "--decision", "approved",
    ]) == 1


def test_product_contract_can_be_renewed_after_product_correction(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    (epic / "details.md").write_text("# Corrected details\n", encoding="utf-8")
    (epic / "acceptance-criteria.md").write_text(
        "# Corrected acceptance\n\n## AC-001\n", encoding="utf-8"
    )

    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run),
        "--authority-id", "AUTH-PRODUCT-RENEWED", "--gate", "product_contract",
        "--source", "preapproval", "--decision", "approved",
    ]) == 0

    state = yaml.safe_load((epic / "refinement-state.yaml").read_text(encoding="utf-8"))
    gates = [row for row in state["user_decisions"] if row["kind"] == "product_contract"]
    assert [row["id"] for row in gates] == ["AUTH-PRODUCT", "AUTH-PRODUCT-RENEWED"]
    assert gates[-1]["artifact_hashes"] == {
        _relative(epic / "acceptance-criteria.md", repo): _sha(epic / "acceptance-criteria.md"),
        _relative(epic / "details.md", repo): _sha(epic / "details.md"),
    }


def test_mutation_guard_rejects_forged_path_and_recorded_active_job(
    tmp_path: Path, capsys: object
) -> None:
    _, epic, run = _fixture(tmp_path)
    run_doc = yaml.safe_load(run.read_text(encoding="utf-8"))
    forged = run.parents[4] / "forged-run.yaml"
    _dump(forged, run_doc)
    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(forged),
        "--authority-id", "AUTH-X", "--gate", "product_contract",
        "--source", "user", "--decision", "approved",
    ]) == 1
    assert "worker run path must be" in capsys.readouterr().err

    run_doc["active_job"] = {"job_id": "stale-active"}
    _dump(run, run_doc)
    assert VALIDATOR.main([
        "record-authority", str(epic), "--run", str(run),
        "--authority-id", "AUTH-X", "--gate", "product_contract",
        "--source", "user", "--decision", "approved",
    ]) == 1
    assert "recorded active job" in capsys.readouterr().err


def test_mutation_guard_accepts_one_slugged_epic_and_rejects_ambiguity(
    tmp_path: Path, capsys: object
) -> None:
    repo, epic, run = _fixture(tmp_path, "e-001-row-oriented-result-bank")
    _approve_product(epic, run)
    (repo / "docs" / "epics" / "E-001-second-match").mkdir()
    assert VALIDATOR.main([
        "create-review-packet", str(epic), "--run", str(run), "--kind", "full"
    ]) == 1
    assert "resolver is ambiguous" in capsys.readouterr().err


def test_dependency_requires_full_immutable_object_id(tmp_path: Path) -> None:
    repo, epic, _ = _fixture(tmp_path)
    path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["dependencies"] = [{"epic_id": "E-000", "commit": "abc123"}]
    _dump(path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("full 40- or 64-hex" in error for error in errors)


def test_implementation_created_proof_requires_exact_future_command(tmp_path: Path) -> None:
    repo, epic, _ = _fixture(tmp_path)
    path = epic / "delivery-manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["proofs"][0] = {
        "id": "PROOF-001",
        "classification": "implementation_created",
        "level": "unit",
        "path": "tests/unit/test_one.py",
        "expected_result": "passes",
    }
    _dump(path, manifest)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("proofs[0].command" in error for error in errors)


def test_closure_pass_rejects_skips_and_tmp_debug_evidence(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    tmp_evidence = repo / "tmp_debug" / "proof.txt"
    tmp_evidence.parent.mkdir(parents=True, exist_ok=True)
    tmp_evidence.write_text("pass", encoding="utf-8")
    findings = {
        "schema_version": 1,
        "epic_id": "E-001",
        "findings": [
            {
                "id": "RF-001",
                "fingerprint": "f",
                "severity": "major",
                "category": "architecture",
                "status": "corrected",
                "title": "fix",
                "evidence": ["design.md"],
                "affected_acceptance_ids": ["AC-001"],
                "impact": "impact",
                "owner": "architect",
                "closure_test": "check",
                "source_candidate_ids": ["source"],
                "resolution": {
                    "affected_paths": [_relative(epic / "design.md", repo)],
                    "affected_path_hashes": {
                        _relative(epic / "design.md", repo): _sha(epic / "design.md")
                    },
                    "source_candidate_ids": ["source"],
                    "checks": [
                        {
                            "command": "pytest",
                            "outcome": "pass",
                            "exit_code": 0,
                            "passed": 1,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 1,
                            "skip_reason": "not explained enough",
                            "summary": "skip",
                            "evidence_hashes": {_relative(tmp_evidence, repo): _sha(tmp_evidence)},
                        }
                    ],
                },
            }
        ],
    }
    _dump(epic / "refinement-findings.yaml", findings)
    validator = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo)
    errors = validator.validate()
    assert any("PASS requires" in error for error in errors)
    assert any("tmp_debug" in error for error in errors)


def test_obsolete_approval_ledger_is_rejected(tmp_path: Path) -> None:
    repo, epic, _ = _fixture(tmp_path)
    _dump(epic / "refinement-approvals.yaml", {"approvals": []})
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("obsolete duplicate lifecycle artifact" in error for error in errors)


def test_verified_finding_requires_real_targeted_receipt_not_hash_shaped_claim(tmp_path: Path) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    findings = {
        "schema_version": 1,
        "epic_id": "E-001",
        "findings": [
            {
                "id": "RF-001",
                "fingerprint": "authority-flow",
                "severity": "major",
                "category": "architecture",
                "status": "verified",
                "title": "fix owner",
                "evidence": ["design.md"],
                "affected_acceptance_ids": ["AC-001"],
                "impact": "implementation would guess",
                "owner": "architect",
                "closure_test": "inspect owner",
                "source_candidate_ids": ["review:refine-001:codex:semantic_core:C-1"],
                "resolution": {
                    "affected_paths": [_relative(epic / "design.md", repo)],
                    "affected_path_hashes": {
                        _relative(epic / "design.md", repo): _sha(epic / "design.md")
                    },
                    "source_candidate_ids": ["review:refine-001:codex:semantic_core:C-1"],
                    "checks": [
                        {
                            "command": "pytest",
                            "outcome": "pass",
                            "exit_code": 0,
                            "passed": 1,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 0,
                            "summary": "1 passed",
                            "evidence_hashes": {
                                _relative(epic / "baseline.txt", repo): _sha(epic / "baseline.txt")
                            },
                        }
                    ],
                    "verification": {
                        "review_id": "refine-999",
                        "provider": "codex",
                        "mission": "semantic_core",
                        "receipt_sha256": "sha256:" + "0" * 64,
                    },
                },
            }
        ],
    }
    _dump(epic / "refinement-findings.yaml", findings)
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("reviewer receipt" in error or "targeted review" in error for error in errors)


def test_duplicate_yaml_keys_fail_loud(tmp_path: Path) -> None:
    repo, epic, _ = _fixture(tmp_path)
    (epic / "delivery-manifest.yaml").write_text(
        "schema_version: 1\nepic_id: E-001\nepic_id: E-002\n", encoding="utf-8"
    )
    errors = VALIDATOR.RefinementValidator(epic, "product", repo_root=repo).validate()
    assert any("duplicate YAML key" in error for error in errors)


def test_policy_has_only_canonical_lifecycle_artifacts() -> None:
    policy = yaml.safe_load(
        (Path(__file__).parents[2] / "src_shared" / "config" / "refinement-policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    text = yaml.safe_dump(policy)
    assert "delivery-manifest.yaml" in text
    assert "refinement-state.yaml" in text
    assert "gate_approvals:" not in text
    assert "correction_checks:" not in text
    assert "rejected" not in policy["findings"]["statuses"]


@pytest.mark.parametrize('severity,terminal', [('minor', 'deferred'), ('major', 'corrected'), ('blocking', 'corrected')])
def test_minor_optional_only_after_third_completed_review(tmp_path: Path, severity: str, terminal: str) -> None:
    repo, epic, run = _fixture(tmp_path)
    _approve_product(epic, run)
    full = _create_packet(epic, run)
    receipt = _receipt(repo, full)
    assert VALIDATOR.main(['apply-review-receipt', str(epic), str(receipt), '--run', str(run)]) == 0
    finding = _corrected_finding(repo, epic)
    finding['severity'] = severity
    ledger = epic / 'refinement-findings.yaml'
    _dump(ledger, {'schema_version': 1, 'epic_id': 'E-001', 'findings': [finding]})
    for expected in ('corrected', terminal):
        packet = _create_packet(epic, run, 'targeted', '--target-fingerprint', finding['fingerprint'])
        check = {'fingerprint': finding['fingerprint'], 'outcome': 'still_open', 'evidence': 'defect remains',
                 'source_candidate_ids': finding['source_candidate_ids'], 'closure_test': finding['closure_test']}
        receipt = _receipt(repo, packet, verifications={'codex': [check]})
        assert VALIDATOR.main(['apply-review-receipt', str(epic), str(receipt), '--run', str(run)]) == 0
        finding = yaml.safe_load(ledger.read_text())['findings'][0]
        assert finding['status'] == expected
    state = yaml.safe_load((epic / 'refinement-state.yaml').read_text())
    assert len(state['completed_review_ids']) == 3
    assert (state['status'] == 'ready_for_final_approval') == (severity == 'minor')
    if severity == 'minor':
        assert VALIDATOR.RefinementValidator(epic, 'review', repo_root=repo).validate() == []
        assert VALIDATOR.main(['render-summary', str(epic), '--run', str(run)]) == 0
        assert 'deferred' in (epic / 'refinement-review.md').read_text()
        finding['severity'] = 'major'
        _dump(ledger, {'schema_version': 1, 'epic_id': 'E-001', 'findings': [finding]})
        assert any('only minor' in error for error in VALIDATOR.RefinementValidator(epic, 'review', repo_root=repo).validate())

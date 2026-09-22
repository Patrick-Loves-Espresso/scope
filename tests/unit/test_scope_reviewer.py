from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType

from filelock import FileLock
import psutil
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "src_shared/scripts/scope-reviewer.py"
POLICY_PATH = REPO_ROOT / "src_shared/config/reviewer-policy.yaml"
CODEGRAPH_POLICY = REPO_ROOT / "src_shared/config/codegraph-policy.yaml"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scope_reviewer", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load_module()


FAKE_PROVIDER = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import time

provider, state, *args = sys.argv[1:]
state = Path(state)
config_path = state / f"{provider}.json"
config = json.loads(config_path.read_text()) if config_path.exists() else {}

def record(kind, values):
    scrubbed = list(values)
    if "--print" in scrubbed:
        index = scrubbed.index("--print") + 1
        if index < len(scrubbed) and not scrubbed[index].startswith("--"):
            scrubbed[index] = "<PROMPT>"
    with (state / f"{provider}-invocations.jsonl").open("a") as handle:
        handle.write(json.dumps({"kind": kind, "args": scrubbed}) + "\n")

if args == ["--version"]:
    record("version", args)
    if config.get("version_fail"):
        print("version unavailable", file=sys.stderr)
        raise SystemExit(7)
    print(f"{provider} fake 1.0")
    raise SystemExit(0)
if args == ["--help"]:
    record("help", args)
    print("--print --no-session-persistence --permission-mode")
    raise SystemExit(0)
if args == ["auth", "status", "--json"]:
    record("auth", args)
    print(json.dumps({"loggedIn": True, "authMethod": "subscription"}))
    raise SystemExit(0)
if args == ["models"]:
    record("models", args)
    print("gemini-3.1-pro-high\ngemini-3.8-flash-high")
    raise SystemExit(0)

model = args[args.index("--model") + 1]
prompt = sys.stdin.read() if provider in {"claude", "codex"} else args[args.index("--print") + 1]
record("runtime", args)
(state / f"started-{provider}-{time.time_ns()}").write_text("1")
barrier = int(config.get("barrier", 0))
deadline = time.time() + 3
while barrier and len(list(state.glob("started-*"))) < barrier and time.time() < deadline:
    time.sleep(0.01)
if barrier and len(list(state.glob("started-*"))) < barrier:
    raise SystemExit(41)
if config.get("spawn_child"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (state / f"child-{provider}.pid").write_text(str(child.pid))
if config.get("commit"):
    changed = Path.cwd() / "reviewer-commit.txt"
    changed.write_text("changed")
    subprocess.run(["git", "add", "--", changed.name], check=True)
    subprocess.run([
        "git", "-c", "user.name=Reviewer", "-c", "user.email=r@example.invalid",
        "commit", "-q", "-m", "reviewer drift"
    ], check=True)
if config.get("usage_event"):
    print(json.dumps(config["usage_event"]), file=sys.stderr, flush=True)
if config.get("sleep"):
    time.sleep(float(config["sleep"]))
if config.get("rate_primary") and model == "gemini-3.1-pro-high":
    if config.get("partial"):
        print("partial semantic output", flush=True)
    print("RESOURCE_EXHAUSTED: quota exceeded", file=sys.stderr)
    raise SystemExit(29)
if config.get("error"):
    print(config["error"], file=sys.stderr)
    raise SystemExit(9)
if config.get("invalid"):
    output = "not a valid review\n"
elif "AUDIT_PROVIDER:" in prompt:
    candidate = config.get("candidate", "None")
    disposition = config.get("disposition", "")
    targeted = "AUDIT-VERIFICATION-" in candidate
    decision = config.get("decision") or (
        "findings" if targeted and "outcome: still_open" in candidate
        else "pass" if targeted
        else "blocked" if disposition in {"user_decision", "documentation_decision"}
        else "unverified" if disposition == "accepted_risk"
        else "findings" if candidate != "None" else "pass"
    )
    heading = "Targeted Verification" if targeted else "Finding Candidates"
    if "coverage_headers" in config:
        coverage_headers = config["coverage_headers"]
    else:
        covered = config.get("covered_acceptance_ids", ["AC-001"])
        coverage_headers = [f"COVERED_ACCEPTANCE_IDS: {covered}"]
    coverage = "\n".join(coverage_headers)
    output = f"""# Audit Review: {provider}

AUDIT_PROVIDER: {provider}
AUDIT_MISSION: semantic_core
DECISION: {decision}
{coverage}

## {heading}
{candidate}

## Unread or Unverified Evidence
- None

## Questions for User
- None

## Rationale
The evidence supports the decision.
"""
else:
    mission = "capability_specialist" if "capability_specialist" in prompt else "semantic_core"
    candidate = config.get("candidate", "None")
    targeted = "RF-VERIFICATION-" in candidate
    decision = config.get("decision") or (
        "corrections_required" if candidate != "None" else "approved"
    )
    heading = "Targeted Verification" if targeted else "Findings"
    output = f"""# Refinement Review

REVIEW_PROVIDER: {provider}
REVIEW_MISSION: {mission}
DECISION: {decision}

## Coverage
- Reviewed the assigned boundary.

## {heading}
{candidate}

## Questions for User
- None

## Decision Rationale
- The evidence supports the decision.
"""

if provider == "codex":
    Path(args[args.index("--output-last-message") + 1]).write_text(output)
else:
    sys.stdout.write(output)
'''


FAKE_CODEGRAPH = r"""#!/usr/bin/env python3
import json
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("1.5.0")
elif args and args[0] == "init":
    index = Path(args[1]) / ".codegraph"
    index.mkdir(parents=True, exist_ok=True)
    (index / "codegraph.db").write_text("fake")
elif args and args[0] == "sync":
    pass
elif args and args[0] == "status":
    root = Path(args[1]).resolve()
    print(json.dumps({
        "initialized": True,
        "projectPath": str(root),
        "worktreeMismatch": None,
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "index": {"state": "complete", "reindexRecommended": False},
    }))
else:
    raise SystemExit(2)
"""


AUDIT_TEMPLATE = """# Audit
AUDIT_PROVIDER: {{AUDIT_PROVIDER}}
AUDIT_MISSION: {{AUDIT_MISSION}}
Reviewer: {{REVIEWER_IDENTITY}}
Epic: {{EPIC_ID}}
Repo: {{REPO_ROOT}}
Packet: {{REVIEW_PACKET_PATH}}
Output: {{OUTPUT_PATH}}
"""


REFINEMENT_TEMPLATE = """# Refinement
REVIEW_PROVIDER: {{REVIEW_PROVIDER}}
REVIEW_MISSION: {{REVIEW_MISSION}}
Epic: {{EPIC_ID}}
Repo: {{REPO_ROOT}}
Packet: {{REVIEW_PACKET_PATH}}
Output: {{REVIEW_OUTPUT_PATH}}
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _dump(path: Path, value: object) -> Path:
    return _write(path, yaml.safe_dump(value, sort_keys=False))


def _environment(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / ".gitignore", "tmp_debug/\n.codegraph/\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    state = tmp_path / "provider-state"
    state.mkdir()
    fake = _write(repo / "fake-provider.py", FAKE_PROVIDER)
    codegraph = _write(state / "codegraph", FAKE_CODEGRAPH)
    codegraph.chmod(0o755)
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    for provider in ("claude", "codex", "agy"):
        selected = policy["providers"][provider]
        selected["executable"] = sys.executable
        selected["executable_prefix_args"] = [str(fake), provider, str(state)]
        selected["review_timeout_seconds"] = 2
        selected["supervisor_timeout_seconds"] = 2
    policy_path = _dump(repo / "reviewer-policy.yaml", policy)
    cg_policy = yaml.safe_load(CODEGRAPH_POLICY.read_text(encoding="utf-8"))
    cg_policy["executable"] = str(codegraph)
    _dump(repo / "codegraph-policy.yaml", cg_policy)
    return repo, state, policy_path


def _configure(state: Path, provider: str, **values: object) -> None:
    _write(state / f"{provider}.json", json.dumps(values))


def _packet(
    repo: Path,
    workflow: str,
    assignments: list[dict[str, str]],
    *,
    review_id: str = "review-001",
    review_kind: str = "full",
    targeted_fingerprints: list[str] | None = None,
    required_acceptance_ids: list[str] | None = None,
    targeted_acceptance_ids: list[list[str]] | None = None,
    targeted_source_ids: list[list[str]] | None = None,
    targeted_closure_tests: list[str] | None = None,
    targeted_required_assignments: list[list[dict[str, str]]] | None = None,
) -> tuple[Path, Path]:
    directory = repo / f"docs/epics/E-001/reviews/{review_id}"
    if workflow == "audit":
        required = required_acceptance_ids or ["AC-001"]
        fingerprints = targeted_fingerprints or []
        affected = targeted_acceptance_ids or [required for _ in fingerprints]
        source_ids = targeted_source_ids or [
            ["review:audit-001:codex:semantic_core:AUDIT-CANDIDATE-001"]
            for _ in fingerprints
        ]
        closure_tests = targeted_closure_tests or [
            "pytest -q tests/test_main.py" for _ in fingerprints
        ]
        assert len(affected) == len(source_ids) == len(closure_tests) == len(fingerprints)
        packet = _dump(
            directory / "review-packet.yaml",
            {
                "schema_version": 1,
                "epic_id": "E-001",
                "attempt_id": review_id,
                "mode": review_kind,
                "required_acceptance_ids": required,
                "target_findings": [
                    {
                        "fingerprint": fingerprint,
                        "affected_acceptance_ids": acceptance_ids,
                        "source_candidate_ids": target_source_ids,
                        "closure_test": closure_test,
                    }
                    for fingerprint, acceptance_ids, target_source_ids, closure_test in zip(
                        fingerprints,
                        affected,
                        source_ids,
                        closure_tests,
                        strict=True,
                    )
                ],
            },
        )
        _dump(
            directory / "audit-attempt.yaml",
            {"review": {"required_assignments": assignments}},
        )
        template = _write(repo / "reviewer-audit.md", AUDIT_TEMPLATE)
    else:
        fingerprints = targeted_fingerprints or []
        source_ids = targeted_source_ids or [
            ["review-000/codex/semantic_core/RF-CANDIDATE-001"]
            for _ in fingerprints
        ]
        closure_tests = targeted_closure_tests or ["run proof" for _ in fingerprints]
        required = targeted_required_assignments or [assignments for _ in fingerprints]
        assert len(source_ids) == len(closure_tests) == len(required) == len(fingerprints)
        packet = _dump(
            directory / "review-packet.yaml",
            {
                "schema_version": 1,
                "epic_id": "E-001",
                "review_id": review_id,
                "review_kind": review_kind,
                "target_findings": [
                    {
                        "fingerprint": fingerprint,
                        "source_candidate_ids": target_source_ids,
                        "closure_test": closure_test,
                        "required_assignments": target_assignments,
                    }
                    for fingerprint, target_source_ids, closure_test, target_assignments in zip(
                        fingerprints, source_ids, closure_tests, required, strict=True
                    )
                ],
                "assignments": assignments,
            },
        )
        template = _write(repo / "reviewer-refinement.md", REFINEMENT_TEMPLATE)
    return packet, template


def _args(
    repo: Path,
    policy: Path,
    packet: Path,
    template: Path | None,
    workflow: str,
    *,
    repair: bool = False,
    run: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        repo_root=repo,
        policy=policy,
        packet=packet,
        template=template,
        runtime_dir=None,
        receipt=None,
        run=run,
        workflow=workflow,
        repair_infrastructure=repair,
        reviewer_profile="default",
        reviewer_set="standard",
    )


def test_default_installed_template_is_snapshotted_into_review_directory(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, _ = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )

    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, None, "audit")
    )

    snapshot = packet.parent / "reviewer-template.md"
    assert code == 0
    assert snapshot.is_file()
    assert receipt["template_path"] == snapshot.relative_to(repo).as_posix()
    assert receipt["template_sha256"] == RUNNER.file_sha256(snapshot)


def test_default_installed_template_refuses_stale_review_snapshot(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, _ = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    (packet.parent / "reviewer-template.md").write_text(
        "stale template\n", encoding="utf-8"
    )

    with pytest.raises(RUNNER.ReviewerError, match="snapshot differs"):
        RUNNER.run_reviewers(_args(repo, policy, packet, None, "audit"))


def test_worktree_accepts_scope_run_from_matching_common_repository(
    tmp_path: Path,
) -> None:
    main, state, _ = _environment(tmp_path)
    subprocess.run(
        ["git", "-C", str(main), "config", "user.email", "scope@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "config", "user.name", "Scope Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(main), "commit", "-q", "-m", "baseline"],
        check=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "worktree",
            "add",
            "-q",
            "-b",
            "epic-test",
            str(worktree),
        ],
        check=True,
    )
    policy = worktree / "reviewer-policy.yaml"
    _configure(state, "codex")
    packet, template = _packet(
        worktree,
        "audit",
        [{"provider": "codex", "mission": "semantic_core"}],
        review_id="audit-001",
    )
    run = _dump(
        main / "tmp_debug/scope-runs/E-001/audit_epic/run.yaml",
        {
            "schema_version": 2,
            "epic_id": "E-001",
            "command": "audit_epic",
            "repository_root": str(main.resolve()),
            "working_root": str(worktree.resolve()),
            "codegraph": {
                "status": "degraded",
                "reason": "prepared_degraded",
                "project_root": str(worktree.resolve()),
            },
        },
    )

    code, receipt = RUNNER.run_reviewers(
        _args(worktree, policy, packet, template, "audit", run=run)
    )

    assert code == 0
    assert receipt["status"] == "completed"
    prompt = worktree / receipt["assignments"][0]["paths"]["prompt"]
    assert "prepared_degraded" in prompt.read_text(encoding="utf-8")


def _runtime_count(state: Path, provider: str) -> int:
    path = state / f"{provider}-invocations.jsonl"
    if not path.exists():
        return 0
    return sum(
        json.loads(line)["kind"] == "runtime" for line in path.read_text().splitlines()
    )


AUDIT_CANDIDATE = """### AUDIT-CANDIDATE-001
- severity: major
- category: implementation
- disposition: remediation_required
- fingerprint: entrypoint
- evidence: src/main.py is unreachable
- affected_acceptance_ids: [AC-001]
- affected_files: [src/main.py]
- impact: AC-001 is not delivered
- owner: implementation
- closure_test: pytest -q tests/test_main.py"""


REFINEMENT_CANDIDATE = """### RF-CANDIDATE-001
- severity: major
- category: architecture
- fingerprint: delivery-boundary
- evidence: design.md is incomplete
- affected_manifest_ids: [AC-001]
- impact: implementation is blocked
- required_correction: complete the design
- closure_test: inspect design.md
- requires_user: false"""


def test_policy_uses_direct_read_only_provider_commands_without_metadata_paths() -> (
    None
):
    policy = RUNNER.load_policy(POLICY_PATH)
    assert policy["receipt_version"] == 2
    for workflow in policy["workflows"].values():
        assert set(workflow["paths"]) == {"prompt", "output", "log"}
    codex = policy["providers"]["codex"]["command_args"]
    claude = policy["providers"]["claude"]["command_args"]
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert "--output-last-message" in codex
    assert "--print" in claude and "--safe-mode" in claude
    assert "Write,Edit,NotebookEdit,Task,Agent" in claude
    assert claude[claude.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert not {"search", "deps", "path"} & set(
        yaml.safe_load(CODEGRAPH_POLICY.read_text())["query_commands"]
    )


def test_assignments_reject_duplicates_and_unselected_providers(tmp_path: Path) -> None:
    repo, _state, policy_path = _environment(tmp_path)
    assignments = [{"provider": "codex", "mission": "semantic_core"}] * 2
    packet, _template = _packet(repo, "refinement", assignments)
    policy = RUNNER.load_policy(policy_path)
    with pytest.raises(RUNNER.ReviewerError, match="duplicate"):
        RUNNER.assignments_from_packet(
            packet, RUNNER.load_yaml(packet, "packet"), policy, "refinement"
        )


def test_review_rejects_stale_packet_artifact_before_provider_launch(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    artifact = _write(repo / "docs/epics/E-001/design.md", "approved\n")
    packet, template = _packet(
        repo, "refinement", [{"provider": "codex", "mission": "semantic_core"}]
    )
    document = yaml.safe_load(packet.read_text(encoding="utf-8"))
    document["artifact_hashes"] = {
        artifact.relative_to(repo).as_posix(): RUNNER.file_sha256(artifact)
    }
    _dump(packet, document)
    artifact.write_text("drifted\n", encoding="utf-8")

    with pytest.raises(RUNNER.ReviewerError, match="artifact changed"):
        RUNNER.run_reviewers(_args(repo, policy, packet, template, "refinement"))
    assert _runtime_count(state, "codex") == 0


def test_audit_run_publishes_one_receipt_and_no_metadata_sidecars(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", candidate=AUDIT_CANDIDATE)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    row = receipt["assignments"][0]
    assert code == 0 and receipt["status"] == "completed"
    assert receipt["schema_version"] == 2
    assert "source_snapshot" not in receipt
    assert receipt["git_identity"]["unchanged"] is True
    assert "isolation" not in receipt and "codegraph" not in receipt
    assert row["requested_model"] == "gpt-6-astra"
    assert row["requested_reasoning_effort"] == "max"
    assert row["decision"] == "findings"
    assert row["covered_acceptance_ids"] == ["AC-001"]
    assert "bytes" not in row
    assert row["candidates"][0]["fingerprint"] == "entrypoint"
    assert row["candidates"][0]["closure_test"] == "pytest -q tests/test_main.py"
    assert "closure_requirement" not in row["candidates"][0]
    assert set(row["paths"]) == {"prompt", "output", "log"}
    assert (repo / row["paths"]["output"]).is_file()
    assert not list(packet.parent.glob("metadata-*"))
    stored = yaml.safe_load((packet.parent / "reviewer-receipt.yaml").read_text())
    assert stored == receipt


@pytest.mark.parametrize(
    ("covered", "missing", "extra"),
    [
        (["AC-001"], ["AC-002"], []),
        (["AC-001", "AC-002", "AC-999"], [], ["AC-999"]),
    ],
)
def test_audit_coverage_header_must_exactly_match_full_packet(
    tmp_path: Path,
    covered: list[str],
    missing: list[str],
    extra: list[str],
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", covered_acceptance_ids=covered)
    packet, template = _packet(
        repo,
        "audit",
        [{"provider": "codex", "mission": "semantic_core"}],
        required_acceptance_ids=["AC-001", "AC-002"],
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    row = receipt["assignments"][0]
    assert code == 1 and row["status"] == "invalid_output"
    assert f"missing={missing}, extra={extra}" in row["error"]


@pytest.mark.parametrize(
    "headers",
    [[], ["COVERED_ACCEPTANCE_IDS: [AC-001]"] * 2],
)
def test_audit_requires_exactly_one_coverage_header(
    tmp_path: Path, headers: list[str]
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", coverage_headers=headers)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    row = receipt["assignments"][0]
    assert code == 1 and row["status"] == "invalid_output"
    assert "exactly one COVERED_ACCEPTANCE_IDS" in row["error"]


def test_claude_cli_is_direct_and_uses_stdin(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "claude")
    packet, template = _packet(
        repo, "refinement", [{"provider": "claude", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "refinement")
    )
    row = receipt["assignments"][0]
    assert code == 0 and row["transport"] == "claude-print"
    invocation = [
        json.loads(line)
        for line in (state / "claude-invocations.jsonl").read_text().splitlines()
    ]
    runtime = next(value for value in invocation if value["kind"] == "runtime")
    assert "--print" in runtime["args"] and "--model" in runtime["args"]
    allowed = runtime["args"][runtime["args"].index("--allowedTools") + 1]
    query_commands = yaml.safe_load(CODEGRAPH_POLICY.read_text())["query_commands"]
    executable = (state / "codegraph").resolve()
    assert allowed.split(",") == [
        "Read",
        "Glob",
        "Grep",
        *(f"Bash({executable} {command}:*)" for command in query_commands),
    ]
    prompt = repo / row["paths"]["prompt"]
    assert f"`{executable} explore " in prompt.read_text()
    assert row["requested_model"] == "claude-fable-5-1"
    assert "model_execution" not in row


def test_all_assignments_launch_concurrently(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", barrier=2)
    _configure(state, "agy", barrier=2)
    packet, template = _packet(
        repo,
        "audit",
        [
            {"provider": "codex", "mission": "semantic_core"},
            {"provider": "agy", "mission": "semantic_core"},
        ],
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 0
    assert [row["status"] for row in receipt["assignments"]] == [
        "completed",
        "completed",
    ]


def test_agy_fallback_is_explicit_and_only_before_semantic_output(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "agy", rate_primary=True)
    packet, template = _packet(
        repo, "audit", [{"provider": "agy", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    row = receipt["assignments"][0]
    assert code == 0 and len(row["attempts"]) == 2
    assert row["fallback"] == {
        "from_model": "gemini-3.1-pro-high",
        "to_model": "gemini-3.8-flash-high",
        "reason": "rate_or_quota_exhausted_before_semantic_output",
    }


def test_agy_does_not_fallback_after_partial_semantic_output(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "agy", rate_primary=True, partial=True)
    packet, template = _packet(
        repo, "audit", [{"provider": "agy", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    row = receipt["assignments"][0]
    assert code == 1 and row["status"] == "provider_failed"
    assert len(row["attempts"]) == 1 and row["fallback"] is None


def test_provider_usage_is_preserved_raw_without_model_taxonomy(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    raw = {"modelUsage": {"claude-fable-5": {"inputTokens": 10}}, "num_turns": 2}
    _configure(state, "claude", usage_event=raw)
    packet, template = _packet(
        repo, "refinement", [{"provider": "claude", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "refinement")
    )
    assert code == 0
    assert receipt["assignments"][0]["attempts"][0]["provider_reported"] == [raw]


def test_invalid_semantic_output_requires_new_attempt(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", invalid=True)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    args = _args(repo, policy, packet, template, "audit")
    code, receipt = RUNNER.run_reviewers(args)
    assert code == 1 and receipt["assignments"][0]["status"] == "invalid_output"
    with pytest.raises(RUNNER.ReviewerError, match="new review attempt"):
        RUNNER.run_reviewers(
            _args(repo, policy, packet, template, "audit", repair=True)
        )
    assert _runtime_count(state, "codex") == 1


def test_infrastructure_repair_preserves_completed_review(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", candidate=AUDIT_CANDIDATE)
    _configure(state, "agy", error="authentication failed before review")
    packet, template = _packet(
        repo,
        "audit",
        [
            {"provider": "codex", "mission": "semantic_core"},
            {"provider": "agy", "mission": "semantic_core"},
        ],
    )
    args = _args(repo, policy, packet, template, "audit")
    first_code, first = RUNNER.run_reviewers(args)
    assert first_code == 1
    assert [row["status"] for row in first["assignments"]] == [
        "completed",
        "infrastructure_failed_before_review",
    ]
    assert sum(len(row["candidates"]) for row in first["assignments"]) == 1
    with pytest.raises(RUNNER.ReviewerError, match="--repair-infrastructure"):
        RUNNER.run_reviewers(args)
    _configure(state, "agy")
    second_code, second = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "audit", repair=True)
    )
    assert second_code == 0
    assert _runtime_count(state, "codex") == 1
    assert _runtime_count(state, "agy") == 2
    assert (
        second["assignments"][0]["output_sha256"]
        == first["assignments"][0]["output_sha256"]
    )


@pytest.mark.parametrize("changed", ["packet", "template"])
def test_packet_and_template_hashes_are_immutable(tmp_path: Path, changed: str) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    assert RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))[0] == 0
    target = packet if changed == "packet" else template
    if changed == "packet":
        value = yaml.safe_load(target.read_text())
        value["note"] = "changed"
        _dump(target, value)
    else:
        target.write_text(target.read_text() + "\nchanged\n")
    with pytest.raises(RUNNER.ReviewerError, match=f"{changed} changed"):
        RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))


def test_completed_output_hash_is_immutable(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    _code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "audit")
    )
    output = repo / receipt["assignments"][0]["paths"]["output"]
    output.write_text(output.read_text() + "tampered")
    with pytest.raises(RUNNER.ReviewerError, match="changed after receipt"):
        RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))


def test_preflight_barrier_prevents_partial_launch(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "claude", version_fail=True)
    _configure(state, "codex")
    packet, template = _packet(
        repo,
        "refinement",
        [
            {"provider": "claude", "mission": "semantic_core"},
            {"provider": "codex", "mission": "semantic_core"},
        ],
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "refinement")
    )
    assert code == 1
    assert {row["status"] for row in receipt["assignments"]} == {
        "preflight_failed",
        "not_launched_preflight_barrier",
    }
    assert _runtime_count(state, "codex") == 0


def test_timeout_terminates_provider_process_tree_and_is_not_repairable(
    tmp_path: Path,
) -> None:
    repo, state, policy_path = _environment(tmp_path)
    policy = yaml.safe_load(policy_path.read_text())
    policy["providers"]["codex"]["supervisor_timeout_seconds"] = 1
    _dump(policy_path, policy)
    _configure(state, "codex", spawn_child=True, sleep=30)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy_path, packet, template, "audit")
    )
    assert code == 1 and receipt["assignments"][0]["status"] == "timed_out"
    child_pid = int((state / "child-codex.pid").read_text())
    deadline = time.monotonic() + 2
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        try:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    assert (
        not psutil.pid_exists(child_pid)
        or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    )
    with pytest.raises(RUNNER.ReviewerError, match="new review attempt"):
        RUNNER.run_reviewers(
            _args(repo, policy_path, packet, template, "audit", repair=True)
        )


def test_cancellation_terminates_provider_and_writes_canceled_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", sleep=30)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    original = RUNNER.wait_process
    interrupted = False

    def interrupt_once(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(RUNNER, "wait_process", interrupt_once)
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 130 and receipt["status"] == "canceled"
    assert receipt["assignments"][0]["status"] == "canceled"
    assert (packet.parent / "reviewer-receipt.yaml").is_file()


def test_clean_commit_changes_git_identity_and_fails_review(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", commit=True)
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 1
    assert receipt["assignments"][0]["status"] == "completed"
    assert receipt["git_identity"]["unchanged"] is False


def test_run_level_codegraph_state_is_reused_without_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    run = _dump(
        repo / "tmp_debug/run.yaml",
        {
            "codegraph": {
                "status": "degraded",
                "reason": "prepared_degraded",
                "project_root": str(repo),
            }
        },
    )
    monkeypatch.setattr(
        RUNNER.scope_codegraph, "prepare", lambda *_args: pytest.fail("prepare called")
    )
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "audit", run=run)
    )
    assert code == 0 and "codegraph" not in receipt
    prompt = repo / receipt["assignments"][0]["paths"]["prompt"]
    assert "prepared_degraded" in prompt.read_text()


def test_publication_lock_is_nonblocking_and_outputs_are_not_partially_published(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    lock_path = repo / "tmp_debug/scope-mutation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    guard = FileLock(str(lock_path))
    guard.acquire(timeout=0)
    try:
        with pytest.raises(RUNNER.ReviewerError, match="mutation root is busy"):
            RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    finally:
        guard.release()
    assert not (packet.parent / "reviewer-receipt.yaml").exists()
    assert not (packet.parent / "review-codex-semantic-core.md").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_publication_lock_rejects_symlinked_tmp_debug_without_outside_write(
    tmp_path: Path,
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    outside = tmp_path / "outside-runtime"
    outside.mkdir()
    os.symlink(outside, repo / "tmp_debug")
    args = _args(repo, policy, packet, template, "audit")
    args.runtime_dir = repo / "reviewer-runtime"

    with pytest.raises(RUNNER.ReviewerError, match="symlink"):
        RUNNER.run_reviewers(args)

    assert list(outside.iterdir()) == []
    assert not (packet.parent / "reviewer-receipt.yaml").exists()
    assert not (packet.parent / "review-codex-semantic-core.md").exists()


def test_packet_change_during_execution_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    original = RUNNER.wait_process

    def change_packet(*args: object, **kwargs: object) -> dict[str, object]:
        result = original(*args, **kwargs)
        value = yaml.safe_load(packet.read_text())
        value["changed_during_review"] = True
        _dump(packet, value)
        return result

    monkeypatch.setattr(RUNNER, "wait_process", change_packet)
    with pytest.raises(RUNNER.ReviewerError, match="changed during review"):
        RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert not (packet.parent / "reviewer-receipt.yaml").exists()
    assert not (packet.parent / "review-codex-semantic-core.md").exists()


def test_targeted_refinement_requires_exact_packet_fingerprints(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    verification = """### RF-VERIFICATION-001
- fingerprint: delivery-boundary
- outcome: still_open
- evidence: proof still fails
- source_candidate_ids: [paraphrased-source]
- closure_test: paraphrased closure"""
    _configure(state, "codex", candidate=verification, decision="corrections_required")
    packet, template = _packet(
        repo,
        "refinement",
        [{"provider": "codex", "mission": "semantic_core"}],
        review_kind="targeted",
        targeted_fingerprints=["delivery-boundary"],
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "refinement")
    )
    assert code == 0
    assert (
        receipt["assignments"][0]["targeted_verifications"][0]["outcome"]
        == "still_open"
    )
    assert receipt["assignments"][0]["targeted_verifications"][0][
        "source_candidate_ids"
    ] == ["review-000/codex/semantic_core/RF-CANDIDATE-001"]
    assert receipt["assignments"][0]["targeted_verifications"][0][
        "closure_test"
    ] == "run proof"


def test_targeted_refinement_routes_only_source_owned_fingerprints(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    assignments = [
        {"provider": "claude", "mission": "semantic_core"},
        {"provider": "codex", "mission": "semantic_core"},
    ]
    for provider, fingerprint in (("claude", "claude-owned"), ("codex", "codex-owned")):
        _configure(
            state,
            provider,
            candidate=(
                "### RF-VERIFICATION-001\n"
                f"- fingerprint: {fingerprint}\n"
                "- outcome: verified\n"
                "- evidence: closure inspected"
            ),
            decision="approved",
        )
    packet, template = _packet(
        repo,
        "refinement",
        assignments,
        review_kind="targeted",
        targeted_fingerprints=["claude-owned", "codex-owned"],
        targeted_source_ids=[
            ["review-000/claude/semantic_core/RF-CANDIDATE-001"],
            ["review-000/codex/semantic_core/RF-CANDIDATE-001"],
        ],
        targeted_required_assignments=[[assignments[0]], [assignments[1]]],
    )

    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "refinement")
    )

    assert code == 0
    for row in receipt["assignments"]:
        expected = f"{row['provider']}-owned"
        assert [item["fingerprint"] for item in row["targeted_verifications"]] == [
            expected
        ]
        prompt = (repo / row["paths"]["prompt"]).read_text(encoding="utf-8")
        assert expected in prompt
        assert f"{'codex' if row['provider'] == 'claude' else 'claude'}-owned" not in prompt


def test_targeted_audit_requires_exact_packet_fingerprints(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    verification = """### AUDIT-VERIFICATION-001
- fingerprint: entrypoint
- outcome: verified
- evidence: closure proof and corrected entrypoint inspected"""
    _configure(
        state,
        "codex",
        candidate=verification,
        decision="pass",
        covered_acceptance_ids=["AC-002"],
    )
    packet, template = _packet(
        repo,
        "audit",
        [{"provider": "codex", "mission": "semantic_core"}],
        review_kind="targeted",
        targeted_fingerprints=["entrypoint"],
        required_acceptance_ids=["AC-001", "AC-002"],
        targeted_acceptance_ids=[["AC-002"]],
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 0
    row = receipt["assignments"][0]
    assert row["decision"] == "pass"
    assert row["covered_acceptance_ids"] == ["AC-002"]
    assert row["targeted_verifications"][0]["closure_test"] == (
        "pytest -q tests/test_main.py"
    )


def test_targeted_verification_metadata_is_supplied_by_packet(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    verification = """### AUDIT-VERIFICATION-001
- fingerprint: entrypoint
- outcome: verified
- evidence: closure proof inspected"""
    _configure(state, "codex", candidate=verification, decision="pass")
    packet, template = _packet(
        repo,
        "audit",
        [{"provider": "codex", "mission": "semantic_core"}],
        review_kind="targeted",
        targeted_fingerprints=["entrypoint"],
    )
    code, receipt = RUNNER.run_reviewers(
        _args(repo, policy, packet, template, "audit")
    )
    row = receipt["assignments"][0]
    assert code == 0 and row["status"] == "completed"
    assert row["targeted_verifications"][0]["source_candidate_ids"] == [
        "review:audit-001:codex:semantic_core:AUDIT-CANDIDATE-001"
    ]
    assert row["targeted_verifications"][0]["closure_test"] == (
        "pytest -q tests/test_main.py"
    )


def test_conclusion_must_match_structured_findings(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex", candidate=AUDIT_CANDIDATE, decision="pass")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 1
    assert receipt["assignments"][0]["status"] == "invalid_output"
    assert "contradicts evidence" in receipt["assignments"][0]["error"]


def test_audit_reviewer_cannot_self_authorize_accepted_risk(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    candidate = AUDIT_CANDIDATE.replace(
        "disposition: remediation_required", "disposition: accepted_risk"
    )
    _configure(state, "codex", candidate=candidate, decision="unverified")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code, receipt = RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))
    assert code == 1
    assert "unsupported disposition" in receipt["assignments"][0]["error"]


def test_existing_unreceipted_output_fails_closed(tmp_path: Path) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    _write(packet.parent / "review-codex-semantic-core.md", "stale")
    with pytest.raises(RUNNER.ReviewerError, match="without a receipt"):
        RUNNER.run_reviewers(_args(repo, policy, packet, template, "audit"))


def test_main_preflight_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, state, policy = _environment(tmp_path)
    _configure(state, "codex")
    packet, template = _packet(
        repo, "audit", [{"provider": "codex", "mission": "semantic_core"}]
    )
    code = RUNNER.main(
        [
            "preflight",
            "--workflow",
            "audit",
            "--packet",
            str(packet),
            "--repo-root",
            str(repo),
            "--policy",
            str(policy),
            "--template",
            str(template),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        code == 0 and result["providers"][0]["toolchain"]["version"] == "codex fake 1.0"
    )

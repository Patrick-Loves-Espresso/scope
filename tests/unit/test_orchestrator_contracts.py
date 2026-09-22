from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EPIC_REFINE = ROOT / "src_shared/commands/epic_refine.md"
REFINEMENT_REVIEWER = (
    ROOT / "src_shared/commands/epic_refine/reviewer-refinement.md"
)
AUDIT_EPIC = ROOT / "src_shared/commands/audit_epic.md"
DECISION = ROOT / "src_shared/commands/decision.md"
LESSON = ROOT / "src_shared/commands/lesson.md"
CODEX_IMPLEMENT = ROOT / "src_codex/commands/implement.md"
CLAUDE_IMPLEMENT = ROOT / "src_claude/commands/implement.md"
SHARED_WRAP = ROOT / "src_shared/commands/wrap_epic.md"
DELIVERY_MANIFEST_TEMPLATE = (
    ROOT
    / "src_shared/skills/project-documentation/templates-technical-arc42-c4/epic/delivery-manifest.yaml"
)
DESIGN_TEMPLATE = (
    ROOT
    / "src_shared/skills/project-documentation/templates-technical-arc42-c4/epic/design.md"
)
PUBLIC_WORKFLOWS = (EPIC_REFINE, AUDIT_EPIC, CODEX_IMPLEMENT, CLAUDE_IMPLEMENT)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(text: str, *phrases: str) -> None:
    normalized = " ".join(text.split()).lower()
    for phrase in phrases:
        assert " ".join(phrase.split()).lower() in normalized


@pytest.mark.parametrize("path", PUBLIC_WORKFLOWS)
def test_public_workflows_delegate_model_work(path: Path) -> None:
    command = read(path)
    require(command, "sole user-facing orchestrator", "scope-worker.py")
    assert "codex exec" not in command
    assert "claude --model" not in command
    assert "agy --model" not in command
    assert "\nskills:" not in command


@pytest.mark.parametrize("path", PUBLIC_WORKFLOWS)
def test_public_workflows_use_the_small_worker_lifecycle(path: Path) -> None:
    command = read(path)
    require(
        command,
        '"$WORKER" init',
        "status",
        "recover",
        "cancel",
    )
    for removed in (
        " operate ",
        " set-profile ",
        " unattributed_change_incidents",
        " metadata-job",
        " materialize_handoff",
        " finalize_candidate",
    ):
        assert removed not in f" {command.lower()} "


def test_refinement_has_two_authority_gates_and_one_review_boundary() -> None:
    command = read(EPIC_REFINE)
    require(
        command,
        "product_contract",
        "final_handoff",
        "create-review-packet",
        "apply-review-receipt",
        "one full immutable packet",
        "targeted packet",
        "only independent targeted evidence",
        "documentation obligations",
        "one manifest row",
        "validate --phase handoff",
    )
    assert "Gate 0" not in command
    assert "Gate 1" not in command
    assert "Gate 2" not in command
    assert "Gate 3" not in command


def test_refinement_reviewer_preserves_existing_fingerprint_semantics() -> None:
    prompt = read(REFINEMENT_REVIEWER)
    require(
        prompt,
        "inspect `refinement-findings.yaml`",
        "reuse it only by copying its stable semantics exactly",
        "use a distinct fingerprint",
    )


@pytest.mark.parametrize("path", (EPIC_REFINE, AUDIT_EPIC))
def test_review_workflow_invocation_authorizes_configured_packet_transmission(
    path: Path,
) -> None:
    command = read(path)
    require(
        command,
        "implicitly authorizes transmission",
        "hash-bound review packet and only its declared artifacts",
        "configured reviewer policy, profile, and set",
        "including external-provider CLIs",
        "Do not ask for separate transmission approval",
        "does not authorize other providers, unbound files, credentials, or reviewer writes",
    )


def test_audit_is_read_only_and_source_bounded() -> None:
    command = read(AUDIT_EPIC)
    require(
        command,
        "verify-evidence",
        "create no audit attempt",
        "one full and one targeted attempt",
        "all-provider preflight barrier",
        "source-bounded synthesis",
        "requires every deterministic, reviewer, and active-ledger source exactly once",
        "direct audit never launches a write implementation worker",
        "--phase complete",
    )
    assert "--access workspace-write" not in command


def normalize_implementation(text: str) -> str:
    replacements = (
        ("scope:implement", "PLATFORM_IMPLEMENT"),
        ("/implement", "PLATFORM_IMPLEMENT"),
        ("scope:wrap_epic", "PLATFORM_WRAP"),
        ("/wrap_epic", "PLATFORM_WRAP"),
        ("plugins/scope", "PLATFORM_SCOPE"),
        (".claude", "PLATFORM_SCOPE"),
        ('PROVIDER="codex"', 'PROVIDER="PLATFORM"'),
        ('PROVIDER="claude"', 'PROVIDER="PLATFORM"'),
        ("AGENTS.md", "REPOSITORY_INSTRUCTIONS.md"),
        ("CLAUDE.md", "REPOSITORY_INSTRUCTIONS.md"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return " ".join(text.split())


def test_implementation_workflows_are_behavioral_mirrors() -> None:
    assert normalize_implementation(read(CODEX_IMPLEMENT)) == normalize_implementation(
        read(CLAUDE_IMPLEMENT)
    )


@pytest.mark.parametrize("path", (CODEX_IMPLEMENT, CLAUDE_IMPLEMENT))
def test_implementation_uses_bounded_story_workers_and_safe_dependency_merge(
    path: Path,
) -> None:
    command = read(path)
    require(
        command,
        "wip/{epic-id}",
        "scope-dependency-merge.py",
        "It accepts no branch tip",
        "one writer at a time",
        "required_proof_ids",
        "durable implementation evidence",
        "verify-proofs",
        "implementation/audit_remediation",
        "same worker/reviewer profiles",
        "documentation obligation",
        "scope-wrap-finalize.py",
        '"$WRAP_FINALIZER" seal',
        "Do not commit implementation/remediation",
    )


def test_wrap_is_one_shared_lean_deterministic_command() -> None:
    command = read(SHARED_WRAP)
    require(
        command,
        "scope:wrap_epic {epic-id}",
        "/wrap_epic {epic-id}",
        "scope-wrap-finalize.py",
        "implement` is the sole owner",
        "resume `scope:implement {epic-id}`",
        "/implement {epic-id}` in Claude",
        '"$FINALIZER" verify',
        '"$FINALIZER" prepare',
        '"$FINALIZER" commit-merge',
        "NOT_READY",
        "ABANDONMENT_DEFERRED",
        "approved-staged-tree",
        "approved-main-head",
        "approved-main-branch",
        "fixed closure label",
    )
    assert not (ROOT / "src_codex/commands/wrap_epic.md").exists()
    assert not (ROOT / "src_claude/commands/wrap_epic.md").exists()
    assert "\nskills:" not in command
    assert '"$FINALIZER" seal' not in command
    for removed in (
        "git add",
        "git commit",
        "git merge",
        "codegraph init",
        "codegraph sync",
        ".scope/tracking/commands",
        "agent_summaries",
        "generate implementation summary",
    ):
        assert removed not in command.lower()


def test_manifest_v2_templates_expose_documentation_obligations() -> None:
    manifest = read(DELIVERY_MANIFEST_TEMPLATE)
    design = read(DESIGN_TEMPLATE)
    require(manifest, "schema_version: 3", "documentation_obligations: []", "execution:", "depends_on: []")
    require(design, "## Documentation Obligations", "DOC-NNN")


def test_closeout_commands_do_not_depend_on_removed_tracking_surfaces() -> None:
    for path in (SHARED_WRAP, DECISION, LESSON):
        assert ".scope/tracking/commands" not in read(path)
    for path in (DECISION, LESSON):
        assert "agent_summaries" not in read(path)


def normalize_developer(text: str) -> str:
    body = text.split("# Developer Agent", maxsplit=1)[1]
    return normalize_implementation(body)


def test_standalone_developer_roles_are_small_platform_mirrors() -> None:
    codex_path = ROOT / "src_codex/agents/developer.md"
    claude_path = ROOT / "src_claude/agents/developer.md"
    codex = read(codex_path)
    claude = read(claude_path)
    assert "model: gpt-6-astra" in codex
    assert "model_reasoning_effort: max" in codex
    assert "model: claude-fable-5-1" in claude
    assert normalize_developer(codex) == normalize_developer(claude)
    require(codex, "standalone bounded developer role", "at most four times")
    for removed in (
        "agent-lifecycle",
        "TaskList",
        "TaskGet",
        "TaskUpdate",
        "session-id-finder",
        "agent_summaries",
    ):
        assert removed not in codex
        assert removed not in claude


def test_standalone_product_owner_uses_only_current_installed_contracts() -> None:
    product_owner = read(ROOT / "src_shared/agents/product-owner.md")
    require(
        product_owner,
        "standalone bounded product-owner role",
        "skills: project-documentation",
        "all currently discoverable questions",
        "acceptance and decision IDs added or affected",
    )
    for removed in (
        "agent-lifecycle",
        "agent-summary-complex",
        "session-id-finder",
        "TaskList",
        "TaskGet",
        "TaskUpdate",
        "agent_summaries",
        ".scope/{epic-id}",
    ):
        assert removed not in product_owner


def test_worker_prompts_stay_bounded_and_non_conversational() -> None:
    for name in (
        "refinement-worker.md",
        "implementation-worker.md",
        "diagnostic-worker.md",
    ):
        prompt = read(ROOT / "src_shared/workers" / name)
        require(prompt, "fresh, bounded", "communicate with the user")
        assert len(prompt.splitlines()) <= 40

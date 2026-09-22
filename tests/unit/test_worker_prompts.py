from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS = REPO_ROOT / "src_shared/workers"


def _text(name: str) -> str:
    return (WORKERS / name).read_text(encoding="utf-8")


def test_prompts_are_small_bounded_and_batch_questions() -> None:
    for name in ("refinement-worker.md", "implementation-worker.md", "diagnostic-worker.md"):
        text = _text(name)
        normalized = " ".join(text.split())
        assert len(text.splitlines()) < 60
        assert "bounded" in normalized
        assert "every" in normalized and "question" in normalized
        assert "one question at a time" in normalized
        assert "worker-result v2" in normalized
        assert "notification" in normalized


def test_removed_anchor_and_transport_grammar_is_absent() -> None:
    combined = "\n".join(_text(path.name) for path in WORKERS.glob("*.md"))
    for obsolete in ("question_discovery", "path#anchor", "developer_discovered", "next_action"):
        assert obsolete not in combined


def test_refinement_handoff_delegates_baselines_to_executor() -> None:
    text = _text("refinement-worker.md")
    for value in ("design_handoff", "existing_runnable", "implementation_created", "external_blocked", "runner executes"):
        assert value in text
    assert "do not manufacture" in text


def test_refinement_correction_resolves_the_complete_open_batch() -> None:
    text = _text("refinement-worker.md")
    normalized = " ".join(text.split())
    assert "every `status: open` finding" in normalized
    assert "targeted reviewer receipt" in normalized
    assert "latest outcome is `still_open`" in normalized
    assert "Do not stop after one" in normalized
    assert "return all of it" in normalized


def test_implementation_proof_evidence_is_runner_owned() -> None:
    text = " ".join(_text("implementation-worker.md").split())
    assert "return `proof_evidence: []`" in text
    assert "Scope executes and records authoritative proofs" in text
    assert "Never create or edit `implementation-evidence.yaml`" in text
    assert "runner promotes the observed paths" in text

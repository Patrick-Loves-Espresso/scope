#!/usr/bin/env python3
"""Launch Scope's independent reviewers from a durable review packet."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

import psutil
import yaml

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)
import scope_codegraph  # noqa: E402
import scope_git  # noqa: E402


class ReviewerError(ValueError):
    """Raised when a reviewer launch contract cannot be satisfied."""


@dataclass(frozen=True)
class Assignment:
    provider: str
    mission: str


@dataclass(frozen=True)
class AssignmentPaths:
    prompt: Path
    output: Path
    draft: Path
    log: Path


@dataclass
class RunningProcess:
    process: subprocess.Popen[bytes]
    started_at: str
    started_monotonic: float
    log_offset: int
    model: str


@dataclass(frozen=True)
class ReviewContract:
    """Mechanically parsed reviewer conclusion and its decision evidence."""

    decision: str
    questions: list[str]
    unverified_evidence: list[str]
    covered_acceptance_ids: list[str]


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewerError(f"{context} must be a mapping")
    return dict(value)


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewerError(f"{context} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, context: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ReviewerError(f"{context} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ReviewerError(f"{context} must not be empty")
    return [item.strip() for item in value]


def load_yaml(path: Path, context: str) -> dict[str, Any]:
    if not path.is_file():
        raise ReviewerError(f"missing {context}: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReviewerError(f"invalid {context} {path}: {exc}") from exc
    return _mapping(value, str(path))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _text_sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _nested(value: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _format(value: str, context: Mapping[str, Any], label: str) -> str:
    try:
        return value.format_map(dict(context))
    except (KeyError, ValueError) as exc:
        raise ReviewerError(f"cannot render {label}: {exc}") from exc


def _inside(root: Path, value: Path, context: str) -> Path:
    root = root.resolve()
    resolved = value.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReviewerError(
            f"{context} must be inside repository root: {resolved}"
        ) from exc
    return resolved


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReviewerError(f"path is outside repository root: {path}") from exc


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_from(base: Path, value: str, root: Path, context: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return _inside(root, path, context)


def policy_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "reviewer-policy.yaml"


def load_policy(path: Path) -> dict[str, Any]:
    policy = load_yaml(path.resolve(), "reviewer policy")
    if policy.get("schema_version") != 2:
        raise ReviewerError("unsupported reviewer policy schema_version")
    workflows = _mapping(policy.get("workflows"), "reviewer policy workflows")
    providers = _mapping(policy.get("providers"), "reviewer policy providers")
    allowed_efforts = {"low", "medium", "high", "xhigh", "max"}
    allowed_models = {provider: set(_string_list(values, f"allowed models for {provider}"))
                      for provider, values in _mapping(policy.get("allowed_models"), "allowed_models").items()}
    for section in ("reviewers", "reviewers_on_budget"):
        profiles = _mapping(policy.get(section), f"reviewer policy {section}")
        for workflow, workflow_config in workflows.items():
            configured = _mapping(profiles.get(workflow), f"{section}.{workflow}")
            allowed = set(
                _string_list(
                    _mapping(workflow_config, f"workflow {workflow}").get(
                        "allowed_providers"
                    ),
                    f"{workflow} allowed_providers",
                )
            )
            if set(configured) != allowed:
                raise ReviewerError(
                    f"{section}.{workflow} must configure exactly {sorted(allowed)}"
                )
            for provider, raw in configured.items():
                selected = _mapping(raw, f"{section}.{workflow}.{provider}")
                if set(selected) != {"model", "reasoning_effort"}:
                    raise ReviewerError(
                        f"{section}.{workflow}.{provider} must contain only model/effort"
                    )
                model = _string(selected.get("model"), f"{provider} model")
                if model not in allowed_models.get(provider, set()):
                    raise ReviewerError(
                        f"invalid reviewer model for {section}.{workflow}.{provider}: {model}"
                    )
                effort = _string(
                    selected.get("reasoning_effort"), f"{provider} reasoning_effort"
                )
                if effort not in allowed_efforts:
                    raise ReviewerError(
                        f"invalid reviewer effort for {section}.{workflow}.{provider}: {effort}"
                    )
                if provider not in providers:
                    raise ReviewerError(f"unregistered reviewer provider: {provider}")
    reviewer_sets = _mapping(policy.get("reviewer_sets"), "reviewer_sets")
    for workflow, workflow_config in workflows.items():
        configured_sets = _mapping(
            reviewer_sets.get(workflow), f"reviewer_sets.{workflow}"
        )
        if set(configured_sets) != {"standard", "expanded"}:
            raise ReviewerError(
                f"reviewer_sets.{workflow} must define standard and expanded"
            )
        allowed = set(
            _string_list(
                _mapping(workflow_config, f"workflow {workflow}").get(
                    "allowed_providers"
                ),
                f"{workflow} allowed_providers",
            )
        )
        for name, values in configured_sets.items():
            selected = _string_list(values, f"reviewer_sets.{workflow}.{name}")
            if (
                not selected
                or len(selected) != len(set(selected))
                or not set(selected).issubset(allowed)
            ):
                raise ReviewerError(
                    f"reviewer_sets.{workflow}.{name} contains invalid providers"
                )
        if not set(configured_sets["standard"]).issubset(
            set(configured_sets["expanded"])
        ):
            raise ReviewerError(
                f"reviewer_sets.{workflow}.expanded must include the standard set"
            )
    return policy


def reviewer_provider_config(
    policy: Mapping[str, Any],
    workflow: str,
    provider: str,
    reviewer_profile: str,
) -> dict[str, Any]:
    section = {"default": "reviewers", "budget": "reviewers_on_budget"}.get(
        reviewer_profile
    )
    if section is None:
        raise ReviewerError(f"unsupported reviewer profile: {reviewer_profile}")
    base = _mapping(
        _mapping(policy.get("providers"), "reviewer policy providers").get(provider),
        f"provider {provider}",
    )
    selected = _mapping(
        _mapping(
            _mapping(policy.get(section), section).get(workflow),
            f"{section}.{workflow}",
        ).get(provider),
        f"{section}.{workflow}.{provider}",
    )
    return {**base, **selected}


def load_codegraph_policy(path: Path) -> dict[str, Any]:
    try:
        return scope_codegraph.load_policy(path.resolve())
    except scope_codegraph.CodeGraphPolicyError as exc:
        raise ReviewerError(str(exc)) from exc


def workflow_policy(policy: Mapping[str, Any], workflow: str) -> dict[str, Any]:
    workflows = _mapping(policy.get("workflows"), "reviewer policy workflows")
    if workflow not in workflows:
        raise ReviewerError(f"unsupported reviewer workflow: {workflow}")
    return _mapping(workflows[workflow], f"reviewer workflow {workflow}")


def _assignment_values(
    packet_path: Path,
    packet: Mapping[str, Any],
    configured_sources: Any,
) -> list[Any]:
    if not isinstance(configured_sources, list) or not configured_sources:
        raise ReviewerError("assignment_sources must be a non-empty list")
    for index, raw_source in enumerate(configured_sources, start=1):
        source = _mapping(raw_source, f"assignment_sources[{index}]")
        filename = _string(source.get("file"), f"assignment_sources[{index}].file")
        key = _string(source.get("key"), f"assignment_sources[{index}].key")
        if filename == "packet":
            content = packet
        else:
            candidate = packet_path.parent / filename
            if not candidate.is_file():
                continue
            content = load_yaml(candidate, "assignment source")
        value = _nested(content, key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ReviewerError(f"assignment source {filename}:{key} must be a list")
        return value
    raise ReviewerError(f"no configured assignment source exists for {packet_path}")


def assignments_from_packet(
    packet_path: Path,
    packet: Mapping[str, Any],
    policy: Mapping[str, Any],
    workflow: str,
    reviewer_set: str = "standard",
) -> list[Assignment]:
    workflow_config = workflow_policy(policy, workflow)
    values = _assignment_values(
        packet_path, packet, workflow_config.get("assignment_sources")
    )
    pattern = re.compile(
        _string(policy.get("identifier_pattern"), "identifier_pattern")
    )
    providers = set(
        _string_list(workflow_config.get("allowed_providers"), "allowed_providers")
    )
    selected_providers = set(
        _string_list(
            _mapping(
                _mapping(policy.get("reviewer_sets"), "reviewer_sets").get(workflow),
                f"reviewer_sets.{workflow}",
            ).get(reviewer_set),
            f"reviewer_sets.{workflow}.{reviewer_set}",
        )
    )
    missions = set(
        _string_list(workflow_config.get("allowed_missions"), "allowed_missions")
    )
    assignments: list[Assignment] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(values, start=1):
        row = _mapping(raw, f"assignments[{index}]")
        provider = _string(row.get("provider"), f"assignments[{index}].provider")
        mission = _string(row.get("mission"), f"assignments[{index}].mission")
        if not pattern.fullmatch(provider) or not pattern.fullmatch(mission):
            raise ReviewerError(
                f"assignment has unsafe provider/mission: {provider}/{mission}"
            )
        if provider not in providers:
            raise ReviewerError(f"provider {provider!r} is not allowed for {workflow}")
        if provider not in selected_providers:
            raise ReviewerError(
                f"provider {provider!r} is not enabled by reviewer set {reviewer_set}"
            )
        if mission not in missions:
            raise ReviewerError(f"mission {mission!r} is not allowed for {workflow}")
        key = (provider, mission)
        if key in seen:
            raise ReviewerError(f"duplicate reviewer assignment: {provider}/{mission}")
        seen.add(key)
        assignments.append(Assignment(provider, mission))
    return assignments


def _review_id(packet: Mapping[str, Any], workflow_config: Mapping[str, Any]) -> str:
    keys = _string_list(
        workflow_config.get("review_id_keys"), "review_id_keys", allow_empty=False
    )
    for key in keys:
        value = _nested(packet, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ReviewerError(
        f"review packet is missing review identifier keys: {', '.join(keys)}"
    )


def _base_context(
    packet: Mapping[str, Any],
    packet_path: Path,
    repo_root: Path,
    review_id: str,
    path_component_pattern: str,
) -> dict[str, str]:
    try:
        component_pattern = re.compile(path_component_pattern)
    except re.error as exc:
        raise ReviewerError(f"invalid path_component_pattern: {exc}") from exc
    epic_id = _string(packet.get("epic_id"), "review packet epic_id")
    if not component_pattern.fullmatch(epic_id) or not component_pattern.fullmatch(
        review_id
    ):
        raise ReviewerError(
            "epic_id and review identifier must be safe path components"
        )
    return {
        "epic_id": epic_id,
        "review_id": review_id,
        "repo_root": str(repo_root.resolve()),
        "packet_path": str(packet_path.resolve()),
    }


def resolve_template(
    workflow_config: Mapping[str, Any],
    repo_root: Path,
    override: Path | None,
) -> Path:
    if override is not None:
        return _resolve_from(repo_root, str(override), repo_root, "reviewer template")
    configured = _string(workflow_config.get("template"), "workflow template")
    installation_root = Path(__file__).resolve().parents[1]
    return _inside(
        installation_root,
        Path(__file__).resolve().parent / configured,
        "configured reviewer template",
    )


def materialize_template(
    policy: Mapping[str, Any],
    packet_path: Path,
    repo_root: Path,
    template_path: Path,
) -> Path:
    if _is_inside(repo_root, template_path):
        return template_path
    paths = _mapping(policy.get("paths"), "reviewer policy paths")
    configured = _string(paths.get("template_snapshot"), "paths.template_snapshot")
    snapshot = _resolve_from(
        packet_path.parent,
        configured,
        repo_root,
        "reviewer template snapshot",
    )
    content = template_path.read_bytes()
    if snapshot.exists():
        if not snapshot.is_file() or snapshot.read_bytes() != content:
            raise ReviewerError(
                "reviewer template snapshot differs from the installed template"
            )
        return snapshot
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    temporary = snapshot.with_name(f".{snapshot.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, snapshot)
    finally:
        temporary.unlink(missing_ok=True)
    return snapshot


def resolve_runtime_dir(
    policy: Mapping[str, Any],
    repo_root: Path,
    base_context: Mapping[str, str],
    override: Path | None,
) -> Path:
    if override is not None:
        return _resolve_from(
            repo_root, str(override), repo_root, "reviewer runtime directory"
        )
    paths = _mapping(policy.get("paths"), "reviewer policy paths")
    configured = _format(
        _string(paths.get("runtime_directory"), "paths.runtime_directory"),
        base_context,
        "runtime directory",
    )
    return _resolve_from(repo_root, configured, repo_root, "reviewer runtime directory")


def resolve_receipt(
    policy: Mapping[str, Any],
    packet_path: Path,
    repo_root: Path,
    override: Path | None,
) -> Path:
    if override is not None:
        return _resolve_from(repo_root, str(override), repo_root, "reviewer receipt")
    paths = _mapping(policy.get("paths"), "reviewer policy paths")
    configured = _string(paths.get("receipt"), "paths.receipt")
    return _resolve_from(packet_path.parent, configured, repo_root, "reviewer receipt")


def assignment_paths(
    assignment: Assignment,
    workflow_config: Mapping[str, Any],
    packet_path: Path,
    runtime_dir: Path,
    repo_root: Path,
    base_context: Mapping[str, str],
) -> AssignmentPaths:
    configured = _mapping(workflow_config.get("paths"), "workflow paths")
    context = {
        **base_context,
        "provider": assignment.provider,
        "mission": assignment.mission,
        "mission_slug": assignment.mission.replace("_", "-"),
    }
    values: dict[str, Path] = {}
    for name in ("prompt", "output", "log"):
        rendered = _format(
            _string(configured.get(name), f"paths.{name}"), context, name
        )
        base = packet_path.parent if name == "output" else runtime_dir
        values[name] = _resolve_from(base, rendered, repo_root, f"reviewer {name}")
    values["draft"] = _inside(
        repo_root,
        runtime_dir / f"draft-{values['output'].name}",
        "reviewer draft output",
    )
    if len(set(values.values())) != len(values):
        raise ReviewerError(
            f"reviewer paths overlap for {assignment.provider}/{assignment.mission}"
        )
    return AssignmentPaths(**values)


def render_prompt(
    template: str,
    workflow_config: Mapping[str, Any],
    assignment: Assignment,
    paths: AssignmentPaths,
    base_context: Mapping[str, str],
    codegraph_state: Mapping[str, Any] | None = None,
    targeted_fingerprints: Sequence[str] = (),
) -> str:
    context = {
        **base_context,
        "provider": assignment.provider,
        "mission": assignment.mission,
        "mission_slug": assignment.mission.replace("_", "-"),
        "output_path": str(paths.output.resolve()),
    }
    placeholders = _mapping(
        workflow_config.get("placeholders"), "workflow placeholders"
    )
    rendered = template
    for placeholder, raw_value in placeholders.items():
        value = _format(
            _string(raw_value, f"placeholder {placeholder}"), context, placeholder
        )
        rendered = rendered.replace("{{" + str(placeholder) + "}}", value)
    remaining = sorted(set(re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", rendered)))
    if remaining:
        raise ReviewerError(
            f"reviewer template has unresolved placeholders: {', '.join(remaining)}"
        )
    if targeted_fingerprints:
        assigned = "\n".join(f"- `{value}`" for value in targeted_fingerprints)
        rendered = (
            f"{rendered.rstrip()}\n\n## Assigned Targeted Fingerprints\n\n"
            "Return one verification for each fingerprint below and no others. "
            "The runner supplies its immutable source IDs and closure text.\n\n"
            f"{assigned}"
        )
    if codegraph_state is not None:
        rendered = f"{rendered.rstrip()}\n\n{scope_codegraph.prompt_instructions(codegraph_state)}"
    return rendered


def _field_values(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError:
        parsed = value
    values = parsed if isinstance(parsed, list) else [parsed]
    return [str(item).strip() for item in values if str(item).strip()]


def _section(text: str, title: str) -> str:
    matches = list(re.finditer(rf"(?m)^##\s+{re.escape(title)}\s*$", text))
    if len(matches) != 1:
        raise ReviewerError(
            f"review output must contain exactly one ## {title} section"
        )
    start = matches[0].end()
    following = re.search(r"(?m)^##\s+", text[start:])
    return text[start : start + following.start() if following else len(text)].strip()


def _items(body: str, label: str) -> list[str]:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) == 1 and re.fullmatch(r"[-*]?\s*None\.?", lines[0], re.I):
        return []
    if not lines or any(not re.match(r"^[-*]\s+\S", line) for line in lines):
        raise ReviewerError(f"review output {label} must be None or Markdown bullets")
    return [re.sub(r"^[-*]\s+", "", line).strip() for line in lines]


def _records(body: str, prefix: str) -> list[tuple[str, dict[str, str], str]]:
    headings = list(
        re.finditer(rf"(?m)^###\s+({re.escape(prefix)}[A-Za-z0-9_-]+)\s*$", body)
    )
    if not headings:
        if not re.fullmatch(r"None\.?", body.strip(), re.I):
            raise ReviewerError(f"{prefix} section must be None or structured records")
        return []
    records: list[tuple[str, dict[str, str], str]] = []
    seen: set[str] = set()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        raw = body[heading.start() : end].rstrip() + "\n"
        fields: dict[str, str] = {}
        current: str | None = None
        for line in raw.splitlines()[1:]:
            match = re.match(r"^-\s+([a-z_]+):\s*(.*)$", line)
            if match:
                current = match.group(1)
                fields[current] = match.group(2).strip()
            elif current and line.strip() and not line.startswith("#"):
                fields[current] = f"{fields[current]} {line.strip()}".strip()
        source_id = heading.group(1)
        if source_id in seen:
            raise ReviewerError(f"duplicate review record ID: {source_id}")
        seen.add(source_id)
        records.append((source_id, fields, raw))
    return records


def _required(fields: Mapping[str, str], name: str, source_id: str) -> str:
    value = fields.get(name, "").strip()
    if not value:
        raise ReviewerError(f"candidate {source_id} is missing {name}")
    return value


def extract_candidates(text: str, assignment: Assignment) -> list[dict[str, Any]]:
    workflow = (
        "audit"
        if re.search(r"(?m)^##\s+Finding Candidates\s*$", text)
        else "refinement"
    )
    title = "Finding Candidates" if workflow == "audit" else "Findings"
    prefix = "AUDIT-CANDIDATE-" if workflow == "audit" else "RF-CANDIDATE-"
    result: list[dict[str, Any]] = []
    for source_id, fields, raw in _records(_section(text, title), prefix):
        candidate: dict[str, Any] = {
            "source_id": source_id,
            "provider": assignment.provider,
            "mission": assignment.mission,
            "severity": _required(fields, "severity", source_id),
            "category": _required(fields, "category", source_id),
            "fingerprint": _required(fields, "fingerprint", source_id),
            "evidence": _required(fields, "evidence", source_id),
            "impact": _required(fields, "impact", source_id),
            "closure_test": _required(fields, "closure_test", source_id),
            "content_sha256": _text_sha256(raw),
        }
        if workflow == "audit":
            acceptance_ids = _field_values(fields.get("affected_acceptance_ids", ""))
            affected_files = _field_values(fields.get("affected_files", ""))
            if not acceptance_ids or not affected_files:
                raise ReviewerError(
                    f"candidate {source_id} is missing affected audit surfaces"
                )
            candidate.update(
                {
                    "disposition": _required(fields, "disposition", source_id),
                    "affected_acceptance_ids": acceptance_ids,
                    "affected_files": affected_files,
                    "owner": _required(fields, "owner", source_id),
                }
            )
        else:
            manifest_ids = _field_values(fields.get("affected_manifest_ids", ""))
            requires_user = _required(fields, "requires_user", source_id).lower()
            if not manifest_ids or requires_user not in {"true", "false"}:
                raise ReviewerError(
                    f"candidate {source_id} has invalid refinement fields"
                )
            candidate.update(
                {
                    "affected_manifest_ids": manifest_ids,
                    "required_correction": _required(
                        fields, "required_correction", source_id
                    ),
                    "requires_user": requires_user == "true",
                }
            )
        result.append(candidate)
    return result


def extract_targeted_verifications(
    text: str, assignment: Assignment, *, prefix: str = "RF-VERIFICATION-"
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_id, fields, raw in _records(
        _section(text, "Targeted Verification"), prefix
    ):
        outcome = _required(fields, "outcome", source_id)
        if outcome not in {"verified", "still_open"}:
            raise ReviewerError(f"targeted verification {source_id} has invalid fields")
        result.append(
            {
                "source_id": source_id,
                "provider": assignment.provider,
                "mission": assignment.mission,
                "fingerprint": _required(fields, "fingerprint", source_id),
                "outcome": outcome,
                "evidence": _required(fields, "evidence", source_id),
                "content_sha256": _text_sha256(raw),
            }
        )
    return result


def _decision(text: str) -> str:
    decisions = re.findall(r"(?m)^DECISION:\s*([a-z][a-z0-9_]*)\s*$", text)
    if len(decisions) != 1:
        raise ReviewerError("review output must contain exactly one DECISION")
    return decisions[0]


def _covered_acceptance_ids(text: str) -> list[str]:
    values = re.findall(r"(?m)^COVERED_ACCEPTANCE_IDS:\s*(.*?)\s*$", text)
    if len(values) != 1:
        raise ReviewerError(
            "audit review output must contain exactly one COVERED_ACCEPTANCE_IDS header"
        )
    try:
        parsed = yaml.safe_load(values[0])
    except yaml.YAMLError as exc:
        raise ReviewerError("COVERED_ACCEPTANCE_IDS must be a YAML list") from exc
    covered = _string_list(parsed, "COVERED_ACCEPTANCE_IDS", allow_empty=False)
    if len(covered) != len(set(covered)):
        raise ReviewerError("COVERED_ACCEPTANCE_IDS must not contain duplicates")
    return covered


def _expected_audit_acceptance_ids(
    packet: Mapping[str, Any], review_kind: str
) -> list[str]:
    required = _string_list(
        packet.get("required_acceptance_ids"),
        "review packet required_acceptance_ids",
        allow_empty=False,
    )
    if len(required) != len(set(required)):
        raise ReviewerError("review packet required_acceptance_ids contains duplicates")
    if review_kind == "full":
        return required
    if review_kind != "targeted":
        raise ReviewerError(f"unsupported audit review mode: {review_kind}")
    targets = packet.get("target_findings")
    if not isinstance(targets, list) or not targets:
        raise ReviewerError("targeted audit packet requires target_findings")
    covered: list[str] = []
    for index, raw in enumerate(targets):
        target = _mapping(raw, f"target_findings[{index}]")
        affected = _string_list(
            target.get("affected_acceptance_ids"),
            f"target_findings[{index}].affected_acceptance_ids",
            allow_empty=False,
        )
        if len(affected) != len(set(affected)):
            raise ReviewerError(
                f"target_findings[{index}].affected_acceptance_ids contains duplicates"
            )
        covered.extend(value for value in affected if value not in covered)
    if not set(covered).issubset(required):
        raise ReviewerError(
            "target_findings affected_acceptance_ids exceed required_acceptance_ids"
        )
    return required if set(covered) == set(required) else covered


def _targeted_contracts(
    targets: Sequence[Mapping[str, Any]],
    assignment: Assignment | None = None,
) -> dict[str, tuple[list[str], str]]:
    contracts: dict[str, tuple[list[str], str]] = {}
    for index, target in enumerate(targets):
        fingerprint = _string(
            target.get("fingerprint"), f"target_findings[{index}].fingerprint"
        )
        if fingerprint in contracts:
            raise ReviewerError("target_findings contains duplicate fingerprints")
        source_ids = _string_list(
            target.get("source_candidate_ids"),
            f"target_findings[{index}].source_candidate_ids",
            allow_empty=False,
        )
        if len(source_ids) != len(set(source_ids)):
            raise ReviewerError(
                f"target_findings[{index}].source_candidate_ids contains duplicates"
            )
        closure_test = _string(
            target.get("closure_test"), f"target_findings[{index}].closure_test"
        )
        required = target.get("required_assignments")
        if required is not None:
            if not isinstance(required, list):
                raise ReviewerError(
                    f"target_findings[{index}].required_assignments must be a list"
                )
            rows = [
                _mapping(
                    row,
                    f"target_findings[{index}].required_assignments[{row_index}]",
                )
                for row_index, row in enumerate(required)
            ]
            keys = {
                (
                    _string(row.get("provider"), "targeted assignment provider"),
                    _string(row.get("mission"), "targeted assignment mission"),
                )
                for row in rows
            }
            if not keys:
                raise ReviewerError("target finding requires at least one assignment")
            if assignment is not None and (
                assignment.provider,
                assignment.mission,
            ) not in keys:
                continue
        contracts[fingerprint] = (source_ids, closure_test)
    return contracts


def _require_targeted_contracts(
    verifications: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, tuple[list[str], str]],
) -> None:
    fingerprints = [str(value["fingerprint"]) for value in verifications]
    if len(fingerprints) != len(set(fingerprints)) or set(fingerprints) != set(
        contracts
    ):
        raise ReviewerError(
            "targeted output verification fingerprints do not match the packet"
        )
    for verification in verifications:
        fingerprint = str(verification["fingerprint"])
        expected_sources, expected_closure = contracts[fingerprint]
        verification["source_candidate_ids"] = expected_sources
        verification["closure_test"] = expected_closure


def _validate_output_details(
    path: Path,
    workflow_config: Mapping[str, Any],
    policy: Mapping[str, Any],
    assignment: Assignment,
    *,
    review_kind: str,
    targeted_contracts: Mapping[str, tuple[list[str], str]] | None = None,
    expected_acceptance_ids: Sequence[str] = (),
) -> tuple[
    bool, str, list[dict[str, Any]], ReviewContract | None, list[dict[str, Any]]
]:
    minimum = policy.get("minimum_output_bytes")
    if not isinstance(minimum, int) or minimum < 1:
        raise ReviewerError("minimum_output_bytes must be a positive integer")
    if not path.is_file() or path.stat().st_size < minimum:
        return False, "review output is missing or empty", [], None, []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, f"review output is unreadable: {exc}", [], None, []
    context = {
        "provider": re.escape(assignment.provider),
        "mission": re.escape(assignment.mission),
    }
    for raw in _string_list(
        workflow_config.get("output_validation_patterns"),
        "output_validation_patterns",
        allow_empty=False,
    ):
        try:
            matched = re.search(
                _format(raw, context, "output validation pattern"), text
            )
        except re.error as exc:
            raise ReviewerError(f"invalid output validation pattern: {exc}") from exc
        if not matched:
            return (
                False,
                f"review output does not match required pattern: {raw}",
                [],
                None,
                [],
            )

    try:
        workflow = _string(workflow_config.get("workflow"), "review workflow")
        decision = _decision(text)
        candidates: list[dict[str, Any]] = []
        targeted: list[dict[str, Any]] = []
        covered_acceptance_ids: list[str] = []
        target_contracts = targeted_contracts or {}
        if workflow == "audit":
            covered_acceptance_ids = _covered_acceptance_ids(text)
            expected_ids = list(expected_acceptance_ids)
            missing = sorted(set(expected_ids) - set(covered_acceptance_ids))
            extra = sorted(set(covered_acceptance_ids) - set(expected_ids))
            if missing or extra:
                raise ReviewerError(
                    "COVERED_ACCEPTANCE_IDS does not match the packet boundary: "
                    f"missing={missing}, extra={extra}"
                )
            questions = _items(
                _section(text, "Questions for User"), "Questions for User"
            )
            unverified = _items(
                _section(text, "Unread or Unverified Evidence"),
                "Unread or Unverified Evidence",
            )
            if not _section(text, "Rationale"):
                raise ReviewerError("audit rationale must not be empty")
            if review_kind == "targeted":
                if "## Finding Candidates" in text:
                    raise ReviewerError(
                        "targeted audit output must not contain finding candidates"
                    )
                targeted = extract_targeted_verifications(
                    text, assignment, prefix="AUDIT-VERIFICATION-"
                )
                _require_targeted_contracts(targeted, target_contracts)
                expected = (
                    "blocked"
                    if questions
                    else "findings"
                    if any(value["outcome"] == "still_open" for value in targeted)
                    else "unverified"
                    if unverified
                    else "pass"
                )
            elif review_kind == "full":
                if "## Targeted Verification" in text:
                    raise ReviewerError(
                        "full audit output must not contain targeted verification records"
                    )
                candidates = extract_candidates(text, assignment)
                dispositions = {str(value["disposition"]) for value in candidates}
                if not dispositions <= {
                    "remediation_required",
                    "user_decision",
                    "documentation_decision",
                }:
                    raise ReviewerError("audit review has an unsupported disposition")
                expected = (
                    "blocked"
                    if questions
                    or dispositions & {"user_decision", "documentation_decision"}
                    else "findings"
                    if candidates
                    else "unverified"
                    if unverified
                    else "pass"
                )
            else:
                raise ReviewerError(f"unsupported audit review mode: {review_kind}")
        elif workflow == "refinement":
            if not _section(text, "Coverage") or not _section(
                text, "Decision Rationale"
            ):
                raise ReviewerError(
                    "refinement coverage and rationale must not be empty"
                )
            questions = _items(
                _section(text, "Questions for User"), "Questions for User"
            )
            unverified = []
            if review_kind == "targeted":
                targeted = extract_targeted_verifications(text, assignment)
                _require_targeted_contracts(targeted, target_contracts)
                expected = (
                    "user_decision_required"
                    if questions
                    else "corrections_required"
                    if any(value["outcome"] == "still_open" for value in targeted)
                    else "approved"
                )
            elif review_kind == "full":
                if "## Targeted Verification" in text:
                    raise ReviewerError(
                        "non-targeted refinement output must not contain verification records"
                    )
                candidates = extract_candidates(text, assignment)
                unverified = [
                    str(value["source_id"])
                    for value in candidates
                    if value["category"] == "missing_evidence"
                ]
                expected = (
                    "user_decision_required"
                    if questions or any(value["requires_user"] for value in candidates)
                    else "corrections_required"
                    if any(
                        value["category"] != "missing_evidence" for value in candidates
                    )
                    else "unverified"
                    if unverified
                    else "approved"
                )
            else:
                raise ReviewerError(
                    f"unsupported refinement review kind: {review_kind}"
                )
        else:
            raise ReviewerError(f"unsupported reviewer workflow: {workflow}")
        if decision != expected:
            raise ReviewerError(
                f"review DECISION {decision!r} contradicts evidence; expected {expected!r}"
            )
        contract = ReviewContract(
            decision, questions, unverified, covered_acceptance_ids
        )
    except (ReviewerError, re.error) as exc:
        return False, str(exc), [], None, []
    return True, "", candidates, contract, targeted


def _resolve_executable(config: Mapping[str, Any]) -> tuple[str, list[str]]:
    executable = _string(config.get("executable"), "provider executable")
    resolved = shutil.which(executable)
    if resolved is None:
        raise ReviewerError(f"provider executable not found: {executable}")
    prefix = _string_list(
        config.get("executable_prefix_args", []), "executable_prefix_args"
    )
    return str(Path(resolved).resolve()), prefix


def _capture(
    command: Sequence[str], timeout: int, context: str
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewerError(f"{context} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReviewerError(f"{context} failed with exit {result.returncode}: {detail}")
    return result


def _contains_model(models_output: str, model: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(model)}(?![A-Za-z0-9_.-])",
            models_output,
        )
        is not None
    )


def _claude_flag_value(command_args: Sequence[str], flag: str) -> str:
    if command_args.count(flag) != 1:
        raise ReviewerError(f"Claude reviewer command must contain {flag} exactly once")
    index = command_args.index(flag)
    if index + 1 >= len(command_args) or command_args[index + 1].startswith("--"):
        raise ReviewerError(f"Claude reviewer command {flag} requires a value")
    return command_args[index + 1]


def _claude_allowed_tools(
    codegraph_state: Mapping[str, Any] | None,
) -> list[str]:
    allowed = ["Read", "Glob", "Grep"]
    if not codegraph_state or codegraph_state.get("status") != "ready":
        return allowed
    executable = _string(codegraph_state.get("executable"), "CodeGraph executable")
    if not Path(executable).is_absolute() or any(
        value in executable for value in (",", "(", ")", "\n", "\r")
    ):
        raise ReviewerError(
            "ready CodeGraph executable cannot be represented safely in Claude allowedTools"
        )
    commands = _string_list(
        codegraph_state.get("query_commands"),
        "CodeGraph query_commands",
        allow_empty=False,
    )
    if len(commands) != len(set(commands)) or not set(commands).issubset(
        scope_codegraph.KNOWN_QUERY_COMMANDS
    ):
        raise ReviewerError("CodeGraph allowedTools contain invalid query commands")
    displayed_executable = shlex.quote(executable)
    allowed.extend(f"Bash({displayed_executable} {command}:*)" for command in commands)
    return allowed


def _validate_claude_command_args(
    command_args: Sequence[str],
    expected_allowed_tools: Sequence[str] = ("Read", "Glob", "Grep"),
) -> None:
    required_flags = {
        "--print",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-chrome",
        "--no-session-persistence",
    }
    missing = sorted(required_flags - set(command_args))
    if missing:
        raise ReviewerError(
            f"Claude reviewer command is missing required flags: {', '.join(missing)}"
        )
    forbidden = {"--dangerously-skip-permissions", "--mcp-config"}
    present = sorted(forbidden & set(command_args))
    if present:
        raise ReviewerError(
            f"Claude reviewer command contains forbidden flags: {', '.join(present)}"
        )
    if _claude_flag_value(command_args, "--permission-mode") != "dontAsk":
        raise ReviewerError("Claude reviewers require --permission-mode dontAsk")
    if _claude_flag_value(command_args, "--output-format") != "text":
        raise ReviewerError("Claude reviewers require --output-format text")
    tools = set(_claude_flag_value(command_args, "--tools").split(","))
    if tools != {"Read", "Glob", "Grep", "Bash"}:
        raise ReviewerError("Claude reviewer tools must be Read, Glob, Grep, and Bash")
    allowed = _claude_flag_value(command_args, "--allowedTools").split(",")
    if allowed != list(expected_allowed_tools):
        raise ReviewerError(
            "Claude reviewer allowedTools must exactly match the read-only run policy"
        )
    denied = set(_claude_flag_value(command_args, "--disallowedTools").split(","))
    required_denied = {"Write", "Edit", "NotebookEdit", "Task", "Agent"}
    if not required_denied.issubset(denied):
        raise ReviewerError("Claude reviewer disallowedTools is missing write tools")


def _validate_read_only_command(
    backend: str,
    command_args: Sequence[str],
    *,
    claude_allowed_tools: Sequence[str] = ("Read", "Glob", "Grep"),
) -> None:
    if backend == "claude":
        _validate_claude_command_args(command_args, claude_allowed_tools)
        return
    if backend == "codex":
        required = {"exec", "--ephemeral", "--ignore-user-config", "--sandbox"}
        if not required.issubset(command_args):
            raise ReviewerError("Codex reviewer command is missing read-only flags")
        if _claude_flag_value(command_args, "--sandbox") != "read-only":
            raise ReviewerError("Codex reviewers require --sandbox read-only")
        if "--dangerously-bypass-approvals-and-sandbox" in command_args:
            raise ReviewerError("Codex reviewer command bypasses its sandbox")
        return
    if backend == "agy":
        if "--sandbox" not in command_args:
            raise ReviewerError("AGY reviewer command must enable its sandbox")
        return
    if backend == "opencode":
        if "--pure" not in command_args or "--agent" not in command_args:
            raise ReviewerError("OpenCode reviewer command must use pure plan mode")
        if _claude_flag_value(command_args, "--agent") != "plan":
            raise ReviewerError("OpenCode reviewers require the read-only plan agent")


def preflight_provider(
    provider: str,
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    timeout = policy.get("preflight_timeout_seconds")
    if not isinstance(timeout, int) or timeout < 1:
        raise ReviewerError("preflight_timeout_seconds must be a positive integer")
    backend = _string(config.get("backend"), f"provider {provider} backend")
    model = _string(config.get("model"), f"provider {provider} model")
    transport = _string(config.get("transport"), f"provider {provider} transport")
    if backend not in {"claude", "codex", "agy", "opencode"}:
        raise ReviewerError(f"unsupported provider backend: {backend}")
    command_args = _string_list(
        config.get("command_args"), f"{provider} command_args", allow_empty=False
    )
    _validate_read_only_command(backend, command_args)
    executable, prefix = _resolve_executable(config)
    version_args = _string_list(
        config.get("version_args"), "version_args", allow_empty=False
    )
    version_command = [executable, *prefix, *version_args]
    version_result = _capture(version_command, timeout, f"{provider} version preflight")
    versions = (version_result.stdout or version_result.stderr).strip().splitlines()
    if not versions:
        raise ReviewerError(f"{provider} version preflight returned no version")
    toolchain: dict[str, Any] = {
        "provider": provider,
        "backend": backend,
        "transport": transport,
        "model": model,
        "executable": executable,
        "executable_prefix_args": prefix,
        "version": versions[0],
    }
    if backend in {"claude", "codex", "agy", "opencode"}:
        toolchain["reasoning_effort"] = _string(
            config.get("reasoning_effort"), f"{provider} reasoning_effort"
        )
    if backend == "claude":
        help_args = _string_list(
            config.get("help_args"), "Claude help_args", allow_empty=False
        )
        help_result = _capture(
            [executable, *prefix, *help_args], timeout, "Claude help preflight"
        )
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        required_help_flags = _string_list(
            config.get("required_help_flags"),
            "Claude required_help_flags",
            allow_empty=False,
        )
        missing_help_flags = [
            flag for flag in required_help_flags if flag not in help_text
        ]
        if missing_help_flags:
            raise ReviewerError(
                "Claude CLI lacks required reviewer flags: "
                + ", ".join(missing_help_flags)
            )
        auth_args = _string_list(
            config.get("auth_args"), "Claude auth_args", allow_empty=False
        )
        auth_result = _capture(
            [executable, *prefix, *auth_args], timeout, "Claude auth preflight"
        )
        try:
            auth = json.loads(auth_result.stdout)
        except json.JSONDecodeError as exc:
            raise ReviewerError("Claude auth status was not valid JSON") from exc
        if not isinstance(auth, dict) or auth.get("loggedIn") is not True:
            raise ReviewerError("Claude CLI is not authenticated for headless review")
        toolchain["auth_method"] = str(auth.get("authMethod", "unknown"))
    if backend in {"agy", "opencode"}:
        models_args = _string_list(
            config.get("models_args"), f"{provider} models_args", allow_empty=False
        )
        models_result = _capture(
            [executable, *prefix, *models_args], timeout, f"{provider} models preflight"
        )
        models_output = f"{models_result.stdout}\n{models_result.stderr}"
        candidates = [model]
        if backend == "agy":
            fallback = _string(config.get("fallback_model"), "AGY fallback_model")
            candidates.append(fallback)
            toolchain["fallback_model"] = fallback
        missing = [
            candidate
            for candidate in candidates
            if not _contains_model(models_output, candidate)
        ]
        if missing:
            raise ReviewerError(
                f"{provider} configured model not reported by model catalog: {', '.join(missing)}"
            )
        toolchain["models_output_sha256"] = _text_sha256(models_output)
    return toolchain


def preflight_assignments(
    assignments: Sequence[Assignment],
    policy: Mapping[str, Any],
    workflow: str,
    reviewer_profile: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for provider in dict.fromkeys(assignment.provider for assignment in assignments):
        try:
            config = reviewer_provider_config(
                policy, workflow, provider, reviewer_profile
            )
            results[provider] = preflight_provider(provider, config, policy)
        except ReviewerError as exc:
            errors[provider] = str(exc)
    return results, errors


def _provider_context(
    config: Mapping[str, Any],
    assignment: Assignment,
    paths: AssignmentPaths,
    base_context: Mapping[str, str],
    model: str,
) -> dict[str, str]:
    context = {
        **base_context,
        "provider": assignment.provider,
        "mission": assignment.mission,
        "mission_slug": assignment.mission.replace("_", "-"),
        "prompt_path": str(paths.prompt.resolve()),
        "output_path": str(paths.draft.resolve()),
        "log_path": str(paths.log.resolve()),
        "model": model,
        "reasoning_effort": str(config.get("reasoning_effort", "")),
        "print_timeout": str(config.get("print_timeout", "")),
        "prompt_text": paths.prompt.read_text(encoding="utf-8"),
    }
    return context


def build_command(
    config: Mapping[str, Any],
    assignment: Assignment,
    paths: AssignmentPaths,
    base_context: Mapping[str, str],
    model: str,
    codegraph_state: Mapping[str, Any] | None = None,
) -> list[str]:
    backend = _string(config.get("backend"), "provider backend")
    context = _provider_context(config, assignment, paths, base_context, model)
    raw_args = _string_list(
        config.get("command_args"), "provider command_args", allow_empty=False
    )
    command_args = [
        _format(arg, context, "provider command argument") for arg in raw_args
    ]
    claude_allowed_tools = _claude_allowed_tools(codegraph_state)
    if backend == "claude":
        allowed_index = command_args.index("--allowedTools") + 1
        command_args[allowed_index] = ",".join(claude_allowed_tools)
    _validate_read_only_command(
        backend, command_args, claude_allowed_tools=claude_allowed_tools
    )
    executable, prefix = _resolve_executable(config)
    command = [executable, *prefix, *command_args]
    if (
        backend == "codex"
        and codegraph_state
        and codegraph_state.get("status") == "ready"
    ):
        insertion = command.index("--model")
        command[insertion:insertion] = ["--add-dir", str(codegraph_state["index_path"])]
    return command


def _environment(config: Mapping[str, Any]) -> dict[str, str]:
    result = os.environ.copy()
    configured = config.get("environment", {})
    if not isinstance(configured, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in configured.items()
    ):
        raise ReviewerError("provider environment must be a string mapping")
    result.update(configured)
    return result


def launch_process(
    command: Sequence[str],
    config: Mapping[str, Any],
    paths: AssignmentPaths,
    repo_root: Path,
    model: str,
) -> RunningProcess:
    paths.draft.parent.mkdir(parents=True, exist_ok=True)
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    prompt_transport = _string(config.get("prompt_transport"), "prompt_transport")
    if prompt_transport not in {"stdin", "file", "argument"}:
        raise ReviewerError(f"unsupported prompt_transport: {prompt_transport}")
    stdin_handle = paths.prompt.open("rb") if prompt_transport == "stdin" else None
    output_handle = (
        paths.draft.open("wb")
        if prompt_transport == "argument" or config.get("backend") == "claude"
        else None
    )
    log_handle = paths.log.open("ab")
    log_offset = paths.log.stat().st_size
    started_at = utc_now()
    started_monotonic = time.monotonic()
    try:
        process = subprocess.Popen(
            list(command),
            cwd=repo_root,
            env=_environment(config),
            stdin=stdin_handle,
            stdout=output_handle or log_handle,
            stderr=log_handle,
            **(
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
    except OSError:
        raise
    finally:
        if stdin_handle is not None:
            stdin_handle.close()
        if output_handle is not None:
            output_handle.close()
        log_handle.close()
    return RunningProcess(process, started_at, started_monotonic, log_offset, model)


def _terminate(process: subprocess.Popen[bytes], grace_seconds: int) -> None:
    if process.poll() is not None:
        return
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        descendants = []
        parent = None
    targets = [*descendants, *([parent] if parent is not None else [])]
    process_group: int | None = None
    if os.name != "nt":
        try:
            process_group = os.getpgid(process.pid)
            os.killpg(process_group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for target in targets:
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    _, alive = psutil.wait_procs(targets, timeout=grace_seconds)
    if alive and process_group is not None:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    psutil.wait_procs(alive, timeout=grace_seconds)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def wait_process(
    running: RunningProcess,
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    log_path: Path,
) -> dict[str, Any]:
    timeout = config.get("supervisor_timeout_seconds")
    grace = policy.get("termination_grace_seconds")
    if not isinstance(timeout, int) or timeout < 1:
        raise ReviewerError("supervisor_timeout_seconds must be a positive integer")
    if not isinstance(grace, int) or grace < 1:
        raise ReviewerError("termination_grace_seconds must be a positive integer")
    remaining = max(0.001, timeout - (time.monotonic() - running.started_monotonic))
    timed_out = False
    try:
        exit_code = running.process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(running.process, grace)
        exit_code = running.process.returncode
    completed_at = utc_now()
    duration = round(time.monotonic() - running.started_monotonic, 3)
    new_log = b""
    if log_path.is_file():
        with log_path.open("rb") as handle:
            handle.seek(running.log_offset)
            new_log = handle.read()
    return {
        "model": running.model,
        "started_at": running.started_at,
        "completed_at": completed_at,
        "duration_seconds": duration,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "new_log": new_log.decode("utf-8", errors="replace"),
    }


def _matches_any(text: str, raw_patterns: Any, context: str) -> bool:
    patterns = _string_list(raw_patterns or [], context)
    for pattern in patterns:
        try:
            if re.search(pattern, text):
                return True
        except re.error as exc:
            raise ReviewerError(
                f"invalid {context} pattern {pattern!r}: {exc}"
            ) from exc
    return False


def _path_record(repo_root: Path, paths: AssignmentPaths) -> dict[str, str]:
    return {
        "prompt": _relative(repo_root, paths.prompt),
        "output": _relative(repo_root, paths.output),
        "log": _relative(repo_root, paths.log),
    }


def _git_revision_identity(repo_root: Path) -> dict[str, str]:
    try:
        head_result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "-q", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewerError(f"cannot capture reviewer Git identity: {exc}") from exc
    if head_result.returncode == 1:
        return {"head": "unborn", "tree": "unborn"}
    if head_result.returncode != 0:
        detail = (head_result.stderr or head_result.stdout).strip()
        raise ReviewerError(f"cannot capture reviewer Git identity: {detail}")
    head = head_result.stdout.strip()
    try:
        tree_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-parse",
                "--verify",
                "-q",
                f"{head}^{{tree}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewerError(
            f"cannot capture reviewer Git tree identity: {exc}"
        ) from exc
    if tree_result.returncode != 0:
        detail = (tree_result.stderr or tree_result.stdout).strip()
        raise ReviewerError(f"cannot capture reviewer Git tree identity: {detail}")
    tree = tree_result.stdout.strip()
    object_id_pattern = r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})"
    if not re.fullmatch(object_id_pattern, head) or not re.fullmatch(
        object_id_pattern, tree
    ):
        raise ReviewerError("Git returned a malformed reviewer revision identity")
    return {"head": head.lower(), "tree": tree.lower()}


def _base_row(
    assignment: Assignment,
    paths: AssignmentPaths,
    repo_root: Path,
    toolchain: Mapping[str, Any] | None,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    prior_attempts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    model = (
        requested_model
        if requested_model is not None
        else ""
        if toolchain is None
        else str(toolchain.get("model", ""))
    )
    effort = (
        requested_effort
        if requested_effort is not None
        else ""
        if toolchain is None
        else str(toolchain.get("reasoning_effort", ""))
    )
    return {
        "provider": assignment.provider,
        "mission": assignment.mission,
        "status": "pending",
        "transport": "" if toolchain is None else str(toolchain.get("transport", "")),
        "requested_model": model,
        "requested_reasoning_effort": effort,
        "started_at": "",
        "completed_at": "",
        "duration_seconds": 0.0,
        "exit_code": None,
        "error": "",
        "toolchain": dict(toolchain or {}),
        "paths": _path_record(repo_root, paths),
        "prompt_sha256": "",
        "output_sha256": "",
        "decision": "",
        "questions": [],
        "unverified_evidence": [],
        "covered_acceptance_ids": [],
        "candidates": [],
        "targeted_verifications": [],
        "attempts": [dict(value) for value in prior_attempts],
        "fallback": None,
    }


def _provider_reported_execution(text: str) -> list[dict[str, Any]]:
    """Keep provider-emitted usage fields verbatim without inferring model families."""
    fields = {
        "model",
        "modelUsage",
        "usage",
        "total_cost_usd",
        "duration_api_ms",
        "num_turns",
    }
    reports: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        report = {key: value[key] for key in fields if key in value}
        if report:
            reports.append(report)
    return reports


def _finish_row(
    row: dict[str, Any],
    paths: AssignmentPaths,
) -> dict[str, Any]:
    if paths.prompt.is_file():
        row["prompt_sha256"] = file_sha256(paths.prompt)
    output = paths.draft if paths.draft.is_file() else paths.output
    if output.is_file() and output.stat().st_size > 0:
        row["output_sha256"] = file_sha256(output)
    return row


def _prior_rows(receipt: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    values = receipt.get("assignments")
    if not isinstance(values, list):
        raise ReviewerError("existing reviewer receipt assignments must be a list")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in values:
        row = _mapping(raw, "existing reviewer receipt assignment")
        key = (
            _string(row.get("provider"), "receipt provider"),
            _string(row.get("mission"), "receipt mission"),
        )
        if key in rows:
            raise ReviewerError(
                f"existing reviewer receipt has duplicate assignment: {key}"
            )
        rows[key] = row
    return rows


def _preserved_row(
    prior: Mapping[str, Any],
    assignment: Assignment,
    paths: AssignmentPaths,
    workflow_config: Mapping[str, Any],
    policy: Mapping[str, Any],
    workflow: str,
    review_kind: str,
    targeted_contracts: Mapping[str, tuple[list[str], str]],
    expected_acceptance_ids: Sequence[str],
) -> dict[str, Any]:
    valid, error, candidates, contract, targeted = _validate_output_details(
        paths.output,
        workflow_config,
        policy,
        assignment,
        review_kind=review_kind,
        targeted_contracts=targeted_contracts,
        expected_acceptance_ids=expected_acceptance_ids,
    )
    if not valid:
        raise ReviewerError(
            f"completed output cannot be preserved for {assignment.provider}/{assignment.mission}: {error}"
        )
    expected_hash = prior.get("output_sha256")
    actual_hash = file_sha256(paths.output)
    if expected_hash != actual_hash:
        raise ReviewerError(
            f"completed output changed after receipt for {assignment.provider}/{assignment.mission}"
        )
    row = dict(prior)
    row["candidates"] = candidates
    if contract is None:
        raise ReviewerError(
            "validated preserved reviewer output has no decision contract"
        )
    row["decision"] = contract.decision
    row["questions"] = contract.questions
    row["unverified_evidence"] = contract.unverified_evidence
    row["covered_acceptance_ids"] = contract.covered_acceptance_ids
    row["targeted_verifications"] = targeted
    if workflow == "refinement" and review_kind == "targeted":
        if candidates:
            raise ReviewerError("targeted refinement output must not create candidates")
    return row


def _clear_repair_outputs(paths: AssignmentPaths) -> None:
    for path in (paths.draft,):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _classify_attempt(
    attempt: Mapping[str, Any],
    config: Mapping[str, Any],
    output_path: Path,
) -> str:
    if attempt.get("timed_out") is True:
        return "timed_out"
    if attempt.get("exit_code") == 0:
        return "completed"
    log = str(attempt.get("new_log", ""))
    infrastructure_error = _matches_any(
        log, config.get("infrastructure_error_patterns"), "infrastructure error"
    )
    if infrastructure_error and (
        not output_path.is_file()
        or output_path.stat().st_size == 0
        or attempt.get("exit_code") == 127
    ):
        return "infrastructure_failed_before_review"
    return "provider_failed"


def _run_assignment(
    running: RunningProcess,
    assignment: Assignment,
    paths: AssignmentPaths,
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    workflow: str,
    workflow_config: Mapping[str, Any],
    base_context: Mapping[str, str],
    repo_root: Path,
    toolchain: Mapping[str, Any],
    review_kind: str,
    targeted_contracts: Mapping[str, tuple[list[str], str]],
    expected_acceptance_ids: Sequence[str],
    prior_attempts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    row = _base_row(
        assignment,
        paths,
        repo_root,
        toolchain,
        prior_attempts=prior_attempts,
    )
    primary = wait_process(running, config, policy, paths.log)
    primary_record = {key: value for key, value in primary.items() if key != "new_log"}
    reported = _provider_reported_execution(str(primary.get("new_log", "")))
    if reported:
        primary_record["provider_reported"] = reported
    row["attempts"].append(primary_record)
    classification = _classify_attempt(primary, config, paths.draft)
    final = primary
    fallback_allowed = (
        workflow == "audit"
        and assignment.provider == "agy"
        and workflow
        in _string_list(config.get("fallback_workflows", []), "fallback_workflows")
        and classification == "provider_failed"
        and (not paths.draft.is_file() or paths.draft.stat().st_size == 0)
        and _matches_any(
            str(primary.get("new_log", "")),
            config.get("rate_quota_error_patterns"),
            "rate/quota error",
        )
    )
    if fallback_allowed:
        fallback_model = _string(config.get("fallback_model"), "fallback_model")
        try:
            paths.draft.unlink()
        except FileNotFoundError:
            pass
        fallback_command = build_command(
            config, assignment, paths, base_context, fallback_model
        )
        try:
            fallback_running = launch_process(
                fallback_command, config, paths, repo_root, fallback_model
            )
        except OSError as exc:
            row.update(
                {
                    "status": "launch_failed",
                    "completed_at": utc_now(),
                    "fallback": {
                        "from_model": running.model,
                        "to_model": fallback_model,
                        "reason": "rate_or_quota_exhausted_before_semantic_output",
                    },
                    "error": f"AGY fallback launch failed: {exc}",
                }
            )
            return _finish_row(row, paths)
        final = wait_process(fallback_running, config, policy, paths.log)
        fallback_record = {
            key: value for key, value in final.items() if key != "new_log"
        }
        reported = _provider_reported_execution(str(final.get("new_log", "")))
        if reported:
            fallback_record["provider_reported"] = reported
        row["attempts"].append(fallback_record)
        classification = _classify_attempt(final, config, paths.draft)
        if classification == "provider_failed" and _matches_any(
            str(final.get("new_log", "")),
            config.get("rate_quota_error_patterns"),
            "rate/quota error",
        ):
            classification = "rate_quota_exhausted"
        row["fallback"] = {
            "from_model": running.model,
            "to_model": fallback_model,
            "reason": "rate_or_quota_exhausted_before_semantic_output",
        }
    row.update(
        {
            "status": classification,
            "started_at": row["attempts"][0]["started_at"],
            "completed_at": final["completed_at"],
            "duration_seconds": round(
                sum(
                    float(attempt["duration_seconds"])
                    for attempt in row["attempts"]
                    if isinstance(attempt, dict) and "duration_seconds" in attempt
                ),
                3,
            ),
            "exit_code": final["exit_code"],
        }
    )
    if classification == "completed":
        valid, error, candidates, contract, targeted = _validate_output_details(
            paths.draft,
            workflow_config,
            policy,
            assignment,
            review_kind=review_kind,
            targeted_contracts=targeted_contracts,
            expected_acceptance_ids=expected_acceptance_ids,
        )
        if valid:
            row["candidates"] = candidates
            if contract is None:
                row["status"] = "invalid_output"
                row["error"] = "review output has no validated decision contract"
            else:
                row["decision"] = contract.decision
                row["questions"] = contract.questions
                row["unverified_evidence"] = contract.unverified_evidence
                row["covered_acceptance_ids"] = contract.covered_acceptance_ids
            if review_kind == "targeted":
                if candidates:
                    row["status"] = "invalid_output"
                    row["error"] = "targeted review output must not create candidates"
                else:
                    row["targeted_verifications"] = targeted
        else:
            row["status"] = "invalid_output"
            row["error"] = error
    elif classification == "timed_out":
        row["error"] = "reviewer exceeded supervisor timeout"
    else:
        row["error"] = str(final.get("new_log", "")).strip()[-4000:]
    return _finish_row(row, paths)


def _write_receipt(
    policy: Mapping[str, Any],
    workflow: str,
    repo_root: Path,
    packet_path: Path,
    template_path: Path,
    started_at: str,
    started_monotonic: float,
    rows: Sequence[Mapping[str, Any]],
    status: str,
    before_identity: Mapping[str, str],
    after_identity: Mapping[str, str],
    reviewer_profile: str,
    reviewer_set: str,
) -> dict[str, Any]:
    completed_at = utc_now()
    receipt = {
        "schema_version": policy.get("receipt_version"),
        "workflow": workflow,
        "reviewer_profile": reviewer_profile,
        "reviewer_set": reviewer_set,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "repository_root": ".",
        "packet_path": _relative(repo_root, packet_path),
        "packet_sha256": file_sha256(packet_path),
        "template_path": _relative(repo_root, template_path),
        "template_sha256": file_sha256(template_path),
        "assignment_manifest_sha256": _json_sha256(
            [
                {"provider": row.get("provider"), "mission": row.get("mission")}
                for row in rows
            ]
        ),
        "git_identity": {
            "before": dict(before_identity),
            "after": dict(after_identity),
            "unchanged": dict(before_identity) == dict(after_identity),
        },
        "assignments": [dict(row) for row in rows],
    }
    return receipt


def _atomic_write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _publish(
    repo_root: Path,
    receipt_path: Path,
    receipt: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    paths_by_assignment: Mapping[Assignment, AssignmentPaths],
) -> None:
    """Publish semantic outputs and their sole receipt under one short mutation lock."""
    try:
        with scope_git.mutation_locks([repo_root]):
            row_by_key = {
                (str(row.get("provider")), str(row.get("mission"))): row
                for row in rows
            }
            publications: list[tuple[Path, Path]] = []
            for assignment, paths in paths_by_assignment.items():
                row = row_by_key[(assignment.provider, assignment.mission)]
                expected_hash = str(row.get("output_sha256", ""))
                if not paths.draft.is_file() or not expected_hash:
                    continue
                if file_sha256(paths.draft) != expected_hash:
                    raise ReviewerError(
                        f"review output changed before publication for "
                        f"{assignment.provider}/{assignment.mission}"
                    )
                paths.output.parent.mkdir(parents=True, exist_ok=True)
                publications.append((paths.draft, paths.output))
            for draft, output in publications:
                os.replace(draft, output)
            _atomic_write_yaml(receipt_path, receipt)
    except scope_git.GitError as exc:
        raise ReviewerError(f"review publication refused: {exc}") from exc


def _require_unchanged_inputs(
    packet_path: Path,
    packet_sha256: str,
    template_path: Path,
    template_sha256: str,
    packet: Mapping[str, Any],
    repo_root: Path,
) -> None:
    if file_sha256(packet_path) != packet_sha256:
        raise ReviewerError("review packet changed during review; start a new attempt")
    if file_sha256(template_path) != template_sha256:
        raise ReviewerError(
            "reviewer template changed during review; start a new attempt"
        )
    _require_current_artifact_hashes(packet, repo_root)


def _require_current_artifact_hashes(
    packet: Mapping[str, Any], repo_root: Path
) -> None:
    raw_hashes = packet.get("artifact_hashes")
    if raw_hashes is None:
        return
    hashes = _mapping(raw_hashes, "review packet artifact_hashes")
    if not hashes:
        raise ReviewerError("review packet artifact_hashes must not be empty")
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not relative:
            raise ReviewerError(
                "review packet artifact path must be a non-empty string"
            )
        if not isinstance(expected, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", expected
        ):
            raise ReviewerError(f"review packet artifact hash is invalid: {relative!r}")
        path = _resolve_from(repo_root, relative, repo_root, "review packet artifact")
        if not path.is_file() or path.is_symlink():
            raise ReviewerError(
                f"review packet artifact is missing or symlinked: {relative}"
            )
        if file_sha256(path) != expected:
            raise ReviewerError(f"review packet artifact changed: {relative}")


def _prepare(
    args: argparse.Namespace,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    Path,
    Path,
    Path,
    list[Assignment],
    dict[Assignment, AssignmentPaths],
    dict[str, str],
]:
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        raise ReviewerError(f"repository root does not exist: {repo_root}")
    policy = load_policy(Path(args.policy))
    workflow_config = workflow_policy(policy, args.workflow)
    packet_path = _resolve_from(repo_root, str(args.packet), repo_root, "review packet")
    packet = load_yaml(packet_path, "review packet")
    _require_current_artifact_hashes(packet, repo_root)
    if packet.get("reviewer_profile", args.reviewer_profile) != args.reviewer_profile:
        raise ReviewerError("--reviewer-profile does not match the review packet")
    if packet.get("reviewer_set", args.reviewer_set) != args.reviewer_set:
        raise ReviewerError("--reviewer-set does not match the review packet")
    assignments = assignments_from_packet(
        packet_path, packet, policy, args.workflow, args.reviewer_set
    )
    review_id = _review_id(packet, workflow_config)
    base_context = _base_context(
        packet,
        packet_path,
        repo_root,
        review_id,
        _string(policy.get("path_component_pattern"), "path_component_pattern"),
    )
    template_path = resolve_template(
        workflow_config,
        repo_root,
        None if args.template is None else Path(args.template),
    )
    runtime_dir = resolve_runtime_dir(
        policy,
        repo_root,
        base_context,
        None if args.runtime_dir is None else Path(args.runtime_dir),
    )
    receipt_path = resolve_receipt(
        policy,
        packet_path,
        repo_root,
        None if args.receipt is None else Path(args.receipt),
    )
    paths = {
        assignment: assignment_paths(
            assignment,
            workflow_config,
            packet_path,
            runtime_dir,
            repo_root,
            base_context,
        )
        for assignment in assignments
    }
    all_paths = [path for value in paths.values() for path in value.__dict__.values()]
    if len(set(all_paths)) != len(all_paths):
        raise ReviewerError("reviewer assignments do not own distinct paths")
    if receipt_path in all_paths:
        raise ReviewerError("reviewer receipt overlaps an assignment path")
    return (
        policy,
        workflow_config,
        packet,
        repo_root,
        packet_path,
        template_path,
        receipt_path,
        assignments,
        paths,
        base_context,
    )


def _codegraph_state_for_run(
    args: argparse.Namespace,
    repo_root: Path,
    codegraph_policy: Mapping[str, Any],
) -> dict[str, Any]:
    run_argument = getattr(args, "run", None)
    if run_argument is None:
        return scope_codegraph.prepare(codegraph_policy, repo_root)
    run_path = Path(str(run_argument))
    if not run_path.is_absolute():
        run_path = repo_root / run_path
    run_path = run_path.resolve(strict=True)
    run = load_yaml(run_path, "Scope run")
    if not _is_inside(repo_root, run_path):
        working_root = Path(str(run.get("working_root", ""))).resolve()
        repository_root = Path(str(run.get("repository_root", ""))).resolve()
        try:
            common = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ReviewerError("cannot resolve the worktree Git common directory") from exc
        common_root = Path(common.stdout.strip()).resolve().parent
        epic_id = str(run.get("epic_id", ""))
        command = str(run.get("command", ""))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", epic_id) or command not in {
            "epic_refine",
            "implement",
            "audit_epic",
        }:
            raise ReviewerError("Scope run has an invalid identity")
        expected = (
            common_root
            / "tmp_debug"
            / "scope-runs"
            / epic_id
            / command
            / "run.yaml"
        ).resolve()
        if (
            working_root != repo_root
            or repository_root != common_root
            or run_path != expected
        ):
            raise ReviewerError(
                "Scope run is not bound to this worktree and its common repository"
            )
    state = _mapping(run.get("codegraph"), "Scope run codegraph")
    if state.get("project_root", str(repo_root)) != str(repo_root):
        raise ReviewerError("Scope run CodeGraph state belongs to another working root")
    status = _string(state.get("status"), "Scope run codegraph.status")
    if status not in {"ready", "degraded", "unavailable", "disabled"}:
        raise ReviewerError(f"unsupported Scope run CodeGraph status: {status}")
    expected_index = repo_root / str(codegraph_policy["index_directory"])
    configured_index = state.get("index_path")
    if (
        configured_index is not None
        and Path(str(configured_index)).resolve() != expected_index.resolve()
    ):
        raise ReviewerError("Scope run CodeGraph index path does not match policy")
    merged = {
        **state,
        "reason": str(state.get("reason", status)),
        "project_root": str(repo_root),
        "index_path": str(expected_index),
        "query_commands": list(codegraph_policy["query_commands"]),
        "explore_max_files": int(codegraph_policy["explore_max_files"]),
        "affected_depth": int(codegraph_policy["affected"]["depth"]),
        "affected_test_filters": list(codegraph_policy["affected"]["test_filters"]),
        "status": status,
    }
    if status == "ready":
        executable = merged.get("executable")
        if not isinstance(executable, str) or not executable:
            executable = shutil.which(str(codegraph_policy["executable"]))
        if not executable:
            raise ReviewerError("ready Scope run CodeGraph state has no executable")
        merged["executable"] = str(Path(executable).resolve())
        if (
            expected_index.is_symlink()
            or (expected_index / "codegraph.db").is_symlink()
        ):
            raise ReviewerError("ready Scope run CodeGraph index path is a symlink")
    return merged


def run_reviewers(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    started_at = utc_now()
    started_monotonic = time.monotonic()
    (
        policy,
        workflow_config,
        packet,
        repo_root,
        packet_path,
        template_path,
        receipt_path,
        assignments,
        paths_by_assignment,
        base_context,
    ) = _prepare(args)
    template_path = materialize_template(
        policy, packet_path, repo_root, template_path
    )
    owned_paths = {
        path
        for paths in paths_by_assignment.values()
        for path in paths.__dict__.values()
    }
    if template_path == receipt_path or template_path in owned_paths:
        raise ReviewerError("reviewer template snapshot overlaps a reviewer output")
    codegraph_policy = load_codegraph_policy(
        Path(args.policy).resolve().parent / "codegraph-policy.yaml"
    )
    review_kind = str(
        packet.get("review_kind" if args.workflow == "refinement" else "mode", "")
    )
    raw_targets = packet.get("target_findings", [])
    if not isinstance(raw_targets, list) or any(
        not isinstance(row, dict) for row in raw_targets
    ):
        raise ReviewerError("target_findings must be a list of mappings")
    targeted_contracts = {
        assignment: _targeted_contracts(raw_targets, assignment)
        for assignment in assignments
    }
    if raw_targets and any(not values for values in targeted_contracts.values()):
        raise ReviewerError("targeted packet contains an assignment with no fingerprints")
    expected_acceptance_ids = (
        _expected_audit_acceptance_ids(packet, review_kind)
        if args.workflow == "audit"
        else []
    )
    template_text = template_path.read_text(encoding="utf-8")
    packet_sha256 = file_sha256(packet_path)
    template_sha256 = file_sha256(template_path)
    prior_receipt: dict[str, Any] | None = None
    prior_rows: dict[tuple[str, str], dict[str, Any]] = {}
    if receipt_path.is_file():
        prior = load_yaml(receipt_path, "reviewer receipt")
        prior_receipt = prior
        if prior.get("schema_version") != policy.get("receipt_version"):
            raise ReviewerError("existing reviewer receipt has an unsupported schema")
        if prior.get("workflow") != args.workflow:
            raise ReviewerError("existing reviewer receipt workflow does not match")
        if prior.get("reviewer_profile") != args.reviewer_profile:
            raise ReviewerError("existing reviewer receipt profile does not match")
        if prior.get("reviewer_set") != args.reviewer_set:
            raise ReviewerError("existing reviewer receipt set does not match")
        if prior.get("packet_sha256") != file_sha256(packet_path):
            raise ReviewerError(
                "review packet changed after the existing reviewer receipt"
            )
        if prior.get("template_sha256") != file_sha256(template_path):
            raise ReviewerError(
                "reviewer template changed after the existing reviewer receipt"
            )
        expected_assignment_hash = _json_sha256(
            [{"provider": row.provider, "mission": row.mission} for row in assignments]
        )
        if prior.get("assignment_manifest_sha256") != expected_assignment_hash:
            raise ReviewerError(
                "reviewer assignments changed after the existing receipt"
            )
        git_identity = _mapping(prior.get("git_identity"), "receipt git_identity")
        if git_identity.get("unchanged") is not True:
            raise ReviewerError(
                "repository identity changed during the prior review; start a new attempt"
            )
        prior_rows = _prior_rows(prior)
    repairable = set(
        _string_list(policy.get("repairable_statuses"), "repairable_statuses")
    )
    preserved: dict[Assignment, dict[str, Any]] = {}
    prior_attempts: dict[Assignment, list[dict[str, Any]]] = {}
    pending: list[Assignment] = []
    for assignment in assignments:
        paths = paths_by_assignment[assignment]
        old = prior_rows.get((assignment.provider, assignment.mission))
        if old is None:
            if paths.output.exists() or (
                paths.draft.is_file() and paths.draft.stat().st_size > 0
            ):
                raise ReviewerError(
                    "review output exists without a receipt; start a new review attempt "
                    f"for {assignment.provider}/{assignment.mission}"
                )
            _clear_repair_outputs(paths)
            pending.append(assignment)
            continue
        status = str(old.get("status", ""))
        if status == "completed":
            preserved_row = _preserved_row(
                old,
                assignment,
                paths,
                workflow_config,
                policy,
                args.workflow,
                review_kind,
                targeted_contracts[assignment],
                expected_acceptance_ids,
            )
            preserved[assignment] = preserved_row
        elif status in repairable:
            if not args.repair_infrastructure:
                raise ReviewerError(
                    "infrastructure repair requires --repair-infrastructure; "
                    f"assignment is {assignment.provider}/{assignment.mission} ({status})"
                )
            if paths.output.exists() or (
                paths.draft.is_file() and paths.draft.stat().st_size > 0
            ):
                raise ReviewerError(
                    "infrastructure repair is forbidden after semantic output; "
                    "start a new review attempt"
                )
            attempts = old.get("attempts", [])
            if not isinstance(attempts, list) or any(
                not isinstance(value, dict) for value in attempts
            ):
                raise ReviewerError("existing reviewer attempts must be mappings")
            prior_attempts[assignment] = [dict(value) for value in attempts]
            _clear_repair_outputs(paths)
            pending.append(assignment)
        else:
            raise ReviewerError(
                "a new review attempt is required for "
                f"{assignment.provider}/{assignment.mission} with status {status!r}"
            )
    extra_prior = sorted(
        set(prior_rows) - {(row.provider, row.mission) for row in assignments}
    )
    if extra_prior:
        raise ReviewerError(
            f"existing reviewer receipt has assignments absent from packet: {extra_prior}"
        )
    if not pending:
        if prior_receipt is None:
            raise ReviewerError("review has no pending assignments and no receipt")
        return 0, prior_receipt
    codegraph_state = _codegraph_state_for_run(args, repo_root, codegraph_policy)
    for assignment in pending:
        paths = paths_by_assignment[assignment]
        paths.prompt.parent.mkdir(parents=True, exist_ok=True)
        paths.prompt.write_text(
            render_prompt(
                template_text,
                workflow_config,
                assignment,
                paths,
                base_context,
                codegraph_state,
                tuple(targeted_contracts[assignment]),
            ),
            encoding="utf-8",
        )
    before_identity = _git_revision_identity(repo_root)
    preflight, preflight_errors = preflight_assignments(
        pending, policy, args.workflow, args.reviewer_profile
    )
    rows: dict[Assignment, dict[str, Any]] = dict(preserved)
    if preflight_errors:
        for assignment in pending:
            paths = paths_by_assignment[assignment]
            selected_config = reviewer_provider_config(
                policy, args.workflow, assignment.provider, args.reviewer_profile
            )
            row = _base_row(
                assignment,
                paths,
                repo_root,
                preflight.get(assignment.provider),
                _string(
                    selected_config.get("model"),
                    f"provider {assignment.provider} model",
                ),
                _string(
                    selected_config.get("reasoning_effort"),
                    f"provider {assignment.provider} reasoning_effort",
                ),
                prior_attempts.get(assignment, ()),
            )
            row.update(
                {
                    "status": (
                        "preflight_failed"
                        if assignment.provider in preflight_errors
                        else "not_launched_preflight_barrier"
                    ),
                    "completed_at": utc_now(),
                    "error": preflight_errors.get(
                        assignment.provider,
                        "another required provider failed preflight",
                    ),
                }
            )
            rows[assignment] = _finish_row(row, paths)
        ordered = [rows[assignment] for assignment in assignments]
        after_identity = _git_revision_identity(repo_root)
        _require_unchanged_inputs(
            packet_path,
            packet_sha256,
            template_path,
            template_sha256,
            packet,
            repo_root,
        )
        receipt = _write_receipt(
            policy,
            args.workflow,
            repo_root,
            packet_path,
            template_path,
            started_at,
            started_monotonic,
            ordered,
            "failed",
            before_identity,
            after_identity,
            args.reviewer_profile,
            args.reviewer_set,
        )
        _publish(repo_root, receipt_path, receipt, ordered, paths_by_assignment)
        return 1, receipt
    launch_specs: dict[Assignment, tuple[dict[str, Any], str, list[str]]] = {}
    for assignment in pending:
        config = reviewer_provider_config(
            policy, args.workflow, assignment.provider, args.reviewer_profile
        )
        if codegraph_state.get("status") == "ready":
            configured_environment = _mapping(
                config.get("environment", {}),
                f"provider {assignment.provider} environment",
            )
            config["environment"] = {
                **configured_environment,
                "CODEGRAPH_DIR": str(codegraph_policy["index_directory"]),
            }
        paths = paths_by_assignment[assignment]
        model = _string(config.get("model"), f"provider {assignment.provider} model")
        command = build_command(
            config,
            assignment,
            paths,
            base_context,
            model,
            codegraph_state,
        )
        _environment(config)
        launch_specs[assignment] = (config, model, command)
    running: dict[Assignment, RunningProcess] = {}
    for assignment, (config, model, command) in launch_specs.items():
        paths = paths_by_assignment[assignment]
        try:
            running[assignment] = launch_process(
                command, config, paths, repo_root, model
            )
        except OSError as exc:
            row = _base_row(
                assignment,
                paths,
                repo_root,
                preflight[assignment.provider],
                prior_attempts=prior_attempts.get(assignment, ()),
            )
            row.update(
                {
                    "status": "launch_failed",
                    "completed_at": utc_now(),
                    "error": str(exc),
                }
            )
            rows[assignment] = _finish_row(row, paths)
    canceled = False
    try:
        for assignment, process in running.items():
            config = reviewer_provider_config(
                policy, args.workflow, assignment.provider, args.reviewer_profile
            )
            rows[assignment] = _run_assignment(
                process,
                assignment,
                paths_by_assignment[assignment],
                config,
                policy,
                args.workflow,
                workflow_config,
                base_context,
                repo_root,
                preflight[assignment.provider],
                review_kind,
                targeted_contracts[assignment],
                expected_acceptance_ids,
                prior_attempts.get(assignment, ()),
            )
    except KeyboardInterrupt:
        canceled = True
        grace = policy.get("termination_grace_seconds")
        if not isinstance(grace, int) or grace < 1:
            raise ReviewerError("termination_grace_seconds must be a positive integer")
        for process in running.values():
            _terminate(process.process, grace)
        for assignment, process in running.items():
            if assignment in rows:
                continue
            config = reviewer_provider_config(
                policy, args.workflow, assignment.provider, args.reviewer_profile
            )
            attempt = wait_process(
                process, config, policy, paths_by_assignment[assignment].log
            )
            row = _base_row(
                assignment,
                paths_by_assignment[assignment],
                repo_root,
                preflight[assignment.provider],
                prior_attempts=prior_attempts.get(assignment, ()),
            )
            attempt_record = {
                key: value for key, value in attempt.items() if key != "new_log"
            }
            reported = _provider_reported_execution(str(attempt.get("new_log", "")))
            if reported:
                attempt_record["provider_reported"] = reported
            row["attempts"].append(attempt_record)
            row.update(
                {
                    "status": "canceled",
                    "started_at": attempt["started_at"],
                    "completed_at": attempt["completed_at"],
                    "duration_seconds": attempt["duration_seconds"],
                    "exit_code": attempt["exit_code"],
                    "error": "review canceled by caller",
                }
            )
            rows[assignment] = _finish_row(row, paths_by_assignment[assignment])
    except BaseException:
        grace = policy.get("termination_grace_seconds", 5)
        for process in running.values():
            _terminate(process.process, int(grace))
        raise
    ordered = [rows[assignment] for assignment in assignments]
    after_identity = _git_revision_identity(repo_root)
    status = (
        "canceled"
        if canceled
        else "completed"
        if all(row.get("status") == "completed" for row in ordered)
        and before_identity == after_identity
        else "failed"
    )
    _require_unchanged_inputs(
        packet_path, packet_sha256, template_path, template_sha256, packet, repo_root
    )
    receipt = _write_receipt(
        policy,
        args.workflow,
        repo_root,
        packet_path,
        template_path,
        started_at,
        started_monotonic,
        ordered,
        status,
        before_identity,
        after_identity,
        args.reviewer_profile,
        args.reviewer_set,
    )
    _publish(repo_root, receipt_path, receipt, ordered, paths_by_assignment)
    return (0 if status == "completed" else 130 if status == "canceled" else 1), receipt


def preflight_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    (
        policy,
        _workflow_config,
        _packet,
        repo_root,
        _packet_path,
        _template_path,
        _receipt_path,
        assignments,
        _paths,
        _base_context,
    ) = _prepare(args)
    codegraph_policy = load_codegraph_policy(
        Path(args.policy).resolve().parent / "codegraph-policy.yaml"
    )
    _codegraph_state_for_run(args, repo_root, codegraph_policy)
    toolchains, errors = preflight_assignments(
        assignments, policy, args.workflow, args.reviewer_profile
    )
    result = {
        "status": "completed" if not errors else "failed",
        "providers": [
            {
                "provider": provider,
                "status": "completed" if provider not in errors else "preflight_failed",
                "toolchain": toolchains.get(provider, {}),
                "error": errors.get(provider, ""),
            }
            for provider in dict.fromkeys(row.provider for row in assignments)
        ],
    }
    return (0 if not errors else 1), result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "run"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--workflow", required=True, choices=("refinement", "audit")
        )
        subparser.add_argument("--packet", type=Path, required=True)
        subparser.add_argument("--repo-root", type=Path, required=True)
        subparser.add_argument("--policy", type=Path, default=policy_path())
        subparser.add_argument("--template", type=Path)
        subparser.add_argument("--runtime-dir", type=Path)
        subparser.add_argument("--receipt", type=Path)
        subparser.add_argument("--run", type=Path)
        subparser.add_argument(
            "--reviewer-profile", choices=("default", "budget"), default="default"
        )
        subparser.add_argument(
            "--reviewer-set", choices=("standard", "expanded"), default="standard"
        )
    run_parser = subparsers.choices["run"]
    run_parser.add_argument("--repair-infrastructure", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            code, result = preflight_command(args)
            print(json.dumps(result, sort_keys=True))
            return code
        code, receipt = run_reviewers(args)
        print(json.dumps(receipt, sort_keys=True))
        return code
    except ReviewerError as exc:
        print(f"Scope reviewer launch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Sample project texts shared by the fake provider and the tests."""

CRITERIA = """# DEMO-001: Acceptance Criteria

```yaml scope
size_estimate:
  production_loc: 12
  files: 1
  rationale: "One small greeting function"
```

## Not building

- Localized greetings

## Criteria

### AC-001: Greets a person by name

**Given** a name **when** `greet` is called **then** it returns `Hello, <name>!`.

### AC-002: Rejects an empty name

**Given** an empty name **when** `greet` is called **then** it raises `ValueError`.

## Open product questions

None
"""

PLAN = """# DEMO-001: Plan

```yaml scope
estimate:
  production_loc: 12
  files: 1
production_paths: [src/]
test_paths: [tests/]
```

## Purpose

Callers can greet a person by name.

## Approach

One function in `src/greet.py`, documented in `docs/architecture/05-building-blocks.md`.

## Milestones

- **M1**: greeting works
- **M2**: empty names are rejected

## Stories

```yaml scope
stories:
  - {id: S1, title: Greet by name, milestone: M1, complexity: 1, estimate_loc: 6, criteria: [AC-001], status: todo}
  - {id: S2, title: Reject empty names, milestone: M2, complexity: 1, estimate_loc: 6, criteria: [AC-002], status: todo}
```

## Validation

```yaml scope
validation:
  - id: unit
    type: test
    command: "PYTHON -m pytest tests -q -p no:cacheprovider --junitxml={junit}"
  - id: lint
    type: check
    command: "PYTHON -m py_compile src/greet.py"
acceptance_tests:
  AC-001: ["tests/test_greet.py::test_greets_by_name"]
  AC-002: ["tests/test_greet.py::test_rejects_empty_name", "command:lint"]
unavailable_evidence: []
```

## Documentation obligations

```yaml scope
docs:
  - {target: docs/architecture/05-building-blocks.md, story: S1, change: "Describe greet"}
```

## Dependencies

None

## Concepts

Planned: one module, no persisted state.

## Progress log

## Decision log

## Surprises and discoveries
"""

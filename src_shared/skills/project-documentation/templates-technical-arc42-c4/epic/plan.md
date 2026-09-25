# {epic-id}: Plan

A living plan in the ExecPlan style. The planner writes it after Gate 1; the
implementer keeps Progress, Decision log, Surprises, and story status current.
A fresh implementer resumes from this file and `git log`. Reference the
durable architecture docs instead of restating them; record only what this
epic adds or changes. Blocks tagged `yaml scope` are read by Scope's runner.

```yaml scope
estimate:
  production_loc: 0        # sum of the story estimates
  files: 0
production_paths: [src/]   # production code only; tests and docs excluded
test_paths: [tests/]
expected_paths: []         # other planned changes (config, requirements); not counted
# Changes outside these paths and docs/ are reported as scope warnings.
```

## Purpose

[What the user can do after this epic, in two or three sentences. The approved
criteria are in `acceptance-criteria.md`.]

## Approach

[The design in the fewest concepts that satisfy the criteria. Link the durable
docs it relies on (`docs/architecture/...`, ADRs, specs) and state only what
this epic adds or changes. Hostile cases and fail-closed behavior only where a
criterion touches security, money, data integrity, or destructive actions.]

## Milestones

- **M1: [name]**: [what works at the end, and how it is observed]

## Stories

Complexity is 0–10 (maximum 7; split anything above, or record
`complexity_exception`). `estimate_loc` is production code lines. The
implementer sets `status: done` when the story is committed.

```yaml scope
stories:
  - id: S1
    title: "[story title]"
    milestone: M1
    complexity: 3
    estimate_loc: 0
    criteria: [AC-001]
    status: todo
```

## Validation

Scope's runner runs these with `sh -c` from the repository root, on a
committed state. A test suite writes JUnit XML with its standard reporter to
the `{junit}` path (pytest `--junitxml`, `gotestsum --junitfile`, a Jest JUnit
reporter); a failing or skipped test without a reason fails the run.
`type: check` commands (lint, types, coverage) count by exit code. Check
coverage of new and changed code with a standard tool, for example
`diff-cover coverage.xml --compare-branch=main --fail-under=90`.

Map each criterion to the tests that prove it (`path::test` or the JUnit
`classname.name`). A criterion proved only by a command without JUnit output
names that command as `command:<id>`; the record marks it as proved by exit
code. Milestone runs require the criteria of stories marked done; the final
run requires every criterion.

```yaml scope
validation:
  - id: unit
    type: test
    command: "python -m pytest tests/unit --junitxml={junit}"
acceptance_tests:          # tests that prove each criterion; all must run and pass
  AC-001: ["tests/unit/test_example.py::test_example"]
unavailable_evidence: []   # evidence that cannot run here: {id, reason}
```

## Documentation obligations

Each durable doc change, its target file, and the story that owns it.

```yaml scope
docs:
  - target: docs/architecture/05-building-blocks.md
    story: S1
    change: "[what changes in the current-state description]"
```

## Dependencies

[Other epics, services, or data this plan needs, or "None".]

## Concepts

Planned: [new modules, abstractions, persisted state]. Actual (filled in by the
implementer before the audit): [what was added, and why any difference].

## Progress log

Each entry is one line. Command output, operational steps, and other evidence
go into a working paper in the epic folder (for example `notes/S1.md`), linked
from the entry.

- [date] [story or event]: [what was completed]; [commit]

## Decision log

Each entry is one line. A longer analysis goes into a working paper in the
epic folder, linked from the entry.

- [date] [decision] — [why]; [reversible or high-impact]

## Surprises and discoveries

- [date] [observation and its consequence]

# Simplicity and Size

Scope appends this file to every planner, implementer, and reviewer prompt.
These rules are mandatory. Reviewers enforce them; the runner only reports
sizes.

## Why this exists

Claude and Codex habitually over-engineer: extra files, single-use
abstractions, speculative configurability, defensive re-validation of data
already checked upstream, and handling for cases that cannot happen. Each rule
below counters that tendency.

## Simplicity

- Build the minimum that satisfies the approved acceptance criteria. Nothing
  on the "not building" list, nothing for hypothetical futures.
- Validate only at system boundaries (API input, config loading, external
  data). Trust internal calls; do not re-check what an earlier step checked.
- Handle only errors that can occur. Fail fast with a clear message; no broad
  catches, silent defaults, or empty handlers.
- No single-use abstractions, wrappers, or configuration switches nobody asked
  for. Reuse existing code before writing new code; prefer the standard
  library and established, maintained libraries.
- Between two designs that both satisfy the criteria, choose the one with fewer
  concepts and fewer lines.
- One responsibility per module. No dead code, commented-out code, or unused
  imports.

## Production code

- No stubs or placeholders in production code: no `TODO`, `pass` where logic
  belongs, `NotImplementedError`, or hardcoded return values standing in for
  real computation.
- I/O is real: when a criterion says the code calls, sends, queries, or writes,
  the production path contains that I/O. Tests may mock the boundary.
- Configurable values (URLs, ports, thresholds, model names, timeouts, retry
  counts) come from the project's configuration files, not literals, unless the
  approved criteria say otherwise.
- Components are wired: every new module is used by a real entry point, not
  only by its own tests.
- No credentials or secrets in code.

## Tests

- Write tests alongside the code, as early as they can run. Tests assert real
  behavior through the interface a user or caller uses.
- Every acceptance criterion has the tests the plan maps to it, and they must
  run and pass. A skip needs a reason.
- Coverage target: 90% of new and changed production code, measured per epic.
  A lower target needs a recorded reason in the plan.
- A new external service, database, or container gets one live smoke test of
  the real connection, not only mocks.
- Do not duplicate coverage across unit, integration, and end-to-end layers.
- Never weaken a test, or make production code or mocks wrong, to get a pass.

## Story complexity

Score each story 0–10 for integration and correctness difficulty, not effort.
The maximum is 7. Anything above is split before handoff; when in doubt,
split. An exception needs a recorded reason in the plan, and the refinement
reviewer must accept it.

| Score | Anchor |
|---|---|
| 0–2 | Configuration, copy, or a one-function change following an existing pattern |
| 3–4 | One component, established pattern, a few edge cases |
| 5–6 | Two or three components, or one new pattern; a straightforward schema change |
| 7 | Several boundaries, new persisted state, or an external integration with real failure modes; still one clear, testable outcome |
| 8–10 | Split: several new concepts at once, concurrency or distributed state, a risky migration, or high uncertainty |

## Module size

Code lines exclude blank lines, comments, and docstrings.

- Target: at most 350 code lines per production module.
- Between 350 and 450: allowed; a reviewer may suggest a split.
- Hard limit: 450. New modules stay under it. A module already above it must
  not grow. Above the limit, split by responsibility or record a one-line
  exception with its reason in the plan's decision log; the audit reviewer
  judges the exception.

Split for clarity, never to game the count. Code lines are a warning signal,
read together with the concepts added and the defects found.

## Proportionality

The size estimate approved at Gate 1 is a commitment to proportion. After each
story the runner compares production code lines with the plan's cumulative
estimate. Growth above the threshold is explained in the plan's decision log
and classified by an independent check: scope growth goes to the user,
implementation growth continues and is shown at merge.

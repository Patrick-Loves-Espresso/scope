# {epic-id}: Acceptance Criteria

The user approves this file at Gate 1. It states what the epic delivers, how
big it should be, and what it deliberately leaves out. Keep criteria concise
and observable; include only the error cases that matter to the product.

```yaml scope
size_estimate:
  production_loc: 0   # new or changed production code lines (no tests, no docs)
  files: 0            # production files added or changed
  rationale: ""       # one line: why this size is proportional to the criteria
```

## Not building

- [Something a reader might expect that this epic deliberately excludes]

## Criteria

### AC-001: [Observable outcome]

**Given** [precondition] **when** [user or system action] **then** [observable
result, with a measurable threshold where one applies].

### AC-002: [Error case that matters to the product]

**Given** [invalid, unavailable, or failed condition] **when** [action] **then**
[rejection, error, or recovery the user can observe].

## Baseline

[The commit checked, each existing test, lint, and type command run on it, and
its result. The failures that already exist, or "None"; a long list goes into
a working paper in the epic folder, linked here. Any command that could not
run here, and why.]

## Open product questions

None

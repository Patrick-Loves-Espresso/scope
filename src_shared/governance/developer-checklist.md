# Developer Pre-Completion Checklist

Read this file from disk before reporting a story or task complete. It checks
the rules in `simplicity-and-size.md`; it does not replace them.

## Delivery

- [ ] **Criteria delivered through a real entry point.** Each affected
  acceptance criterion works through the entry point a user or caller uses,
  not only in isolated units.
- [ ] **Mapped tests pass.** The tests the plan maps to each affected
  criterion exist, assert real behavior, and pass. Every skip has a reason.
- [ ] **Promised output observed.** When a criterion promises output, state,
  or a side effect, a representative run shows it. Code for a migration,
  backfill, or sync is not proof that it ran.
- [ ] **Live smoke test.** A new external service, database, or container was
  exercised once for real, not only through mocks.
- [ ] **Coverage.** New and changed production code is covered at the epic's
  target (90% by default), or the plan records why not.

## Simplicity and size

- [ ] **Nothing beyond the criteria.** No item from the "Not building" list,
  no speculative configuration, no single-use abstraction.
- [ ] **No re-validation of internal calls** and no handling for cases that
  cannot occur.
- [ ] **Module size.** No production module above 450 code lines without a
  recorded exception; a module already above it did not grow.
- [ ] **No stubs, dead code, or leftovers** from earlier attempts.
- [ ] **Components are wired**: every new module is used by a real entry point.

## Records

- [ ] **Plan updated.** Story status, progress log, decision log (reversible
  choices, and why; one line per entry), and surprises are current.
- [ ] **Durable docs.** Each doc obligation of the story is done;
  `docs/architecture/` describes the current state only.
- [ ] **Lessons.** Nothing in `docs/lessons-learned/INDEX.md` is violated.
- [ ] **Committed** with a meaningful label that names the story.

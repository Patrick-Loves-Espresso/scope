# {epic-id}: Review

All refinement and audit findings for this epic. Scope's runner appends each
reviewer round (with the commit it reviewed) and every verification,
adjudication, check, and user decision. Authors (the planner in refinement,
the implementer in the audit) edit only a finding's `disposition` line, and
may raise a minor finding's severity to `major`:

- `fixed — <what changed>`
- `rejected — <reason: out of scope, or hypothetical, with evidence>`
- `disproportionate — <the requirement or mechanism a minor fix would add>`
  (always checked by the raising reviewer, then adjudicated if maintained)
- `duplicate of <finding id>` (the shared finding is verified by every
  reviewer that raised it)

The runner resets a disposition to `open` when a fix is not verified or a
rejection is overturned. Suggestions are optional and never tracked.

<!-- Example of what the runner appends:

## refine 1 · full · 2026-09-23T10:00:00Z
- commit: 0123456789abcdef0123456789abcdef01234567
- reviewer claude · claude-opus-5-5/high · completed · changes_required
- reviewer codex · gpt-6-astra/high · completed · approve

### R1.claude.1 · major · feasibility
- evidence: plan.md "Approach" assumes a queue that 05-building-blocks.md does not have
- correction: use the existing scheduler
- closure: plan.md names the scheduler and no queue
- disposition: open

## refine 2 · verify · 2026-09-23T11:00:00Z
- commit: 89abcdef0123456789abcdef0123456789abcdef
- reviewer claude · claude-opus-5-5/high · completed · done
- R1.claude.1 · claude: verified — the plan now uses the scheduler
-->

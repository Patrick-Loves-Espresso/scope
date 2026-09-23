---
name: epic_refine
description: Draft acceptance criteria for user approval (Gate 1), write the living plan, and settle one independent review.
args: "{epic-id}"
---

# epic_refine

You orchestrate. A fresh planner writes the criteria and the plan; independent
reviewers (Claude and Codex) review the plan. Never write the criteria, plan,
findings, or `approvals.yaml` yourself, and never review the plan yourself.
You talk to the user; workers and reviewers do not.

Invoking this command authorizes sending the epic's documents and the
repository to the configured reviewer providers.

## Setup

Run in the main checkout, not a worktree.

```bash
EPIC="{epic-id}"
ROOT="$(git rev-parse --show-toplevel)"
HOST=claude; SCOPE_ROOT="$ROOT/.claude"            # Claude Code
# HOST=codex; SCOPE_ROOT="$ROOT/plugins/scope"     # Codex
S="$SCOPE_ROOT/scripts"; PY=python3                # interpreter with Scope's requirements
```

Require exactly one `docs/epics/<epic-dir>/` whose name starts with the epic
ID, with a `details.md`. If `codegraph` is on PATH, run `codegraph sync` when
`.codegraph/` exists, or `codegraph init` when it is missing and
`git check-ignore -q .codegraph/` succeeds.

An epic planned by an older Scope version: `git rm -r` any of `design.md`,
`delivery-manifest.yaml`, `file-plan-story-*.yaml`, `refinement-state.yaml`,
`refinement-findings.yaml`, `refinement-review.md`,
`implementation-evidence.yaml`, `implementation-summary.md`,
`audit-findings.yaml`, `epic_audit.md`, and `reviews/` from the epic folder,
and commit `refine(<epic>): remove old-format artifacts`. Its existing
criteria are re-drafted and re-approved below.

Every script prints JSON. Read it; do not guess state.

## 1. Draft the acceptance criteria

```bash
$PY "$S/scope_launch.py" work --host $HOST --role planner --epic $EPIC \
  --task "Draft acceptance-criteria.md from details.md, the PRD, and the durable docs."
$PY "$S/scope_check.py" criteria --epic $EPIC
```

Fix format errors with a fresh planner job naming them. If the planner
returned `needs_user` or the check reports open questions, ask the user all of
them at once, then run a fresh planner job whose task carries the answers.

## 2. Gate 1: the user approves the criteria

Show the user the criteria, the size estimate with its rationale, and the "Not
building" list. The user approves, edits, or cuts. Apply edits through a fresh
planner job (task: the user's exact changes) and show the result again. Only
on explicit approval of the content as shown:

```bash
$PY "$S/scope_check.py" approve --epic $EPIC --source "<who approved, how, when>"
```

A general pre-approval does not count: the user approves this content.

## 3. Write the plan

```bash
$PY "$S/scope_launch.py" work --host $HOST --role planner --epic $EPIC \
  --task "Write plan.md for the approved acceptance criteria."
$PY "$S/scope_check.py" plan --epic $EPIC
$PY "$S/scope_check.py" criteria --epic $EPIC
```

Send plan errors back to a fresh planner job. If the criteria no longer match
the approval, show the user the diff and size delta the check prints and get
renewed approval (step 2) before continuing.

## 4. Independent review

```bash
$PY "$S/scope_launch.py" review --host $HOST --workflow refine --mission full --epic $EPIC
```

This runs Claude and Codex in parallel at high effort. An unavailable or
failed reviewer is replaced by the fallback provider. If fewer than two
reviewers completed, rerun the missing one with `--providers <name>`; never
substitute yourself.

## 5. Resolve until settled

Loop on:

```bash
$PY "$S/scope_review.py" status --epic $EPIC --workflow refine
```

Act on the first that applies, then check status again:

- `needs_user`: product-scope disputes or `product_decision` findings. Ask the
  user all of them at once; record each answer with
  `$PY "$S/scope_review.py" decide --epic $EPIC --finding <id> --outcome
  finding_upheld|rejection_upheld --note "<the user's words>"`.
- `blocked`: a finding failed again after its diagnosis. Ask the user only when
  it needs a product choice, an accepted risk (`rejection_upheld`), or more
  resources; otherwise stop and report the epic as blocked.
- `needs_diagnosis`: the finding survived two fixes. Run
  `review --workflow refine --mission diagnose --finding <id>`; the next
  planner job gets the diagnosis.
- `needs_disposition`: run a fresh planner job with task "Resolve the open
  findings in review.md." (add any diagnosis). Then rerun `scope_check.py plan`
  and `scope_check.py criteria` (renewed approval if the criteria changed).
- `needs_verification` or `needs_rejection_check`:
  `review --workflow refine --mission verify`.
- `needs_adjudication`: `review --workflow refine --mission adjudicate`.
- Nothing pending but `complete: false`: rerun each provider in `missing_reviews` with
  `--mission full --providers <name>`.
- Nothing pending but `fresh: false`: the plan changed after the last review
  round; run the full review again.

Verification passes check only the named findings and add nothing new. No
finding is accepted because a round budget ran out.

## 6. Finish

When status reports `settled: true`, confirm `scope_check.py criteria` and
`scope_check.py plan` pass, then commit the epic folder with
`refine(<epic>): plan ready for implementation`.

## Final response

Report: approval source and blob, the size estimate, stories with complexity
and estimates, reviewers and their models, findings by outcome (fixed,
rejected and how adjudicated, accepted quality tradeoffs), doc obligations, and
anything open. Recommend `/implement <epic>` (Codex: `scope:implement <epic>`)
only when settled. Never report success with a check failing or a finding open.

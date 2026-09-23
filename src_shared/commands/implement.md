---
name: implement
description: Implement a refined epic in its worktree with one implementer, runner verification, and the independent audit.
args: "{epic-id}"
---

# implement

You orchestrate. A fresh implementer writes the code, tests, and docs, and
commits per story; Scope's runner executes the tests; independent reviewers
check plan changes and audit the result. Never write code, tests, or docs
yourself, and never review them yourself. You talk to the user; workers and
reviewers do not.

## Setup and readiness

```bash
EPIC="{epic-id}"
ROOT="$(git rev-parse --show-toplevel)"            # main checkout
HOST=claude; SCOPE_ROOT="$ROOT/.claude"            # Claude Code
# HOST=codex; SCOPE_ROOT="$ROOT/plugins/scope"     # Codex
S="$SCOPE_ROOT/scripts"; PY=python3
$PY "$S/scope_check.py" criteria --epic $EPIC
$PY "$S/scope_check.py" plan --epic $EPIC
$PY "$S/scope_review.py" status --epic $EPIC --workflow refine
```

Proceed only when the criteria match their approval, the plan passes, and the
refinement review is `settled`. Otherwise report what is missing and recommend
`/epic_refine` (Codex: `scope:epic_refine`). If the epic depends on another
epic, that epic must already be merged into the base branch.

Create or reuse the worktree, then work there with the Scope installation of
the main checkout:

```bash
WT="$ROOT/wip/$EPIC"
if ! git worktree list --porcelain | grep -qx "worktree $WT"; then
  if git show-ref --quiet "refs/heads/epic/$EPIC"; then git worktree add "$WT" "epic/$EPIC"
  else git worktree add -b "epic/$EPIC" "$WT"; fi
fi
[ -e "$WT/.env" ] || [ ! -e "$ROOT/.env" ] || ln -s "$ROOT/.env" "$WT/.env"
cd "$WT"
```

If `codegraph` is on PATH, index the worktree: `codegraph sync` when
`.codegraph/` exists, else `codegraph init` when
`git check-ignore -q .codegraph/` succeeds.

## 1. Implement

```bash
$PY "$S/scope_launch.py" work --host $HOST --role implementer --epic $EPIC \
  --task "Implement plan.md, continuing from its story status and the git log."
```

One implementer works through the whole plan by default. It commits per
story, runs the size check after each story, runs the runner at each
milestone, finalizes the durable docs, and archives the epic folder. Split the
work into sequential jobs (name the milestone in `--task`) only when size,
context, external integrations, or uncertainty warrant it. Act on its status:

- `done`: go to step 2.
- `needs_check`: the implementer recorded a size overrun or a departure from
  the reviewed plan in the decision log. Run the independent check (the other
  provider); add `--size` for a size overrun so the round records the numbers:

  ```bash
  $PY "$S/scope_launch.py" review --host $HOST --workflow implement --mission check \
    --epic $EPIC --context "<the decision-log entry>" [--size]
  ```

  `implementation_growth` or `approved`: launch a fresh implementer whose task
  says so. `concerns`: launch a fresh implementer whose task carries the
  concerns, then check again. `scope_growth`: ask the user to re-approve, cut,
  or re-plan (see "Renewing Gate 1 on the branch").
- `needs_user`: ask the user every question at once; continue with a fresh
  implementer whose task carries the answers. An answer that changes the
  acceptance criteria goes through "Renewing Gate 1 on the branch".
- `blocked` or a failed job: read the job log named in the JSON. Relaunch a
  fresh implementer once with the diagnosis; if it fails again, report blocked.

**Renewing Gate 1 on the branch.** Criteria never change silently. In the
worktree, run a planner job with the user's exact change
(`scope_launch.py work --role planner --task "<change>; update the plan to
match"`), then `scope_check.py criteria`: show the user the diff and size
delta it prints. On explicit approval run `scope_check.py approve` in the
worktree (the record lands on the epic branch), then an independent check of
the plan change (`--mission check`) before a fresh implementer continues.

A resumed `/implement` re-reads the plan and the git log; nothing else is
needed.

## 2. Final verification

The implementer must have committed everything and archived the epic folder.

```bash
git status --short
$PY "$S/scope_verify.py" run --epic $EPIC --milestone final
```

The runner executes the plan's validation commands on the committed state,
checks that the tests mapped to every criterion ran and passed, and commits
`verification.yaml`. On `failed`, launch a fresh implementer with the problems
it lists, then run it again.

## 3. Audit

Execute the installed `audit_epic.md` in this session, from its Setup, with
the same `EPIC`, `HOST`, and `S`. Do not start a second orchestrator.

## Final response

Report the branch and worktree, the commits per story, verification counts per
milestone, size against the estimate (`scope_verify.py size`), checks run and
their outcomes, the audit result, and anything open. Recommend `/wrap_epic
<epic>` (Codex: `scope:wrap_epic <epic>`) only when the audit passed. Do not
merge, push, or remove the worktree.

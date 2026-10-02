---
name: audit_epic
description: Independent audit of an implemented epic by its reviewers, with fixes, verification, and adjudication until settled.
args: "{epic-id}"
---

# audit_epic

You orchestrate. The epic's reviewers audit its branch independently; the
implementer fixes or rejects findings; the raising reviewer verifies; the
adjudicator settles maintained rejections. Never review, fix, or judge a
finding yourself. You talk to the user; workers and reviewers do not.

Invoking this command authorizes sending the epic's branch and documents to
the configured reviewer providers.

## Setup

Run in the epic's worktree (`/implement` executes this command there).

```bash
EPIC="{epic-id}"
WT="$(git rev-parse --show-toplevel)"              # wip/<epic>, branch epic/<epic>
ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd -P)"
HOST=claude; SCOPE_ROOT="$ROOT/.claude"            # Claude Code
# HOST=codex; SCOPE_ROOT="$ROOT/plugins/scope"     # Codex
S="$SCOPE_ROOT/scripts"; PY=python3
$PY "$S/scope_verify.py" covers --epic $EPIC
```

The epic folder must already be archived under `docs/epics/_implemented/` on
this branch and the tree must be clean. If `covers` reports that no passing
verification covers the current commit, run
`$PY "$S/scope_verify.py" run --epic $EPIC --milestone final` first; on failure,
return to `/implement` step 2. If `codegraph` is on PATH, run `codegraph sync`
in the worktree when `.codegraph/` exists.

**Reviewers.** Run `$PY "$S/scope_reviewers.py" show --epic $EPIC`. Without an
`audit` adjudicator, stop: the user chooses one with `/scope_reviewers`
(Codex: `scope:reviewers`). If the arguments (or `/implement`'s) add, remove,
or replace a reviewer or the adjudicator, apply it to this epic only, with
exact model strings (ask when one is not): `$PY "$S/scope_reviewers.py" epic
--epic $EPIC --workflow audit --add|--add-optional|--remove|--replace|--adjudicator
... --requested "<the user's words>"`; show the warnings it prints.

## 1. Full audit

```bash
$PY "$S/scope_launch.py" review --host $HOST --workflow audit --mission full --epic $EPIC
```

The reviewers check the diff against the approved criteria, the plan and its
decision log, the verification record and its logs, and the final docs against
the code in both directions. A failed mandatory reviewer is retried once; an
optional one never blocks. Never replace or substitute a reviewer on your own:
if a mandatory one still fails, in any round, ask the user whether to wait and
rerun it (`--reviewers <name>`) or replace it for this epic (Setup). Until
every mandatory reviewer completes, the audit is **incomplete**: never ask the
user to accept it, and never count it as passed.

## 2. Fix, verify, adjudicate until settled

Loop on:

```bash
$PY "$S/scope_review.py" status --epic $EPIC --workflow audit
```

Act on the first that applies, then check status again:

- `needs_user`: product-scope disputes or `product_decision` findings. Ask the
  user all of them at once; record each answer with
  `$PY "$S/scope_review.py" decide --epic $EPIC --finding <id> --outcome
  finding_upheld|rejection_upheld --note "<the user's words>"`.
- `blocked`: a finding failed again after its diagnosis. Ask the user only for
  a product choice, an accepted risk (`rejection_upheld`), or more resources;
  otherwise stop and report the epic as blocked.
- `needs_diagnosis`: the finding survived two fixes. Run
  `review --workflow audit --mission diagnose --finding <id>`; the next
  implementer job gets the diagnosis.
- `needs_disposition`: run a fresh implementer (add any diagnosis to the task):

  ```bash
  $PY "$S/scope_launch.py" work --host $HOST --role implementer --epic $EPIC \
    --task "Resolve the open audit findings in review.md."
  ```

  Handle its status exactly as `/implement` step 1 does (`needs_check`,
  `needs_user`, renewed Gate 1, `blocked`). Only after `done`, run
  `$PY "$S/scope_verify.py" run --epic $EPIC --milestone remediation`; on
  failure send its problems to a fresh implementer and run it again.
- `needs_verification` or `needs_rejection_check`:
  `review --workflow audit --mission verify`.
- `needs_adjudication`: `review --workflow audit --mission adjudicate`.
- Nothing pending but not settled: rerun each reviewer in `missing_reviews`
  as in step 1; if none is missing, the branch changed after the last round,
  so run `scope_verify.py run --milestone remediation`, then the full audit.

Verification passes check only the named findings and add nothing new. A real
defect is never accepted because a round budget ran out.

**A round the user asks for always runs**, even when status is settled: a new
audit with `--mission full --reviewers <names>`, a re-verification with
`--mission verify --recheck [--finding <id>] [--reviewers <name>]`. Report the
audit passed only after that round, once status is settled again.

## Result

- **passed**: status reports `complete: true` and `settled: true`, and
  `scope_verify.py covers` passes.
- **incomplete**: a mandatory reviewer has not completed. Gate 2 is blocked
  until it runs; only the user can separately invoke the waiver in
  `/wrap_epic`.
- **blocked**: a finding needs the user or stays unresolved after diagnosis.

## Final response

Report the result, findings by severity and outcome (fixed and verified,
rejected and how adjudicated, accepted quality tradeoffs, user decisions),
verification counts, and anything open; end with the table of
`$PY "$S/scope_reviewers.py" metrics --epic $EPIC --workflow audit`. When
passed, recommend `/wrap_epic <epic>` (Codex: `scope:wrap_epic <epic>`).

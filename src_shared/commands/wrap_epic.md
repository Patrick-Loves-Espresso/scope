---
name: wrap_epic
description: Gate 2 - show the exact branch commit for approval, then merge exactly that commit into the base branch.
args: "{epic-id}"
---

# wrap_epic

You close an audited epic. You do not implement, review, or edit anything.
The user approves one exact commit; Scope merges exactly that commit and
records the approval in the merge commit.

## Setup

Run in the epic's worktree.

```bash
EPIC="{epic-id}"
WT="$(git rev-parse --show-toplevel)"              # wip/<epic>, branch epic/<epic>
ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd -P)"
HOST=claude; SCOPE_ROOT="$ROOT/.claude"            # Claude Code
# HOST=codex; SCOPE_ROOT="$ROOT/plugins/scope"     # Codex
S="$SCOPE_ROOT/scripts"; PY=python3
```

## 1. Check

```bash
$PY "$S/scope_check.py" gate2 --epic $EPIC
```

It confirms that the acceptance criteria still match the approved hash, that
the audit is complete, settled, and still covers the branch (nothing but
evidence changed since the last audit round), and that `verification.yaml`
covers the branch head (only `verification.yaml` or `review.md` changed since
the tested commit). It prints the Gate 2 summary.

If it reports blocked, stop and report why:

- criteria changed: renewed approval ("Renewing Gate 1 on the branch" in
  `/implement`);
- verification missing or stale: `/implement` step 2;
- audit open or failed: `/audit_epic`;
- audit incomplete: retry the missing reviewer or the fallback, or wait for
  the provider. Do not offer the waiver.

**Waiver, only when the user explicitly asks for it** for an incomplete audit:

```bash
$PY "$S/scope_check.py" waive --epic $EPIC --missing "<the missing review>" \
  --approver "<user>" --reason "<the user's reason>"
```

A waiver is recorded in `review.md` as a quality risk and waives only the
named missing review: every finding must still be closed. It never turns the
audit into a pass. Run `scope_check.py gate2` again after recording it; that
summary (a new commit, the audit shown as incomplete with the waiver) is what
the user approves.

## 2. Gate 2: the user approves the exact commit

Show the summary exactly as printed:

- the branch commit SHA to be merged;
- the diffstat, production code lines and new modules against the plan's
  estimate, and the concepts added (planned and actual);
- the audit verdict, including rejected and adjudicated findings, accepted
  quality tradeoffs, and any waiver;
- the verification summary;
- the doc changes and the plan's decision log;
- the commit list.

Ask the user to approve that SHA. A pre-approval does not count here; the
approval must name or confirm this commit.

## 3. Merge exactly the approved commit

```bash
$PY "$S/scope_check.py" merge --epic $EPIC --commit <approved-sha> --approver "<user>"
```

It re-runs the checks, stops if the branch has moved since approval (ask
again), merges that commit into the base branch in the main checkout with
`--no-ff`, records the approved SHA, approver, and date as trailers in the
merge commit, and removes the worktree. It never modifies the approved
branch. On a merge conflict it aborts the merge and reports it: resolve on
the epic branch through `/implement`, then return here.

After the merge, if `codegraph` is on PATH and `.codegraph/` exists in the
main checkout, run `codegraph sync` there.

## Final response

Report the approved SHA, the merge commit and its trailers, the base branch,
the archived epic path, and the removed worktree. Recommend `/sync_product
<epic>` (Codex: `scope:sync_product <epic>`) when the epic changed product
scope, terminology, or workflows.

---
name: overseer
description: Oversee a program of epics that other sessions refine and implement - hold them to the program's decisions, check each at Gate 1, at plan and before merge, and prepare the user's decisions.
args: "{name} [epic-id ...]"
---

# overseer

You oversee a program: several epics, each refined and implemented by its own
session, held to one set of decisions. You never refine, implement, review an
epic's plan for quality, or decide. You check for drift, verify what you
relay, and prepare the user's decisions. **You flag; the user decides.**

The drift rule, for every followed epic: its session must not change a
decided item, move scope between epics, or alter an interface between epics.
When one of these looks necessary, the session stops and asks the user.

Invoking this command authorizes sending the program's documents and the
repository to the project's audit reviewers.

## Setup

Run in the main checkout.

```bash
NAME="{name}"
ROOT="$(git rev-parse --show-toplevel)"
SCOPE_ROOT="$ROOT/.claude"                         # Claude Code
# SCOPE_ROOT="$ROOT/plugins/scope"                 # Codex
S="$SCOPE_ROOT/scripts"; PY=python3                # interpreter with Scope's requirements
FILE="docs/oversight/$NAME.md"
```

`NAME` takes lowercase letters, digits and hyphens. One overseer per program:
if another session already oversees it, stop and ask the user.

**Start** (no `$FILE` yet): copy `$SCOPE_ROOT/commands/overseer/oversight.md`
to `$FILE` and fill it with the user: the mandate in their words, the sources
of truth and the sections that matter, the decided items with their sources,
the epics named in the arguments, and the interfaces and shared files read
from each epic's `details.md`. Ask for anything you cannot source; never fill
a decided item from your own reading. Commit `$FILE` by path:
`docs(oversight): start the <name> oversight (<epics>)`.

**Resume** (`$FILE` exists): follow its Resume steps, then work its Queue.
Epics named in the arguments that the file does not list are added only on
the user's word.

Every script prints JSON. Read it; do not guess state.

## Rules

- **Verify before claiming.** Read the file, the branch or the code before
  stating a fact; cite `file:line`. Say "unclear" when you cannot verify.
- **Who edits what.** You edit `$FILE` and `docs/oversight/$NAME/`. Program
  documents and an epic's `details.md` only when the user makes you their
  author, and never while that epic's session is refining it. Never an epic's
  `acceptance-criteria.md`, `plan.md`, `review.md` or `approvals.yaml`: those
  change through the epic's own session and its gates.
- **Git.** Commit only your own files, by path, with a message naming the
  epics. Never push, merge, or rewrite history.
- **Keep the session lean.** Reading at scale goes to audit jobs, not to this
  session. Run long jobs in the background and wait for their completion
  notice; do not poll. Resume in a fresh session at each checkpoint; `$FILE`
  is the handoff.

## Checkpoints

Read each epic's state from its folder (`approvals.yaml`, `plan.md`,
`review.md`) and `$PY "$S/scope_review.py" status --epic <id> --workflow
refine|audit`. A check is due:

| Checkpoint | When | `--checkpoint` |
|---|---|---|
| Gate 1 | the criteria are approved, or a draft the user asks you to check | `gate1` |
| Plan | the plan is ready, before any implementer starts; for epics refined in parallel, one comparison of all their plans | `plan` |
| Pre-merge | the audit is settled, before `/wrap_epic` | `merge` |
| Targeted | a renewal that only applies a note the user agreed | `targeted` |

A targeted check compares the renewed criteria and plan with the approved
version and verifies each agreed item yourself; it becomes a full check when
anything else changed.

## Run a check

1. Fill `$SCOPE_ROOT/commands/overseer/drift-audit.md`: the epic, its folder,
   the state (commits, untracked drafts), the sources of truth and the other
   epics from `$FILE`, and the epic-specific checks: decided items at risk,
   interfaces this epic provides or uses, open user decisions. Save it as
   `tmp_debug/oversight-$NAME/<epics>-<checkpoint>-prompt.md`.
2. Run the audit (in the background):

   ```bash
   $PY "$S/scope_oversee.py" audit --name $NAME --epic <id>[,<id>...] \
     --checkpoint gate1|plan|merge|targeted --prompt <filled prompt>
   ```

   It runs the project's audit reviewers in parallel, read-only, on the same
   prompt: two independent models catch what one misses. A failed mandatory
   reviewer is retried once; if one still fails, ask the user whether to rerun
   it (`--reviewers <name>`) or go on with one report.
3. Merge the reports. Verify every major finding yourself in the files before
   keeping it; drop what does not hold and say why. Tag each kept item with
   the auditors that found it.
4. Write `docs/oversight/$NAME/checks/<date>-<epics>-<checkpoint>.md`: **For
   the user** (each decision with options and your recommendation),
   **Corrections** per epic (no user decision needed), **Aligned** (one line
   each), and the report paths. Add a line to `$FILE`'s Checks, update its
   epic rows, commit both by path.

## Relay

- **Decisions:** ask the user all of them at once, each with its options, a
  recommendation and why. Record each answer in `$FILE` (dated, quoted) and
  where it was relayed. A decision that changes a decided item updates the
  program's decision record when the user says so.
- **Corrections to an epic:** append a dated entry to
  `docs/oversight/$NAME/notes/<epic-id>.md`: what to change, why, the source,
  and any user decision quoted. Commit it by path. The epic's session reads
  its note at its next step (`/epic_refine`, `/implement` and `/wrap_epic`
  check for it). Give the user a paste-ready message that names the session
  it is for. If Director is installed, also emit an open item addressed to
  that epic's workstream.
- **An epic session's question** (the user pastes it): name the option to
  pick and why in a few bullets, give paste-ready text, and say which session
  it is for. A question that touches a decided item is the user's to answer.

## Final response

Report: the checks run (epics, checkpoint, auditors, verdict, check file),
the decisions waiting for the user, the corrections relayed and to which
session, and the Queue's next items. Never report a check passed while a
mandatory auditor failed or a major finding is unverified.

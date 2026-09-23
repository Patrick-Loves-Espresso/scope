---
name: session-handoff
description: Create an ephemeral preliminary/user-confirmed session-handoff.md at the active worktree or project root.
args: ""
skills: project-documentation
---

# /session-handoff

Create `session-handoff.md` in the active git worktree root, or in the current
directory when not inside a git repository. This file is ephemeral handoff
context for a fresh agent session and must not be committed.

**Syntax:**
- Claude: `/session-handoff`
- Codex: `scope:session-handoff`

## Intent

Long sessions degrade after many turns even with context compaction. This command
creates a concise, durable handoff packet that lets a fresh agent inspect the
current state and independently recommend a course of action to the user.

The handoff must not bias the next agent with unapproved instructions. It must
not contain `Next Action`, `Next Steps`, `Recommended Steps`, or `Resume
Options`.

The handoff may include reader guidance that explains how to use the file. This
guidance is not a project next action. It should tell the fresh agent to avoid
recapping the handoff and instead recommend a course of action for user
confirmation after reading it.

## Rules

- Overwrite `session-handoff.md` if it already exists.
- Write the file at the active git worktree root from `git rev-parse
  --show-toplevel`, or `pwd` when not in git.
- Ensure `session-handoff.md` is ignored locally:
  - If inside git, add it to `.git/info/exclude` when missing.
  - Do not modify the target project's tracked `.gitignore`.
- Use durable artifacts for observed state.
- Draft proposed goal, current status, constraints, and open questions from the
  current conversation plus durable artifacts.
- Write a preliminary handoff before asking the user to confirm or correct it.
- After user confirmation or corrections, overwrite the same file with the
  updated confirmed handoff.
- Separate observed facts from user-confirmed intent.
- A preliminary handoff must use `Status: preliminary handoff, user confirmation
  pending`.
- A confirmed handoff must use `Status: complete handoff`.
- Do not infer intent when durable artifacts are unclear. Ask the user.
- Do not create or reference `handoff.md`; the artifact name is always
  `session-handoff.md`.

## Execution

### Step 1: Locate Target Root

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ROOT="$(git rev-parse --show-toplevel)"
  GIT_DIR="$(git rev-parse --git-dir)"
  mkdir -p "$GIT_DIR/info"
  grep -qxF 'session-handoff.md' "$GIT_DIR/info/exclude" 2>/dev/null || \
    echo 'session-handoff.md' >> "$GIT_DIR/info/exclude"
else
  ROOT="$(pwd)"
  GIT_DIR=""
fi

HANDOFF_FILE="${ROOT}/session-handoff.md"
```

### Step 2: Inspect Durable State

Collect concise facts for the file. Do not print a verbose durable-state dump to
the user.

```bash
cd "$ROOT"

DATE_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
BRANCH="$(git branch --show-current 2>/dev/null || true)"
HEAD_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || true)"
WORKTREE_LIST="$(git worktree list 2>/dev/null || true)"
STATUS_SHORT="$(git status --short 2>/dev/null || true)"
STATUS_BRANCH="$(git status --short --branch 2>/dev/null || true)"
CHANGED_FILES="$(git diff --name-only 2>/dev/null || true)"
STAGED_FILES="$(git diff --cached --name-only 2>/dev/null || true)"
UNTRACKED_FILES="$(git ls-files --others --exclude-standard 2>/dev/null || true)"
```

Also inspect likely Scope artifacts when present:

- `docs/epics/*/details.md`
- `docs/epics/*/acceptance-criteria.md` and `approvals.yaml` (Gate 1 record)
- `docs/epics/*/plan.md` (story status, progress log, decision log)
- `docs/epics/*/review.md` (latest refine or audit round and open findings)
- `docs/epics/*/verification.yaml` (latest run, tested commit, outcome)
- the same files under `docs/epics/_implemented/*/` for epics archived on an
  epic branch, and worktrees under `wip/`
- `session-handoff.md` if it already exists

For each detected epic, record only factual state: file paths, latest
timestamps, story statuses and the latest review round or verification run as
the files state them. When Scope is installed, `scope_review.py status` gives
an epic's review state. Do not infer a phase or action unless an artifact
states it.

### Step 3: Draft Proposed Confirmation Fields

Draft concise proposed values for:

- Goal
- Current status
- Constraints and cautions
- Open questions or unconfirmed items

Use the current conversation when available. Use durable artifacts only to
support the draft, not to invent intent. If a proposed value is uncertain, say so
inside that field.

Do not draft or include any next action, recommendation, resume option, or
instruction for the fresh agent.

### Step 4: Write Preliminary `session-handoff.md`

Overwrite the file with this structure. Keep it concise and factual.

```markdown
# Session Handoff

Generated: {DATE_UTC}
Repository root: {ROOT}
Worktree: {worktree path or "not a git worktree"}
Branch: {BRANCH or "not available"}
HEAD: {HEAD_COMMIT or "not available"}

Status: preliminary handoff, user confirmation pending

## Reader Guidance

This handoff is for immediate continuation after a long or noisy session, not
for recovering a stale session days or weeks later. After reading it, do not
summarize the handoff back to the user. Use it as context to assess the current
state and recommend a concise course of action for user confirmation.

## Proposed Goal

{Agent-drafted goal/objective/intent. Pending user confirmation.}

## Proposed Current Status

{Agent-drafted current status. Pending user confirmation.}

## Observed Repository State

- Git status summary: {STATUS_BRANCH}
- Modified files: {changed files summary}
- Staged files: {staged files summary}
- Untracked files: {untracked files summary, excluding ignored session-handoff.md}
- Handoff file: session-handoff.md (ephemeral, locally ignored when inside git)
- Relevant epic/workflow artifacts detected: {paths and factual statuses}

## Work Completed In This Session

{Factual summary from conversation plus durable evidence. Do not invent work from
missing context.}

## Validation Evidence

{Commands/results known from durable artifacts or conversation. Include
failures, skipped checks, and checks not run when known.}

## Proposed Open Questions / Unconfirmed Items

{Uncertainty only. No recommendations. No next action. Pending user confirmation.}

## Proposed Constraints And Cautions

{Agent-drafted constraints, files not to touch, known risks, platform
differences, intentionally deferred items. Pending user confirmation.}

## Important Context Pointers

{Files/artifacts a fresh agent should read before assessing the situation.}
```

### Step 5: Ask For Confirmation

After writing the preliminary handoff, do not print the full durable-state
summary. Report only the path and the proposed confirmation fields:

```text
Wrote preliminary handoff: {HANDOFF_FILE}

Please confirm or correct these fields:
- Proposed goal: {drafted goal}
- Proposed current status: {drafted status}
- Proposed constraints and cautions: {drafted constraints}
- Proposed open questions / unconfirmed items: {drafted open questions}
```

If the user confirms, overwrite the same file using the confirmed structure
below. If the user corrects any field, update the file accordingly. If the user
does not respond, leave the preliminary handoff in place.

### Step 6: Write Confirmed `session-handoff.md`

Overwrite the file with this structure after user confirmation or correction.

```markdown
# Session Handoff

Generated: {DATE_UTC}
Repository root: {ROOT}
Worktree: {worktree path or "not a git worktree"}
Branch: {BRANCH or "not available"}
HEAD: {HEAD_COMMIT or "not available"}

Status: complete handoff

## Reader Guidance

This handoff is for immediate continuation after a long or noisy session, not
for recovering a stale session days or weeks later. After reading it, do not
summarize the handoff back to the user. Use it as context to assess the current
state and recommend a concise course of action for user confirmation.

## User-Confirmed Goal

{Confirmed goal/objective/intent.}

## User-Confirmed Current Status

{User-confirmed current status. Required.}

## Observed Repository State

- Git status summary: {STATUS_BRANCH}
- Modified files: {changed files summary}
- Staged files: {staged files summary}
- Untracked files: {untracked files summary}
- Relevant epic/workflow artifacts detected: {paths and factual statuses}

## Work Completed In This Session

{Factual summary from user confirmation plus durable evidence. Do not invent work
from missing context.}

## Validation Evidence

{Commands/results known from durable artifacts or user confirmation. Include
failures, skipped checks, and checks not run when known.}

## Open Questions / Unconfirmed Items

{Uncertainty only. No recommendations. No next action.}

## Constraints And Cautions

{User-confirmed constraints, files not to touch, known risks, platform
differences, intentionally deferred items.}

## Important Context Pointers

{Files/artifacts a fresh agent should read before assessing the situation.}
```

Forbidden headings/content:

- `Next Action`
- `Next Steps`
- `Recommended Steps`
- `Resume Options`
- Any project-specific imperative instruction telling the fresh agent what to do
  next

### Step 7: Report

After writing the preliminary or confirmed handoff:

- Report the absolute path to `session-handoff.md`.
- Report that it was added to local git exclude when inside git.
- State that the file is ephemeral and should not be committed.

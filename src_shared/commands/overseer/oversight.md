# Oversight: {name}

The state of one program's oversight and the overseer's handoff: a new session resumes from this file alone.
Keep it current: update **State**, **Queue** and the epic rows at the end of every work session. Write facts with
their source (a commit, a file:line, the user's dated words), never from memory.

## Resume

1. Read this file, then the sources of truth below (the sections named), then each followed epic's `details.md`.
2. Run `git log --oneline -15` on the main branch and on each epic branch, and `git worktree list`; compare with
   **State** to see what moved. If Director is installed, read its open items for this program.
3. Work the **Queue** from the top.

## Mandate

{the user's words: what the program is, why it is overseen, quoted and dated}

The drift rule: an epic's session must not change a decided item, move scope between epics, or alter an interface
between epics. When one of these looks necessary, it stops and asks the user. The overseer flags; the user decides.

## Sources of truth

| Source | Sections to read | What it settles |
|---|---|---|
| {path} | {sections} | {program decisions, guardrails, design, decision records} |

## Decided items

Each item the epics must hold, with its source. Amend only with the user's dated decision.

| Item | Decision | Source |
|---|---|---|
| {short name} | {the decision} | {file:line or the user's dated words} |

## Epics

| Epic | Session (worktree, Director workstream) | Status | Gate 1 check | Plan check | Pre-merge check |
|---|---|---|---|---|---|
| {epic-id} | {who refines or implements it} | {draft, Gate 1 approved, plan ready, implementing, audited, merged} | {date and check file, or due} | {…} | {…} |

## Interfaces between epics

| Interface | Provided by | Used by | Shared files and their order |
|---|---|---|---|
| {what} | {epic} | {epics} | {files; which epic changes them first} |

## State

{dated: where each epic stands, what moved since the last session, what is waiting on whom}

## Queue

1. {the next action, most urgent first: a check due, a decision for the user, a note to relay}

## User decisions

Dated, quoted, with where each was relayed (an epic's note, a decision record).

- {date} — "{the user's words}" — {what it settles; relayed to …}

## Checks

One line per check: date, epics, checkpoint, auditors, verdict, the check file under `{name}/checks/`.

# Drift audit: <EPIC> (<CHECKPOINT>)

You are auditing epic <EPIC> for drift against a program of epics. Read-only: do not modify, stage or commit
any file; run no test that writes to a database; make no paid or network call. The epic is refined or implemented
by an independent session, at <STATE>. This is a drift audit, not a code-quality review: read the epic's
`review.md` so you do not repeat its findings.

**Read fully:** `<FOLDER>/details.md`, `acceptance-criteria.md`, `approvals.yaml`, `plan.md`, `review.md` and
`notes/` (those that exist); the program's sources of truth: <SOURCES>; the other epics this one depends on or
shares files with: <OTHER-EPICS>. Read an epic still on its branch with `git -C <worktree> show HEAD:<path>`,
and an uncommitted draft from disk. Read source code to verify every claim and cite `file:line`.

**Check and report every deviation:**

A. **Scope.** Each in-scope item of `details.md`, and each part the program assigns to this epic, is covered by
   criteria, stories and tests. Nothing is pulled in from another epic or from deferred work; out-of-scope items
   stay out; epic numbers and names are current.
B. **Decided items.** Each decision listed in the sources, with the user's later amendments, is respected. A limit,
   default or threshold whose meaning changes is checked against every record that states the old value.
C. **Guardrails.** The program's guardrails and Scope's governance: growth justified line by line, removals
   listed; every new table, column, file kind or state has a reader in this epic; no migration, compatibility or
   back-fill code the program does not allow; after-merge actions for the user listed; no story above complexity
   7.
D. **Where drift hides.** Decision-log entries added during review rounds (a fix can add scope, compatibility code
   or paid calls); every new paid path (who pays, which window, which cap, and whether the cap is wired on that
   code path); channels or callers treated differently; lock, transaction and connection-budget changes; seams
   with other epics (ordering promised, code moved that others import); behaviour at the program's stated scale;
   stale numbering and wording; provenance of generated values.
E. **Interfaces.** What this epic provides to the other epics matches what they expect, with one owner per piece
   and nothing built twice or by no one; files several epics touch follow the program's order.
F. **Money and environments.** Every paid or live run has a cap the user approved, set from measured cost, and the
   cap is enforced on every paid path; validation runs only on the epic's own environment or database.
G. **User decisions.** Every answer the user gave this epic is written in the repository, dated and quoted.
H. **Anything else** that changes a decided item, moves scope between epics, or alters an interface without the
   user.

**Epic-specific checks:**

<EPIC-SPECIFIC-CHECKS>

**Output** a Markdown report under 2,500 words. For each finding: an ID; the severity (`major`: changes a decided
item, moves scope, alters an interface, adds a paid path or a repeated paid call, breaks a guardrail, misses a user
decision, or leaves epics colliding; `minor`: wording, a stale reference, unclear ownership); evidence with
`file:line` and a short quote; why it is drift (which decision, guardrail or interface); the correction; whether
it needs the user. Then list, one line each, what you checked and found aligned. Say "unclear" when you cannot
verify; never invent.

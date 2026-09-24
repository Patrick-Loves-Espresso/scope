# SCOPE - Simple Claude/Codex Orchestrator for Product Engineering

**Status:** Implementation (Scope 2.0, the lean lifecycle)

The design rationale and the evidence behind it are in
[`scope-simplification-plan.md`](scope-simplification-plan.md). This document
describes how Scope works now. For Codex, read `/command` as `scope:command`.

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. File Structure](#2-file-structure)
- [3. Commands](#3-commands)
- [4. The Epic Lifecycle](#4-the-epic-lifecycle)
- [5. Epic Artifacts](#5-epic-artifacts)
- [6. Launcher, Runner, and Checker](#6-launcher-runner-and-checker)
- [7. Review Rules](#7-review-rules)
- [8. Verification Rules](#8-verification-rules)
- [9. Simplicity and Size](#9-simplicity-and-size)
- [10. Agents and Skills](#10-agents-and-skills)
- [11. Documentation Structure](#11-documentation-structure)
- [12. Architectural Decisions](#12-architectural-decisions)
- [13. Guardrails Against Regrowth](#13-guardrails-against-regrowth)

---

## 1. Overview

Scope makes AI coding agents work like a disciplined product team that the
user directs rather than babysits: aligned with the user's intent, grounded in
durable documentation, and independently verified before anything is merged.

What carries the value, and is kept:

- the user approves the acceptance criteria (Gate 1) and the exact merged
  commit (Gate 2);
- durable documentation is an output of every epic, and the audit checks the
  docs against the code in both directions;
- a different provider reviews the plan and audits the result, and rejected
  findings are adjudicated by someone other than the author;
- tests are executed by Scope's runner, never reported by a model;
- implementation happens in a git worktree.

What Scope no longer does: story groups, debugging jobs, hash-bound receipts,
seals, manifests and their legacy readers, full-tree snapshots, all-provider
preflight barriers, and custom proof parsers. Git records what changed and
when; a small set of epic files records what was approved, reviewed, and
verified.

```
 user ──► command (Claude Code or Codex session: the only orchestrator)
               │
               ├── scope_launch.py work    ──► planner / implementer (host provider)
               ├── scope_launch.py review  ──► Claude + Codex reviewers (read-only)
               │                                └─ Muse Spark fallback
               ├── scope_verify.py         ──► runs the plan's validation commands
               ├── scope_review.py         ──► finding states from review.md
               └── scope_check.py          ──► criteria, plan, Gate 1, Gate 2, merge
```

---

## 2. File Structure

### 2.1 This Repository

```
scope/
├── src_shared/
│   ├── commands/        # Workflow commands (shared by Claude and Codex)
│   ├── workers/         # planner.md, implementer.md, reviewer.md
│   ├── scripts/         # scope_launch, scope_providers, scope_review, scope_verify, scope_check, scope_common
│   ├── config/          # scope-policy.yaml (the only policy file)
│   ├── governance/      # simplicity-and-size.md, developer-checklist.md
│   ├── agents/          # Standalone roles
│   ├── skills/          # project-documentation, project-tracking
│   └── docs/            # Reference docs installed for Codex
├── src_claude/agents/   # Claude model routing for the developer agent
├── src_codex/           # Codex plugin manifest, workflow skill, docs, developer agent
├── tests/
│   ├── workflow/        # Black-box lifecycle test with fake provider CLIs
│   └── unit/            # Focused tests of the scripts
└── docs/                # This document, the simplification plan, lessons
```

### 2.2 Target Project

```
your-project/
├── .claude/ or plugins/scope/   # commands, workers, scripts, config, governance, agents, skills
├── docs/
│   ├── product/                 # strategy, definition, reference, decisions.md (PDRs)
│   ├── architecture/            # current state only: arc42 01-13, ADRs, 13-specs
│   ├── epics/<epic>/            # the epic's files while it is being refined and built
│   ├── epics/_implemented/<epic>/
│   └── lessons-learned/
├── wip/<epic-id>/               # worktree on branch epic/<epic-id>
└── tmp_debug/scope-runs/<epic>/ # git-ignored prompts, transcripts, and test logs
```

---

## 3. Commands

| Command | What it does |
|---|---|
| `/prd_create` | Interview the user for a first-pass PRD |
| `/prd_refine` | Refine the PRD with a checklist |
| `/prd_breakdown` | Break the PRD into epics with dependencies |
| `/epic_refine {epic}` | Criteria and Gate 1, plan, one review by Claude and Codex |
| `/implement {epic}` | Worktree, one implementer, runner verification, then the audit |
| `/audit_epic {epic}` | Two-provider audit with fix, verify, and adjudication |
| `/wrap_epic {epic}` | Gate 2 on the exact commit and the pinned merge |
| `/sync_product [epic]` | Update product docs after scope-changing work |
| `/re_documentation`, `/re_ops` | Reverse engineer docs from an existing codebase |
| `/decision`, `/lesson`, `/audit_decisions` | Capture decisions and lessons |
| `/session-handoff` | Write an ephemeral handoff for a fresh session |

Each lifecycle command prompt is at most 150 lines. The prompts describe the
orchestration; the scripts do the deterministic work.

---

## 4. The Epic Lifecycle

```text
/prd_breakdown ─► details.md
/epic_refine
  1. planner drafts acceptance-criteria.md (size estimate, "not building")
  2. GATE 1: user approves → approvals.yaml (blob hash + commit)
  3. planner writes plan.md
  4. Claude + Codex review (high effort); fix or reject; verify; adjudicate
/implement  (worktree wip/<epic>, branch epic/<epic>)
  5. one implementer: commit per story, size check per story, runner per milestone
  6. docs finalized, epic folder archived on the branch
  7. final runner verification
/audit_epic
  8. Claude + Codex audit (docs <-> code); fix, verify, adjudicate until settled
/wrap_epic
  9. GATE 2: user approves the exact commit
 10. --no-ff merge of that commit with approval trailers; worktree removed
```

### 4.1 `/epic_refine`

Runs in the main checkout. A planner job drafts `acceptance-criteria.md`;
`scope_check.py criteria` checks its structure; product questions go to the
user in one batch. At Gate 1 the user approves the criteria, the size
estimate, and the "not building" list as shown; `scope_check.py approve`
commits the file and records its git blob hash and commit in `approvals.yaml`.
A planner job then writes `plan.md`, which `scope_check.py plan` checks. One
full review by Claude and Codex follows, and the orchestrator loops on
`scope_review.py status` (section 7) until the review is settled.

Epics planned by Scope 1.x are re-planned: their old-format artifacts are
removed with a commit, and the criteria are re-drafted and re-approved.

### 4.2 `/implement`

Requires approved, unchanged criteria, a valid plan, and a settled refinement
review. It creates `wip/<epic>` on branch `epic/<epic>` and launches one
implementer, which works through the plan, commits per story, runs
`scope_verify.py size` after each story and `scope_verify.py run` at each
milestone, finalizes the durable docs, and archives the epic folder to
`docs/epics/_implemented/`. It stops with `needs_check` for a size overrun or
a departure from the reviewed plan (a new migration, a changed permission
boundary, a new or changed external contract, a dropped proof or doc
obligation); another provider classifies it. Scope growth goes to the user;
implementation growth continues and is shown at Gate 2. A change to the
criteria is re-approved on the branch. The orchestrator then runs the final
verification and executes `/audit_epic`.

### 4.3 `/audit_epic`

Claude and Codex audit the branch: the diff against the criteria, the plan and
its decision log, the verification record and its logs, and the final docs
against the code in both directions. They also flag over-engineering in the
delivered code. The implementer fixes or rejects findings, the runner
re-verifies, and the loop of section 7 continues until settled. If a provider
is unavailable, Muse Spark replaces it; if two independent reviewers still
cannot complete, the audit is incomplete and blocks Gate 2.

### 4.4 `/wrap_epic`

`scope_check.py gate2` confirms that the criteria match their approval, the
audit is complete, settled, and still covers the branch, and the verification
covers the branch head, then prints the Gate 2 summary. After the user
approves that exact SHA, `scope_check.py merge` re-checks, refuses if the
branch moved, merges the commit into the base branch with `--no-ff` and the
trailers `Scope-Approved-Commit`, `Scope-Approved-By`, and `Scope-Approved-On`,
and removes the worktree. The approved branch is never modified. Only an
explicit user request records a waiver for an incomplete audit, one per
missing provider review, and only when at least one independent review
completed; it is a quality risk, never a pass.

---

## 5. Epic Artifacts

| File | Written by | Content |
|---|---|---|
| `details.md` | `/prd_breakdown` | Goal, scope, non-goals |
| `acceptance-criteria.md` | Planner; approved by the user | `AC-NNN` criteria, size estimate, "not building", open questions |
| `approvals.yaml` | `scope_check.py approve` only | Blob hash and commit of the approved criteria, source, date |
| `plan.md` | Planner, then implementer | ExecPlan: approach, milestones, stories, validation, criterion→test map, doc obligations, concepts, logs |
| `review.md` | `scope_launch.py` (rounds), authors (dispositions) | All findings with dispositions, verification, adjudication, checks, user decisions |
| `verification.yaml` | `scope_verify.py run` only | One run per milestone: tested commit, commands, counts, criteria, size, problems |

Machine-read parts of `acceptance-criteria.md` and `plan.md` are fenced blocks
tagged `yaml scope`. **Evidence commits:** `verification.yaml` names the tested
commit; a later commit stays covered only when it changes nothing but
`verification.yaml` or `review.md`.

---

## 6. Launcher, Runner, and Checker

| Script | Responsibility |
|---|---|
| `scope_providers.py` | Provider command lines, preflight, run with timeout and process-tree kill |
| `scope_launch.py` | `work` (planner, implementer) and `review` (full, verify, adjudicate, check, diagnose) jobs |
| `scope_review.py` | `review.md` parsing, finding states, `status`, user `decide` |
| `scope_verify.py` | `run`, `size`, `covers`, `lines` |
| `scope_check.py` | `criteria`, `approve`, `plan`, `gate2`, `merge`, `waive` |

Every script prints JSON. Provider invocation keeps the flags proven in Scope
1.x: Claude runs with `--print --safe-mode --strict-mcp-config --no-chrome
--no-session-persistence --permission-mode dontAsk`, and its reviewers may use
only read tools and read-only `git`/`codegraph` commands; Codex runs `exec
--ephemeral --ignore-user-config` with a `read-only` sandbox for reviewers and
`workspace-write` for workers (implementers in a worktree also get the git
directory via `--add-dir` so they can commit); OpenCode runs `--pure --agent
plan`; Antigravity runs `--sandbox`. Preflight checks each CLI's version,
flags, authentication, or model catalog. Reviewers run in parallel; there is
no all-provider barrier. In refinement and audit, a failed Claude or Codex
review is retried once before the fallback replaces it. Reviewer output is
parsed leniently about Markdown decoration and separators (`verified.`,
`**R1.claude.1**`, `**DECISION:**`) and strictly about content: only assigned
finding IDs and allowed outcome words count.

The launcher appends `governance/simplicity-and-size.md` to every worker and
reviewer prompt, so the rules reach the model without depending on it choosing
to read them. `config/scope-policy.yaml` holds the model routing (workers per
host provider, reviewers per workflow), the standard and fallback reviewers,
timeouts, and the size limits.

---

## 7. Review Rules

Reviewer rounds are appended to `review.md`, each with the commit it reviewed.
Findings are `blocking`, `major`, or `minor`; severity is not maximized across
reviewers. Suggestions are optional and untracked.

- **Dispositions.** The author (planner or implementer) sets `fixed`,
  `rejected` (with a reason), `disproportionate` (a minor fix that would add a
  requirement or mechanism), or `duplicate of <id>`, and may raise a minor to
  major.
- **Verification.** The reviewer that raised a finding verifies the fix. A
  verification pass checks only named findings and adds nothing new; it
  repeats only for findings still open. Fixed minors ride along with a pass
  that runs anyway; otherwise they close unverified.
- **Rejections.** A rejected blocking or major finding, any
  `disproportionate` rejection, and any security or data-integrity rejection go
  back to the raising reviewer. If it maintains the finding, the uninvolved
  provider adjudicates from scratch; when both providers raised it, the
  fallback does. Product-scope disputes go to the user. Other rejected minors
  stand as accepted quality tradeoffs, shown at Gate 2.
- **Stuck findings.** Two failed fixes trigger an independent diagnosis and a
  fresh author; a failure after the diagnosis goes to the user only for a
  product choice, an accepted risk, or more resources, and otherwise blocks
  the epic. No finding is accepted because a budget ran out.
- **Complete, fresh, settled.** A round succeeds when every reviewer in it
  completed or was replaced by a completed fallback; failed rounds count for
  nothing. A review is complete when two distinct providers completed the last
  successful full round or a full round on the same content (a rerun of a
  missing reviewer), fresh when nothing but evidence changed since the last
  successful reviewing round, and settled when it is complete, fresh, and every
  finding is closed. A finding's duplicates add their raisers, severity, and
  category to it; two failed verification rounds, not two reviewer answers,
  trigger a diagnosis. An explicit `--providers` choice cannot break these
  rules: a verifier must have raised the finding (or be the fallback), and an
  adjudicator or checker must be uninvolved.

---

## 8. Verification Rules

`scope_verify.py run` requires a clean, committed state. It runs each
validation command of the plan with a POSIX `sh -c` in its own process group
(a timeout stops the whole group), replacing `{junit}` with a fresh log path, and reads the JUnit XML that standard reporters write (pytest
`--junitxml`, `gotestsum --junitfile`, a Jest JUnit reporter). A run fails on
a non-zero exit, a missing JUnit file, a failure or error in the XML, or a skip
without a reason. Each criterion's mapped tests must have run and passed;
`command:<id>` maps a criterion to an exit-code-only command and is recorded as
such. Milestone runs require the criteria of finished stories; `final` and
`remediation` runs require all. Coverage of new and changed code is checked by
a standard tool declared as a `check` command.

`scope_verify.py size` counts production code lines added since the epic
branch forked (comments, docstrings, and blank lines excluded, by pygments)
against the plan's cumulative estimate for finished stories. Growth above 1.5×
(D3) triggers an independent check; after an accepted implementation-growth
check, the 1.5× test applies to the growth since then. It also reports each
changed module's code lines against 350/450 and changes outside the planned
paths.

---

## 9. Simplicity and Size

`governance/simplicity-and-size.md` is the one governance file every worker
and reviewer receives. It states the over-engineering tendency it counters and
the rules: build the minimum that satisfies the criteria, validate only at
boundaries, handle only errors that can occur, no single-use abstractions,
reuse first, fewer concepts and lines. Story complexity is scored 0–10 with a
maximum of 7; modules target 350 code lines with a hard limit of 450. Above a
limit the agent splits or records an exception that a reviewer judges; the
runner only reports.

---

## 10. Agents and Skills

Standalone agents (`architect`, `developer`, `product-owner`, and the
reverse-engineering roles) serve work outside the lifecycle commands; inside
them, the worker prompts in `workers/` apply.

- **project-documentation**: the documentation structure, templates (arc42 +
  C4 technical, Atlassian Blueprint product, epic templates), and the
  current-state rule.
- **project-tracking**: local YAML tracking by default; Jira adapters optional.

---

## 11. Documentation Structure

`docs/architecture/` holds the current state only: arc42 sections, specs,
schemas, and ADRs. Superseded content is replaced, not appended; nothing dated
or in-progress goes there. Epic working papers stay in the epic folder,
lasting decisions become ADRs (listed in `09-adr-summary.md`), product
decisions go to `docs/product/decisions.md`, lessons to
`docs/lessons-learned/`, and current behavior and contracts to the arc42
sections and `13-specs/`. The implementer applies this rule when it finalizes
an epic's docs, and the audit checks it.

System, backend, and frontend architecture each use the arc42 `01`–`13` tree
(`docs/architecture/`, `backend/`, `frontend/`); `13-specs/` is the canonical
home of machine-readable contracts.

---

## 12. Architectural Decisions

**12.1 One orchestrator, fresh workers.** The command is the only process that
talks to the user. Workers and reviewers run in fresh provider processes with
their own context, so the plan, not a conversation, carries the work.

**12.2 One implementer by default.** Current models sustain a whole epic.
Milestones, per-story commits, and the plan's logs make a long job resumable;
the orchestrator may split the work when size, context, integrations, or
uncertainty warrant it.

**12.3 Git is the record.** Per-story commits replace attribution machinery;
the Gate 1 record is a blob hash and a commit; the merge approval lives in the
merge commit's trailers.

**12.4 Cross-provider review with adjudication.** Claude and Codex catch
different defects. The author never has the final word on a finding against
its own work.

**12.5 Runner-executed tests with standard reporters.** Scope parses JUnit XML
from standard tooling instead of maintaining its own result formats.

**12.6 Native contracts over prose.** Plans and docs use project-native
contract forms (OpenAPI, schemas, SQL) in `13-specs/`.

**12.7 Local files for documentation.** Docs are Markdown in `docs/`, versioned
with the code.

**12.8 CodeGraph is optional.** When the CLI is installed, commands sync the
index once and workers and reviewers query it read-only; Scope does not manage
its lifecycle.

---

## 13. Guardrails Against Regrowth

Two earlier simplifications grew back. To keep this one small:

- **Complexity budget:** lifecycle Python ≤ 3,000 lines, modules ≤ 450 code
  lines, each lifecycle command prompt ≤ 150 lines, one policy file.
  `scripts/validate-pr-checks.sh` reports the totals on every PR.
- **Every new rule states the model weakness it assumes** and how to test
  whether it is still needed.
- **Incidents go to lessons-learned first.** Code is added only when a lesson
  recurs and no simpler fix exists.
- **Periodic ablation:** each quarter, or on a major model release, run one
  epic with a component removed.

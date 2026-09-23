# ExecPlan: lean post-breakdown lifecycle

This is a living plan. Keep Progress, Decision log, and Surprises current. A
fresh session resumes by reading this file, `docs/scope-simplification-plan.md`
(revision 7, the source of truth), and `git log main..lean-lifecycle`.

## Purpose

Deliver §6 Step 1 and Step 2 of the simplification plan as one release on
branch `lean-lifecycle`: the new `/epic_refine`, `/implement`, `/audit_epic`,
and `/wrap_epic`, their templates, governance, launcher, verification runner,
structural checker, and approval record; updated `/sync_product`, `/lesson`,
`/decision`, `/session-handoff`; the documentation rule; removal of the old
lifecycle and the web-epic tools; rewritten Scope docs. Out of scope: the pilot
(Step 3), Director (D16), any other project. Do not push or merge.

## Budgets (part of done)

| Budget | Limit | How measured |
|---|---|---|
| Lifecycle Python | ≤ 3,000 physical lines | `wc -l src_shared/scripts/*.py` |
| Module size | ≤ 450 code lines (target 350) | `scope_verify.py lines` (blank, comment, docstring lines excluded) |
| Command prompt | ≤ 150 lines each | `wc -l` of the four command files |
| Policy files | 1 | `src_shared/config/scope-policy.yaml` |

## Design

### Artifacts per epic (plan §4.2)

`details.md` (unchanged), `acceptance-criteria.md` (planner; Gate 1),
`approvals.yaml` (orchestrator via `scope_check.py approve` only), `plan.md`
(planner, then implementer), `review.md` (runner appends reviewer rounds;
authors edit only `disposition` lines), `verification.yaml` (runner only).
Machine-read parts of `acceptance-criteria.md` and `plan.md` live in fenced
blocks tagged `yaml scope`; everything else is prose.

### Scripts (`src_shared/scripts/`)

| Module | Responsibility |
|---|---|
| `scope_common.py` | Epic resolution, git helper, policy load, `yaml scope` block parsing |
| `scope_providers.py` | Provider CLIs (harvested flags), preflight, process run with timeout and tree kill, final-message extraction |
| `scope_launch.py` | Worker and reviewer jobs: prompt composition (governance appended), parallel reviewers, fallback, appending rounds to `review.md` |
| `scope_review.py` | Parse `review.md`; per-finding state; `status` for the orchestrator |
| `scope_verify.py` | Run declared commands on a clean commit, JUnit counts, criterion→test check, `verification.yaml`, size check, code-line counter |
| `scope_check.py` | Criteria format and approved hash, plan structure, Gate 2 summary, pinned merge with trailers, waiver |

### Provider invocation (harvested from `scope-worker.py`/`scope-reviewer.py`)

- Claude: `--print --safe-mode --strict-mcp-config --no-chrome
  --no-session-persistence --model --effort --permission-mode dontAsk`, tool
  lists per role; reviewers read-only (`Write,Edit,NotebookEdit,Task,Agent`
  disallowed; only read-only git and CodeGraph query commands allowed in Bash).
  Safe mode skips CLAUDE.md, so the prompt tells the worker to read it.
- Codex: `exec --ephemeral --ignore-user-config --cd --model -c
  model_reasoning_effort=… --sandbox read-only|workspace-write
  --output-last-message -`; implementers in a linked worktree also get
  `--add-dir <git common dir>` so they can commit, and `--add-dir` for the
  run-log directory.
- OpenCode (Muse Spark fallback): `run --pure --agent plan --model --variant
  --dir <root> <prompt>`. Antigravity (Gemini on request): `--model --sandbox
  --print-timeout --print <prompt>`.

### Script interfaces (all print JSON; `--root` defaults to the current git top level)

```text
scope_launch.py work    --host claude|codex --role planner|implementer --epic E --task TEXT
scope_launch.py review  --host H --workflow refine|implement|audit
                        --mission full|verify|adjudicate|check|diagnose --epic E
                        [--finding ID] [--context TEXT] [--size] [--providers a,b]
scope_launch.py preflight [--providers a,b]
scope_review.py status  --epic E --workflow refine|audit
scope_review.py decide  --epic E --finding ID --outcome finding_upheld|rejection_upheld --note TEXT
scope_check.py  criteria|plan|gate2 --epic E
scope_check.py  approve --epic E --source TEXT
scope_check.py  merge   --epic E --commit SHA --approver NAME
scope_check.py  waive   --epic E --missing TEXT --approver NAME --reason TEXT
scope_verify.py run     --epic E --milestone M
scope_verify.py size|covers --epic E
scope_verify.py lines   FILE...
```

Fixed commit labels: `refine(E): approve acceptance criteria (Gate 1)`,
`review(E): <workflow> round <n> <mission>`, `verify(E): <milestone>
verification record`, `audit(E): record audit waiver`, `merge(E): ...` with
trailers `Scope-Approved-Commit`, `Scope-Approved-By`, `Scope-Approved-On`.

### `review.md` rules

- Round header `## <workflow> <n> · <mission> · <timestamp>`, then
  `- commit: <sha>` (the reviewed commit; refinement rounds first commit the
  epic folder, audit rounds require a clean tree), reviewer lines
  `- reviewer <provider> · <model>/<effort> · <status> · <decision>`, and for
  size checks `- size: actual=<n> planned=<n> ratio=<r>`.
- Finding `### <R|A><round>.<provider>.<n> · <severity> · <category>` with
  `evidence`, `correction`, `closure`, `disposition` bullets; outcome lines
  `- <id> · <provider|user>: <outcome> — <text>`. A fallback reviewer's
  outcome is credited to the provider it replaced, with a `[fallback x]` note.
- Dispositions: `open`, `fixed`, `rejected`, `disproportionate` (minor-guard
  rejection), `duplicate of <id>`. Authors may raise minor severity to major.
- State per finding (raisers = the raising provider plus those of its
  duplicates): user `rejection_upheld` → closed. `product_decision` without a
  user decision, or `product_scope` → needs_user. `open` → needs_disposition;
  needs_diagnosis after two `still_open`; blocked when `still_open` follows a
  diagnosis. `fixed` → closed when every raiser's latest is `verified`;
  needs_verification when blocking/major, already in a pass, or a pass runs
  anyway; else closed unverified. `rejected`/`disproportionate` → closed on
  `rejection_upheld` or every raiser `rejection_accepted`; needs_adjudication
  when any raiser `maintained`; needs_rejection_check when blocking/major,
  security/data_integrity, `disproportionate`, already in a pass, or a pass
  runs anyway; else accepted quality tradeoff. The runner reopens a finding on
  `still_open`/`finding_upheld`. Adjudicator: first of standard + fallback that
  raised nothing; none → executable check or the user.
- A round succeeds when every reviewer completed, was replaced by a completed
  fallback, or (audit) was waived. Complete: two distinct providers completed
  the last successful full round or a full round on the same content. Fresh:
  nothing but evidence changed since the last successful full/verify/adjudicate
  round (refinement: within the epic folder, criteria and approvals allowed).
  Settled: complete, fresh, every finding closed. Duplicates add their raisers,
  severity, and category; failed verification rounds (not reviewer answers)
  count toward diagnosis; checks after a reopening only count.
- Waiver: one per missing standard provider, only with at least one completed
  independent review; never a pass.

### Verification rules

- Clean tree required; commands run with `sh -c`; `{junit}` is replaced by a
  log path. Missing/unreadable JUnit, failures, errors, non-zero exits, or a
  skip without a reason fail the run. A `type: test` command without `{junit}`
  records a gap.
- A criterion passes when every reference matches at least one test case
  (boundary-aware suffix match on normalized `classname.name`) and all matches
  passed; `command:<id>` references pass by exit code and are recorded as
  `passed_by_exit_code`. Milestone runs require criteria of `done` stories
  (others `pending`); `final` and `remediation` require all.
- Coverage of a later commit: the latest run is `final`/`remediation`,
  passed, and only `verification.yaml`/`review.md` under `docs/epics/`
  changed since its tested commit.
- Size: production code lines *added* (diff `-U0` added lines that are code
  per pygments; comments, docstrings, blank lines excluded) since the merge
  base with the main checkout's HEAD, against the sum of `estimate_loc` of
  `done` stories. Over when growth since the last accepted
  `implementation_growth` check exceeds 1.5 × planned growth since then (the
  whole epic when there was none). Changes outside production, test, and docs
  paths are reported as scope warnings.

### Lifecycle flow

- `/epic_refine` (main checkout): planner drafts criteria → `scope_check.py
  criteria` → Gate 1 → `approve` (commit + `approvals.yaml`) → planner writes
  `plan.md` → `scope_check.py plan` → full review (Claude + Codex, high) →
  resolve loop driven by `scope_review.py status` → commit plan.
- `/implement` (worktree `wip/<epic>`, branch `epic/<epic>`): one implementer
  job by default; it commits per story, runs `scope_verify.py size` after each
  story and `scope_verify.py run` at each milestone, stops with `needs_check`
  on size growth over threshold or a high-impact plan change; the orchestrator
  runs the independent check and restarts a fresh implementer; implementer
  finalizes docs and archives the epic folder; the orchestrator runs final
  verification, then executes `audit_epic.md`.
- `/audit_epic`: full audit (Claude + Codex; fallback Muse Spark), fix/verify/
  adjudicate loop, runner verification after remediation; incomplete audit
  never passes.
- `/wrap_epic`: `scope_check.py gate2` → Gate 2 on the exact SHA → `scope_check.py
  merge` (pinned, `--no-ff`, approval trailers) → worktree removed.

## Milestones

1. **M1 Design draft**: ExecPlan, templates, governance, worker/reviewer
   prompts, four command prompts, policy file. Codex read-only review #1.
2. **M2 Scripts and tests**: black-box workflow test with fake provider CLIs
   first, then the five modules, then unit tests (≥ 90% coverage).
3. **M3 Removal and updates**: delete the old lifecycle, web-epic tools,
   `/content_refine`, website-strategy skill; rewrite architect/product-owner/
   developer agents; update `/sync_product`, `/lesson`, `/decision`,
   `/session-handoff`, `/audit_decisions`, project-documentation skill;
   installers remove retired files; PR checks and CI updated with budget report.
4. **M4 Scope docs**: rewrite `README.md` and `docs/scope-architecture.md`
   (and Codex copies); delete superseded plans (D12).
5. **M5 Validation**: `git diff --check`, `scripts/validate-pr-checks.sh`,
   install smoke, dry run on a throwaway sample repo (user answers both gates),
   Codex review #2, final report.

## Progress

- [x] 2026-09-23 Read plan rev 7, repository rules, the old lifecycle.
- [x] 2026-09-23 Branch `lean-lifecycle` created from `cc99098`.
- [x] 2026-09-23 User decisions U1–U4 recorded (below).
- [x] 2026-09-23 Verified: Codex `workspace-write` + `--add-dir <git common dir>` can commit in a linked worktree.
- [x] 2026-09-23 M1 draft written; Codex review #1 (gpt-6-astra, high, read-only): 17 findings, all accepted and fixed (see below)
- [x] 2026-09-23 M1 committed (design draft after review #1)
- [x] 2026-09-23 M2: six modules (1,477 lines; largest 280 code lines) and 111 tests
  (black-box lifecycle with fake provider CLIs plus unit tests): 111 passed, 0
  failed, 0 errors, 0 skipped; 100% statement coverage of the new modules
  (subprocesses measured via `patch = subprocess`)
- [x] 2026-09-23 M3: 60 files deleted (old scripts, 10 policy/schema files, old
  workers, web-epic commands, `/content_refine`, website-strategy skill, old epic
  templates, folded governance, 20 old test modules); agents rewritten lean;
  `/sync_product`, `/lesson`, `/decision`, `/session-handoff`, `/audit_decisions`,
  the documentation skill, `details.md` template, Codex skill and README updated;
  installers (v2.0.0) remove retired files; PR checks rewritten (install smoke
  with retired files seeded, budgets, provider-flag contract, tests);
  `scripts/validate-pr-checks.sh` passes
- [x] 2026-09-23 M4: `README.md` and `docs/scope-architecture.md` rewritten (with
  the §6 regrowth guardrails), Codex copies synced, CONTRIBUTING updated,
  superseded plans deleted (D12); PR checks pass
- [x] 2026-09-23 M5 dry run (throwaway repo `texttools`, epic DRY-001 word count,
  host Claude, real Claude/Codex): Gate 1 approved by the user (4 criteria, 25
  LoC / 1 file); planner 318 s + 527 s + 241 s ($4.65); refine review: Claude
  approve, Codex 1 major (BOM handling vs AC-002), fixed and verified by Codex;
  implementer 502 s ($2.12), 2 story commits, M1/M2 runner records, docs and
  archive; final verification 6/6 tests, 4/4 criteria, lint/types/diff-cover
  passing; audit Claude (xhigh) and Codex (max) approve; Gate 2 approved by the
  user; merge `85b840c` of `b7ca5cb` with trailers, worktree removed. Size 21
  LoC vs 22 planned. Not exercised with real providers (covered by fake-provider
  tests): audit remediation, adjudication, fallback, size overrun, needs_check.
  Reviewer costs are not reported by the CLIs' text output.
- [x] 2026-09-23 Codex review #2 (gpt-6-astra, high, read-only, whole branch):
  13 findings; 12 accepted and fixed, F12 fixed in part (see below); 126 tests
  pass; PR gate passes
- [x] 2026-09-23 M5 final report delivered in the session. Final gate: `git diff
  --check` clean, 126 passed / 0 failed / 0 errors / 0 skipped, 100% statement
  coverage, install smoke passes, budgets: Python 1,529 lines, largest module
  288 code lines, prompts 139/119/110/99 lines, one policy file

## Decision log

| ID | Decision | Why |
|---|---|---|
| U1 | Rewrite `architect.md` and `product-owner.md` lean around `acceptance-criteria.md`/`plan.md` (user, 2026-09-23) | User choice; hostile cases limited per §5 |
| U2 | Audit reviewer effort unchanged: Codex `gpt-6-astra` max, Claude `claude-opus-5-5` xhigh (user) | Plan sets `high` only for refinement |
| U3 | Retire `/content_refine` and the website-strategy skill with D11 (user) | They only served the web-epic workflow |
| U4 | Keep CodeGraph use, drop `scope_codegraph.py` and its policy (user) | Value is in read-only queries; the 548-line manager is not core. Commands run one `codegraph sync` (or `init` when `.codegraph/` is missing and git-ignored) |
| U5 | Planner effort `xhigh` for Claude and Codex (user, after the dry run) | Planner jobs at max took 4–9 min for a 21-line epic |
| U6 | Planners may prototype in a temporary directory outside the repository (user) | The dry-run planner measured its design that way; the repository stays untouched |
| U7 | No detailed cost measurement in Scope; use `ccusage claude` and `ccusage codex` for testing and the pilot (user) | Reviewer CLIs do not report usage in the formats Scope reads |
| L1 | The implementer invokes the runner (`size` after each story, `run` at milestones); the orchestrator runs final verification | Only way to check per story inside one implementer job; numbers still come from the runner |
| L2 | `/implement` executes `audit_epic.md` in-session, as today | Keeps user stops at the two gates |
| L3 | Refinement commits (Gate 1, review rounds, final plan) land on the main checkout's current branch | Plan requires the Gate 1 commit; mirrors today's handoff commit |
| L4 | One shared policy file keyed by host provider; worker/reviewer "budget" profiles dropped | One-policy budget; profiles were extra configuration |
| L5 | Planner uses the old `design_handoff` models (effort later set to `xhigh`, U5), implementer the old `story` routing (also for audit fixes) | Nearest existing user routing; plan is silent |
| L6 | Any maintained rejection (any severity) goes to adjudication; findings reset to `open` by the runner after `still_open`/`finding_upheld` | One rule; lets the parser know an author must act again |
| L7 | After an accepted `implementation_growth` check, the 1.5× test applies to growth since that check (actual and planned increments) | Keeps D3 for all new work without re-triggering on already classified growth (review #1 F11) |
| L8 | Code lines counted with `pygments` tokens (comments and docstrings excluded); `pygments` added to requirements, `filelock`/`jsonschema` dropped | Multi-language, one small maintained dependency (already pulled in by pytest) |
| L9 | Any unavailable or invalid reviewer is replaced by the fallback; verification by an unavailable raising reviewer also falls back | Plan D10; no all-provider barrier |
| L10 | Antigravity's in-provider flash-model fallback is not carried over | Recovery machinery; Gemini is on-request only |
| L11 | `DIRECTOR_BIN` is not set by the runner yet | D16: Director is integrated only after this rebuild |
| L13 | `production-code-rules.md` and `test-strategy-guide.md` folded into `simplicity-and-size.md` and deleted; `developer-checklist.md` kept and rewritten | Plan §6 Step 1.2 ("fold or delete the rest"; the developer agent "and its checklist" reference the new file) |
| L14 | `/sync_product` also resolves archived epics under `docs/epics/_implemented/` | `/wrap_epic` recommends it after the merge, when the epic is already archived |
| L15 | Installer version 2.0.0 (both installers) | Breaking replacement of the lifecycle |
| L16 | Windows CI runs the platform-independent unit tests instead of the retired worker-recovery test | The fake-provider lifecycle tests use POSIX shell wrappers |
| L12 | Stale references to removed artifacts are fixed where they occur (`/audit_decisions`, `details.md` template, Codex docs); `/prd_breakdown` is left unchanged per plan | Removal consequence, not new design |

## Codex review #1 (design) dispositions

All 17 findings were accepted; none rejected.

| # | Sev. | Finding | Fix |
|---|---|---|---|
| F1 | blocking | Milestone runs required criteria of later milestones | Milestone runs require criteria of `done` stories; final/remediation require all |
| F2 | major | No criteria-renewal path during implementation/audit/wrap | "Renewing Gate 1 on the branch" in `/implement`; `approve` works in the worktree |
| F3 | major | Audit remediation ignored implementer statuses | Audit dispatches statuses as `/implement` step 1 |
| F4 | major | Audit completeness not tied to a reviewed commit | Rounds record `commit`; `fresh` required for settled and Gate 2 |
| F5 | major | No transition after diagnosis | `blocked` state when `still_open` follows a diagnosis; prompts handle it |
| F6 | major | Duplicate semantics undefined | Raisers include duplicates; all raisers verify; adjudicator excludes all raisers |
| F7 | major | Minor-guard rejection could become a tradeoff | `disproportionate` disposition always checked and adjudicated |
| F8 | major | Exit-code-only criteria could never pass | `command:<id>` references, recorded as `passed_by_exit_code` |
| F9 | major | JUnit failures masked by exit code | Failures, errors, missing JUnit all fail the run |
| F10 | major | Net size hid rewritten code | Count added code lines |
| F11 | major | Re-baselining exempted new growth | Incremental 1.5× test since the last accepted check |
| F12 | major | Waiver left Gate 2 summary stale | Waiver in `review.md`, re-run Gate 2, findings still must close |
| F13 | major | Gate 1 pre-approval not tied to content | Removed; approval of the content as shown |
| F14 | minor | Implementer stopped for already-planned work | Stop only for departures from the reviewed plan; resume via `implement` rounds |
| F15 | minor | Severity edits contradicted ownership text | Ownership text allows raising minor to major |
| F16 | minor | Gate 2 lacked concepts comparison | Gate 2 prints the plan's Concepts section |
| F17 | minor | No git-diff scope warning | `test_paths`; changes outside planned paths reported |

Suggestions taken: script paths quoted in prompts; commands run with `sh -c`
(documented). Regrowth guardrails go into `docs/scope-architecture.md` (M4).

## Codex review #2 (branch) dispositions

| # | Sev. | Finding | Disposition |
|---|---|---|---|
| F1 | blocking | A failed round advanced freshness; completeness was historical | Fixed: only successful rounds count; completeness from the last successful full round and same-content reruns |
| F2 | major | Any waiver covered every missing review | Fixed: one waiver per missing provider, and at least one completed review |
| F3 | major | State and verifier selection used different histories | Fixed: both use outcomes since the last reopening |
| F4 | major | Duplicates dropped their own requirements | Fixed: duplicates add severity and category to the shared finding |
| F5 | minor | Minors rode along only with blocking/major passes | Fixed: any mandatory check triggers the pass |
| F6 | minor | Two reviewer answers in one round triggered diagnosis | Fixed: count failed rounds |
| F7 | major | `--providers` could break independence | Fixed: verifier must be a raiser or the fallback; adjudicator/checker uninvolved |
| F8 | major | Zero planned growth disabled the size trigger | Fixed: growth with no planned growth is over |
| F9 | major | JUnit ids with `/` did not match | Fixed: normalize both sides |
| F10 | major | Duplicate validation ids reused reports | Fixed: ids unique (plan check and runner); report path cleared before each run |
| F11 | major | Timeout killed only the shell | Fixed: commands run in their own process group via the launcher's process runner |
| F12 | major | `shell=True` means cmd.exe on Windows | Fixed in part: commands always run with `sh -c`. Rejected: adding a Windows CI run of the runner; Windows runner support is not in the plan and was not validated in Scope 1.x either (stated as a limitation) |
| F13 | minor | `/decision` boundary too broad | Fixed: only ADR folders and `decisions.md` |

## Surprises and discoveries

- Codex loads the user's global `AGENTS.md` even with `--ignore-user-config`
  (a test commit reply carried the user's "--Response #N" rule). Relevant for
  D16 later; no action now.
- Dry run: the machine's Claude CLI (2.1.278) could not run `claude-opus-5-5`
  (needs ≥ 2.1.280). Preflight passed (flags, auth) but the job failed in 2 s
  with the API message surfaced in the job JSON; the user updated the CLI.
  Preflight does not probe model support (no catalog command for Claude).
- `git check-ignore .codegraph` reports "not ignored" for a missing directory
  when the ignore pattern is `.codegraph/`; the prompts now use
  `git check-ignore -q .codegraph/`.
- Dry run bug: `scope_common.git()` stripped leading whitespace, so the first
  `git status --porcelain` line of a modified tracked file lost a character
  (`ocs/epics/...`), producing a false planner warning and skewing freshness
  and coverage checks. Fixed (`rstrip`) with a regression test; the fake
  provider tests had only untracked files.
- Dry run gap: a Codex read-only reviewer could not open the CodeGraph
  database. The old runner passed `--add-dir <index>` to Codex reviewers; that
  harvested detail had been dropped and is restored with a test.
- 2026-09-23 User review of the final report: merge approved; planner effort
  `xhigh` (U5); planner prototyping outside the repository allowed (U6);
  `ccusage` for cost measurement (U7); Claude Code ≥ 2.1.280 required.

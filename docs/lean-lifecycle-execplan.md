# ExecPlan: lean post-breakdown lifecycle

This is a living plan. Keep Progress, Decision log, and Surprises current. A
fresh session resumes by reading this file, `docs/scope-simplification-plan.md`
(revision 7, the source of truth), and `git log` (the `lean-lifecycle` branch
is merged into `main` and was deleted on 2026-09-24).

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
- OpenCode (any `provider/model` the user chooses, U14): `run --pure --agent
  plan --model --variant --dir <root> <prompt>`, with a private data directory
  per run (U20). Antigravity was dropped with U14 (L34).

### Script interfaces (all print JSON; `--root` defaults to the current git top level)

```text
scope_launch.py work    --host claude|codex --role planner|implementer --epic E --task TEXT
scope_launch.py review  --host H --workflow refine|implement|audit
                        --mission full|verify|adjudicate|check|diagnose --epic E
                        [--finding ID] [--context TEXT] [--size] [--reviewers a,b] [--recheck]
scope_reviewers.py show [--epic E] [--preflight]
scope_reviewers.py set  --workflow refine|audit|both <changes> [--allow-max]
scope_reviewers.py epic --epic E --workflow refine|audit <changes> --requested TEXT [--allow-max]
                        changes: --add|--add-optional CLI MODEL EFFORT, --remove NAME,
                        --replace NAME CLI MODEL EFFORT, --adjudicator CLI MODEL EFFORT
scope_reviewers.py metrics --epic E --workflow refine|audit | --all
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

Reviewer selection, replacement, adjudication, and completeness as amended by
U14–U25 are summarized under "Configurable reviewers" below; the bullets here
keep the original wording where it still holds.

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
- [x] 2026-09-24 Pilot SAG-113 (sagara) implemented, audited, and merged on
  sagara `main` (`6ee973ae`, approved commit `30210a91`); see Surprises
- [x] 2026-09-24 Pilot follow-ups (U12, U13) on branch `feat/pilot-followups`:
  items 1, 2, 3, 5, 7, 8 implemented with tests; merged local branches deleted
- [x] 2026-10-02 Configurable reviewers (U14–U25, L23–L36) on branch
  `feat/reviewer-selection`: `scope_reviewers.py`, `/scope_reviewers`
  (Codex `scope:reviewers`), per-epic requests, adjudicator, OpenCode
  isolation, metrics, Gate 2 reviewer lines; tests and docs updated
- [x] 2026-10-02 Codex models switched to `gpt-6.1-sol` (U26); not yet run
  with the real Codex CLI
- [x] 2026-10-02 Claude models switched to the `opus` alias (U27); not yet run
  with the real Claude CLI

## Configurable reviewers (U14–U25, implemented 2026-10-02)

Goal: the user chooses the reviewers (a user may have only Claude, only Codex,
or want extra OpenCode models), and learns from metrics which reviewers add
value. Decisions U14–U25; implementer decisions L23–L36 (L29–L32 confirmed by
the user's "implement").

**Today.** A reviewer is identified by its CLI (`claude`, `codex`,
`opencode`): policy keys, finding IDs (`R1.codex.2`), credit, and waivers use
that name, so two OpenCode models would collide. Completeness is hard-coded to
two providers; `standard_reviewers` and the Muse Spark fallback are fixed in the
installed policy, which a reinstall overwrites. There are two reviewer sets,
`refine` and `audit`; `/implement`'s checks use the audit set's models.
Durations are measured but not written to `review.md`.

**Configuration.** A reviewer is `{cli: claude|codex|opencode, model: <exact
string passed to the CLI>, effort: <exact string>}`, plus `optional: true`
when it is optional; every other reviewer is mandatory. Each workflow also has
one adjudicator, defined the same way (U25). The project's settings live in
`.scope/config.yaml` under `reviewers: {refine: [...], audit: [...]}` and
`adjudicator: {refine: {...}, audit: {...}}`, written only by
`/scope_reviewers` (Codex `scope:reviewers`); the installers already create
that file once and never overwrite it. Without it, the defaults in
`scope-policy.yaml` apply: today's Claude + Codex reviewers and no adjudicator
(L30), since Scope cannot assume credits for any model. Scope never guesses a
model: OpenCode models are given exactly as `opencode run --model` takes them
(`zai/glm-5.3`, `meta/muse-spark-1.3-contributor`).

**`/scope_reviewers`.** Shows the effective lists with a preflight status per
reviewer; sets them for both workflows by default, only `refine` when the user
says architecture, epic_refine, or refine, only `audit` for implementation,
qa, audit, or audit_epic. Saving preflights every reviewer and reports
unavailable ones. It requires an adjudicator per workflow and warns, without
blocking, when the adjudicator has the same model and effort as a reviewer
(it would judge findings its own model raised). `max` effort is accepted only when the user
asks for it explicitly. Installed as `.claude/commands/scope_reviewers.md` and
`plugins/scope/commands/reviewers.md` (one rename line per installer).

**Per-epic requests.** "add …", "replace … with …", or "remove …" in the
arguments of `/epic_refine` (refine), `/implement` or `/audit_epic` (audit)
apply to that epic's workflow only. The launcher records them in `review.md`
as a `## <workflow> reviewers · <timestamp>` entry with the user's words and
the resulting list; later rounds, status, and Gate 2 use the latest entry.
Added reviewers are mandatory. The project lists are not changed.

**Review rules.**
- Complete: every mandatory reviewer of the effective list completed the
  latest full content (replaces "two providers"). Mandatory reviewers are
  retried once and are never replaced without the user's words (U9, U10
  generalized); optional reviewers are not retried, and their failure does
  not block.
- Blocking and major findings from optional reviewers must be resolved like
  any other; their minor findings never block settlement, and the author may
  fix them (U24).
- A finding is resolved whoever raised it. A replaced or removed reviewer's
  open findings are verified by its replacement (credited as the fallback is
  today), or, after a removal, by another reviewer of the list (L27).
- Adjudication: always the workflow's adjudicator (U25, L29), recorded in
  the round as `· adjudicator`, so its queries are counted apart even when
  the same model also reviews. Product-scope disputes still go to the user.
  The Muse Spark fallback is removed.
- `/implement` checks and diagnoses: the first mandatory audit reviewer on a
  CLI other than the host, else the first mandatory audit reviewer (L24).
- Authors must mark cross-reviewer repeats as `duplicate of <id>` (planner and
  implementer prompts), so unique counts are trustworthy.

**OpenCode concurrency.** Parallel `opencode run` processes share one SQLite
database and fail with lock errors (opencode issues #47566, #21215, open for
1.18.x). Each OpenCode run gets a private temporary directory as
`XDG_DATA_HOME` and `XDG_STATE_HOME`, with `auth.json` copied in (mode 0700,
outside the repository), removed after the run. These runs do not appear in
`opencode stats`.

**Metrics.** Reviewer lines in `review.md` gain the duration
(`· 412s`; old lines still parse). `scope_reviewers.py metrics --epic E
--workflow refine|audit` prints, per reviewer: model/effort, runs, total time
(full, verify, adjudicate, diagnose, retries), `blocking`, `major`, `minor` as
`n (unique)`, fixed, and rejected (rejection accepted or upheld). Unique: no
other reviewer's finding is a duplicate of it or it of theirs. `--all` adds up
every archived epic. `/epic_refine` and `/audit_epic` (and so `/implement`)
end with the table, followed by the adjudicator: model/effort, the number of
times it was queried, and its time. Gate 2 lists the reviewers and the
adjudicator who ran and flags a single-reviewer audit.

**Proposals (implementer).**
- L23 (confirmed by U23): reviewer names (used in finding IDs) are derived
  from the model: last path segment, lowercase, `.` → `-` (`gpt-6-astra`,
  `glm-5-3`); finding IDs become `R1.glm-5-3.2` and the ID patterns accept
  digits and hyphens.
- L24: check and diagnosis selection as above.
- L25: `.scope/config.yaml` is the project's configuration, not a second Scope
  policy file; the one-policy-file budget is unchanged.
- L26: the request and metrics procedure is written once in the reviewers
  command file; the lifecycle commands reference it (`/epic_refine` is at
  145/150 lines and gets trimmed).
- L27: after a removal, a removed reviewer's open findings are verified by the
  first remaining reviewer that did not raise them.
- L28: a new module `scope_reviewers.py` (effective lists, `show`, `set`,
  per-epic requests, `metrics`) keeps `scope_launch.py` (327 code lines) under
  its target.
- L29: every adjudication goes to the adjudicator, also when a reviewer that
  did not raise the finding exists (one rule; the plan's "other provider
  re-examines" becomes "the adjudicator re-examines").
- L30: no default adjudicator. Without one, `/epic_refine` and `/audit_epic`
  stop at setup and ask the user to run `/scope_reviewers`; existing projects
  run it once.
- L31: a failed adjudicator is retried once; then the user decides
  (`scope_review.py decide`) or names a replacement for that epic.
- L32: diagnoses after two failed fixes stay with a reviewer (L24), not the
  adjudicator. Per-epic requests may also replace the adjudicator.
- L33: the project's reviewers live in their own file, `.scope/reviewers.yaml`,
  not in `.scope/config.yaml`: rewriting the user's commented config file with
  a YAML dump would destroy its comments. Neither installer touches it; the PR
  check seeds one and verifies a reinstall leaves it unchanged.
- L34: Antigravity (`agy`) support is removed: U14 limits reviewers to
  claude, codex, and opencode, so its command line became dead code.
- L35: `scope_launch.py preflight` is replaced by `scope_reviewers.py show
  --preflight`, which checks the configured reviewers and adjudicator; no
  prompt used the old subcommand.
- L36: the per-epic request procedure is written inline in `/epic_refine`,
  `/implement`, and `/audit_epic` (about six lines each) instead of a
  reference into the reviewers command, whose installed file name differs per
  host (revises L26). `/epic_refine` stays at 150 lines by rewrapping and by
  listing the Scope 1.x artifacts with globs. Metrics count runs including
  failed attempts (shown as `n (k failed)`); Gate 2 lists only reviewers that
  completed at least once.

Resolved 2026-10-02: O1 → U23, O2 → U24, O3 → U25.

**Validation.** Unit and workflow tests with fake providers (two OpenCode
reviewers in parallel, a single reviewer, an optional failure, a per-epic
replacement, metrics with duplicates, a missing adjudicator, an adjudicator
duplicating a reviewer); `git diff --check`;
`./scripts/validate-pr-checks.sh` (budgets, install smoke with an existing
`.scope/config.yaml` preserved); mirrored docs.

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
| U8 | No `max` effort anywhere: implementers (Claude, Codex), the Codex audit reviewer, and the Codex developer agent lowered to `xhigh` (user, 2026-09-23; supersedes U2 for Codex) | User decision |
| U9 | In refinement and audit, a failed Claude or Codex review is retried once before the fallback (`standard_reviewer_retries: 1`); the fallback and `/implement` checks are not retried (user, 2026-09-24) | Claude and Codex are the reviewers that matter most |
| L17 | Reviewer output parsing is lenient about Markdown decoration and separators, strict about IDs and outcome words | A pilot verification that confirmed all findings was rejected because it wrote `verified.` instead of `verified —` (user report, 2026-09-24) |
| U10 | The fallback never replaces Claude or Codex automatically, in any mission; `--replace <provider> --approved-by` runs it in their place only with the user's explicit, recorded approval (user, 2026-09-24; amends D10 and supersedes L9) | A pilot showed OpenCode's automatic "verified" closing Claude's findings; the plan has the raiser verify |
| U11 | A round the user asks for always runs, even when settled: a new full round, or `verify --recheck [--finding]`, which re-sends closed findings to their raiser; the restate-your-outcomes follow-up is not added (user, 2026-09-24) | User authority over review rounds; lenient parsing and one retry already cover formatting slips |
| U12 | Pilot follow-ups (user, 2026-09-24): (1) plan.md logs one line per entry, command output and operational steps in working papers in the epic folder, plan length at Gate 2; (2) the planner records a baseline of the existing test, lint, and type commands before Gate 1, and pre-existing failures become one product question (fix in the epic, or leave out of validation with a reason); (3) `min_cli_versions` in the policy, checked by preflight (Claude Code ≥ 2.1.280); (5) optional `expected_paths` in plan.md; (7) stale `/prd_breakdown` lifecycle text fixed and the unused `orchestration` section dropped from `config_example.yaml` (supersedes L12 for `/prd_breakdown`); (8) `scope_check.py criteria` writes nothing | SAG-113: plan.md reached 2,148 lines; 76 unit failures already on main surfaced only at M1 and forced a Gate 1 renewal and an extra story; the dry run failed on Claude Code 2.1.278; config, requirements, and AGENTS.md changes showed as scope warnings |
| U13 | Not taken now (user, 2026-09-24): (6) the `scope_check.py report` command, deferred under the regrowth guardrail (code only when a lesson recurs); (9) Windows `sh` for the runner stays the limitation stated in F12/L16; (4) `verify --recheck` for SAG-113 dropped because the epic is merged; reinstalling Scope in sagara follows once this work is on `main` | User choice; the recommendations were given with the options |
| U14 | Reviewers are configured per workflow (`refine`, `audit`) as CLI (claude, codex, opencode), exact model string, and effort; every reviewer is mandatory unless listed as optional; one reviewer is allowed (user, 2026-10-02; Claude + Codex become the default only, superseding D2 as a fixed rule) | Users differ in subscriptions; extra reviewers show which models add value |
| U15 | Only `/scope_reviewers` (Codex `scope:reviewers`) changes the project's reviewers; a reinstall never overwrites them; it preflights each reviewer on saving and tells a single-reviewer user that they adjudicate (user, 2026-10-02) | User choice |
| U16 | Reviewer requests in `/epic_refine`, `/implement`, or `/audit_epic` arguments apply to that epic's workflow only, and added reviewers are mandatory (user, 2026-10-02) | A user adds a reviewer for an important reason |
| U17 | Blocking and major findings from optional reviewers must be resolved (user, 2026-10-02) | The goal is to learn who adds real value |
| U18 | A finding is resolved regardless of who raised it; a replaced reviewer's open findings are verified by its replacement (user, 2026-10-02) | User choice |
| U19 | With a single reviewer, the user adjudicates disputed rejections (user, 2026-10-02; superseded the same day by U25) | No uninvolved reviewer exists |
| U20 | Each OpenCode run gets a private data directory (user, 2026-10-02) | Concurrent runs on one SQLite database fail (opencode #47566, #21215) |
| U21 | Reviewer metrics at the end of `/epic_refine` and `/audit_epic`: model/effort, time, `blocking`/`major`/`minor` as `n (unique)`, fixed and rejected; across epics; Gate 2 lists the reviewers who ran; Scope's severity names (user, 2026-10-02) | Identify which reviewers add value |
| U22 | `max` effort only when the user asks for it explicitly (user, 2026-10-02; amends U8) | User choice |
| U23 | Reviewer names, used in finding IDs, are derived from the model string (user, 2026-10-02) | Exact, and gives per-model metrics with no extra input |
| U24 | An optional reviewer's minor findings never block settlement; the author may fix them (user, 2026-10-02) | Only blocking and major findings from optional reviewers must be resolved (U17) |
| U25 | Each workflow has one user-defined adjudicator (CLI, exact model, effort); no model is assumed (the Muse Spark fallback is removed); a warning, not a block, when it has the same model and effort as a reviewer; the metrics list the adjudicator and how often it was queried (user, 2026-10-02; supersedes U19 and D10) | Adjudication is important, and Scope cannot assume the user has credits for any model |
| U26 | Codex models: `gpt-6-astra` (planner, default reviewers) and `gpt-6-sol` (implementer, Codex developer agent) become `gpt-6.1-sol`, efforts unchanged (user, 2026-10-02) | User choice; GPT-6.1 Sol was released 2026-09-29 and supports `xhigh`; the local Codex CLI is 0.160.0, and no minimum CLI version for it is documented |
| U27 | Claude models use Claude Code aliases that follow the latest model of the family: `opus` for the planner, implementer, default reviewers, and developer agent (architect and product-owner already did); per-version reviewer comparison is not needed (user, 2026-10-02) | Always the latest Opus without editing Scope; Codex has no such alias, so `gpt-6.1-sol` stays pinned. A new Opus arrives with a Claude Code update, and `min_cli_versions` keeps an outdated CLI from resolving `opus` to an older model |
| L18 | Completeness is judged on the content of the latest full round (all full rounds on that content count); freshness still needs a successful round | A partly failed round no longer lists the provider that completed as missing |
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
| L19 | The baseline lives in `acceptance-criteria.md` (section "Baseline"), not `plan.md`, and `scope_check.py` does not enforce it | The user must see it at Gate 1, before the plan exists; U12 item 2 is prompt and template only |
| L20 | `preflight` takes an optional `minimum`; the version is the first dotted number in `--version` output, compared numerically; an unreadable version counts as older; only Claude has a minimum | Only Claude has evidence of a minimum (the dry run); no speculative minimums for the other CLIs |
| L21 | The changed-criteria diff is a `difflib` unified diff of the approved blob against the current text (labels `approved`, `current`) | Without `hash-object -w` the current blob is not in the object store, so `git diff <blob> <blob>` cannot run |
| L22 | Working papers are suggested as `notes/<story>.md` in the epic folder; nothing enforces the name | A concrete example helps agents; the destination rule (§4.7) already allows it |
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
- 2026-09-24 The handoff brief said SAG-113 was waiting for Gate 2. It had
  already been merged on sagara `main` (`6ee973ae`, 21:56 local, trailers
  pinning `30210a91`, approver Patrick), and `/sync_product` ran at 22:04
  (`f0934824`). The worktree is gone; the local branch `epic/SAG-113` remains.
  A `verify --recheck` no longer applies.
- 2026-09-24 SAG-113's plan.md (2,148 lines): Stories 511 (about 440 of them
  "Story notes" that pre-specify implementation steps), Decision log 403,
  Progress log 394, Approach 267, Validation 182. One story note told the
  implementer to record commands and diffs in the progress log, which is where
  the transcripts came from. U12 bounds the logs; story notes are not bounded.

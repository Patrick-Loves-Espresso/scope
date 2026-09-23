# Scope Simplification Plan

**Status:** Approved by the user on 2026-09-22 (revision 7)
**Date:** 2026-09-22
**Scope of change:** the post-breakdown lifecycle (`/epic_refine`, `/implement`,
`/audit_epic`, `/wrap_epic`) and the scripts, policies, templates, and
governance that support it.
**Out of scope:** `/prd_create`, `/prd_refine`, `/prd_breakdown`, `/sync_product`,
`/re_documentation`, and the durable documentation skill. These stay as they
are, except that commands reading retired epic artifacts are updated to the new
ones (§6 Step 1), and the documentation skill gains the current-state rule
(§4.7). Scope is generic: nothing in this plan acts on a specific project.
Evidence from projects Scope has run on is cited as evidence only.

---

## 1. Objective

Scope exists to make AI coding agents work like a disciplined product team that
the user directs rather than babysits: aligned with the user's intent, grounded
in durable documentation, and independently verified before anything is merged.

That purpose has not changed. What has changed is the models. Opus 5.5 and
GPT-6 Sol plan well, sustain multi-hour work, debug their own failures, and hold
a whole epic in context. Much of Scope's current machinery was built to
compensate for weaknesses that current models no longer have, and some of it now
works against the purpose. It makes the product larger and the process slower.

**The objective of this plan is to keep what makes Scope valuable and remove what
the current models no longer need.** Concretely:

1. The product built for an epic should be proportional to its approved
   acceptance criteria. Growth beyond the plan becomes visible early, and a
   material change of scope needs the user's approval.
2. The user keeps authority where it creates alignment: approving acceptance
   criteria, genuine product decisions, and the final merge.
3. Durable documentation (PRD, ADRs, arc42 architecture, schemas and specs,
   product docs) remains a first-class output of every epic. `docs/architecture/`
   holds the current state only.
4. Independent verification by different providers stays, focused on what
   matters.
5. Scope's own control surface shrinks substantially and stays small.

### Success criteria

Measured on a pilot epic and compared against SAG-122:

| Measure | Target |
|---|---|
| Production LoC vs. planned estimate | Reported and explained. A warning signal, not a quality measure; read together with the measures below |
| Concepts added | New modules, abstractions, and persisted state reported per epic and compared with the plan |
| Defects found after merge | Tracked over the next two epics |
| Verification time | Recorded for refinement review and audit |
| User stops | 2 planned gates (acceptance criteria, merge). Exceptions only for genuine product decisions, scope growth confirmed by an independent check, an accepted risk or a need for more resources, or a product dispute that adjudication cannot settle |
| Refinement review rounds | 1 full review; verification passes only for still-open blocking or major findings |
| Audit rounds | 1 full audit; verification passes only for still-open findings. No finding is accepted because a round budget ran out |
| Independent verification | Every blocking or major finding is either fixed and verified by the reviewer that raised it, or its rejection is upheld by independent adjudication. A one-provider audit is never reported as complete |
| Reruns caused by Scope infrastructure | 0 |
| Scope-only artifacts per epic | None: no receipts, packets, seals, or manifests. The epic keeps its contract, plan, review, approval record, and verification record |
| Durable docs updated in the same epic | 100% of declared doc obligations, and the final docs are audited |
| Story complexity | Every story ≤ 7 on the 0–10 scale |
| Module size | No production module above 450 code lines (target ≤ 350), excluding blank lines, comments, and docstrings |
| Scope Python control surface | ≤ 3,000 lines (today: ~12,700) |

Token cost and wall time should also be recorded. Scope has never measured
them, so the pilot establishes the first baseline.

---

## 2. What the evidence shows

### 2.1 SAG-122: a concrete case

SAG-122 (disambiguation evaluation baseline) is the most recent full run of the
current process. Its implementation sits uncommitted in the `wip/SAG-122`
worktree.

| Item | Measured |
|---|---|
| Written size estimate before refinement | 700–1,100 LoC of tooling, "no generic experiment platform" (`sagara/docs/architecture/backend/08-cross-cutting/disambiguation-scope-complexity-review-2026-09-14.md:90`). The same document calls its ranges "preliminary… potentially optimistic, not a reliable delivery ceiling" (line 84) |
| Production Python delivered | 8,704 lines in 5 modules: 8–12× that preliminary estimate, and about 3–4× the 2–3K recalled from refinement |
| Test Python | 5,243 lines, plus 1,696 lines of fixtures |
| `design.md` during refinement | Grew from 284 to 936 lines over the review cycle |
| Share of final code present after the first implementation | ~92%. Audit remediation added ~8% |
| Epic folder | 122 files, 13,241 lines. Only ~13% is the contract (details, ACs, design, manifest, file plans); the rest is receipts, reviews, proofs, and evidence |
| Refinement review rounds | 7. Only 2 produced findings. The others were reruns for an expired login, an outdated CLI, a validator deadlock, the manifest v3 migration, and a portability fix |
| Workaround scripts outside Scope | 38 scripts (5,275 lines) under `sagara/tmp_debug/scope-runs/SAG-122/epic_refine/` |
| Manual recovery snapshots | `implement.before-runner-fix-…`, `run.before-attribution-recovery-…`, `run.before-worktree-removal-…` |

**Conclusion:** the size was mostly set during refinement, before any code was
written. The design roughly tripled under review rules that treat every
under-specified contract as blocking and offer no way to reject a finding as
disproportionate. Implementation then faithfully built the enlarged design.
Some of the growth is genuine: the goal required frozen hashes and joint gates,
a user-run red-team step added more, and the only written estimate was
explicitly preliminary. But no Scope mechanism ever asked whether the design
was still proportional to the goal.

### 2.2 Scope itself: complexity creep

Measured across Scope's 80 commits (2026-02-11 → 2026-09-22):

- **Complexity moved rather than disappeared.** The four post-breakdown prompts
  shrank from 399 KB (July peak) to 60 KB, while the control surface (prompts +
  scripts + config + tests) grew from 19.1K to 33.7K lines. Python scripts went
  from ~0 to 12,730 lines, tests to 9,293 lines (350 test functions).
- **Reactive growth.** 31 commits were fixes or hardening after failures in real
  epics. They added a net ~5.9K lines. The pattern is: an incident in an epic
  leads to a new rule, validator, or recovery path, which adds surface area and
  causes the next incident. "recover" appears 40 times in the scripts. Written
  lessons stopped after 2026-04-01; incidents turned into code instead.
- **Self-management dominates.** An estimated 36–50% of the Python manages
  Scope's own state: hashes, receipts, snapshots, seals, locks, run recovery,
  and legacy formats. About 244 validator checks (`errors.append`) inspect
  Scope's YAML. None inspect the product's code quality or size.
- **Simplifications regrew.** The April agent rewrite and the August bloat
  removal were both undone within weeks. The core files are within 2% of the
  11,000-line hard cap set in `docs/refactoring-bloat-removal.md`; all scripts
  combined already exceed it.

### 2.3 The mechanisms that inflate the product

Each item below is verified in the source at commit `e2c6ce1`.

| # | Mechanism | Where | Effect |
|---|---|---|---|
| P1 | Reviewers check that "hostile cases are rejected by concrete contracts or fail-closed behavior" and that implementation can proceed "without invention" | `src_shared/commands/epic_refine/reviewer-refinement.md:48,52,72-74` | Every unspecified detail becomes a blocking finding. The design must pre-specify everything. |
| P2 | Architects must build a full authority→proof flow and "the strongest plausible hostile implementation" for each high/critical requirement | `src_shared/agents/architect.md:97-106`; design template FLOW/HOSTILE sections (`templates-technical-arc42-c4/epic/design.md:53-92`) | Each hostile case turns into validation, hashing, and fail-closed code |
| P3 | Findings can only be fixed, verified, deferred, or accepted as risk. There is no "rejected" or "disproportionate" status | `src_shared/config/refinement-policy.yaml:180-195` | False positives and overreach become work |
| P4 | Minor findings are mandatory in the first two reviews, and in both audit rounds | `src_shared/commands/epic_refine.md:205-211`; `audit_epic.md:117-118` | More rounds, more contract |
| P5 | Audit synthesis keeps the maximum severity across reviewers | `src_shared/commands/audit_epic.md:198-201` | Severity ratchets up |
| P6 | No size or proportionality check anywhere. Scope "does not impose a fixed story count, file count, or line count" and nothing estimates size | `docs/scope-architecture.md:340-342` | Growth goes unnoticed until the code exists |
| P7 | Worker prompts contain only the role file plus CLAUDE.md/AGENTS.md. The governance rules, including "Trust internal function calls (don't re-validate within the same service)", never reach workers | `src_shared/scripts/scope-worker.py:1179-1203`; `src_shared/governance/production-code-rules.md:61-62` | SAG-122 stories re-checked earlier hashes at each stage |
| P8 | Reviewers run at maximum effort; a high or critical risk label adds a specialist reviewer | `src_shared/config/reviewer-policy.yaml:117-125`; `refinement-policy.yaml:158-160` | More, and more exhaustive, findings. The specialist alone raised 5 blocking candidates in SAG-122 |
| P9 | Every story must reach 90%+ coverage, and proof obligations may never be weakened | `src_shared/governance/test-strategy-guide.md:13-20`; `epic_refine.md:157` | Tests scale with the enlarged contract |
| P10 | The audit reviewer is told "Do not redesign approved architecture". No reviewer is ever asked what could be removed | `src_shared/commands/audit_epic/reviewer-audit.md:54` | Nothing pushes toward a smaller solution |

### 2.4 The mechanisms that inflate the process

| # | Mechanism | Where | Model weakness it assumes |
|---|---|---|---|
| S1 | Implementation is split into dependency-connected groups of at most 3 stories, each with its own worker and checkpoint | `src_shared/config/execution-policy.yaml:2`; `scope_proofs.py:260-284` | The model cannot sustain a whole epic. This is the sprint decomposition Anthropic removed once models improved |
| S2 | A separate debugging job type, with a budget of 2 | `execution-policy.yaml:3`; `implement.md:184-189` | The author cannot debug its own failures |
| S3 | Hash-bound authority rows, receipts, fingerprints, snapshots, and seals | `validate-refinement.py`, `audit-artifacts.py`, `scope_fingerprint.py`, `scope_snapshot.py`, `scope-wrap-finalize.py` | Approvals and evidence will be forged or will drift. Git already records both |
| S4 | Manifest v1/v2/v3 and legacy readers. Approved artifacts can never be edited, so old formats live forever | README; `refinement-policy.yaml`; `audit-artifacts.py` | Only Scope's own history |
| S5 | Proof contract with argv, parsers (`pytest` or a `SCOPE_RESULT` JSON line), zero skips, fresh flags, environment fingerprints | `epic_refine.md:149-157`; `execution-policy.yaml` | The model fabricates test results. Running the tests is enough to prevent that |
| S6 | Full-tree snapshots; a worker's declared paths must exactly equal the observed paths; writes to ignored paths are a hard failure | `scope-worker.py:751-757, 803-882, 2296-2318` | The model writes out of scope or misreports. A git-diff warning is enough |
| S7 | All-provider preflight barrier for reviewers: one missing CLI blocks the whole review | `epic_refine.md:170-172`; `audit_epic.md:163-165` | None; this is a robustness gap |
| S8 | Implementation stays uncommitted until wrap | `implement.md:236-237` | Pushes recovery and attribution into custom code instead of git |
| S9 | The 1,287-line wrap finalizer, which neutralizes hooks and fsmonitor | `scope-wrap-finalize.py` | The user's own repository configuration is hostile |
| S10 | Orchestrator prompts are mostly runner CLI plumbing (an estimated 39–75% of lines) | `epic_refine.md`, `implement.md`, `audit_epic.md` | The model cannot be trusted with ordinary tool use |

### 2.5 What is load-bearing and must stay

These carry the real value and are kept (and in places strengthened):

- **Acceptance-criteria approval by the user.** It has proven the most useful
  alignment tool.
- **Durable documentation** as an output of every epic, and the audit that
  checks docs against code in both directions (lesson L-002).
- **Independent review by a different provider**, of the plan and of the
  finished build. Claude and Codex catch different critical issues.
- **Worktree isolation** and **one merge approval by the user**.
- **Tests executed by a runner**, not reported by the model, with a compact
  durable record of what ran on which commit.
- **Independent adjudication of findings**: the author of a plan or change
  never has the final word on a finding against it.
- **Approval tied to exact content**: the approved criteria and the approved
  merge commit are identifiable.
- **Targeted re-reviews that cannot add new findings**, which stops scope creep
  during verification.
- **Batched questions** for genuine product decisions.

---

## 3. What current best practices recommend

The industry has converged on the same core ideas Scope was built on:
persistent artifacts over prompts, fresh context for focused work, independent
verification, and humans owning intent. The debate in 2026 is about how much
structure to keep as models improve.

### 3.1 Anthropic: remove scaffolding as models improve (March 2026)

Anthropic's harness for long-running application development started with a
planner, a generator working in sprints, and an evaluator grading each sprint.
With Opus 4.6 they **removed the sprint decomposition entirely** and moved
evaluation to a single end-of-build pass. They kept:

- **the planner**, because without it the generator under-scoped: it started
  building without first specifying its work;
- **the evaluator**, but conditionally: it is worth its cost only when the task
  is beyond what the model reliably does alone.

Their guiding principle: every harness component encodes an assumption about
what the model cannot do alone, and those assumptions should be stress-tested.

**Applied to Scope:** keep one planner and one end-of-build evaluator. Remove
story groups, per-group checkpoints, and debugging jobs (S1, S2). Re-test each
remaining component against the current models.

### 3.2 OpenAI: harness engineering (February 2026)

OpenAI built a production product of roughly one million lines with Codex, with
humans designing the environment instead of writing code. Their practices:

- **A short AGENTS.md (~100 lines) that points to deeper docs.** A giant
  instruction file crowds out the task and rots quickly.
- **The repository is the system of record.** Knowledge the agent cannot see
  effectively does not exist.
- **Mechanical enforcement** of architecture through custom linters and
  structural tests, not prose rules.
- **Continuous cleanup.** Background tasks remove drift in small increments.
- **Human attention is the bottleneck.** In their words, corrections are cheap
  and waiting is expensive. Few blocking gates, short-lived branches.

**Applied to Scope:** durable docs in the repo are exactly right; keep them
current-state and lean. Move enforcement from checks on Scope's YAML to checks
on the product (lint, types, tests, size). Minimize user stops.

### 3.3 OpenAI ExecPlans: the living plan

OpenAI's `PLANS.md` pattern has let Codex work for 7+ hours from one prompt. An
ExecPlan is self-contained and kept up to date, with a progress log, a decision
log, a "surprises and discoveries" section, milestones, and concrete
validation: commands to run and expected outputs.

**Applied to Scope:** replace `design.md`, `delivery-manifest.yaml`, the
`file-plan-story-NN.yaml` files, and `refinement-state.yaml` with one living
`plan.md`, and replace `implementation-evidence.yaml` with a compact
`verification.yaml`. The plan's milestones, its progress log, and git commits
replace Scope's recovery machinery. A resuming agent re-reads the plan and the
git log.

### 3.4 Critiques of spec-driven development

- Böckeler (martinfowler.com, October 2025) found spec tools over-process small
  problems: Kiro turned a small bug into 4 user stories and 16 acceptance
  criteria. She also found reviewing piles of markdown worse than reviewing code,
  and agents ignoring specs anyway.
- Several 2026 critiques observe that spec-driven development "keeps ending up
  as waterfall" when there is no guardrail for when to stop specifying.
- A June 2026 comparison of AI development frameworks (arXiv 2606.04967) found
  mature frameworks converging on persistent artifacts, work contracts,
  traceability, and human review. It named the main risks as spec-to-code drift
  and over-reliance on generated artifacts.

**Applied to Scope:** the specification must scale with the problem, and a
guardrail must exist. That means the size estimate and "not building" list at
the acceptance-criteria gate, and reviewers who flag over-specification.

### 3.5 Verification is now the hard part

"The Verification Horizon" (arXiv 2606.26300, June 2026) argues that generating
solutions is no longer hard; reliably verifying them is, and every verifier is
only a proxy for intent.

**Applied to Scope:** independent review from a different provider remains
Scope's most valuable mechanism. But verification must check fit to intent,
including proportionality, not just contract completeness. Maximizing contract
completeness is what inflated SAG-122.

### 3.6 Widely adopted frameworks

| Framework | Relevant pattern |
|---|---|
| Superpowers (290K stars) | Design approval → worktree → plan → subagent per task with two-stage review (spec compliance, then code quality) → TDD → finish branch. Lean, with skills loaded on demand |
| Compound Engineering (25K stars) | brainstorm → plan → work → simplify → review → **capture learnings** into `docs/solutions/`. Includes a dedicated simplify step and optional second opinions from other models |
| OpenSpec (70K stars) | propose → apply → archive. The archive step merges changes into current-state specs, which is the "current state only" rule for docs |
| GSD Core (10K stars; original repo 64K, archived) | Fresh context per plan to fight context rot |

None of these combine a user-approved acceptance-criteria gate, review by a
different provider, and arc42/ADR documentation discipline, which is why this
plan rebuilds Scope's post-breakdown lifecycle instead of adopting one of them
(see §7).

---

## 4. Target process

### 4.1 Overview

```text
/prd_breakdown (unchanged)
      │  epic details.md + architecture docs
      ▼
/epic_refine
  1. Planner drafts acceptance-criteria.md, with size estimate and "not building" list
  2. ── GATE 1: user approves acceptance criteria (hash + commit recorded) ──
  3. Planner writes plan.md (living plan with milestones, references durable docs)
  4. One review by Claude and Codex; findings fixed, or rejected and adjudicated
      ▼
/implement
  5. One implementer by default, worktree, commit per story, resumable milestones
  6. Size check against planned cumulative work after each story; runner verification record per milestone
  7. Durable docs finalized (ADRs, arc42 current state, specs); epic folder archived on the branch
      ▼
/audit_epic
  8. 2 providers review diff + ACs + plan + verification record + final docs↔code
  9. Fix, verify, and adjudicate until settled; diagnose and retry before asking the user
      ▼
/wrap_epic
 10. ── GATE 2: user approves the exact branch commit (diffstat vs estimate, audit, docs) ──
 11. Merge that commit; the approval is recorded in the merge commit
```

### 4.2 Artifacts per epic

| File | Owner | Content |
|---|---|---|
| `details.md` | `/prd_breakdown` | Goal, context, scope, non-goals (unchanged) |
| `acceptance-criteria.md` | Planner; **approved by user** | Observable criteria with IDs, including meaningful error cases. Header block: size estimate (LoC, files) and "not building" list |
| `approvals.yaml` | Orchestrator only (never an agent) | The Gate 1 record: the git blob hash of the approved `acceptance-criteria.md`, the commit that introduced it, and the approval source and date. The hash identifies content, so later commits do not invalidate it. The merge approval is **not** stored here (§4.6) |
| `plan.md` | Planner, then implementer | Living plan in the ExecPlan style: approach (referencing durable architecture docs rather than restating them), milestones, stories as a checklist with complexity scores and estimates, the tests that prove each acceptance criterion, validation commands, doc obligations, dependencies, progress log, decision log, surprises |
| `review.md` | Reviewers + planner/implementer | All refinement and audit findings with their disposition, rejection reasons, adjudication, and resolution, appended per round |
| `verification.yaml` | Runner only | Compact durable proof record per milestone: tested commit, commands, outcome, pass/fail/error/skip counts, reasons for skips and unavailable external evidence, and where the raw logs are kept |

Raw logs, reviewer transcripts, and run state go to git-ignored
`tmp_debug/scope-runs/<epic>/` and are kept at least until the epic is merged.
The durable facts about them live in `verification.yaml`. Git is the record of
what changed, when, and in which commit.

**Evidence files and commit identity.** Committing `verification.yaml` or
`review.md` creates a new commit, so no record can name "the current commit".
The rule is therefore: `verification.yaml` names the tested commit, and a later
commit is still covered when `git diff --name-only <tested> <later>` touches
only the evidence files (`verification.yaml`, `review.md`). Any other change
since the tested commit requires a new verification run.

### 4.3 `/epic_refine`

1. **Draft the acceptance criteria.** One planner job reads `details.md`, the
   PRD, and the durable architecture docs. It writes `acceptance-criteria.md`
   with:
   - concise, observable criteria with stable IDs, including error cases that
     matter to the product;
   - a **size estimate** in production LoC and files, with a one-line rationale;
   - a **"not building" list**: things a reader might expect that are
     deliberately excluded;
   - genuine product questions, batched.
2. **Gate 1.** The user reviews and approves, edits, or cuts. The orchestrator
   commits the approved file and records its git blob hash and commit in
   `approvals.yaml`; agents never write that record. Before implementation and
   again before merge, the orchestrator compares the current criteria with the
   approved hash. Any difference comes back to the user as a diff with the size
   delta ("+3 criteria, estimate 1.1K → 1.8K") and needs renewed approval.
3. **Write the plan.** The planner writes `plan.md`. It references the durable
   architecture docs and records only what this epic adds or changes. It
   declares milestones, the validation commands (tests, lint, types), the tests
   that prove each acceptance criterion, the doc obligations (target file plus
   owner story), and dependencies. Each story gets a complexity score (0–10,
   maximum 7; anything above is split, and when in doubt the story is split)
   and an estimated number of production code lines. Planned modules stay
   within the size limits in §4.8.

   The plan stays a living document during implementation, and changes are
   handled by impact:
   - **Reversible implementation choices** go into the decision log and are
     shown at merge. The audit judges the result.
   - **High-impact changes get a short independent check before work
     continues.** These are: a new migration, a changed permission or security
     boundary, a new or changed external contract, or a dropped proof or doc
     obligation. One other-provider reviewer checks the plan change. This is not
     a user gate.
   - **A change that alters the acceptance criteria** needs renewed approval
     (step 2).
4. **Review.** One full review by Claude and Codex at **high** effort (not max).
   The reviewer mission becomes:
   - Does the plan satisfy every acceptance criterion?
   - Is it feasible and consistent with the current architecture docs?
   - **Is it proportional?** Flag speculative generality, scope beyond the
     criteria, an implausible size estimate, stories scoring above 7, and
     planned modules above the size limits.
   - Are the doc obligations complete?

   Severity is `blocking`, `major`, `minor`, or `suggestion`. Maximum severity
   is not taken across reviewers; disagreements are listed as they are.
   Hostile-case and fail-closed analysis is required only where the criteria
   touch security, money, data integrity, or destructive actions.
5. **Resolve.** Blocking and major findings must be addressed: the planner
   fixes each one or **rejects it with a reason** (disproportionate, out of
   scope, hypothetical). Suggestions are optional.
   - **Fixed blocking or major findings** are verified by the reviewer that
     raised them. A verification pass checks only named findings and adds
     nothing new. It repeats only for findings still open.
   - **Minor findings are fixed by default** (decision D7). The raising
     reviewer verifies them in the pass that runs anyway for blocking or major
     findings, so they add no extra round. In SAG-122 all three minors rode
     along with a major in this way. Minors were substantive there, not polish:
     one privacy defect, one data-integrity defect, and one determinism
     requirement.
   - **Guard on minor fixes.** A minor fix may not add a new requirement or
     mechanism. If it would, the finding is reclassified: either it becomes
     major and gets the full treatment, or it is rejected as disproportionate
     and adjudicated. SAG-122's RF-002 is the example: "add one clause"
     committed the implementation to identical results across processes.
   - **Rejected minor findings.** If a verification pass runs, the raising
     reviewer checks the rejection in that pass. If none runs, a rejected minor
     in the security or data-integrity category still gets the raising
     reviewer's check. Any other rejected minor stands, and is recorded in
     `review.md` as an accepted quality tradeoff and shown at merge.
   - **Rejections of blocking or major findings are adjudicated
     independently.** A rejected finding goes back once to the raising reviewer
     with the reason. If that reviewer maintains it:
     - a dispute about product scope goes to the user;
     - a technical dispute goes to the other provider, which examines the
       evidence and the closure test from scratch. If both providers raised the
       finding, neither is uninvolved, so the independent fallback reviewer or
       a decisive executable check settles it.
   - **No finding is accepted because a budget ran out.** When the same
     finding survives two fix attempts, the next step is an independent
     diagnosis, followed by a fresh author. The user is asked only when
     resolution needs a product choice, an accepted risk, or more resources. If
     the finding is still unresolved, the epic stops as *blocked*, with the
     diagnosis.

### 4.4 `/implement`

1. Create the worktree and branch as today.
2. **One implementer by default.** A fresh implementer works through
   `plan.md`, writes tests alongside code, runs them itself, debugs its own
   failures, updates the plan's progress and decision logs, and **commits per
   story** on the epic branch with a meaningful label. Milestones make a long job
   resumable: a new job re-reads the plan and the git log and continues. The
   plan, or the orchestrator during the run, may split the work into sequential
   jobs when size, context, external integrations, or uncertainty warrant it.
   One implementer is a default to be tested in the pilot, not a proven limit.
3. **Governance reaches the worker.** The runner appends the simplicity and
   size governance (§4.8) to every refinement and implementation worker
   prompt, so the rules no longer depend on a worker choosing to read them.
4. **Runner verification.** At each milestone and at the end, the runner
   executes the declared validation commands on a committed state and writes
   `verification.yaml`: tested commit, commands, outcome, pass/fail/error/skip
   counts, skip reasons, unavailable external evidence, and the location of the
   raw logs. Counts come from JUnit XML instead of a Scope-specific result line.
   The plan declares, for each test suite, a command that writes JUnit XML using
   standard tooling, not custom Scope adapters: pytest's native `--junitxml`,
   `gotestsum --junitfile` for Go, or a configured JUnit reporter for Jest. A
   tool with no JUnit output falls back to its exit code, and the missing
   counts are recorded as a gap. Exit codes remain enough for non-test checks
   such as lint. The pilot must confirm that the runner stays within its size
   budget with these tools. The runner also confirms that the
   tests the plan maps to each acceptance criterion actually ran and passed, so
   a command cannot pass while relevant tests were skipped or never selected.
   Every skip needs a reason. Coverage is reported, with the target set by
   epic risk rather than a fixed 90% per story.
5. **Size check against planned cumulative work.** After each story, the
   runner compares the production code lines added so far with the plan's
   cumulative estimate for the stories completed. The comparison is against the
   plan, not an extrapolated total. If the actual exceeds the planned
   cumulative by more than the threshold (decision D3, proposed 1.5×):
   - the implementer explains the growth in the decision log;
   - the short independent check from §4.3 step 3 classifies it.

   **Scope growth** (behavior or capability beyond the approved criteria) goes
   to the user as a scope decision: re-approve, cut, or re-plan.
   **Implementation growth** continues and is shown at merge. A numeric overrun
   alone is not a product-scope decision. The early warning exists because
   SAG-122's size was set before review ever saw code. That shows a check at
   merge comes too late, but it does not prove that an early projection is
   reliable, so the pilot must test it. The runner also reports code lines per
   changed production module against the §4.8 limits.
6. **Docs in the same epic, finalized before the audit.** Each doc obligation
   is implemented by its owner story. Before handing over to the audit, the
   implementer finalizes the durable documentation:
   - lasting decisions become ADRs;
   - current behavior goes into the arc42 sections and `13-specs`;
   - working papers produced during the epic move into the epic folder, never
     into `docs/architecture/` (§4.7).

   The implementer then **archives the epic folder on the epic branch**: it
   moves `docs/epics/<epic>/` to `docs/epics/_implemented/<epic>/`. That is the
   same destination `/wrap_epic` uses today, only earlier. Final verification,
   the audit, and Gate 2 all see the archived state, so the merge adds nothing
   that was not audited and approved. If the user rejects at Gate 2, the move
   exists only on the unmerged branch and main is unaffected. The audit
   therefore checks the documentation and layout that will actually be merged.

### 4.5 `/audit_epic`

1. Two providers (Claude and Codex) review:
   - the diff against the approved criteria;
   - `plan.md`, including its decision log;
   - `verification.yaml` and the raw logs it points to;
   - **the final docs against the code in both directions** (L-002).

   They judge whether a real entry point delivers each criterion, whether tests
   assert real behavior, and whether anything is unwired, stubbed, or unsafe.
   They also flag **over-engineering in the delivered code** (dead
   generality, duplicate helpers, re-validation of internal calls) as a finding
   category.
2. Findings are `blocking`, `major`, `minor`, or `suggestion`. Severity is not
   maximized across reviewers; disagreements are listed as they are.
3. **Fix, verify, adjudicate.** The implementer addresses every finding as in
   refinement (§4.3 step 5): it fixes it or rejects it with a reason.
   - The raising reviewer verifies fixed blocking or major findings; a
     verification pass checks only named findings and adds nothing new. It
     repeats only while findings remain open.
   - Minor findings follow D7 and its guard (§4.3 step 5).
   - Rejections are adjudicated the same way as in refinement: first the
     raising reviewer, then the other provider re-examining from scratch. If
     both providers raised the finding, the fallback reviewer or an executable
     check decides. Product-scope disputes go to the user.
   - Remediation reruns the runner verification (§4.4 step 4) on the new
     commit.
   - When the same finding survives two fix attempts: an independent
     diagnosis, then a fresh implementer. The user is asked only for a product
     choice, an accepted risk, or more resources. Otherwise the epic stops as
     *blocked*. A real defect is never accepted because a round budget ran out.
4. **Reviewer unavailable.** If a provider is unavailable, the audit first uses
   a configured independent fallback provider (decision D10). If no independent
   reviewer is available, the audit is **incomplete** and blocks Gate 2
   (§4.6). Verification responsibility is not handed to the user. There is no
   all-provider barrier before launch.

### 4.6 `/wrap_epic`

1. **Check.** The orchestrator confirms three things:
   - the acceptance criteria still match the approved hash in `approvals.yaml`;
   - the audit is complete and passed: every required independent review ran,
     either by the standard providers or by the fallback;
   - `verification.yaml` covers the current branch commit: the diff between its
     tested commit and the branch head touches only evidence files (§4.2).

   **An incomplete audit blocks Gate 2.** The orchestrator retries the fallback
   reviewer or waits for a provider; it does not ask the user to accept a
   one-provider review. The only exception is a separate path that the user must
   invoke explicitly: a recorded waiver that names the missing review and is
   stored as a quality risk. A waiver never turns the audit into a pass, and
   Gate 2 shows the audit as incomplete with the waiver attached.
2. **Gate 2.** Show the user:
   - the exact branch commit SHA to be merged;
   - the diffstat, with production code lines and concepts added against the
     plan;
   - the audit verdict, including adjudicated and rejected findings, and any
     recorded waiver;
   - the verification summary;
   - the doc changes and the plan's decision log;
   - the commit list.
3. On approval, the orchestrator merges **exactly the approved commit** into
   main. It does not modify the approved branch. The approval is recorded in the
   merge commit itself, as git trailers: the approved commit SHA, the approver,
   and the date. If the branch has moved since approval, it stops and asks
   again. The epic folder was already archived on the branch before the audit
   (§4.4 step 6), so the merge adds nothing unapproved. Then it removes the
   worktree. There is no hook neutralization, seal, or staged-tree binding: the
   pinned commit SHA and the merge-commit trailers are enough.

### 4.7 The documentation rule

Added to the project-documentation skill, applied when the implementer
finalizes the docs (§4.4 step 6), and checked by the audit:

> `docs/architecture/` holds the current state only: arc42 sections, specs,
> schemas, and ADRs. Superseded content is replaced, not appended. Nothing
> dated or in-progress goes there.

Scope prescribes only destinations it already defines. It adds no new folder:

| Material | Destination |
|---|---|
| Epic working papers (briefs, red-team outputs, handoff or session prompts, checkpoints, evidence) | The epic folder, `docs/epics/<epic>/`, archived with the epic to `docs/epics/_implemented/<epic>/` |
| Lasting architecture decisions | ADRs in the relevant `adr/` folder, rolled up in `09-adr-summary.md` |
| Product decisions | `docs/product/decisions.md` |
| Lessons | `docs/lessons-learned/` |
| Current behavior and contracts | arc42 sections and `13-specs/` |

Anything else that is not tied to one epic, such as cross-epic research or
strategy snapshots, is the project's own business. Scope requires only that it
stays out of `docs/architecture/`.

Why it matters, from one project Scope has been used on:
`docs/architecture/backend/08-cross-cutting/` holds
**281 lines of durable docs against 33,757 lines of working papers across 108
files**, and there are only 3 ADRs. When an agent refines epic 50 and reads that
folder, it loads superseded proposals next to current contracts. That costs
tokens and risks following an overturned decision. The rule protects the
foundation that the documentation investment is meant to build.

### 4.8 Simplicity, story complexity, and module size

The user approved these limits on 2026-09-22; they are not implemented yet.
They would live in one governance file,
`src_shared/governance/simplicity-and-size.md`. The runner would append that
file to refinement and implementation worker prompts; the developer agent and
its checklist would reference it; reviewers would check against it.

**How the limits are enforced.** They are mandatory rules, enforced by review,
not by the runner failing a build:
- **Story above 7:** split before handoff, or record an exception with a
  reason in the plan. The refinement reviewer must accept the exception;
  otherwise it is a finding.
- **Module above 450 code lines:** split by responsibility, or record an
  exception with a reason in the plan or the worker result. The audit reviewer
  checks the exception; an unjustified one is a finding.
- **Module between 350 and 450:** allowed; the reviewer may suggest a split.
- **The runner only reports** code lines per changed module and cumulative
  size against the plan.

An agent therefore never has to guess: above a limit it splits or writes an
exception, and a reviewer judges the exception. The goal is simpler code, not
arbitrary splitting. Code lines are a warning signal, read together with the
concepts added and the defects found (§1).

- **Over-engineering warning.** Claude and Codex habitually over-engineer:
  extra files, single-use abstractions, speculative configurability, defensive
  re-validation of data already checked upstream, and handling for cases that
  cannot happen. Anthropic's own prompting guide documents this tendency for
  its Opus models. OpenAI's Codex guide asks for no broad catches or silent
  defaults, and for reuse instead of duplication.
- **Simplicity and maintenance rules.** Build the minimum that satisfies the
  approved criteria. Validate only at system boundaries. No single-use
  abstractions and no design for hypothetical futures. Reuse before writing.
  Handle only errors that can occur. Prefer the standard library and
  established libraries. Choose the design with fewer concepts and lines. Give
  each module one responsibility, with no dead code.
- **Module size.** Code lines exclude blank lines, comments, and docstrings.
  The target is at most 350 code lines per production module. The hard limit is
  450: new modules stay under it, modules already above it must not grow, and an
  exception needs a one-line recorded reason. For calibration: across Sagara's
  323 production modules the median is 115 code lines and 9% exceed 400. The
  SAG-122 modules are 980–2,290 code lines.
- **Story complexity.** Each story is scored 0–10 for integration and
  correctness difficulty, not effort. The maximum is 7. Anything above is split
  before handoff, and when in doubt the story is split.

| Score | Anchor |
|---|---|
| 0–2 | Configuration, copy, or a one-function change following an existing pattern |
| 3–4 | One component, established pattern, a few edge cases |
| 5–6 | Two or three components, or one new pattern; a straightforward schema change |
| 7 | Several boundaries, new persisted state, or an external integration with real failure modes; still one clear, testable outcome |
| 8–10 | Split: several new concepts at once, concurrency or distributed state, a risky migration, or high uncertainty |

---

## 5. Keep / simplify / remove, mechanism by mechanism

| Mechanism | Decision | Reason |
|---|---|---|
| Acceptance-criteria gate | **Keep, strengthen** | Proven alignment value. Adding a size estimate and "not building" list makes it the proportionality checkpoint |
| Genuine product decisions (batched) | Keep | User authority where it matters |
| Final-handoff (design) approval gate | Remove | A 900-line design is not realistically reviewable. The acceptance-criteria gate provides alignment; the merge gate covers the result |
| Separate product/design/correction workers | Simplify to one planner job, plus fix passes | Current models hold the whole plan |
| `design.md` + manifest v3 + file plans + refinement state | Replace with `plan.md` | One living plan (ExecPlan pattern); less duplication |
| FLOW/HOSTILE sections for every high/critical requirement | Restrict to security, money, data integrity, destructive actions | Main driver of fail-closed and hashing code (P1, P2) |
| Refinement review: 2 providers at max, plus specialist, 1 full + up to 3 targeted | 2 providers at high; 1 full review; verification passes only for still-open blocking/major findings; specialist only on request | Most findings come from the first full review; later rounds mostly re-ran for infrastructure |
| Mandatory minors with re-review, severity maximization | Minors fixed by default and verified within the pass that runs anyway; a minor fix may not add a requirement or mechanism (else reclassified); no severity maximization (D7) | SAG-122's minors were substantive and cost no extra rounds; the guard stops "minor" scope creep (P3–P5) |
| No "reject" disposition | Add "rejected, with reason", adjudicated independently: raising reviewer, then the other provider re-examining from scratch (or the fallback reviewer or an executable check when both providers raised it); product-scope disputes go to the user | Authors can push back without grading their own work |
| Fixed review round budgets | Remove; verification repeats for still-open findings; after two failed fixes, independent diagnosis and a fresh author; the user only for product choice, accepted risk, or resources; otherwise *blocked* | A real defect must never be accepted because a budget expired, and the user must not become the fallback debugger |
| Living-plan changes after review | Reversible choices logged and shown at merge; high-impact changes (migration, permission boundary, external contract, dropped obligation) get a short independent check | Late discovery at audit is too costly for high-impact changes |
| Proportionality review | **Add** | Nothing pushes toward smaller solutions today (P6, P10) |
| Governance files not reaching workers | The runner appends the simplicity and size governance to worker prompts; fold or delete the rest | They currently have no effect (P7) |
| No complexity or module-size limits | **Add:** story complexity ≤ 7/10; modules target 350, hard limit 450 code lines (§4.8) | Nothing bounds story or module size today (P6) |
| 90% coverage floor per story | Risk-based coverage target per epic | Tests scale with the contract (P9) |
| Story groups ≤3, per-group checkpoints | Remove; one implementer by default with resumable milestones; split when size, context, integrations, or uncertainty warrant it | Sprint decomposition (S1) |
| Debugging job type and budget | Remove | The implementer debugs its own work (S2) |
| Uncommitted implementation until wrap | Commit per story on the epic branch | Git becomes the recovery and attribution mechanism (S8) |
| Proof contract (parsers, `SCOPE_RESULT`, zero skips, env fingerprints) | Replace with a compact durable `verification.yaml`: tested commit, commands, JUnit-parsed counts from standard tooling (pytest, gotestsum, a Jest reporter), skip reasons, log location; the tests mapped to each criterion must have run and passed; later evidence-only commits stay covered | Keeps "the model doesn't report its own tests" and catches skipped or unselected tests, at a fraction of the surface (S5) |
| Full-tree snapshots, exact path equality, ignored-path failure | Git-diff scope warning | Current rules are brittle and caused failures (S6) |
| Hash-bound authority rows, receipts, fingerprints, seals | Replace with one small `approvals.yaml` for Gate 1, written only by the orchestrator (the approved criteria's content hash); record the merge approval as trailers in the merge commit | Ties approval to the exact text and commit the user saw, without receipts or seals, and without modifying the approved branch (S3) |
| Manifest versions and legacy readers | Remove with a clean break; no compatibility with old-format epics; epics in progress are re-planned on the new version | Only Scope's history (S4) |
| All-provider reviewer barrier | Remove; use an independent fallback provider; a one-provider audit is labeled incomplete | One missing CLI should not block, and verification must not fall to the user (S7) |
| Doc distillation and archival at wrap | Move both to the end of `/implement`, on the epic branch, before final verification and the audit | The final docs and layout must be audited and approved; the merge must add nothing unapproved |
| Wrap finalizer (1,287 lines) | Replace with an approved merge of the pinned commit | S9 |
| Dependency-merge script | Remove; branch from main after the dependency epic is merged | Plain git |
| CodeGraph lifecycle management | Make optional and external | Not core to the purpose |
| Audit: 3 providers standard | 2 standard (Claude, Codex); a third configured as the independent fallback | Cost and infrastructure fragility; see decisions D2 and D10 |
| Doc obligations, docs↔code audit, `/sync_product` | **Keep** | Durable-documentation requirement |
| Worktree isolation, merge approval | **Keep** | Load-bearing |

---

## 6. Implementation roadmap

The roadmap **replaces** the post-breakdown lifecycle instead of simplifying
it in place or running both versions side by side. The user's decision
(2026-09-22): the current version produces unusable results, so there is
nothing to preserve. An in-place path could not keep Scope working at every
step anyway. Even a "prompt-only" change such as a `rejected` finding status
needs a validator change, and per-story commits conflict with today's runner
evidence and wrap checks.

### Step 1: Build the new lifecycle

Build it in `src_shared/` under the canonical command names (`/epic_refine`,
`/implement`, `/audit_epic`, `/wrap_epic`), with Claude and Codex mirrored.
Components:

1. **Templates:**
   - `acceptance-criteria.md`, with the size estimate and "not building" list;
   - `plan.md` in the ExecPlan style, with milestones, story complexity,
     tests mapped to each criterion, and doc obligations;
   - `review.md`;
   - `approvals.yaml`;
   - `verification.yaml`.
2. **Governance:** `simplicity-and-size.md` (§4.8), plus the few
   production-code and test rules worth keeping. The runner appends it to
   worker prompts.
3. **Launcher**, harvested from `scope-worker.py` and `scope-reviewer.py`:
   provider CLI invocation, preflight, timeouts, model routing, parallel
   reviewers, independent fallback reviewer. Target ≤ 1,500 lines.
4. **Verification runner:** runs the declared commands on a commit, parses
   JUnit XML, checks the tests mapped to each criterion, and writes
   `verification.yaml`. Target ≤ 400 lines.
5. **Structural checker and approval record:**
   - the acceptance criteria have IDs and match the approved hash;
   - the plan has its required sections and an estimate;
   - each doc obligation has a target;
   - the merge commit is pinned.

   Target ≤ 400 lines.
6. **Commands:** four prompts of ≤ 150 lines each, shared-first.
7. **Commands that read the old artifacts**, updated to the new ones:
   - `/sync_product` reads `delivery-manifest.yaml` and
     `implementation-evidence.yaml`;
   - `/lesson` and `/decision` read `implementation-evidence.yaml`;
   - `/session-handoff` lists the old epic state files. It stays until Director
     is adopted, which is sequenced after this rebuild (D16).

   The web-epic commands (`/webepic_refine`, `/webepic_implement`) and
   `/website_breakdown`, which only feeds them, are retired rather than updated
   (decision D11).
8. **Tests:** a black-box workflow test of the whole lifecycle first, then
   focused unit tests.

Budget from day one: Scope's Python for the new lifecycle ≤ 3,000 lines, and
one policy file.

### Step 2: Remove the old lifecycle in the same release

Delete:
- the scripts: `validate-refinement.py`, `audit-artifacts.py`,
  `scope-wrap-finalize.py`, `scope-dependency-merge.py`,
  `scope_fingerprint.py`, `scope_snapshot.py`, `scope_proofs.py`, and the old
  worker and reviewer runners;
- the old epic templates (`design.md`, `delivery-manifest.yaml`,
  `file-plan-story-NN.yaml`, `refinement-state.yaml`,
  `refinement-findings.yaml`, `implementation-evidence.yaml`,
  `audit-findings.yaml`, `implementation-summary.md`);
- their policies and tests;
- the retired commands: `/webepic_refine`, `/webepic_implement`, and
  `/website_breakdown` (D11).

`scope_codegraph.py` becomes optional and external. The installers
(`install.sh`, `install.bat`) remove the old files from a target project, as
they already do for earlier obsolete files, and the install smoke test checks
that the old files are absent. Scope's own documentation is updated in the same
release (decision D12).

Validation before release: `git diff --check`, the install smoke test, the unit
and workflow tests, and a dry run on a small epic.

### Step 3: Pilot and measure

The first real epic on the new version is the pilot (decision D1). Record the
§1 measures and compare with SAG-122. The measures include concepts added,
defects after merge, and verification time as well as code lines. There is no
old version to fall back on, so problems are fixed forward. Decide from the
data whether any removed component needs to come back, and in what form.

### Documentation rule

Add the current-state-only rule and the destination table (§4.7) to the
project-documentation skill. The new `/implement` applies it when it finalizes
the docs. Any cleanup of an existing project's documentation is that project's
task, not part of this plan.

### Guardrails against regrowth

The history shows that simplifications regrow. To prevent that:

- **Complexity budget for Scope:** Python ≤ 3,000 lines; each command prompt ≤
  150 lines; one policy file. A PR check reports the totals.
- **Every new rule states the model weakness it assumes** and how to test
  whether it is still needed (Anthropic's principle).
- **Incidents go to lessons-learned first.** Code is added only when a lesson
  recurs and no simpler fix exists.
- **Periodic ablation:** each quarter, or on a major model release, run one
  epic with a component removed.

---

## 7. Alternatives considered

| Option | Assessment |
|---|---|
| **A. Rebuild the post-breakdown lifecycle and replace the current one (recommended; decided D6)** | Keeps the acceptance-criteria gate, the arc42/ADR documentation discipline, `/sync_product`, and review by a different provider, which no adopted framework combines. It keeps the pre-breakdown commands, the documentation skill, and the install and mirroring setup. It harvests the proven provider-launch knowledge from the current runners. It borrows the ExecPlan living plan, Compound Engineering's simplify and learning-capture ideas, and Superpowers' two-stage review focus. The current lifecycle is removed in the same release, because it produces unusable results and is not worth preserving. |
| A2. Simplify the current machinery in place (the first revision's recommendation) | Rejected after the red-team review. It cannot keep Scope working at every step without temporary compatibility layers: the new artifact and state model shares almost nothing with the current one, and most of the 9.3K lines of tests pin the machinery being removed. Two earlier in-place simplifications grew back. |
| B. Replace post-breakdown Scope with Superpowers | The most widely adopted and a close 1:1 mapping. But reviewers are same-model subagents, there is no docs↔code sync or arc42 integration, and it has no acceptance-criteria approval gate or size check. Worth re-evaluating after the pilot as the implementation loop inside `/implement`. |
| C. Replace with Compound Engineering | Has cross-model opinions and learning capture, but 36 skills is not lean, and it has no documentation discipline of the arc42/ADR kind. |
| D. Keep the current machinery and only tune prompts | Would fix product inflation cheaply. But it leaves ~12.7K lines of Python, much of it self-managing, that keeps generating incidents and reruns. And editing hash-pinned prompts under in-flight epics risks breaking their receipts. Insufficient as an end state, and unsafe as a stopgap. |

---

## 8. Transition

- **Replacement, not coexistence.** The new lifecycle ships under the canonical
  command names, and the installers remove the old files. No project runs both
  versions.
- **Epics in progress on the old version** are re-planned on the new version.
  Their acceptance criteria are reviewed and re-approved at Gate 1, and a new
  plan is written. Old-format artifacts in an epic folder are handled by
  decision D13. What happens to code already produced under the old version is
  the project's decision.
- **Mirroring:** every change in `src_shared/` applies to both platforms. The
  remaining platform-specific files (`src_claude/`, `src_codex/`) are updated
  together, per the repository's mirroring rule.
- **Session-state tools such as Director (D16).** Director is adopted only after
  this rebuild is stable; the notes below are the requirements for that later
  step. Director installs global hooks
  (`~/.claude/settings.json`, `~/.codex/hooks.json`) that inject its decision
  log into every new session, plus namespaced commands and skills. It writes to
  its own hub (`~/.director`), not to the repository. Checked against
  `~/projects/director` on 2026-09-22:
  - **Worker and reviewer processes must run without it.** Reviewers must not be
    anchored by the builder's recorded decisions, and workers take their
    authority from the plan, not from an injected log.
    - Claude workers and reviewers already run with `--safe-mode`, which turns
      off hooks.
    - Codex processes run with `--ignore-user-config`, which skips only
      `config.toml`, not `hooks.json`. Once the user trusts Director's hooks,
      `codex exec` workers and reviewers would receive the injection.
    - Director has no disable flag, and its throwaway-session mode still
      injects. Its documented fail-safe does work: a `DIRECTOR_BIN` pointing at
      a non-existent path makes the hooks exit without doing anything. The new
      runner therefore sets that variable for every worker and reviewer
      process, and a test checks it.
  - **One home per fact.** Director holds in-flight session state (open loops,
    handoffs, where a session stopped). `plan.md` holds the epic's decisions
    and progress, and ADRs hold durable decisions. With Director adopted,
    Scope's `/session-handoff` becomes redundant and is retired.
  - **No file or name collisions:** Director's commands are namespaced
    (`/director:*`, `$director-*`), it never edits `CLAUDE.md` or `AGENTS.md`,
    and Scope's installers never touch the global settings or hooks files.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Quality regresses without the extra review rounds | Review by a different provider stays at both ends; verification repeats while findings remain open; the pilot compares defects found and escaped |
| The model misreports test results, or relevant tests are skipped or never selected | The runner executes the commands, parses JUnit counts, checks the tests mapped to each criterion, and keeps a durable `verification.yaml` tied to the tested commit |
| The author dismisses an independent finding | Rejections are adjudicated by the raising reviewer, then the other provider re-examining from scratch, or the fallback reviewer or an executable check when both providers raised it; product-scope disputes go to the user |
| A genuine minor defect is waved away | Minors are fixed by default and verified in the existing pass; rejected security and data-integrity minors are always checked; other rejected minors are recorded as an accepted quality tradeoff |
| A "minor" fix quietly adds scope | The guard: a minor fix may not add a requirement or mechanism; if it would, it is reclassified |
| Approval drifts from the text the user saw | `approvals.yaml` holds the approved criteria's content hash; any change needs renewed approval; the merge is pinned to the approved commit SHA and the approval is recorded in the merge commit |
| Evidence commits break commit identity | `verification.yaml` names the tested commit; later commits stay covered only if they touch evidence files alone; nothing modifies the approved branch at merge |
| Final docs or layout escape review | Docs are finalized and the epic folder is archived on the branch before the audit, which checks them |
| A high-impact plan change surfaces only at audit | Migrations, permission boundaries, external contracts, and dropped obligations get a short independent check before work continues |
| A long implementation job fails midway | Milestones, per-story commits, and the plan's progress log make it resumable; the work may be split |
| The user becomes the fallback debugger | After two failed fixes: independent diagnosis and a fresh author first; the user only for product choice, accepted risk, or resources; otherwise *blocked* |
| The size estimate is gamed or wrong | The estimate is visible at Gate 1; each story compares actual with planned cumulative work; an independent check classifies growth, and only scope growth goes to the user; the pilot tests whether the early signal is reliable |
| Numeric limits cause arbitrary splitting | Above a limit, the agent splits or records an exception that a reviewer judges; the runner only reports; code lines are read together with concepts added and defects found |
| Old files linger in a project after the upgrade | The installers remove the retired files; the install smoke test checks that they are absent |
| JUnit output is not uniform across test tools | Standard tooling per suite (pytest `--junitxml`, `gotestsum`, a Jest reporter); exit-code fallback with a recorded gap; the pilot confirms the runner budget |
| No independent reviewer is available | A configured fallback provider; otherwise the audit is incomplete and blocks the merge. Only an explicit, recorded user waiver can bypass it, and the audit still never counts as passed |
| The new lifecycle fails in its first real epic, with no old version to fall back on | Accepted: the old version produces unusable results. Mitigated by the workflow test and a dry run before release, and by fixing forward during the pilot; git history keeps the old version retrievable |
| Simplification regrows | The complexity budget and guardrails in §6 |
| Security-sensitive epics need the deeper analysis | Hostile-case analysis is kept for security, money, data-integrity, and destructive surfaces |

---

## 10. Decisions

| ID | Decision | Recommendation |
|---|---|---|
| D1 | Pilot epic | **Decided (2026-09-22):** the first epic re-planned on the new version, after the project's own architecture review |
| D2 | Audit providers | **Decided (2026-09-22):** Claude and Codex as standard; the third provider only as fallback (D10) |
| D3 | Threshold for the size check after each story | **Decided (2026-09-22):** actual above 1.5× the planned cumulative estimate triggers an explanation and an independent classification; only scope growth goes to the user |
| D4 | Commit per story during `/implement` | **Decided (2026-09-22):** yes, after the story's tests pass. D14 makes it possible without a per-commit approval |
| D5 | Where working papers go | **Decided by the user (2026-09-22):** Scope stays generic and prescribes only its existing destinations (§4.7). No project-specific cleanup is part of this plan |
| D6 | Roadmap | **Decided by the user (2026-09-22):** replace the post-breakdown lifecycle; no side-by-side versions, because the old version produces unusable results |
| D7 | Minor findings | **Decided by the user (2026-09-22):** minors are fixed by default and verified in the pass that runs anyway, with the guard that a minor fix may not add a requirement or mechanism (§4.3 step 5) |
| D8 | Stopgap changes to the old version | **Moot:** the old version is replaced, not maintained |
| D9 | Command names | **Decided by the user (2026-09-22):** canonical names (`/epic_refine`, `/implement`, `/audit_epic`, `/wrap_epic`); no temporary pilot names |
| D10 | Independent fallback reviewer | **Decided (2026-09-22):** Muse Spark; Gemini on request |
| D11 | Web-epic commands | **Decided by the user (2026-09-22):** retire and remove `/webepic_refine`, `/webepic_implement`, and `/website_breakdown` |
| D12 | Scope's own documentation | **Decided by the user (2026-09-22):** rewrite `README.md` and `docs/scope-architecture.md` for the new lifecycle; delete the superseded plans (`scope-modernization.md`, the orchestrator-worker plans, `refactoring-bloat-removal.md`); git history keeps them |
| D13 | Old-format artifacts in the folder of an epic being re-planned | **Decided by the user (2026-09-22):** delete them when re-planning begins; git history keeps them. The new `/epic_refine` starts from `details.md` and the acceptance criteria |
| D14 | Commit authorization | **Decided and applied (2026-09-22):** Rule 12 in the user's global `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` now says that every commit needs a meaningful label, that labels need no user approval, and to ask the user when in doubt. The Scope-specific exception was removed |
| D15 | Test coverage | **Decided by the user (2026-09-22):** 90% on new and changed production code, measured per epic rather than per story, with a recorded exception where coverage adds little |
| D16 | Adopting Director alongside Scope | **Decided by the user (2026-09-22):** adopt it, but only **after** the simplified Scope is stable. This rebuild does not integrate Director. When it is adopted: retire `/session-handoff`, and have the runner disable Director's hooks for worker and reviewer processes (§8). The user installs Director separately |

---

## Revision history

| Revision | Changes |
|---|---|
| 1 (2026-09-22) | Initial proposal |
| 2 (2026-09-22) | Incorporated the red-team review: <ul><li>rejected findings are adjudicated independently instead of by the author;</li><li>an orchestrator-written approval record ties Gate 1 to the exact criteria, and the merge is pinned to the approved commit;</li><li>docs are finalized before the audit, so the final docs are audited;</li><li>a compact durable verification record (JUnit counts, skip reasons, tests mapped to each criterion) replaces exit-code-only proof;</li><li>the roadmap changes from in-place simplification to building alongside, piloting, then switching;</li><li>one implementer is a default with resumable milestones, and no defect is accepted because a budget expired;</li><li>the stop count is reworded as 2 planned gates plus defined exceptions;</li><li>an unavailable reviewer is replaced by a fallback, or the audit is labeled incomplete;</li><li>code lines become a warning signal alongside concepts, defects, and verification time; the file-count target is dropped;</li><li>the SAG-122 estimate is presented as preliminary;</li><li>minor findings are an explicit decision (D7).</li></ul> The early size check is kept against the red team's advice: in SAG-122, 92% of the code existed after the first implementation pass |
| 3 (2026-09-22) | Incorporated the second red-team review and the user's D7 decision: <ul><li>an incomplete audit blocks Gate 2, with a separate recorded waiver path that never counts as a pass;</li><li>commit identity: `verification.yaml` names the tested commit and later evidence-only commits stay covered; the merge approval moves into merge-commit trailers, so the approved branch is never modified;</li><li>the epic folder is archived on the branch before final verification, the audit, and Gate 2;</li><li>the tie-breaker re-examines from scratch, and when both providers raised a finding, the fallback reviewer or an executable check decides;</li><li>D7: minors are fixed by default and verified in the existing pass, with a guard against minor fixes that add requirements or mechanisms;</li><li>high-impact plan changes get a short independent check;</li><li>the size check compares actual with planned cumulative work, and only independently classified scope growth reaches the user (the earlier 92% rationale was corrected);</li><li>after two failed fixes: independent diagnosis and a fresh author before the user, otherwise *blocked*;</li><li>the story and module limits get explicit enforcement rules;</li><li>the stopgap is dropped, the old installed runtime is frozen, and the lean path installs to separate paths;</li><li>JUnit comes from standard per-tool reporters, with an exit-code fallback.</li></ul> |
| 4 (2026-09-22) | User decisions: <ul><li>replace the post-breakdown lifecycle under the canonical command names, with no side-by-side versions, no frozen old runtime, and no stopgap (D6, D8, D9);</li><li>Scope stays generic: no project-specific cleanup, and working papers go only to destinations Scope already defines, with epic folders archived to `docs/epics/_implemented/` (D5);</li><li>epics in progress are re-planned on the new version.</li></ul>Added the commands that read retired artifacts (`/sync_product`, `/lesson`, `/decision`, `/session-handoff`) to the rebuild, the installer cleanup of old files, and new decisions D11–D15 |
| 5 (2026-09-22) | User decisions D4 and D11–D15: the web-epic commands are retired; the Scope documentation is rewritten and superseded plans deleted; old-format epic artifacts are deleted at re-planning; Rule 12 was changed in both global instruction files; coverage is 90% per epic. Added the compatibility check with Director (§8, D16) |
| 6 (2026-09-22) | Plan approved. Director adopted (D16): `/session-handoff` retired. `/website_breakdown` retired with the web-epic commands. Defaults D1, D2, D3, and D10 confirmed |
| 7 (2026-09-22) | Director is sequenced after the rebuild, once the simplified Scope is stable. `/session-handoff` stays and is updated to the new artifacts until then |

---

## Sources

- Anthropic, *Harness design for long-running application development* (2026-03-24): https://www.anthropic.com/engineering/harness-design-long-running-apps
- OpenAI, *Harness engineering: leveraging Codex in an agent-first world* (2026-02-11): https://openai.com/index/harness-engineering/ (read via the summary at https://businessdatasolutions.github.io/ai-wiki/sources/2026-02-11-lopopolo-codex-harness-engineering)
- OpenAI Cookbook, *Using PLANS.md for multi-hour problem solving*: https://developers.openai.com/cookbook/articles/codex_exec_plans
- B. Böckeler, *Understanding spec-driven development: Kiro, spec-kit, and Tessl* (2025-10-15): https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- *Spec-Driven Development Isn't Waterfall — But It Keeps Ending Up There*: https://dev.to/pacheco/spec-driven-development-isnt-waterfall-but-it-keeps-ending-up-there-4eei
- *From Prompt to Process: a Process Taxonomy and Comparative Assessment of Frameworks Supporting AI Software Development Agents* (2026-06-03): https://arxiv.org/abs/2606.04967
- *The Verification Horizon: No Silver Bullet for Coding Agent Rewards* (2026-06): https://arxiv.org/abs/2606.26300
- obra/superpowers: https://github.com/obra/superpowers
- EveryInc/compound-engineering-plugin: https://github.com/EveryInc/compound-engineering-plugin
- Fission-AI/OpenSpec: https://github.com/Fission-AI/OpenSpec
- open-gsd/gsd-core: https://github.com/open-gsd/gsd-core
- InfoQ, *Claude Code Adds Dynamic Workflows* (2026-06): https://www.infoq.com/news/2026/06/dynamic-workflows-claude-code/

Evidence for §2 comes from the Scope repository at commit `e2c6ce1`
(committed state only), and from the Sagara repository
(`docs/epics/SAG-122-*`, the `wip/SAG-122` worktree, and
`tmp_debug/scope-runs/SAG-122/`). Shares described as estimates (the
self-management share of the scripts, the plumbing share of the prompts) were
classified by keyword and function and are approximate.

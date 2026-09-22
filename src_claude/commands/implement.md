---
name: implement
description: Implement an approved epic through bounded story workers, proof, audit, and remediation.
args: "{epic-id}"
---

# /implement

For this orchestration-only session, prefer `gpt-6-sol` at high effort in
Codex or `claude-opus-5-5` at high effort in Claude. The host session selects its
own model; worker/reviewer policies do not switch it. Respect an explicit user
model choice and do not interrupt an active workflow solely to change models.

You are the sole user-facing orchestrator. Fresh implementation workers own
bounded source changes; independent reviewers remain read-only. Own user
decisions, worktree/Git lifecycle, worker supervision, proof, nested audit, and
the final status. Do not implement or semantically review code in this session.

Report `delivery-complete` only when the current approved handoff, every story,
strict proof evidence, epic verification, audit, and implementation summary all
pass. Otherwise report the exact partial or blocked state.

## Resolve the approved handoff

```bash
EPIC_ID="{epic-id}"
COMMAND_ROOT="$(pwd -P)"
GIT_COMMON_DIR="$(git -C "$COMMAND_ROOT" rev-parse --path-format=absolute --git-common-dir)"
REPOSITORY_ROOT="$(cd "${GIT_COMMON_DIR}/.." && pwd -P)"
EPIC_DIR="$(find "$COMMAND_ROOT/docs/epics" -mindepth 1 -maxdepth 1 -type d \
  -iname "*${EPIC_ID}*" -print | sort | head -1)"
SCOPE_ROOT="$(cd "$COMMAND_ROOT/.claude" && pwd -P)"
PROVIDER="claude"
WORKER="${SCOPE_ROOT}/scripts/scope-worker.py"
REFINEMENT="${SCOPE_ROOT}/scripts/validate-refinement.py"
DEPENDENCY_MERGE="${SCOPE_ROOT}/scripts/scope-dependency-merge.py"
AUDIT_COMMAND="${SCOPE_ROOT}/commands/audit_epic.md"
WRAP_FINALIZER="${SCOPE_ROOT}/scripts/scope-wrap-finalize.py"
WRAP_POLICY="${SCOPE_ROOT}/config/wrap-policy.yaml"
AUDIT_POLICY="${SCOPE_ROOT}/config/audit-policy.yaml"
WORKER_PROFILE="default"       # budget only when the user asks
REVIEWER_PROFILE="default"     # budget only when the user asks
REVIEWER_SET="standard"        # expanded only when the user asks
```

Resolve exactly one epic and one interpreter. Require the runner, v2 schemas,
provider policy, implementation worker, validators, audit command, wrap
finalizer/policy, and canonical artifacts. Validate; implementation never
repairs refinement:

```bash
"$PYTHON_CMD" "$REFINEMENT" validate "$EPIC_DIR" \
  --phase handoff --repo-root "$COMMAND_ROOT"
```

If current approved handoff paths are dirty, invoking this command authorizes
only the exact checkpoint label below. Stage and commit only the current
hash-bound handoff paths; preserve unrelated staged and unstaged work. Stop on
ambiguity or Git failure.

```text
refine({epic-id}): implementation handoff
```

Create or verify branch `epic/{epic-id}` in
`${REPOSITORY_ROOT}/wip/{epic-id}`. Never overwrite repository
instructions. Link the root `.env` only when the worktree has none. Re-resolve
the epic inside the worktree, but retain the absolute installed `SCOPE_ROOT`
captured above.

```bash
WORKING_ROOT="${REPOSITORY_ROOT}/wip/${EPIC_ID}"
EPIC_DIR="$(find "$WORKING_ROOT/docs/epics" -mindepth 1 -maxdepth 1 -type d \
  -iname "*${EPIC_ID}*" -print | sort | head -1)"
RUN="${REPOSITORY_ROOT}/tmp_debug/scope-runs/${EPIC_ID}/implement/run.yaml"
"$PYTHON_CMD" "$WORKER" init \
  --repository-root "$REPOSITORY_ROOT" --working-root "$WORKING_ROOT" \
  --scope-root "$SCOPE_ROOT" --epic-id "$EPIC_ID" \
  --command implement --worker-profile "$WORKER_PROFILE"
```

`init` prepares CodeGraph once. The runner performs a cheap incremental sync
before every implementation write job; workers only query it. Degraded
CodeGraph falls back visibly to direct reads and `rg`, never reduced proof.

For resume or interruption, use only `status`, `recover`, and identity-checked
`cancel`:

```bash
"$PYTHON_CMD" "$WORKER" status --run "$RUN"
"$PYTHON_CMD" "$WORKER" recover --run "$RUN"
"$PYTHON_CMD" "$WORKER" cancel --run "$RUN" \
  --job-id "$ACTIVE_JOB_ID" --reason "$REASON"
```

Take `ACTIVE_JOB_ID` from the current `status` response; stale cancellation is
rejected.

Stop on concurrent ownership, HEAD drift, escaping symlinks, ignored or normal
out-of-scope writes, invalid results, or failed recovery. Never auto-revert
user work or create a parallel ownership ledger.

## Exact dependency baselines

Process each full commit pin in `delivery-manifest.yaml` before the first
implementation job. Never construct a Git merge command from model or user
prose. The dedicated command verifies the exact epic/pin, canonical run and
worktree, empty job history, clean tree, local commit object, ancestry,
conflict-free preview, fixed parents, and fixed subject under the same
non-blocking mutation lock:

```bash
"$PYTHON_CMD" "$DEPENDENCY_MERGE" --run "$RUN" --epic-dir "$EPIC_DIR" \
  --dependency-epic-id "$DEPENDENCY_EPIC_ID" \
  --dependency-commit "$DEPENDENCY_COMMIT"
```

This command alone owns the authorized label
`merge({epic-id}): integrate {dependency-epic-id} implementation baseline`.
It accepts no branch tip, foreign repository, extra Git option, or alternate
message. Report any resulting merge commit. This authorizes no other commit.

## Story groups and proof checkpoints

New epics use delivery-manifest v3. Keep already-approved older epics on the
Scope installation that approved them; never rewrite a hash-bound handoff to
upgrade it. Fresh processes isolate every group from earlier epics.

Read the approved manifest and story plans, then obtain the stable groups:

```bash
"$PYTHON_CMD" "$WORKER" story-groups "$EPIC_DIR" --scope-root "$SCOPE_ROOT"
```

The shared execution policy caps a dependency-connected group at three stories.
Run groups sequentially, one writer at a time. One worker owns the complete
group: union its read/write boundaries, acceptance IDs, proof IDs, and required
documentation targets. Individual story and proof ownership stays unchanged.
Each documentation obligation retains its owner and `requirement_ref`; include
its target in the group's write scope and implement it with that group.

Create a v2 authoring job with `story_ids` matching the returned group and
`required_proof_ids` equal to its exact proof union. Hash-bind the approved
manifest, member plans, relevant artifacts, and decisions. Candidate files are
advisory; approved contracts and forbidden changes remain binding. New scope or
product/architecture decisions return to refinement. Use `allowed_commands`
for optional approved local test runs; reserve `required_validations` for
additional mandatory checks. Do not require the author to rerun every proof.

```bash
"$PYTHON_CMD" "$WORKER" run --provider "$PROVIDER" \
  --role implementation --job "$JOB_PATH" --result "$RESULT_PATH" \
  --cwd "$WORKING_ROOT" --access workspace-write \
  --worker-profile "$WORKER_PROFILE"
```

`run` preflights internally. Accept a group only after both the observed author
changes and its runner proof checkpoint pass. The worker returns an empty
`proof_evidence`; Scope executes approved argv, captures raw logs, counts,
context hashes and timings, and writes implementation-evidence.yaml. Workers
may test while developing but never manufacture authoritative proof evidence.
A failed checkpoint retains attributed code and reports `verification_failed`;
it does not mark that group completed or authorize the next group.

## Epic verification, audit, and remediation

After all groups, execute the complete approved proof inventory once:

```bash
"$PYTHON_CMD" "$WORKER" verify-proofs "$EPIC_DIR" --run "$RUN"
```

This inventory includes acceptance, regression, native contract,
runtime/operational, and observable-value obligations. Earlier group results
do not certify the final workspace. Identical local execution contexts may
share one result; external/stateful proofs declare `fresh: true` and run again.
Proofs classified `external_blocked` are declared evidence limitations rather
than commands: Scope retains their IDs in implementation evidence and the
summary, never executes them, and never reports them as passed. They do not
prevent a story checkpoint when all executable proofs pass.
Keep explicit repetition or flakiness checks in the project command itself.
Scope executes project commands; it does not provision fixtures.

On a failed group or final checkpoint, launch a fresh `implementation/debugging`
worker over the failed checks and approved affected boundary. Then rerun the
complete group proof set, or the complete epic set for a final checkpoint.
The shared policy allows two debugging jobs per implementation run, persisted
across resumes. Exhaustion stops with failed checks and retained changes.
This budget is separate from audit remediation. Do not launch an unconditional
model verification job. `needs_user` batches all currently discoverable material
questions. Out-of-scope writes, invalid attribution, or infrastructure failures
stop sequencing; never auto-revert user changes.

Execute the installed `audit_epic.md` contract inside this orchestrator with
the same worker/reviewer profiles and set; do not create a second
conversational orchestrator and do not substitute this session for reviewers.

For audit `FAIL`, group current remediation-required findings by coupled root
cause and launch one fresh `implementation/audit_remediation` worker per
bounded batch. Require pattern-wide inspection, sibling surfaces, strict
closure proof, and updated durable implementation evidence. Mark a finding
`remediated_pending_verification` only with that evidence, then rerun the complete epic proof set and run one
authorized targeted audit. Route product/architecture defects to refinement
and genuine user/documentation/accepted-risk decisions to the user. A targeted
`FAIL` or `BLOCKED` stops delivery.

After validated audit `PASS`, render the delivery summary directly from evidence:

```bash
"$PYTHON_CMD" "$WRAP_FINALIZER" render-summary "$EPIC_DIR" \
  --run "$RUN" --policy "$WRAP_POLICY" --audit-policy "$AUDIT_POLICY"
```

The renderer binds its summary to the approved manifest, current implementation
evidence, audit findings, and report. It performs no model work.

Immediately seal the completed delivery with the deterministic finalizer. It
must verify the current audit and durable implementation delta, including every
declared documentation target, before writing the seal:

```bash
"$PYTHON_CMD" "$WRAP_FINALIZER" seal "$EPIC_DIR" \
  --run "$RUN" --policy "$WRAP_POLICY" --audit-policy "$AUDIT_POLICY"
```

A failed seal leaves delivery incomplete and must be reported; never reconstruct
or hand-author it.

All deterministic artifact mutations and write workers share the same
non-blocking working-root lock. Do not launch them concurrently.

## Final response

Report exact state, branch/worktree, dependency merges, each story/job, changed
paths, proof and test counts, epic verification, audit attempt/providers/
outcome, remediation, summary, residual risk, and blockers. Do not commit
implementation/remediation, merge the epic, remove the worktree, or push.
Recommend `/wrap_epic {epic-id}` only for `delivery-complete`.

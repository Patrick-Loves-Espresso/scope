---
name: epic_refine
description: Refine an epic with bounded workers, two explicit gates, and independent review.
args: "{epic-id}"
---

# epic_refine

For this orchestration-only session, prefer `gpt-5.6-sol` at high effort in
Codex or `claude-opus-5` at high effort in Claude. The host session selects its
own model; worker/reviewer policies do not switch it. Respect an explicit user
model choice and do not interrupt an active workflow solely to change models.

You are the sole user-facing orchestrator. Fresh workers author one bounded
phase at a time; independent reviewers are read-only. Do not author or
semantically review epic artifacts in this session.

Invoking this command implicitly authorizes transmission of this workflow's
hash-bound review packet and only its declared artifacts to every reviewer
selected by the configured reviewer policy, profile, and set, including
external-provider CLIs. Do not ask for separate transmission approval before
launch. This does not authorize other providers, unbound files, credentials,
or reviewer writes.

Complete only when the product contract and final handoff have current
hash-bound authority, all required reviewers completed, every finding is
terminal, and `validate --phase handoff` passes. A user may preapprove either
gate for this epic, but the authority can be recorded only after the applicable
artifact boundary exists and can be hashed.

## Resolve and initialize

Resolve exactly one epic and installation. Use one Python interpreter for the
whole run.

```bash
EPIC_ID="{epic-id}"
WORKING_ROOT="$(pwd -P)"
GIT_COMMON_DIR="$(git -C "$WORKING_ROOT" rev-parse --path-format=absolute --git-common-dir)"
REPOSITORY_ROOT="$(cd "${GIT_COMMON_DIR}/.." && pwd -P)"
EPIC_DIR="$(find "$WORKING_ROOT/docs/epics" -mindepth 1 -maxdepth 1 -type d \
  -iname "*${EPIC_ID}*" -print | sort | head -1)"
# Codex:
PROVIDER="codex"; SCOPE_ROOT="$(cd "$WORKING_ROOT/plugins/scope" && pwd -P)"
# Claude instead uses:
# PROVIDER="claude"; SCOPE_ROOT="$(cd "$WORKING_ROOT/.claude" && pwd -P)"
WORKER="${SCOPE_ROOT}/scripts/scope-worker.py"
REVIEWER="${SCOPE_ROOT}/scripts/scope-reviewer.py"
REFINEMENT="${SCOPE_ROOT}/scripts/validate-refinement.py"
REVIEWER_POLICY="${SCOPE_ROOT}/config/reviewer-policy.yaml"
RUN="${REPOSITORY_ROOT}/tmp_debug/scope-runs/${EPIC_ID}/epic_refine/run.yaml"
WORKER_PROFILE="default"       # budget only when the user asks
REVIEWER_PROFILE="default"     # budget only when the user asks
REVIEWER_SET="standard"        # expanded only when the user asks
```

Require the runners, policies, v2 schemas, refinement worker, reviewer
template, and canonical templates. Reject an empty or ambiguous epic match.

```bash
"$PYTHON_CMD" "$WORKER" init \
  --repository-root "$REPOSITORY_ROOT" --working-root "$WORKING_ROOT" \
  --scope-root "$SCOPE_ROOT" --epic-id "$EPIC_ID" \
  --command epic_refine --worker-profile "$WORKER_PROFILE"
```

`init` prepares CodeGraph once and records either ready or degraded fallback in
the small run record. Workers and reviewers may query it; they never initialize
or repair it. Degraded CodeGraph means direct reads and `rg`, never skipped
work.

For an existing run, use only:

```bash
"$PYTHON_CMD" "$WORKER" status --run "$RUN"
"$PYTHON_CMD" "$WORKER" recover --run "$RUN"
"$PYTHON_CMD" "$WORKER" cancel --run "$RUN" \
  --job-id "$ACTIVE_JOB_ID" --reason "$REASON"
```

Take `ACTIVE_JOB_ID` from the current `status` response; stale cancellation is
rejected. Recovery either finalizes one valid completed result, reports an active
provider, or marks an interrupted job failed. It never republishes an invalid
result or repairs ownership. Stop on concurrent or out-of-scope changes and
report the exact paths; never auto-revert user work.

## Worker contract

Create one v2 job under `RUN`'s `jobs/` directory for each phase. It contains
only IDs and roots, bounded read/write scopes, hash-bound artifact and decision
references, required validations, `required_proof_ids: []`, and its result path. Do not embed artifact
contents or routing policy. The worker profile belongs to the orchestrator and
runner, not the job or worker prompt.

No worker write scope may include `refinement-state.yaml`, a review packet, a
reviewer receipt, or an audit attempt. Those are deterministic-tool outputs.
Use exact artifact paths, not the whole epic directory, for every write phase.

Launch a fresh worker for each authoring phase; `run` preflights internally:

```bash
"$PYTHON_CMD" "$WORKER" run --provider "$PROVIDER" \
  --role refinement --job "$JOB_PATH" --result "$RESULT_PATH" \
  --cwd "$WORKING_ROOT" --access workspace-write \
  --worker-profile "$WORKER_PROFILE"
```

Accept only a schema-valid result whose reported paths and validations match
the runner's actual post-job evidence. `needs_user` must batch every currently
discoverable blocking question; explain the evidence and tradeoffs, ask the
user, then launch a fresh job with the recorded decision reference. `blocked`,
`failed`, cancellation, HEAD drift, escaping symlinks, ignored or ordinary
out-of-scope writes, and ambiguous concurrent edits stop sequencing.

## Lean phase sequence

1. **Product.** A `refinement/product` worker creates or updates
   `details.md`, `acceptance-criteria.md`, `design.md`,
   and delivery-manifest v2. The manifest always contains an explicit
   `documentation_obligations` list, empty when no durable documentation change
   is required. It never writes `refinement-state.yaml`; the
   first deterministic authority operation initializes that file. The worker makes observable
   behavior, negative cases, measures, risk/capabilities, and pending product
   decisions concrete. Run:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" validate "$EPIC_DIR" \
     --phase product --repo-root "$WORKING_ROOT"
   ```

   Resolve any genuine product decision with the user and persist each answer
   as `record-authority --kind product_decision --subject <decision-id>` before
   a fresh worker applies it. Then obtain or consume the user's product-contract
   approval and record it directly under the shared non-blocking mutation lock:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" record-authority "$EPIC_DIR" --run "$RUN" \
     --authority-id "$AUTHORITY_ID" --gate product_contract \
     --source "$AUTHORITY_SOURCE" --decision approved
   ```

2. **Design and handoff.** One `refinement/design_handoff` worker receives
   the approved product contract and authors architecture, native contracts,
   failure behavior, story plans, proof strategy, and documentation obligations.
   Keep the smallest coherent story set. Each obligation has a `### DOC-NNN`
   design heading and one manifest row with its owner story, future target, and
   `requirement_ref`. Implement the target during its story group, not refinement.

   New manifests use schema v3. Every story lists `depends_on`. Every executable
   proof declares its direct `execution.argv`, relative `cwd`, result `parser`,
   approved environment variable names, and explicit `fresh` boolean. `command`
   is the display form of argv (`shlex.join`). Classify proofs as
   `existing_runnable`, `implementation_created`, or `external_blocked`.
   Use `pytest` for pytest counts, `scope-json` for a project command emitting
   exactly one `SCOPE_RESULT {"passed":1,"failed":0,"errors":0,"skipped":0}` line,
   and `exit-code` only for approved non-test checks. External/stateful commands
   require `fresh: true`. Do not weaken existing proof obligations to reduce time.

   Execute existing runnable baselines through the runner, preserving failures:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" baseline-proofs "$EPIC_DIR" --run "$RUN"
   ```

   Implementation-created proofs are not run yet. The executor writes baseline
   evidence into the manifest. Run `validate --phase product` again; packet
   creation checks story/proof structure. Only manifest declarations are handoff
   bound; do not bind expected-to-change implementation documentation content.

3. **Independent review.** Create one full immutable packet, then let the
   reviewer runner preflight every assignment as one all-provider barrier,
   launch them concurrently, and publish one receipt:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" create-review-packet "$EPIC_DIR" --run "$RUN" \
     --kind full --reviewer-profile "$REVIEWER_PROFILE" \
     --reviewer-set "$REVIEWER_SET"
   "$PYTHON_CMD" "$REVIEWER" run --workflow refinement --packet "$PACKET" \
     --repo-root "$WORKING_ROOT" --policy "$REVIEWER_POLICY" --run "$RUN" \
     --reviewer-profile "$REVIEWER_PROFILE" --reviewer-set "$REVIEWER_SET"
   "$PYTHON_CMD" "$REFINEMENT" apply-review-receipt "$EPIC_DIR" "$RECEIPT" \
     --run "$RUN"
   ```

   Never substitute this session for a reviewer. Retry only a proven
   pre-semantic infrastructure failure with reviewer
   `--repair-infrastructure`; semantic failure or timeout requires a new
   authorized review boundary. Every valid candidate is applied even when a
   review decision is findings or blocked. If an unapplied failed or canceled
   receipt has no completed assignment or semantic output and its only later
   incompatibility is a replaced reviewer template, leave that receipt in
   place; `create-review-packet` may issue the next same-kind attempt.

4. **Correction.** Give one `refinement/correction` worker the complete open
   batch. It updates only affected canonical artifacts and stores closure
   commands, counts, affected paths, hashes, and source candidate IDs inline in
   `refinement-findings.yaml`; permanent state never depends on `tmp_debug`.
   If a correction changes an existing runnable proof command, rerun
   `baseline-proofs` before review. For corrected fingerprints, create one targeted packet by repeating
   `--target-fingerprint <fingerprint>`, run the original required reviewers,
   and apply the receipt. Only independent targeted evidence may change
   `corrected` to `verified`. Accepted risk requires a separate hash-bound
   `record-authority --kind accepted_risk --subject <fingerprint>` row.

   Minor means a concrete low-risk defect. Fix minors after the first full
   review and again after the second completed review when still open. After
   the third completed review, the applier marks remaining minors `deferred`:
   optional and visible at final approval, never verified. Infrastructure retries
   do not advance this count. Major/blocking findings remain mandatory. Optional
   polish belongs in Suggestions, outside parsed Findings. Targeted reviews
   introduce no new candidates.

5. **Finalize.** When `validate --phase review` passes, render the summary:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" render-summary "$EPIC_DIR" --run "$RUN"
   ```

   Present any deferred minors at the existing final-handoff gate. Obtain or
   consume approval and record it:

   ```bash
   "$PYTHON_CMD" "$REFINEMENT" record-authority "$EPIC_DIR" --run "$RUN" \
     --authority-id "$AUTHORITY_ID" --gate final_handoff \
     --source "$AUTHORITY_SOURCE" --decision approved
   "$PYTHON_CMD" "$REFINEMENT" validate "$EPIC_DIR" \
     --phase handoff --repo-root "$WORKING_ROOT"
   ```

All deterministic artifact mutations take the same working-root lock
non-blocking. Never run one while a write worker is active.

## Final response

Report status, both authorities and their sources, worker jobs, proof baseline
counts, reviewer assignments/receipt, findings and closure, changed canonical
paths, documentation obligations and owner stories, and residual risk. Never claim completion if any provider, validation,
authority, proof, or finding is incomplete.

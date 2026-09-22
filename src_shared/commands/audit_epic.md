---
name: audit_epic
description: Run a read-only evidence audit and return PASS, FAIL, BLOCKED, or NOT_READY.
args: "{epic-id}"
---

# audit_epic

For this orchestration-only session, prefer `gpt-5.6-sol` at high effort in
Codex or `claude-opus-5` at high effort in Claude. The host session selects its
own model; worker/reviewer policies do not switch it. Respect an explicit user
model choice and do not interrupt an active workflow solely to change models.

You are the sole user-facing orchestrator. Deterministic tools own audit state,
independent reviewers own semantic review, and one bounded read-only worker
normalizes their findings. Do not inspect implementation broadly, review it
semantically, or remediate it in a direct audit.

Invoking this command implicitly authorizes transmission of this workflow's
hash-bound review packet and only its declared artifacts to every reviewer
selected by the configured reviewer policy, profile, and set, including
external-provider CLIs. Do not ask for separate transmission approval before
launch. This does not authorize other providers, unbound files, credentials,
or reviewer writes.

Return exactly one outcome:

- `PASS`: every required gate and reviewer completed and all findings terminal;
- `FAIL`: current findings are remediable inside the approved boundary;
- `BLOCKED`: authority, provider, or required evidence is unavailable;
- `NOT_READY`: durable implementation evidence fails before an attempt exists.

Audit may write only its attempt, evidence, findings, report, and ignored
runtime files. It never changes implementation, tests, approved handoff
artifacts, or Git history.

## Resolve and readiness

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
AUDIT="${SCOPE_ROOT}/scripts/audit-artifacts.py"
AUDIT_POLICY="${SCOPE_ROOT}/config/audit-policy.yaml"
REVIEWER_POLICY="${SCOPE_ROOT}/config/reviewer-policy.yaml"
RUN="${REPOSITORY_ROOT}/tmp_debug/scope-runs/${EPIC_ID}/audit_epic/run.yaml"
WORKER_PROFILE="default"       # budget only when the user asks
REVIEWER_PROFILE="default"     # budget only when the user asks
REVIEWER_SET="standard"        # expanded only when the user asks
```

Resolve exactly one epic and one interpreter. Require the runners, policies,
v2 run contract, executor policy, reviewer template, current approved refinement
handoff, delivery manifest, and implementation evidence.

```bash
"$PYTHON_CMD" "$REFINEMENT" validate "$EPIC_DIR" \
  --phase handoff --repo-root "$WORKING_ROOT"
"$PYTHON_CMD" "$AUDIT" verify-evidence "$EPIC_DIR" \
  --repo-root "$WORKING_ROOT" --policy "$AUDIT_POLICY"
```

On either failure, return `NOT_READY` with every verifier error and create no
audit attempt. Otherwise initialize the compact run:

```bash
"$PYTHON_CMD" "$WORKER" init \
  --repository-root "$REPOSITORY_ROOT" --working-root "$WORKING_ROOT" \
  --scope-root "$SCOPE_ROOT" --epic-id "$EPIC_ID" \
  --command audit_epic --worker-profile "$WORKER_PROFILE"
```

For resume or interruption use only `status`, `recover`, and identity-checked
`cancel`. Pass the current active job ID returned by `status`; a stale cancel
must be rejected. Never repair ownership, auto-revert user work, or create
parallel incident state.

```bash
"$PYTHON_CMD" "$WORKER" status --run "$RUN"
"$PYTHON_CMD" "$WORKER" recover --run "$RUN"
"$PYTHON_CMD" "$WORKER" cancel --run "$RUN" \
  --job-id "$ACTIVE_JOB_ID" --reason "$REASON"
```

## Prepare one attempt

Use `full` unless every named target is already
`remediated_pending_verification`. A targeted attempt names each coupled
finding with repeated `--finding`:

```bash
"$PYTHON_CMD" "$AUDIT" prepare "$EPIC_DIR" --run "$RUN" \
  --mode "$MODE" $FINDING_ARGUMENTS --reason "$REASON" \
  --reviewer-profile "$REVIEWER_PROFILE" --reviewer-set "$REVIEWER_SET" \
  --policy "$AUDIT_POLICY"
```

The printed directory contains one canonical `audit-attempt.yaml` and one
immutable `review-packet.yaml`. Preparation binds the current base HEAD plus
tracked/untracked content state, the approved handoff and native artifacts,
implementation evidence, exact gates, reviewer assignments, and target IDs. It
does not require implementation HEAD to equal its base because implementation
is intentionally uncommitted.

One full and one targeted attempt are the normal hard budget. Minor defects
remain mandatory in both; there is no third audit round. A pending attempt
is resumed only when its fingerprint, boundary, profiles, set, mode, and targets
match. Never delete, renumber, or use free-text reason to reset the budget.

## Mechanical gates

Use the deterministic executor for pending gates:

```bash
"$PYTHON_CMD" "$AUDIT" execute-gates "$EPIC_DIR" "$ATTEMPT_DIR" --run "$RUN"
```

The executor captures raw output, strict counts, exit code and timing. It reuses
implementation evidence only for identical argv, cwd, source and approved
environment fingerprints with intact logs. `fresh: true` always reruns. Missing
or ambiguous counts fail closed. No model transcribes successful execution.
A targeted closure must use an approved manifest command or declare the same
explicit execution contract in hash-bound `remediation.execution`. For a semantic
closure predicate, retain its text for independent review and bind a runnable
check separately; `remediation.proof_level` defaults to unit and may explicitly
name inspection for a non-test check. Reuse runner result rows for remediation
checks; never transcribe counts from memory.

An unexecuted blocked gate uses `--status blocked --reason ...` and no counts.
`not_applicable` is never a free-text waiver: first record a current user or
preapproval authority row naming that gate, then cite its ID:

```bash
"$PYTHON_CMD" "$AUDIT" record-authority "$EPIC_DIR" "$ATTEMPT_DIR" \
  --run "$RUN" --authority-id "$AUTHORITY_ID" --kind gate_not_applicable \
  --subject "$GATE_ID" --source "$AUTHORITY_SOURCE" --decision approved
"$PYTHON_CMD" "$AUDIT" record-gate "$EPIC_DIR" "$ATTEMPT_DIR" \
  --run "$RUN" --gate "$GATE_ID" --status not_applicable \
  --authority-id "$AUTHORITY_ID"
```

Then run:

```bash
"$PYTHON_CMD" "$AUDIT" validate "$EPIC_DIR" "$ATTEMPT_DIR" \
  --phase pre_review --repo-root "$WORKING_ROOT" --policy "$AUDIT_POLICY"
```

## Independent review

When the packet has assignments, the reviewer runner performs one all-provider
preflight barrier and then launches them concurrently with the run-level
CodeGraph state:

```bash
"$PYTHON_CMD" "$REVIEWER" run --workflow audit --packet "$PACKET" \
  --repo-root "$WORKING_ROOT" --policy "$REVIEWER_POLICY" --run "$RUN" \
  --reviewer-profile "$REVIEWER_PROFILE" --reviewer-set "$REVIEWER_SET"
```

Reviewers use direct read-only CLIs and immutable artifact hashes. Do not
substitute this session or another provider. Retry only a proven pre-semantic
infrastructure failure in the same packet with `--repair-infrastructure`.
Semantic timeout, invalid output, a question, or unverified evidence remains
visible and blocks PASS. Every valid candidate is ingested even from an
aggregate failed or blocked receipt.

## Source-bounded synthesis and decision

Before synthesis, resolve any genuine accepted-risk request with the user and
record hash-bound authority naming its fingerprint:

```bash
"$PYTHON_CMD" "$AUDIT" record-authority "$EPIC_DIR" "$ATTEMPT_DIR" \
  --run "$RUN" --authority-id "$AUTHORITY_ID" --kind accepted_risk \
  --subject "$FINGERPRINT" --source "$AUTHORITY_SOURCE" --decision approved
```

Synthesize all validated sources directly, without a model job:

```bash
"$PYTHON_CMD" "$AUDIT" apply-synthesis "$EPIC_DIR" "$ATTEMPT_DIR" \
  --run "$RUN" --policy "$AUDIT_POLICY"
```

The applier requires every deterministic, reviewer, and active-ledger source
exactly once; merges only identical fingerprints, rejects conflicting
dispositions, categories, or closure tests; preserves maximum severity and minority evidence; and verifies
accepted-risk authority. A targeted attempt can verify only its named findings
after strict closure proof and every required detecting provider.

```bash
"$PYTHON_CMD" "$AUDIT" finalize "$EPIC_DIR" "$ATTEMPT_DIR" \
  --run "$RUN" --policy "$AUDIT_POLICY"
"$PYTHON_CMD" "$AUDIT" validate "$EPIC_DIR" "$ATTEMPT_DIR" \
  --phase complete --repo-root "$WORKING_ROOT" --policy "$AUDIT_POLICY"
```

Only complete validation authorizes the outcome. Direct audit never launches a
write implementation worker. A later explicit remediation request—or the
parent `implement` workflow—may remediate `FAIL` findings and request the one
targeted attempt.

## Final response

Report outcome, attempt/mode, repository fingerprint, every gate with exact
counts, reviewer assignment and decision coverage, findings with source IDs,
authority, residual risk, and unavailable evidence. For `NOT_READY`, confirm
that no attempt was created. Never claim PASS when any gate, reviewer, source,
hash, question, or finding is incomplete.

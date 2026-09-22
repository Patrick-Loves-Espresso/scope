# Scope execution update

Newly refined epics use delivery-manifest v3. Worker authoring messages remain
v2; the manifest determines who owns proof execution. An approved v1/v2 epic
must continue with its approving Scope installation. Do not edit approved
hash-bound artifacts to migrate it. Legacy readers remain for archived evidence.

## Workflow

- Refinement: product author → existing product approval → one design/handoff
  author → deterministic baseline execution → independent review/correction →
  deterministic summary → existing final-handoff approval.
- Implementation: fresh worker per dependency-connected group, initially at
  most three stories; deterministic group checkpoint; complete final epic
  checkpoint; independent audit. Failed checkpoints retain authored changes.
- Repair: at most two pre-audit debugging jobs per implementation run, counted
  before launch and retained across resumes. Audit remediation has its existing
  separate budget. After repair, rerun the complete relevant proof set.
- Audit: executor gates, independent reviewers, deterministic synthesis and
  decision. Exactly one full and one targeted audit remain the normal limit.

Minor findings mean concrete low-risk defects. They require correction after
reviews one and two. Remaining minors become `deferred` after review three
completes; the final approval summary shows them as unverified and optional.
Major/blocking findings stay mandatory. Infrastructure retries do not increment
the review count. Pure polish goes in an unparsed Suggestions section.

## Proof execution

Every executable manifest proof keeps its story owner, level, classification,
expected result and command, and adds:

```yaml
command: pytest -q tests/unit/test_feature.py
execution:
  argv: [pytest, -q, tests/unit/test_feature.py]
  cwd: .
  parser: pytest
  environment: []
  fresh: false
```

`command` equals Python `shlex.join(argv)` and is only a display value. Scope
executes argv directly. Shell executables are rejected. Scope never provisions
project services or fixtures. Keep setup in approved project commands.

Parsers:

- `pytest`: exactly one unambiguous summary. Expected failures, unexpected
  passes and deselected/skipped tests cannot silently become a clean pass.
- `scope-json`: exactly one output line prefixed `SCOPE_RESULT ` followed by a
  JSON object with nonnegative integer `passed`, `failed`, `errors`, `skipped`.
  Projects may provide an adapter for their native tool or aggregate suite.
- `exit-code`: only non-test static/runtime/operational/inspection commands.
  A zero exit represents one passed check; semantic adequacy remains reviewed.

A clean pass requires exit 0, at least one passed check and zero failures,
errors and skips. Raw logs, result counts, duration and execution context are
stored in `reviews/proofs/`. Workers cannot publish or edit this evidence.
Workers can run approved `allowed_commands` for local feedback and return an
empty `proof_evidence` array; the runner publishes authoritative results.

`external_blocked` proofs have no execution contract. Scope excludes them from
story and final execution, retains their IDs as declared unavailable proofs in
implementation evidence and the delivery summary, and never reports them as
passed. A story is implementation-verified when all of its executable proofs
pass; the external claim remains `insufficient_evidence` until a separately
authorized handoff replaces that declaration with executable evidence.

Identical argv, cwd, parser, executable, source and approved environment fingerprints may
share a successful result within a checkpoint or from final verification into
audit. Log hashes must still match. There is no global cache. External/stateful
proofs require `fresh: true`; live and operational levels enforce this. Other
integration proofs must be marked fresh whenever external state matters.
Keep intentional repetition/flakiness runs explicit in the project command.
Source changes invalidate prior results; earlier groups cannot certify the
final workspace. Environment values are passed only from the policy allowlist
plus manifest-approved names, and are hashed rather than stored in receipts.

The source fingerprint covers Git HEAD and tracked/untracked changes, excluding
Scope runtime/index and proof/audit evidence. Ignored fixtures, remote services,
time-dependent results or other untracked external inputs require fresh proofs.
Proof commands must leave deliverable source unchanged; mutations fail the
checkpoint and require attribution before continuing.

## Routing and evaluation

Product refinement and diagnostic workers use `gpt-6-sol` or `claude-opus-5-5`
at high effort in both profiles. Prefer the same model/effort for the
orchestration-only host session. That selection belongs to the host; the worker
policy cannot switch the current conversation's model.

Codex corrections and story groups use `gpt-6-sol`; design/handoff, audit
remediation, debugging, and quality-profile independent reviews retain
`gpt-6-astra`. All Claude workers and reviewers use `claude-opus-5-5` at their
existing phase-specific efforts. Budget review remains high. Muse Spark
(`meta/muse-spark-1.3-contributor`) uses OpenCode `--variant high` in both profiles
and is the third standard audit reviewer; Gemini remains expanded-only.

This routing change is not a claim of equivalent model quality or effort. Keep
the same proof, review, and approval gates. Assess rework, missed requirements,
and total usage on a new epic. Current worker routing is per phase, not per-job
complexity; do not silently switch difficult jobs or change a bound run's policy.

Each worker starts a fresh process. Group size is configurable in
`config/execution-policy.yaml`; a three-story cap is an initial evaluation
choice, not a claim that a model cannot handle a larger epic.

New review packets retain a Git replay ref under `refs/scope/replays/` using a
temporary index seeded from HEAD. Snapshots include new non-ignored files and
leave HEAD, the real index and working files unchanged. They are local refs,
not commits on the user's branch or remote publications.

On the first new epic, inspect existing worker timings, proof receipts, retries,
findings and these snapshots. Compare authoring/repair time, actual command
time, duplicate executions avoided, reviewer latency and unique actionable
findings. Worker elapsed time includes its proof checkpoint: subtract the
checkpoint duration when comparing author time, and count only non-reused proof
rows as command executions. Check especially group size, context breadth, parser fit and repair
budget. Evaluate cheaper reviewers or lower efforts against the retained same
content before making them required. No timing conclusions are drawn from the
pre-v2 SAG-111 epic.

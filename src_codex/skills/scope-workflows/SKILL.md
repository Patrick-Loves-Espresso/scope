---
name: scope-workflows
description: Run Scope-style product engineering workflows in Codex using command playbooks, role instructions, approval gates, docs, and worktrees.
---

# Scope Workflows for Codex

Use this skill when the user asks for Scope-like workflows in Codex, invokes `scope:<command>`, or asks to run a Scope command such as `prd_refine`, `epic_refine`, `implement`, `audit_epic`, `wrap_epic`, `re_documentation`, or `sync_product`.

## Artifact Locations

Resolve the installed Scope root once from the checkout where the command starts.

- Codex plugin root: `./plugins/scope/`

When an implementation command creates or resumes `./wip/{epic-id}`, retain
that absolute installed root; ignored plugin files are not copied by Git
worktree creation. If a new command starts inside a worktree, require an
installation in that checkout and stop if none exists—do not silently choose an
unrelated checkout.
Do not read `.claude/` as a Codex command, role, governance, skill, template, or override source. `.claude/` belongs to the Claude installation only.

Within the current checkout:

- Commands: `commands/{command}.md`
- Role instructions: `agents/{role}.md`
- Governance: `governance/*.md`
- Documentation templates and tracking adapters: `skills/`
- Scope reference docs: `docs/`

Prefer the project installation resolved at command start because it may contain
project-specific edits.

## Command Invocation

Treat these as equivalent user requests:

- `scope:epic_refine E1`
- `/epic_refine E1`
- `run epic_refine for E1`

Execution steps:

1. Read the matching command file.
2. Read referenced role files from `agents/`.
3. Read referenced skills from `skills/`.
4. Read governance files when the command or role requires them.
5. Execute the command as a Codex workflow, preserving its product-contract,
   material-decision, and final-handoff authority boundaries.
6. Write or update project artifacts in `docs/`, `.scope/`, and `./wip/` as the command specifies.

### Nested Scope Command Execution

When a Scope command says to run another Scope command, Codex must execute the
referenced workflow from the parent's retained Scope root at
`commands/{command}.md`.

Do not satisfy a nested command by producing similarly named artifacts, writing a local summary, or performing an informal equivalent workflow unless the referenced command file explicitly allows that substitution.

For every nested Scope command:

1. Read the referenced command file from the retained Scope root.
2. Execute its initialization, validation, artifact, reviewer, remediation, and output requirements as written.
3. Preserve the referenced command's proof requirements and canonical state,
   finding, attempt, reviewer-receipt, and status artifacts.
4. Report the actual nested command evidence in the parent command result.

If the nested command cannot be executed, the parent command is not delivery-complete. Report it as blocked or incomplete with the concrete reason instead of fabricating the nested command's artifacts.

## Role Mapping

The main Codex session remains the sole conversational orchestrator. Scope
commands run exactly four protocol roles in fresh bounded workers through the
installed runner:

- `refinement`: product, design, handoff, correction, and finalization phases;
- `implementation`: story, verification, remediation, debugging, and summary phases;
- `audit`: read-only finding synthesis from bounded sources;
- `diagnostic`: one bounded read-only investigation.

Do not perform these worker roles in the main session or substitute Codex
sub-agents for the Scope worker protocol. The command owns worker packets,
results, reviewer receipts, deterministic gates, and user interaction.

Standalone architect, developer, product-owner, and reverse-engineering agent
files remain available for non-worker workflows; they are not implementation
substitutes inside `epic_refine`, `implement`, or `audit_epic`.

Independent refinement and audit reviewer roles are the exception. The review
packet derives its assignments from `refinement-policy.yaml` or
`audit-policy.yaml`; required assignments run concurrently in fresh processes.
The standard refinement set uses Claude and Codex, while high/critical risk adds
the author provider as a capability specialist. Reviewer model/effort comes
only from `reviewer-policy.yaml`. Never perform those roles in the
orchestrating context.

## Codex Adaptations

- Replace Claude `Read`, `Glob`, and `Grep` with local file reads and `rg`.
- Replace Claude `TaskCreate/TaskUpdate` with `.scope/` tracking files, Codex plans, or explicit checklists.
- Replace Claude `AskUserQuestion` with concise approval or clarification questions.
- Preserve the two refinement gates and stop for any material decision the
  command assigns to the user. Honor only an explicit per-epic preapproval.
- Use git worktrees exactly as Scope specifies for implementation commands.
- For Codex, the implementation worktree root is `./wip/`.
- Keep implementation in the worktree once a command moves there.
- Do not rebind the retained Scope root after moving into a worktree.

## Context Sources

Use Obsidian MCP when available for prior decisions, lessons, and related product notes. If Obsidian MCP is unavailable, continue with local repo search and say that MCP was unavailable.

Scope uses CodeGraph 1.5 or newer only through its command-line interface. Do
not configure or invoke a CodeGraph MCP for Scope work.

### CodeGraph Working Directory Rule

CodeGraph is scoped to the active repository/worktree root.

- During refinement and planning, use the main repository root as the CodeGraph project path.
- During implementation and audit, after the workflow changes into `./wip/{epic-id}`, use that worktree as the CodeGraph project path.
- Do not query the main repo CodeGraph DB for implementation code that is being changed inside a worktree.
- The worker runner loads `config/codegraph-policy.yaml`, verifies the CLI
  version, initializes a missing index only when its directory is Git-ignored,
  and records one compact run-level state. Implementation incrementally
  synchronizes before each new write job; refinement and read-only audit reuse
  the prepared state. Absence or failure is explicit and falls back to direct
  reads plus `rg`; it does not weaken any validation or proof obligation.
- Workers and external reviewers are query-only. They must not run `init`,
  `index`, `sync`, `uninit`, `daemon`, `unlock`, `install`, `uninstall`,
  `telemetry`, or `upgrade`.
- Start focused investigation with `codegraph explore --path . --max-files 8
  "<specific symbols, files, or question>"`. Use `node` for exact source and
  its caller/callee trail, then focused `query`, `callers`, `callees`, or
  `impact` as needed. Broad natural-language exploration can be noisy, so name
  known symbols or files.
- CodeGraph source blocks are direct indexed source; relationship and blast
  radius output is derived. Confirm ambiguous or safety-critical relationships
  against source and tests. After editing, directly read changed files if the
  index reports staleness.
- `affected` must use an explicit filter and configured depth, for example
  `codegraph affected --path . --depth 3 --filter 'tests/unit/**/*.py'
  src/example.py`. Run applicable filters separately. Its output adds tests; it
  never authorizes omitting required validation.
- `scope:wrap_epic` delegates the approved exact commit, merge, and main-root
  CodeGraph refresh to its deterministic finalizer. Do not run a second Git or
  CodeGraph lifecycle beside it.

## Quality Bar

Follow repository instructions in `AGENTS.md` when present. Generated intelligence outputs must be read and judged as a product, not just mechanically produced files.

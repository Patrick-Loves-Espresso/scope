---
name: scope-workflows
description: Run Scope-style product engineering workflows in Codex using command playbooks, role instructions, approval gates, docs, and worktrees.
---

# Scope Workflows for Codex

Use this skill when the user asks for Scope-like workflows in Codex, invokes `scope:<command>`, or asks to run a Scope command such as `prd_refine`, `epic_refine`, `implement`, `audit_epic`, `wrap_epic`, `re_documentation`, or `sync_product`.

## Artifact Locations

Resolve the installed Scope root once from the checkout where the command starts.

- Codex plugin root: `./plugins/scope/`

When `implement` creates or resumes `./wip/{epic-id}`, keep using the absolute
installed root of the main checkout; ignored plugin files are not copied into a
Git worktree.
Do not read `.claude/` as a Codex command, role, governance, skill, template, or override source. `.claude/` belongs to the Claude installation only.

Within the installation:

- Commands: `commands/{command}.md`
- Worker and reviewer prompts: `workers/`
- Role instructions: `agents/{role}.md`
- Governance: `governance/*.md`
- Policy: `config/scope-policy.yaml`
- Lifecycle scripts: `scripts/scope_*.py`
- Documentation templates and tracking adapters: `skills/`
- Scope reference docs: `docs/`

## Command Invocation

Treat these as equivalent user requests:

- `scope:epic_refine E1`
- `/epic_refine E1`
- `run epic_refine for E1`

Read the matching command file and execute it as written, with `HOST=codex` and
`SCOPE_ROOT` set to the plugin root. Preserve its two user gates (acceptance
criteria and merge) and ask the user only where the command says so.

When a command says to execute another Scope command (for example `implement`
executing `audit_epic`), read that command file from the same installation and
execute it in the same session. Never produce look-alike artifacts instead of
running it; if it cannot run, report the parent command as blocked.

## Roles

The main Codex session is the only orchestrator and the only party that talks
to the user. The lifecycle commands launch fresh processes through
`scripts/scope_launch.py`: a planner and an implementer on the host provider,
and independent read-only reviewers (Claude and Codex, with Muse Spark as the
fallback). Never do a worker's or reviewer's job in the orchestrating session,
and never substitute Codex sub-agents for them. Scope's runner, not a model,
executes the validation commands and writes `verification.yaml`.

The standalone architect, developer, product-owner, and reverse-engineering
agents serve workflows outside these lifecycle commands.

## Codex Adaptations

- Replace Claude `Read`, `Glob`, and `Grep` with local file reads and `rg`.
- Replace Claude `TaskCreate/TaskUpdate` with Codex plans or explicit checklists.
- Replace Claude `AskUserQuestion` with concise approval or clarification questions.
- Use git worktrees exactly as Scope specifies; the implementation worktree root is `./wip/`.

## Context Sources

Use Obsidian MCP when available for prior decisions, lessons, and related product notes. If Obsidian MCP is unavailable, continue with local repo search and say that MCP was unavailable.

CodeGraph is optional and used only through its command-line interface. When
the `codegraph` CLI is installed, the lifecycle commands run one `codegraph
sync` (or `init` when `.codegraph/` is missing and git-ignored) in the checkout
or worktree they work in. Workers and reviewers only query it (`explore`,
`node`, `query`, `callers`, `callees`, `impact`, `affected`) and confirm what
matters in the source; they never initialize, index, or sync it.

## Quality Bar

Follow repository instructions in `AGENTS.md` when present. Generated intelligence outputs must be read and judged as a product, not just mechanically produced files.

Also support Codex (but I'm too lazy to update the picture)
<p align="center">
  <img src="assets/scope-banner.png" alt="SCOPE Banner" />
</p>

# SCOPE

**Simple Claude/Codex Orchestrator for Product Engineering**

SCOPE turns Claude Code or OpenAI Codex into a structured product engineering environment. It gives you slash commands that take a product from PRD to implemented, tested, and audited epics, with user authority at the product contract, material decisions, and final handoff.

Claude: idea → /prd_create → /prd_refine → /prd_breakdown → /epic_refine → /implement → /wrap_epic
Codex: idea → scope:prd_create → scope:prd_refine → scope:prd_breakdown → scope:epic_refine → scope:implement → scope:wrap_epic

**Already have code but no docs?** Use `/re_documentation` or `run scope:re_documentation` to reverse engineer the product and architecture documentation from your existing codebase.

Scope includes deterministic Python validators and a bounded worker runner.
`epic_refine`, `implement`, and `audit_epic` remain conversational: the public
command owns every user decision while fresh worker processes perform one
product contract, design/handoff, story group, or correction batch at a time. Independent
reviewers remain separate and read-only. You never need to enter a worker
thread. Worker model/effort routing is provider-local, with explicit quality
and budget profiles; cross-provider choices are limited to independent review.
Reviewer profile and reviewer set are selected separately and recorded in
durable review evidence. CodeGraph 1.5+ is an optional CLI-only accelerator:
Scope prepares one Git-ignored index per command run, incrementally refreshes it
between implementation write jobs, and falls back to direct inspection without
weakening proof when unavailable. Other MCP servers remain optional.

Newly refined epics use manifest v3: dependency-connected groups of up to three
stories, runner-owned proof execution, and deterministic audit synthesis and
summaries. Two pre-audit debugging jobs are allowed per implementation run.
Minors must be addressed after the first two refinement reviews; remaining
minors become visibly deferred after the third completed review. Audit retains
one full and one targeted review, with minors mandatory in both.

Product refinement and diagnostic workers use Sol (`gpt-6-sol`) or Opus 5.5
(`claude-opus-5-5`) at high effort in both quality and budget profiles. Use those
models at high effort for the user-facing orchestrator too; select the session
model in the host, since worker policies do not change it. Codex corrections,
story groups, and the standalone developer use Sol; design/handoff, audit
remediation, debugging, and independent reviews retain Astra (`gpt-6-astra`).
Claude workers and reviewers use Opus 5.5 at their existing phase efforts.
The standard audit uses Muse Spark (`meta/muse-spark-1.3-contributor`, variant
`high`) as its third independent reviewer. Gemini remains expanded-only.
Evaluate the new routing on a new epic; equivalent quality and quota savings
have not yet been measured in Scope.

Keep approved or running older epics on their approving Scope version. Do not
rewrite their hash-bound manifests or evidence. See
[the execution contract](docs/scope-modernization.md) for proof formats,
compatibility and the next-epic evaluation checklist.

**NOTE:** for Codex, replace "/" with "run scope:"

---

## What It Does

### Forward Engineering (PRD to Code)

- **`/prd_create`** — Interview the user to create a lightweight first-pass PRD before refinement
- **`/prd_refine`** — Interactively refine a product requirements document using a checklist-driven approach
- **`/prd_breakdown`** — Break the PRD into implementable epics with architecture and dependency analysis
- **`/epic_refine`** — Build a risk-appropriate product, architecture, native-contract, story, and proof handoff through a product-contract gate, independent review, and final approval
- **`/implement`** — Deliver the validated stories sequentially in a git worktree, including tests, runtime proof, and audit remediation
- **`/audit_epic`** — Perform a read-only evidence audit, followed by one targeted verification after implementation remediates named findings
- **`/wrap_epic`** — Verify the sealed delivery, archive it, and commit/merge the exact approved staged tree
- **`/sync_product`** — Update product documentation when implementation reveals scope changes

### Reverse Engineering (Code to Docs)

- **`/re_documentation`** — Reverse engineer product and architecture documentation from an existing codebase. Two agents scan your code, interview you about decisions and rationale, then generate 24 documentation files (9 product + 15 architecture)

### Session Continuity

- **`/session-handoff`** / **`scope:session-handoff`** — Create an ephemeral `session-handoff.md` at the active worktree or project root when a long session has become inefficient. The file captures enough durable context for a fresh agent to assess the state and recommend the next course of action without treating unconfirmed next steps as instructions. The file is overwritten on each run and should not be tracked by git.

Scope stops for user authority when product behavior, material boundaries, or
final handoff approval requires it. A user may explicitly preapprove a named
gate for one epic. Nothing is merged without the command's documented authority.

## How It Works

SCOPE uses Claude Code or Codex for the user-facing conversation and controlled
provider processes for bounded repository work:

### Claude

- **Slash commands** (`.claude/commands/`) define multi-phase workflows with approval gates
- **Agent definitions** (`.claude/agents/`) provide standalone architect, developer, product-owner, and reverse-engineering roles
- **Worker contracts** (`.claude/workers/`) isolate refinement and implementation jobs
- **Skills** (`.claude/skills/`) provide documentation templates (Arc42+C4 for technical, Atlassian Blueprint for product)
- **Public commands** retain story dependencies, user gates, and sequencing while bounded workers execute one job at a time
- **Git worktrees** isolate implementation from the main branch

### Codex

- **commands** (`plugins/scope/commands/`) define multi-phase workflows with approval gates
- **Agent definitions** (`plugins/scope/agents/`) provide standalone architect, developer, product-owner, and reverse-engineering roles
- **Worker contracts** (`plugins/scope/workers/`) isolate refinement and implementation jobs
- **Skills** (`plugins/scope/skills/`) provide documentation templates (Arc42+C4 for technical, Atlassian Blueprint for product)
- **Public commands** retain story dependencies, user gates, and sequencing while bounded workers execute one job at a time
- **Git worktrees** isolate implementation from the main branch

The lean refinement validator, audit validator, and worker runner require
Python 3 and the packages in `requirements.txt`. Scope has no persistent
service or database; ignored runtime records live under `tmp_debug/` and
canonical workflow state remains in epic and audit artifacts.

To enable CodeGraph, install its 1.5+ CLI and add `.codegraph/` to the target
repository's `.gitignore`. Scope never installs or upgrades CodeGraph and never
allows workers/reviewers to mutate the index lifecycle themselves.

## Installation

Clone Scope first:

```bash
git clone https://github.com/Patrick-Loves-Espresso/scope.git
cd scope
```

Install the validator dependency with the Python interpreter Scope should use:

```bash
python3 -m pip install -r requirements.txt
```

On Windows:

```bat
py -3 -m pip install -r requirements.txt
```

Set `SCOPE_PYTHON` when Scope should use a different interpreter.

### macOS and Linux

**Install to a project** (commands available only in that project):

```bash
./install.sh /path/to/your-project
```

**Install to user directory** (commands available in all projects):

```bash
./install.sh --user
```

**Install to current directory** (default):

```bash
./install.sh
```

### Windows

Run these commands from Command Prompt. In PowerShell, prefix the installer with `./` or `.\`.

**Install to a project** (commands available only in that project):

```bat
install.bat "C:\path\to\your-project"
```

**Install to user directory** (commands available in all projects):

```bat
install.bat --user
```

**Install to current directory** (default):

```bat
install.bat
```

Both installers copy the same commands, bounded worker contracts, agents,
skills, governance files, deterministic scripts, policies, schemas, and Codex
plugin assets. Project installs also create `.scope/config.yaml` when it does
not already exist. The installed configuration is ready to use with local
Markdown documentation in `./docs` and local YAML tracking in `./tracking`;
Jira and Confluence are not required.

## Quick Start

**Starting from an idea or a PRD:**

```
1. /prd_create              → Create a first-pass PRD if you do not have one
2. /prd_refine              → Refine it interactively
3. /prd_breakdown           → Get epics with dependencies
4. /epic_refine EPIC-001    → Refine the first epic (product + final approval)
5. /implement EPIC-001      → Implement it story-by-story in a worktree
6. /wrap_epic EPIC-001      → Approve the exact sealed closure commit and merge
```

**Starting from existing code:**

```
1. /re_documentation        → Reverse engineer product + architecture docs
2. /prd_refine              → Refine/extend the PRD for new features
3. /prd_breakdown           → Break into epics
4. Continue as above
```

## Project Structure (Target Project)

After installation, your project will generate this structure as you work:

```
your-project/
├── .claude/
│   ├── commands/           # Slash commands
│   ├── agents/             # Agent definitions
│   ├── workers/            # Fresh bounded worker contracts
│   ├── skills/             # Documentation templates
│   ├── governance/         # Production quality rules and checklists
│   ├── config/             # Refinement and audit policies
│   └── scripts/            # Deterministic validators
├── plugins/
│   └── scope/
│       ├── commands/       # Codex command playbooks
│       ├── agents/         # Codex role instructions
│       ├── workers/        # Fresh bounded worker contracts
│       ├── skills/         # Shared templates + Codex workflow skill
│       ├── governance/     # Production quality rules and checklists
│       ├── config/         # Refinement and audit policies
│       ├── docs/           # Scope reference docs for Codex
│       ├── scripts/        # Helper scripts such as scope-command
│       ├── .codex-plugin/  # Codex plugin metadata
│       └── .mcp.json       # MCP server configuration
├── .scope/
│   └── config.yaml         # Project configuration
├── docs/
│   ├── product/            # Product docs (strategy, definition, decisions)
│   ├── architecture/       # Technical docs (Arc42 sections 01-13)
│   ├── epics/{epic-id}/    # Per-epic contract, design, story plans, and evidence
│   └── releases/           # Release documentation
├── wip/
│   └── {epic-id}/          # Git worktree per epic (implementation happens here)
└── src/                    # Your application code
```

## Key Concepts

**Native contracts** — `/epic_refine` uses the project-appropriate contract:
OpenAPI, JSON Schema, SQL, language interfaces, configuration schemas, or other
verifiable boundaries. Python Protocols are used only when they fit the design.

**Implementation boundaries** — Each `file-plan-story-*.yaml` records binding
contracts, integration touchpoints, forbidden changes, and proof obligations.
Candidate files remain advisory.

**Bounded audit** — `/audit_epic` runs one full, read-only audit. Implementation
remediates findings, then audit performs one targeted verification. Additional
full audits require a material boundary change or explicit authorization. One
failed-review recovery is allowed when every gate passed but no independent
review completed; the failed attempt and its raw outputs remain preserved.

**Conversational orchestration** — The public command is the only process that
talks to you. It derives progress from durable artifacts and validators, starts
one write-capable worker at a time, and restarts stale work after a material
decision change. `session-handoff` remains the recovery path for unusually long
conversations.

**Git worktrees** — After the refinement handoff is approved, `/implement`
automatically checkpoints only its resolved epic and native-contract paths with
the fixed `refine({epic-id}): implementation handoff` label. It then creates
`wip/{epic-id}` on branch `epic/{epic-id}` without another confirmation prompt.
An exact, validated dependency commit may also be integrated automatically with
the fixed `merge({epic-id}): integrate {dependency-epic-id} implementation
baseline` label when the worktree is clean and the merge is conflict-free.
Implementation stays isolated there until closure. After audit PASS, the runner records observed implementation evidence and
`/implement` seals the delivery summary and exact workspace. `/wrap_epic` then
shows the staged tree and current main HEAD for one approval before committing
and merging that exact closure. It does not infer unrelated dirty files or
rewrite documentation after audit.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- Python 3 with the compatible dependency ranges in `requirements.txt`
- Git

`SCOPE_PYTHON` selects Scope's tooling interpreter; do not point it at a
project test environment unless that environment also contains Scope's Python
requirements. Refinement and audit preflight this interpreter and the Claude
CLI before creating a reviewer attempt.

Claude workers and independent reviewers invoke the authenticated Claude CLI
directly in headless mode. Reviewer prompts use stdin and review Markdown uses
stdout; no PTY wrapper is involved. Windows CI validates installed assets and
one Codex supervisor-recovery path, but this local macOS validation did not
produce a Windows execution receipt. Windows is not a validated Claude worker
or reviewer runtime.

## Repository validation

Install `requirements-dev.txt` and run `./scripts/validate-pr-checks.sh` before
committing. The gate runs the unit suite with subprocess-aware line coverage
over all Python files in `src_shared/scripts`, targeting 90% and enforcing an
85% minimum. It clears prior coverage data on each run and stores new coverage
data under `tmp_debug/`. No production files are excluded to meet
the threshold.

## License

MIT

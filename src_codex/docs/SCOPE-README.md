<p align="center">
  <img src="assets/scope-banner.png" alt="SCOPE Banner" />
</p>

# SCOPE

**Simple Claude/Codex Orchestrator for Product Engineering**

SCOPE turns Claude Code or OpenAI Codex into a structured product engineering environment. It gives you commands that take a product from PRD to implemented, tested, and audited epics, with user authority at the product contract, material decisions, and final handoff.

Claude: idea → /prd_create → /prd_refine → /prd_breakdown → /epic_refine → /implement → /wrap_epic

Codex: idea → scope:prd_create → scope:prd_refine → scope:prd_breakdown → scope:epic_refine → scope:implement → scope:wrap_epic

**Already have code but no docs?** Use `/re_documentation` to reverse engineer the product and architecture documentation from your existing codebase.

Scope includes deterministic refinement/audit validators and a bounded worker
runner. MCP servers remain optional.

---

## What It Does

### Forward Engineering (PRD to Code)

- **`/prd_create`** — Interview the user to create a lightweight first-pass PRD before refinement
- **`/prd_refine`** — Interactively refine a product requirements document using a checklist-driven approach
- **`/prd_breakdown`** — Break the PRD into implementable epics with architecture and dependency analysis
- **`/epic_refine`** — Build a risk-appropriate product, architecture, native-contract, story, and proof handoff through a product-contract gate, independent review, and final approval
- **`/implement`** — Deliver validated stories sequentially, including tests, runtime proof, and audit remediation
- **`/audit_epic`** — Perform a read-only evidence audit and one targeted verification after remediation
- **`/wrap_epic`** — Verify the sealed delivery, archive it, and commit/merge the exact approved staged tree
- **`/sync_product`** — Update product documentation when implementation reveals scope changes

### Reverse Engineering (Code to Docs)

- **`/re_documentation`** — Reverse engineer product and architecture documentation from an existing codebase. Two agents scan your code, interview you about decisions and rationale, then generate 24 documentation files (9 product + 15 architecture)

### Session Continuity

- **`/session-handoff`** / **`scope:session-handoff`** — Create an ephemeral `session-handoff.md` at the active worktree or project root when a long session has become inefficient. The file captures enough durable context for a fresh agent to assess the state and recommend the next course of action without treating unconfirmed next steps as instructions. The file is overwritten on each run and should not be tracked by git.

Commands stop at their documented authority boundaries. Nothing is merged without your sign-off.

## How It Works

SCOPE keeps the public command conversational while fresh controlled workers
perform bounded repository phases, stories, corrections, and audit synthesis.
The user never needs to switch to a worker thread.

- **Public commands** (`.claude/commands/` or `plugins/scope/commands/`) define multi-phase workflows with approval gates
- **Agent definitions** (`.claude/agents/` or `plugins/scope/agents/`) provide platform-specific role guidance
- **Worker contracts** (`.claude/workers/` or `plugins/scope/workers/`) define isolated worker authority and outputs
- **Skills** (`.claude/skills/` or `plugins/scope/skills/`) provide documentation templates (Arc42+C4 for technical, Atlassian Blueprint for product)
- **The lean run record and result hashes** enforce story dependencies and sequential execution
- **Git worktrees** isolate implementation from the main branch

Implementation resolves `plugins/scope/` before entering `./wip/{epic-id}` and
retains that absolute installation path because ignored plugin files are not
copied into linked worktrees. A new invocation already inside a worktree must
have its own install; it must not silently select an unrelated checkout.

The refinement/audit validators and worker runner require Python 3 and the
packages in `requirements.txt`. Scope has no persistent service or database.

CodeGraph 1.5+ is an optional CLI-only accelerator. Add `.codegraph/` to the
target repository's `.gitignore`; Scope's runners then initialize/synchronize
the active repository/worktree index, provide query-only access to workers and
reviewers, and record one ready/degraded/unavailable run-level state. Fallback to
direct reads and `rg` never weakens proof or validation.

## Installation

```bash
git clone https://github.com/Patrick-Loves-Espresso/scope.git
cd scope
python3 -m pip install -r requirements.txt
```

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

The script copies commands, workers, agents, skills, scripts, policies, and
schemas into both `.claude/` and `plugins/scope/`. For project installs, it also
creates `.scope/config.yaml` — edit it to set your project name and tracking
preferences.

## Quick Start

**Starting from an idea or a PRD:**

Use the shown `/command` form in Claude Code. In Codex, invoke the same workflow
as `scope:command` (for example, `scope:epic_refine EPIC-001`).

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
│   ├── workers/            # Fresh bounded worker contracts
│   ├── agents/             # Agent definitions
│   └── skills/             # Documentation templates
├── plugins/scope/
│   ├── commands/           # Codex command playbooks
│   ├── workers/            # Fresh bounded worker contracts
│   ├── agents/             # Codex role guidance
│   └── skills/             # Documentation templates
├── .scope/
│   └── config.yaml         # Project configuration
├── docs/
│   ├── product/            # Product docs (strategy, definition, decisions)
│   ├── architecture/       # Technical docs (Arc42 sections 01-13)
│   ├── epics/{epic-id}/    # Per-epic contract, design, plans, and evidence
│   └── releases/           # Release documentation
├── ./wip/
│   └── {epic-id}/          # Git worktree per epic (implementation happens here)
└── src/                    # Your application code
```

## Key Concepts

**Native contracts** — `/epic_refine` uses project-appropriate contracts such
as OpenAPI, JSON Schema, SQL, language interfaces, and configuration schemas.
Python Protocols are optional.

**Implementation boundaries** — Each `file-plan-story-*.yaml` records binding
contracts, touchpoints, forbidden changes, and proof obligations. Candidate
files remain advisory.

**Bounded audit** — `/audit_epic` runs one full read-only audit. Implementation
remediates findings, then audit performs one targeted verification.

**Git worktrees** — Implementation happens in `./wip/{epic-id}` on branch
`epic/{epic-id}`. After audit PASS, implementation records runner-observed
evidence and seals the exact delivery. `/wrap_epic` presents one approval bound
to the staged tree and current main HEAD, then commits and merges only that
closure.

## Requirements

- Claude Code or OpenAI Codex CLI
- Python 3 with the compatible dependency ranges in `requirements.txt`
- Git

## License

MIT

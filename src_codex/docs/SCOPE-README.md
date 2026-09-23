# SCOPE

**Simple Claude/Codex Orchestrator for Product Engineering**

SCOPE makes Claude Code or OpenAI Codex work like a disciplined product team
that you direct rather than babysit: aligned with your intent, grounded in
durable documentation, and independently verified before anything is merged.

Claude: idea → /prd_create → /prd_refine → /prd_breakdown → /epic_refine → /implement → /wrap_epic

Codex: idea → scope:prd_create → scope:prd_refine → scope:prd_breakdown → scope:epic_refine → scope:implement → scope:wrap_epic

**Already have code but no docs?** Use `/re_documentation` to reverse engineer the product and architecture documentation from your existing codebase.

You stop the work twice per epic: once to approve the acceptance criteria,
once to approve the exact commit that is merged. In between, Scope asks only
for genuine product decisions.

**NOTE:** for Codex, replace "/" with "run scope:"

---

## What It Does

### Forward Engineering (PRD to Code)

- **`/prd_create`** — Interview the user to create a lightweight first-pass PRD before refinement
- **`/prd_refine`** — Interactively refine a product requirements document using a checklist-driven approach
- **`/prd_breakdown`** — Break the PRD into implementable epics with architecture and dependency analysis
- **`/epic_refine`** — Draft observable acceptance criteria with a size estimate and a "not building" list for your approval, write a living plan, and settle one review by Claude and Codex
- **`/implement`** — Build the plan in a git worktree with one implementer that commits per story, runner-executed tests, a size check after each story, finalized docs, and the independent audit
- **`/audit_epic`** — Two independent providers audit the branch, including docs against code; findings are fixed and verified, or rejected and adjudicated
- **`/wrap_epic`** — Show the exact commit for your approval and merge exactly that commit, with the approval recorded in the merge commit
- **`/sync_product`** — Update product documentation when implementation reveals scope changes

### Reverse Engineering (Code to Docs)

- **`/re_documentation`** — Reverse engineer product and architecture documentation from an existing codebase. Two agents scan your code, interview you about decisions and rationale, then generate 24 documentation files (9 product + 15 architecture)

### Knowledge capture

- **`/decision`** — Record an architecture (ADR) or product (PDR) decision with its rationale
- **`/lesson`** — Capture a lesson learned with a detection rule
- **`/audit_decisions`** — Find undocumented decisions in the code

### Session Continuity

- **`/session-handoff`** / **`scope:session-handoff`** — Create an ephemeral `session-handoff.md` at the active worktree or project root when a long session has become inefficient. The file captures enough durable context for a fresh agent to assess the state and recommend the next course of action without treating unconfirmed next steps as instructions. The file is overwritten on each run and should not be tracked by git.

## How It Works

The command you run is the only process that talks to you. It orchestrates
fresh provider processes through Scope's launcher:

- a **planner** writes the acceptance criteria and the plan;
- an **implementer** writes the code, tests, and docs, and commits per story;
- **reviewers** (Claude and Codex, read-only) review the plan and audit the
  result; Muse Spark replaces an unavailable reviewer.

Workers run on the provider that hosts the command. Scope appends the
simplicity-and-size rules to every worker and reviewer prompt: build the
minimum that satisfies the criteria, story complexity at most 7/10, modules at
most 450 code lines. Tests are executed by Scope's runner, never reported by a
model.

Each epic keeps six files: `details.md`, `acceptance-criteria.md`,
`approvals.yaml`, `plan.md`, `review.md`, and `verification.yaml`. Git records
everything else. Raw logs go to the git-ignored `tmp_debug/scope-runs/`.

CodeGraph is optional. When its CLI is installed, the lifecycle commands sync
the index once per command and workers and reviewers query it read-only. Add
`.codegraph/` to the target repository's `.gitignore`.

See [the architecture](scope-architecture.md) for the full lifecycle.

## Installation

Clone Scope first:

```bash
git clone https://github.com/Patrick-Loves-Espresso/scope.git
cd scope
```

Install the Python dependencies with the interpreter Scope should use:

```bash
python3 -m pip install -r requirements.txt
```

On Windows:

```bat
py -3 -m pip install -r requirements.txt
```

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

Both installers copy the same commands, worker prompts, agents, skills,
governance, scripts, the policy file, and the Codex plugin assets, and remove
files retired by earlier Scope versions. Project installs also create
`.scope/config.yaml` when it does not already exist. The default configuration
uses local Markdown documentation in `./docs` and local YAML tracking in
`./tracking`; Jira and Confluence are not required.

**Upgrading from Scope 1.x.** Reinstall; the installers remove the old
lifecycle files. An epic in progress on the old version is re-planned: run
`/epic_refine` on it, which removes its old-format artifacts, re-drafts the
criteria for your approval, and writes a new plan. What happens to code
already produced under the old version is your decision.

## Quick Start

**Starting from an idea or a PRD:**

```
1. /prd_create              → Create a first-pass PRD if you do not have one
2. /prd_refine              → Refine it interactively
3. /prd_breakdown           → Get epics with dependencies
4. /epic_refine EPIC-001    → Approve the acceptance criteria; Scope plans and reviews
5. /implement EPIC-001      → Build, verify, and audit it in a worktree
6. /wrap_epic EPIC-001      → Approve the exact commit and merge it
```

**Starting from existing code:**

```
1. /re_documentation        → Reverse engineer product + architecture docs
2. /prd_refine              → Refine/extend the PRD for new features
3. /prd_breakdown           → Break into epics
4. Continue as above
```

## Project Structure (Target Project)

```
your-project/
├── .claude/                # Claude installation
│   ├── commands/           # Slash commands
│   ├── workers/            # Planner, implementer, reviewer prompts
│   ├── agents/             # Standalone roles
│   ├── skills/             # Documentation templates and tracking
│   ├── governance/         # Simplicity and size rules, developer checklist
│   ├── config/             # scope-policy.yaml (the only policy file)
│   └── scripts/            # Launcher, verification runner, checker
├── plugins/
│   └── scope/              # Codex plugin with the same layout plus docs/ and .codex-plugin/
├── .scope/
│   └── config.yaml         # Project configuration
├── docs/
│   ├── product/            # Product docs (strategy, definition, decisions)
│   ├── architecture/       # Current-state technical docs (Arc42 01-13, ADRs, specs)
│   ├── epics/{epic}/       # details, acceptance criteria, approvals, plan, review, verification
│   ├── epics/_implemented/ # Epic folders after implementation
│   └── lessons-learned/
├── wip/
│   └── {epic-id}/          # Git worktree per epic (branch epic/{epic-id})
└── tmp_debug/scope-runs/   # Git-ignored logs of workers, reviewers, and test runs
```

## Key Concepts

**Two gates.** Gate 1: you approve the acceptance criteria, their size
estimate, and what is deliberately not built; the approval is recorded by the
content's git hash and commit, and any later change comes back to you as a
diff with its size delta. Gate 2: you approve the exact branch commit, after
seeing the diffstat against the estimate, the audit verdict, the verification
summary, the doc changes, and the plan's decision log.

**Proportionality.** Reviewers flag speculative generality and scope beyond
the criteria. After each story the runner compares production code lines with
the plan's estimate; growth beyond 1.5× is classified by an independent check,
and only scope growth comes to you.

**Independent review with adjudication.** Findings are fixed or rejected with a
reason. The reviewer who raised a finding verifies the fix; a rejected finding
goes back to that reviewer and, if maintained, to the other provider, which
examines it from scratch. A finding that survives two fixes gets an
independent diagnosis. No finding is accepted because a budget ran out, and a
one-provider audit never passes.

**Runner verification.** The plan declares its validation commands; Scope runs
them on a committed state, reads standard JUnit XML (pytest `--junitxml`,
gotestsum, a Jest reporter), checks that the tests mapped to each criterion
ran and passed, and records the result in `verification.yaml`.

**Durable documentation.** Each epic declares its doc obligations. Before the
audit, the implementer finalizes ADRs, arc42 sections, and specs, and archives
the epic folder on the branch, so the audit checks the docs against the code
and the merge adds nothing unreviewed. `docs/architecture/` holds the current
state only.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and/or the
  [Codex](https://developers.openai.com/codex) CLI, authenticated; both are
  needed for cross-provider review
- [OpenCode](https://opencode.ai) with Muse Spark for the fallback reviewer
  (optional; without it, an unavailable provider leaves the audit incomplete)
- Python 3 with the packages in `requirements.txt`
- Git

## Repository validation

Install `requirements-dev.txt`, activate the hooks once with
`./scripts/setup-git-hooks.sh`, and run `./scripts/validate-pr-checks.sh`
before committing. The gate checks whitespace, mirrored Claude/Codex files,
the install smoke test (including removal of retired files), the complexity
budgets (lifecycle Python ≤ 3,000 lines, modules ≤ 450 code lines, command
prompts ≤ 150 lines, one policy file), and runs the test suite with
subprocess-aware coverage (minimum 90%).

## License

MIT

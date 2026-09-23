# Scope for Codex

Scope for Codex adapts the Scope product engineering workflow from Claude Code to Codex.

The original Scope model uses Claude slash commands, Claude agents, Claude skills, and task tools. Codex does not load those exact primitives the same way, so this plugin keeps the Scope artifacts as command playbooks and role instructions, then adds Codex-facing guidance.

## Invocation

Ask Codex to run a Scope command by name:

```text
scope:prd_refine docs/prd.md
scope:prd_breakdown docs/prd.md
scope:epic_refine E1
scope:implement E1
scope:audit_epic E1
scope:wrap_epic E1
scope:re_documentation
scope:sync_product
```

Codex should read the matching file in `commands/`, load any referenced role
file in `agents/`, and preserve its product-contract, material-decision, and
final-handoff authority boundaries.

An implementation command resolves `plugins/scope/` before creating or resuming
`wip/{epic-id}` and retains that absolute installation path. Git does not copy
ignored plugin files into a linked worktree. A new command started directly in
a worktree must have its own installation rather than silently selecting an
unrelated checkout.

Scope's lifecycle scripts (launcher, verification runner, checker) require
Python 3 and the packages installed with:

```bash
python3 -m pip install -r plugins/scope/requirements.txt
```

CodeGraph is optional and CLI-only. When the `codegraph` CLI is installed, the
lifecycle commands run one `codegraph sync` (or `init` when `.codegraph/` is
missing and git-ignored) in the checkout or worktree they work in; workers and
reviewers only query it.

The launcher invokes the provider CLIs directly in headless mode: Codex
(`codex exec`), Claude (`claude --print --safe-mode`), and, as the independent
fallback reviewer, OpenCode with Muse Spark. Windows CI validates the installed
assets and the platform-independent unit tests; the lifecycle tests with fake
provider CLIs run on macOS and Linux.

## Porting Model

- `commands/` contains the public conversational orchestrators.
- `workers/` contains the planner, implementer, and reviewer prompts launched in fresh provider processes by `scripts/scope_launch.py`.
- `agents/` contains standalone Scope role definitions for work outside the lifecycle commands.
- `skills/` contains reusable documentation and tracking skills.
- `governance/` contains the simplicity-and-size rules appended to every worker and reviewer prompt, and the developer checklist.
- `config/scope-policy.yaml` is Scope's single policy file (model routing, reviewers, fallback, timeouts, size limits).
- `docs/` contains Scope reference documentation.

## Differences From Claude Code

- Claude slash commands are not native Codex commands. They are invoked by natural language, usually `scope:<command>`.
- The lifecycle commands launch workers and reviewers through Scope's launcher rather than native subagents, so model, effort, and sandbox are set by the policy file.
- Claude task tools are replaced by `.scope/` tracking files and Codex task plans where practical.
- MCP servers must be configured in Codex separately. Scope deliberately uses
  the CodeGraph CLI rather than a CodeGraph MCP; the plugin's `.mcp.json`
  remains available for unrelated project-specific MCP configuration.

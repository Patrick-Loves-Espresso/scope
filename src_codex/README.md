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

The refinement/audit validators and bounded worker runner require Python 3 and
the packages installed with:

```bash
python3 -m pip install -r plugins/scope/requirements.txt
```

CodeGraph 1.5+ is optional and CLI-only. When `.codegraph/` is Git-ignored,
Scope prepares the active repository or worktree index once per command run and
incrementally refreshes it between implementation write jobs. Workers receive
query-only guidance and one compact run-level status. Missing or degraded
CodeGraph falls back to direct repository inspection without reducing proof.

The shared runners invoke the authenticated Claude CLI directly in headless
mode for workers and independent reviewers. Reviewer prompts use stdin and
review Markdown uses stdout; no PTY wrapper is involved. Windows CI validates
installed assets and one Codex supervisor-recovery path; this local macOS
validation did not produce a Windows execution receipt and does not cover
Claude worker or reviewer execution.

## Porting Model

- `commands/` contains the public conversational orchestrators and the thin deterministic wrap playbook.
- `workers/` contains the bounded roles launched in fresh controlled provider processes by `epic_refine`, `implement`, and `audit_epic`.
- `agents/` contains standalone Scope role definitions used by workflows that have not moved to bounded workers.
- `skills/` contains reusable documentation and tracking skills.
- `governance/` contains production quality rules and checklists.
- `docs/` contains Scope reference documentation.

## Differences From Claude Code

- Claude slash commands are not native Codex commands. They are invoked by natural language, usually `scope:<command>`.
- The three worker-backed workflows use Scope's shared runner rather than native subagent inheritance, so model, effort, access, lifecycle, and structured results are deterministic.
- Claude task tools are replaced by `.scope/` tracking files and Codex task plans where practical.
- MCP servers must be configured in Codex separately. Scope deliberately uses
  the CodeGraph CLI rather than a CodeGraph MCP; the plugin's `.mcp.json`
  remains available for unrelated project-specific MCP configuration.

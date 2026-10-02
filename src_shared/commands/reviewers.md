---
name: reviewers
description: Show or change the project's reviewers and adjudicator for /epic_refine and /audit_epic, with a preflight of each.
args: "[the change, in your words]"
---

# reviewers

Installed as `/scope_reviewers` in Claude Code and `scope:reviewers` in Codex.
Only this command changes the project's reviewers. They live in
`.scope/reviewers.yaml`, which reinstalling Scope never touches. A lifecycle
command can change them for one epic only.

## Setup

```bash
ROOT="$(git rev-parse --show-toplevel)"
SCOPE_ROOT="$ROOT/.claude"                         # Claude Code
# SCOPE_ROOT="$ROOT/plugins/scope"                 # Codex
S="$SCOPE_ROOT/scripts"; PY=python3
$PY "$S/scope_reviewers.py" show --preflight
```

With no change requested, show the result per workflow (`refine` and
`audit`): each reviewer with its CLI, model, effort, mandatory or optional,
and preflight status; the adjudicator; any problems and warnings. Then stop.

## What a reviewer is

- A CLI (`claude`, `codex`, or `opencode`), the exact model string that CLI
  takes, and an effort. OpenCode models are `provider/model`, exactly as
  OpenCode's `--model` option takes them (`zai/glm-5.3`). Never guess or
  complete a model name or a CLI: when the user's words are not exact, ask for
  the exact string.
- Every reviewer is mandatory unless the user says optional. Mandatory
  reviewers must complete a full review for it to count, and a failed one is
  retried once. Optional reviewers are not retried and never block; their
  blocking and major findings must still be resolved, their minor ones never
  block.
- Each workflow has one adjudicator. It settles every rejected finding that
  its raiser maintains. Scope assumes no model: when one is missing, ask the
  user for its CLI, exact model, and effort.
- `refine` serves `/epic_refine`; `audit` serves `/audit_epic` and the checks
  in `/implement`. Change both unless the user names one: architecture,
  epic_refine, refine, or planning means `refine`; implementation, implement,
  qa, audit, or audit_epic means `audit`.
- Effort `max` only when the user explicitly asks for it; then add
  `--allow-max`.

## Change

Turn the user's words into one command. Removals and replacements name a
reviewer, its exact model, or its CLI when only one reviewer uses that CLI:

```bash
$PY "$S/scope_reviewers.py" set --workflow refine|audit|both \
  [--add CLI MODEL EFFORT] [--add-optional CLI MODEL EFFORT] [--remove NAME] \
  [--replace NAME CLI MODEL EFFORT] [--adjudicator CLI MODEL EFFORT] [--allow-max]
```

Show the user the result as above, every warning in plain words (an
unavailable reviewer, a single reviewer, an adjudicator with the same model
and effort as a reviewer, which then judges findings its own model raised),
and the file that changed. If the project tracks `.scope/reviewers.yaml`,
offer to commit it. A refused change prints why; fix it with the user.

## Final response

The reviewers and adjudicator per workflow, with their preflight status, and
any warning still standing.

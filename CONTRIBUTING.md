# Contributing to Scope

Thanks for helping improve Scope. This project accepts contributions through pull requests.

## Contribution Flow

For public contributions, use the standard fork workflow:

1. Fork the repository.
2. Activate the repository checks with `./scripts/setup-git-hooks.sh`.
3. Create a branch in your fork.
4. Commit your change and push the branch. The pre-push hook runs the same validation as CI.
5. Open a pull request against this repository.

Branch names should be short and descriptive:

- `fix/worktree-awareness-wrap-epic`
- `docs/contributing-guide`
- `feature/audit-validation`

## Pull Request Expectations

Every PR should explain:

- The problem being fixed.
- The root cause, when applicable.
- The approach used in the fix.
- How the change was verified.

For bug fixes, include the behavior before and after the fix. If the bug affects a Scope command, include the command name and the scenario that reproduced it.

## Scope Source Layout

Scope has shared and agent-specific source trees:

- `src_shared/`: files installed for both Claude and Codex.
- `src_claude/`: Claude-specific overrides.
- `src_codex/`: Codex-specific overrides and plugin files.

When changing a command, agent, skill, script, or template, check whether the change belongs in `src_shared/` or whether both `src_claude/` and `src_codex/` need equivalent updates.

If a file under `src_claude/` changes and the matching file under `src_codex/` exists, update both files in the same PR. The same rule applies in the other direction. This keeps Claude and Codex behavior aligned while still allowing truly platform-specific files that have no counterpart.

Do not edit generated install output as the source of truth. Update the files under `src_shared/`, `src_claude/`, or `src_codex/`.

## Verification

Install the Python validation dependencies once:

```bash
python3 -m pip install -r requirements-dev.txt
```

Before opening a PR, run:

```bash
./scripts/validate-pr-checks.sh
```

This runs the same checks as GitHub Actions, including whitespace checks for
staged and untracked files, mirrored Claude/Codex file changes, generated-file
rejection, the install smoke test (including removal of retired files), the
complexity budgets (lifecycle Python ≤ 3,000 lines, modules ≤ 450 code lines,
lifecycle command prompts ≤ 150 lines, one policy file), and the test suite
with its 90% coverage threshold.

For quick manual install checks, you can also verify that installation propagates
the files correctly:

```bash
tmpdir=$(mktemp -d)
./install.sh "$tmpdir"
find "$tmpdir/.claude" "$tmpdir/plugins/scope" -maxdepth 3 -type f | sort
rm -rf "$tmpdir"
```

If your change affects a command workflow, include the relevant installed path in the PR description, for example:

- Claude command: `.claude/commands/wrap_epic.md`
- Codex command: `plugins/scope/commands/wrap_epic.md`

## Generated and Local Files

Do not commit local machine files, generated caches, temporary install directories, or editor artifacts.

Examples to exclude:

- `.DS_Store`
- `__pycache__/`
- `.pytest_cache/`
- temporary `mktemp` install directories
- local environment files with secrets

## Documentation and Command Changes

Command changes should be explicit about paths and working directories. If a command operates inside an epic worktree, document whether reads, writes, and git commands run from the main repository root or the worktree.

For CodeGraph support, use the CodeGraph CLI when it is installed, read-only for workers and reviewers; Scope does not use a CodeGraph MCP.

Every new rule should state the model weakness it assumes and how to test whether it is still needed. Record incidents in `docs/lessons-learned/` first; add code only when a lesson recurs and no simpler fix exists.

## Review

Maintainers may ask for changes before merging. Keep PRs focused when possible; small, well-scoped fixes are easier to review and merge.

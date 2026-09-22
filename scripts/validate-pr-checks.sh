#!/usr/bin/env bash

set -euo pipefail

section() {
  printf '\n==> %s\n' "$1"
}

fail() {
  echo "Validation failed: $1" >&2
  exit 1
}

diff_range() {
  if [[ -n "${1:-}" ]]; then
    printf '%s\n' "$1"
  elif [[ -n "${GITHUB_BASE_REF:-}" ]]; then
    printf 'origin/%s...HEAD\n' "$GITHUB_BASE_REF"
  elif git rev-parse --verify HEAD~1 >/dev/null 2>&1; then
    printf 'HEAD~1...HEAD\n'
  fi
}

changed_files() {
  local range="$1"

  {
    if [[ -n "$range" ]]; then
      git diff --name-only "$range"
    fi
    git diff --name-only
    git diff --cached --name-only
    git ls-files --others --exclude-standard
  } | sed '/^$/d' | sort -u
}

is_text_file() {
  local file="$1"
  local mime

  mime="$(file -b --mime-type "$file" 2>/dev/null || true)"
  case "$mime" in
    text/*|application/json|application/xml|application/x-yaml|application/yaml|application/x-shellscript)
      return 0
      ;;
  esac

  case "$file" in
    *.md|*.yaml|*.yml|*.json|*.toml|*.sh|*.bat|*.cmd|*.ps1|*.py|*.txt|*.sql|*.html|*.css|*.js|*.ts|*.tsx)
      return 0
      ;;
  esac

  return 1
}

check_whitespace() {
  local range="$1"
  local failed=0
  local file
  local output

  section "Check whitespace"

  if [[ -n "$range" ]]; then
    git diff --check "$range" || failed=1
  fi

  git diff --check || failed=1
  git diff --cached --check || failed=1

  while IFS= read -r file; do
    [[ -f "$file" ]] || continue
    is_text_file "$file" || continue

    output="$(git diff --check --no-index /dev/null "$file" 2>/dev/null || true)"
    if [[ -n "$output" ]]; then
      echo "$output"
      failed=1
    fi
  done < <(git ls-files --others --exclude-standard)

  [[ "$failed" -eq 0 ]] || fail "whitespace check failed"
}

check_generated_files() {
  local failed=0
  local file

  section "Reject local/generated files"

  while IFS= read -r file; do
    case "$file" in
      .DS_Store|*/.DS_Store|*.pyc|*/__pycache__/*|*/.pytest_cache/*|.claude/*|plugins/*)
        echo "Forbidden local/generated file: $file"
        failed=1
        ;;
    esac
  done < <({
    git ls-files
    git ls-files --others --exclude-standard
  } | sort -u)

  [[ "$failed" -eq 0 ]] || fail "forbidden local/generated files found"
}

check_mirrors() {
  local range="$1"
  local missing=""
  local changed_file_list
  local file
  local counterpart

  section "Check mirrored Claude/Codex changes"

  changed_file_list="$(mktemp)"
  changed_files "$range" > "$changed_file_list"

  while IFS= read -r file; do
    [[ -n "$file" ]] || continue

    case "$file" in
      src_claude/*)
        counterpart="src_codex/${file#src_claude/}"
        ;;
      src_codex/*)
        counterpart="src_claude/${file#src_codex/}"
        ;;
      *)
        continue
        ;;
    esac

    if [[ -f "$counterpart" ]] && ! grep -F -x -q "$counterpart" "$changed_file_list"; then
      missing="${missing}\n${file} changed, but matching ${counterpart} was not changed."
    fi
  done < "$changed_file_list"

  rm -f "$changed_file_list"

  if [[ -n "$missing" ]]; then
    echo "Claude/Codex mirrored files must be updated together when a counterpart exists."
    printf '%b\n' "$missing"
    fail "mirrored file check failed"
  fi
}

check_install() {
  local tmpdir
  local obsolete

  section "Install smoke test"

  tmpdir="$(mktemp -d)"
  mkdir -p \
    "$tmpdir/.claude/scripts/__pycache__" \
    "$tmpdir/.claude/scripts/.pytest_cache" \
    "$tmpdir/.claude/config" \
    "$tmpdir/.claude/commands" \
    "$tmpdir/.claude/workers" \
    "$tmpdir/.claude/governance" \
    "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic" \
    "$tmpdir/plugins/scope/scripts/__pycache__" \
    "$tmpdir/plugins/scope/scripts/.pytest_cache" \
    "$tmpdir/plugins/scope/config" \
    "$tmpdir/plugins/scope/commands" \
    "$tmpdir/plugins/scope/docs" \
    "$tmpdir/plugins/scope/workers" \
    "$tmpdir/plugins/scope/governance" \
    "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic"
  touch \
    "$tmpdir/.claude/workers/audit-worker.md" \
    "$tmpdir/.claude/scripts/.DS_Store" \
    "$tmpdir/.claude/scripts/scope-reviewer-claude-pexpect.py" \
    "$tmpdir/.claude/scripts/scope-proof-preflight.py" \
    "$tmpdir/.claude/config/worker-runtime-policy.yaml" \
    "$tmpdir/.claude/commands/implement_tdd.md" \
    "$tmpdir/.claude/governance/agent-lifecycle.md" \
    "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml" \
    "$tmpdir/.claude/scripts/__pycache__/stale.pyc" \
    "$tmpdir/.claude/scripts/.pytest_cache/stale" \
    "$tmpdir/plugins/scope/workers/audit-worker.md" \
    "$tmpdir/plugins/scope/scripts/.DS_Store" \
    "$tmpdir/plugins/scope/scripts/scope-reviewer-claude-pexpect.py" \
    "$tmpdir/plugins/scope/scripts/scope-proof-preflight.py" \
    "$tmpdir/plugins/scope/config/worker-runtime-policy.yaml" \
    "$tmpdir/plugins/scope/commands/implement_tdd.md" \
    "$tmpdir/plugins/scope/docs/epic-workflow.md" \
    "$tmpdir/plugins/scope/governance/agent-lifecycle.md" \
    "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml" \
    "$tmpdir/plugins/scope/scripts/__pycache__/stale.pyc" \
    "$tmpdir/plugins/scope/scripts/.pytest_cache/stale"
  ./install.sh "$tmpdir" >/tmp/scope-install-smoke.log

  test -f "$tmpdir/.claude/commands/wrap_epic.md"
  cmp -s src_shared/commands/wrap_epic.md "$tmpdir/.claude/commands/wrap_epic.md"
  test -f "$tmpdir/.claude/commands/implement.md"
  test -f "$tmpdir/.claude/commands/audit_epic.md"
  test -f "$tmpdir/.claude/commands/epic_refine/reviewer-refinement.md"
  test -f "$tmpdir/.claude/commands/audit_epic/reviewer-audit.md"
  test -f "$tmpdir/.claude/agents/developer.md"
  test -f "$tmpdir/.claude/config/refinement-policy.yaml"
  test -f "$tmpdir/.claude/config/audit-policy.yaml"
  test -f "$tmpdir/.claude/config/reviewer-policy.yaml"
  test -f "$tmpdir/.claude/config/worker-policy.yaml"
  test -f "$tmpdir/.claude/config/codegraph-policy.yaml"
  test -f "$tmpdir/.claude/config/worker-job.schema.json"
  test -f "$tmpdir/.claude/config/worker-result.schema.json"
  test -f "$tmpdir/.claude/config/wrap-policy.yaml"
  test -f "$tmpdir/.claude/scripts/validate-refinement.py"
  test -f "$tmpdir/.claude/scripts/audit-artifacts.py"
  test ! -e "$tmpdir/.claude/scripts/scope-reviewer-claude-pexpect.py"
  test -x "$tmpdir/.claude/scripts/scope-reviewer.py"
  test -x "$tmpdir/.claude/scripts/scope-worker.py"
  test -x "$tmpdir/.claude/scripts/scope-dependency-merge.py"
  test -x "$tmpdir/.claude/scripts/scope-wrap-finalize.py"
  test -f "$tmpdir/.claude/scripts/scope_git.py"
  test -f "$tmpdir/.claude/scripts/scope_fingerprint.py"
  test -f "$tmpdir/.claude/scripts/scope_codegraph.py"
  test -f "$tmpdir/.claude/workers/refinement-worker.md"
  test -f "$tmpdir/.claude/workers/implementation-worker.md"
  test -f "$tmpdir/.claude/workers/diagnostic-worker.md"
  test -f "$tmpdir/.claude/requirements.txt"

  test -f "$tmpdir/plugins/scope/commands/wrap_epic.md"
  cmp -s src_shared/commands/wrap_epic.md "$tmpdir/plugins/scope/commands/wrap_epic.md"
  test -f "$tmpdir/plugins/scope/commands/implement.md"
  test -f "$tmpdir/plugins/scope/commands/audit_epic.md"
  test -f "$tmpdir/plugins/scope/commands/epic_refine/reviewer-refinement.md"
  test -f "$tmpdir/plugins/scope/commands/audit_epic/reviewer-audit.md"
  test -f "$tmpdir/plugins/scope/agents/developer.md"
  test -f "$tmpdir/plugins/scope/config/refinement-policy.yaml"
  test -f "$tmpdir/plugins/scope/config/audit-policy.yaml"
  test -f "$tmpdir/plugins/scope/config/reviewer-policy.yaml"
  test -f "$tmpdir/plugins/scope/config/worker-policy.yaml"
  test -f "$tmpdir/plugins/scope/config/codegraph-policy.yaml"
  test -f "$tmpdir/plugins/scope/config/worker-job.schema.json"
  test -f "$tmpdir/plugins/scope/config/worker-result.schema.json"
  test -f "$tmpdir/plugins/scope/config/wrap-policy.yaml"
  test -f "$tmpdir/plugins/scope/scripts/validate-refinement.py"
  test -f "$tmpdir/plugins/scope/scripts/audit-artifacts.py"
  test ! -e "$tmpdir/plugins/scope/scripts/scope-reviewer-claude-pexpect.py"
  test -x "$tmpdir/plugins/scope/scripts/scope-reviewer.py"
  test -x "$tmpdir/plugins/scope/scripts/scope-worker.py"
  test -x "$tmpdir/plugins/scope/scripts/scope-dependency-merge.py"
  test -x "$tmpdir/plugins/scope/scripts/scope-wrap-finalize.py"
  test -f "$tmpdir/plugins/scope/scripts/scope_git.py"
  test -f "$tmpdir/plugins/scope/scripts/scope_fingerprint.py"
  test -f "$tmpdir/plugins/scope/scripts/scope_codegraph.py"
  test -f "$tmpdir/plugins/scope/workers/refinement-worker.md"
  test -f "$tmpdir/plugins/scope/workers/implementation-worker.md"
  test -f "$tmpdir/plugins/scope/workers/diagnostic-worker.md"
  test -f "$tmpdir/plugins/scope/requirements.txt"
  test -f "$tmpdir/plugins/scope/.codex-plugin/plugin.json"
  test -f "$tmpdir/.scope/config.yaml"

  test ! -e "$tmpdir/.claude/scripts/.DS_Store"
  test ! -e "$tmpdir/.claude/scripts/__pycache__"
  test ! -e "$tmpdir/.claude/scripts/.pytest_cache"
  test ! -e "$tmpdir/plugins/scope/scripts/.DS_Store"
  test ! -e "$tmpdir/plugins/scope/scripts/__pycache__"
  test ! -e "$tmpdir/plugins/scope/scripts/.pytest_cache"
  test ! -e "$tmpdir/.claude/scripts/scope-proof-preflight.py"
  test ! -e "$tmpdir/plugins/scope/scripts/scope-proof-preflight.py"
  test ! -e "$tmpdir/.claude/config/worker-runtime-policy.yaml"
  test ! -e "$tmpdir/plugins/scope/config/worker-runtime-policy.yaml"
  test ! -e "$tmpdir/.claude/commands/implement_tdd.md"
  test ! -e "$tmpdir/plugins/scope/commands/implement_tdd.md"
  test ! -e "$tmpdir/plugins/scope/docs/epic-workflow.md"
  test ! -e "$tmpdir/.claude/governance/agent-lifecycle.md"
  test ! -e "$tmpdir/plugins/scope/governance/agent-lifecycle.md"

  grep -n '^  skill: local-tracking-bash' "$tmpdir/.scope/config.yaml"
  grep -n '^  project_key: PROJECT' "$tmpdir/.scope/config.yaml"
  grep -n '^  base_path: ./tracking' "$tmpdir/.scope/config.yaml"
  grep -n '^  skill: project-documentation-file' "$tmpdir/.scope/config.yaml"
  grep -n '^  docs_path: ./docs' "$tmpdir/.scope/config.yaml"
  if grep -n -E 'MYPROJ|MYSPACE|jira|confluence' "$tmpdir/.scope/config.yaml"; then
    fail "installed default config must not require Jira or Confluence setup"
  fi

  test -d "$tmpdir/.claude/commands/audit_epic"
  test -d "$tmpdir/plugins/scope/commands/audit_epic"

  test -f "$tmpdir/.claude/skills/project-documentation/SKILL.md"
  test -f "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/design.md"
  test -f "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml"
  test -f "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/delivery-manifest.yaml"
  test -f "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/refinement-state.yaml"
  test -f "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/refinement-findings.yaml"
  test ! -e "$tmpdir/.claude/skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/SKILL.md"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/design.md"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/delivery-manifest.yaml"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/refinement-state.yaml"
  test -f "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/refinement-findings.yaml"
  test ! -e "$tmpdir/plugins/scope/skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml"
  grep -n "Path selection rule" "$tmpdir/.claude/skills/project-documentation/SKILL.md"
  grep -n "docs/architecture/backend/01-intro.md" "$tmpdir/.claude/skills/project-documentation/SKILL.md"
  grep -n "Path selection rule" "$tmpdir/plugins/scope/skills/project-documentation/SKILL.md"
  grep -n "docs/architecture/backend/01-intro.md" "$tmpdir/plugins/scope/skills/project-documentation/SKILL.md"
  grep -n "do not ask for a" "$tmpdir/.claude/skills/project-documentation/SKILL.md"
  grep -n "Do not ask for a Jira project key" "$tmpdir/.claude/skills/project-tracking/SKILL.md"
  grep -n '^model: claude-opus-5-5$' "$tmpdir/.claude/agents/developer.md"
  grep -n '^model: gpt-6-sol$' "$tmpdir/plugins/scope/agents/developer.md"
  grep -n '^model_reasoning_effort: max$' "$tmpdir/plugins/scope/agents/developer.md"

  for obsolete in \
    workers/audit-worker.md \
    commands/audit_epic/reviewer-codex.md \
    commands/audit_epic/reviewer-claude.md \
    commands/audit_epic/reviewer-agy.md \
    commands/audit_epic/reviewer-glm.md \
    commands/epic_refine/reviewer-architecture-codex.md \
    commands/epic_refine/reviewer-architecture-claude.md \
    commands/epic_refine/reviewer-architecture-agy.md \
    commands/epic_refine/reviewer-architecture-glm.md \
    skills/project-documentation/templates-technical-arc42-c4/epic/system-context.md \
    skills/project-documentation/templates-technical-arc42-c4/epic/architecture.md \
    skills/project-documentation/templates-technical-arc42-c4/epic/adr.md \
    skills/project-documentation/templates-technical-arc42-c4/epic/pdr.md \
    skills/project-documentation/templates-technical-arc42-c4/epic/test-strategy.md; do
    test ! -e "$tmpdir/.claude/$obsolete"
    test ! -e "$tmpdir/plugins/scope/$obsolete"
  done

  rm -rf "$tmpdir"
}

check_windows_installer() {
  local batch_version
  local required_path
  local shell_version

  section "Check Windows installer parity"

  test -f install.bat
  shell_version="$(sed -n 's/^VERSION="\([^"]*\)"/\1/p' install.sh)"
  batch_version="$(sed -n 's/^set "VERSION=\([^"]*\)"/\1/p' install.bat)"
  [[ -n "$shell_version" && "$batch_version" == "$shell_version" ]] || fail "install.sh and install.bat versions differ"
  grep -n 'if /I "%~1"=="--user"' install.bat
  grep -n 'set "INSTALL_DIR=%USERPROFILE%"' install.bat
  grep -n 'set "CLAUDE_DIR=%~1\\.claude"' install.bat
  grep -n 'set "CODEX_DIR=%~1\\plugins\\scope"' install.bat

  for required_path in commands scripts skills agents workers governance config docs .codex-plugin; do
    grep -n "${required_path}" install.bat >/dev/null
  done

  grep -n 'config_example.yaml' install.bat
  grep -n 'requirements.txt' install.bat
  grep -n 'scope-reviewer-tmux.sh' install.bat
  grep -n 'reviewer-codex reviewer-claude reviewer-agy reviewer-glm' install.bat
  grep -n 'reviewer-architecture-codex reviewer-architecture-claude reviewer-architecture-agy reviewer-architecture-glm' install.bat
  grep -n 'system-context architecture adr pdr test-strategy' install.bat
  grep -n '__pycache__ .pytest_cache' install.bat
  grep -n '\*.pyc \*.pyo' install.bat
  grep -n '\.DS_Store' install.bat
  grep -n 'install.bat --user' README.md
  grep -n 'install.bat "C:\\path\\to\\your-project"' README.md
}

check_git_hooks() {
  section "Check Git hook enforcement"

  test -x .githooks/pre-push
  test -x scripts/setup-git-hooks.sh
  bash -n .githooks/pre-push scripts/setup-git-hooks.sh
  grep -n 'validate-pr-checks.sh' .githooks/pre-push
  grep -n 'core.hooksPath .githooks' scripts/setup-git-hooks.sh
  grep -n 'setup-git-hooks.sh' AGENTS.md CONTRIBUTING.md
}

check_actions_runtime() {
  section "Check GitHub Actions runtime"

  if grep -R -n 'actions/checkout@v4' .github/workflows; then
    fail "actions/checkout@v4 uses the deprecated Node 20 runtime"
  fi

  grep -n 'actions/checkout@v6' .github/workflows/pr-checks.yml
  grep -n 'actions/setup-python@v6' .github/workflows/pr-checks.yml
}

check_codex_plugin_naming() {
  section "Check Codex plugin naming"

  if grep -R -n -E "scope-for-codex|scope_for_codex" src_codex src_shared install.sh install.bat README.md CONTRIBUTING.md; then
    fail "found stale Codex plugin naming; use 'scope'"
  fi

  grep -n -E '"name"[[:space:]]*:[[:space:]]*"scope"' src_codex/.codex-plugin/plugin.json
}

check_worker_contracts() {
  section "Check worker protocol ownership"

  for command in \
    src_shared/commands/epic_refine.md \
    src_shared/commands/audit_epic.md \
    src_claude/commands/implement.md \
    src_codex/commands/implement.md; do
    test -f "$command"
  done

  for worker in refinement implementation diagnostic; do
    test -f "src_shared/workers/${worker}-worker.md"
  done
  test -f src_shared/config/worker-job.schema.json
  test -f src_shared/config/worker-result.schema.json
  test -f src_shared/config/execution-policy.yaml
  test -f src_shared/scripts/scope_proofs.py
  test -f src_shared/scripts/scope_snapshot.py
  test ! -f src_shared/workers/audit-worker.md

  if grep -R -n -E 'codex exec|agy --model|claude --model' \
    src_shared/commands/epic_refine.md \
    src_shared/commands/audit_epic.md \
    src_claude/commands/implement.md \
    src_codex/commands/implement.md; then
    fail "public orchestrators must not duplicate provider launcher syntax"
  fi

  if grep -R -n -E 'scope-proof-preflight|worker-runtime-policy|unattributed_change_incidents|question_discovery|metadata-job|materialize_handoff|finalize_candidate|operate --' \
    src_shared/commands \
    src_claude/commands/implement.md \
    src_codex/commands/implement.md \
    src_shared/workers; then
    fail "active workflow surfaces still mention a removed lifecycle subsystem"
  fi
}

check_codex_override_sources() {
  section "Check Codex override sources"

  if grep -R -n -E 'prefer[^[:cntrl:]]*\.claude|fallback[^[:cntrl:]]*\.claude|\.claude[^[:cntrl:]]*project-specific|CLAUDE\.md' src_codex; then
    fail "Codex files must use plugins/scope and AGENTS.md, not .claude overrides or CLAUDE.md"
  fi

  grep -n "Do not read \`.claude/\`" src_codex/skills/scope-workflows/SKILL.md
  grep -n "Follow repository instructions in \`AGENTS.md\`" src_codex/skills/scope-workflows/SKILL.md
}

check_codex_invocation() {
  section "Check Codex invocation"

  if grep -R -n -E -- '--ask-for-approval([[:space:]]|$)' src_shared src_claude src_codex; then
    fail "Codex exec no longer supports --ask-for-approval; use supported flags only"
  fi

  if grep -R -n -F 'gpt-5.5' \
    src_shared/config src_shared/scripts src_codex/config src_claude/config; then
    fail "Scope worker/reviewer defaults must use the approved Sol/Astra routing"
  fi

  grep -n -- "--ephemeral" src_shared/scripts/scope-worker.py
  grep -n -- "--ignore-user-config" src_shared/scripts/scope-worker.py
  grep -n -- "--output-schema" src_shared/scripts/scope-worker.py
  grep -n -- "--sandbox" src_shared/scripts/scope-worker.py
  grep -n 'model_reasoning_effort' src_shared/scripts/scope-worker.py
  grep -n 'model: gpt-6-astra' src_codex/config/worker-policy.yaml
  grep -n 'product: {model: gpt-6-sol, reasoning_effort: high}' src_codex/config/worker-policy.yaml
  grep -n 'story: {model: gpt-6-sol, reasoning_effort: max}' src_codex/config/worker-policy.yaml
  grep -n 'investigate: {model: gpt-6-sol, reasoning_effort: high}' src_codex/config/worker-policy.yaml
  grep -n -- "--ignore-user-config" src_shared/config/reviewer-policy.yaml
  grep -n -- "- read-only" src_shared/config/reviewer-policy.yaml
  grep -n '^model: gpt-6-sol$' src_codex/agents/developer.md
  grep -n '^model_reasoning_effort: max$' src_codex/agents/developer.md
  grep -n 'minimum_version: 1.5.0' src_shared/config/codegraph-policy.yaml
  grep -n 'sync_on_prepare: true' src_shared/config/codegraph-policy.yaml
  grep -n 'index_directory_not_ignored' src_shared/scripts/scope_codegraph.py
  grep -n -- '--add-dir' src_shared/scripts/scope-worker.py
  grep -n -- '--add-dir' src_shared/scripts/scope-reviewer.py

  if grep -R -n -E 'codegraph (context|sync-if-dirty)|Prefer CodeGraph MCP' \
    src_shared src_claude src_codex; then
    fail "Scope must use the CodeGraph 1.5 CLI contract, not removed commands or MCP preference"
  fi
}

check_claude_invocation() {
  section "Check Claude invocation"

  if grep -R -n -E 'Claude Opus 4\.7|Opus 4\.7|claude-opus-4\.7' src_shared src_claude src_codex; then
    fail "Claude reviewer must use local Opus alias naming, not a stale pinned Opus version label"
  fi

  if grep -R -n -E 'permission_mode:[[:space:]]*(acceptEdits|bypassPermissions)' src_claude/config/worker-policy.yaml; then
    fail "Claude write workers must use the tested non-interactive permission mode"
  fi

  if grep -R -n -F -- "--mcp-config '{}'" \
    src_shared/config/reviewer-policy.yaml \
    src_shared/scripts/scope-worker.py; then
    fail "Scope Claude reviewer automation must not pass a version-sensitive empty MCP configuration"
  fi

  grep -n 'permission_mode: dontAsk' src_claude/config/worker-policy.yaml
  if grep -n 'reported_fallback_model_families:' src_claude/config/worker-policy.yaml; then
    fail "worker policy must record raw model usage without fallback-family taxonomy"
  fi
  grep -n 'product: {model: claude-opus-5-5, reasoning_effort: high}' src_claude/config/worker-policy.yaml
  grep -n 'investigate: {model: claude-opus-5-5, reasoning_effort: high}' src_claude/config/worker-policy.yaml
  grep -n 'design_handoff: {model: claude-opus-5-5' src_claude/config/worker-policy.yaml
  grep -n -- "--strict-mcp-config" src_shared/scripts/scope-worker.py
  grep -n -- "--no-session-persistence" src_shared/scripts/scope-worker.py
  grep -n -- "--permission-mode" src_shared/scripts/scope-worker.py
  grep -n -- "--allowedTools" src_shared/scripts/scope-worker.py
  grep -n -- "--disallowedTools" src_shared/scripts/scope-worker.py
  grep -n 'claude: {model: claude-opus-5-5' src_shared/config/reviewer-policy.yaml
  grep -n 'opencode: {model: meta/muse-spark-1.3-contributor, reasoning_effort: high}' src_shared/config/reviewer-policy.yaml
  grep -n -- "--safe-mode" src_shared/config/reviewer-policy.yaml
  grep -n -- "--strict-mcp-config" src_shared/config/reviewer-policy.yaml
  grep -n -- "--permission-mode" src_shared/config/reviewer-policy.yaml
  grep -n -- "      - dontAsk" src_shared/config/reviewer-policy.yaml
  grep -n -- "--disallowedTools" src_shared/config/reviewer-policy.yaml
  grep -n -- "      - Write,Edit,NotebookEdit,Task,Agent" src_shared/config/reviewer-policy.yaml
  if grep -n -- "--dangerously-skip-permissions" src_shared/config/reviewer-policy.yaml; then
    fail "external reviewers must not bypass provider permission checks"
  fi
  grep -n -- "--no-chrome" src_shared/config/reviewer-policy.yaml
  grep -n 'backend: claude' src_shared/config/reviewer-policy.yaml
  grep -n 'prompt_transport: stdin' src_shared/config/reviewer-policy.yaml
  grep -n -- '--print' src_shared/config/reviewer-policy.yaml
  test ! -e src_shared/scripts/scope-reviewer-claude-pexpect.py
  test ! -e tests/unit/test_scope_reviewer_claude_pexpect.py
  if grep -R -n -i -E --exclude-dir='__pycache__' 'pexpect|claude_pty' \
    requirements.txt src_shared/config src_shared/scripts tests/unit/test_scope_reviewer.py; then
    fail "Scope Claude reviewers must use the CLI directly without PTY or pexpect"
  fi
}

check_command_expectations() {
  section "Check lean workflow assets"

  test -f src_claude/commands/implement.md
  test -f src_codex/commands/implement.md
  test -f src_shared/commands/wrap_epic.md
  test ! -e src_claude/commands/wrap_epic.md
  test ! -e src_codex/commands/wrap_epic.md

  test -f src_shared/commands/audit_epic/reviewer-audit.md
  test -f src_shared/commands/epic_refine/reviewer-refinement.md

  test -f tests/unit/test_orchestrator_contracts.py
  test -f tests/unit/test_scope_worker.py
  test -f tests/unit/test_scope_codegraph.py
  test -f tests/unit/test_scope_reviewer.py
  test -f tests/unit/test_worker_prompts.py
  test -f tests/unit/test_worker_schema.py
  test -f src_shared/skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml
  if grep -R -n -E 'LEGACY_VALIDATOR|legacy input mode|maximum_followups|minimum_followups|followup_count|followup-[0-9N]' \
    src_shared/commands/epic_refine.md \
    src_shared/commands/audit_epic.md \
    src_claude/commands/implement.md \
    src_codex/commands/implement.md; then
    fail "Scope commands must not contain legacy workflow fallbacks or old follow-up names"
  fi
}

check_unit_tests() {
  local python_cmd

  section "Run unit tests"

  python_cmd="${SCOPE_PYTHON:-python3}"
  command -v "$python_cmd" >/dev/null 2>&1 || fail "Python is required; set SCOPE_PYTHON to a Python 3 executable"
  "$python_cmd" -c 'import coverage, filelock, jsonschema, psutil, pytest, yaml' >/dev/null 2>&1 ||
    fail "Missing Python dependencies; run: python3 -m pip install -r requirements-dev.txt"

  mkdir -p tmp_debug
  "$python_cmd" -m coverage erase
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    "$python_cmd" -m coverage run -m pytest -q tests/unit
  "$python_cmd" -m coverage combine
  "$python_cmd" -m coverage report
}

main() {
  local range

  range="$(diff_range "${1:-}")"

  check_whitespace "$range"
  check_generated_files
  check_mirrors "$range"
  check_install
  check_windows_installer
  check_git_hooks
  check_actions_runtime
  check_codex_plugin_naming
  check_worker_contracts
  check_codex_override_sources
  check_codex_invocation
  check_claude_invocation
  check_command_expectations
  check_unit_tests

  section "All PR checks passed"
}

main "$@"

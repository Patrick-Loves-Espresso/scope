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

python_cmd() {
  local python="${SCOPE_PYTHON:-python3}"

  command -v "$python" >/dev/null 2>&1 || fail "Python is required; set SCOPE_PYTHON to a Python 3 executable"
  "$python" -c 'import coverage, psutil, pygments, pytest, yaml' >/dev/null 2>&1 ||
    fail "Missing Python dependencies; run: python3 -m pip install -r requirements-dev.txt"
  printf '%s\n' "$python"
}

check_install() {
  local tmpdir
  local root
  local path
  local executable

  section "Install smoke test"

  tmpdir="$(mktemp -d)"
  for root in .claude plugins/scope; do
    for path in \
      scripts/validate-refinement.py scripts/audit-artifacts.py scripts/scope-wrap-finalize.py \
      scripts/scope-dependency-merge.py scripts/scope_fingerprint.py scripts/scope_snapshot.py \
      scripts/scope_proofs.py scripts/scope-worker.py scripts/scope-reviewer.py \
      scripts/scope_codegraph.py scripts/scope_git.py scripts/validate-architecture-contracts.sh \
      scripts/validate-epic-docs.sh config/audit-policy.yaml config/codegraph-policy.yaml \
      config/execution-policy.yaml config/refinement-policy.yaml config/reviewer-policy.yaml \
      config/worker-job.schema.json config/worker-result.schema.json config/wrap-policy.yaml \
      config/worker-policy.yaml workers/refinement-worker.md workers/implementation-worker.md \
      workers/diagnostic-worker.md commands/webepic_refine.md commands/webepic_implement.md \
      commands/website_breakdown.md commands/content_refine.md governance/production-code-rules.md \
      governance/test-strategy-guide.md skills/website-strategy/SKILL.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/design.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/delivery-manifest.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/refinement-state.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/refinement-findings.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/implementation-summary.md \
      workers/audit-worker.md scripts/scope-reviewer-claude-pexpect.py \
      scripts/scope-proof-preflight.py config/worker-runtime-policy.yaml commands/implement_tdd.md \
      governance/agent-lifecycle.md commands/audit_epic/reviewer-codex.md \
      commands/epic_refine/reviewer-architecture-codex.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/system-context.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml; do
      mkdir -p "$(dirname "$tmpdir/$root/$path")"
      touch "$tmpdir/$root/$path"
    done
    mkdir -p "$tmpdir/$root/scripts/__pycache__" "$tmpdir/$root/scripts/.pytest_cache"
    touch "$tmpdir/$root/scripts/.DS_Store" "$tmpdir/$root/scripts/__pycache__/stale.pyc" \
      "$tmpdir/$root/scripts/.pytest_cache/stale"
  done
  ./install.sh "$tmpdir" >"$tmpdir/install.log"

  for root in .claude plugins/scope; do
    for path in \
      commands/epic_refine.md commands/implement.md commands/audit_epic.md commands/wrap_epic.md \
      commands/epic_refine/reviewer-refinement.md commands/audit_epic/reviewer-audit.md \
      workers/planner.md workers/implementer.md workers/reviewer.md \
      governance/simplicity-and-size.md governance/developer-checklist.md config/scope-policy.yaml \
      scripts/scope_common.py scripts/scope_providers.py agents/developer.md agents/architect.md \
      agents/product-owner.md skills/project-documentation/SKILL.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/details.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-criteria.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/plan.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/review.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/approvals.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/verification.yaml \
      requirements.txt; do
      test -f "$tmpdir/$root/$path" || fail "install is missing $root/$path"
    done
    for executable in scope_launch.py scope_review.py scope_verify.py scope_check.py; do
      test -x "$tmpdir/$root/scripts/$executable" || fail "$root/scripts/$executable is not executable"
    done
    for path in \
      scripts/validate-refinement.py scripts/audit-artifacts.py scripts/scope-wrap-finalize.py \
      scripts/scope-dependency-merge.py scripts/scope_fingerprint.py scripts/scope_snapshot.py \
      scripts/scope_proofs.py scripts/scope-worker.py scripts/scope-reviewer.py \
      scripts/scope_codegraph.py scripts/scope_git.py scripts/validate-architecture-contracts.sh \
      scripts/validate-epic-docs.sh config/audit-policy.yaml config/codegraph-policy.yaml \
      config/execution-policy.yaml config/refinement-policy.yaml config/reviewer-policy.yaml \
      config/worker-job.schema.json config/worker-result.schema.json config/wrap-policy.yaml \
      config/worker-policy.yaml workers/refinement-worker.md workers/implementation-worker.md \
      workers/diagnostic-worker.md commands/webepic_refine.md commands/webepic_implement.md \
      commands/website_breakdown.md commands/content_refine.md governance/production-code-rules.md \
      governance/test-strategy-guide.md skills/website-strategy/SKILL.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/design.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/delivery-manifest.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/refinement-state.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/refinement-findings.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/implementation-evidence.yaml \
      skills/project-documentation/templates-technical-arc42-c4/epic/implementation-summary.md \
      workers/audit-worker.md scripts/scope-reviewer-claude-pexpect.py \
      scripts/scope-proof-preflight.py config/worker-runtime-policy.yaml commands/implement_tdd.md \
      governance/agent-lifecycle.md commands/audit_epic/reviewer-codex.md \
      commands/epic_refine/reviewer-architecture-codex.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/system-context.md \
      skills/project-documentation/templates-technical-arc42-c4/epic/acceptance-traceability.yaml; do
      test ! -e "$tmpdir/$root/$path" || fail "retired file still installed: $root/$path"
    done
    test ! -e "$tmpdir/$root/skills/website-strategy" || fail "retired website-strategy skill still installed"
    test ! -e "$tmpdir/$root/scripts/.DS_Store" || fail "generated file installed in $root"
    test ! -e "$tmpdir/$root/scripts/__pycache__" || fail "generated cache installed in $root"
    test ! -e "$tmpdir/$root/scripts/.pytest_cache" || fail "generated cache installed in $root"
    cmp -s src_shared/commands/implement.md "$tmpdir/$root/commands/implement.md" ||
      fail "$root/commands/implement.md differs from src_shared"
    grep -q "Current-State Rule" "$tmpdir/$root/skills/project-documentation/SKILL.md" ||
      fail "$root skill lacks the current-state rule"
    "$(python_cmd)" "$tmpdir/$root/scripts/scope_verify.py" lines "$tmpdir/$root/scripts/scope_common.py" >/dev/null ||
      fail "installed scripts in $root do not run"
  done

  test -f "$tmpdir/plugins/scope/.codex-plugin/plugin.json"
  test -f "$tmpdir/plugins/scope/README.md"
  test -x "$tmpdir/plugins/scope/scripts/scope-command"
  test -f "$tmpdir/.scope/config.yaml"
  grep -n '^  skill: local-tracking-bash' "$tmpdir/.scope/config.yaml"
  grep -n '^  docs_path: ./docs' "$tmpdir/.scope/config.yaml"
  if grep -n -E 'MYPROJ|MYSPACE|jira|confluence' "$tmpdir/.scope/config.yaml"; then
    fail "installed default config must not require Jira or Confluence setup"
  fi
  grep -n "Path selection rule" "$tmpdir/.claude/skills/project-documentation/SKILL.md"
  grep -n "Do not ask for a Jira project key" "$tmpdir/.claude/skills/project-tracking/SKILL.md"
  grep -n '^model: claude-opus-5-5$' "$tmpdir/.claude/agents/developer.md"
  grep -n '^model: gpt-6-sol$' "$tmpdir/plugins/scope/agents/developer.md"
  grep -n '^model_reasoning_effort: xhigh$' "$tmpdir/plugins/scope/agents/developer.md"

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

check_codex_override_sources() {
  section "Check Codex override sources"

  if grep -R -n -E 'prefer[^[:cntrl:]]*\.claude|fallback[^[:cntrl:]]*\.claude|\.claude[^[:cntrl:]]*project-specific|CLAUDE\.md' src_codex; then
    fail "Codex files must use plugins/scope and AGENTS.md, not .claude overrides or CLAUDE.md"
  fi

  grep -n "Do not read \`.claude/\`" src_codex/skills/scope-workflows/SKILL.md
  grep -n "Follow repository instructions in \`AGENTS.md\`" src_codex/skills/scope-workflows/SKILL.md
}

check_budgets() {
  local python
  local lines
  local command
  local policies

  section "Check Scope complexity budgets"

  python="$(python_cmd)"
  lines="$(cat src_shared/scripts/*.py | wc -l | tr -d ' ')"
  echo "Lifecycle Python: ${lines} lines (budget 3000)"
  [[ "$lines" -le 3000 ]] || fail "lifecycle Python exceeds 3000 lines"

  "$python" src_shared/scripts/scope_verify.py lines src_shared/scripts/*.py | "$python" -c '
import json, sys
sizes = json.load(sys.stdin)
for path, code in sorted(sizes.items()):
    print(f"  {path}: {code} code lines (target 350, limit 450)")
over = [path for path, code in sizes.items() if code > 450]
sys.exit(f"modules above 450 code lines: {over}" if over else 0)' || fail "module size budget exceeded"

  for command in epic_refine implement audit_epic wrap_epic; do
    lines="$(wc -l < "src_shared/commands/${command}.md" | tr -d ' ')"
    echo "Command /${command}: ${lines} lines (budget 150)"
    [[ "$lines" -le 150 ]] || fail "/${command} exceeds 150 lines"
  done

  policies="$(find src_shared/config src_claude src_codex -name '*.yaml' -path '*config*' | sort)"
  echo "Policy files: ${policies}"
  [[ "$policies" == "src_shared/config/scope-policy.yaml" ]] || fail "Scope must have exactly one policy file"
}

check_lifecycle_contract() {
  section "Check lifecycle contract"

  test ! -e src_claude/commands/implement.md || fail "implement is shared; remove the Claude copy"
  test ! -e src_codex/commands/implement.md || fail "implement is shared; remove the Codex copy"
  if grep -R -n -E 'codex exec|claude --print|opencode run' src_shared/commands; then
    fail "command prompts must launch providers through scope_launch.py"
  fi
  if grep -R -n -E -- '--ask-for-approval([[:space:]]|$)|--dangerously-skip-permissions|--dangerously-bypass' \
    src_shared src_claude src_codex; then
    fail "providers must keep their permission checks and sandboxes"
  fi
  if grep -R -n -E 'Opus 4\.7|claude-opus-4\.7|gpt-5\.5' src_shared src_claude src_codex; then
    fail "stale model names in Scope sources"
  fi
  grep -n -- '"--safe-mode"' src_shared/scripts/scope_providers.py
  grep -n -- '"--no-session-persistence"' src_shared/scripts/scope_providers.py
  grep -n -- '"--permission-mode", "dontAsk"' src_shared/scripts/scope_providers.py
  grep -n 'REVIEWER_DENIED = "Write,Edit,NotebookEdit,Task,Agent"' src_shared/scripts/scope_providers.py
  grep -n -- '"--ignore-user-config"' src_shared/scripts/scope_providers.py
  grep -n -- '"workspace-write" if write else "read-only"' src_shared/scripts/scope_providers.py
  grep -n -- '"--agent", "plan"' src_shared/scripts/scope_providers.py
  grep -n 'fallback_reviewer: opencode' src_shared/config/scope-policy.yaml
  grep -n 'standard_reviewers: \[claude, codex\]' src_shared/config/scope-policy.yaml
  grep -n 'growth_threshold: 1.5' src_shared/config/scope-policy.yaml
  grep -n 'story_complexity_max: 7' src_shared/config/scope-policy.yaml
}

check_tests() {
  local python

  section "Run tests"

  python="$(python_cmd)"
  mkdir -p tmp_debug
  "$python" -m coverage erase
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    "$python" -m coverage run -m pytest -q -p no:cacheprovider tests
  "$python" -m coverage combine -q
  "$python" -m coverage report
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
  check_codex_override_sources
  check_lifecycle_contract
  check_budgets
  check_tests

  section "All PR checks passed"
}

main "$@"

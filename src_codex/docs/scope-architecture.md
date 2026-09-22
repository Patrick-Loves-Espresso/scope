# SCOPE - Simple Claude/Codex Orchestrator for Product Engineering

**Status:** Implementation

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. File Structure](#2-file-structure)
- [3. User Workflow](#3-user-workflow)
- [4. Commands](#4-commands)
- [5. Agents](#5-agents)
- [6. Skills](#6-skills)
- [7. Core Concepts](#7-core-concepts)
- [8. Epic Lifecycle](#8-epic-lifecycle)
- [9. Documentation Structure](#9-documentation-structure)
- [10. Architectural Decisions](#10-architectural-decisions)

---

## 1. Overview

SCOPE is a Claude Code and Codex framework for epic lifecycle management. It
provides public command contracts, bounded worker roles, deterministic
validators, standalone agents, and documentation skills.

The public command is the sole conversational orchestrator. For
`epic_refine`, `implement`, and `audit_epic`, it derives phase state from
durable artifacts and deterministic validators, then launches fresh bounded
provider processes for repository work. The orchestrator retains approval
gates and all user communication.

```
┌──────────────────────────────────────────────────────────────────┐
│                         User Commands                             │
│                                                                   │
│   /prd_create [product]    Create first-pass PRD from interview  │
│   /prd_refine [product]    Refine product requirements           │
│   /prd_breakdown           Break PRD into epics                  │
│   /epic_refine {epic-id}   Refine epic (contract-first)          │
│   /implement {epic-id}     Implement (developer writes tests)    │
│   /audit_epic {epic-id}    Audit implementation                  │
│   /wrap_epic {epic-id}     Verify, archive, commit, and merge    │
│   /sync_product [epic-id]  Sync product documentation            │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│               Claude Code or Codex (Orchestrator)                 │
│                                                                   │
│   - Executes command workflows step-by-step                      │
│   - Derives workflow state from durable artifacts                │
│   - Launches bounded workers with structured job/results         │
│   - Manages git worktrees for implementation                     │
│   - Handles user approval gates                                  │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                   │
              ▼                  ▼                   ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   Agent Files     │  │   Skills          │  │   Documentation   │
│                   │  │                   │  │                   │
│   architect       │  │   project-        │  │   docs/product/   │
│   developer       │  │    documentation  │  │   docs/arch/      │
│   product-owner   │  │    tracking       │  │   docs/releases/  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

---

## 2. File Structure

### 2.1 This Project (SCOPE Repository)

```
scope/
├── src_shared/
│   ├── commands/                           # Cross-platform workflow contracts
│   ├── workers/                            # Bounded refinement/implementation/audit roles
│   ├── scripts/                            # Runner, reviewer, and validators
│   ├── config/                             # Policies and strict JSON schemas
│   ├── agents/                             # Shared standalone roles
│   ├── skills/                             # Documentation and tracking skills
│   └── governance/                         # Production quality rules and checklists
├── src_claude/
│   ├── commands/                           # Claude-specific commands
│   └── agents/                             # Claude role overrides
├── src_codex/
│   ├── commands/                           # Codex-specific commands
│   ├── agents/                             # Codex role overrides
│   ├── skills/                             # Codex workflow guidance
│   ├── docs/                               # Installed Codex reference docs
│   └── .codex-plugin/                      # Plugin manifest
├── tests/unit/                             # Deterministic runtime/contract tests
└── docs/                                   # Repository documentation
```

### 2.2 Target Project Structure

After installing SCOPE skills into a target project:

```
user-project/
├── .claude/
│   ├── commands/                           # Claude workflow contracts
│   │   ├── prd_refine.md
│   │   ├── prd_breakdown.md
│   │   ├── epic_refine.md
│   │   ├── implement.md
│   │   ├── audit_epic.md
│   │   ├── wrap_epic.md
│   │   └── sync_product.md
│   │
│   ├── agents/                             # Standalone role definitions
│   │   ├── architect.md
│   │   ├── developer.md
│   │   └── product-owner.md
│   │
│   ├── workers/                            # Fresh bounded worker contracts
│   ├── scripts/                            # Runner, reviewer, and validators
│   ├── config/                             # Policies and strict schemas
│   └── skills/                             # Shared skills
│       ├── project-documentation/
│       │   ├── SKILL.md
│       │   ├── templates-product-atlassian/
│       │   └── templates-technical-arc42-c4/
│       └── project-tracking/
│           ├── SKILL.md
│           └── {backend}.md
│
├── plugins/scope/                          # Equivalent Codex plugin assets
│   ├── commands/
│   ├── agents/
│   ├── workers/
│   ├── scripts/
│   ├── config/
│   ├── skills/
│   └── docs/
│
├── .scope/
│   └── config.yaml                         # Project configuration
│
├── docs/                                   # Documentation (local files)
│   ├── product/                            # Product documentation
│   ├── architecture/                       # Arc42 technical documentation
│   ├── epics/{epic-id}/                    # Per-epic documentation
│   └── releases/{version}/                 # Release documentation
│
├── ./wip/                                  # Worktrees for implementation
│   └── {epic-id}/                          # Worktree per epic
│       ├── .git                            # Worktree link
│       └── src/                            # Code changes
│
└── src/                                    # Application source code
```

**Key points:**
- Documentation is always local markdown files in `docs/`
- Implementation happens in git worktrees under `./wip/`
- Main branch holds refinement artifacts; worktrees hold implementation
- Canonical workflow state and reviewer receipts remain in epic/audit
  artifacts; only runner snapshots, prompts, and logs live under `tmp_debug/`

---

## 3. User Workflow

The complete product development pipeline:

```
Create PRD draft or run /prd_create
       │
       ▼
/prd_refine [product]          Interactive PRD refinement
       │                       (checklist-driven, discovery updates)
       ▼
/prd_breakdown                 Convert PRD → epics
       │                       (architecture, dependency analysis)
       ▼
/epic_refine {epic-id}         Adaptive epic refinement
       │                       (product + final authority, native contracts)
       ▼
/implement {epic-id}          Story-by-story implementation
       │
       ▼
/audit_epic {epic-id}          Read-only evidence audit
       │                       (one full + one targeted verification)
       ▼
/wrap_epic {epic-id}           Verify seal, archive, approved exact merge
```

**Supporting commands:**
- `/sync_product [epic-id]` - Sync product docs when implementation changes product scope

---

## 4. Commands

Each public command is a workflow contract in Markdown with YAML frontmatter.
The three worker-backed commands keep orchestration in that contract and put
repository execution behind shared worker/result schemas.

| Command | Description | Execution context | Supporting context |
|---------|-------------|-------------------|--------------------|
| `/prd_create` | Lightweight interview to create a first-pass PRD | (inline) | project-documentation |
| `/prd_refine` | Interactive PRD refinement with checklist | (inline) | project-documentation |
| `/prd_breakdown` | Convert PRD into implementable epics | (inline) | project-documentation, project-tracking |
| `/epic_refine` | Contract-first epic refinement, product and final authority | fresh refinement workers + independent reviewers | durable epic artifacts and validators |
| `/implement` | Story implementation, proof, nested audit, and remediation | fresh implementation workers | boundary plans, governance, and validators |
| `/audit_epic` | Read-only evidence and semantic audit | deterministic tooling + independent reviewers + audit synthesis worker | durable audit artifacts and validators |
| `/wrap_epic` | Verify and close an already completed delivery | deterministic finalizer after one bound approval | delivery seal, exact staged tree, and hardened Git helpers |
| `/sync_product` | Sync product docs after implementation | (inline) | project-documentation |

### Command Frontmatter

```yaml
---
name: implement
description: Orchestrate bounded story workers through proof, audit, remediation, and delivery evidence.
args: "{epic-id}"
---
```

- `name` - Slash command name
- `description` - What the command does
- `args` - Expected arguments
- `skills` - Optional skills used by commands that still require them
- `agents` - Optional agent definitions used by non-worker workflows

### Bounded Workers

`epic_refine`, `implement`, and `audit_epic` launch shared worker roles from
`workers/` through `scripts/scope-worker.py`. Each fresh process receives one
job packet, a role prompt, exact read/write boundaries, validation obligations,
and a strict result schema. The runner owns the write lock, one pre/post
snapshot for write jobs, timeout, cancellation, and compact recovery state
under ignored `tmp_debug/`.
Independent semantic review is launched separately through
`scripts/scope-reviewer.py` and remains read-only.

---

## 5. Agents

Agents are Markdown files that define persona, responsibilities, and
constraints for workflows and standalone roles that have not moved to bounded
workers. The three worker-backed commands do not use native subagent inheritance
for repository execution.

| Agent | File | Role |
|-------|------|------|
| **Architect** | `architect.md` | System design, story breakdown, implementation boundary plans, ADRs, contracts |
| **Developer** | `developer.md` | Implements stories, writes tests, follows implementation boundary plan intent |
| **Product Owner** | `product-owner.md` | Business requirements, acceptance criteria, PDRs |
| **RE Architect** | `reverse-engineer-architect.md` | Reverse-engineer architecture from existing code |
| **RE Product Owner** | `reverse-engineer-po.md` | Reverse-engineer product requirements from existing code |
| **RE Operations** | `reverse-engineer-ops.md` | Reverse-engineer operational behavior and runbooks |

The worker-backed epic commands use `workers/*.md` through `scope-worker.py`,
not these standalone role files or native Task inheritance. The runner permits
one write worker per working root and records one result plus one completed-job
row before the command advances.

---

## 6. Skills

### 6.1 Project Documentation

**File:** `src_shared/skills/project-documentation/SKILL.md`

Local markdown files in `docs/`. The skill defines:
- Folder structure (`docs/product/`, `docs/architecture/`, `docs/epics/`, `docs/releases/`)
- Operations: `read(path)`, `write(path, content)`, `search(pattern)`, `list(path)`
- Templates for product docs (Atlassian Blueprint pattern) and technical docs (Arc42+C4)

**Configuration:**
```yaml
# .scope/config.yaml
documentation:
  root: ./docs
```

### 6.2 Project Tracking

**File:** `src_shared/skills/project-tracking/SKILL.md`

Wrapper skill that dispatches to a configured backend. Supports:
- Local file-based tracking
- Jira (Atlassian MCP or Sooperset MCP)
- GitHub issues

**Configuration:**
```yaml
# .scope/config.yaml
tracking:
  skill: project-tracking-file   # or jira-atlassian-mcp, jira-sooperset-mcp
```

**Note:** Project tracking is optional. Many commands work without it. The documentation skill is the primary requirement.

---

## 7. Core Concepts

### 7.1 Native Contracts

Scope uses the contract mechanism appropriate to each boundary: OpenAPI, JSON
Schema, SQL, event schemas, configuration schemas, language interfaces, or
project-native validators. Python Protocols and `mypy` are optional, not
workflow requirements.

### 7.2 Git Worktrees

Implementation happens in git worktrees, not on the main branch.

```
/implement {epic-id}
  → Creates worktree at ./wip/{epic-id} on branch epic/{epic-id}
  → All stories implemented in the worktree
  → Audit PASS and delivery summary are sealed without committing
  → /wrap_epic archives and merges the exact seal-bound delta after approval
  → Worktree cleanup remains a separate user decision
```

### 7.3 Artifact-Derived Worker State

The worker-backed public commands derive the next legal phase or story from
epic/audit artifacts and deterministic validation output. The runner records
job, process, result, and recovery summaries under ignored
`tmp_debug/scope-runs/`; those operational records do not become a second
semantic workflow ledger or permanent evidence source. For implementation
jobs, the runner—not the worker—promotes observed path identities and proof
provenance into durable `implementation-evidence.yaml`.

### 7.4 Story Sizing

Create the fewest independently verifiable stories that preserve useful
dependency, rollout, and proof boundaries. Scope does not impose a fixed story
count, file count, or line count.

### 7.5 Inter-Story Dependencies

Dependencies are parsed from each boundary plan's YAML `depends_on` field. The
public command validates them and launches one eligible implementation worker
at a time, preserving the declared order and one-writer invariant.

### 7.6 Test-as-Soon-as-Possible

Write tests at the earliest point where the test becomes possible:
- **Unit tests:** Always in each story
- **Integration tests:** When component integration exists in that story
- **E2E tests:** When user flow completes in that story

### 7.7 Cross-Epic Test Evolution

Tests evolve progressively across epics rather than being written all at once:
```
Epic 1: user_lifecycle_journey.test.ts
  ✅ User logs in → ✅ User sees dashboard → 🔵 Future → ✅ User logs out

Epic 2 (extends):
  ✅ User logs in → ✅ User sees dashboard → ✅ User updates profile → ✅ User logs out
```

Tests organized by user journey, not by epic.

### 7.8 Production-Ready Code Rules

- File plan intent is the source of truth for what a file does
- No stubs, placeholders, or TODO implementations
- No mock-only code that passes tests but fails integration
- Fail-fast: no fallbacks or hardcoded values masking bugs

### 7.9 Audit Loop Guard

`/audit_epic` runs one full read-only audit. Implementation remediates named
findings, then audit performs one targeted verification. Additional full audits
require a material approved boundary change or explicit user authorization.

### 7.10 Context Window Optimization

- Worker prompts contain only the bounded role contract
- Job packets carry the epic, phase/story, exact paths, and validation commands
- Workers fetch authorized repository context on demand via direct file paths
- Progressive disclosure: parent files link to details, agents load only what's needed

---

## 8. Epic Lifecycle

### 8.1 Refinement (`/epic_refine`)

Adaptive epic refinement with two preapprovable authority gates:

```
Phase 1: Observable product contract and negative cases
  → PRODUCT-CONTRACT AUTHORITY

Phase 2: Repository-grounded architecture and native contracts

Phase 3: Story boundaries and proof obligations
  → RUN EACH PRE-EXISTING PROOF ONCE

Phase 4: Independent review and bounded correction
  → FINAL HANDOFF AUTHORITY
```

**Output:** A canonical `delivery-manifest.yaml`, evidence-backed `design.md`,
native contracts, per-story boundary plans, durable findings, and
`refinement-state.yaml` containing hash-bound authority.

### 8.2 Implementation (`/implement`)

**`/implement`:**
```
Optional Story 0: Architect-authored content or shared scaffolding
Story 1-N: Developer implements and proves each boundary-plan obligation
After all stories: Project-native tests, static checks, runtime proof, and audit
```

**Orchestration:** The public command validates dependency order and launches a
fresh bounded worker for each eligible story. It verifies the result hash,
actual changed paths, durable proof evidence, and story boundary before
advancing. Material product, architecture, or operations documentation is a
manifest v2 obligation owned by an implementation story, so it is current
before audit rather than rewritten during wrap. After audit PASS, the delivery
summary is written and the deterministic finalizer seals the exact audited
workspace.

### 8.3 Audit (`/audit_epic`)

Audit is read-only and evidence based:

1. validate the implementation handoff and durable evidence;
2. derive scoped acceptance and gate rows from canonical artifacts;
3. run project-native evidence gates;
4. execute risk-directed reviewer roles in fresh contexts;
5. merge stable findings and return `PASS`, `FAIL`, or `BLOCKED`;
6. after remediation, verify named findings in one targeted attempt.

**Output:** `docs/epics/{epic-dir}/epic_audit.md`

**Post-audit flow:**
1. Implementation remediates `remediation_required` findings.
2. The user resolves decision-gated findings.
3. Audit performs one targeted verification.

### 8.4 Closure (`/wrap_epic`)

`/wrap_epic` is a thin controller over the deterministic wrap finalizer. It
verifies the durable seal without relying on prunable runtime logs, stages only
the sealed delta plus the epic archival rename, and presents the staged tree,
fixed labels, and current main HEAD for one approval. The finalizer then commits
that exact tree, rechecks main HEAD under both mutation locks, merges the exact
closure commit, verifies the merge, and refreshes CodeGraph at the main root.

Wrap does not discover decisions or lessons, author documentation, regenerate
the implementation summary, infer dirty-file ownership, or write a tracking
marker. Incomplete deliveries return `NOT_READY` without mutation; automated
abandonment remains explicitly deferred.

The finalizer neutralizes Git hooks, fsmonitor commands, injected Git
environment, replace refs, and grafts. Repository-configured merge drivers and
clean/process filters remain enabled so legitimate custom merges and Git LFS
continue to work; the selected repository's local Git configuration is therefore
an explicit trust boundary.

### 8.5 Supporting Operations

- **`/sync_product`** - When implementation reveals product-level changes (new capabilities, terminology changes, scope shifts), updates `docs/product/` accordingly

---

## 9. Documentation Structure

Documentation uses two complementary standards:

### Product Documentation (Atlassian Blueprint Pattern)

```
docs/product/
├── overview.md               # Auto-generated parent with links
├── strategy.md               # Vision, markets, problems, scope
├── definition.md             # Use cases, capability map
├── reference/
│   ├── feature-catalog.md    # Features with status, priority, release
│   ├── terminology.md        # Domain terms, key entities
│   ├── ux-workflows.md       # Navigation, screens, workflows
│   └── apis-integrations.md  # External integrations
└── decisions.md              # Product Decision Records (PDR)
```

### Technical Documentation (Arc42 + C4)

```
docs/architecture/
├── 01-intro.md               # System purpose, stakeholders, quality goals
├── 02-constraints.md         # Technical, organizational constraints
├── 03-context.md             # C4 L1 context diagram
├── 04-strategy.md            # Solution approach, technology decisions
├── 05-building-blocks.md     # C4 L2/L3 component diagrams
├── 06-runtime.md             # Key scenarios, sequence diagrams
├── 07-deployment.md          # Infrastructure, deployment
├── 08-cross-cutting/         # Domain, security, operations, testing
├── 09-adr-summary.md         # Architecture Decision Records
├── 10-quality.md             # Quality requirements
├── 11-risks.md               # Risks, technical debt
├── 12-glossary.md            # Architecture terms
├── 13-specs/                 # System API contracts, schemas, database specs
│   ├── api/
│   ├── schemas/              # Schemas live here; do not create 14-schema
│   ├── database/
│   └── errors/
├── backend/                  # Backend-specific Arc42 01-13 tree
│   ├── 01-intro.md
│   ├── 02-constraints.md
│   ├── 03-context.md
│   ├── 04-strategy.md
│   ├── 05-building-blocks.md
│   ├── 06-runtime.md
│   ├── 07-deployment.md
│   ├── 08-cross-cutting/
│   ├── 09-adr-summary.md
│   ├── 10-quality.md
│   ├── 11-risks.md
│   ├── 12-glossary.md
│   ├── 13-specs/
│   └── adr/
└── frontend/                 # Frontend-specific Arc42 01-13 tree
    ├── 01-intro.md
    ├── 02-constraints.md
    ├── 03-context.md
    ├── 04-strategy.md
    ├── 05-building-blocks.md
    ├── 06-runtime.md
    ├── 07-deployment.md
    ├── 08-cross-cutting/
    ├── 09-adr-summary.md
    ├── 10-quality.md
    ├── 11-risks.md
    ├── 12-glossary.md
    ├── 13-specs/
    └── adr/
```

`13-specs/` is the canonical location for machine-readable contracts: OpenAPI,
JSON/YAML schemas, database specs, queue/message specs, and error contracts.
Do not create a separate `14-schema`; schemas belong in `13-specs/schemas/`.

Legacy component docs may exist in older projects:
`backend/overview.md`, `backend/services.md`, `backend/data.md`,
`frontend/overview.md`, `frontend/structure.md`, and `frontend/patterns.md`.
Read them as context when present, but do not create or extend them. New or
updated backend/frontend documentation uses the component `01-intro.md` through
`13-specs/` tree.

### Epic Documentation

```
docs/epics/{epic-id}/
├── details.md                  # Goal, scope, non-goals, lifecycle status
├── acceptance-criteria.md      # Canonical observable product behavior
├── design.md                   # Evidence, decisions, architecture, failures, proof
├── delivery-manifest.yaml      # Risk, acceptance, decisions, stories, proof ownership
├── refinement-state.yaml       # Workflow state and hash-bound user authority
├── file-plan-story-*.yaml      # Per-story implementation boundaries
├── refinement-findings.yaml    # Independent review findings
├── refinement-review.md        # Approved implementation handoff
├── implementation-evidence.yaml # Runner-observed paths and proof provenance
├── implementation-summary.md   # Post-audit delivery summary
├── delivery-seal.yaml          # Deterministic closure boundary
└── epic_audit.md               # Terminal audit report
```

### Agent Documentation Responsibilities

| Agent | Writes | Reads |
|-------|--------|-------|
| **Product Owner** | product/*, epics/*/details, acceptance-criteria, product decisions in design | architecture/10-quality |
| **Architect** | architecture/*, epics/*/design, native contracts, boundary plans | product/strategy, product/definition |
| **Developer** | code, tests, proof results, and required documentation targets | architecture/08-cross-cutting/*, epics/*/design, boundary plans |
| **Runner/finalizer** | durable implementation evidence and delivery seal | worker results, Git identities, audit artifacts, delivery manifest |

---

## 10. Architectural Decisions

### 10.1 Conversational Orchestrator and Bounded Workers

Public commands own user decisions, the product/final gates, status, and lifecycle.
Fresh Scope-managed provider processes perform one refinement phase,
implementation story or remediation batch, or audit-synthesis pass. The shared
runner enforces structured results, one mutation at a time, timeout,
cancellation, scoped write snapshots, and three-case recovery. Its small
`run.yaml` contains operational job summaries only; semantic state and closure
evidence remain in canonical epic and audit artifacts.

Worker routing is provider-local. A Codex installation reads
`plugins/scope/config/worker-policy.yaml`; a Claude installation reads
`.claude/config/worker-policy.yaml`. Each file defines `workers` (quality) and
`workers_on_budget`; the orchestrator selects the profile at run initialization.
The worker receives only its bounded job, never the routing profile.

Product refinement and diagnostic investigation use pinned `gpt-5.6-sol` or
`claude-opus-5` at high effort in both profiles. Other worker phases retain
`gpt-6-astra` or `claude-fable-5-1` with phase-specific effort. Routing is per
phase, not per-job complexity. The orchestration-only host session should also
use Sol high or Opus 5 high, selected in the host rather than by worker policy.

Completed jobs record the requested model and raw model IDs reported by Claude
Code `modelUsage` without maintaining a version-sensitive fallback-family
taxonomy. The Claude reviewer uses CLI text output directly; because that
transport does not report resolved model IDs, its receipt marks actual-model and
transparent-fallback status as unavailable rather than treating the requested
model as proof of execution.

Independent reviewers use shared `reviewer-policy.yaml`. Reviewer profile
(`default` or `budget`) and reviewer set (`standard` or `expanded`) are separate
choices bound into the durable review packet/attempt and receipt. Expanded
review can add Antigravity/Gemini 3.1 Pro High and OpenCode/Muse Spark 1.3 Contributor (high) without
changing the primary provider used for workers.

#### CodeGraph-assisted repository investigation

Workers and independent reviewers use the CodeGraph 1.5+ CLI, never its MCP.
The shared lifecycle policy initializes only a Git-ignored missing index and
prepares it once per command run. Implementation incrementally synchronizes it
before each new write job; refinement and read-only audit do not repeat the
lifecycle. Agents receive query-only commands and one compact
ready/degraded/unavailable state.
Focused `explore`/`node` queries accelerate navigation and relationship
analysis; direct source, tests, and validators remain authoritative. Affected
tests require explicit configured filters and supplement rather than replace
the workflow's required validation.

### 10.2 Native Contracts over Generic Prose

Epic refinement selects a project-appropriate machine-verifiable contract for
each important boundary. No language-specific contract type is mandatory.

### 10.3 Local Files for Documentation

Documentation is always local markdown files in `docs/`. Files are in git alongside code. Agents read/write directly. Follows Arc42+C4 and Atlassian Blueprint patterns for structure.

### 10.4 One Worker Per Story

A single agent implements the complete story. Splitting across agents creates context coordination complexity. Story boundaries align with technical component boundaries.

### 10.5 Test-as-Soon-as-Possible

Tests are written at the earliest possible point, not deferred to epic end. Fixing issues in closed stories is expensive (context lost). Early testing catches issues while context is fresh.

### 10.6 Architect-Led Story Breakdown

Architect leads story breakdown; Product Owner validates business alignment. Technical boundaries drive story structure (component alignment, dependencies).

### 10.8 Implementation Boundary Plan Intent Documentation

Each file in the plan has a 600-1200 character intent with 5-part structure:

1. **WHAT** (~100 chars): Core functionality
2. **WHY** (~150-250 chars): Architectural purpose
3. **RESPONSIBILITIES** (~150-250 chars): Key functions (3-5)
4. **DEPENDENCIES** (~100-150 chars): Module dependencies
5. **RELATED MODULES** (~100-150 chars): Positive delegation

Use positive delegation ("session encryption via SessionStore") instead of negation ("Does NOT handle encryption") to avoid confusing semantic search routing.

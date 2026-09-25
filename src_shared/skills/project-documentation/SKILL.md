---
name: project-documentation
description: Project documentation structure and templates for product, architecture, epic and releases.
---

# Project Documentation

Local markdown files. The architecture documentation follows Arc42 methodology. The product documentation follows Atlassian Product Documentation Blueprints. The provided templates will complement your internal knowledge.

Local files are the default and require no external service configuration. The
Atlassian name describes the product-documentation template format; it does not
select Confluence. Unless the active configuration explicitly selects an
external documentation backend, write under `./docs` and do not ask for a
Confluence space key, Atlassian URL, or Jira project key.

---

## Configuration

```yaml
# .scope/config.yaml
documentation:
  skill: project-documentation-file
  docs_path: ./docs
```

---

## Documentation folders and templates

### Templates

Templates are located in skills/project-documentation/ (try in ./.claude, and use ~/.claude as fallback):
- **Product:** `templates-product-atlassian/`
- **Technical:** `templates-technical-arc42-c4/`
- **Operations:** `templates-operations/`

Template names match the documentation structure below.

### Folder structure

Make sure the content is in the right folder.

Path selection rule:

- System architecture documentation goes directly under `docs/architecture/`.
  Example: `docs/architecture/01-intro.md`.
- Backend architecture documentation goes under `docs/architecture/backend/`.
  Example: `docs/architecture/backend/01-intro.md`.
- Frontend architecture documentation goes under `docs/architecture/frontend/`.
  Example: `docs/architecture/frontend/01-intro.md`.

If the requested scope is backend, create or update
`docs/architecture/backend/{01-intro.md ... 13-specs/}`. Do not answer with the
system-level path unless the user asked for system architecture.

If the requested scope is frontend, create or update
`docs/architecture/frontend/{01-intro.md ... 13-specs/}`. Do not answer with the
system-level path unless the user asked for system architecture.

Compatibility rule: legacy backend/frontend files may exist in older projects.
Always read them as source context when present:

- `docs/architecture/backend/overview.md`
- `docs/architecture/backend/services.md`
- `docs/architecture/backend/data.md`
- `docs/architecture/frontend/overview.md`
- `docs/architecture/frontend/structure.md`
- `docs/architecture/frontend/patterns.md`

Do not create new legacy files and do not treat them as the target format.
Going forward, new or updated backend/frontend architecture documentation uses
the component-specific `01-intro.md` through `13-specs/` trees below. When
touching legacy content, migrate or summarize it into the corresponding new
section instead of extending the legacy file.

```
docs/
├── product/
│   ├── overview.md
│   ├── strategy.md
│   ├── definition.md
│   ├── reference/
│   │   ├── feature-catalog.md
│   │   ├── use-case.md
│   │   ├── terminology.md
│   │   ├── ux-workflows.md
│   │   ├── terminology-data-model.md
│   │   └── apis-integrations.md 
│   └── decisions.md
├── architecture/
│   ├── 01-intro.md              # System-level Arc42
│   ├── 02-constraints.md
│   ├── 03-context.md
│   ├── 04-strategy.md
│   ├── 05-building-blocks.md
│   ├── 06-runtime.md
│   ├── 07-deployment.md
│   ├── 08-cross-cutting/
│   │   ├── domain.md
│   │   ├── security.md
│   │   ├── operations.md
│   │   └── testing.md
│   ├── 09-adr-summary.md        # Roll-up of all ADRs (all scopes)
│   ├── 10-quality.md
│   ├── 11-risks.md
│   ├── 12-glossary.md
│   ├── 13-specs/                # System-level machine-readable specs
│   │   ├── api/                 # OpenAPI contracts
│   │   ├── schemas/             # JSON/YAML schemas; no separate 14-schema
│   │   ├── database/            # SQL/NoSQL/vector/graph specs
│   │   └── errors/              # Error taxonomy and domain errors
│   ├── adr/                      # System-level ADRs
│   │   └── adr-template.md       # Shared template (all scopes)
│   ├── backend/                  # Backend-specific Arc42 tree
│   │   ├── 01-intro.md
│   │   ├── 02-constraints.md
│   │   ├── 03-context.md
│   │   ├── 04-strategy.md
│   │   ├── 05-building-blocks.md
│   │   ├── 06-runtime.md
│   │   ├── 07-deployment.md
│   │   ├── 08-cross-cutting/
│   │   ├── 09-adr-summary.md
│   │   ├── 10-quality.md
│   │   ├── 11-risks.md
│   │   ├── 12-glossary.md
│   │   ├── 13-specs/             # Backend API/schema/database/error specs
│   │   └── adr/                  # Backend-specific ADRs
│   └── frontend/                 # Frontend-specific Arc42 tree
│       ├── 01-intro.md
│       ├── 02-constraints.md
│       ├── 03-context.md
│       ├── 04-strategy.md
│       ├── 05-building-blocks.md
│       ├── 06-runtime.md
│       ├── 07-deployment.md
│       ├── 08-cross-cutting/
│       ├── 09-adr-summary.md
│       ├── 10-quality.md
│       ├── 11-risks.md
│       ├── 12-glossary.md
│       ├── 13-specs/             # Frontend API/schema/state/error specs
│       └── adr/                  # Frontend-specific ADRs
├── epics/{epic-id-with-filesafe-title}/
│   ├── details.md
│   ├── acceptance-criteria.md   # approved at Gate 1
│   ├── approvals.yaml           # Gate 1 record (orchestrator only)
│   ├── plan.md                  # living plan
│   ├── review.md                # refinement and audit findings
│   └── verification.yaml        # runner verification record
├── epics/_implemented/{epic}/    # epic folders archived on the epic branch
├── operations/
│   ├── overview.md                # System inventory, access points, contacts
│   ├── environments.md            # Environment matrix, infra details, access
│   ├── runbooks/
│   │   ├── deployment.md          # Build, deploy, rollback procedures
│   │   ├── secrets-management.md  # Key rotation, vault access, provisioning
│   │   ├── identity-access.md     # User provisioning, roles, SSO, auditing
│   │   ├── networking-security.md # WAF, firewall, TLS certs, DNS
│   │   ├── database.md            # Backup, restore, migration, scaling
│   │   ├── monitoring-alerting.md # Dashboards, alerts, on-call response
│   │   ├── scaling.md             # Horizontal/vertical scaling, auto-scaling
│   │   └── disaster-recovery.md   # DR plan, RTO/RPO, failover
│   ├── troubleshooting/
│   │   ├── common-issues.md       # Known issues and resolutions
│   │   └── escalation.md          # Escalation matrix, contacts, SLAs
│   └── maintenance/
│       ├── scheduled.md           # Regular maintenance tasks and schedules
│       └── upgrade-procedures.md  # OS, runtime, platform upgrade playbooks
├── lessons-learned/
│   ├── INDEX.md                   # One-liner per lesson, loaded on startup
│   └── {date}-{slug}.md           # Individual lessons (pattern/anti-pattern + RCA)
└── releases/{VERSION}/
    ├── record.md
    ├── notes.md
    └── postmortem.md
```

---

## Current-State Rule

`docs/architecture/` holds the current state only: arc42 sections, specs,
schemas, and ADRs. Superseded content is replaced, not appended. Nothing dated
or in-progress goes there.

Scope prescribes only these destinations:

| Material | Destination |
|---|---|
| Epic working papers (briefs, red-team outputs, handoff or session prompts, checkpoints, evidence) | The epic folder, `docs/epics/<epic>/`, archived with the epic to `docs/epics/_implemented/<epic>/` |
| Lasting architecture decisions | ADRs in the relevant `adr/` folder, rolled up in `09-adr-summary.md` |
| Product decisions | `docs/product/decisions.md` |
| Lessons | `docs/lessons-learned/` |
| Current behavior and contracts | arc42 sections and `13-specs/` |

Anything else not tied to one epic, such as cross-epic research or strategy
snapshots, is the project's own business; it only has to stay out of
`docs/architecture/`. `/implement` applies this rule when it finalizes an
epic's documentation, and `/audit_epic` checks it.

---

## Operations

### read(path)
```python
read(f"{root}/{path}")
```

### write(path, content, frontmatter=None)
```python
if frontmatter:
    content = f"---\n{yaml.dump(frontmatter)}\n---\n\n{content}"
write(f"{root}/{path}", content)
```

### search(pattern, path=None)
```python
grep(pattern, f"{root}/{path or ''}", recursive=True)
```

### list(path)
```python
glob(f"{root}/{path}/**/*.md")
```

---

## Product Documentation

### prd.md
**Template:** `templates-product-atlassian/prd.md`
**Content:** Starting Product Requirements Document with purpose, project posture, vision, users, problem, outcomes, scope, key features, workflows, product rules, acceptance criteria, constraints, success metrics, risks, launch plan, non-goals, and readiness checklist
**Owner:** Product Owner
**Readers:** Product Owner, Architect, SDET
**Trigger:** Before PRD refinement or when starting a new product from raw intent

### strategy.md
**Template:** `templates-product-atlassian/strategy.md`
**Content:** Vision, target markets, customer problems, scope boundaries, competitive landscape
**Owner:** Product Owner
**Readers:** Product Owner, Architect
**Trigger:** Quarterly, strategic shift

### definition.md
**Template:** `templates-product-atlassian/definition.md`
**Content:** Use cases (actor, goal, flow), capability map by theme
**Owner:** Product Owner
**Readers:** Product Owner, Architect, SDET
**Trigger:** Capabilities change

### reference/features.md
**Template:** `templates-product-atlassian/reference/features.md`
**Content:** Feature catalog with status, priority, release
**Owner:** Product Owner
**Readers:** All
**Trigger:** Features added/changed

### reference/terminology.md
**Template:** `templates-product-atlassian/reference/terminology.md`
**Content:** Terms, key entities, relationships, data lifecycle
**Owner:** Product Owner
**Readers:** Architect, SDET, Developer
**Trigger:** Data model changes

### reference/ui-workflows.md
**Template:** `templates-product-atlassian/reference/ux-workflows.md`
**Content:** Navigation, key screens, workflows
**Owner:** Product Owner
**Readers:** Architect, SDET
**Trigger:** UI changes

### reference/apis.md
**Template:** `templates-product-atlassian/reference/apis.md`
**Content:** External integrations, direction, data exchanged
**Owner:** Product Owner
**Readers:** Architect, Developer
**Trigger:** Integrations added

### decisions.md (PDR)
**Template:** `templates-product-atlassian/decisions.md`
**Format:** PDR-NNN with date, status, epic, context, decision, alternatives, consequences
**Owner:** Product Owner
**Trigger:** Per epic (Epic Housekeeping aggregates)

---

## Technical Documentation (Arc42)

### 01-intro.md
**Template:** `templates-technical-arc42-c4/architecture/01-intro.md`
**Content:** Purpose, stakeholders, top 3-5 quality goals
**Owner:** Architect

### 02-constraints.md
**Template:** `templates-technical-arc42-c4/architecture/02-constraints.md`
**Content:** Technical, organizational, conventions
**Owner:** Architect

### 03-context.md
**Template:** `templates-technical-arc42-c4/architecture/03-context.md`
**Content:** C4 L1 diagram, external interfaces, business/technical context
**Owner:** Architect | **Contributors:** DevOps

### 04-strategy.md
**Template:** `templates-technical-arc42-c4/architecture/04-strategy.md`
**Content:** High-level approach, patterns, technology decisions with rationale
**Owner:** Architect

### 05-building-blocks.md
**Template:** `templates-technical-arc42-c4/architecture/05-building-blocks.md`
**Content:** C4 L2/L3 diagrams, component responsibilities, interfaces
**Owner:** Architect

### 06-runtime.md
**Template:** `templates-technical-arc42-c4/architecture/06-runtime.md`
**Content:** Key scenarios, sequence diagrams, interaction flows
**Owner:** Architect | **Readers:** SDET (heavily)

### 07-deployment.md
**Template:** `templates-technical-arc42-c4/architecture/07-deployment.md`
**Content:** Infrastructure, deployment diagrams, strategies
**Owner:** Architect | **Contributors:** DevOps

### 08-cross-cutting/domain.md
**Template:** `templates-technical-arc42-c4/architecture/08-cross-cutting/domain.md`
**Content:** Domain entities, API conventions, transaction management
**Owner:** Architect | **Readers:** Developer (frequently)

### 08-cross-cutting/security.md
**Template:** `templates-technical-arc42-c4/architecture/08-cross-cutting/security.md`
**Content:** Auth, authz, data protection, compliance
**Owner:** Architect | **Contributors:** Security Reviewer

### 08-cross-cutting/operations.md
**Template:** `templates-technical-arc42-c4/architecture/08-cross-cutting/operations.md`
**Content:** Error handling, logging, caching, configuration
**Owner:** Architect | **Contributors:** DevOps

### 08-cross-cutting/testing.md
**Template:** `templates-technical-arc42-c4/architecture/08-cross-cutting/testing.md`
**Content:** Test levels, coverage targets, test data management
**Owner:** Architect | **Readers:** SDET, Developer

### 09-adr-summary.md
**Template:** `templates-technical-arc42-c4/architecture/09-adr-summary.md`
**Format:** ADR-NNN with date, epic, decision, context, consequences
**Owner:** Architect | **Trigger:** Per epic (Epic Housekeeping)

### 10-quality.md
**Template:** `templates-technical-arc42-c4/architecture/10-quality.md`
**Content:** Performance, scalability, availability, security, maintainability requirements
**Owner:** Architect | **Readers:** SDET, Developer

### 11-risks.md
**Template:** `templates-technical-arc42-c4/architecture/11-risks.md`
**Content:** Known risks, technical debt, mitigation strategies
**Owner:** Architect | **Contributors:** Developer, Security Reviewer

### 12-glossary.md
**Template:** `templates-technical-arc42-c4/architecture/12-glossary.md`
**Content:** Architecture terms, acronyms, patterns
**Owner:** Architect | **Readers:** All

---

## Architecture Decision Records (ADRs)

ADRs are scoped by component to keep decisions close to the code they affect.

### Scoping Rules

| Scope | Directory | When to Use | Prefix |
|-------|-----------|-------------|--------|
| **System** | `architecture/adr/` | Cross-cutting decisions (auth provider, deployment model, inter-service communication) | ADR- |
| **Backend** | `architecture/backend/adr/` | Backend-specific (database schema, queue strategy, LLM pipeline, Python patterns) | ADR- |
| **Frontend** | `architecture/frontend/adr/` | Frontend-specific (component library, state management, routing, testing framework) | ADR- |

**If in doubt:** Ask "does this decision affect only one component, or does it cross the boundary?" System-level if it crosses.

### Numbering — Single Global Sequence

**All ADRs share one global sequence** regardless of scope. This guarantees uniqueness and makes chronological ordering clear.

**Before creating a new ADR:**
1. Scan `09-adr-summary.md` and the `adr/` folders for the highest existing ADR number
2. Assign the next number in sequence

**File naming:** `ADR-{NNN}-{kebab-title}.md` in the scope's `adr/` directory.

**Example:** If the highest existing ADR is ADR-036, the next ADR is ADR-037 regardless of whether it's system, backend, or frontend scope.

### ADR Template
**Template:** `templates-technical-arc42-c4/architecture/adr/adr-template.md`
**Format:** ADR-NNN with date, status, scope, epic, context, decision, alternatives, consequences
**Owner:** Architect
**Trigger:** Any significant technical decision during epic work

### 09-adr-summary.md (Roll-Up)
The existing `09-adr-summary.md` aggregates ADRs from **all scopes** with links to the source files. Epic Housekeeping updates this file after each epic.

**Format:**
```
## System ADRs
- [ADR-001: Logging](adr/ADR-001-logging.md) — Accepted, 2026-02-08

## Backend ADRs
- [ADR-037: Queue Strategy](backend/adr/ADR-037-queue-strategy.md) — Accepted, 2026-02-16

## Frontend ADRs
- [ADR-038: Component Library](frontend/adr/ADR-038-component-library.md) — Accepted, 2026-02-19
```

---

## Component Architecture — Backend

Backend architecture uses its own Arc42-style tree under `docs/architecture/backend/`.
Use this for backend-specific runtime, service, data, integration, deployment,
quality, and contract decisions that would make the system-level architecture
too large or too implementation-specific.

If legacy backend files exist, read them first as context and migrate their
content into the new tree when the related topic is updated:

- `backend/overview.md` → `backend/01-intro.md`, `backend/03-context.md`, `backend/04-strategy.md`
- `backend/services.md` → `backend/05-building-blocks.md`, `backend/06-runtime.md`
- `backend/data.md` → `backend/13-specs/database/`, `backend/13-specs/schemas/`, and backend runtime/data-flow sections

### backend/01-intro.md through backend/12-glossary.md
**Template:** Use the matching `templates-technical-arc42-c4/architecture/{NN}-*.md` system template as the baseline and scope the content to backend concerns.
**Content:** Backend-specific purpose, constraints, context, strategy, building blocks, runtime, deployment, cross-cutting concerns, ADR summary, quality, risks, and glossary
**Owner:** Architect
**Readers:** Developer, SDET
**Trigger:** Backend service landscape, persistence, orchestration, integration, deployment, quality, or risk model changes

### backend/13-specs/
**Template:** `templates-technical-arc42-c4/architecture/13-specs/`
**Content:** Backend-owned OpenAPI contracts, JSON/YAML schemas, database specs, migration contracts, queue/message contracts, and error codes
**Owner:** Architect
**Readers:** Developer, SDET
**Trigger:** Backend API, schema, persistence, queue, integration, or error contract changes

Schemas belong under `13-specs/schemas/`; do not create a separate `14-schema`
folder. The `13-specs/` section is the canonical machine-readable contract
location for API contracts, data schemas, database specs, and error contracts.

---

## Component Architecture — Frontend

Frontend architecture uses its own Arc42-style tree under `docs/architecture/frontend/`.
Use this for frontend-specific runtime, routing, state, component, API
consumption, design-system, accessibility, performance, and testing decisions.

If legacy frontend files exist, read them first as context and migrate their
content into the new tree when the related topic is updated:

- `frontend/overview.md` → `frontend/01-intro.md`, `frontend/03-context.md`, `frontend/04-strategy.md`
- `frontend/structure.md` → `frontend/05-building-blocks.md`, `frontend/06-runtime.md`
- `frontend/patterns.md` → `frontend/08-cross-cutting/`, `frontend/10-quality.md`, and frontend test/error sections

### frontend/01-intro.md through frontend/12-glossary.md
**Template:** Use the matching `templates-technical-arc42-c4/architecture/{NN}-*.md` system template as the baseline and scope the content to frontend concerns.
**Content:** Frontend-specific purpose, constraints, context, strategy, building blocks, runtime, deployment, cross-cutting concerns, ADR summary, quality, risks, and glossary
**Owner:** Architect
**Readers:** Developer (frontend), SDET
**Trigger:** Frontend application structure, routing, state, rendering, deployment, quality, or risk model changes

### frontend/13-specs/
**Template:** `templates-technical-arc42-c4/architecture/13-specs/`
**Content:** Frontend-owned API consumption contracts, view-model schemas, route/state contracts, component interface specs, design-token contracts, and frontend error contracts
**Owner:** Architect
**Readers:** Developer (frontend), SDET
**Trigger:** Frontend API usage, state, route, component, design-token, validation, or error contract changes

Schemas belong under `13-specs/schemas/`; do not create a separate `14-schema`
folder. The `13-specs/` section is the canonical machine-readable contract
location for frontend API payloads, state/view-model schemas, component
contracts, and error contracts.

---

## Epic Documentation

An epic folder holds the epic's contract and records; durable knowledge goes
into the product and architecture docs above. Templates are in
`templates-technical-arc42-c4/epic/`.

### Epic Folder Hygiene
- Epic folders contain only Markdown and YAML files (plus the epic's working
  papers), never source code, generated code, caches, or binaries.
- `/implement` moves the folder to `docs/epics/_implemented/<epic>/` on the
  epic branch before the audit.

### details.md
**Template:** `templates-technical-arc42-c4/epic/details.md`
**Content:** Intent, scope, non-goals, user value, success measures, constraints, dependencies, and risks
**Owner:** `/prd_breakdown`

### acceptance-criteria.md
**Template:** `templates-technical-arc42-c4/epic/acceptance-criteria.md`
**Content:** Observable criteria under stable `AC-NNN` headings, including the error cases that matter; a `yaml scope` size estimate (production code lines, files, rationale); a "Not building" list; the baseline (existing test, lint, and type results, and failures that already exist); open product questions
**Owner:** Planner; approved by the user at Gate 1

### approvals.yaml
**Template:** `templates-technical-arc42-c4/epic/approvals.yaml`
**Content:** The Gate 1 record: blob hash and commit of the approved criteria, approval source and date
**Owner:** Orchestrator only, through `scope_check.py approve`

### plan.md
**Template:** `templates-technical-arc42-c4/epic/plan.md`
**Content:** Living plan in the ExecPlan style: approach referencing the durable docs, milestones, stories with complexity and estimates, validation commands, tests mapped to each criterion, documentation obligations, concepts, progress log, decision log, surprises
**Owner:** Planner, then implementer

### review.md
**Template:** `templates-technical-arc42-c4/epic/review.md`
**Content:** Every refinement and audit round with the reviewed commit, findings, dispositions, verification, adjudication, checks, and user decisions
**Owner:** Scope's runner appends rounds; authors edit dispositions

### verification.yaml
**Template:** `templates-technical-arc42-c4/epic/verification.yaml`
**Content:** One run per milestone: tested commit, commands, JUnit counts, skip reasons, criterion results, size, unavailable evidence, log location
**Owner:** Scope's runner only

---

## Release Documentation

### record.md
**Template:** `templates-technical-arc42-c4/release/record.md`
**Content:** Epic/story list, dates, links to summaries
**Owner:** Release Planner

### notes.md
**Template:** `templates-technical-arc42-c4/release/notes.md`
**Content:** Internal (technical), external (user-facing)
**Owner:** Release Documentation

### postmortem.md
**Template:** `templates-technical-arc42-c4/release/postmortem.md`
**Content:** Retrospective, lessons learned
**Owner:** User
**Trigger:** After release

---

## Operations Documentation

Practical runbook documentation for sysadmins and on-call engineers. Complements architecture docs (which describe **what** and **why**) with operational docs (which describe **how to**).

### overview.md
**Template:** `templates-operations/overview.md`
**Content:** System inventory, component map, access points, service dependencies, on-call contacts
**Owner:** Operations / DevOps
**Readers:** All ops staff, on-call engineers
**Trigger:** New components added, infrastructure changes

### environments.md
**Template:** `templates-operations/environments.md`
**Content:** Environment matrix (dev/staging/prod), infrastructure details, network config, access methods, cloud project mapping
**Owner:** Operations / DevOps
**Readers:** All ops staff, developers
**Trigger:** Environment added/changed, infrastructure migration

### runbooks/deployment.md
**Template:** `templates-operations/runbooks/deployment.md`
**Content:** Build, deploy, rollback procedures with actual commands. CI/CD pipeline description. Hotfix process.
**Owner:** Operations / DevOps
**Readers:** On-call engineers, developers
**Trigger:** Deployment process changes

### runbooks/secrets-management.md
**Template:** `templates-operations/runbooks/secrets-management.md`
**Content:** Secrets inventory, rotation schedule, create/rotate/revoke procedures, compromised secret response
**Owner:** Operations / Security
**Readers:** On-call engineers, security team
**Trigger:** New secrets added, rotation policy changes

### runbooks/identity-access.md
**Template:** `templates-operations/runbooks/identity-access.md`
**Content:** User provisioning, approval, deprovisioning, role matrix, SSO config, access audit
**Owner:** Operations / Security
**Readers:** On-call engineers, team leads
**Trigger:** IdP changes, role model changes

### runbooks/networking-security.md
**Template:** `templates-operations/runbooks/networking-security.md`
**Content:** WAF rules, firewall config, TLS certificate management, DNS procedures, security incident response
**Owner:** Operations / Security
**Readers:** On-call engineers, security team
**Trigger:** Network topology changes, security policy updates

### runbooks/database.md
**Template:** `templates-operations/runbooks/database.md`
**Content:** Database inventory, connection procedures, backup/restore, schema migration, scaling, performance investigation
**Owner:** Operations / DBA
**Readers:** On-call engineers, developers
**Trigger:** Database changes, migration process updates

### runbooks/monitoring-alerting.md
**Template:** `templates-operations/runbooks/monitoring-alerting.md`
**Content:** Monitoring stack, dashboards, alert definitions and response procedures, on-call procedures
**Owner:** Operations / SRE
**Readers:** On-call engineers
**Trigger:** New alerts, monitoring stack changes

### runbooks/scaling.md
**Template:** `templates-operations/runbooks/scaling.md`
**Content:** Current scaling config, horizontal/vertical scaling procedures, auto-scaling configuration
**Owner:** Operations / DevOps
**Readers:** On-call engineers
**Trigger:** Scaling policy changes, capacity planning

### runbooks/disaster-recovery.md
**Template:** `templates-operations/runbooks/disaster-recovery.md`
**Content:** RTO/RPO targets, backup strategy, disaster scenarios, failover procedures, DR testing plan
**Owner:** Operations / DevOps
**Readers:** All ops staff, management
**Trigger:** DR test results, infrastructure changes

### troubleshooting/common-issues.md
**Template:** `templates-operations/troubleshooting/common-issues.md`
**Content:** Known operational issues with symptoms, root causes, and step-by-step resolutions
**Owner:** Operations (updated by all who resolve issues)
**Readers:** On-call engineers
**Trigger:** After resolving any recurring issue

### troubleshooting/escalation.md
**Template:** `templates-operations/troubleshooting/escalation.md`
**Content:** Severity levels, escalation path, contact list, vendor support, communication plan
**Owner:** Operations / Engineering Manager
**Readers:** On-call engineers
**Trigger:** Team changes, vendor contract changes

### maintenance/scheduled.md
**Template:** `templates-operations/maintenance/scheduled.md`
**Content:** Maintenance calendar, regular task procedures, maintenance window process
**Owner:** Operations / DevOps
**Readers:** All ops staff
**Trigger:** Maintenance schedule changes

### maintenance/upgrade-procedures.md
**Template:** `templates-operations/maintenance/upgrade-procedures.md`
**Content:** Component upgrade inventory, per-component upgrade playbooks, dependency update policy
**Owner:** Operations / DevOps
**Readers:** All ops staff
**Trigger:** Major version upgrades planned

---

## Lessons Learned

Actionable patterns and anti-patterns captured from real work. Each lesson has a detection rule so Claude can recognize when it applies.

### INDEX.md
**Template:** `templates-operations/lessons-learned/INDEX.md`
**Content:** One-liner per lesson with detection rule summary and severity. Loaded on conversation start for context.
**Owner:** All (appended by the independently invoked `/lesson` command)
**Readers:** All agents — read on startup
**Trigger:** After `/lesson`

### {date}-{slug}.md
**Template:** `templates-operations/lessons-learned/lesson-template.md`
**Content:** Pattern or anti-pattern with detection rule, root cause analysis, and resolution
**Owner:** Whoever captures the lesson
**Readers:** All agents
**Trigger:** Created by `/lesson` command (interview or auto-detect mode)

### Lesson Structure

Each lesson contains:
- **Type**: Pattern (do this) or Anti-Pattern (avoid this)
- **Detection**: How Claude recognizes when this applies (code pattern, config state, error message, or situation)
- **Rule**: One actionable sentence
- **Root Cause**: Why it matters (2-4 sentences)
- **Resolution**: How to fix or implement correctly

### Context Loading

Projects should add to CLAUDE.md:
```
On conversation start, read docs/lessons-learned/INDEX.md for project-specific lessons.
```

---

## Agent Responsibilities

| Agent | Writes | Reads (Primary) |
|-------|--------|-----------------|
| **Product Owner / Planner** | product/*, epic acceptance criteria, PDRs in `product/decisions.md` | architecture/10-quality.md |
| **Architect / Planner** | architecture trees, ADR files, epic `plan.md` | product/{strategy,definition}.md |
| **SDET** | - | product/definition.md, architecture testing/quality docs, epic acceptance criteria and `plan.md` |
| **Developer / Implementer (backend)** | story code/tests and the plan's documentation obligations | backend architecture/ADRs, cross-cutting docs, epic `plan.md` |
| **Developer / Implementer (frontend)** | story code/tests and the plan's documentation obligations | frontend architecture/ADRs, cross-cutting docs, epic `plan.md` |
| **Security Reviewer** | security architecture and security ADRs | architecture security/quality docs and epic `plan.md` |
| **DevOps** | architecture/{07,08}/operations.md | architecture/{03,07,08}*.md, architecture/backend/{03-context,07-deployment}.md |
| **Operations (RE)** | operations/* | architecture/{07,08-cross-cutting}*.md, architecture/backend/*.md |

---

## Guidelines

**Templates:** Use templates from `templates-product-atlassian/`, `templates-technical-arc42-c4/`, and `templates-operations/` matching structure above

**File naming:**
- Lowercase with hyphens: `building-blocks.md`
- Epic folders: `epic-123/`
- Numbered Arc42: `01-` through `12-`

**Frontmatter (YAML):** Use for epic metadata only. Keep minimal.

**Token efficiency:**
- Agents load only pages they need (use direct paths from epic docs)
- No need to load entire guide during read operations

**Progressive disclosure:**
- Main page: overview, links
- Child pages: details >300 words

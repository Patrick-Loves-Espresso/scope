---
name: prd_breakdown
description: Convert completed PRD into implementable epics with dependencies. Creates system architecture, identifies epics, analyzes dependencies, creates documentation and tracking issues.
skills: project-documentation, project-tracking
---

# /prd_breakdown

Interactive conversion of Product Requirements Document (PRD) to implementable epics.

**Syntax:** `/prd_breakdown`

## Workflow Overview

**Documentation Philosophy:** Documentation backend is source of truth. Tracking system has summaries + links.

```
1. Load PRD Context (from documentation)
2. System Architecture (high-level technical design)
3. Identify Epic Candidates (informed by architecture)
4. Interactive Validation (user approves/modifies)
5. Identify Dependencies (architecture reasoning)
6. Create Epic Documentation Pages (source of truth)
7. Create Epic Issues in Tracking System (summary + link to documentation)
8. Update Product Definition (epic map summary)
```

## Prerequisites

Before running epic breakdown:
- Product Strategy complete (vision, markets, problems)
- Product Definition complete (use cases, capability map)
- Product Reference complete (4 children: Feature Catalog, Terminology & Data Model, UI & Workflows, APIs & Integrations)
- User has confirmed PRD is ready

**If PRD incomplete:** Direct user to `/prd_refine` first

---

## Phase 1: Load PRD Context

Load PRD content using `project-documentation` skill's `ai_search()` function for summarization.

### Required Content

```
Load via project-documentation skill:

1. Product Strategy
   - Vision statement
   - Target markets
   - Customer problems

2. Product Definition
   - Use cases (all)
   - Capability map (full)

3. Product Decisions
   - MVP & Phased Release Approach (CRITICAL for epic classification)
   - Success criteria and KPIs

4. Product Reference
   - Feature Catalog (features with status and priority)
   - Terminology & Data Model (domain terms and entities)
   - UI & Workflows (navigation and screens)
   - APIs & Integrations (external systems)
```

### Context Validation

After loading, verify:
- ✅ Capability map has 12+ capabilities
- ✅ Capabilities are grouped by theme
- ✅ Use cases exist for major capabilities
- ❌ If missing: Inform user PRD incomplete, recommend `/prd_refine`

---

## Phase 2: System Architecture

Create high-level system architecture before epic identification. This informs epic boundaries and identifies infrastructure needs.

### Architecture Deliverables

1. **C4 Context Diagram** - System and external actors/systems
2. **C4 Container Diagram** - High-level components (web app, API, database, etc.)
3. **Tech Stack Decisions** - Languages, frameworks, databases, infrastructure
4. **Cross-Cutting Concerns** - Auth, logging, monitoring, error handling
5. **Integration Points** - External APIs, third-party services
6. **Technical Specifications (docs/architecture/13-specs/)** - API contracts, schemas, database specs, error taxonomy

### Architecture Process

```
1. Analyze PRD for technical implications:
   - UI & Workflows → Frontend approach (SPA, SSR, mobile)
   - APIs & Integrations → Backend services needed
   - Terminology & Data Model → Database and storage needs
   - Feature Catalog → Scale and performance requirements

2. Draft C4 Context Diagram:
   - System boundary
   - Users/actors
   - External systems

3. Draft C4 Container Diagram:
   - Frontend container(s)
   - Backend container(s)
   - Database(s)
   - Message queues (if needed)
   - External service integrations

4. Identify tech stack:
   - Frontend: [framework, state management]
   - Backend: [language, framework]
   - Database: [type, specific technology]
   - Infrastructure: [cloud provider, deployment]

5. Identify cross-cutting concerns:
   - Authentication/Authorization
   - Logging/Monitoring
   - Error handling
   - Configuration management
   - These often become infrastructure epics
```

### Present Architecture to User

```
System Architecture Overview:

## C4 Context
[Mermaid diagram showing system + external actors]

## C4 Container
[Mermaid diagram showing major components]

## Tech Stack
- Frontend: [choice + rationale]
- Backend: [choice + rationale]
- Database: [choice + rationale]
- Infrastructure: [choice + rationale]

## Cross-Cutting Concerns
These typically become infrastructure epics:
- Authentication: [approach]
- Monitoring: [approach]
- [Other concerns]

## Integration Points
- [External system 1]: [integration approach]
- [External system 2]: [integration approach]

Does this architecture align with your vision? Any changes needed?
```

### User Response Handling

**"Approve"**: Proceed to Phase 3 (Epic Identification)

**"Change tech stack"**: Discuss alternatives, update, re-present

**"Add concern"**: Add to cross-cutting concerns, re-present

**"Question about [X]"**: Explain reasoning, adjust if needed

### Create Technical Specifications Directory

After user approval, create the `docs/architecture/13-specs/` directory structure:

```bash
# Create 13-specs directory structure
ARCH_DIR="./docs/architecture"
SPECS_DIR="$ARCH_DIR/13-specs"

mkdir -p "$SPECS_DIR/api"
mkdir -p "$SPECS_DIR/schemas/common"
mkdir -p "$SPECS_DIR/schemas/domain"
mkdir -p "$SPECS_DIR/database/sql"
mkdir -p "$SPECS_DIR/database/nosql"
mkdir -p "$SPECS_DIR/database/graph"
mkdir -p "$SPECS_DIR/database/vector"
mkdir -p "$SPECS_DIR/errors/by-domain"
```

Copy templates from the project-documentation skill:

```bash
TEMPLATE_DIR=".claude/skills/project-documentation/templates-technical-arc42-c4/architecture/13-specs"

# Copy API template
cp "$TEMPLATE_DIR/api/_template.yaml" "$SPECS_DIR/api/"

# Copy schema templates
cp "$TEMPLATE_DIR/schemas/common/error.yaml" "$SPECS_DIR/schemas/common/"
cp "$TEMPLATE_DIR/schemas/common/pagination.yaml" "$SPECS_DIR/schemas/common/"
cp "$TEMPLATE_DIR/schemas/domain/_template.yaml" "$SPECS_DIR/schemas/domain/"

# Copy database templates (based on tech stack decisions)
cp "$TEMPLATE_DIR/database/sql/_template.sql" "$SPECS_DIR/database/sql/"
cp "$TEMPLATE_DIR/database/nosql/_template.yaml" "$SPECS_DIR/database/nosql/"
cp "$TEMPLATE_DIR/database/graph/_template.yaml" "$SPECS_DIR/database/graph/"
cp "$TEMPLATE_DIR/database/vector/_template.yaml" "$SPECS_DIR/database/vector/"

# Copy error templates
cp "$TEMPLATE_DIR/errors/taxonomy.yaml" "$SPECS_DIR/errors/"
cp "$TEMPLATE_DIR/errors/by-domain/_template.yaml" "$SPECS_DIR/errors/by-domain/"

# Copy README
cp "$TEMPLATE_DIR/README.md" "$SPECS_DIR/"
```

**Note:** Only copy database templates relevant to the chosen tech stack. If using PostgreSQL only, skip nosql/graph/vector templates.

### Create Architecture Documentation

After user approval, create Product Architecture page:

```
create_page(
  title: "Product Architecture",
  parent: "Product Definition",
  tags: ["architecture", "product-architecture"],
  content: """
    ## System Context (C4 Level 1)
    [Mermaid diagram]

    ## Container Diagram (C4 Level 2)
    [Mermaid diagram]

    ## Tech Stack
    | Layer | Technology | Rationale |
    |-------|------------|-----------|
    | Frontend | ... | ... |
    | Backend | ... | ... |
    | Database | ... | ... |
    | Infrastructure | ... | ... |

    ## Cross-Cutting Concerns
    | Concern | Approach | Epic |
    |---------|----------|------|
    | Authentication | ... | TBD |
    | Monitoring | ... | TBD |

    ## Integration Points
    | System | Purpose | Protocol |
    |--------|---------|----------|
    | ... | ... | ... |

    ## Architecture Decision Records
    ADRs are added per epic when `/implement` finalizes its documentation.

    ## Technical Specifications
    Technical specifications are maintained in `docs/architecture/13-specs/`:
    - **API Contracts**: `docs/architecture/13-specs/api/` - OpenAPI 3.0 service definitions
    - **Schemas**: `docs/architecture/13-specs/schemas/` - JSON Schema domain entities
    - **Database**: `docs/architecture/13-specs/database/` - DDL and schema definitions
    - **Errors**: `docs/architecture/13-specs/errors/` - Error taxonomy and domain codes

    Each epic's plan names the specifications it adds or changes; `/implement` updates them before the audit.
  """
)
```

**Why architecture before epics:**
- Container diagram reveals natural epic boundaries
- Cross-cutting concerns become infrastructure epics
- Tech stack decisions affect how capabilities group
- Integration points may warrant dedicated epics

---

## Phase 3: Identify Epic Candidates

Use capability map, **architecture decisions from Phase 2**, and [Epic Identification Patterns](prd_breakdown/epic-identification-patterns.md) to propose epics.

**Architecture-Informed Epic Identification:**
- Container diagram components often map to epics
- Cross-cutting concerns become infrastructure epics
- Integration points may warrant dedicated epics
- Tech stack boundaries suggest natural groupings

### Identification Process

1. **Analyze capability map structure**
   - Note existing groupings/themes
   - Look for natural clusters

2. **Apply identification patterns**:
   - Capability-based (shared user goal)
   - Component-based (same system module)
   - Integration-based (same external service)
   - Data-based (entity lifecycle)
   - Infrastructure-based (foundational services)
   - User journey-based (end-to-end workflow)

3. **Draft epic list** with:
   - Epic title (clear, value-focused)
   - Capabilities included
   - Rationale (why these capabilities group together)
   - Estimated scope (2-8 weeks guideline)
   - **Release phase (MVP / Phase 2 / Phase 3 / Future)** - based on Product Decisions

### Quality Checks

For each proposed epic:
- ✅ Delivers standalone value?
- ✅ Semi-independent development possible?
- ✅ Clear acceptance criteria can be written?
- ✅ Not too broad (>8 weeks) or too granular (<2 weeks)?

---

## Phase 4: Interactive Validation

Present epic candidates to user for approval/modification.

### Presentation Format

**IMPORTANT:** Group epics by release phase to ensure post-MVP features aren't forgotten.

```
Proposed Epics from Capability Map:

═══════════════════════════════════════
MVP EPICS ([X] epics needed for v1.0)
═══════════════════════════════════════

Epic 1: [Title] ⭐ MVP
Capabilities:
- [Capability 1]
- [Capability 2]
...
Rationale: [Why these capabilities belong together]
Estimated Scope: [2-8 weeks]
Release Phase: MVP

Epic 2: [Title] ⭐ MVP
...

═══════════════════════════════════════
POST-MVP EPICS ([Y] epics for Phase 2+)
═══════════════════════════════════════

Epic 3: [Title] 📦 Phase 2
Capabilities:
- [Capability 3]
...
Rationale: [Enhancement to MVP functionality]
Estimated Scope: [2-8 weeks]
Release Phase: Phase 2

Epic 4: [Title] 🔮 Future
...

---

Summary:
- Total: [N] epics covering all [M] capabilities
- MVP: [X] epics (needed for v1.0 launch)
- Phase 2: [Y] epics (enhancements)
- Future: [Z] epics (long-term)

✅ All capabilities accounted for (no features forgotten)

Options:
1. Approve all epics as-is
2. Modify specific epics (split, merge, rename)
3. Adjust MVP scope (move epics between phases)
4. Add missing epics
5. Review dependencies before deciding

Which would you like to do?
```

### User Response Handling

**"Approve"**: Proceed to Phase 5 (Dependencies)

**"Modify [Epic X]"**:
- Ask what changes needed
- Re-apply identification patterns
- Re-present for approval

**"Split [Epic X]"**:
- Ask splitting criteria
- Create 2+ epics from original
- Validate new epic sizes
- Re-present

**"Merge [Epic X] and [Epic Y]"**:
- Combine capabilities
- Validate merged epic size (<8 weeks)
- Re-present

**"Add missing [area]"**:
- Identify capabilities for that area
- Create new epic
- Re-present

**"Review dependencies"**: Proceed to Phase 5, return to validation after

---

## Phase 5: Identify Dependencies

**IMPORTANT:** Only analyze dependencies for epics approved in Phase 4. If new epics are discovered during dependency analysis, return to Phase 4 for validation.

Apply [Dependency Patterns](prd_breakdown/dependency-patterns.md) to identify technical dependencies.

### Dependency Analysis

**Before starting:** Confirm epic list from Phase 4 validation (the approved epics).

For each pair of approved epics (A, B):

1. **Check for foundation dependencies**
   - Does B require A's infrastructure/capabilities?
   - Example: Profile Management requires Authentication

2. **Check for interface dependencies**
   - Do A and B interact at runtime?
   - Example: API Gateway routes to Microservices
   - If yes: Contract definition needed

3. **Check for data dependencies**
   - Do A and B share data models?
   - Example: Both read/write Repository entity
   - If yes: Schema coordination needed

4. **Check for integration dependencies**
   - Does B extend A's external integration?
   - Example: Subscriptions require Stripe Payment Integration

5. **Check for infrastructure dependencies**
   - Do epics require platform capabilities?
   - Example: All feature epics need Monitoring

6. **Check for UI/UX dependencies**
   - Do feature epics require design system?
   - Example: Dashboard UI needs Component Library

### Document Dependencies

For each dependency found:

```yaml
dependency:
  from: [Epic B Title]
  to: [Epic A Title]
  type: [foundation|interface|data|integration|infrastructure|ui]
  description: [Why B depends on A]
  resolution: [sequential|contract-first|parallel-with-mocks]
  risk: [high|medium|low]
```

### Dependency Visualization

Create dependency graph:

```
Foundation Epics (no dependencies):
┌────────────────────┐
│ Authentication     │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Profile Management │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Dashboard          │
└────────────────────┘

Parallel Track:
┌────────────────────┐
│ Design System      │  ← No dependency on Auth track
└────────────────────┘
```

### Consistency Check

**Before presenting dependencies:** Verify epic ordering only includes approved epics from Phase 4.

```
Approved epics from Phase 4:
- Epic 1: Authentication
- Epic 2: Profile Management
- Epic 3: Dashboard
- Epic 4: Notifications

✓ All epics in ordering are approved
✓ No new epics introduced during dependency analysis
```

**If new epics discovered:** Return to Phase 4 to validate them first.

### Present Dependencies to User

**CRITICAL:** Ensure "Recommended Epic Order" includes ONLY the approved epics. Count must match.

```
Dependency Analysis:

Foundation Dependencies (Sequential):
- Profile Management depends on Authentication (foundation)
- Dashboard depends on Profile Management (data)
- Notifications depends on Dashboard (interface)

Interface Dependencies (Contract-First):
- API Gateway and Microservices need API contract defined upfront

Parallel Tracks:
- Design System can develop in parallel with Auth track
- Infrastructure (Monitoring) can develop alongside features

Recommended Epic Order:
1. Authentication, Design System, Monitoring (parallel)
2. Profile Management
3. Dashboard, Component Library (parallel)
4. Notifications

**Verification:** [N] epics in ordering = [N] approved epics ✓

Does this dependency analysis look correct? Any adjustments needed?
```

### User Response Handling

**"Approve"**: Proceed to Phase 6 (Create Epic Documentation)

**"Dependency incorrect"**:
- Ask user to explain
- Re-analyze using patterns
- Update dependency graph
- Re-present

**"Change epic order"**:
- Update recommended order
- Validate dependencies still satisfied
- Re-present

---

## Phase 6: Create Epic Documentation Pages

**CRITICAL:** Documentation backend is the source of truth for all epic documentation. Tracking system will reference these pages.

Use `project-documentation` skill to create Epic Details parent page following Arc42-C4 pattern.

### Step 6.1: Determine Epic ID Prefix

Before creating epics, recommend a project-specific prefix for epic IDs. Derive it from the project name, product name, or domain:

```
I recommend using the epic-id prefix: [PREFIX]
(derived from [project name / product name / domain])

Examples of how epic IDs will look:
  [PREFIX]-001: Authentication System
  [PREFIX]-002: Profile Management
  [PREFIX]-003: Dashboard

Would you like to use this prefix, or choose a different one?
```

**Derivation rules:**
- Use the project name from `.scope/config.yaml` (`project.name`) if available
- Otherwise derive from the PRD product name
- Uppercase, 2-6 characters, alphanumeric only
- Examples: "Acme Invoicing" → `ACME`, "CodeReview Pro" → `CRP`, "Supply Chain Manager" → `SCM`

Store the approved prefix for use in all subsequent epic IDs.

### Step 6.2: Create Epic Documentation

For each approved epic (in dependency order):

1. **Load project-documentation skill**
   - Read `.claude/skills/project-documentation/SKILL.md`

2. **Assign epic ID** using the approved prefix:
   - Format: `{PREFIX}-###` (e.g., `{PREFIX}-001`, `{PREFIX}-002`)
   - Number based on priority order from Phase 5

3. **Create Epic Details parent page** (using template: `templates-technical-arc42-c4/epic/details.md`):
   ```
   create_epic_page(
     epic_id: "{PREFIX}-001",
     title: "{PREFIX}-001: [Epic Title]",
     tags: ["epic", "epic-details", "{PREFIX}-001"],
     content: """
       ## Overview
       [Vision statement from PRD]

       This epic addresses: [Customer problems from PRD]

       ## Capabilities
       - [Capability 1]
       - [Capability 2]
       - [Capability 3]
       ...

       ## Acceptance Criteria (High-Level)
       - [ ] [Criterion 1 based on capabilities]
       - [ ] [Criterion 2]
       - [ ] [Criterion 3]

       ## Dependencies
       **Depends on:**
       - [Epic ID]: [Epic Title] - [Why dependency exists]

       **Blocks:**
       - [Epic ID]: [Epic Title] - [What this epic unblocks]

       ## Release Phase
       ⭐ MVP / 📦 Phase 2 / 🔮 Future

       [Rationale for release phase assignment]

       ## Next Steps
       After this epic is created in tracking system, run:
       ```
       /epic_refine {PREFIX}-001
       ```
       to draft its acceptance criteria and plan.
     """
   )
   ```

4. **Store documentation page URL**:
   - Project-documentation skill returns page URL
   - Store for tracking system epic creation in Phase 7

### Epic Documentation Structure (Arc42-C4 Pattern)

**Created in Phase 6:**
- Epic Details (parent page) with tags: `epic`, `epic-details`, `{epic-id}`

**Added to the epic folder later:**
- `/epic_refine`: `acceptance-criteria.md` (approved at Gate 1), `approvals.yaml`, `plan.md`, `review.md`
- `/implement`: `verification.yaml`; ADRs, arc42 sections, and specs are updated in `docs/architecture/`, and the epic folder is archived to `docs/epics/_implemented/`

### What Goes in Epic Details (Source of Truth)

**Initial content (Phase 6):**
- Complete overview and problem statement
- Full capabilities list
- High-level acceptance criteria
- Dependency rationale and analysis
- Release phase justification

**Later stages** leave Epic Details unchanged; they write their own files next
to it (above).

---

## Phase 7: Create Epic Issues in Tracking System

**CRITICAL:** Tracking system issues are for tracking only. Keep descriptions minimal (1-2 sentences) and link to documentation for full details.

Use `project-tracking` skill to create epic issues.

### Epic Issue Creation Process

For each epic (with documentation page already created in Phase 6):

1. **Load project-tracking skill**
   - Read `.claude/skills/project-tracking/SKILL.md`

2. **Prepare minimal tracking issue content**:
   ```
   Title: [Epic Title]

   Description:
   📄 **Documentation:** [Documentation URL from Phase 6]

   [1-2 sentence summary of what this epic delivers]
   ```

3. **Create epic in tracking system**:
   ```
   create_epic(
     title: "[Epic Title]",
     description: """
       📄 **Documentation:** [Documentation URL]

       [Brief 1-2 sentence summary]
     """,
     priority: High/Medium/Low (based on release phase),
     labels: ["mvp"] for ⭐ MVP, ["phase-2"] for 📦, ["future"] for 🔮,
     dependencies: [List of epic keys this depends on]
   )
   ```

4. **Store epic key**:
   - Project-tracking skill returns epic key (e.g., "{PREFIX}-001")
   - This becomes the canonical epic ID

5. **Verify tracking → documentation link**:
   - Open epic in tracking system
   - Click documentation link to verify it works
   - All detailed content should be in documentation, not duplicated in tracking system

### What Goes in Tracking System (Tracking Only)

**Minimal content:**
- Epic title
- 1-2 sentence summary
- Link to documentation (source of truth)
- Priority (High/Medium/Low)
- Labels (mvp, phase-2, future)
- Status (Backlog initially)
- Dependencies (blocks/blocked-by relationships)

**Do NOT duplicate in tracking system:**
- ❌ Full overview or problem statement
- ❌ Complete capabilities list
- ❌ Detailed acceptance criteria
- ❌ Dependency rationale
- ❌ Release phase justification

**All detailed content lives in documentation backend.**

### Example Epic Issue

```
Title: Authentication System

Description:
📄 **Documentation:** [Documentation URL to Epic Details page]

Implement user authentication with registration, login, and session management.

Priority: High
Labels: mvp
Status: Backlog
Dependencies: None
```

### Completion Verification

After this phase, each epic has:
- ✅ Documentation page (Phase 6) - **Source of truth** with Epic Details parent
- ✅ Tracking issue (Phase 7) - **Tracking** with minimal summary + link

**Flow:**
```
Documentation: {PREFIX}-001 Epic Details (complete details)
  ↑ linked from ↑
Tracking: {PREFIX}-001 Epic (summary + tracking + link)
```

---

## Phase 8: Update Product Definition with Epic Map

Update Product Definition with epic breakdown summary.

**Template format:** Read [prd_breakdown/epic-map-template.md](prd_breakdown/epic-map-template.md) for complete epic map template with MVP/Phase 2/Future grouping.

**Key requirements:**
- Group epics by release phase (MVP / Phase 2 / Future)
- Include dependency graph (mermaid format)
- Include prioritization rationale
- Document total epic counts per phase

Update Product Definition page using `project-documentation` skill.

---

## Completion

After all epics created and documented:

```
Epic Breakdown Complete!

Created Epic Documentation Pages (source of truth):
✓ {PREFIX}-001: [Epic Title] - Epic Details (parent page)
✓ {PREFIX}-002: [Epic Title] - Epic Details (parent page)
✓ {PREFIX}-003: [Epic Title] - Epic Details (parent page)
... all [N] epic documentation pages

Note: /epic_refine adds the acceptance criteria and the plan to each epic folder.

Created Tracking System Epics (tracking with links to documentation):

⭐ MVP Epics ([X] epics for v1.0):
✓ {PREFIX}-001: Authentication → [Documentation link]
✓ {PREFIX}-002: Profile Management → [Documentation link]
✓ {PREFIX}-003: Dashboard → [Documentation link]

📦 Phase 2 Epics ([Y] epics post-MVP):
✓ {PREFIX}-004: Notifications → [Documentation link]
✓ {PREFIX}-005: Advanced Analytics → [Documentation link]

🔮 Future Epics ([Z] epics long-term):
✓ {PREFIX}-010: Mobile App → [Documentation link]

Updated Product Definition:
✓ Epic map grouped by release phase
✓ MVP vs Post-MVP clearly marked
✓ Dependency graph
✓ Recommended priority order

Documentation Architecture:
  Documentation Backend (source of truth) ← Tracking System (tracking + link)
  {PREFIX}-001 Epic Details (parent) ← {PREFIX}-001 Epic Issue
  └─ acceptance-criteria.md and plan.md added by /epic_refine

⚠️ REMINDER: Focus on MVP epics first! Post-MVP features will wait until after v1.0 launch.

Next Steps:
1. Review epic documentation (source of truth): [Documentation backend URL]
2. Review epic tracking: [Tracking system filter URL showing all epics]
3. Refine first MVP epic: /epic_refine {PREFIX}-001
4. Complete all [X] MVP epics before starting Phase 2
```

---

## Quality Checks

Before completing, verify:

### Consistency Checks
- [ ] **Epic count matches across phases:** Phase 4 approved = Phase 5 ordering = Phase 6 created
- [ ] **No extra epics introduced:** All epics in ordering were approved in Phase 4
- [ ] **No missing epics:** All approved epics are included in ordering and created
- [ ] **Epic titles consistent:** Same titles used in recommendations, ordering, and tracking creation

### MVP Classification Checks
- [ ] **Every epic has release phase:** MVP / Phase 2 / Phase 3 / Future
- [ ] **MVP scope reasonable:** Not too large (should be achievable)
- [ ] **Post-MVP features identified:** Clear separation between MVP and enhancements
- [ ] **All capabilities accounted for:** Every capability from PRD assigned to an epic
- [ ] **MVP count documented:** Clear count of MVP epics vs post-MVP epics

### Epic Quality
- [ ] Each epic has clear title (value-focused, not implementation)
- [ ] Each epic has 3-7 capabilities assigned
- [ ] Each epic scope is 2-8 weeks (rough estimate)
- [ ] Each epic delivers standalone value
- [ ] All capabilities from PRD are assigned to an epic

### Dependency Quality
- [ ] Foundation dependencies identified and documented
- [ ] No circular dependencies exist
- [ ] Parallel development opportunities identified
- [ ] Interface contracts flagged for early definition

### Documentation Quality
- [ ] Epic map in Product Definition
- [ ] Dependency graph clear and accurate
- [ ] Prioritization rationale explained
- [ ] Links to tracking epics work

---

## Error Handling

When encountering errors, read [prd_breakdown/error-patterns.md](prd_breakdown/error-patterns.md) for standard error messages and response patterns.

**Common scenarios:**
- PRD incomplete (missing capability map)
- Capability map too sparse (< 12 capabilities)
- User rejects all epic proposals

---

## Communication Style

**Progress indicators:**
- "Loading PRD context from documentation..."
- "Analyzing capability map (24 capabilities across 5 themes)..."
- "Identified 7 epic candidates. Validating..."

**Dependency explanations:**
- Use specific technical reasoning
- Reference architecture concepts from PRD
- Explain "why" dependencies exist

**User validation:**
- Present options clearly (approve/modify/add)
- Don't proceed without explicit approval
- Acknowledge user changes: "Updated Epic 3 to split Authentication and Authorization"

**Completion:**
- Summarize what was created
- Provide concrete next steps
- Include tracking links for visibility

---

## Cost Tracking

Track costs for this command using the agent summaries format.

### Baseline Entry (Start of Command)

At the start of the command, write a baseline entry to the tracking file:

```bash
# Create tracking file in .scope/tracking/commands/
TRACKING_DIR=".scope/tracking/commands"
mkdir -p "$TRACKING_DIR"
TRACKING_FILE="$TRACKING_DIR/prd_breakdown-$(date +%Y%m%d-%H%M%S).jsonl"

# Write baseline entry
echo '{"agent":"baseline","completed_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}' > "$TRACKING_FILE"
```

### Summary Entry (End of Command)

At the end of the command, write a summary entry and calculate costs:

```bash
# Write command summary entry
echo '{"agent":"prd_breakdown","session_id":"'"$CLAUDE_SESSION_ID"'","completed_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}' >> "$TRACKING_FILE"

# Calculate and store costs
src/commands/scripts/agents-tokens.sh --aggregate "$TRACKING_FILE" --storeInSummaries
```

### Output

The cost summary is appended to the tracking file and output to console:

```json
{
  "baseline": "2025-01-26T10:00:00Z",
  "file": ".scope/tracking/commands/prd_breakdown-20250126-100000.jsonl",
  "agents": [
    {"agent": "prd_breakdown", "session_id": "abc123", "cost_usd": 0.1247}
  ],
  "total_cost_usd": 0.1247
}
```

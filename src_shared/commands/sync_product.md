---
name: sync_product
description: Sync product documentation when product scope, features, or terminology changes. Run less frequently than /sync_architecture.
args: "[epic-id]"
skills: project-documentation
---

# /sync_product

Sync product documentation when implementation reveals product-level changes. Run when:
- New capability added (not just implemented - genuinely new to product)
- Terminology changed
- Product scope shifted
- User workflows changed significantly

**Syntax:** `/sync_product [epic-id]`

If `epic-id` provided, focuses on changes from that epic. Otherwise, syncs all pending product changes.

## When to Run

| Trigger | Example |
|---------|---------|
| New feature capability | Added real-time notifications (not planned in PRD) |
| Terminology change | Renamed "Workspace" to "Project" throughout |
| Scope expansion | Added mobile support |
| UX flow change | Redesigned onboarding workflow |
| Integration added | New third-party integration |

**Note:** Most epics don't require product sync - they implement what was already in the PRD. Only run when implementation genuinely changes product definition.

## What Gets Synced

```
/sync_product [epic-id]

1. FEATURE CATALOG
   └── docs/product/reference/feature-catalog.md
   └── New features, status changes, release assignments

2. TERMINOLOGY & DATA MODEL
   └── docs/product/reference/terminology.md
   └── New terms, renamed entities, changed definitions

3. UI & WORKFLOWS
   └── docs/product/reference/ui-workflows.md
   └── New screens, changed navigation, updated flows

4. APIs & INTEGRATIONS
   └── docs/product/reference/apis-integrations.md
   └── New external integrations, changed interfaces

5. PRODUCT DECISIONS (if scope changed)
   └── docs/product/decisions.md
   └── Capability scope changes, phasing updates
```

---

## Execution

### Step 0: Initialize

```bash
EPIC_ID="${1:-}"  # Optional

if [ -n "$EPIC_ID" ]; then
  # Active or implemented (archived) epic folder
  EPIC_FOLDER=$(find docs/epics docs/epics/_implemented -mindepth 1 -maxdepth 1 -type d \
    -iname "${EPIC_ID}*" ! -name "_*" 2>/dev/null | head -1)
  if [ -z "$EPIC_FOLDER" ]; then
    echo "Epic not found in docs/epics/ or docs/epics/_implemented/"
    exit 1
  fi
fi

PRODUCT_DIR="docs/product"
```

### Step 1: Detect Product Changes

If epic provided, analyze epic for product-impacting changes:

```python
# Sources to check
epic_acceptance = Read(f"{epic_folder}/acceptance-criteria.md")  # approved scope and "Not building"
epic_plan = Read(f"{epic_folder}/plan.md")                      # approach, concepts, decision log
epic_review = Read(f"{epic_folder}/review.md")                  # accepted or rejected scope findings
epic_verification = Read(f"{epic_folder}/verification.yaml")    # what was verified on which commit

# Detect product-level changes
changes = {
    "new_features": [],      # Features not in original PRD
    "terminology": [],       # New or renamed terms
    "ui_changes": [],        # New screens or workflows
    "integrations": [],      # New external integrations
    "scope_changes": []      # Capability scope shifts
}
```

If no epic provided, scan all recent epics for unsynced product changes.

---

## Phase 1: Feature Catalog

**Goal:** Update `docs/product/reference/feature-catalog.md` with implementation reality.

### 1.1 Load Current Catalog

```python
catalog = Read("docs/product/reference/feature-catalog.md")
documented_features = parse_feature_table(catalog)
```

### 1.2 Compare with Implementation

```python
# From epic implementation
implemented_features = extract_features_from_epic(epic_id)

# Detect changes
new_features = implemented_features - documented_features
status_changes = detect_status_changes(documented_features, implemented_features)
```

### 1.3 Propose Updates

```
Feature Catalog Changes:

NEW FEATURES (implemented but not in catalog):
  + Real-time notifications
  + Bulk export

STATUS CHANGES:
  ~ User authentication: Planned → Released (v1.0)
  ~ API rate limiting: Planned → In Dev

Apply these changes? [yes / review each / skip]
```

### 1.4 Update Catalog

```markdown
| Feature | Description | Status | Priority | Release |
|---------|-------------|--------|----------|---------|
| Real-time notifications | Push notifications for events | Released | High | v1.0 |
```

---

## Phase 2: Terminology & Data Model

**Goal:** Update `docs/product/reference/terminology.md` with new/changed terms.

### 2.1 Detect Terminology Changes

Sources:
- `docs/architecture/13-specs/schemas/domain/` - new entities
- Epic documentation - new terms introduced
- Code changes - renamed concepts

### 2.2 Propose Updates

```
Terminology Changes:

NEW TERMS:
  + Notification: A system-generated alert sent to users
  + Channel: Delivery method for notifications (email, push, in-app)

RENAMED:
  ~ Workspace → Project (throughout system)

UPDATED DEFINITIONS:
  ~ User: Added "notification_preferences" attribute

Apply these changes? [yes / review each / skip]
```

### 2.3 Update Terminology

```markdown
| Term | Definition | Usage Example |
|------|------------|---------------|
| Notification | A system-generated alert sent to users | "User receives notification when task assigned" |
| Channel | Delivery method for notifications | "Configure email channel for weekly digest" |
```

---

## Phase 3: UI & Workflows

**Goal:** Update `docs/product/reference/ui-workflows.md` if UX changed.

### 3.1 Detect UI Changes

Sources:
- Frontend components added/modified
- Route changes
- Epic acceptance criteria with UI requirements

### 3.2 Propose Updates

```
UI & Workflow Changes:

NEW SCREENS:
  + Notification Settings (/settings/notifications)
  + Notification Center (drawer component)

UPDATED WORKFLOWS:
  ~ Onboarding: Added notification opt-in step

NAVIGATION CHANGES:
  + Settings → Notifications (new menu item)

Apply these changes? [yes / review each / skip]
```

---

## Phase 4: APIs & Integrations

**Goal:** Update `docs/product/reference/apis-integrations.md` if integrations changed.

### 4.1 Detect Integration Changes

Sources:
- `docs/architecture/13-specs/api/` - new external-facing APIs
- `docs/architecture/03-context.md` - external dependencies
- Epic implementation - third-party integrations

### 4.2 Propose Updates

```
API & Integration Changes:

NEW INTEGRATIONS:
  + SendGrid (email delivery)
  + Firebase Cloud Messaging (push notifications)

NEW EXTERNAL APIs:
  + Webhook API: Allow external systems to receive events

Apply these changes? [yes / review each / skip]
```

---

## Phase 5: Product Decisions

**Goal:** Update `docs/product/decisions.md` if capability scope changed.

### 5.1 Detect Scope Changes

Triggers:
- Feature moved between phases (v1.0 → v2.0 or vice versa)
- New capability not in original PRD
- Capability removed or deprioritized

### 5.2 Propose Updates

```
Product Decision Changes:

SCOPE CHANGES:
  ~ Notifications: Moved from v2.0 to v1.0 (user demand)
  + Bulk operations: Added to v1.0 (emerged from implementation)

Update Product Decisions? [yes / skip]
```

---

## Completion Output

```
Product Sync Complete

Summary:
├── Feature Catalog
│   ├── New features: [N]
│   └── Status updates: [N]
│
├── Terminology
│   ├── New terms: [N]
│   ├── Renamed: [N]
│   └── Updated: [N]
│
├── UI & Workflows
│   ├── New screens: [N]
│   └── Updated workflows: [N]
│
├── APIs & Integrations
│   ├── New integrations: [N]
│   └── New external APIs: [N]
│
└── Product Decisions
    └── Scope changes: [N or "No changes"]

Product documentation is now current.
```

---

## No Changes Needed

If no product-level changes detected:

```
Product Sync: No Changes Needed

The epic {epic-id} implemented features as designed in the PRD.
No product documentation updates required.

Tip: /sync_product is for product-level changes (new capabilities,
terminology shifts, UX redesigns). For architecture sync after
any epic, use /sync_architecture.
```

---

## Full Product Audit

For a comprehensive product documentation review (not tied to a specific epic):

```bash
/sync_product --audit
```

This mode:
1. Compares all product docs against implemented system
2. Identifies all gaps and drift
3. Proposes comprehensive updates
4. Useful for periodic documentation health checks

---

## Communication Style

**Progress indicators:**
- "Phase 1/5: Feature Catalog"
- "Phase 2/5: Terminology & Data Model"
- etc.

**Approval gates:**
- Present all detected changes
- Allow review of each change
- Skip phases with no changes

**Guidance:**
- Explain what triggered each change
- Reference epic or implementation source
- Flag items needing product owner review

---
name: decision
description: Record architectural (ADR) or product (PDR) decisions with intent and rationale. With args, interviews the user. Without args, auto-detects decisions from recent durable repository evidence.
args: "[decision description]"
skills: project-documentation
---

# /decision

Capture decisions and their rationale so they survive beyond the conversation. Works in two modes:

**With argument:** `/decision Use SQLite instead of PostgreSQL for local-first simplicity`
- Interviews you to understand the full intent and "why"
- Challenges you if the decision could create issues
- Saves only the decision and rationale (not the full conversation)

**Without argument:** `/decision`
- Auto-detects decisions from recent Git and canonical epic evidence
- Presents candidates for discussion, refinement, and approval
- You choose which to keep, edit, or discard

---

## Mode 1: Interview (With Argument)

**Trigger:** `/decision {description}`

### Step 1: Classify

Determine whether this is an ADR or PDR using this decision tree:

```
Does this change what the user sees or can do?
  → Yes → PDR
  → No  → Does this change how the system is built or operated?
            → Yes → ADR
            → No  → Probably not worth recording
  → Both → ADR with product consequences noted in Consequences section
```

**Core test:** "Would a non-technical stakeholder care about this decision?"
- Yes → PDR (or both)
- No → ADR

| | ADR (Architecture) | PDR (Product) |
|---|---|---|
| **Answers** | _How_ do we build it? | _What_ do we build and _for whom_? |
| **Stakeholder** | Developer, architect, ops | Product owner, user, business |
| **User impact** | Invisible to user (or indirect) | Directly changes user experience |
| **Examples** | PostgreSQL vs SQLite, async vs sync, Alembic for migrations, retry strategy, caching layer | Remove export from MVP, require email verification, support Canada only, free tier limits, change onboarding flow |

**Edge cases:** Some decisions are both technical and product-facing. For example, "Use Gemini Flash instead of Pro for synthesis" is a technology choice but affects output quality the user sees. In that case, **record as ADR and note the product consequence in the Consequences section.** Don't duplicate as two entries.

| Type | Destination |
|------|-------------|
| **ADR** | The scope's `adr/` folder (`docs/architecture/adr/`, `backend/adr/`, or `frontend/adr/`), listed in `09-adr-summary.md` |
| **PDR** | `docs/product/decisions.md` |

A reversible implementation choice inside one epic is not an ADR: it belongs
in that epic's `plan.md` decision log.

If still unclear after applying the decision tree, ask: "Does this decision change what the user experiences, or how the system is built?"

### Step 2: Identify Scope

```python
# Determine which epic this belongs to (if any)
active_epics = Glob("docs/epics/*/details.md")
# Exclude _implemented/ and _deferred_superseded/
active_epics = [e for e in active_epics if "/_" not in e]

if len(active_epics) == 1:
    epic_dir = active_epics[0].split("/")[-2]
    # Confirm with user
elif len(active_epics) > 1:
    # Ask user which epic, or "system-level"
else:
    # System-level decision
```

### Step 3: Interview for Intent and "Why"

Ask the user these questions (adapt based on what the initial description already covers):

**For the intent:**
1. "What exactly are you deciding?" — Get the precise decision statement
2. "What problem does this solve?" — The context/trigger

**For the "why":**
3. "Why this approach over alternatives?" — Rationale
4. "What alternatives did you consider (even briefly)?" — At least one alternative

**Challenge if necessary:**
5. Review the decision against:
   - Existing ADRs in the epic/system — does it contradict any?
   - Architecture constraints (`docs/architecture/02-constraints.md`)
   - Known risks (`docs/architecture/11-risks.md`)

6. If you see potential issues, raise them:
   - "This contradicts ADR-{N} which decided {X}. Are you superseding it?"
   - "This could create a problem with {X} because {Y}. Are you aware?"
   - "Have you considered {alternative} which would avoid {tradeoff}?"

7. If the user confirms after your challenge, proceed. If they change their mind, re-interview.

### Step 4: Save

**Save ONLY the structured decision** — not the interview conversation.

**For an ADR** — create `ADR-{NNN}-{kebab-title}.md` in the scope's `adr/`
folder and add it to `09-adr-summary.md`. Name the epic when one prompted it:

```markdown
## ADR-{NNN}: {Title}

**Date:** {today}
**Status:** Accepted
**Scope:** {System | Backend | Frontend}
**Epic:** {epic-id or "System-level"}

### Context

{Problem statement from interview — 2-4 sentences}

### Decision

{What was decided and why — 2-4 sentences}

### Alternatives Considered

- **{Alternative 1}**: {Why rejected — 1 sentence}
- **{Alternative 2}**: {Why rejected — 1 sentence}

### Consequences

{Key tradeoffs — 2-3 bullet points}
```

**For a PDR** — append to `docs/product/decisions.md`:

```markdown
## PDR-{NNN}: {Title}

**Date:** {today}
**Status:** Accepted

### Context

{Product question or tradeoff — 2-4 sentences}

### Decision

{What product direction was chosen and why — 2-4 sentences}

### Alternatives Considered

- **{Alternative 1}**: {Why rejected — 1 sentence}

### Consequences

**User Impact:** {1-2 sentences}
**Business Impact:** {1-2 sentences}
```

### Step 5: ADR/PDR Numbering

- ADR: Read `docs/architecture/09-adr-summary.md` and the `adr/` folders for
  the highest number. Use the next number of the single global sequence.
- PDR: Read `docs/product/decisions.md` for the highest number.

### Step 6: Cross-Post (if available)

```python
# If Obsidian MCP is available, cross-post
if mcp_available("obsidian"):
    # Create or append to relevant note in vault
    obsidian.write(f"decisions/{decision_type}-{number}-{slug}.md", content)
```

### Step 7: Confirm

Tell the user:
```
Saved {ADR|PDR}-{NNN}: {Title}
  Location: {file path}
  Epic: {epic-id or "System-level"}
  Type: {Architecture | Product}
```

---

## Mode 2: Auto-Detect (Without Argument)

**Trigger:** `/decision` (no args)

### Step 1: Determine Time Window

```python
# Use the most recent committed decision artifact as the discovery boundary.
last_decision = Bash(
    "git log -1 --format='%aI' -- docs/architecture/adr/ docs/architecture/backend/adr/ "
    "docs/architecture/frontend/adr/ docs/product/decisions.md"
).strip()

if last_decision:
    since_date = last_decision
else:
    # Fall back to the active epic history, or the last 30 days.
    epic_first_commit = Bash("git log --reverse --oneline -- docs/epics/{EPIC_DIR}/ | head -1")
    if epic_first_commit:
        since_date = Bash(f"git log --format='%aI' {epic_first_commit.split()[0]}")
    else:
        since_date = "30 days ago"
    print(f"No committed decision boundary found. Scanning since {since_date}.")
```

**Important:** Already-recorded decisions (ADRs, `decisions.md`, and the
decision logs of epic `plan.md` files) must be filtered out. Read existing decision
files and exclude any candidates that match existing entries by title or
content.

### Step 2: Scan for Decision Candidates

Analyze the following sources for decisions made in the time window:

**Source 1: Git history**
```bash
# Commits since the durable decision boundary
git log --oneline --since="{since_date}"

# File changes (what was modified)
git diff --stat HEAD~{N}..HEAD
```

Look for signals in commit messages and diffs:
- New dependencies added (package.json, requirements.txt, pyproject.toml)
- New configuration entries (config/*.yaml)
- Schema changes (migrations, new tables/columns)
- New patterns introduced (new base classes, utilities, middleware)
- Infrastructure changes (Dockerfile, CI/CD, IaC)

**Source 2: Epic plans and reviews**
```python
plans = Glob("docs/epics/**/plan.md")      # decision logs: which choices proved lasting
reviews = Glob("docs/epics/**/review.md")  # adjudicated findings that settled a design question
```

**Source 3: Code patterns**
```python
# Scan for technology introductions
new_imports = Grep("^import |^from ", glob="*.py", recent_only=True)
new_packages = Grep("new dependency", glob="requirements*.txt")

# Scan for ADR-worthy patterns
# New base classes, protocols, abstract classes
# New config sections
# New error types
# New API endpoints
```

**Source 4: Existing docs gap**
```python
# Compare what's documented vs. what's in code
existing_decisions = Read("docs/epics/{active-epic}/plan.md")  # its decision log
# Are there technologies/patterns in code not covered by ADRs?
```

### Step 3: Classify and Present Candidates

For each candidate, apply the classification heuristic:
- "Does this change what the user sees or can do?" → PDR
- "Does this change how the system is built or operated?" → ADR
- Both → ADR with product consequences noted

Present discovered decisions to the user as a numbered list:

```
Decisions detected since {date}:

  1. [ADR] Added Redis for session caching
     Signal: redis added to requirements.txt, new RedisStore class
     Why (inferred): Performance — avoid DB round-trips for sessions

  2. [ADR] Switched from sync to async HTTP client
     Signal: httpx replaced requests in 4 files
     Why (inferred): Non-blocking I/O for concurrent API calls

  3. [PDR] Removed user export feature from MVP scope
     Signal: export/ directory deleted, AC updated
     Why (inferred): Scope reduction for timeline

  4. [ADR] Used Alembic for migrations (instead of raw SQL)
     Signal: alembic/ directory created, alembic.ini added
     Why (inferred): Repeatable migrations across environments

  5. [ADR+Product] Switched from Gemini Pro to Flash for synthesis
     Signal: model config changed, cost-per-entity dropped
     Why (inferred): Cost reduction — but may affect output quality
     Note: Recording as ADR with product consequences

Actions for each:
  [keep] — Record as-is (or edit the "why")
  [edit] — Discuss and refine before recording
  [skip] — Not a decision worth recording
  [add]  — I missed a decision, let me add it
```

### Step 4: Interview Each Kept Decision

For each decision the user keeps or wants to edit:
- Confirm or refine the "why"
- Challenge if necessary (same as Mode 1, Step 3)
- If user edits, update the description

For `[add]`, switch to Mode 1 interview for that decision.

### Step 5: Save All Approved Decisions

Save each approved decision using the same format as Mode 1, Step 4.

### Step 6: Summary

```
Recorded {N} decisions:
  - ADR-{NNN}: {Title} → {file}
  - PDR-{NNN}: {Title} → {file}
  - ...

Skipped {M} candidates.
```

---

## Key Principles

1. **Capture intent and "why" only** — not the deliberation process, not the conversation
2. **Challenge the user** — if a decision contradicts existing ADRs, introduces risk, or has a better alternative, say so
3. **Lightweight** — this should take 2-5 minutes per decision, not 30 minutes
4. **Auto-detect is best-effort** — it's OK to miss some; the user can always add manually
5. **Numbering is global** — ADRs share a single sequence across system/backend/frontend/epic scopes

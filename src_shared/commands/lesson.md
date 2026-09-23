---
name: lesson
description: Record lessons learned as detectable patterns/anti-patterns with RCA. With args, interviews the user. Without args, auto-detects lessons from recent durable repository evidence.
args: "[lesson description]"
skills: project-documentation
---

# /lesson

Capture lessons learned as actionable patterns so Claude and the team don't repeat mistakes. Each lesson is a **detection rule** — a pattern to follow or an anti-pattern to avoid, with the root cause of why it matters.

**With argument:** `/lesson SQLite WAL mode must be enabled before concurrent writes`
- Interviews you to extract the pattern, root cause, and prevention
- Produces a lesson that Claude can match against in future work

**Without argument:** `/lesson`
- Auto-detects lessons from recent work (multi-cycle fixes, test failures, corrections, workarounds)
- Presents candidates for discussion, refinement, and approval

---

## Lesson Format

Every lesson follows this structure (regardless of how it's captured):

```markdown
## L-{NNN}: {Title}

**Date:** {today}
**Epic:** {epic-id or "General"}
**Domain:** {component/area — e.g., "database", "auth", "crawling", "deployment"}
**Severity:** {Critical | Important | Informational}

### Pattern / Anti-Pattern

**Type:** {Pattern (do this) | Anti-Pattern (avoid this)}

**Detection:** {How Claude can recognize when this applies — specific code patterns,
config states, error messages, or situations that trigger this lesson}

**Rule:** {The actionable rule — one clear sentence}

### Root Cause

{Why this matters. What went wrong or could go wrong. 2-4 sentences maximum.
Include the specific incident or debugging session that surfaced this.}

### Resolution

{How to fix it when detected, or how to implement the pattern correctly.
Include commands or code snippets if relevant.}
```

---

## Mode 1: Interview (With Argument)

**Trigger:** `/lesson {description}`

### Step 1: Understand the Lesson

Ask the user (adapt based on what the description already covers):

1. "What happened?" — The incident or problem that surfaced this lesson
2. "What was the root cause?" — Why it happened (not just what went wrong)
3. "How did you fix it?" — The resolution or correct approach

### Step 2: Extract the Pattern

4. "Is this a pattern (something to do) or an anti-pattern (something to avoid)?"
5. "How would Claude detect when this applies in the future?" — The detection rule:
   - A code pattern? (e.g., "When opening SQLite in a service that uses async workers...")
   - A configuration state? (e.g., "When config has concurrent_workers > 1 but no WAL mode...")
   - An error message? (e.g., "When you see 'database is locked'...")
   - A situation? (e.g., "When adding a new Gemini API call without retry logic...")

6. "What's the one-sentence rule?" — Force a concise, actionable statement:
   - Good: "Enable WAL mode on SQLite before any concurrent writes"
   - Bad: "SQLite can have issues with concurrent access" (too vague to act on)

### Step 3: Validate

- Verify the lesson is **actionable** — Claude could detect the situation and apply the rule
- Verify the lesson is **not already documented** — check existing lessons:
  ```python
  existing = Glob("docs/lessons-learned/*.md")
  # Also check INDEX.md for duplicates
  ```
- If the lesson overlaps with an existing one, suggest merging instead of duplicating

### Step 4: Determine Severity

| Severity | Criteria |
|----------|----------|
| **Critical** | Cost >1 hour of debugging, data loss risk, production impact |
| **Important** | Cost 15-60 min of debugging, recurring issue, affects multiple files |
| **Informational** | Good-to-know, optimization, minor confusion |

### Step 5: Save

**Primary location:** `docs/lessons-learned/{date}-{slug}.md`

```python
slug = lesson_title.lower().replace(" ", "-")[:50]
file_path = f"docs/lessons-learned/{today}-{slug}.md"
Write(file_path, formatted_lesson)
```

**Update index:** Append one-liner to `docs/lessons-learned/INDEX.md`:

```markdown
- [L-{NNN}: {Title}]({date}-{slug}.md) — {Detection rule summary} [{Severity}]
```

**Cross-post to Obsidian** (if available):
```python
if mcp_available("obsidian"):
    obsidian.write(f"lessons-learned/L-{number}-{slug}.md", content)
```

### Step 6: Confirm

```
Saved L-{NNN}: {Title}
  Location: docs/lessons-learned/{date}-{slug}.md
  Type: {Pattern | Anti-Pattern}
  Detection: {one-line summary of when this applies}
  Severity: {Critical | Important | Informational}
```

---

## Mode 2: Auto-Detect (Without Argument)

**Trigger:** `/lesson` (no args)

### Step 1: Determine Time Window

```python
# Use the most recent committed lesson as the discovery boundary.
last_lesson = Bash(
    "git log -1 --format='%aI' -- docs/lessons-learned/"
).strip()

if last_lesson:
    since_date = last_lesson
else:
    # Fall back to the active epic history, or the last 30 days.
    epic_first_commit = Bash("git log --reverse --oneline -- docs/epics/{EPIC_DIR}/ | head -1")
    if epic_first_commit:
        since_date = Bash(f"git log --format='%aI' {epic_first_commit.split()[0]}")
    else:
        since_date = "30 days ago"
    print(f"No committed lesson boundary found. Scanning since {since_date}.")
```

**Important:** Already-recorded lessons (those in `docs/lessons-learned/`) must be filtered out. Read INDEX.md and exclude any candidates that match existing entries by title or detection rule.

### Step 2: Scan for Lesson Candidates

Analyze these sources for lessons:

**Source 1: Durable delivery evidence — failures, retries, and corrections**
```python
verification = Glob("docs/epics/**/verification.yaml")
reviews = Glob("docs/epics/**/review.md")
plans = Glob("docs/epics/**/plan.md")
# Look for:
# - failed verification runs and what fixed them
# - blocking/major findings, still_open outcomes, diagnoses, and adjudications
# - "Surprises and discoveries" and decision-log entries in plans
```

**Source 2: Git history — fix patterns**
```bash
# Commits with fix/bugfix/hotfix in message
git log --oneline --since="{since_date}" --grep="fix\|bug\|revert\|workaround\|hack"

# Multiple commits to same file (iterative fixes)
git log --since="{since_date}" --name-only --pretty=format: | sort | uniq -c | sort -rn | head -20
```

**Source 3: Epic audit findings**
```python
# Audit rounds in each review.md: findings that were fixed, rejected, or
# failed a fix more than once
audits = Glob("docs/epics/**/review.md")
```

**Source 4: Code patterns that suggest past pain**
```python
# Comments that indicate lessons
Grep("# IMPORTANT:|# NOTE:|# WORKAROUND:|# HACK:|# WARNING:", glob="*.py")

# Retry/backoff logic added (suggests unreliable operations)
Grep("retry|backoff|max_attempts", glob="*.py")

# Error handling that's suspiciously specific (suggests past incident)
Grep("except.*Error.*#", glob="*.py")
```

**Source 5: Test failures and fixes**
```python
# Tests that were modified multiple times
git log --since="{since_date}" --name-only --pretty=format: -- "tests/" | sort | uniq -c | sort -rn
```

### Step 3: Synthesize Candidates

For each candidate, infer:
- **What happened**: The problem or iteration
- **Pattern/Anti-Pattern**: What to do or avoid
- **Detection rule**: How to spot it
- **Root cause**: Why it matters

### Step 4: Present Candidates

```
Lessons detected since {date}:

  1. [Anti-Pattern] [Critical] Gemini Pro times out on large context
     Signal: 3 failed proof attempts for story-04 and matching audit evidence
     Detection: When Gemini Pro prompt exceeds ~100K tokens
     Rule: Split large synthesis into chunks or use Flash for pre-processing
     RCA: Pro model has 60s timeout; large entity profiles exceed it

  2. [Pattern] [Important] Always verify Firestore composite index exists
     Signal: 5 commits to same query file, "index not found" errors
     Detection: When adding a new Firestore query with multiple filters
     Rule: Create composite index BEFORE deploying query code
     RCA: Firestore silently returns empty results without proper index

  3. [Anti-Pattern] [Important] Don't mock the HTTP client in integration tests
     Signal: Tests passed but production failed for 2 endpoints
     Detection: When test file mocks httpx.AsyncClient
     Rule: Use httpx mock transport, not client mock, for integration tests
     RCA: Mocking client hides serialization and header issues

Actions for each:
  [keep]  — Record as-is
  [edit]  — Discuss and refine
  [skip]  — Not worth recording
  [add]   — I missed a lesson
```

### Step 5: Interview Each Kept Lesson

For each kept lesson:
- Confirm or refine the detection rule
- Confirm the root cause is accurate
- Ensure the resolution is actionable
- Assign severity

For `[add]`, switch to Mode 1 interview.

### Step 6: Save All Approved Lessons

Save each using the standard format (Step 5 from Mode 1).

### Step 7: Summary

```
Recorded {N} lessons:
  - L-{NNN}: {Title} [{Severity}] → {file}
  - ...

Skipped {M} candidates.

Reminder: These lessons are loaded on conversation start via INDEX.md.
Add "Read docs/lessons-learned/INDEX.md on startup" to your CLAUDE.md if not already there.
```

---

## Context Loading

For lessons to actually prevent repeat mistakes, Claude must read them. Add to the project's CLAUDE.md:

```markdown
## Lessons Learned

On conversation start, read `docs/lessons-learned/INDEX.md` for project-specific
lessons. Apply relevant lessons as constraints during all work.
```

The INDEX.md is intentionally compact (one line per lesson with detection summary) so it fits in context without bloat.

---

## Auto-Detection in Conversations

Beyond the `/lesson` command, agents should suggest lessons when they detect:

1. **User correction**: "I told you...", "you forgot...", "you're drifting...", "stop doing X"
   - Acknowledge, correct, then suggest: "Should I record this as a lesson? (`/lesson`)"

2. **Multi-cycle debugging**: When 3+ fix attempts happen on the same issue
   - After resolution, suggest: "This took several iterations. Record as lesson? (`/lesson`)"

3. **Workaround applied**: When the fix is a workaround rather than root cause fix
   - Flag: "This is a workaround. Record the underlying issue as a lesson? (`/lesson`)"

This is a behavior guideline for agents, not enforced by code. Add to CLAUDE.md or agent instructions.

---

## Key Principles

1. **Detection-first** — every lesson must have a concrete detection rule. "Be careful with X" is not a lesson; "When you see {specific signal}, do {specific action}" is.
2. **Root cause, not symptom** — capture why it matters, not just what happened
3. **Compact index** — INDEX.md must stay small enough to load in every conversation
4. **Challenge redundancy** — don't duplicate existing lessons; merge if overlapping
5. **Severity guides attention** — Critical lessons should be impossible to miss

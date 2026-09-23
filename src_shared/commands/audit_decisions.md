---
name: audit_decisions
description: Project-wide audit of undocumented decisions. Scans codebase for technologies, patterns, and product choices not covered by existing ADRs/PDRs. Only appends — never modifies or deletes existing documentation.
args: "[scope filter]"
skills: project-documentation
---

# /audit_decisions

Scan the entire project for decisions that were made but never documented. Cross-references what the code does against what ADRs and PDRs explain.

**Syntax:**
- `/audit_decisions` — Full project scan
- `/audit_decisions backend` — Scope to backend only
- `/audit_decisions {epic-id}` — Scope to one epic

**Output:** Candidate decisions for review. Only appends to existing docs — never modifies or deletes.

---

## What This Is NOT

- **NOT** `/re_documentation` — does not regenerate or rewrite existing docs
- **NOT** an architecture review — does not evaluate if decisions are good or bad
- **NOT** a code audit — does not check implementation quality

This command answers one question: **"What decisions exist in the code that aren't explained in any ADR or PDR?"**

---

## Workflow

```
┌──────────────────────────────────────────────────────┐
│ Step 1: Load all existing ADRs and PDRs              │
│ - System-level, epic-level, component-level          │
│ - Build inventory of documented decisions            │
└──────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────┐
│ Step 2: Scan codebase for decision signals           │
│ - Dependencies, frameworks, cloud services           │
│ - Patterns, data models, API design                  │
│ - Config choices, feature flags, business rules      │
│ - Infrastructure, deployment, operations             │
└──────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────┐
│ Step 3: Cross-reference — find gaps                  │
│ - "Code uses X, no ADR explains why"                 │
│ - "Product does Y, no PDR explains the choice"       │
│ - Exclude what's already documented                  │
└──────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────┐
│ Step 4: Present candidates grouped by area           │
│ → USER REVIEWS, DISCUSSES, APPROVES EACH             │
└──────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────┐
│ Step 5: Record approved decisions                    │
│ - Append to appropriate ADR/PDR files                │
│ - Never modify or delete existing entries            │
└──────────────────────────────────────────────────────┘
```

---

## Execution

### Step 1: Load Existing Decision Inventory

Build a complete map of what's already documented:

```python
# System-level ADRs
system_adrs = Glob("docs/architecture/adr/*.md")
# Read each, extract decision titles and key technologies mentioned

# Component-level ADRs
backend_adrs = Glob("docs/architecture/backend/adr/*.md")
frontend_adrs = Glob("docs/architecture/frontend/adr/*.md")

# Epic-level decisions: each plan's decision log (reversible choices)
epic_plans = Glob("docs/epics/*/plan.md") + Glob("docs/epics/_implemented/*/plan.md")
# Read each "## Decision log", extract decisions and technologies

# ADR summary (cross-check)
adr_summary = Read("docs/architecture/09-adr-summary.md")

# Product decisions / PDRs
product_decisions = Read("docs/product/decisions.md")
# Lasting decisions from epics are ADRs above or PDRs in decisions.md.

# Product docs (strategy, definition — contain implicit decisions)
product_strategy = Read("docs/product/strategy.md")
product_definition = Read("docs/product/definition.md")

# Build a set of "documented decisions" — technologies, patterns, choices
# that are explicitly explained somewhere
documented = {
    "technologies": [...],  # e.g., "PostgreSQL", "FastAPI", "Gemini Flash"
    "patterns": [...],      # e.g., "async workers", "circuit breaker", "WAL mode"
    "product_choices": [...], # e.g., "Canada-only", "no export in MVP"
    "infra_choices": [...],  # e.g., "Cloud Run", "Cloud SQL", "Cloudflare WAF"
}
```

### Step 2: Scan Codebase for Decision Signals

Scan the codebase systematically. For each category, look for decisions that should have an ADR or PDR.

#### 2.1: Technology Choices (→ ADR candidates)

```python
# Languages and runtimes
Glob("*.py")           # Python — version in pyproject.toml?
Glob("*.ts")           # TypeScript
Glob("*.go")           # Go

# Package managers and dependencies
deps = Read("requirements.txt") or Read("pyproject.toml") or Read("package.json")
# Each major dependency is a technology decision:
# - Web framework (FastAPI, Flask, Express, etc.)
# - ORM / DB client (SQLAlchemy, Prisma, etc.)
# - Auth library
# - LLM SDK (openai, anthropic, google-genai)
# - Testing framework (pytest, vitest, jest)
# - Linting / formatting (ruff, black, eslint, prettier)

# Cloud services
Grep("boto3|google.cloud|azure", glob="*.py")  # AWS/GCP/Azure SDK usage
Grep("firebase|firestore|dynamodb|cosmos", glob="*.py")  # Specific services

# Databases
Grep("sqlite|postgresql|mysql|mongodb|redis|qdrant|pinecone", glob="*.py")
Grep("DATABASE_URL|DB_HOST|REDIS_URL", glob="*.env*")

# LLM providers
Grep("openai|anthropic|gemini|claude|gpt", glob="*.py")

# Message queues, caching
Grep("celery|rabbitmq|kafka|memcached", glob="*.py")
```

#### 2.2: Architectural Patterns (→ ADR candidates)

```python
# Design patterns in code
Grep("class.*Protocol|class.*ABC|@abstractmethod", glob="*.py")  # Abstractions
Grep("class.*Factory|class.*Strategy|class.*Observer", glob="*.py")  # GoF patterns
Grep("class.*Repository|class.*Service|class.*Controller", glob="*.py")  # Layering

# Error handling strategy
Grep("class.*Error|class.*Exception", glob="*.py")  # Custom error hierarchy
Grep("retry|backoff|circuit.breaker", glob="*.py")  # Resilience patterns

# Data access patterns
Grep("async def|await ", glob="*.py")  # Async architecture
Grep("class.*Schema|class.*Model|class.*Base", glob="*.py")  # ORM/schema patterns

# API design
Grep("@app\\.get|@app\\.post|@router", glob="*.py")  # REST endpoints
Grep("graphql|subscription|websocket", glob="*.py")  # API style

# Configuration approach
config_files = Glob("config/**/*.yaml") + Glob("config/**/*.json")
Grep("os\\.environ|getenv|dotenv", glob="*.py")  # Env var usage
Grep("pydantic.Settings|BaseSettings", glob="*.py")  # Typed config

# Migration strategy
Glob("alembic/**") or Glob("migrations/**") or Glob("prisma/**")

# Caching strategy
Grep("@cache|lru_cache|TTL|cache_key", glob="*.py")
```

#### 2.3: Infrastructure & Deployment (→ ADR candidates)

```python
# Containerization
Glob("Dockerfile*")
Glob("docker-compose*.yml")

# CI/CD
Glob(".github/workflows/*.yml")
Glob(".gitlab-ci.yml")
Glob("cloudbuild.yaml")

# IaC
Glob("*.tf")  # Terraform
Glob("ansible/**")
Glob("pulumi/**")

# Cloud platform config
Glob("app.yaml")  # App Engine
Glob("service.yaml")  # Cloud Run / K8s
Glob("fly.toml")
Glob("render.yaml")

# Monitoring / observability
Grep("prometheus|datadog|sentry|newrelic|structlog|loguru", glob="*.py")
Grep("opentelemetry|jaeger|zipkin", glob="*.py")
```

#### 2.4: Product Choices (→ PDR candidates)

```python
# Feature flags / toggles
Grep("feature_flag|FEATURE_|enabled.*=.*False|enabled.*=.*True", glob="*.py")
Grep("feature", glob="config/*.yaml")

# Business rules in code
Grep("if.*role|permission|is_admin|is_superuser", glob="*.py")  # Access control model
Grep("pricing|tier|plan|subscription|limit", glob="*.py")  # Business model
Grep("locale|timezone|currency|language|i18n", glob="*.py")  # Internationalization

# Scope boundaries
# Look at what the product DOESN'T do (commented out, deferred, TODO)
Grep("# TODO|# DEFERRED|# FUTURE|# MVP", glob="*.py")

# UI/UX choices (if frontend exists)
Grep("theme|dark.mode|responsive|mobile", glob="*.{ts,tsx,css}")

# Target audience signals
# Config that limits scope (jurisdictions, industries, regions)
Grep("jurisdiction|region|country|industry|sector", glob="config/*.yaml")
```

#### 2.5: Data Model Decisions (→ ADR candidates)

```python
# Schema definitions
Grep("CREATE TABLE|ALTER TABLE", glob="*.sql")
Grep("class.*Base.*Model|Column\\(|relationship\\(", glob="*.py")  # SQLAlchemy
Grep("model.*{|schema.*{", glob="*.prisma")  # Prisma

# Data formats
Grep("json|yaml|csv|parquet|protobuf|avro", glob="*.py")

# Storage layout (S3/GCS key patterns)
Grep("bucket|blob|s3://|gs://", glob="*.py")
```

### Step 3: Cross-Reference — Find Gaps

For each signal found in Step 2, check against the documented inventory from Step 1:

```python
gaps = []

for signal in discovered_signals:
    # Is this technology/pattern/choice explained in any ADR or PDR?
    if signal.technology not in documented["technologies"] and \
       signal.pattern not in documented["patterns"]:
        gaps.append({
            "signal": signal,
            "type": classify(signal),  # ADR or PDR (use decision tree heuristic)
            "source": signal.file_path,
            "why_inferred": infer_rationale(signal),
        })

# Filter out trivial decisions not worth documenting:
# - Standard language features (using Python's logging module)
# - Default framework choices (FastAPI's default JSON serialization)
# - Boilerplate (standard project structure)
# Keep only decisions where "why this and not that?" is a meaningful question
```

**Filtering heuristic — is it worth documenting?**

A decision is worth an ADR/PDR if at least one is true:
- There was a meaningful alternative (PostgreSQL vs SQLite — yes; using Python's `os.path` — no)
- It constrains future choices (choosing Alembic means all schema changes go through it)
- It has tradeoffs worth explaining (async adds complexity for concurrency benefit)
- Someone new would ask "why did you do it this way?"

### Step 4: Present Candidates

Group gaps by area and present to user:

```
Decision Audit: {N} undocumented decisions found

━━━ Technology Choices ({N} gaps) ━━━

  1. [ADR] Using structlog for structured logging
     Source: src/core/logger.py, requirements.txt
     Current ADRs: None mention logging framework choice
     Why (inferred): Structured JSON logging for machine-readable log aggregation
     Worth documenting? Constrains log format across all services

  2. [ADR] Using Jina v5 embeddings (not OpenAI, not Cohere)
     Source: config/embeddings.yaml, src/embedding_api/
     Current ADRs: None mention embedding model selection
     Why (inferred): Multilingual support, cost, self-hostable
     Worth documenting? Key ML infrastructure choice with cost/quality tradeoffs

━━━ Architectural Patterns ({N} gaps) ━━━

  3. [ADR] Template-as-prompt pattern for LLM synthesis
     Source: templates/*.j2, src/synthesizer/
     Current ADRs: May be partially covered in epic ADRs
     Why (inferred): Separation of prompt structure from code
     Worth documenting? Core architectural pattern

  4. [ADR] Fail-fast configuration (no defaults for required config)
     Source: src/config/loader.py — raises on missing keys
     Current ADRs: None
     Why (inferred): Surface misconfig at startup, not at runtime
     Worth documenting? Cross-cutting pattern affecting all services

━━━ Product Choices ({N} gaps) ━━━

  5. [PDR] Canada-only jurisdiction scope for MVP
     Source: config/jurisdictions.yaml — only CA provinces listed
     Current PDRs: Mentioned in strategy.md but no formal PDR
     Why (inferred): Market focus, regulatory complexity scoping
     Worth documenting? Defines product boundary

  6. [PDR] No user self-registration — admin-provisioned only
     Source: No signup endpoint, admin-only user creation
     Current PDRs: None
     Why (inferred): Enterprise B2B model, controlled access
     Worth documenting? Affects onboarding and growth model

━━━ Infrastructure ({N} gaps) ━━━

  7. [ADR] Cloud Run for stateless services (not GKE, not App Engine)
     Source: service.yaml, cloudbuild.yaml
     Current ADRs: Partially in deployment docs but no formal ADR
     Why (inferred): Serverless scaling, cost efficiency, simplicity
     Worth documenting? Constrains deployment model

━━━ Data Model ({N} gaps) ━━━

  8. [ADR] pgvector for embeddings (not separate vector DB)
     Source: migrations/003_add_embeddings.sql
     Current ADRs: None
     Why (inferred): Single database for queries + vectors, simpler ops
     Worth documenting? Key data architecture choice

Actions for each:
  [keep]     — Record this decision (will interview for "why")
  [skip]     — Not worth documenting
  [merge]    — Already partially covered in {existing ADR} — add to it
  [add]      — I missed a decision, let me add it
  [done]     — Finish review
```

### Step 5: Record Approved Decisions

For each `[keep]` decision:
1. Interview the user for the "why" (same as `/decision` Mode 1, Steps 3-4)
2. Challenge if the decision contradicts existing ADRs or creates issues
3. Save using the standard ADR/PDR format

For each `[merge]` decision:
1. Read the existing ADR/PDR that partially covers it
2. Ask user what to add (a new section? a note? an update?)
3. Append to the existing entry — never rewrite it

**Critical rule: ONLY APPEND.** This command never modifies, rewrites, or deletes existing ADR/PDR content.

### Step 6: Update ADR Summary

If new system-level ADRs were created, append them to `docs/architecture/09-adr-summary.md`:

```python
adr_summary = Read("docs/architecture/09-adr-summary.md")
# Append new entries under the appropriate scope section
# (System ADRs, Backend ADRs, Frontend ADRs)
```

### Step 7: Summary Report

```
Decision Audit Complete

  Scanned: {files scanned} files, {dependencies} dependencies, {patterns} patterns
  Found:   {total} undocumented decisions

  Recorded: {kept} new decisions
    ADRs: {adr_count} ({system} system, {backend} backend, {frontend} frontend, {epic} epic-level)
    PDRs: {pdr_count}

  Merged:  {merged} into existing ADRs/PDRs
  Skipped: {skipped} (not worth documenting)

  Files updated:
    {list of files that were appended to}

  No existing documentation was modified or deleted.
```

---

## Scope Filters

| Filter | What Gets Scanned |
|--------|------------------|
| (none) | Entire project |
| `backend` | Backend code, services, data model, infrastructure |
| `frontend` | Frontend code, components, patterns, UI choices |
| `product` | Product choices, feature scope, business rules, UX |
| `infra` | Infrastructure, deployment, CI/CD, monitoring |
| `{epic-id}` | Files changed by that epic only (git-based) |

---

## When to Run

| Trigger | Why |
|---------|-----|
| After reverse engineering a project | Fill decision gaps in freshly-documented codebase |
| Before a major epic | Ensure baseline architecture is well-documented |
| Quarterly review | Catch decisions that slipped through |
| New team member onboarding | Document tribal knowledge before it's needed |
| After `/re_documentation` | The architecture docs explain "what" — this adds "why" |

---

## Key Principles

1. **APPEND ONLY** — never modify, rewrite, or delete existing documentation
2. **Cross-reference first** — don't suggest documenting what's already documented
3. **Filter trivial decisions** — not every `import` is worth an ADR
4. **Interview for "why"** — inferred rationale is a starting point, not the final record
5. **Challenge contradictions** — if a gap reveals an inconsistency with existing ADRs, flag it
6. **Respect the user's time** — present candidates in priority order, let them skip freely

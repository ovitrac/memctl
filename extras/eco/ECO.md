# ECO Mode — Behavioral Strategy for memctl MCP Tools

## What eco is

eco mode is **deterministic structural retrieval + persistent cross-file reasoning**.

Native Claude reads files sequentially. eco Claude queries architecture.
It finds the exact chunk — not the first 200 lines, not the whole file, not boilerplate.
It surfaces cross-file invariants that sequential browsing misses.
It remembers what it found, across sessions, without re-exploration.

eco is a **behavioral layer**, not a mandatory runtime.
memctl remains usable as a plain CLI without Claude hooks.

## Why eco is OFF by default

eco changes Claude's behavior via context injection. Making it default ON creates
the perception that memctl is "intrusive." The main failure mode is natural language
queries returning 0 results (FTS5 AND logic) — a first-impression killer at scale.

Opt-in with a clear enable step (`memctl eco on` or `/eco on`) is the safer
adoption path. See the wahoo demo (`demos/eco_demo.sh`) for a 60-second showcase.

---

## Mode Classification Protocol

Before using injected context, classify the user's intent.
Classification is automated via `classify_mode()` in `memctl/query.py` —
it uses deterministic verb matching, no ML required.

### Exploration-first (comprehension, traceability, architecture)

**Triggered by:** how, where, what, which, explain, describe, show, list,
understand, trace, find, search, check, compare, analyze, structure,
dependency, module, layer, flow, pattern

**Action:** Answer using injected context. Do not read or edit files unless
the context is insufficient.

### Modification-first (editing, refactoring, adding code)

**Triggered by:** add, replace, refactor, fix, create, delete, update,
modify, remove, rename, implement, migrate, upgrade, configure

**Action:** Use injected context to identify **WHICH file** and **WHERE**.
Then use native Read to get current content, and native Edit to modify it.

### Critical rules

1. **eco context tells Claude WHAT exists and WHERE.**
   Native tools tell Claude what the file contains **RIGHT NOW**.

2. **NEVER edit files using chunk content.**
   Chunks may be from a stale sync, a different branch, or a different version.
   Edits MUST use native Read to get current file content, then native Edit.

3. **NEVER let injected context override the user's question.**
   The user question is the dominant signal. Injected context is reference material.

---

## Injection Budget Guidelines

Injection context can exceed the user's question by 100:1. This risks intent
distortion. Budget calibration prevents it.

Budget selection is automated via `suggest_budget()` in `memctl/query.py`.

| Context | Recommended Budget | Rationale |
|---------|-------------------|-----------|
| Hook-based injection (PreToolUse) | 600-800 tokens | Keeps ratio < 30:1 |
| Explicit `memctl push` (CLI) | 2200 tokens (default) | User controls the query |
| MCP `memory_recall` | 1500 tokens | MCP response size limits |

Short questions (< 80 chars) → budget 600.
Medium questions (80-200 chars) → budget 800.
Long questions (200-400 chars) → budget 1200.
Very long questions (> 400 chars) → budget 1500.

---

## Escalation Ladder (Operational Doctrine)

Always apply in this order before native fallback:

1. **`memory_inspect`** — structural overview (file tree, extensions, sizes, observations)
2. **`memory_recall`** — selective content retrieval (FTS5, token-budgeted, scoped)
3. **`memory_loop --protocol json`** — iterative refinement (bounded, convergence-detecting)
4. **Native `Read`/`View`** — only if raw content is required for editing or line-level precision

Native file reads are a last resort for editing or line-level precision.

Do NOT skip levels. Do NOT jump to native `Read` after a single failed recall.
Narrow the query, scope to a subfolder, refine iteratively — then fall back.

### Automatic normalization at L2

Since v0.10.0, `memory_recall` and `memory_search` automatically apply
`normalize_query()` before FTS search. This strips French and English stop words
(articles, prepositions, question words) while preserving code identifiers
(CamelCase, snake_case, UPPER_CASE, dotted paths).

Example:
```
User query:   "how does the incident escalation workflow work"
Normalized:   "incident escalation workflow work"
FTS MATCH:    "incident" AND "escalation" AND "workflow" AND "work"
```

If the normalized query still returns 0 results, the response includes
actionable recovery hints (use identifiers, remove ambiguous terms, try inspect).

### Recovery sequence when recall returns 0

1. **Remove the broadest term** — keep only identifiers and domain nouns.
2. **Try the function/class name directly** — `IncidentServiceImpl` > `incident service`.
3. **Scope to a subfolder** — restrict search to the relevant directory.
4. **Split into two narrower queries** — one per concept.
5. **Escalate to `memory_loop`** — structured iterative refinement.
6. **Only then** fall back to native `Read` on the specific file identified.

---

## FTS5 Query Discipline

memctl uses SQLite FTS5 full-text search with **AND logic**.
Every term in the query must match within a single item. Natural language
sentences over-constrain and return nothing.

Stop words are stripped automatically (v0.10.0+), but fewer terms is still better.

### Rules

1. **Use 2-3 precise keywords**, not sentences.
2. **Prefer identifiers** — function names, class names, variable names, constants.
3. **Avoid natural language** — stop words are stripped, but remaining terms must all match.
4. **Split broad questions** into two narrow queries.

### FTS5 tokenizer: unicode61 (no stemming)

memctl's default tokenizer (`unicode61 remove_diacritics 2`) does NOT perform
Porter stemming. This has practical implications:

| Behavior | Example | Result |
|----------|---------|--------|
| Case-insensitive | "Incident" matches "incident" | Match |
| Diacritics normalized | "créer" matches "creer" | Match |
| **No inflection matching** | "monitored" vs "Monitoring" | **No match** |
| **No singular/plural** | "notification" vs "notifications" | **No match** |
| **AND logic** | All terms must be in ONE item | No cross-item |

This is the correct trade-off: deterministic FTS5 with no dependencies vs.
embedding-based search requiring FAISS/Ollama. When exact forms don't match,
use the identifier (class/method name) instead of a description.

### Good queries

**Identifiers (always best):**
```
SomeServiceImpl              → finds the service implementation
PreAuthorize                 → finds security-annotated controllers
JmsListener                  → finds messaging consumers
dateCreation                 → finds DTO/entity fields
RestController               → finds REST endpoints
SECRET_PATTERNS              → finds the actual pattern list
```

**Domain term pairs:**
```
incident workflow             → finds workflow specs + code
security authentication       → finds auth-related classes
Redis caching                 → finds caching strategy
Kubernetes deployment         → finds deployment manifests
```

**NL queries after automatic normalization (v0.10.0+):**
```
"what is the incident escalation workflow"
  → normalized: "incident escalation workflow"   → match

"what authentication Spring Security uses"
  → normalized: "authentication Spring Security uses"   → match

"what REST conventions do the endpoints follow"
  → normalized: "REST conventions endpoints follow"   → match
```

### Bad queries (even after normalization)

```
"how are the services monitored in production"
  → normalized: "services monitored production"
  → MISS: "monitored" ≠ "Monitoring" (no stemming)
  → Fix: use "Prometheus monitoring" instead

"how does the notification system work"
  → normalized: "notification system work"
  → MISS: "notification" ≠ "notifications" (no stemming)
  → Fix: use "NotificationServiceImpl" instead

"what database is used for storage in this project"
  → normalized: "database used storage project"
  → MISS: terms span multiple items (AND logic)
  → Fix: split into "PostgreSQL database" and "storage partitioned"
```

Think like a developer using grep, not like a user asking a chatbot.
eco rewards precision. That's not a flaw — that's power.

---

## Bypass Decision Tree

```
Is the task about a SPECIFIC FILE you already know the path to?
  → YES: Bypass eco. Use native Read.

Is the task about EDITING or MODIFYING code?
  → YES: Use eco to LOCATE. Use native to EDIT.

Is the task about UNDERSTANDING structure or traceability?
  → YES: Use eco (inspect + recall).

Is the query in natural language (> 5 words)?
  → YES: Stop words are stripped automatically.
         If still 0 results, extract 2-3 identifiers. Then recall.
```

---

## When to use memory_inspect

- **Before exploring a directory for the first time.**
- To get a structural overview: file tree, extension distribution, size totals, observations.
- `memory_inspect` auto-mounts, auto-syncs, and produces a token-budgeted digest.
- This replaces browsing files one by one to "understand the project."

**Example:**
```
memory_inspect("src/")
→ 35 files, 71 chunks, 357 KB
→ Dominant: .py (26 files), .md (9 files)
→ Largest: cli.py (57 KB), store.py (54 KB)
→ Observation: src/ dominates content (78% of chunks)
```

One call. ~600 tokens. Replaces 10-15 individual file reads.

## When to use memory_recall

- To retrieve **relevant content** for a question, instead of reading entire files.
- Uses FTS5 full-text search with token budgets.
- Supports scoping to a folder (mount) for precision.
- Stop words are automatically stripped from queries (v0.10.0+).

**When recall returns insufficient results:**

1. **Narrow the query** — more specific keywords, scope to a subfolder.
2. **Try different terms** — use the class/function name, not a description.
3. **Scope to a mount** — restrict search to the relevant directory.
4. **Check inflection** — use the exact word form from the codebase.
5. **Escalate to `memory_loop`** — structured refinement with convergence detection.
6. **Only then** fall back to native `Read` on the specific file identified.

Premature fallback to native `Read` defeats the eco mode purpose.

## When to use memory_propose

- When you have validated a finding, decision, convention, or architectural observation.
- Examples:
  - "The project uses JWT for authentication with 15-min access tokens"
  - "Config is loaded from src/config/ with env var override"
  - "The database schema uses event sourcing with CQRS"
- Stored findings persist across sessions and are recalled by future questions.
- Use `--tier stm` for recent observations, `--tier mtm` for verified facts.

**Rule:** If you discovered something that would be useful in a future session, propose it.

## When to use memory_consolidate

- After a session with many stored findings (5+ STM items).
- Merges similar STM items by type and tag overlap (Jaccard similarity).
- Promotes verified knowledge to MTM.
- Fully deterministic — no LLM calls, same input produces same output.

**When to trigger:** End of an exploration session, or when `memory_stats` shows high STM count.

## When to use native Read/View

Native file access is correct and expected for:

- **Editing a specific file** — need exact line content for `str_replace` or patch.
- **Diffing two files** — need raw content side-by-side.
- **Inspecting a single small file** — where full content is needed and the file is < 200 lines.
- **After the escalation ladder is exhausted** — recall, narrowing, and loop refinement all failed.

eco mode covers **exploration and comprehension**.
Native Read covers **mutation and line-level precision**.

These are complementary, not competing.

---

## Binary formats (eco-plus)

When extraction dependencies are available, memctl extracts text from binary formats that are invisible to native Claude Code:

| Format | Dependency | Status |
|--------|-----------|--------|
| `.docx` | `python-docx` | Stable |
| `.odt` | stdlib | Stable |
| `.pptx` | `python-pptx` | Stable |
| `.odp` | stdlib | Stable |
| `.xlsx` | `openpyxl` | Beta |
| `.ods` | stdlib | Beta |
| `.pdf` | `pdftotext` (Poppler) | Stable, text-layer only |

Always use memctl for these formats. Native Claude Code cannot read them at all.
PDF extraction is text-layer only; OCR is out of scope.

## Memory tiers

- **STM (Short-Term):** Recent observations, unverified. Created by `memory_propose`.
- **MTM (Medium-Term):** Consolidated, verified. Created by `memory_consolidate`.
- **LTM (Long-Term):** Stable decisions, definitions. Promoted by repeated validation.

Knowledge flows upward: STM → MTM → LTM.
Each tier has higher confidence and longer retention.
Consolidation is the promotion mechanism — deterministic, no LLM required.

---

## Programmatic API (v0.10.0+)

eco mode's classification and normalization are available as Python functions:

```python
from memctl.query import normalize_query, classify_mode, suggest_budget

# Stop-word stripping
normalize_query("comment créer un incident dans le système")
# → "créer incident système"

normalize_query("how does SomeServiceImpl work")
# → "SomeServiceImpl work"

# Intent classification
classify_mode("How does authentication work?")    # → "exploration"
classify_mode("Add logging to SomeServiceImpl")   # → "modification"

# Budget suggestion
suggest_budget(len("short question"))              # → 600
suggest_budget(len("a longer question about architecture"))  # → 800
```

These functions are stdlib-only and integrated into `store.search_fulltext()`,
`memory_recall`, and `memory_search` automatically.

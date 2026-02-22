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
the perception that memctl is "intrusive." Since v0.11.0, the FTS cascade mitigates
the main failure mode (NL queries returning 0 results), but eco still changes
behavioral expectations — opt-in is the safer adoption path.

See the wahoo demo (`demos/eco_demo.sh`) for a 60-second showcase.

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

Since v0.11.0, the cascade already attempts term reduction and OR fallback
automatically. If you still get 0 results after cascade, it means no indexed
item contains any of your query terms. Recovery steps:

1. **Check `fts_strategy`** — if it's `OR_FALLBACK`, the cascade tried everything.
2. **Try the function/class name directly** — `IncidentServiceImpl` > `incident service`.
3. **Check inflection** — use the exact word form from the codebase (no stemming).
4. **Scope to a subfolder** — restrict search to the relevant directory.
5. **Escalate to `memory_loop`** — structured iterative refinement.
6. **Only then** fall back to native `Read` on the specific file identified.

---

## FTS5 Query Discipline

memctl uses SQLite FTS5 full-text search. Stop words are stripped
automatically (v0.10.0+), and queries benefit from a deterministic cascade
(v0.11.0+) that improves recall without sacrificing explainability.

### Rules

1. **Use 2-3 precise keywords**, not sentences.
2. **Prefer identifiers** — function names, class names, variable names, constants.
3. **Avoid inflected forms** — use base forms from the codebase (see Stemming below).
4. **Split broad questions** into two narrow queries when possible.

### FTS Cascade Behavior (v0.11.0)

When a multi-term query returns 0 results, the system automatically cascades:

```
AND(all terms)  →  REDUCED_AND(N-1)  →  ...  →  AND(1 term)  →  OR(all terms)
```

Each step is logged and the strategy is reported in MCP responses via `fts_strategy`.

| Strategy | When | Precision | What it means |
|----------|------|-----------|---------------|
| `AND` | All terms matched in one chunk | Highest | Exact match — all terms co-occur |
| `REDUCED_AND` | Some terms were dropped | Medium | Context is narrower than requested — check `fts_dropped_terms` |
| `OR_FALLBACK` | Any term matches, ranked by coverage | Lower | Broad match — review results for relevance |
| `LIKE` | FTS5 unavailable | Variable | Substring matching — no ranking |

**Reading the strategy in MCP responses:**
```json
{
  "fts_strategy": "REDUCED_AND",
  "fts_original_terms": ["REST", "endpoints"],
  "fts_effective_terms": ["REST"],
  "fts_dropped_terms": ["endpoints"]
}
```

**When to escalate after cascade:**
- `AND` → trust the results.
- `REDUCED_AND` → results are valid but narrower. If insufficient, try a different query.
- `OR_FALLBACK` → results may include loosely related items. Review before acting.
  If relevance is poor, escalate to `memory_loop` or native `Read`.

The cascade runs automatically — no manual intervention needed. The old advice
"narrow your query" still applies for best results, but the system now recovers
gracefully instead of returning empty.

### Stemming Limitations

memctl's default tokenizer (`unicode61 remove_diacritics 2`) does NOT perform
Porter stemming. This means inflected forms do not match:

| Behavior | Example | Result |
|----------|---------|--------|
| Case-insensitive | "Incident" matches "incident" | Match |
| Diacritics normalized | "créer" matches "creer" | Match |
| **No inflection** | "monitored" vs "Monitoring" | **No match** |
| **No singular/plural** | "notification" vs "notifications" | **No match** |

**Workaround:** Use the exact form from the codebase, or the identifier:
- "configuration" not "configured"
- "monitor" not "monitored"
- `NotificationServiceImpl` not "notification system"

### Stemming control (v0.12.0)

If inflection misses are frequent, switch to the Porter stemmer:

    memctl reindex --tokenizer en

This enables: "configured" ↔ "configuration", "monitored" ↔ "monitoring".
Porter is English-only. The default (`fr`) is accent-safe without stemming.

After reindex, prefix expansion is automatically disabled (Porter handles it).

Use `memctl reindex --dry-run` to preview changes before applying.

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

**NL queries after normalization + cascade (v0.11.0):**
```
"what is the incident escalation workflow"
  → normalized: "incident escalation workflow"
  → AND: match (all terms co-occur)

"what REST conventions do the endpoints follow"
  → normalized: "REST conventions endpoints follow"
  → AND miss → REDUCED_AND("REST conventions endpoints"): match
  → fts_strategy: REDUCED_AND, dropped: ["follow"]

"how are the services monitored in production"
  → normalized: "services monitored production"
  → AND miss → REDUCED_AND miss → OR_FALLBACK: 5 hits
  → ranked by coverage — items with more terms ranked first
  → fts_strategy: OR_FALLBACK
```

### Bad queries (even after cascade)

```
"monitored" vs "Monitoring"
  → no stemming — neither AND nor cascade helps
  → Fix: use the exact codebase form

"what database is used for storage in this project"
  → normalized: "database used storage project"
  → cascade finds OR results but precision is low
  → Fix: use identifiers — "PostgreSQL" or "DataSource"
```

Think like a developer using grep, not like a user asking a chatbot.
eco rewards precision. The cascade catches the misses — but identifiers still win.

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

## Scale-Aware Patterns

eco works on codebases from 10 files to 10,000+ files. Adjust strategy by scale:

**Small stores (<500 items):** Default settings work well. Single inspect +
recall covers most questions. Budget 800-1200 tokens.

**Medium stores (500-3,000 items):** Scope recall to specific mounts or
subfolders. Use `memory_inspect` at the directory level before drilling down.
Budget 1200-1500 tokens.

**Large stores (3,000+ items):** Hierarchical inspect — start at root, drill
into the dominant subtree. Scope every recall to a mount. Budget 1500+ tokens.
For files with 50+ chunks, navigate by chunk index rather than reading all chunks.

Mount scoping is the key optimization at scale — it restricts both inspect and
recall to the relevant portion of the codebase.

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

Since v0.11.0, the cascade automatically tries term reduction and OR fallback.
Check `fts_strategy` in the response to understand what happened:

1. **Check `fts_strategy`** — `OR_FALLBACK` means the system already relaxed the query.
2. **Try different terms** — use the class/function name, not a description.
3. **Check inflection** — "configuration" not "configured" (no stemming).
4. **Scope to a mount** — restrict search to the relevant directory.
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

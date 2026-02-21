# ECO Mode — Behavioral Strategy for memctl MCP Tools

## Guiding principle

eco mode is **deterministic structural retrieval + persistent cross-file reasoning**.

Native Claude reads files sequentially. eco Claude queries architecture.
It finds the exact chunk — not the first 200 lines, not the whole file, not boilerplate.
It surfaces cross-file invariants that sequential browsing misses.
It remembers what it found, across sessions, without re-exploration.

Follow the escalation ladder. Every tool call serves one goal:
**give Claude the right context with the fewest tokens.**

## Escalation ladder (Operational Doctrine)

Always apply in this order before native fallback:

1. **`memory_inspect`** — structural overview (file tree, extensions, sizes, observations)
2. **`memory_recall`** — selective content retrieval (FTS5, token-budgeted, scoped)
3. **`memory_loop --protocol json`** — iterative refinement (bounded, convergence-detecting)
4. **Native `Read`/`View`** — only if raw content is required for editing or line-level precision

Native file reads are a last resort for editing or line-level precision.

Do NOT skip levels. Do NOT jump to native `Read` after a single failed recall.
Narrow the query, scope to a subfolder, refine iteratively — then fall back.

## FTS5 Query Discipline

memctl uses SQLite FTS5 full-text search with **AND logic**.
Every term in the query must match. Multi-word natural language sentences
over-constrain results and return nothing.

**Rules:**

1. **Use 2-3 precise keywords**, not sentences.
2. **Prefer identifiers** — function names, class names, variable names, constants.
3. **Avoid natural language** — no articles, no verbs, no prepositions.
4. **Split broad questions** into two narrow queries.

Avoid:
```
How is middleware order enforced and how does rate limiting interact with audit logging?
→ 0 results (AND requires every word to match)
```

Prefer:
```
middleware guard rate_limiter audit order
→ finds cross-file middleware architecture
```

Better yet — use identifiers:
```
SECRET_PATTERNS                → finds the actual pattern list
validate_path                  → finds the guard implementation
RateLimitExceeded              → finds the exception class + usage
make_content_detail            → finds audit privacy logic
```

If recall is empty or incomplete:

- Remove the least important term.
- Try the function/class name directly.
- Scope to a subfolder for precision.
- Split into two narrower queries.
- Use `memory_loop --protocol json` to refine iteratively.

Think like a developer using grep, not like a user asking a chatbot.

eco rewards precision. That's not a flaw — that's power.

## When to use memory_inspect

- **Before exploring a directory for the first time.**
- To get a structural overview: file tree, extension distribution, size totals, rule-based observations.
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
- This replaces `head -200` truncations and full file reads.

**When recall returns insufficient results:**

1. **Narrow the query** — more specific keywords, scope to a subfolder.
2. **Try different terms** — synonyms, related concepts, function names.
3. **Scope to a mount** — restrict search to the relevant directory.
4. **Escalate to `memory_loop`** — structured refinement with convergence detection.
5. **Only then** fall back to native `Read` on the specific file identified.

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

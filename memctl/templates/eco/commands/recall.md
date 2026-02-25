You are searching memctl memory for relevant content.

Argument received: $ARGUMENTS

## Step 1: Normalize the query

Before calling memory_recall, transform the user's input into an effective FTS query:

1. **Strip question words and stop words** — remove "how", "what", "where", "the", "is", "are", "does", etc.
2. **Extract identifiers** — class names (`SomeServiceImpl`), function names (`handleRequest`), constants (`MAX_RETRIES`) are always the best query terms. Preserve them exactly.
3. **Keep 2-3 domain keywords** — "authentication", "incident", "deployment" work well. Full sentences do not.
4. **Prefer exact codebase forms** — "configuration" not "configured" (no stemming by default).

Examples:
- `/recall how does authentication work` → query: `authentication`
- `/recall SomeServiceImpl incident handling` → query: `SomeServiceImpl incident`
- `/recall what REST endpoints exist` → query: `RestController` or `REST endpoints`
- `/recall where is the database config` → query: `DataSource configuration` or `db_config`

## Step 2: Call memory_recall

If the `memory_recall` MCP tool is available:

```
memory_recall(query="<normalized query>", budget_tokens=1500)
```

If MCP tools are not available, use the CLI fallback:
```bash
memctl search --db .memory/memory.db "<normalized query>"
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

**Important:** The CLI command is `memctl search`, NOT `memctl recall` — that command does not exist.

## Step 3: Present results with output contract

Always show the search provenance — do not hide the transformation:

```
query_in:    <original user input>
query_used:  <normalized query actually sent to FTS>
strategy:    AND | REDUCED_AND | OR_FALLBACK | LIKE | PREFIX_AND
hits:        <number of matched items>
hint:        <only if hits=0 or query was too long>
```

This is non-negotiable. Users must see what was searched and how the cascade resolved. Without this, results "feel random" and adoption collapses.

### If results found (hits > 0):

Display: the output contract line, then matched items with content and tokens used.

Check `fts_strategy` in the response:
- **AND** — all terms matched in one chunk. High confidence.
- **REDUCED_AND** — some terms dropped (check `fts_dropped_terms`). Results are valid but narrower than requested.
- **OR_FALLBACK** — any term matches, ranked by coverage. Review for relevance before acting.

Structure the answer in two blocks:
- **Retrieved** — only statements supported by recalled chunks (cite source paths or item IDs)
- **Analysis** — your reasoning, hypotheses, connections, next steps, uncertainties

### If zero results (hits = 0):

The cascade already tried AND → REDUCED_AND → OR_FALLBACK automatically. If still 0, propose exactly two next queries (not a paragraph):

1. **An identifier query** — the exact class/function name if you can infer it.
   Example: `SomeServiceImpl` instead of "the service that handles incidents"
2. **A different term** — synonym, related concept, or the base word form.
   Example: "monitor" instead of "monitored" (no stemming by default)

Also mention:
- `/scan <path>` if the directory may not be indexed yet
- `/reindex en` if inflection misses are the pattern (enables Porter stemming)

### If no argument provided:

Ask: "What are you looking for? Give me 2-3 keywords — identifiers (class/function names) work best."

## Query discipline (why this matters)

FTS5 is not a chatbot. It matches terms in indexed chunks. The cascade (v0.11+) recovers gracefully from multi-term misses, but precision always beats breadth:

| Query type | Expected recall | Example |
|-----------|----------------|---------|
| Identifier | ~100% | `SomeServiceImpl`, `PreAuthorize` |
| Domain term pair | ~90% | `incident workflow`, `Redis caching` |
| Natural language (after normalization) | ~60-80% | `authentication flow` |
| Full sentence (raw) | ~40% | `how does the authentication system work` |

This is why /recall normalizes before searching — it moves your query up the recall table.

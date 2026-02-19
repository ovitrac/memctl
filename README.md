# memctl

**A Unix-native memory control plane for LLM orchestration.**

One file, one truth. Ingest files, recall with FTS5, pipe into any LLM.

```
pip install memctl
memctl init
memctl push "project architecture" --source src/ | llm "Summarize the architecture"
echo "The architecture uses event sourcing" | memctl pull --tags arch
```

---

## Why memctl?

LLMs forget everything between turns. memctl gives them persistent, structured, policy-governed memory backed by a single SQLite file.

- **Zero dependencies** — stdlib only. No numpy, no torch, no compiled extensions.
- **One file** — Everything in `memory.db` (SQLite + FTS5 + WAL).
- **Unix composable** — `push` writes to stdout, `pull` reads from stdin. Pipe freely.
- **Policy-governed** — 30 detection patterns block secrets, injection, and instructional content before storage.
- **Content-addressed** — SHA-256 dedup ensures idempotent ingestion.
- **Forward-compatible** — Identical schema to [RAGIX](https://github.com/ovitrac/RAGIX). Upgrade seamlessly.

---

## Installation

```bash
pip install memctl
```

For Office/ODF document ingestion (.docx, .odt, .pptx, .odp, .xlsx, .ods):

```bash
pip install memctl[docs]
```

For MCP server support (Claude Code / Claude Desktop):

```bash
pip install memctl[mcp]
```

For everything:

```bash
pip install memctl[all]
```

**Requirements:** Python 3.10+ (3.12 recommended). No compiled dependencies for core.
PDF extraction requires `pdftotext` from poppler-utils (`sudo apt install poppler-utils` or `brew install poppler`).

---

## Quickstart

### 1. Initialize a memory workspace

```bash
memctl init
# Creates .memory/memory.db, .memory/config.yaml, .memory/.gitignore
```

Set the environment variable for convenience:

```bash
eval $(memctl init)
# Sets MEMCTL_DB=.memory/memory.db
```

### 2. Ingest files and recall

```bash
# Ingest source files + recall matching items → injection block on stdout
memctl push "authentication flow" --source src/auth/

# Ingest Office documents (requires memctl[docs])
memctl push "project status" --source reports/*.docx slides/*.pptx

# Ingest PDFs (requires pdftotext)
memctl push "specifications" --source specs/*.pdf

# Recall only (no ingestion)
memctl push "database schema"
```

### 3. Store LLM output

```bash
# Pipe LLM output into memory
echo "We chose JWT for stateless auth" | memctl pull --tags auth,decision --title "Auth decision"

# Or pipe from any LLM CLI
memctl push "API design" | llm "Analyze this" | memctl pull --tags api
```

### 4. Search

```bash
# Human-readable
memctl search "authentication"

# JSON for scripts
memctl search "database" --json -k 5
```

### 5. Inspect and manage

```bash
memctl show MEM-abc123def456     # Show item details
memctl stats                     # Store metrics
memctl stats --json              # Machine-readable stats
memctl consolidate               # Merge similar STM items
memctl consolidate --dry-run     # Preview without writing
```

---

## CLI Reference

```
memctl <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `init [PATH]` | Initialize a memory workspace (default: `.memory`) |
| `push QUERY [--source ...]` | Ingest files + recall matching items to stdout |
| `pull [--tags T] [--title T]` | Read stdin, store as memory items |
| `search QUERY [-k N]` | FTS5 full-text search |
| `show ID` | Display a single memory item |
| `stats` | Store statistics |
| `consolidate [--dry-run]` | Deterministic merge of similar STM items |
| `loop QUERY --llm CMD` | Bounded recall-answer loop with LLM |
| `serve` | Start MCP server (requires `memctl[mcp]`) |

### Global Flags

| Flag | Description |
|------|-------------|
| `--db PATH` | SQLite database path |
| `--json` | Machine-readable JSON output |
| `-q, --quiet` | Suppress stderr progress messages |
| `-v, --verbose` | Enable debug logging |

### Command Details

#### `memctl init`

```bash
memctl init [PATH] [--force] [--fts-tokenizer fr|en|raw]
```

Creates the workspace directory, SQLite database with schema, `config.yaml`, and `.gitignore`. Prints `export MEMCTL_DB="..."` to stdout for eval.

Idempotent: running twice on the same path exits 0 without error.

#### `memctl push`

```bash
memctl push QUERY [--source FILE ...] [--budget N] [--tier TIER] [--tags T] [--scope S]
```

Two-phase command:
1. **Ingest** (optional): processes `--source` files with SHA-256 dedup and paragraph chunking.
2. **Recall**: FTS5 search for QUERY, format matching items as an injection block on stdout.

stdout contains only the injection block (`format_version=1`). Progress goes to stderr.

#### `memctl pull`

```bash
echo "..." | memctl pull [--tags T] [--title T] [--scope S]
```

Reads text from stdin and stores it as memory items. Attempts structured proposal extraction first; falls back to single-note storage. All content passes through the policy engine before storage.

#### `memctl search`

```bash
memctl search QUERY [--tier TIER] [--type TYPE] [-k N] [--json]
```

FTS5 full-text search. Returns human-readable output by default, or JSON with `--json`.

#### `memctl consolidate`

```bash
memctl consolidate [--scope S] [--dry-run] [--json]
```

Deterministic consolidation: clusters STM items by type + tag overlap (Jaccard), merges each cluster (longest content wins), promotes to MTM. High-usage MTM items promote to LTM. No LLM calls.

#### `memctl loop`

```bash
memctl push "question" | memctl loop "question" --llm "claude -p" [--max-calls 3] [--protocol json]
```

Bounded recall-answer loop: sends context + question to an external LLM, parses its response for refinement directives, performs additional recalls from the memory store, and detects convergence. The LLM is never autonomous — it only proposes queries. The controller enforces bounds, dedup, and stopping conditions.

**Protocol:** The LLM must output a JSON first line: `{"need_more": bool, "query": "...", "stop": bool}`, followed by its answer. Supported protocols: `json` (default), `regex`, `passive` (single-pass, no refinement).

**Stopping conditions:**
- `llm_stop` — LLM sets `stop: true`
- `fixed_point` — consecutive answers are similar above threshold (default 0.92)
- `query_cycle` — LLM re-requests a query already tried
- `no_new_items` — recall returns no new items for the proposed query
- `max_calls` — iteration limit reached (default 3)

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--llm CMD` | *(required)* | LLM command (e.g. `"claude -p"`, `"ollama run granite3.1:2b"`) |
| `--llm-mode` | `stdin` | How to pass the prompt: `stdin` or `file` |
| `--protocol` | `json` | LLM output protocol: `json`, `regex`, `passive` |
| `--system-prompt` | *(auto)* | Custom system prompt (text or file path) |
| `--max-calls` | `3` | Maximum LLM invocations |
| `--threshold` | `0.92` | Answer fixed-point similarity threshold |
| `--query-threshold` | `0.90` | Query cycle similarity threshold |
| `--stable-steps` | `2` | Consecutive stable steps for convergence |
| `--no-stop-on-no-new` | off | Continue even if recall returns no new items |
| `--budget` | `2200` | Token budget for context |
| `--trace` | off | Emit JSONL trace to stderr |
| `--trace-file` | *(none)* | Write JSONL trace to file |
| `--strict` | off | Exit 1 if max-calls reached without convergence |
| `--timeout` | `300` | LLM subprocess timeout (seconds) |
| `--replay FILE` | *(none)* | Replay a trace file (no LLM calls) |

**Example pipeline:**

```bash
# Iterative recall with Claude
memctl push "How does authentication work?" --source docs/ \
  | memctl loop "How does authentication work?" --llm "claude -p" --trace

# Sovereign local LLM
memctl push "database schema" --source src/ \
  | memctl loop "database schema" --llm "ollama run granite3.1:2b" --protocol json

# Replay a trace (no LLM needed)
memctl loop --replay trace.jsonl "original question"
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMCTL_DB` | `.memory/memory.db` | Path to SQLite database |
| `MEMCTL_BUDGET` | `2200` | Token budget for injection blocks |
| `MEMCTL_FTS` | `fr` | FTS tokenizer preset (`fr`/`en`/`raw`) |
| `MEMCTL_TIER` | `stm` | Default write tier |
| `MEMCTL_SESSION` | *(unset)* | Session ID for audit provenance |

**Precedence:** `CLI --flag` > `MEMCTL_*` env var > compiled default. Always.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (including idempotent no-op) |
| 1 | Operational error (bad args, empty input, policy rejection) |
| 2 | Internal failure (unexpected exception, I/O error) |

---

## Shell Integration

Add to `.bashrc`, `.zshrc`, or your project's `env.sh`:

```bash
export MEMCTL_DB=.memory/memory.db

# Shortcuts
meminit()  { memctl init "${1:-.memory}"; }
memq()     { memctl push "$1"; }                        # recall only
memp()     { memctl push "$1" ${2:+--source "$2"}; }    # push with optional source
mempull()  { memctl pull --tags "${1:-}" ${2:+--title "$2"}; }
```

### Pipe Recipes

```bash
# Ingest docs + recall + feed to LLM + store output
memctl push "API design" --source docs/ | llm "Summarize" | memctl pull --tags api

# Search and pipe to jq
memctl search "auth" --json | jq '.[].title'

# Batch ingest a directory
memctl push "project overview" --source src/ tests/ docs/ -q

# Export all items as JSONL
memctl search "" --json | jq -c '.[]'

# Iterative recall-answer loop with trace
memctl push "auth flow" --source docs/ | memctl loop "auth flow" --llm "claude -p" --trace
```

---

## MCP Server

memctl exposes 7 MCP tools for integration with Claude Code, Claude Desktop, VS Code, and any MCP-compatible client.

### Start the Server

```bash
memctl serve --db .memory/memory.db
# or
python -m memctl.mcp.server --db .memory/memory.db
```

### Claude Code Integration

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "memctl": {
      "command": "memctl",
      "args": ["serve", "--db", ".memory/memory.db"]
    }
  }
}
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `memory_recall` | Token-budgeted context injection (primary tool) |
| `memory_search` | Interactive FTS5 discovery |
| `memory_propose` | Store findings with policy governance |
| `memory_write` | Direct write (privileged/dev operations) |
| `memory_read` | Read items by ID |
| `memory_stats` | Store metrics |
| `memory_consolidate` | Trigger deterministic merge |

Tool names use the `memory_*` prefix for drop-in compatibility with RAGIX.

---

## How It Works

### Architecture

```
memctl/
├── types.py           Data model (MemoryItem, MemoryProposal, MemoryEvent, MemoryLink)
├── store.py           SQLite + FTS5 + WAL backend (9 tables + schema_meta)
├── extract.py         Text extraction (text files + binary format dispatch)
├── ingest.py          Paragraph chunking, SHA-256 dedup, source resolution
├── policy.py          Write governance (30 patterns: secrets, injection, instructional)
├── config.py          Dataclass configuration
├── similarity.py      Stdlib text similarity (Jaccard + SequenceMatcher)
├── loop.py            Bounded recall-answer loop controller
├── cli.py             9 CLI commands
├── consolidate.py     Deterministic merge (Jaccard clustering, no LLM)
├── proposer.py        LLM output parsing (delimiter + regex)
└── mcp/
    ├── tools.py       7 MCP tools (memory_* prefix)
    ├── formatting.py  Injection block format (format_version=1)
    └── server.py      FastMCP server entry point
```

16 source files. ~5,300 lines. Zero compiled dependencies for core.

### Memory Tiers

| Tier | Purpose | Lifecycle |
|------|---------|-----------|
| **STM** (Short-Term) | Recent observations, unverified facts | Created by `pull`. Consolidated or expired. |
| **MTM** (Medium-Term) | Verified, consolidated knowledge | Created by `consolidate`. Promoted by usage. |
| **LTM** (Long-Term) | Stable decisions, definitions, constraints | Promoted from MTM by usage count or type. |

### Policy Engine

Every write path passes through the policy engine. No exceptions.

**Hard blocks** (rejected):
- 10 secret detection patterns (API keys, tokens, passwords, private keys, JWTs)
- 8 injection patterns (prompt override, system prompt fragments)
- 8 instructional block patterns (tool invocation syntax, role fragments)
- Oversized content (>2000 chars for non-pointer types)

**Soft blocks** (quarantined to STM with expiry):
- 4 instructional quarantine patterns (imperative self-instructions)
- Missing provenance or justification
- Quarantined items stored with `injectable=False`

### FTS5 Tokenizer Presets

| Preset | Tokenizer | Use Case |
|--------|-----------|----------|
| `fr` | `unicode61 remove_diacritics 2` | French-safe default (accent normalization) |
| `en` | `porter unicode61 remove_diacritics 2` | English with Porter stemming |
| `raw` | `unicode61` | No diacritics removal, no stemming |

Expert override: `memctl init --fts-tokenizer "porter unicode61 remove_diacritics 2"`

### Supported Formats

| Category | Extensions | Requirement |
|----------|-----------|-------------|
| Text / Markup | `.md` `.txt` `.rst` `.csv` `.tsv` `.html` `.xml` `.json` `.yaml` `.toml` | None (stdlib) |
| Source Code | `.py` `.js` `.ts` `.jsx` `.tsx` `.java` `.go` `.rs` `.c` `.cpp` `.sh` `.sql` `.css` … | None (stdlib) |
| Office Documents | `.docx` `.odt` | `pip install memctl[docs]` |
| Presentations | `.pptx` `.odp` | `pip install memctl[docs]` |
| Spreadsheets | `.xlsx` `.ods` | `pip install memctl[docs]` |
| PDF | `.pdf` | `pdftotext` (poppler-utils) |

All formats are extracted to plain text before chunking and ingestion. Binary format libraries are lazy-imported — a missing library produces a clear `ImportError` with install instructions.

### Content Addressing

Every ingested file is hashed (SHA-256). Re-ingesting the same file is a no-op. Every memory item stores a `content_hash` for deduplication.

### Consolidation

Deterministic, no-LLM merge pipeline:

1. Collect non-archived STM items
2. Cluster by type + tag overlap (Jaccard similarity)
3. Merge each cluster: longest content wins; tie-break by earliest `created_at`, then lexicographic ID
4. Write merged items at MTM tier + `supersedes` links
5. Archive originals (`archived=True`)
6. Promote high-usage MTM items to LTM

---

## Database Schema

Single SQLite file with WAL mode. 9 tables + 1 FTS5 virtual table:

| Table | Purpose |
|-------|---------|
| `memory_items` | Core memory items (22 columns) |
| `memory_revisions` | Immutable revision history |
| `memory_events` | Audit log (every read/write/consolidate) |
| `memory_links` | Directional relationships (supersedes, supports, etc.) |
| `memory_embeddings` | Reserved for RAGIX (empty in memctl) |
| `corpus_hashes` | SHA-256 file dedup registry |
| `corpus_metadata` | Corpus-level metadata |
| `schema_meta` | Schema version, creation info |
| `memory_palace_locations` | Reserved for RAGIX |
| `memory_items_fts` | FTS5 virtual table for full-text search |

Schema version is tracked in `schema_meta`. Current: `SCHEMA_VERSION=1`.

---

## Migration to RAGIX

memctl is extracted from [RAGIX](https://github.com/ovitrac/RAGIX) and maintains schema-identical databases. To upgrade:

```bash
git clone git@github.com:ovitrac/RAGIX.git
cd RAGIX
pip install -e .[all]
# Point at the same database — all items carry over
ragix memory stats --db /path/to/your/.memory/memory.db
```

| Feature | memctl | RAGIX |
|---------|--------|-------|
| SQLite schema | Identical | Identical |
| Injection format | `format_version=1` | `format_version=1` |
| MCP tool names | `memory_*` | `memory_*` |
| FTS5 recall | Yes | Yes (+ hybrid embeddings) |
| Embeddings | No | Yes (FAISS + Ollama) |
| LLM-assisted merge | No | Yes |
| Graph-RAG | No | Yes |
| Reporting | No | Yes |

---

## Python API

```python
from memctl import MemoryStore, MemoryItem, MemoryPolicy

# Open or create a store
store = MemoryStore(db_path=".memory/memory.db")

# Write an item
item = MemoryItem(
    title="Architecture decision",
    content="We chose event sourcing for state management",
    tier="stm",
    type="decision",
    tags=["architecture", "event-sourcing"],
)
store.write_item(item, reason="manual")

# Search
results = store.search_fulltext("event sourcing", limit=10)
for r in results:
    print(f"[{r.tier}] {r.title}: {r.content[:80]}")

# Policy check
policy = MemoryPolicy()
from memctl.types import MemoryProposal
proposal = MemoryProposal(
    title="Config", content="Some content",
    why_store="Important finding",
    provenance_hint={"source_kind": "doc", "source_id": "design.md"},
)
verdict = policy.evaluate_proposal(proposal)
print(verdict.action)  # "accept", "quarantine", or "reject"

store.close()
```

---

## Testing

```bash
pip install memctl[dev]
pytest tests/ -v
```

332 tests covering types, store, policy, ingest, text extraction, similarity, loop controller, forward compatibility, contracts, CLI (subprocess), and pipe composition.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

**Author:** Olivier Vitrac, PhD, HDR | [olivier.vitrac@adservio.fr](mailto:olivier.vitrac@adservio.fr) | Adservio Innovation Lab

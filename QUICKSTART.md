# memctl Quickstart

**Version**: 0.12.0 | **Time to first recall: ~3 minutes**

memctl gives LLMs persistent, structured, policy-governed memory backed by a single SQLite file. Ingest files, recall with FTS5, pipe into any LLM. Zero dependencies beyond Python's stdlib.

---

## 1. Install and Verify

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

**Verify it works:**

```bash
memctl --help
```

You should see the list of 16 commands. If you see `memctl: command not found`, your venv is not activated or pip install target is not on your PATH.

**Requirements:** Python 3.10+ (3.12 recommended). No compiled dependencies for core.
PDF extraction requires `pdftotext` from poppler-utils (`sudo apt install poppler-utils` or `brew install poppler`).

---

## 2. Your First Memory

### Initialize a workspace

```bash
memctl init
# Creates .memory/memory.db, .memory/config.json, .memory/.gitignore
```

Or with eval to set the env var:

```bash
eval $(memctl init)
# Sets MEMCTL_DB=.memory/memory.db
```

### Ingest files and recall

```bash
# Ingest source files + recall matching items
memctl push "authentication" --source src/

# Recall only (no ingestion)
memctl push "database schema"
```

`push` writes an injection block to stdout (ready to pipe into an LLM) and progress to stderr.

### Store an observation

```bash
echo "We chose JWT for stateless auth" | memctl pull --tags auth,decision --title "Auth decision"
```

### Search

```bash
memctl search "authentication"
```

### Full pipeline

```bash
# Ingest docs → recall → feed to LLM → store output
memctl push "API design" --source docs/ | llm "Summarize" | memctl pull --tags api
```

**What happened:**
- `init` created a SQLite database with FTS5 full-text search and WAL mode
- `push --source` ingested files with SHA-256 dedup and paragraph chunking, then recalled matching items
- `pull` stored the LLM's answer as a memory item (after policy checks)
- `search` queried the FTS5 index for matching items

---

## 3. Ingest a Codebase

### Mount and sync

```bash
# Register a folder as a structured source
memctl mount src/

# Delta-sync into the memory store
memctl sync src/
```

### One-liner: inspect

```bash
# Auto-mounts, auto-syncs, and inspects — all in one command
memctl inspect src/
```

Expected output:

```
src/ — 35 files, 71 chunks, 357 KB
  Dominant: .py (26 files), .md (9 files)
  Largest: cli.py (57 KB), store.py (54 KB)
  Observation: src/ dominates content (78% of chunks)
```

One call. ~600 tokens. Replaces 10-15 individual file reads.

### Inspect in JSON (for scripts)

```bash
memctl inspect src/ --json | jq '.extensions'
```

**What happened:**
- `mount` registered the folder metadata (no scanning)
- `sync` applied a 3-tier delta rule: new files ingested, unchanged files skipped (size + mtime), hash-compared files re-ingested only if content changed
- `inspect` combined mount + sync + structural analysis into one command, producing a token-budgeted digest

---

## 4. Ask Questions

### One-shot folder Q&A

```bash
memctl ask docs/ "What authentication risks exist?" --llm "claude -p"
```

This auto-mounts, auto-syncs, inspects the folder, performs scoped recall, and sends everything to the LLM in a single command.

### With a local LLM

```bash
memctl ask src/ "What is under-documented?" --llm "ollama run granite3.1:2b"
```

### Iterative recall-answer loop

```bash
memctl push "auth flow" --source docs/ \
  | memctl loop "auth flow" --llm "claude -p" --trace
```

The loop sends context + question to the LLM, parses refinement directives, performs additional recalls, and detects convergence. Five stopping conditions prevent runaway loops.

### Interactive chat

```bash
memctl chat --llm "claude -p" --session --store
```

Each turn: FTS5 recall → LLM → display answer. `--session` keeps a sliding window of recent Q&A. `--store` persists answers as STM items.

### Folder-scoped chat

```bash
memctl chat --llm "claude -p" --folder docs/ --session --store
```

Every turn's recall is restricted to items from the target folder.

---

## 5. Where Does memctl Work?

| Platform | Integration | Workflow |
|----------|------------|----------|
| **Claude Code (terminal)** | MCP server (14 tools) + optional hooks | Install MCP, then tools are auto-discovered |
| **Claude Code in VS Code** | Same MCP via Claude Code extension | Same as terminal — extension uses CLI |
| **Claude Desktop (macOS)** | MCP server | Configure in `claude_desktop_config.json` |
| **Any LLM CLI** | Unix pipes | `memctl push ... \| llm "..." \| memctl pull ...` |
| **Ollama (local)** | `--llm` flag | `memctl ask src/ "..." --llm "ollama run granite3.1:2b"` |
| **Scripts / CI** | CLI + JSON output | `memctl search "auth" --json \| jq ...` |

### Key rule

memctl works with **any** LLM via Unix pipes. The only difference is integration depth:
- **Claude Code / Desktop**: MCP tools provide rich, structured access (14 tools)
- **Everything else**: use the CLI directly — `push`, `pull`, `search`, `ask`, `loop`, `chat`

---

## 6. MCP Server Setup

### Quick install (recommended)

```bash
# Claude Code (default)
./scripts/install_mcp.sh

# Claude Desktop
./scripts/install_mcp.sh --client claude-desktop

# Both clients
./scripts/install_mcp.sh --client all --yes

# Preview without changes
./scripts/install_mcp.sh --dry-run
```

The installer verifies Python 3.10+, installs `memctl[mcp]`, configures your client, initializes the workspace, and runs `memctl serve --check` to verify.

### Manual setup

```bash
# 1. Install
pip install "memctl[mcp]"

# 2. Initialize workspace
memctl init ~/.local/share/memctl

# 3. Verify
memctl serve --check --db ~/.local/share/memctl/memory.db
```

Then add to your client config:

**Claude Code** (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "memctl": {
      "command": "memctl",
      "args": ["serve", "--db", "~/.local/share/memctl/memory.db"]
    }
  }
}
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "memctl": {
      "command": "memctl",
      "args": ["serve", "--db", "~/.local/share/memctl/memory.db"]
    }
  }
}
```

### Defense in depth

The MCP server applies four protection layers by default:

| Layer | Component | Purpose |
|-------|-----------|---------|
| **L0** | `ServerGuard` | Path validation, write size caps, import batch limits |
| **L1** | `RateLimiter` | Token-bucket throttling: 20 writes/min, 120 reads/min |
| **L1** | `AuditLogger` | Structured JSONL audit trail (schema v1) |
| **L2** | `MemoryPolicy` | 35 detection patterns (secrets, injection, instructional, PII) |
| **L3** | Claude Code hooks | Optional: PreToolUse safety guard + PostToolUse audit logger |

### Available MCP tools (14)

| Tool | Description |
|------|-------------|
| `memory_recall` | Token-budgeted context injection (primary tool) |
| `memory_search` | Interactive FTS5 discovery |
| `memory_propose` | Store findings with policy governance |
| `memory_write` | Direct write (policy-checked) |
| `memory_read` | Read items by ID |
| `memory_stats` | Store metrics |
| `memory_consolidate` | Trigger deterministic merge |
| `memory_mount` | Register, list, or remove folder mounts |
| `memory_sync` | Sync mounted folders (delta or full) |
| `memory_inspect` | Structural injection block from corpus |
| `memory_ask` | One-shot folder Q&A |
| `memory_export` | JSONL export with filters |
| `memory_import` | JSONL import with policy enforcement |
| `memory_loop` | Bounded recall-answer loop |

---

## 7. eco Mode

> **Full guide:** See the [eco Mode Quickstart](ECO_QUICKSTART.md) for a hands-on
> walkthrough — first session, query tips, workflow patterns, binary formats, and troubleshooting.

eco mode replaces sequential file browsing with deterministic structural retrieval. Native Claude reads files one by one. eco Claude queries architecture.

**eco is OFF by default.** It is installed but disabled until explicitly enabled.

### Install and enable

```bash
pip install "memctl[mcp]"
./scripts/install_eco.sh --db-root .memory
memctl eco on    # Required — eco does nothing until enabled
```

### The escalation ladder

1. `memory_inspect` — structural overview (file tree, sizes, observations)
2. `memory_recall` — selective content retrieval (FTS5, token-budgeted)
3. `memory_loop` — iterative refinement (bounded, convergence-detecting)
4. Native `Read`/`View` — last resort for editing or line-level precision

### Query discipline

memctl uses FTS5 full-text search with **AND logic**. Every term must match within a single item. Use identifiers, not sentences:

```
GOOD: IncidentServiceImpl          → finds the service
GOOD: PreAuthorize security        → finds secured controllers
GOOD: Redis caching                → finds caching strategy
BAD:  "how does the system create incidents"  → over-constrained, 0 results
```

Since v0.10.0, stop words (articles, prepositions) are stripped automatically from queries, but fewer terms is still better.

### Disable or check status

```bash
memctl eco off       # Disable
memctl eco status    # Check current state
```

### Uninstall

```bash
./scripts/uninstall_eco.sh
# Removes hook + strategy file. Preserves .memory/memory.db and MCP config.
```

**Full documentation:** [`extras/eco/ECO.md`](extras/eco/ECO.md) | **Pilot guidance:** [`extras/eco/PILOT.md`](extras/eco/PILOT.md)

---

## 8. Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `MEMCTL_DB` | `.memory/memory.db` | Path to SQLite database |
| `MEMCTL_BUDGET` | `2200` | Token budget for injection blocks |
| `MEMCTL_FTS` | `fr` | FTS tokenizer preset (`fr` / `en` / `raw`) |
| `MEMCTL_TIER` | `stm` | Default write tier |
| `MEMCTL_SESSION` | *(unset)* | Session ID for audit provenance |

**Precedence:** `CLI --flag` > `MEMCTL_*` env var > `config.json` > compiled default. Always.

---

## 9. FAQ

### What is the difference between memctl and RAGIX?

memctl is extracted from [RAGIX](https://github.com/ovitrac/RAGIX) and maintains schema-identical databases. memctl is the **memory layer only** — deterministic, zero dependencies, FTS5-based. RAGIX adds embeddings (FAISS + Ollama), LLM-assisted merge, Graph-RAG, and reporting. Upgrade path: `pip install ragix[all]` and point at the same `memory.db`.

### Does memctl call any LLM?

**No.** memctl is deterministic by design. It stores what LLMs produce and provides context for what LLMs consume. Commands like `ask`, `loop`, and `chat` invoke an **external LLM subprocess** that you specify via `--llm CMD` — memctl itself never makes API calls.

### What file formats are supported?

47 extensions total. Text formats (`.py`, `.md`, `.java`, `.json`, `.yaml`, etc.) require no extra dependencies. Office documents (`.docx`, `.odt`, `.pptx`, `.xlsx`) require `pip install memctl[docs]`. PDF requires `pdftotext` from poppler-utils.

### How does policy enforcement work?

Every write path passes through the policy engine. 35 detection patterns check for secrets (API keys, tokens), prompt injection, instructional content, and PII. Dangerous content is rejected; borderline content is quarantined (stored but not injectable into LLM context).

### Can I use memctl without the MCP server?

**Yes.** The CLI is the primary interface. MCP is optional (`pip install memctl[mcp]`). Everything the MCP server does is available via CLI commands and Unix pipes.

### What is FTS5 AND logic?

SQLite FTS5 matches items where **all** query terms appear in a single item. A deterministic cascade (AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK) automatically recovers when strict AND returns 0 results. Prefix expansion (v0.12) handles partial morphological matches. For full stemming: `memctl reindex --tokenizer en`. Short, precise queries (2-3 identifiers) still give the best precision.

### How big can the database get?

SQLite handles databases well into the hundreds of gigabytes. In practice, memctl databases for large codebases (10k+ files) stay under 100 MB. WAL mode ensures concurrent read/write safety.

### Is there a web UI?

No. memctl is a Unix CLI tool and MCP server. For visual exploration, use `memctl search --json | jq` or pipe to your preferred viewer.

---

## 10. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `memctl: command not found` | Venv not activated or not on PATH | `source .venv/bin/activate` or check pip install target |
| `No module named 'mcp'` | MCP extra not installed | `pip install "memctl[mcp]"` |
| `No module named 'docx'` | Docs extra not installed | `pip install "memctl[docs]"` |
| 0 search results | FTS5 AND logic — too many terms | Use 2-3 identifiers, not full sentences |
| `pdftotext` missing | Poppler not installed | `sudo apt install poppler-utils` or `brew install poppler` |
| Duplicate items after re-push | Should not happen (SHA-256 dedup) | Check if file content actually changed |
| Policy rejects valid content | Detection pattern false positive | Check `memctl show ID` for rejection reason; file an issue |
| MCP server won't start | Wrong `--db` path or missing init | `memctl init PATH` then `memctl serve --check --db PATH/memory.db` |
| `SCHEMA_VERSION` mismatch | Old database with new memctl | Migration is automatic and idempotent (ALTER TABLE ADD COLUMN) |
| Import errors on all items | Policy blocks every item | `memctl import FILE --dry-run` to diagnose; check content for secrets/PII |

---

## 11. Next Steps

| Want to... | Read |
|-----------|------|
| Get started with eco mode in Claude Code | [ECO_QUICKSTART.md](ECO_QUICKSTART.md) |
| See the full CLI reference | [README.md — CLI Reference](README.md#cli-reference) |
| Understand the architecture | [README.md — How It Works](README.md#how-it-works) |
| Set up eco mode for a team | [extras/eco/PILOT.md](extras/eco/PILOT.md) |
| Learn eco query discipline | [extras/eco/ECO.md](extras/eco/ECO.md) |
| Run the demo scripts | [demos/](demos/) |
| Migrate to RAGIX | [README.md — Migration to RAGIX](README.md#migration-to-ragix) |
| Explore the Python API | [README.md — Python API](README.md#python-api) |
| Review the database schema | [README.md — Database Schema](README.md#database-schema) |

---

*memctl v0.12.0 — Olivier Vitrac, Adservio Innovation Lab*

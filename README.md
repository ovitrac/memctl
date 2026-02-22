<div align="center">
<p align="center">
  <img src="assets/memctl-logo.png" alt="memctl Logo" height="128"><br>
</p>

# memctl

### One file, one truth. Memory for your LLMs.

**A Unix-native memory control plane for LLM orchestration — zero dependencies, policy-governed, MCP-native**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.12.3-orange.svg)](https://github.com/ovitrac/memctl/releases)
[![Tests](https://img.shields.io/badge/tests-966%20passing-brightgreen.svg)](./tests)
[![MCP](https://img.shields.io/badge/MCP-15%20tools-blueviolet.svg)](#mcp-server)
[![DeepWiki](https://img.shields.io/badge/Docs-DeepWiki-purple.svg)](https://deepwiki.com/ovitrac/memctl)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

[Why memctl](#why-memctl) • [Quick Start](QUICKSTART.md) • [eco for Claude Code](ECO_QUICKSTART.md) • [Installation](#installation) • [CLI Reference](#cli-reference) • [MCP Server](#mcp-server) • [How It Works](#how-it-works)

</div>

---

## Why memctl?

> **New to memctl?** See the full [Quickstart Guide](QUICKSTART.md) with FAQ, compatibility matrix, and troubleshooting.

LLMs forget everything between turns. memctl gives them persistent, structured, policy-governed memory backed by a single SQLite file.

- **Zero dependencies** — stdlib only. No numpy, no torch, no compiled extensions.
- **One file** — Everything in `memory.db` (SQLite + FTS5 + WAL).
- **Unix composable** — `push` writes to stdout, `pull` reads from stdin. Pipe freely.
- **Policy-governed** — 35 detection patterns block secrets, injection, instructional content, and PII before storage.
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
# Creates .memory/memory.db, .memory/config.json, .memory/.gitignore
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

### 5. Inspect a folder (one-liner)

```bash
# Auto-mounts, auto-syncs, and inspects — all in one command
memctl inspect docs/

# Same in JSON (for scripts)
memctl inspect docs/ --json

# Skip sync (use cached state)
memctl inspect docs/ --no-sync
```

`inspect` auto-mounts the folder if needed, checks staleness, syncs only if stale, and produces a structural summary. All implicit actions are announced on stderr.

### 6. Ask a question about a folder

```bash
# One-shot: auto-mount, auto-sync, inspect + recall → LLM → answer
memctl ask docs/ "What authentication risks exist?" --llm "claude -p"

# With Ollama
memctl ask src/ "What is under-documented?" --llm "ollama run granite3.1:2b"

# JSON output with metadata
memctl ask docs/ "Summarize the architecture" --llm "claude -p" --json
```

`ask` combines mount, sync, structural inspection, and scoped recall into a single command. The LLM receives both the folder structure and content context.

### 7. Chat with memory-backed context

```bash
# Interactive chat with any LLM
memctl chat --llm "claude -p" --session

# With pre-ingested files and answer storage
memctl chat --llm "ollama run granite3.1:2b" --source docs/ --store --session
```

Each question recalls from the memory store, sends context + question to the LLM, and displays the answer. `--session` keeps a sliding window of recent Q&A pairs. `--store` persists answers as STM items.

### 8. Manage

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
| `mount PATH` | Register a folder as a structured source |
| `sync [PATH]` | Delta-sync mounted folders into the store |
| `inspect [PATH]` | Structural inspection with auto-mount and auto-sync |
| `ask PATH "Q" --llm CMD` | One-shot folder Q&A (inspect + scoped recall + loop) |
| `chat --llm CMD` | Interactive memory-backed chat REPL |
| `export [--tier T]` | Export memory items as JSONL to stdout |
| `import [FILE]` | Import memory items from JSONL file or stdin |
| `serve` | Start MCP server (requires `memctl[mcp]`) |

### Global Flags

| Flag | Description |
|------|-------------|
| `--db PATH` | SQLite database path |
| `--config PATH` | Path to `config.json` (auto-detected beside database) |
| `--json` | Machine-readable JSON output |
| `-q, --quiet` | Suppress stderr progress messages |
| `-v, --verbose` | Enable debug logging |

### Command Details

#### `memctl init`

```bash
memctl init [PATH] [--force] [--fts-tokenizer fr|en|raw]
```

Creates the workspace directory, SQLite database with schema, `config.json`, and `.gitignore`. Prints `export MEMCTL_DB="..."` to stdout for eval.

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

#### `memctl mount`

```bash
memctl mount PATH [--name NAME] [--ignore PATTERN ...] [--lang HINT]
memctl mount --list
memctl mount --remove ID_OR_NAME
```

Registers a folder as a structured source. Stores metadata only — no scanning, no ingestion. The folder contents are synced separately via `sync` or automatically via `inspect`.

#### `memctl sync`

```bash
memctl sync [PATH] [--full] [--json] [--quiet]
```

Delta-syncs mounted folders into the memory store. Uses a 3-tier delta rule:
1. **New file** (not in DB) → ingest
2. **Size + mtime match** → fast skip (no hashing)
3. **Hash compare** → ingest only if content changed

If `PATH` is given but not yet mounted, it is auto-registered first. `--full` forces re-processing of all files.

#### `memctl inspect`

```bash
# Orchestration mode — auto-mounts, auto-syncs, and inspects
memctl inspect PATH [--sync auto|always|never] [--no-sync] [--mount-mode persist|ephemeral]
                    [--budget N] [--ignore PATTERN ...] [--json] [--quiet]

# Classic mode — inspect an existing mount by ID/name
memctl inspect --mount ID_OR_NAME [--budget N] [--json] [--quiet]
```

When given a positional `PATH`, inspect operates in **orchestration mode**:
1. **Auto-mount** — registers the folder if not already mounted
2. **Staleness check** — compares disk inventory (path/size/mtime triples) against the store
3. **Auto-sync** — runs delta sync only if stale (or always/never per `--sync`)
4. **Inspect** — generates a deterministic structural summary

Output includes file/chunk/size totals, per-folder breakdown, per-extension distribution, top-5 largest files, and rule-based observations. All paths in output are mount-relative (never absolute).

`--mount-mode ephemeral` removes the mount record after inspection (corpus data is preserved). `--no-sync` is shorthand for `--sync never`.

All implicit actions (mount, sync) are announced on stderr. `--quiet` suppresses them.

#### `memctl ask`

```bash
memctl ask PATH "question" --llm CMD [--inspect-cap N] [--budget N]
           [--sync auto|always|never] [--no-sync] [--mount-mode persist|ephemeral]
           [--protocol passive|json|regex] [--max-calls N] [--json] [--quiet]
```

One-shot folder Q&A. Orchestrates auto-mount, auto-sync, structural inspection, scoped recall, and bounded loop — all in one command.

| Flag | Default | Description |
|------|---------|-------------|
| `--llm CMD` | *(required)* | LLM command (e.g. `"claude -p"`) |
| `--inspect-cap` | `600` | Tokens reserved for structural context |
| `--budget` | `2200` | Total token budget (inspect + recall) |
| `--sync` | `auto` | Sync mode: `auto`, `always`, `never` |
| `--no-sync` | off | Skip sync (shorthand for `--sync never`) |
| `--mount-mode` | `persist` | Keep mount (`persist`) or remove after (`ephemeral`) |
| `--protocol` | `passive` | LLM output protocol |
| `--max-calls` | `1` | Max loop iterations |

**Budget splitting:** `--inspect-cap` tokens go to structural context (folder tree, observations). The remainder (`--budget` minus `--inspect-cap`) goes to content recall (FTS5 results scoped to the folder).

**Scoped recall:** FTS results are post-filtered to include only items from the target folder's mount. Items from other mounts are excluded.

#### `memctl chat`

```bash
memctl chat --llm CMD [--session] [--store] [--folder PATH]
            [--protocol passive|json|regex] [--max-calls N] [--budget N]
            [--source FILE ...] [--quiet]
```

Interactive memory-backed chat REPL. Each turn: FTS5 recall from the memory store, send context + question to the LLM, display the answer. Persistent readline history (`~/.local/share/memctl/chat_history`) and multi-line input (blank line to send).

**Stateless by default.** Each question sees only the memory store — no hidden conversation state.

| Flag | Default | Description |
|------|---------|-------------|
| `--llm CMD` | *(required)* | LLM command (e.g. `"claude -p"`, `"ollama run granite3.1:2b"`) |
| `--protocol` | `passive` | LLM output protocol. `passive` = single-pass; `json` = iterative refinement |
| `--max-calls` | `1` | Max loop iterations per turn |
| `--session` | off | Enable in-memory session context (sliding window of recent Q&A) |
| `--history-turns` | `5` | Session window size (turns) |
| `--session-budget` | `4000` | Session block character limit |
| `--store` | off | Persist each answer as STM item |
| `--source FILE...` | *(none)* | Pre-ingest files before starting |
| `--folder PATH` | *(none)* | Scope recall to a folder (auto-mount/sync) |
| `--tags` | `chat` | Tags for stored items (comma-separated) |

**Folder-scoped chat:** `--folder PATH` auto-mounts and syncs the folder, then restricts every turn's recall to that folder's items. Combines the convenience of `ask` with the interactivity of `chat`.

**stdout purity:** answers go to stdout only. Prompt, banner, and hints go to stderr.

#### `memctl export`

```bash
memctl export [--tier T] [--type T] [--scope S] [--include-archived]
```

Exports memory items as JSONL (one JSON object per line) to stdout. Each line is a complete `MemoryItem.to_dict()` serialization including full provenance.

```bash
# Export all items
memctl export > backup.jsonl

# Export only LTM decisions
memctl export --tier ltm --type decision > decisions.jsonl

# Pipe between databases
memctl export --db project-a.db | memctl import --db project-b.db
```

**stdout purity:** only JSONL data goes to stdout. Progress goes to stderr.

#### `memctl import`

```bash
memctl import [FILE] [--preserve-ids] [--dry-run]
```

Imports memory items from a JSONL file or stdin. Every item passes through the policy engine. Content-hash deduplication prevents duplicates.

| Flag | Default | Description |
|------|---------|-------------|
| `FILE` | stdin | JSONL file to import |
| `--preserve-ids` | off | Keep original item IDs (default: generate new IDs) |
| `--dry-run` | off | Count items without writing |

```bash
# Import from file
memctl import backup.jsonl --db fresh.db

# Dry run — see what would happen
memctl import backup.jsonl --dry-run

# Preserve original IDs (for controlled migration)
memctl import backup.jsonl --preserve-ids --db replica.db
```

---

## Configuration

memctl reads an optional `config.json` file from beside the database (auto-detected) or from an explicit `--config PATH` flag.

```json
{
  "store": {"fts_tokenizer": "fr"},
  "inspect": {
    "dominance_frac": 0.40,
    "low_density_threshold": 0.10,
    "ext_concentration_frac": 0.75,
    "sparse_threshold": 1
  },
  "chat": {"history_max": 1000}
}
```

**Precedence:** `CLI --flag` > `MEMCTL_*` env var > `config.json` > compiled default. Missing or invalid config file is silently ignored.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMCTL_DB` | `.memory/memory.db` | Path to SQLite database |
| `MEMCTL_BUDGET` | `2200` | Token budget for injection blocks |
| `MEMCTL_FTS` | `fr` | FTS tokenizer preset (`fr`/`en`/`raw`) |
| `MEMCTL_TIER` | `stm` | Default write tier |
| `MEMCTL_SESSION` | *(unset)* | Session ID for audit provenance |

**Precedence:** `CLI --flag` > `MEMCTL_*` env var > `config.json` > compiled default. Always.

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

# Export all items as JSONL backup
memctl export > backup.jsonl

# Export only LTM items
memctl export --tier ltm > decisions.jsonl

# Import into a fresh database
memctl import backup.jsonl --db fresh.db

# Pipe between databases
memctl export --db project-a.db | memctl import --db project-b.db

# Dry-run import to check counts
memctl import backup.jsonl --dry-run

# Iterative recall-answer loop with trace
memctl push "auth flow" --source docs/ | memctl loop "auth flow" --llm "claude -p" --trace

# One-liner: inspect a folder (auto-mount + auto-sync)
memctl inspect docs/

# Inspect in JSON, pipe to jq for extension breakdown
memctl inspect src/ --json | jq '.extensions'

# Inspect without syncing (use cached state)
memctl inspect docs/ --no-sync --json

# One-shot folder Q&A (inspect + scoped recall + LLM)
memctl ask docs/ "What are the auth risks?" --llm "claude -p"

# Folder Q&A with JSON output
memctl ask src/ "Summarize the architecture" --llm "claude -p" --json

# Interactive folder-scoped chat
memctl chat --llm "claude -p" --folder docs/ --session --store

# Interactive chat with pre-ingested docs
memctl chat --llm "claude -p" --source docs/ --session --store
```

---

## MCP Server

memctl exposes 14 MCP tools for integration with Claude Code, Claude Desktop, and any MCP-compatible client.

### Quick Install

The installer checks prerequisites, installs `memctl[mcp]`, configures your client, initializes the workspace, and verifies the server starts:

```bash
# Claude Code (default)
bash "$(memctl scripts-path)/install_mcp.sh"

# Claude Desktop
bash "$(memctl scripts-path)/install_mcp.sh" --client claude-desktop

# Both clients (non-interactive)
bash "$(memctl scripts-path)/install_mcp.sh" --client all --yes

# Custom Python / database path
bash "$(memctl scripts-path)/install_mcp.sh" --python /usr/bin/python3.12 --db ~/my-project/.memory/memory.db

# Preview without changes
bash "$(memctl scripts-path)/install_mcp.sh" --dry-run
```

The installer:
- Verifies Python 3.10+ and pip
- Runs `pip install -U "memctl[mcp]"` (idempotent)
- Creates `~/.local/share/memctl/memory.db` if missing
- Inserts/updates the `memctl` entry in the client's MCP config (timestamped `.bak` backup)
- Runs `memctl serve --check` to verify the server starts

Supported platforms: macOS and Linux.

### Manual Setup

If you prefer manual configuration:

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

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

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

### Start the Server

```bash
memctl serve --db ~/.local/share/memctl/memory.db
# or
python -m memctl.mcp.server --db ~/.local/share/memctl/memory.db
```

### Defense in Depth (v0.8)

The MCP server applies four layers of protection:

| Layer | Component | Purpose |
|-------|-----------|---------|
| **L0** | `ServerGuard` | Path validation (`--db-root`), write size caps, import batch limits |
| **L1** | `RateLimiter` | Token-bucket throttling: 20 writes/min, 120 reads/min per session |
| **L1** | `SessionTracker` | In-memory session state, per-turn write tracking |
| **L1** | `AuditLogger` | Structured JSONL audit trail (schema v1, `rid` correlation) |
| **L2** | `MemoryPolicy` | 35 detection patterns (secrets, injection, instructional, PII) |
| **L3** | Claude Code hooks | Optional: PreToolUse safety guard + PostToolUse audit logger |

**Secure server example:**

```bash
# Default: db-root enforced, rate limits on, audit to stderr
memctl serve --db project/memory.db

# Explicit secure mode with audit file
memctl serve --db memory.db --db-root . --audit-log audit.jsonl

# Disable rate limits (development only)
memctl serve --db memory.db --no-rate-limit
```

**Claude Code hooks** (optional, separate from core):

```bash
# Install safety guard + audit logger hooks
bash "$(memctl scripts-path)/install_claude_hooks.sh"

# Uninstall
bash "$(memctl scripts-path)/uninstall_mcp.sh" --hooks-only
```

### MCP Tools

| Tool | Description | Since |
|------|-------------|-------|
| `memory_recall` | Token-budgeted context injection (primary tool) | v0.1 |
| `memory_search` | Interactive FTS5 discovery | v0.1 |
| `memory_propose` | Store findings with policy governance | v0.1 |
| `memory_write` | Direct write (privileged/dev, policy-checked) | v0.1 |
| `memory_read` | Read items by ID | v0.1 |
| `memory_stats` | Store metrics | v0.1 |
| `memory_consolidate` | Trigger deterministic merge | v0.1 |
| `memory_mount` | Register, list, or remove folder mounts | v0.7 |
| `memory_sync` | Sync mounted folders (delta or full) | v0.7 |
| `memory_inspect` | Structural injection block from corpus | v0.7 |
| `memory_ask` | One-shot folder Q&A | v0.7 |
| `memory_export` | JSONL export with filters | v0.7 |
| `memory_import` | JSONL import with policy enforcement | v0.7 |
| `memory_loop` | Bounded recall-answer loop | v0.7 |
| `memory_reindex` | Rebuild FTS5 index (tokenizer change) | v0.12 |

Tool names use the `memory_*` prefix for drop-in compatibility with RAGIX.

### eco mode (v0.9+)

> **Using Claude Code?** See the [eco Mode Quickstart](ECO_QUICKSTART.md) for a hands-on
> walkthrough — install, first session, query tips, workflow patterns, and troubleshooting.

Native Claude reads files. eco Claude queries architecture.

eco mode replaces sequential file browsing with deterministic structural retrieval
and persistent cross-file reasoning. Surgical chunk retrieval (exact algorithm, not
file header), cross-file invariant discovery (architecture in tests), bounded cost
(~5x token reduction).

**eco is OFF by default.** It is installable but disabled until explicitly enabled.
This prevents the "0 results" first-impression problem with untrained users.

**One-shot install:**

```bash
pip install "memctl[mcp]"
bash "$(memctl scripts-path)/install_eco.sh" --db-root .memory
memctl eco on    # Enable eco mode (required)
```

This sets up:
- MCP server with project-scoped memory (`.memory/memory.db`)
- Hook that reminds Claude to prefer `memory_inspect` and `memory_recall` (~50 tokens/turn)
- Strategy file (`.claude/eco/ECO.md`) with the escalation ladder + FTS5 query discipline
- `/eco` slash command for live toggle (`/eco on`, `/eco off`, `/eco status`)

**The escalation ladder:**

1. `memory_inspect` — structural overview (file tree, sizes, observations)
2. `memory_recall` — selective content retrieval (FTS5, token-budgeted)
3. `memory_loop` — iterative refinement (bounded, convergence-detecting)
4. Native `Read`/`View` — last resort for editing or line-level precision

eco mode is advisory for retrieval, not restrictive for editing.

**Query normalization (v0.10):** Stop words (French + English articles, prepositions,
question words) are stripped automatically before FTS search. Code identifiers
(CamelCase, snake_case, UPPER_CASE) are always preserved.

**FTS cascade (v0.11+):** When a multi-term query returns 0 results, the system
automatically cascades: AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK. Prefix
expansion (v0.12) uses `"term"*` for terms ≥5 chars, skipped with Porter stemming.
Each step is logged and the strategy (`fts_strategy`) is reported in MCP responses.

**Stemming (v0.12):** `memctl reindex --tokenizer en` enables Porter stemming for
English codebases. The `reindex` command logs metadata to `schema_meta` and emits
audit events. Use `memctl stats` to check tokenizer and mismatch status.

**Pilot guidance:** See [`extras/eco/PILOT.md`](extras/eco/PILOT.md) for a generic
framework to evaluate eco mode with a development team (20-30 developers, 2-4 weeks,
metrics, exit criteria).

**Demo:** `bash demos/eco_demo.sh` — 4-act demo on the full codebase.

**Uninstall:**

```bash
bash "$(memctl scripts-path)/uninstall_eco.sh"
# Removes hook + strategy file. Preserves .memory/memory.db and MCP config.
```

---

## How It Works

### Architecture

```
memctl/
├── types.py           Data model (MemoryItem, MemoryProposal, MemoryEvent, MemoryLink)
├── store.py           SQLite + FTS5 + WAL backend (10 tables + schema_meta)
├── extract.py         Text extraction (text files + binary format dispatch)
├── ingest.py          Paragraph chunking, SHA-256 dedup, source resolution
├── policy.py          Write governance (35 patterns: secrets, injection, instructional, PII)
├── config.py          Dataclass configuration + JSON config loading
├── similarity.py      Stdlib text similarity (Jaccard + SequenceMatcher)
├── loop.py            Bounded recall-answer loop controller
├── mount.py           Folder mount registration and management
├── sync.py            Delta sync with 3-tier change detection
├── inspect.py         Structural inspection and orchestration
├── chat.py            Interactive chat REPL (readline history, multi-line)
├── ask.py             One-shot folder Q&A orchestrator
├── query.py           FTS query normalization and intent classification
├── export_import.py   JSONL export/import with policy enforcement
├── cli.py             16 CLI commands
├── consolidate.py     Deterministic merge (Jaccard clustering, no LLM)
├── proposer.py        LLM output parsing (delimiter + regex)
└── mcp/
    ├── tools.py       14 MCP tools (memory_* prefix)
    ├── formatting.py  Injection block format (format_version=1)
    └── server.py      FastMCP server entry point
```

23 source files. ~8,700 lines. Zero compiled dependencies for core.

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
- 5 PII patterns (SSN, credit card, email, phone, IBAN)
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

Single SQLite file with WAL mode. 10 tables + 1 FTS5 virtual table:

| Table | Purpose |
|-------|---------|
| `memory_items` | Core memory items (22 columns) |
| `memory_revisions` | Immutable revision history |
| `memory_events` | Audit log (every read/write/consolidate) |
| `memory_links` | Directional relationships (supersedes, supports, etc.) |
| `memory_embeddings` | Reserved for RAGIX (empty in memctl) |
| `corpus_hashes` | SHA-256 file dedup + mount metadata (mount_id, rel_path, ext, size_bytes, mtime_epoch, lang_hint) |
| `corpus_metadata` | Corpus-level metadata |
| `schema_meta` | Schema version, creation info |
| `memory_palace_locations` | Reserved for RAGIX |
| `memory_mounts` | Registered folder mounts (path, name, ignore patterns, lang hint) |
| `memory_items_fts` | FTS5 virtual table for full-text search |

Schema version is tracked in `schema_meta`. Current: `SCHEMA_VERSION=2`. Migration from v1 is additive (ALTER TABLE ADD COLUMN) and idempotent.

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
| SQLite schema | Forward-compatible (RAGIX can open memctl DBs) | Superset |
| Injection format | `format_version=1` | `format_version=1` |
| MCP tool names | `memory_*` | `memory_*` |
| FTS5 recall | Yes | Yes (+ hybrid embeddings) |
| Folder mount + sync | Yes (v0.3+) | No |
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

859 tests across 22 test files covering types, store, policy, ingest, text extraction, similarity, loop controller, mount, sync, inspect, ask, chat, export/import, config, forward compatibility, contracts, CLI (subprocess), pipe composition, MCP tools, PII detection, config validation, exit codes, query normalization, injection integrity, mode classification, and escalation ladder.

---

## Documentation

| Document | Description |
|----------|-------------|
| **[`README.md`](README.md)** | This file — overview, CLI reference, MCP server, architecture |
| **[`QUICKSTART.md`](QUICKSTART.md)** | General quickstart: install, first memory, ingest, ask, MCP setup, FAQ |
| **[`ECO_QUICKSTART.md`](ECO_QUICKSTART.md)** | eco mode for Claude Code: first session, query tips, workflow patterns, binary formats |
| **[`CHANGELOG.md`](CHANGELOG.md)** | Full release history (Keep a Changelog format) |
| **[`extras/eco/ECO.md`](extras/eco/ECO.md)** | eco behavioral strategy (installed at `.claude/eco/ECO.md`) |
| **[`extras/eco/PILOT.md`](extras/eco/PILOT.md)** | Pilot guidance for team evaluation (20-30 developers, 2-4 weeks) |
| **[`extras/eco/README.md`](extras/eco/README.md)** | eco mode technical overview and installation reference |

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

**Author:** Olivier Vitrac, PhD, HDR | [olivier.vitrac@adservio.fr](mailto:olivier.vitrac@adservio.fr) | Adservio Innovation Lab

---

## Links

- **Repository**: https://github.com/ovitrac/memctl
- **PyPI**: https://pypi.org/project/memctl/
- **Issues**: https://github.com/ovitrac/memctl/issues
- **Documentation**: [DeepWiki](https://deepwiki.com/ovitrac/memctl)
- **License**: [MIT](./LICENSE)

---

<div align="center">

*"Every line of code should earn its place. When in doubt, leave it out."*

**[Back to Top](#memctl)**

</div>

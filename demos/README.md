# memctl Demo

Interactive demonstration of memctl's core capabilities:
determinism, security boundaries, Unix composability, and auditability.

## Quick Start

```bash
# From the project root
./demos/run_demo.sh --no-llm --keep

# With LLM reasoning (requires Ollama and/or Claude CLI)
./demos/run_demo.sh --keep
```

## Prerequisites

- **Python 3.10+** with memctl installed (`pip install -e .` from project root)
- **sqlite3** CLI (for raw audit queries in Act 7)

Optional (Act 9 — LLM Reasoning):
- **Ollama** with `granite3.1-moe:3b` (or `--model <name>`)
- **Claude CLI** (`claude` in PATH)

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--keep` | off | Preserve workspace after demo |
| `--workspace DIR` | `/tmp/memctl-demo-*` | Custom workspace directory |
| `--corpus DIR` | auto-detect | Custom corpus directory |
| `--budget N` | `2200` | Token budget for injection blocks |
| `--model MODEL` | `granite3.1-moe:3b` | Ollama model for Act 9 |
| `--no-llm` | off | Skip Act 9 (LLM reasoning) |
| `--interactive` | off | Pause between acts |
| `--verbose` | off | Show full command output |

## Corpus Detection

The demo looks for source documents in this order:

1. **`--corpus DIR`** — explicit flag
2. **`RAGIX_HOME`** env var — `$RAGIX_HOME/docs/*.md` + `$RAGIX_HOME/README.md`
3. **Auto-detect** — known RAGIX locations (`../RAGIX`, `~/Documents/Adservio/Projects/RAGIX`, `~/RAGIX`)
4. **Bundled fallback** — `demos/corpus/` (4 files: architecture, security, database, conception)

When RAGIX docs are detected, only root-level `*.md` files are ingested (no subdirectories).

## Acts

| Act | Title | What It Proves |
|-----|-------|----------------|
| 1 | Init Workspace | One-liner setup, no config files |
| 2 | Ingest Corpus | SHA-256 dedup, paragraph chunking, stats |
| 3 | The Sovereign Loop | push (recall) -> LLM (simulated) -> pull (store) |
| 4 | Deterministic Recall | Same query = same result (SHA-256 proof), French accent folding |
| 5 | Idempotent Ingestion | Re-ingest same corpus = zero new items |
| 6 | Policy Boundary | Secrets blocked, injection blocked, instructional quarantined, clean passes |
| 7 | Audit Trail | Revision history, event log, content hashes (memory time travel) |
| 8 | Unix Composability | Pipes, env vars, exit codes, context isolation, FTS5 benchmark |
| 9 | LLM Reasoning | Ollama (local, sovereign) + Claude (cloud, piped) — optional |
| 10 | RAGIX Upgrade Path | Same DB opens in RAGIX, feature comparison — optional |

## Examples

```bash
# Full demo with bundled corpus, no LLM
./demos/run_demo.sh --no-llm --keep

# Custom corpus from your own project docs
./demos/run_demo.sh --corpus ~/project/docs/ --no-llm

# With RAGIX docs as corpus (auto-detected or explicit)
RAGIX_HOME=~/RAGIX ./demos/run_demo.sh --keep

# LLM demo with a specific model
./demos/run_demo.sh --model mistral:latest --budget 3000

# Interactive mode (pauses between acts)
./demos/run_demo.sh --interactive --no-llm
```

## Bundled Corpus

The `corpus/` directory contains 4 small Markdown files for standalone use:

| File | Language | Topic |
|------|----------|-------|
| `architecture.md` | EN | Microservices, event sourcing, gRPC, Kubernetes |
| `security.md` | EN | JWT, RBAC, OWASP Top 10, encryption, audit |
| `database.md` | EN | PostgreSQL, migrations, indexing, partitioning |
| `conception.md` | FR | Authentification, chiffrement, RGPD (accent folding demo) |

## After the Demo

If you used `--keep`, explore the workspace:

```bash
export MEMCTL_DB=/tmp/memctl-demo-XXXXXX/memory.db
memctl stats
memctl search "architecture" --json
memctl show MEM-xxxxxxxxxxxx
sqlite3 "$MEMCTL_DB" ".tables"
sqlite3 "$MEMCTL_DB" "SELECT COUNT(*) FROM memory_events"
```

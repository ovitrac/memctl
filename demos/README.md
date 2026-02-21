# memctl Demos

Demonstrations of memctl's core capabilities: persistent structured memory, Unix composability, security boundaries, auditability, and bounded LLM loops.

## Quick Start

```bash
# From the project root
pip install -e .

# Tier 1: The launch demo (30s, no dependencies beyond memctl)
bash demos/must_have_demo.sh

# Tier 2: Loop + consolidation (60s, uses bundled mock LLM)
bash demos/advanced_demo.sh

# Chat REPL demo (mock LLM, multi-turn conversation)
bash demos/chat_demo.sh

# eco mode: structural retrieval on the full codebase (~8s)
bash demos/eco_demo.sh

# Full 10-act showcase (comprehensive, optional LLM integration)
bash demos/run_demo.sh --no-llm --keep
```

## Demo Tiers

### Tier 1: `must_have_demo.sh` — The Launch Demo

**The one demo you run first.** Proves the 5 core control-plane properties in under 30 seconds. Self-contained, deterministic, no LLM needed.

| Step | Property | What It Proves |
|------|----------|----------------|
| 1/5 | Zero-config init | One command → one SQLite file (FTS5 + WAL) |
| 2/5 | Ingest + recall | Files in → injection block on stdout (pipeable) |
| 3/5 | Content-addressed dedup | Same files twice → zero new items (SHA-256) |
| 4/5 | Policy-governed writes | GitHub PAT hard-rejected (exit 1), clean content passes |
| 5/5 | Full audit trail | Every write traced with timestamp, action, item_id |

**Why this matters for non-specialists:**

LLMs (like ChatGPT, Claude, Copilot) forget everything between conversations. Every time you start a new chat, the AI starts from scratch. memctl solves this: it gives an LLM a structured, persistent memory — stored in a single file on your machine. No cloud service. No API keys. No vendor lock-in.

Think of it like this: if an LLM is a brilliant consultant with amnesia, memctl is their notebook — one they can't lose, can't leak secrets from, and that keeps a complete audit log of everything they've ever written in it.

```bash
bash demos/must_have_demo.sh           # run and auto-cleanup
bash demos/must_have_demo.sh --keep    # preserve workspace to explore
```

### Tier 2: `advanced_demo.sh` — Loop + Consolidation

**Follow-up demo.** Proves that memctl isn't just "store and retrieve" — it's a control plane for iterative LLM reasoning.

| Step | Property | What It Proves |
|------|----------|----------------|
| 1/4 | Bounded recall-answer loop | LLM proposes queries, controller enforces limits |
| 2/4 | Convergence detection | 5 stopping conditions (llm_stop, fixed_point, cycle, no_new, max_calls) |
| 3/4 | JSONL trace + replay | Every iteration logged, replayable without LLM |
| 4/4 | Deterministic consolidation | STM → MTM merge, no LLM needed |

**Why this matters for non-specialists:**

Imagine asking a research assistant to analyze a topic. They read some documents, realize they need more, ask for specific additional material, read that too, and eventually give you a comprehensive answer. That's what `memctl loop` does — but with guardrails:

- The assistant can only ask for more material a limited number of times
- If they keep asking the same question, the system detects the cycle and stops
- If their answer stops changing, the system recognizes convergence
- Every step is logged and can be replayed later for auditing

Uses a bundled mock LLM (deterministic bash script). No network, no API keys.

```bash
bash demos/advanced_demo.sh            # run and auto-cleanup
bash demos/advanced_demo.sh --keep     # preserve workspace + traces
```

Feature-gated: if `loop` or `consolidate` are unavailable in the installed version, the demo gracefully skips those steps with `[SKIP]` instead of failing.

### Full Showcase: `run_demo.sh` — 10-Act Comprehensive Demo

The complete demonstration covering all memctl capabilities across 10 acts.

| Act | Title | What It Proves |
|-----|-------|----------------|
| 1 | Init Workspace | One-liner setup, no config files |
| 2 | Ingest Corpus | SHA-256 dedup, paragraph chunking, stats |
| 3 | The Sovereign Loop | push → LLM (simulated) → pull |
| 4 | Deterministic Recall | Same query = same result, French accent folding |
| 5 | Idempotent Ingestion | Re-ingest = zero new items |
| 6 | Policy Boundary | Secrets blocked, injection blocked, instructional quarantined |
| 7 | Audit Trail | Revision history, event log, content hashes |
| 8 | Unix Composability | Pipes, env vars, exit codes, context isolation |
| 9 | LLM Reasoning | Ollama (local) + Claude (cloud) — optional |
| 10 | RAGIX Upgrade Path | Same DB opens in RAGIX — optional |

```bash
bash demos/run_demo.sh --no-llm --keep                     # without LLM
bash demos/run_demo.sh --keep                               # with LLM (auto-detect)
bash demos/run_demo.sh --model mistral:latest --budget 3000 # custom model
bash demos/run_demo.sh --interactive --no-llm               # pause between acts
```

### Chat Demo: `chat_demo.sh` — Interactive Chat REPL

Demonstrates the interactive memory-backed chat REPL with a mock LLM.

| Step | What It Proves |
|------|----------------|
| 1/3 | Workspace init + corpus ingestion |
| 2/3 | Multi-turn chat with context recall per turn |
| 3/3 | Chat answers stored as STM items |

```bash
bash demos/chat_demo.sh
```

### eco Mode Demo: `eco_demo.sh` — Deterministic Structural Retrieval

**The wahoo demo.** Proves that eco mode finds architecture that native file browsing misses. Runs on the full memctl codebase (~120 files, 243 chunks, 1.4 MB).

| Act | Title | Key Moment |
|-----|-------|------------|
| 1/4 | The Hard Question | Native: 5 file reads, ~7,700 tokens, misses test invariants |
| 2/4 | eco ON | 3 tool calls, ~4,600 tokens, surfaces M1/M2/M3 from test files |
| 3/4 | FTS Discipline | NL query → 0 results; identifier → 9 results, deep chunk |
| 4/4 | Persistence | Same query, instant recall, zero re-exploration |

```bash
bash demos/eco_demo.sh                  # full 4-act demo
bash demos/eco_demo.sh --act 2          # run only Act 2
bash demos/eco_demo.sh --corpus PATH    # use custom corpus
bash demos/eco_demo.sh --keep           # preserve workspace
```

### Loop-Specific Demo: `run_loop_demo.sh` — 3-Act LLM Loop Demo

Dedicated demo for the `memctl loop` command with three LLM backends.

| Act | Backend | Requirements |
|-----|---------|-------------|
| 1 | Mock LLM | None (deterministic bash script) |
| 2 | Ollama (granite3.1:2b) | `ollama pull granite3.1:2b` |
| 3 | Claude | `claude` CLI in PATH |

```bash
bash demos/run_loop_demo.sh                  # mock only (default)
bash demos/run_loop_demo.sh --ollama         # mock + Ollama
bash demos/run_loop_demo.sh --claude         # mock + Claude
bash demos/run_loop_demo.sh --all            # all three backends
```

---

## Prerequisites

- **Python 3.10+** with memctl installed (`pip install -e .`)
- No other dependencies for Tier 1 and Tier 2 demos

Optional (for `run_demo.sh` Act 9 and `run_loop_demo.sh` Acts 2-3):
- **Ollama** with a pulled model (`ollama pull granite3.1:2b`)
- **Claude CLI** (`npm install -g @anthropic-ai/claude-code`)

---

## File Layout

```
demos/
├── _demo_lib.sh            Shared helpers (colors, logging, workspace, capability detection)
├── must_have_demo.sh        Tier 1: launch demo (5 properties, ~30s)
├── advanced_demo.sh         Tier 2: loop + consolidation (6 steps, ~60s)
├── chat_demo.sh             Chat REPL demo (mock LLM, 3 steps)
├── eco_demo.sh              eco mode demo (4 acts, full codebase)
├── run_demo.sh              Full 10-act showcase
├── run_loop_demo.sh         Loop-specific demo (3 LLM backends)
├── mock_llm.sh              Deterministic mock LLM (3-iteration state machine)
├── mock_chat_llm.sh         Deterministic mock LLM for chat REPL
├── README.md                This file
├── corpus-mini/             Minimal corpus for Tier 1 (3 files, ~600 bytes)
│   ├── architecture.md      Microservices, event sourcing
│   ├── security.md          JWT, RBAC, encryption
│   └── database.md          PostgreSQL, migrations, indexing
└── corpus/                  Full corpus for Tier 2 and showcase (6 files)
    ├── architecture.md      Microservices, event sourcing, gRPC, Kubernetes
    ├── security.md          JWT, RBAC, OWASP Top 10, encryption, audit
    ├── database.md          PostgreSQL, migrations, indexing, partitioning
    ├── conception.md        (FR) Authentification, chiffrement, RGPD
    ├── api_gateway.md       Token validation, rate limiting, error handling
    └── session_management.md  Token lifecycle, concurrent sessions
```

---

## How memctl Works (For Non-Specialists)

### The Problem

Large Language Models (ChatGPT, Claude, Copilot) are powerful but stateless — they forget everything between conversations. Every framework that adds "memory" to an LLM does it with cloud services, vector databases, or fragile abstractions that lock you into a vendor.

### The memctl Approach

memctl takes a radically simpler path:

- **One file** — All memory lives in a single SQLite file. No server to run. Copy it, back it up, git-track it. It's just a file.
- **Unix pipes** — `memctl push` writes injection blocks to stdout. `memctl pull` reads from stdin. Any LLM CLI, any shell script, any cron job can compose with it.
- **Policy engine** — 35 detection patterns block secrets and prompt injection *before* they reach storage. Your memory can never be weaponized against your own LLM.
- **Content addressing** — Every file and every memory item is SHA-256 hashed. Ingesting the same file twice is a no-op. Safe in automated pipelines.
- **Audit trail** — Every write, every read, every merge creates an immutable event. You can prove what your LLM knew, when, and why.
- **Bounded loops** — The LLM can ask for more context, but memctl controls the loop: maximum iterations, cycle detection, convergence stopping. The LLM proposes. memctl decides.

### The Pipeline

```
  documents ─→ memctl push ─→ injection block (stdout) ─→ LLM ─→ memctl pull ─→ memory.db
                    │                                               │
                    └── SHA-256 dedup                               └── policy check
                    └── paragraph chunking                          └── audit event
                    └── FTS5 indexing                                └── content hash
```

### Key Differentiators

| Feature | memctl | Typical "LLM Memory" |
|---------|--------|---------------------|
| Storage | Single SQLite file | Cloud service / vector DB |
| API | Unix pipes (stdin/stdout) | REST API / SDK |
| Dependencies | Zero (stdlib only) | numpy, FAISS, torch, ... |
| Security | 35 detection patterns, hard-reject secrets | Trust the user |
| Audit | Every operation traced | Varies |
| Idempotency | SHA-256 content addressing | Varies |
| LLM calls | Zero (deterministic) | Often required |
| Vendor lock-in | None (upgrade to RAGIX anytime) | High |

---

## After a Demo

If you used `--keep`, explore the workspace:

```bash
# Set the database path
export MEMCTL_DB=/tmp/memctl_musthave.XXXXXX/.memory/memory.db

# Inspect
memctl stats
memctl stats --json
memctl search "authentication"
memctl search "database" --json

# Browse audit trail (Python sqlite3, no CLI needed)
python3 -c "
import sqlite3
conn = sqlite3.connect('$MEMCTL_DB')
for row in conn.execute('SELECT timestamp, action, item_id FROM memory_events ORDER BY timestamp DESC LIMIT 10'):
    print(f'{row[0]}  {row[1]:8s}  {row[2] or \"—\"}')
"
```

---

**Author:** Olivier Vitrac, PhD, HDR | [olivier.vitrac@adservio.fr](mailto:olivier.vitrac@adservio.fr) | Adservio Innovation Lab

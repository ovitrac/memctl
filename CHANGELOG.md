# Changelog

All notable changes to memctl are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.8.0] — 2026-02-20

### Added

**Layer 0 — Path & Resource Guardrails (`mcp/guard.py`)**
- `ServerGuard` class with strict path canonicalization
- `--db-root` flag: constrains DB paths to a directory tree
  - MCP serve mode: defaults to `~/.local/share/memctl/db` (secure-by-default)
  - CLI mode: unset (backwards-compatible)
- `--secure` flag: sets `--db-root=CWD` when not explicitly provided
- Pre-check rejects `..` segments before path resolution
- Symlink resolution and containment enforcement
- Per-call write size cap (`--max-write-bytes`, default 64 KB)
- Per-minute write budget (512 KB/min aggregate)
- Import batch cap (500 items)
- Root-relative path normalization for audit logs

**Layer 1 — MCP Middleware**

*Rate Limiter (`mcp/rate_limiter.py`):*
- Token-bucket rate limiter, per-session, no threading (async single-threaded)
- Write tools: 20/min (memory_write, memory_propose, memory_import, memory_consolidate, memory_sync)
- Read tools: 120/min (memory_recall, memory_search, memory_read, memory_export, memory_inspect, memory_ask, memory_loop)
- Exempt tools: memory_stats, memory_mount (health-check / metadata-only)
- Burst factor ×2.0, configurable via `--writes-per-minute`, `--reads-per-minute`
- Per-turn proposal cap (5/turn)
- `--no-rate-limit` to disable entirely

*Session Tracker (`mcp/session.py`):*
- Minimal in-memory session state (no persistence)
- Session ID from MCP context, fallback to `"default"` singleton
- Turn count and per-turn write tracking

*Audit Logger (`mcp/audit.py`):*
- Structured JSONL audit trail (schema v1, stable contract)
- Top-level fields: `v`, `ts`, `rid`, `tool`, `sid`, `db`, `outcome`, `d`, `ms`
- `rid` (UUID4): correlates multi-tool sequences within one MCP request
- Privacy rules: never logs raw content — only 120-char preview, SHA-256 hash, byte count
- `d.policy`: records policy decision + rule ID for write tools
- Fire-and-forget: audit failures never disrupt tool execution
- Default: JSONL to stderr; `--audit-log PATH` for file output

**Layer 3 — Optional Claude Code Integration Bundle**
- `extras/claude-code/hooks/memctl_safety_guard.sh` — PreToolUse hook
  - Blocks 13 dangerous shell commands + 4 git-destructive patterns
- `extras/claude-code/hooks/memctl_audit_logger.sh` — PostToolUse hook
  - Logs all tool actions to `.agent_logs/memctl_commands.log`
- `scripts/install_claude_hooks.sh` — idempotent hooks installer
- `scripts/uninstall_mcp.sh` — clean removal (MCP config + hooks)
  - `--hooks-only` / `--mcp-only` for selective removal
  - Timestamped `.bak` backups before any config edit
  - Never deletes `.memory/` user data

**Middleware Wiring**
- Locked middleware execution order: guard → session → rate limit → execute → audit
- All 14 MCP tools emit exactly one audit record per call (including failures)
- `register_memory_tools()` accepts optional guard, rate_limiter, session_tracker, audit kwargs

### Changed

- `memctl serve` gains new flags: `--db-root`, `--secure`, `--no-rate-limit`,
  `--writes-per-minute`, `--reads-per-minute`, `--burst-factor`,
  `--max-proposals-per-turn`, `--max-write-bytes`, `--audit-log`
- `mcp/server.py` wires all middleware into `create_server()`
- `mcp/tools.py` rewritten with middleware integration (guard + rate limiter + audit)
- CLI `cmd_serve()` passes through v0.8 flags to MCP server

### Tests

- 749 passed, 7 skipped, 0 failures (+97 new)
- `test_guard.py`: 30 tests — path traversal, symlinks, size caps, import batch, no-root mode
- `test_rate_limiter.py`: 33 tests — token bucket, burst, isolation, refill, batch import, proposals
- `test_session.py`: 11 tests — fallback, turn tracking, write tracking, reset
- `test_audit.py`: 16 tests — schema v1, privacy, fire-and-forget, JSONL format
- `test_mcp_middleware.py`: 8 tests — audit emission, middleware order, policy bypass impossible

### Design Decisions

- Rate limits apply only in MCP mode (CLI remains unthrottled)
- `memory_stats` exempt from rate limiting (health-check must always respond)
- `memory_import` counted as N writes (one per item) + byte budget
- Hooks are optional — not required for memctl core functionality
- Audit schema v1 is a stable contract: fields may be added, never removed
- Session tracking is in-memory only (no persistence, resets on server restart)
Versioning follows [SemVer](https://semver.org/spec/v2.0.0.html) from v1.0.

---

## [0.7.0] - 2026-02-20

MCP feature parity and security hardening.

### Added

- **MCP feature parity** (7 new tools, 7→14 total):
  - `memory_mount` — register, list, or remove folder mounts
  - `memory_sync` — sync mounted folders (delta or full)
  - `memory_inspect` — structural injection block from corpus metadata
  - `memory_ask` — one-shot folder Q&A (mount + sync + inspect + loop)
  - `memory_export` — JSONL export with filters (capped at 1000 items)
  - `memory_import` — JSONL import with policy enforcement and dedup
  - `memory_loop` — bounded recall-answer loop with convergence detection
- **PII detection** (`policy.py`): 5 quarantine-level patterns — US SSN, credit card (Visa/MC/Amex/Discover), email, phone (US+international), IBAN. PII is quarantined (`injectable=False`), not rejected, preserving data for admin access while preventing LLM injection.
- **Config validation** (`config.py`): `validate()` method on all config dataclasses. Range checks for 11 numeric fields. `ValidationError(ValueError)` exception. `load_config(strict=True)` raises on invalid values.
- **MCP installer** (`scripts/install_mcp.sh`): One-command setup for Claude Code and Claude Desktop. Checks prerequisites (Python 3.10+, pip), installs `memctl[mcp]`, configures the client's MCP config (deterministic insert/update with timestamped `.bak` backup), initializes workspace, and verifies server. Options: `--client claude-code|claude-desktop|all`, `--python PATH`, `--db PATH`, `--dry-run`, `--yes`.
- **`memctl serve --check`**: Verify MCP server can start (create + tool registration) without running the server loop. Used by the installer for verification.
- **Test suite** expanded to ~652 tests across 22 files (+~108 tests: 38 MCP tools, 21 PII patterns, 20 config validation, 16 exit codes, 13 other).

### Fixed

- **`evaluate_item()` soft-block gap**: `memory_write` MCP tool now applies instructional-quarantine and PII patterns (previously only hard blocks were checked). Items matching soft-block patterns are stored with `injectable=False`.
- **`cmd_import` exit code**: Returns exit 1 when all lines fail (errors > 0, imported == 0).

### Changed

- **MCP instructions** updated: tool count (7→14), folder/data/loop categories, PII quarantine rule.
- **PolicyConfig**: New field `pii_patterns_enabled: bool = True`.
- **Breaking (security)**: `evaluate_item()` now quarantines content matching instructional-quarantine and PII patterns. Previously-passing content may now be stored with `injectable=False`. This is intentional security hardening.

### Design Decisions

- **PII quarantine, not reject**: Email/phone may be intentional (contact directories, provenance). Quarantine prevents LLM injection while preserving data.
- **No `memory_chat` MCP tool**: Chat is an interactive REPL (readline, TTY, session state) — incompatible with MCP request-response. `memory_recall` + `memory_loop` provides equivalent programmatic access.
- **`memory_import` accepts JSON array string**: MCP params are JSON. JSONL-as-string would require double-serialization. Internally converted.
- **`memory_export` capped at 1000 items**: MCP responses have practical size limits. Truncation indicated.

---

## [0.6.0] - 2026-02-20

Operability and polish: export/import, chat UX hardening, config file support.

### Added

- **JSONL export/import** (`export_import.py`): Backup, migrate, and share memory databases via JSONL. `memctl export` writes one JSON object per line to stdout (filters: `--tier`, `--type`, `--scope`, `--include-archived`). `memctl import [FILE]` reads JSONL from file or stdin with content-hash dedup and policy enforcement. Options: `--preserve-ids`, `--dry-run`.
- **Persistent readline history**: Chat REPL saves command history to `~/.local/share/memctl/chat_history` (XDG_DATA_HOME compliant). Loaded on startup, saved on exit. Max 1000 entries (configurable via `config.json`). Disabled for non-TTY input.
- **Multi-line chat input**: Blank line terminates multi-line input in interactive mode. Continuation prompt (`  `) on stderr. Piped mode unchanged (one line per question). Banner announces convention.
- **JSON config file** (`config.py`): `load_config()` reads `config.json` from beside the database. Silent fallback to compiled defaults if missing or invalid. Sections: `store`, `inspect`, `chat`. Precedence: CLI > env > config > default.
- **Configurable observation thresholds**: `inspect.py` reads thresholds from `config.json` `inspect` section. Custom `dominance_frac`, `low_density_threshold`, `ext_concentration_frac`, `sparse_threshold`. Hardcoded defaults unchanged.
- **Test suite** expanded to 544 tests across 18 files (+35 tests: 16 export/import, 8 config, 7 chat, 5 CLI).

### Changed

- **`memctl init`** now creates `config.json` (not `config.yaml`). If legacy `config.yaml` exists, a migration hint is printed to stderr.
- **CLI `--config PATH`** flag available on all subcommands for explicit config override.
- **CLI: 16 commands** (was 14). New: `export`, `import`.

### Design Decisions

- **JSON over YAML**: Zero dependencies. `json.load()` is stdlib. No `pyyaml` or hand-rolled parser. Python 3.10+ compatible (no `tomllib` which requires 3.11+).
- **New IDs by default on import**: Avoids collision across databases. `--preserve-ids` for controlled migrations.
- **Items-only export**: `corpus_hashes`, `mounts`, `events`, `links` are machine-local. Items are the portable unit.
- **Policy never bypassed**: Every imported item passes through `policy.evaluate_item()`.

---

## [0.5.0] - 2026-02-20

One-shot folder Q&A and scoped recall.

### Added

- **Folder Q&A** (`ask.py`): Answer a question about a folder in one command with `memctl ask <path> "question" --llm CMD`. Orchestrates auto-mount, auto-sync, structural inspect, scoped recall, and bounded loop. Answer to stdout, progress to stderr. Options: `--inspect-cap` (tokens for structural context, default 600), `--sync auto|always|never`, `--mount-mode persist|ephemeral`, `--protocol`, `--max-calls`, `--budget`.
- **Scoped recall**: `recall_items()` accepts `mount_id` parameter. When set, FTS results are post-filtered to items belonging to that mount (via `corpus_hashes` item_ids). Unscoped recall is unchanged.
- **Chat `--folder`**: `memctl chat --folder <path>` scopes every turn's recall to the folder. Auto-mounts and syncs on startup. Same flags as `ask` for sync control (`--sync`, `--no-sync`).
- **Budget splitting**: `--inspect-cap` knob controls how much of `--budget` is reserved for structural context (inspect block). Remainder goes to recall. Deterministic, no rollover.
- **Test suite** expanded to 509 tests across 16 files (+17 tests: 15 ask unit, 2 CLI ask).

### Design Decisions

- **One-shot only**: `ask` is strictly one-shot (no REPL). For interactive folder-scoped sessions, use `memctl chat --folder`.
- **Post-filter scoping**: Scoped recall uses post-filter on FTS results (not SQL JOIN) because FTS5 MATCH doesn't compose well with JOINs. O(N) where N = FTS limit.
- **Ephemeral ordering**: When `--mount-mode ephemeral`, mount is kept during recall and loop, then removed after the answer is computed.
- **CLI: 14 commands** (was 13). New: `ask`.

---

## [0.4.0] - 2026-02-20

Interactive memory-backed chat REPL.

### Added

- **Chat REPL** (`chat.py`): Interactive memory-backed chat via `memctl chat --llm CMD`. Each turn: FTS5 recall → LLM → display. Stateless by default. Options: `--session` (in-memory sliding window), `--store` (persist answers as STM), `--session-budget` (bound session context), `--history-turns`, `--protocol`, `--max-calls`, `--source` (pre-ingest).
- **Passive protocol default**: Chat defaults to `passive` (single-pass, no refinement). Opt into iterative refinement with `--protocol json --max-calls 3`.
- **Uncertainty hint**: When passive mode detects hedging markers in the answer, a one-line tip on stderr suggests enabling iterative refinement. No behavior change — just discoverability.
- **Session dual bounds**: `--history-turns` (turn count) and `--session-budget` (character budget) together prevent runaway context growth. Oldest turns trimmed first.
- **Injectable architecture**: `chat_turn()` accepts `recaller` and `loop_runner` callables for zero-monkeypatch unit testing.
- **Test suite** expanded to 492 tests across 15 files (+18 tests: 16 chat unit, 2 CLI chat).

### Design Decisions

- **stdout purity**: Answers go to stdout only. Prompt, banner, hints, and errors go to stderr. Chat output is pipeable.
- **No per-turn ingestion**: `--source` is pre-ingest only. Per-turn auto-ingestion deferred — hidden state changes, variable latency, token blow-up.
- **In-memory session**: `--session` is ephemeral (not persisted). Durable state requires explicit `--store`. Persistence is opt-in because durable state must be auditable, tagged, and policy-governed.
- **CLI: 13 commands** (was 12). New: `chat`.

---

## [0.3.0] - 2026-02-20

Folder mount, structural sync, and inspection — three new commands.

### Added

- **Folder mount** (`mount.py`): Register folders as structured sources with `memctl mount <path>`. Stores metadata only — no scanning, no ingestion. Options: `--name`, `--ignore`, `--lang`. List with `--list`, remove with `--remove`.
- **Delta sync** (`sync.py`): Scan mounted folders with `memctl sync [<path>]`. 3-tier delta rule: (1) not in DB → ingest, (2) size+mtime match → fast skip without hashing, (3) hash → compare → ingest if different. Auto-registers mount if path given without prior `memctl mount`. `--full` forces re-processing. `--json` for machine output.
- **Structural inspect** (`inspect.py`): Generate deterministic structural injection blocks with `memctl inspect [<path>]`. Positional path auto-mounts and auto-syncs (`inspect_path()` orchestration). Flags: `--sync auto|always|never`, `--no-sync`, `--mount-mode persist|ephemeral`, `--ignore`. Tier 0 staleness check (inventory comparison via path/size/mtime triples) skips sync when store is fresh. All paths in output are mount-relative (never absolute). Reports: file/chunk/size totals, per-folder breakdown, per-extension distribution, top-5 largest files, rule-based observations. Token-bounded via `--budget`. `--json` includes `observation_thresholds` and orchestration metadata.
- **Schema v2**: `SCHEMA_VERSION` bumped from 1 to 2. `corpus_hashes` extended with 6 columns (`mount_id`, `rel_path`, `ext`, `size_bytes`, `mtime_epoch`, `lang_hint`). New `memory_mounts` table. Migration is additive (ALTER TABLE ADD COLUMN) and idempotent.
- **Observation rules**: Four hardcoded constants (frozen in v0.3) for deterministic structural observations — `DOMINANCE_FRAC=0.40`, `LOW_DENSITY_THRESHOLD=0.10`, `EXT_CONCENTRATION_FRAC=0.75`, `SPARSE_THRESHOLD=1`. Exported as `OBSERVATION_THRESHOLDS` dict and included in `--json` output.
- **Size accounting** (`ingest.py`): `ingest_file()` now writes `size_bytes` and `ext` to `corpus_hashes`, ensuring inspect never shows "0 B" or "unknown" for ingestable files. Inspect falls back to `os.stat()` for legacy entries.
- **Test suite** expanded to 474 tests across 14 files (+142 tests: 15 mount, 25 sync, 49 inspect, 27 store mount/migration, 18 CLI mount/sync/inspect, 8 forward compat).

### Changed

- **Schema compatibility stance**: memctl remains forward-compatible with RAGIX (RAGIX can open memctl DBs). Schema identity is not guaranteed after v0.3. New table and columns are ignored by RAGIX.
- **Timestamp rule**: Filesystem `mtime` stored as `INTEGER` epoch seconds (`int(os.stat().st_mtime)`). Logical events continue using `TEXT` ISO-8601 UTC.
- **CLI**: 12 commands (was 9). New: `mount`, `sync`, `inspect`.

---

## [0.2.1] - 2026-02-19

### Fixed

- **README**: RAGIX install instructions now use `git clone` from `ovitrac/RAGIX` (not on PyPI).

---

## [0.2.0] - 2026-02-19

Bounded recall-answer loop, text similarity, and demo infrastructure.

### Added

- **Stdlib text similarity** (`similarity.py`): Jaccard + SequenceMatcher combined similarity for fixed-point detection and query cycle detection. Public API: `normalize`, `tokenize`, `jaccard`, `sequence_ratio`, `similarity`, `is_fixed_point`, `is_query_cycle`. Zero-dependency, deterministic.
- **Bounded recall-answer loop** (`loop.py`): Iterative recall-answer controller with five stopping conditions (`llm_stop`, `fixed_point`, `query_cycle`, `no_new_items`, `max_calls`). JSON/regex/passive protocols. Subprocess LLM invocation (stdin or file mode). JSONL trace emission and replay. Context merge with dedup and budget trimming.
- **CLI `loop` subcommand** (`cli.py`): `memctl loop "query" --llm CMD` with 15 flags (`--protocol`, `--max-calls`, `--threshold`, `--trace-file`, `--replay`, etc.). Unix-composable: reads context from stdin, writes final answer to stdout.
- **Demo infrastructure** (`demos/`):
  - `_demo_lib.sh` — shared helpers (TTY-aware colors, capability detection, workspace management)
  - `must_have_demo.sh` — Tier 1 launch demo: 5 core properties in ~30s, no LLM needed
  - `advanced_demo.sh` — Tier 2 demo: loop + convergence + trace + consolidation, feature-gated
  - `run_loop_demo.sh` — 3-act loop demo (mock LLM, Ollama, Claude)
  - `mock_llm.sh` — deterministic mock LLM (3-iteration state machine)
  - `corpus-mini/` — minimal 3-file corpus for Tier 1 demo
  - `corpus/api_gateway.md`, `corpus/session_management.md` — additional corpus files
- **Test suite** expanded to 332 tests across 11 files (+122 tests: 59 similarity, 55 loop, 8 CLI loop).

---

## [0.1.0] - 2026-02-18

Initial release. Extracted from RAGIX v0.62.0 memory subsystem.

### Added

- **Core data model** (`types.py`): MemoryItem, MemoryProposal, MemoryEvent, MemoryLink, MemoryProvenance, CorpusMetadata. Content-addressed via SHA-256.
- **SQLite store** (`store.py`): 9 tables + FTS5 virtual table, WAL mode, SCHEMA_VERSION=1. Schema-identical to RAGIX for forward compatibility.
- **Policy engine** (`policy.py`): 30 detection patterns (10 secret, 8 injection, 8 instructional block, 4 instructional quarantine). Hard blocks (reject) and soft blocks (quarantine with `injectable=False`).
- **Multi-format extraction** (`extract.py`): Unified `read_file_text()` entry point for ~47 file extensions. Text files read directly (stdlib). Binary formats dispatched to optional extractors: `.docx` (python-docx), `.odt`/`.odp`/`.ods` (odfpy), `.pptx` (python-pptx), `.xlsx` (openpyxl), `.pdf` (pdftotext/poppler). Lazy imports with clear `ImportError` messages.
- **Ingestion** (`ingest.py`): Paragraph chunking, SHA-256 file dedup, source resolution (files, directories, globs), tag/title inference from file paths and markdown headings. Uses `extract.py` for all file reading.
- **Deterministic consolidation** (`consolidate.py`): Jaccard clustering by type + tags, longest-content-wins merge, STM->MTM->LTM promotion. No LLM dependency.
- **Proposal parser** (`proposer.py`): Delimiter and regex-based extraction of structured memory proposals from LLM output.
- **Configuration** (`config.py`): Dataclass configuration for store, policy, consolidation, and proposer.
- **CLI** (`cli.py`): 8 commands — `init`, `push`, `pull`, `search`, `show`, `stats`, `consolidate`, `serve`. Unix-composable with stdout purity.
- **MCP server** (`mcp/`): 7 tools (`memory_recall`, `memory_search`, `memory_propose`, `memory_write`, `memory_read`, `memory_stats`, `memory_consolidate`). Compatible with Claude Code, Claude Desktop, and any MCP client.
- **Injection format** (`mcp/formatting.py`): Stable `format_version=1` contract for token-budgeted injection blocks.
- **Optional dependencies** (`pyproject.toml`): `[docs]` extra for Office/ODF libraries (python-docx, python-pptx, openpyxl, odfpy). `[all]` meta-extra combining docs, mcp, and dev.
- **Test suite**: 210 tests across 9 files (types, store, policy, ingest, extract, forward compatibility, contracts, CLI subprocess, pipe composition).
- **Demos**: `run_demo.sh` — 10-act showcase with bundled corpus and optional LLM reasoning.
- **Documentation**: README with quickstart, CLI reference, shell integration, MCP setup, architecture, and migration guide.

### Design Decisions

- **Zero runtime dependencies**: Core uses only Python stdlib. Office format extractors are optional (`memctl[docs]`). MCP server requires optional `mcp[cli]`.
- **Single SQLite file**: All memory in one `memory.db` with WAL mode for concurrent reads.
- **FTS5 over embeddings**: Full-text search covers 90% of recall use cases. Embeddings reserved for RAGIX upgrade.
- **Policy-first**: Every write path passes through the policy engine. No shortcuts.
- **Forward compatibility**: Schema, injection format, and MCP tool names identical to RAGIX. `pip install ragix[all]` + point at the same DB.

---

**Author:** Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio Innovation Lab

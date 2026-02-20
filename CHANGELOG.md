# Changelog

All notable changes to memctl are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [SemVer](https://semver.org/spec/v2.0.0.html) from v1.0.

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

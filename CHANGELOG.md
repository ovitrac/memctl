# Changelog

All notable changes to memctl are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [SemVer](https://semver.org/spec/v2.0.0.html) from v1.0.

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

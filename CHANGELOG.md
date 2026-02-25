# Changelog

All notable changes to memctl are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.19.1] — 2026-02-25

### Fixed (Cold-start discipline — field report feedback)

- **eco-hint.sh 4-way branch.** The `UserPromptSubmit` hook now has a
  cold-start branch: when eco is ON and item count < 10, injects explicit
  guidance to run `memctl push --source` or `/scan` before exploring.
  Previously, a nearly-empty DB produced the same hint as a populated one,
  giving no indication that ingestion was the first step. The populated
  branch (>= 10 items) now includes Level 0 in its escalation ladder.
  Files: `memctl/templates/eco/eco-hint.sh`, `extras/eco/eco-hint.sh`.

- **eco-nudge.sh cold-start nudge.** The `PreToolUse` hook now emits a
  strong nudge toward `memctl push` when the DB is empty or nearly empty
  (< 10 items), instead of silently exiting. When no DB exists at all,
  emits the same ingestion guidance. The >= 200 item threshold for search
  tool nudges is unchanged.
  File: `memctl/templates/hooks/eco-nudge.sh`.

- **Level 0 in escalation ladder.** ECO.md now starts the ladder at
  Level 0 (Ingest) with explicit prohibition against `find`/`ls`/`Grep`/
  `Glob` on un-indexed codebases. Level 0 applies only on cold start
  (new codebase or directory not yet indexed).
  Files: `memctl/templates/eco/ECO.md`, `extras/eco/ECO.md`.

- **Post-ingest search hint.** `memctl push` now emits a stderr hint
  after successful ingestion: `Next: memctl search <keywords> or /recall
  <keywords>`. Closes the cold-start loop — users know what to do after
  ingestion completes.
  File: `memctl/cli.py`.

## [0.19.0] — 2026-02-25

### Added

- **`memory_recall_best_effort` MCP tool (21st tool).** Multi-step best-effort
  retrieval with full cascade transparency. Adds an outer retry loop on top of
  `store.search_fulltext`, with normalization reporting (`query_used`), cascade
  trace (`steps[]`), query reformulation on zero results (identifier extraction,
  broadest-term fallback), and two suggested queries in zero-result hints.
  Tool description embeds query discipline, answer contract, and escalation
  guidance at MCP schema level (attention level 2).
  - New parameters: `max_steps` (1-5, default 3), `mode` ("auto"/"strict").
  - New response fields: `query_used`, `strategy_used`, `steps[]`, `hint`.
  - Classified as read tool in rate limiter.
  - Helper functions: `_extract_best_retry_query`, `_suggest_next_queries`.
  Files: `memctl/mcp/tools.py`, `memctl/mcp/rate_limiter.py`.

## [0.18.3] — 2026-02-25

### Fixed

- **eco-nudge.sh Bash(find) blind spot.** The PreToolUse hook now intercepts
  `Bash` tool calls containing `find` or recursive `ls` commands — not just
  Grep and Glob. On large indexed codebases, Claude Code reaches for
  `find . -name '*.java'` via Bash, which sailed through the nudge undetected.
  The hook now matches `find .`, `find /`, `ls -R`, and piped `| find` patterns.
  Still never blocks (exit 0), still one-line stderr nudge, still silent on
  small stores. Diagnosed via AdservioToolbox e2e playground validation on a
  1200+ file Java codebase.
  File: `memctl/templates/hooks/eco-nudge.sh`.

## [0.18.2] — 2026-02-25

### Changed (Eco Compliance — behavioral review)

- **eco-hint.sh rewrite (F-M1).** The `UserPromptSubmit` hook now injects
  situational, scale-aware context instead of generic advisory text.
  Changes: item count from DB ("1247 indexed items"), embedded 4-step
  escalation ladder (inspect → recall → Grep → Read), "when eco wins"
  bullets (cross-file, binary formats, persistence), clear bypass rules
  (editing, single known file, git), Retrieved/Analysis answer contract.
  No ECO.md dependency on the hot path.
  Files: `memctl/templates/eco/eco-hint.sh`, `extras/eco/eco-hint.sh`.

- **`/recall` rewrite (F-M6).** The slash command is now a coached
  interaction, not a shortcut. Normalizes the query (strip stop words,
  extract identifiers, keep 2-3 terms), executes `memory_recall`, and
  presents results with a mandatory output contract:
  `query_in` / `query_used` / `strategy` / `hits` / `hint`.
  On zero results, proposes exactly two next queries + mentions `/scan`
  and `/reindex en`. Includes a query discipline recall-rate table.
  File: `memctl/templates/eco/commands/recall.md`.

- **ECO.md bypass rules narrowed (F-M4).** The bypass decision tree now
  says "bypass for editing" instead of "bypass when you know the path."
  Exploration of known files should still use `memory_recall` scoped to
  the file's directory — knowing a path after a Grep hit does not justify
  bypassing eco for understanding the module in context.
  Files: `extras/eco/ECO.md`, `memctl/templates/eco/ECO.md`.

### Added

- **eco-nudge.sh PreToolUse hook (F-M2).** New template that fires before
  Grep and Glob tool calls. Never blocks (exit 0 always). Injects a
  one-line stderr reminder when ALL conditions are met: eco ON, DB exists
  with >=200 indexed items, search looks like exploration (Grep pattern
  >=6 chars or multi-term; Glob with `**` or wide wildcards). Silent on
  small projects, narrow file lookups, and short symbol searches.
  File: `memctl/templates/hooks/eco-nudge.sh`.

- **install_eco.sh step 3b.** Installer now deploys `eco-nudge.sh` into
  `.claude/hooks/` and registers the `PreToolUse` hook in
  `settings.local.json`. Idempotent, backup-aware, dry-run compatible.
  File: `memctl/scripts/install_eco.sh`.

- **uninstall_eco.sh eco-nudge cleanup.** Uninstaller removes
  `eco-nudge.sh` file and its `PreToolUse` hook registration.
  File: `memctl/scripts/uninstall_eco.sh`.

### Documentation

- **`base/REVIEW_eco_compliance.md`** — diagnostic review covering root
  cause analysis (6 memctl-side, 3 Toolbox-side, 3 platform constraints),
  implemented fixes with concrete scripts, "where eco wins" framing,
  authority ceiling analysis, validation protocol, and definition of done.

## [0.18.1] — 2026-02-24

### Fixed
- **`memctl pull` content-hash dedup** — pulling identical content twice no
  longer creates duplicate items. `store.exists_by_content_hash()` checks for
  a non-archived item with the same SHA-256 hash before every `write_item()`
  call. Applies to all three write sites in `cmd_pull()` (structured proposals,
  chunked notes, single notes). Duplicates are silently skipped (exit 0),
  consistent with the idempotent ingestion invariant.
- **MCP write-path dedup** — same content-hash guard applied to `memory_propose`
  (per-item `action: "duplicate"`) and `memory_write` (`status: "duplicate"`),
  ensuring consistency across CLI and MCP entry points.

### Tests
- `test_pull_dedup`: pull identical content twice, verify exactly one item via
  `stats --json`.
- Total: 1126 passed, 5 skipped.

## [0.18.0] — 2026-02-24

### Added
- **`memctl doctor` command** — environment health check. Runs 10 diagnostic
  checks (Python version, sqlite3 module, FTS5 support, DB existence, WAL mode,
  schema version, integrity check, policy patterns count, MCP importability,
  eco config validity). Human-readable output by default; `--json` for machine
  consumption. Exit 0 if all pass/warn, exit 1 if any fail. Modeled after
  `brew doctor` / `toolboxctl doctor`.

### Tests
- `TestDoctor`: 6 new tests (pass, json, no_db, check_names, schema_version,
  exit_code).
- Total: 1125 passed, 5 skipped.

## [0.17.1] — 2026-02-23

### Fixed
- **`pypdf` moved back to `[docs]` extra** — restores zero core dependencies.
  `pip install memctl` has no runtime deps; `pip install memctl[docs]` adds PDF
  + Office support. The `_extract_pdf()` fallback chain (pypdf → pdftotext) is
  unchanged.

## [0.17.0] — 2026-02-23

### Added (Workflow Quality — P4 + P5 + P6)
- **P4: Write-back reinforcement in eco.** "Persist Findings" section added
  to `ECO.md` escalation ladder. `/eco on` hint and `/remember` type guidance
  nudge Claude to store analytical findings (not just raw file content).
  `type="decision"` and `type="definition"` auto-promote to LTM.
- **P5: CamelCase tokenization.** `_expand_camel_case()` in `ingest.py`
  splits PascalCase and camelCase identifiers (e.g. `IncidentMetierService`
  → `incident metier service`) and appends them as `[camel: ...]` metadata
  lines during ingestion. FTS5 can now match partial segments. Existing
  items require `memctl reindex` after re-ingestion.
- **P6: `memctl promote` command.** Manual tier promotion: `memctl promote
  MEM-abc123 --tier ltm`. Supports `--json` output. MCP: `memory_promote(id,
  tier)` (#20). Exits 1 if already at target tier or item not found.
- **MCP tools:** 20 (added `memory_promote`).

### Tests
- P4-T1–T3: Eco template content validation.
- P5-T1–T4 + 2 integration: CamelCase expansion + ingestion pipeline.
- P6-T1–T5: Promote CLI (STM→LTM, STM→MTM, already-at-tier, nonexistent, JSON).
- MCP tool count updated: 19 → 20.
- Total: 1119 passed, 5 skipped.

## [0.16.4] — 2026-02-23

### Added (Structural Integrity — P2 + P3)
- **P2: Content-similarity safety floor.** `_content_similar()` uses
  `difflib.SequenceMatcher` on the first 1000 chars of each item pair.
  Items with content similarity below `min_content_similarity` (default 0.15)
  never cluster, regardless of tag overlap. Catches gross mismatches
  (e.g. `Incident.java` vs `weblogic-application.xml`) while allowing
  legitimate clustering of related items. Threshold 0.0 disables the gate.
- **P3: Path-bonus effective similarity.** `_effective_similarity()` adds a
  path-proximity bonus to tag Jaccard: +0.15 for same-file items (need only
  Jaccard >= 0.55 to reach the 0.7 threshold), +0.05 for same-directory
  (need Jaccard >= 0.65). Different-directory items get no bonus.
- **Three-gate clustering.** `_coarse_cluster()` now requires ALL three
  conditions: (1) effective similarity >= 0.7, (2) source affinity (same
  parent directory), (3) content similarity >= 0.15. This is the full
  clustering model from the Structural Integrity workplan.
- **`ConsolidateConfig.min_content_similarity`**: New config field (float,
  default 0.15, range [0.0, 1.0]). Validated in `config.py`.

### Tests
- `TestContentSimilarity`: 5 tests (dissimilar blocked, similar passes,
  threshold=0 disables, performance <2s for 500 pairs, integration).
- `TestEffectiveSimilarity`: 6 tests (same-file bonus, different-file no
  bonus, same-dir small bonus, cap at 1.0, no-provenance, integration).
- Total: 1105 passed, 5 skipped.

## [0.16.3] — 2026-02-23

### Fixed
- **`reset()` now reclaims disk space.** After `DELETE FROM` on all content
  tables, `VACUUM` + `PRAGMA wal_checkpoint(TRUNCATE)` rewrites and compacts
  the database file. Previously the file retained its peak size forever
  (freelist pages but no reclamation). 96% size reduction on typical resets.
- **Thread-safe `_distinct_scopes()`.** Multi-scope consolidation now acquires
  the store lock before querying distinct scopes, preventing potential
  concurrent-access issues.
- **`memctl status` null mount name.** Mounts with `name=None` no longer crash
  the status display (`NoneType.__format__` error).

### Tests
- Total: 1094 passed, 5 skipped.

## [0.16.2] — 2026-02-23

### Fixed (Structural Integrity — first production feedback)
- **P0: Auto-scope from mount path.** `sync.py` no longer hardcodes
  `scope="project"` for all synced files. Scope is now derived from the
  mount name or path basename via `derive_scope()`. Explicit `--scope`
  flag overrides. Legacy `scope=project` items remain untouched.
  New `--scope` flag on `memctl sync`.
- **P1: Source-path affinity gate in clustering.** `_coarse_cluster()` now
  checks `_source_affinity()` — items from different parent directories
  never cluster, regardless of tag overlap. Prevents knowledge collapse
  from generic tags (e.g. `java`, `domaine`).
- **P1: Multi-scope consolidation.** `memctl consolidate --all-scopes`
  consolidates each scope independently. `memory_consolidate(all_scopes=True)`
  MCP equivalent.

### Tests
- P0-T1–T5: `derive_scope()` unit tests + `sync_mount` auto-scope integration.
- P1-T1–T6: `_source_affinity()` unit tests.
- 6 clustering + multi-scope integration tests.
- Total: 1093 passed, 7 skipped.

## [0.16.1] — 2026-02-23

### Added
- **`pypdf` as core dependency**: PDF text extraction now works out of the box
  without system packages. Pure Python via `pypdf>=4.0.0`.
- **PDF fallback chain**: `_extract_pdf()` tries `pypdf` first, falls back to
  `pdftotext` (poppler-utils) if pypdf returns empty or fails.

### Changed
- `dependencies` in `pyproject.toml`: `[]` → `["pypdf>=4.0.0"]`.
- `pypdf` removed from `[docs]` optional extra (now core).
- README installation section updated: PDF no longer requires poppler-utils.

## [0.16.0] — 2026-02-23

### Added
- **`memctl eco` CLI command**: toggle eco mode from the command line (`memctl eco on`,
  `memctl eco off`, `memctl eco status`). Supports `--json` flag. Backward-compat
  migration from `.claude/eco/.disabled` → `.memory/.eco-disabled`.
- **`memory_eco` MCP tool (#19)**: toggle or query eco mode state via MCP.
  Claude Code's natural reflex is MCP tool calls — this avoids the Bash permission
  prompt entirely. Returns structured JSON (`eco_mode`, `action_taken`).
  Exempt from rate limiting. Full audit trail.

### Changed
- MCP tool count: 18 → 19 (`memory_eco` added).
- `/eco` slash command updated: MCP primary (`memory_eco`) + CLI fallback (`memctl eco`).

### Tests
- E1–E4: MCP tool tests for `memory_eco` (status/on/off/on-with-config).
- E5–E8: CLI subprocess tests for `memctl eco` (status/off/on/on-not-installed).

## [0.15.2] — 2026-02-23

### Added
- **`memctl --version` flag**: prints `memctl X.Y.Z` and exits.
- **`memctl serve --transport`**: support for `streamable-http` and `sse` transports
  in addition to the default `stdio`. Enables remote/multi-machine MCP access.
  New flags: `--transport` (`stdio`/`streamable-http`/`sse`), `--host`, `--port`.
  Warns on `--host 0.0.0.0` (LAN exposure).

## [0.15.1] — 2026-02-22

### Fixed
- **F1: Eco toggle no longer triggers Claude Code sensitive-file prompt.**
  Flag relocated from `.claude/eco/.disabled` → `.memory/.eco-disabled`.
  Backward-compatible: old flag auto-migrated on first `memctl status` or
  `memory_status` call. Three `.claude/`-path permission patterns removed
  from install/uninstall scripts (no longer needed).
- **F2: Clarified `push` vs `pull` CLI help text.**
  `push` now reads "Ingest files (--source) + recall query → injection block on stdout".
  `pull` now reads "Store text from stdin as memory items (pipe from LLM or echo)".
- **F3: Improved CLI discoverability for `show` and `inspect`.**
  `show` help now includes example (`memctl show MEM-abc123`) and `MEM-ID` metavar.
  `inspect` help now clarifies it takes a folder path, not a memory ID.

### Tests
- F1-T1: `.memory/.eco-disabled` flag → status reports "disabled".
- F1-T2: Old `.claude/eco/.disabled` auto-migrated to new location.

## [0.15.0] — 2026-02-22

### Added
- **Unified `parse()` dispatcher** in `MemoryProposer`: single entry point for all
  parse strategies. Priority: tool calls → JSON stdin → delimiter → fallback.
  Replaces hand-wired 3-tier chain in `cmd_pull()` with one-liner.
- **`memctl diff` CLI command**: compare two items or an item against a past revision.
  Supports `--json`, `--revision N`, and `--latest` flags. Uses `difflib.unified_diff`
  (stdlib) for content and field-by-field metadata comparison.
- **`memory_diff` MCP tool (#18)**: read-only diff via MCP. Full middleware wiring
  (guard → rate limit → execute → audit). Returns content_diff, metadata_changes,
  similarity_score, identical flag.
- **`/diff` slash command**: eco template with MCP primary + CLI fallback pattern.
  Read-only, warns against phantom `memctl compare`.
- **`memctl/diff.py` module** (~130 LOC): `compute_diff()` and `resolve_diff_targets()`
  using `difflib.unified_diff` + `memctl.similarity`.

### Changed
- `cmd_pull()` now uses `proposer.parse()` instead of manual 3-tier chain.
- MCP tool count: 17 → 18 (`memory_diff` added).
- Slash command count: 8 → 9 (`/diff` added).
- Install/uninstall loops: 8 → 9 commands.

### Tests
- `tests/test_proposer.py` — 8 new tests (UP1-UP8): unified parse() priority ordering.
- `tests/test_diff.py` — 14 new tests (D1-D14): compute_diff, resolve_diff_targets.
- `tests/test_cli.py` — 5 new tests (D15-D19): diff CLI human/JSON/missing/identical/latest.
- `tests/test_mcp_tools.py` — 3 new tests (D20-D24): 18 tools, memory_diff OK/error/identical.
- `tests/test_eco_templates.py` — 4 new tests (D25-D28): diff template existence, MCP ref,
  CLI fallback, read-only marker. ALL_COMMANDS updated to 9.

## [0.14.0] — 2026-02-22

### Added
- **`memctl pull` JSON parsing (P1)**: 3-tier parse chain — raw JSON arrays on
  stdin are now parsed as structured proposals (type, tags, title preserved).
  Fixes the last determinism gap: LLM proposes structured facts, the kernel
  enforces schema instead of flattening to opaque notes. Supports both
  `[{"content":...}]` and `{"items":[...]}` formats via `parse_json_stdin()`.
- **`store.last_event(actions)`**: public API for querying the most recent event
  by action type. Used by `cmd_status` and `memory_status` — no private `_conn`
  access needed.
- **`memctl status` CLI command**: project memory health dashboard showing eco
  state, store stats, mounts, and last scan timestamp. Supports `--json`.
  Handles missing DB gracefully (suggests `/scan`).
- **`memory_status` MCP tool (#17)**: read-only project health dashboard via MCP.
  Exempt from rate limiting (like `memory_stats`). Returns eco state, store stats,
  mounts, and last scan.
- **Slash commands** (3 new, 8 total):
  - `/consolidate [--dry-run]` — Merge similar memory items (`memory_consolidate`)
  - `/status` — Project memory health dashboard (`memory_status`)
  - `/export [--tier T]` — Export memory as JSONL (`memory_export`)
- **Tokenizer eco config sync (P2)**: `memctl reindex` now updates
  `.claude/eco/config.json` with the new tokenizer, eliminating the mismatch
  warning on subsequent commands.

### Fixed
- **`memctl pull` JSON flattening**: raw JSON arrays on stdin (e.g., from
  `/remember` CLI fallback) were stored as opaque notes instead of structured
  proposals. Now correctly maps `type`, `tags`, `title`, and `content` fields.

### Tests
- `tests/test_proposer.py` — 12 tests (PJ1-PJ6 + edge cases): JSON stdin parsing
  (happy path, items wrapper, empty, no content, plain text, mixed, whitespace).
- `tests/test_store.py` — 4 tests (LE1-LE4): `last_event()` with action filter,
  no events, unfiltered, filter miss.
- `tests/test_cli.py` — 4 tests: pull JSON stdin (structured, tags, multiple items),
  reindex eco config sync.
- `tests/test_eco_templates.py` — 10 new tests (T18-T27): consolidate/status/export
  template existence, MCP references, CLI fallbacks, safety patterns.
- `tests/test_mcp_tools.py` — updated: 16→17 tools, `memory_status` in expected set.
- **Total: 1029 passed, 6 skipped.**

### Architecture
- MCP tool count: 16 → 17 (`memory_status` added; `memory_export` already existed).
- Slash command count: 5 → 8 (`/consolidate`, `/status`, `/export`).
- Slash command governance rule unchanged: commands restricted to bootstrap +
  high-frequency. All operations remain available via CLI and MCP tools.

## [0.13.2] — 2026-02-22

### Added
- **`memory_reset` MCP tool (#16)**: truncate all memory content in a single
  audited transaction. Preserves schema + mount config. Classified as WRITE_TOOL
  with full middleware (guard → rate limit → execute → audit).
- **`memctl reset` CLI command**: `--dry-run`, `--confirm`, `--clear-mounts`.
- **Eco bootstrap slash commands** (5 new):
  - `/scan [path]` — Index a folder and create memory (`memory_inspect`)
  - `/recall <query>` — Search memory (`memory_recall`)
  - `/remember <text>` — Store an observation (`memory_propose`)
  - `/reindex [preset]` — Rebuild FTS index (`memory_reindex`)
  - `/forget all` — Reset memory (`memory_reset`)
- **Eco config file** (`.claude/eco/config.json`): installer persists `db_path`
  so eco-hint.sh reads the correct path (no hardcoded default).
- **Bootstrap detection** in eco-hint.sh: 3-way branch (disabled / no-DB / normal).
- **"First Use" section** in ECO.md with command table + governance rule.
- **Slash command governance rule**: commands restricted to bootstrap + high-frequency.
- **Auto-approve memctl CLI** (`install_eco.sh`): adds `Bash(memctl *)` glob
  permission to `settings.local.json`, eliminating per-command approval prompts.
  Also adds eco toggle patterns. Uninstaller cleans up on removal.

### Fixed
- **Bootstrap DB creation**: `MemoryStore` auto-creates parent directories
  when opening a disk-backed database. `memctl sync --db .memory/memory.db .`
  now works without prior `mkdir -p .memory` or `memctl init`.
- **All 5 slash command templates**: MCP-first with correct CLI fallback syntax.
  Each template warns against phantom CLI names (`memctl recall` → use
  `memctl search`, `memctl remember` → use `memctl pull`, `memctl forget` →
  use `memctl reset`).
- **`serve --check` install-time safety**: `--check` now validates configuration
  (imports, db path, version) without opening the database. Previously failed
  at install time when `.memory/memory.db` did not yet exist.
- eco-hint normal state is concise (~30 tokens, was ~50).
- eco.md `/eco on` and `/eco status` now suggest `/scan` when no DB exists.

### Tests
- `tests/test_memory_reset.py` — 16 tests (R1-R16): store.reset() unit tests,
  MCP-level dry_run/execution/audit, CLI subprocess (--dry-run, --confirm, safety gate).
- `tests/test_eco_templates.py` — 17 tests (T1-T17): template existence, placeholder
  validation, MCP tool references, eco-hint config-driven path, installer/uninstaller
  coverage, CLI fallback correctness (T13-T17).
- **Total: 999 passed, 6 skipped.**

### Architecture
- Slash commands are optional UX helpers. All functionality remains available
  via CLI and MCP tools. memctl is a retrieval engine, not a Claude-first product.

## [0.12.4] — 2026-02-22

### Fixed

- **Hooks schema format**: all 4 hook scripts wrote bare entries
  (`{"type": "command", ...}`) instead of the required Claude Code wrapper format
  (`{"hooks": [{"type": "command", ...}]}`). Fixed in `install_eco.sh`
  (UserPromptSubmit), `install_claude_hooks.sh` (PreToolUse, PostToolUse),
  `uninstall_eco.sh`, and `uninstall_mcp.sh`. Uninstallers now use
  `json.dumps(e)` for matching so they clean up both old and new entry formats.

## [0.12.3] — 2026-02-22

### Fixed

- **v0.12.2 PyPI re-publish**: v0.12.2 was published to PyPI without templates.
  This release is the corrected version with all bundled files.

## [0.12.2] — 2026-02-22

### Fixed

- **Scripts bundled in PyPI wheel**: `scripts/` moved inside `memctl/scripts/` so that
  `pip install memctl` includes installer/uninstaller shell scripts. Previously, scripts
  were only available from a git clone.
- **Templates bundled in PyPI wheel**: `extras/eco/` and `extras/claude-code/hooks/`
  templates copied into `memctl/templates/` so install scripts work after `pip install`.
  Scripts now resolve templates from `SCRIPT_DIR/../templates/` instead of `REPO_ROOT/extras/`.

### Added

- **`memctl scripts-path` command**: prints the path to bundled installer scripts,
  enabling `bash "$(memctl scripts-path)/install_eco.sh"` after a PyPI install.
- **`memctl/templates/`**: bundled eco templates (`eco-hint.sh`, `ECO.md`, `eco.md`)
  and hook templates (`memctl_safety_guard.sh`, `memctl_audit_logger.sh`).
- **`[tool.setuptools.package-data]`** in `pyproject.toml`: ensures `*.sh` and `*.md`
  template files are included in the wheel.

## [0.12.1] — 2026-02-22

### Added

- **Morphological miss hint**: when the FTS cascade falls to a weak strategy
  (OR_FALLBACK, PREFIX_AND, or LIKE) on a non-Porter tokenizer with multi-term
  queries, `SearchMeta.morphological_hint` suggests `memctl reindex --tokenizer en`.
  Surfaced in MCP responses (`memory_recall`, `memory_search`) and CLI (`memctl search`,
  stderr). Advisory only — no behavioral change.

### Tests

- 7 new tests (PX16-PX22): morphological hint conditions, Porter suppression,
  serialization. Total: 966 passed, 6 skipped.

## [0.12.0] — 2026-02-22

### Added

- **`memctl reindex` command**: rebuild FTS5 index with optional tokenizer change.
  Logged, auditable, with `--dry-run` and `--json` support. Every reindex emits
  a `memory_event` with tokenizer, item count, and duration.
- **Tokenizer metadata**: `schema_meta` records `fts_tokenizer`, `fts_indexed_at`,
  `fts_reindex_count`. Visible in `memctl stats` and `memory_stats` MCP tool.
  Mismatch detection warns when stored tokenizer differs from configured.
- **Prefix expansion**: `PREFIX_AND` cascade step — `"monitor"*` matches
  `monitoring`, `monitored`. Only for terms ≥5 chars. Skipped when Porter
  stemming is active (redundant). Position: after REDUCED_AND, before OR_FALLBACK.
- **`memory_reindex` MCP tool** (tool #15): rebuild FTS5 index via MCP, with
  `dry_run` support. Classified as WRITE_TOOL (rate-limited).
- **`_is_porter_tokenizer()` helper**: detects Porter stemming in active tokenizer.

### Changed

- `rebuild_fts()` now updates `schema_meta` with tokenizer, indexed_at, and
  increments `fts_reindex_count`.
- `stats()` reports `fts_tokenizer_stored`, `fts_indexed_at`, `fts_reindex_count`,
  and `fts_tokenizer_mismatch` fields.
- Cascade order: AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK → LIKE.
- MCP tool count: 14 → 15.

### Tests

- `tests/test_reindex.py` — 25 tests (X1-X25): rebuild, tokenizer change, mismatch,
  metadata persistence, dry run, edge cases, event logging.
- `tests/test_prefix_search.py` — 15 tests (PX1-PX15): basic prefix, min-length guard,
  Porter skip, cascade integration, strategy metadata.
- **Total: 959 passed, 6 skipped.**

## [0.11.0] — 2026-02-22

### Behavioral change

`search_fulltext()` now uses a deterministic cascade strategy
(AND → REDUCED_AND → OR_FALLBACK) instead of strict AND. Queries that
previously returned 0 results may now return results with reduced precision.
The `fts_strategy` field in MCP responses and `store._last_search_meta`
indicate which strategy was used. This is an intentional semantic shift
from strict AND to cascade-with-transparency.

### Added

- **FTS cascade** (`memctl/query.py`): `cascade_query()` implements deterministic
  fallback — AND(all terms) → REDUCED_AND(N-1) → ... → AND(1) → OR(all).
  Term-drop heuristic: shortest first. Logged at every transition.
- **Token-coverage ranking** (`memctl/store.py`): `_rank_by_coverage()` ranks OR
  results by number of query terms matched. Stable sort preserves FTS5 BM25 order
  for equal coverage.
- **Search metadata** (`memctl/types.py`): `SearchStrategy` type alias and `SearchMeta`
  dataclass. Strategy, original/effective/dropped terms, candidate count.
- **MCP strategy reporting**: `memory_recall` and `memory_search` return `fts_strategy`,
  `fts_original_terms`, `fts_effective_terms`, `fts_dropped_terms` in responses.
- **Injection block hint** (`memctl/mcp/formatting.py`): strategy hint line in injection
  blocks when cascade used non-AND strategy.
- **ECO.md v2**: FTS Cascade Behavior section, Stemming Limitations section,
  Scale-Aware Patterns section. Updated recovery sequence and escalation ladder
  to reflect cascade. Universal doctrine — no profile split.

### Benchmark (enterprise Java codebases)

Validated on two enterprise Java codebases (3,419 items / 451 MB and 1,283 items / 13 MB):

| Metric | Large v0.10 | Large v0.11 | Medium v0.10 | Medium v0.11 |
|--------|------------|------------|-------------|-------------|
| Identifiers | 100% | 100% | 100%* | 100%* |
| Multi-term | 40% | **100%** | 0% | **60%** |
| NL queries | 0% | **100%** | 0% | **90%** |

No regression on single-term queries. No latency regression (all ops <1.3s).

### Tests

- `tests/test_fts_cascade.py` — 40 tests (C1-C40): AND baseline, REDUCED_AND,
  OR_FALLBACK, strategy metadata, edge cases, LIKE fallback, integration, logging,
  backward compatibility.
- `tests/test_token_ranking.py` — 20 tests (R1-R20): coverage ranking, edge cases,
  case insensitivity, integration, stability properties.

## [0.10.0] — 2026-02-21

### Added

**Injection integrity & query resilience (Phase 0)**

- `memctl/query.py` — new module: FTS stop-word stripping (`normalize_query`), intent
  classification (`classify_mode`), and budget suggestion (`suggest_budget`). French +
  English stop words, question words, identifier preservation (CamelCase, snake_case,
  UPPER_CASE, dotted paths). Zero-dependency, stdlib-only.
- `format_combined_prompt()` in `memctl/mcp/formatting.py` — structural separation of
  user question and injected context. User question always first with explicit "answer
  THIS" marker. Injection block marked as "reference only". Optional mode hints
  (exploration/modification).
- Query-length hints in `memory_recall` and `memory_search` MCP tools: when queries
  exceed 4 words, suggests the normalized form.
- Zero-result guidance: when recall/search returns 0 hits, returns actionable hints
  (use identifiers, remove articles, try inspect first).
- Stop-word normalization integrated into `store.search_fulltext()` — benefits both
  CLI and MCP paths transparently.

**ECO.md hardening (Phase 2)**

- Complete ECO.md rewrite (359 lines): all 8 required sections present.
- Mode Classification Protocol: Exploration-first vs Modification-first with explicit
  verb-based classification rules. References `classify_mode()` as automated.
- Injection Budget Guidelines: budget proportional to question length (600-1500 tokens).
  References `suggest_budget()` as automated.
- Escalation Ladder: formalized with automatic stop-word normalization at L2.
  Recovery sequence documented with concrete examples.
- FTS5 Query Discipline: unicode61 no-stemming limitations documented
  (inflection, singular/plural, AND logic). Good and bad query examples with
  explanations of why bad queries fail and how to fix them.
- Bypass Decision Tree: updated to reflect automatic stop-word stripping.
- Critical Rules: never edit from chunks, never let injection override user question.
- "Why eco is OFF by default" section.
- Programmatic API section: `normalize_query()`, `classify_mode()`, `suggest_budget()`
  with usage examples.
- Generic query examples only (no customer-specific content).

**Escalation ladder tests (Phase 1)**

- `tests/test_escalation_ladder.py` — 32 tests: L1 inspect (structural overview), L2
  recall with NL normalization (7 English + 1 French NL query, identifiers, keyword pairs),
  query hints and zero-result guidance, scope/tier filtering, non-degradation guards,
  FTS5 limitation documentation (inflection, cross-item, singular/plural), 20-query
  recovery rate matrix (85% prediction accuracy, 100% identifier/keyword, 60%+ NL).

**Tests (~109 new)**

- `tests/test_query_normalization.py` — 42 tests: stop-word stripping, identifier
  detection, mode classification, budget suggestion.
- `tests/test_injection_integrity.py` — 20 tests: combined prompt structure, budget
  enforcement, marker preservation, unicode support.
- `tests/test_mode_classification.py` — 15 tests: 10-scenario matrix (5 exploration +
  5 modification) plus edge cases (mixed intent, French, bare identifiers).
- `tests/test_escalation_ladder.py` — 32 tests: full escalation ladder validation
  (L1 inspect, L2 recall/search, guidance, filtering, recovery rates).

**Pilot guidance (Phase 3)**

- `extras/eco/PILOT.md` — generic pilot framework for evaluating eco mode with
  development teams. Recommended pilot size (20-30 developers, 2-4 weeks),
  30-minute training outline (4 modules), metrics to collect (5 KPIs with targets),
  exit criteria (5 conditions), rollout/failure guidance.

**Documentation & branding (Phase 4)**

- `QUICKSTART.md` — general quickstart guide: install, first memory, ingest a codebase,
  ask questions, MCP server setup, eco mode overview, environment variables, FAQ (8 entries),
  troubleshooting (10-row table), next steps.
- `ECO_QUICKSTART.md` — eco mode guide for Claude Code users: first session walkthrough,
  intent classification, escalation ladder, query tips, 5 session workflow patterns,
  binary format superpowers, CloakMCP coexistence, FAQ (7 entries), troubleshooting.
- `README.md` restyled: centered logo + title + tagline header, 7 shield.io badges
  (License, Python, Version, Tests, MCP, DeepWiki, Code style), navigation menu,
  Documentation section (7-document table), Links section, footer with quote.
- `assets/memctl-logo.png` — project logo (owl mascot with eco/FTS5/STM references).

### Changed

- `store.search_fulltext()` now applies `normalize_query()` before FTS — French and
  English stop words stripped automatically. Existing keyword queries unchanged.
- `extras/eco/ECO.md` hardened rewrite (Phase 2): FTS5 no-stemming limitations,
  automatic normalization at L2, recovery examples, programmatic API section.
- `README.md` updated: eco section reflects OFF-by-default, query normalization,
  pilot guidance link. Architecture list includes `query.py`. Test count: 859.

### Design decisions

- Stop-word stripping, not embeddings: covers 80% of NL failure cases without dependencies.
- Budget proportional to question length: prevents intent distortion at the source.
- Mode classification via verbs: deterministic, no ML needed, Claude follows explicit rules.
- eco default OFF: first impressions determine adoption; untrained users must opt in.
- eco is a behavioral layer, not a mandatory runtime.
- No customer-specific content in public documentation.

## [0.9.0] — 2026-02-21

### Added

**eco mode — Token-efficient file exploration for Claude Code**

- `extras/eco/ECO.md` — behavioral strategy file: guiding principle, escalation ladder,
  FTS5 query discipline, tool usage rules, fallback boundaries. Ladder first, tools
  subordinate. Includes explicit FTS5 AND-logic rules: prefer 2-3 keywords, prefer
  identifiers (function/class/constant names), avoid natural language sentences.
- `extras/eco/eco-hint.sh` — `UserPromptSubmit` hook template (~50 tokens per turn).
  Conditional on `.claude/eco/.disabled` flag file (single syscall, O(1)).
  Default: ON (no flag file = enabled). Toggleable at runtime via `/eco` command.
- `extras/eco/eco.md` — `/eco` slash command template (on|off|status toggle).
- `extras/eco/README.md` — eco mode documentation, CloakMCP parallel, install/uninstall.

**One-shot installer (`scripts/install_eco.sh`)**

- Single command sets up everything: MCP server, eco hook, strategy file, .gitignore.
- Verifies `memctl[mcp]` is installed (fails with clear hint if not).
- Registers MCP server with `--db-root .memory` (project-scoped).
- Installs eco hook to `.claude/hooks/eco-hint.sh` (merges with existing hooks).
- Installs strategy file to `.claude/eco/ECO.md`.
- Installs `/eco` slash command to `.claude/commands/eco.md`.
- Validates server startup (`memctl serve --check`).
- Adds `.memory/` to `.gitignore` (idempotent).
- Reports extraction capabilities (informational, non-blocking):
  docx, pdf, xlsx, pptx, odt, ods, odp with OK/MISSING status.
- Options: `--db-root PATH`, `--dry-run`, `--yes`, `--force`.

**Clean removal (`scripts/uninstall_eco.sh`)**

- Removes eco hook, strategy file, and `/eco` slash command.
- Removes `UserPromptSubmit` hook registration from settings.
- Never deletes `.memory/memory.db` (user data), MCP server config, or safety hooks.
- Options: `--dry-run`.

**WAHOO demo (`demos/eco_demo.sh`)**

- 4-act scripted demo on the full memctl codebase (~120 files, 243 chunks, 1.4 MB).
- Act 1: native exploration — 5 file reads, ~7,700 tokens, misses test invariants.
- Act 2: eco mode — 3 tool calls, ~4,600 tokens, surfaces M1/M2/M3 from test files.
- Act 3: FTS discipline — natural-language query → 0 results; identifier → 9 results.
- Act 4: persistence — same query, instant recall, zero re-exploration.
- Self-excluding: `eco_demo.sh` is ignored during ingestion to avoid self-referential hits.
- Options: `--act N`, `--corpus PATH`, `--keep`.

### Design Decisions

- **eco is advisory, not restrictive**: eco mode steers Claude toward token-efficient
  retrieval. It does not block native Read/View for editing or line-level operations.
- **Hook is static**: No dynamic stats, no subprocess calls, no latency. ~50 tokens,
  deterministic, impossible to break.
- **Escalation ladder is foundational**: ECO.md puts the decision hierarchy (inspect →
  recall → loop → fallback) before individual tool descriptions, ensuring Claude
  internalizes the constraint topology before learning tool specifics.
- **Project-scoped memory**: Default `--db-root .memory` keeps knowledge collocated with
  the codebase, gitignored, per-project isolation.
- **No new MCP tools**: eco mode leverages the existing 14 tools. No core code changes.
- **Product identity**: eco is deterministic structural retrieval + persistent cross-file
  reasoning — not "smarter Claude". Strongest wins are surgical chunk retrieval (deep
  implementation, not file headers), cross-file invariant discovery (architecture in tests),
  and bounded cost (~5x token reduction, credible and measurable).
- **FTS5 discipline is the interface**: keyword queries succeed, natural language fails.
  This is by design — eco rewards precision.

---

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

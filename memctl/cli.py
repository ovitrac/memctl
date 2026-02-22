"""
memctl CLI — Unix-Composable Memory Commands

Commands:
    memctl init   [PATH]                     — scaffold store + .gitignore
    memctl push   "query" [--source ...]     — ingest + recall → stdout
    memctl pull   [--tags T] [--title T]     — stdin → proposals → store
    memctl search "query" [-k N] [--tier T]  — FTS5 search → stdout
    memctl show   <id>                       — display single memory item
    memctl stats                             — store metrics
    memctl status                            — project memory health dashboard
    memctl consolidate [--dry-run]           — merge + promote STM items
    memctl loop   "query" --llm CMD          — bounded recall-answer loop
    memctl mount  <path> [--name N]          — register folder for sync
    memctl sync   [<path>] [--full]          — scan + ingest mounted folders
    memctl inspect [--mount M] [--budget N]  — structural injection block → stdout
    memctl ask    <path> "Q" --llm CMD       — one-shot folder Q&A
    memctl chat   --llm CMD [--session]      — interactive memory-backed chat
    memctl export [--tier T] [--type T]      — JSONL export → stdout
    memctl import [FILE] [--preserve-ids]    — JSONL import from file or stdin
    memctl reindex [--tokenizer PRESET]      — rebuild FTS5 index
    memctl serve  [--fts-tokenizer FR]       — start MCP server (foreground)

Environment variables:
    MEMCTL_DB       Path to SQLite database (default: .memory/memory.db)
    MEMCTL_BUDGET   Token budget for injection blocks (default: 2200)
    MEMCTL_FTS      FTS5 tokenizer preset: fr|en|raw (default: fr)
    MEMCTL_TIER     Default write tier (default: stm)
    MEMCTL_SESSION  Optional session ID for audit provenance

Precedence (invariant):
    CLI --flag  >  MEMCTL_* env var  >  compiled default

Exit codes:
    0  Success (including idempotent no-op)
    1  Operational error (bad args, empty input, policy rejection)
    2  Internal failure (unexpected exception, I/O error)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defensive env parsing (never crash on bad export)
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """Parse integer env var with fallback. Never raises on bad input."""
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    """Parse string env var with fallback."""
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _resolve_db(args: Optional[argparse.Namespace] = None) -> str:
    """Resolve database path: CLI --db > MEMCTL_DB > .memory/memory.db."""
    if args and getattr(args, "db", None):
        return args.db
    return _env_str("MEMCTL_DB", ".memory/memory.db")


def _resolve_budget(args: Optional[argparse.Namespace] = None) -> int:
    """Resolve token budget: CLI --budget > MEMCTL_BUDGET > 2200."""
    if args and getattr(args, "budget", None) is not None:
        return args.budget
    return _env_int("MEMCTL_BUDGET", 2200)


def _resolve_fts(value: str = "") -> str:
    """Resolve FTS tokenizer: preset name → tokenizer string.

    Accepts: 'fr', 'en', 'raw' (preset names)
    Or: raw tokenizer string (passed through if not a known preset).
    """
    from memctl.store import FTS_TOKENIZER_PRESETS
    v = value or _env_str("MEMCTL_FTS", "fr")
    return FTS_TOKENIZER_PRESETS.get(v, v)


# ---------------------------------------------------------------------------
# Store factory (direct store + policy, no dispatcher indirection)
# ---------------------------------------------------------------------------


def _open_store(db_path: str, fts_tokenizer: Optional[str] = None):
    """Open a MemoryStore. Creates the DB and parent dirs if needed."""
    from memctl.store import MemoryStore
    fts = fts_tokenizer or _resolve_fts()
    return MemoryStore(db_path=db_path, fts_tokenizer=fts)


def _open_policy():
    """Create a MemoryPolicy with default config."""
    from memctl.policy import MemoryPolicy
    return MemoryPolicy()


# ---------------------------------------------------------------------------
# Stderr helpers (respect --quiet)
# ---------------------------------------------------------------------------

_quiet = False


def _info(msg: str) -> None:
    """Print progress to stderr (suppressed by --quiet)."""
    if not _quiet:
        print(msg, file=sys.stderr)


def _warn(msg: str) -> None:
    """Print warning to stderr (always visible)."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Default config template for init
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_JSON = {
    "store": {"fts_tokenizer": "fr"},
    "inspect": {
        "dominance_frac": 0.40,
        "low_density_threshold": 0.10,
        "ext_concentration_frac": 0.75,
        "sparse_threshold": 1,
    },
    "chat": {"history_max": 1000},
}


# ===========================================================================
# Command: init
# ===========================================================================


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new memory workspace directory."""
    target = Path(args.path).resolve()
    db_path = target / "memory.db"

    if db_path.exists() and not args.force:
        # Idempotent: print paths, exit 0 (not error)
        _info(f"Workspace exists: {target}")
        _info(f"  Database:  {db_path}")
        # The export line goes to stdout (useful for eval)
        print(f'export MEMCTL_DB="{db_path}"')
        return

    if args.force and db_path.exists():
        db_path.unlink()
        # Also remove WAL/SHM if present
        for suffix in ("-wal", "-shm"):
            p = db_path.parent / (db_path.name + suffix)
            if p.exists():
                p.unlink()

    target.mkdir(parents=True, exist_ok=True)

    # Create database with schema + FTS5
    fts = _resolve_fts(getattr(args, "fts_tokenizer", "") or "")
    store = _open_store(str(db_path), fts_tokenizer=fts)
    store.close()

    # Write default config (if absent)
    config_path = target / "config.json"
    if not config_path.exists():
        cfg = dict(_DEFAULT_CONFIG_JSON)
        cfg["store"]["fts_tokenizer"] = fts
        config_path.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # Backward compat: warn if old config.yaml exists
    old_yaml = target / "config.yaml"
    if old_yaml.exists():
        _info(
            f"[init] Found legacy config.yaml — consider migrating to config.json"
        )

    # Write .gitignore (if absent)
    gitignore_path = target / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
            "*.db\n*.db-wal\n*.db-shm\n", encoding="utf-8"
        )

    _info(f"Memory workspace initialized: {target}")
    _info(f"  Database:  {db_path}")
    _info(f"  Config:    {config_path}")
    _info(f"  .gitignore: {gitignore_path}")
    print(f'export MEMCTL_DB="{db_path}"')


# ===========================================================================
# Command: push  (ingest + recall → stdout)
# ===========================================================================


def cmd_push(args: argparse.Namespace) -> None:
    """One-shot: ingest sources (if provided) + recall + print injection block."""
    from memctl.ingest import ingest_file, IngestResult, resolve_sources
    from memctl.mcp.formatting import format_injection_block

    db_path = _resolve_db(args)
    store = _open_store(db_path)
    budget = _resolve_budget(args)

    # --- Phase 1: Ingest (optional) ---
    if args.source:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        try:
            resolved = resolve_sources(args.source)
        except FileNotFoundError as e:
            _warn(f"Error: {e}")
            store.close()
            sys.exit(1)

        total = IngestResult()
        for path in resolved:
            r = ingest_file(
                store, path,
                scope=args.scope,
                max_tokens=args.chunk_tokens,
                tags=tags,
                format_mode="auto",
                injectable=True,  # push mode: injectable by design
            )
            total.files_processed += r.files_processed
            total.files_skipped += r.files_skipped
            total.chunks_created += r.chunks_created
            total.item_ids.extend(r.item_ids)

        _info(
            f"[push] Ingested {total.chunks_created} chunks from "
            f"{total.files_processed} file(s) "
            f"({total.files_skipped} skipped)"
        )

    # --- Phase 2: Recall via FTS5 ---
    items = store.search_fulltext(
        args.query, tier=args.tier, scope=None, limit=50,
    )

    if not items:
        _info("[push] No matching items.")
        store.close()
        sys.exit(0)

    # Filter non-injectable
    items = [it for it in items if it.injectable]

    if not items:
        _info("[push] No injectable items matched.")
        store.close()
        sys.exit(0)

    # --- Phase 3: Format + print (stdout purity: only data here) ---
    enriched = []
    for it in items:
        enriched.append({
            "id": it.id,
            "tier": it.tier,
            "validation": it.validation,
            "type": it.type,
            "title": it.title,
            "content": it.content,
            "provenance": it.provenance.to_dict(),
            "tags": it.tags,
            "confidence": it.confidence,
            "entities": it.entities,
        })

    text = format_injection_block(enriched, budget, len(enriched))

    _info(f"[push] {len(enriched)} items, ~{len(text) // 4} tokens")
    print(text)  # stdout: data only

    store.close()


# ===========================================================================
# Command: pull  (stdin → proposals → store)
# ===========================================================================


def cmd_pull(args: argparse.Namespace) -> None:
    """Capture LLM output from stdin and store as memory items."""
    text = sys.stdin.read()
    if not text.strip():
        _warn("[pull] No input received on stdin.")
        sys.exit(1)

    db_path = _resolve_db(args)
    store = _open_store(db_path)
    policy = _open_policy()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    scope = args.scope
    tier = _env_str("MEMCTL_TIER", "stm")

    # Provenance
    session_id = _env_str("MEMCTL_SESSION", "")
    source_id = f"memctl-pull:{session_id}" if session_id else "memctl-pull"

    # Attempt structured proposal extraction (3-tier: JSON → delimiters → fallback)
    proposals = []
    try:
        from memctl.proposer import MemoryProposer
        proposer = MemoryProposer()
        # Tier 1: raw JSON array (e.g., from /remember CLI fallback)
        _, proposals = proposer.parse_json_stdin(text)
        # Tier 2: delimiter-wrapped proposals
        if not proposals:
            _, proposals = proposer.parse_response_text(text)
    except Exception as e:
        logger.debug("Proposer parse failed: %s", e)

    if proposals:
        # Structured: multiple atomic items via governance pipeline
        from memctl.types import MemoryProvenance
        accepted = 0
        rejected = 0
        for p in proposals:
            if tags:
                p.tags = list(set(p.tags + tags))
            if not p.scope:
                p.scope = scope
            if not p.provenance_hint:
                p.provenance_hint = {}
            p.provenance_hint.setdefault("source_kind", "tool")
            p.provenance_hint.setdefault("source_id", source_id)

            verdict = policy.evaluate_proposal(p)
            if verdict.action == "reject":
                rejected += 1
                continue

            item = p.to_memory_item(tier=tier, scope=scope)
            if verdict.action == "quarantine":
                if verdict.forced_non_injectable:
                    item.injectable = False
                if verdict.forced_validation:
                    item.validation = verdict.forced_validation
                if verdict.forced_expires_at:
                    item.expires_at = verdict.forced_expires_at

            store.write_item(item, reason="pull")
            accepted += 1

        _info(
            f"[pull] Stored {accepted} item(s) from proposals "
            f"({rejected} rejected by policy)"
        )
    else:
        # Fallback: store entire text as note(s)
        _info("[pull] no structured proposals found — storing as single note")
        title = args.title or f"LLM capture {datetime.now():%Y-%m-%d %H:%M}"
        content = text.strip()

        from memctl.types import MemoryItem, MemoryProvenance
        from memctl.ingest import chunk_paragraphs

        provenance = MemoryProvenance(
            source_kind="tool",
            source_id=source_id,
            content_hashes=[],
        )

        if len(content) > 2000:
            chunks = chunk_paragraphs(content, max_tokens=1800)
            for i, (chunk_text, _start, _end) in enumerate(chunks):
                chunk_title = (
                    f"{title} \u00b7 part {i + 1}/{len(chunks)}"
                    if len(chunks) > 1
                    else title
                )
                item = MemoryItem(
                    tier=tier, type="note",
                    title=chunk_title, content=chunk_text,
                    tags=list(tags), provenance=provenance,
                    scope=scope,
                )
                verdict = policy.evaluate_item(item)
                if verdict.action != "reject":
                    if verdict.action == "quarantine" and verdict.forced_non_injectable:
                        item.injectable = False
                    store.write_item(item, reason="pull")

            _info(f"[pull] Stored as {len(chunks)} note(s): {title}")
        else:
            item = MemoryItem(
                tier=tier, type="note",
                title=title, content=content,
                tags=list(tags), provenance=provenance,
                scope=scope,
            )
            verdict = policy.evaluate_item(item)
            if verdict.action == "reject":
                _warn(f"[pull] Rejected by policy: {verdict.reasons}")
                store.close()
                sys.exit(1)
            if verdict.action == "quarantine" and verdict.forced_non_injectable:
                item.injectable = False
            store.write_item(item, reason="pull")
            _info(f"[pull] Stored as note {item.id}: {title}")

    store.close()


# ===========================================================================
# Command: search  (FTS5 search → stdout)
# ===========================================================================


def cmd_search(args: argparse.Namespace) -> None:
    """Search memory items via FTS5."""
    db_path = _resolve_db(args)
    store = _open_store(db_path)

    items = store.search_fulltext(
        args.query,
        tier=args.tier,
        type_filter=args.type,
        scope=None,
        limit=args.k,
    )

    if not items:
        _info("No results found.")
        store.close()
        sys.exit(0)

    if getattr(args, "json", False):
        # Machine-readable JSON output (stdout purity)
        output = []
        for it in items:
            output.append({
                "id": it.id, "tier": it.tier, "type": it.type,
                "title": it.title, "tags": it.tags,
                "confidence": it.confidence,
                "content_preview": it.content[:200],
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # Human-readable output (still stdout — search is a data command)
        print(f"Found {len(items)} item(s):\n")
        for it in items:
            inj = "" if it.injectable else " [QUARANTINED]"
            print(f"  [{it.tier.upper()}] {it.id}  {it.type:12s}  {it.title}{inj}")
            if it.tags:
                print(f"    tags: {', '.join(it.tags)}")
            print()

    # Morphological miss hint (stderr — advisory only)
    meta = store._last_search_meta
    if meta and meta.morphological_hint:
        _info(f"Hint: {meta.morphological_hint}")

    store.close()


# ===========================================================================
# Command: show  (display single item)
# ===========================================================================


def cmd_show(args: argparse.Namespace) -> None:
    """Show a memory item by ID."""
    db_path = _resolve_db(args)
    store = _open_store(db_path)

    item = store.read_item(args.id)
    if item is None:
        _warn(f"Item not found: {args.id}")
        store.close()
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(item.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"ID:         {item.id}")
        print(f"Tier:       {item.tier.upper()}")
        print(f"Type:       {item.type}")
        print(f"Title:      {item.title}")
        print(f"Validation: {item.validation}")
        print(f"Confidence: {item.confidence:.2f}")
        print(f"Injectable: {item.injectable}")
        print(f"Tags:       {', '.join(item.tags) if item.tags else '(none)'}")
        print(f"Scope:      {item.scope}")
        print(f"Created:    {item.created_at}")
        print(f"Updated:    {item.updated_at}")
        print(f"Usage:      {item.usage_count}")
        if item.entities:
            print(f"Entities:   {', '.join(item.entities)}")
        if item.corpus_id:
            print(f"Corpus:     {item.corpus_id}")
        if item.superseded_by:
            print(f"Superseded: {item.superseded_by}")
        print(f"\n--- Content ---\n{item.content}")

    store.close()


# ===========================================================================
# Command: stats
# ===========================================================================


def cmd_stats(args: argparse.Namespace) -> None:
    """Show memory store statistics."""
    db_path = _resolve_db(args)
    store = _open_store(db_path)
    stats = store.stats()

    if getattr(args, "json", False):
        stats["status"] = "ok"
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print("Memory Store Statistics")
        print("=" * 40)
        print(f"  Total items (active): {stats['total_items']}")
        print(f"  FTS5:     {'available' if stats['fts5_available'] else 'unavailable'}")
        if stats.get("fts_tokenizer"):
            print(f"  Tokenizer: {stats['fts_tokenizer']}")
        if stats.get("fts_tokenizer_mismatch"):
            print(f"  Stored tokenizer: {stats['fts_tokenizer_stored']}  (mismatch — run: memctl reindex)")
        if stats.get("fts_indexed_at"):
            print(f"  Indexed at: {stats['fts_indexed_at']}")
        if stats.get("fts_reindex_count", 0) > 0:
            print(f"  Reindex count: {stats['fts_reindex_count']}")
        print(f"  By tier:")
        for tier, count in sorted(stats.get("by_tier", {}).items()):
            print(f"    {tier.upper():4s}: {count}")
        print(f"  By type:")
        for typ, count in sorted(stats.get("by_type", {}).items()):
            print(f"    {typ:12s}: {count}")
        print(f"  Embeddings:   {stats['embeddings_count']}")
        print(f"  Audit events: {stats['events_count']}")

    store.close()


# ===========================================================================
# Command: status  (project memory health dashboard)
# ===========================================================================


def cmd_status(args: argparse.Namespace) -> None:
    """Show project memory health dashboard."""
    db_path = _resolve_db(args)

    # 1. Eco mode state
    eco_config_path = Path(".claude/eco/config.json")
    eco_disabled = Path(".claude/eco/.disabled")
    eco_state = "disabled" if eco_disabled.exists() else (
        "active" if eco_config_path.exists() else "not installed"
    )

    # 2. DB existence check
    db_exists = Path(db_path).exists()

    if not db_exists:
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "ok",
                "eco_mode": eco_state,
                "db_path": str(db_path),
                "db_exists": False,
            }, indent=2))
        else:
            print("memctl status")
            print("=" * 40)
            print(f"  Eco mode:  {eco_state}")
            print(f"  Database:  {db_path} (not created yet)")
            print(f"  Hint:      Run /scan to bootstrap memory")
        return

    # 3. Full stats from store
    store = _open_store(db_path)
    stats = store.stats()
    mounts = store.list_mounts()

    # 4. Last sync age (public API)
    last_scan = store.last_event(actions=["memory_inspect", "sync"])

    store.close()

    if getattr(args, "json", False):
        print(json.dumps({
            "status": "ok",
            "eco_mode": eco_state,
            "db_path": str(db_path),
            "db_exists": True,
            "total_items": stats["total_items"],
            "by_tier": stats["by_tier"],
            "by_type": stats["by_type"],
            "fts5_available": stats["fts5_available"],
            "fts_tokenizer": stats.get("fts_tokenizer"),
            "fts_tokenizer_mismatch": stats.get("fts_tokenizer_mismatch", False),
            "mounts": len(mounts),
            "last_scan": last_scan,
            "events_count": stats["events_count"],
        }, indent=2))
    else:
        print("memctl status")
        print("=" * 40)
        print(f"  Eco mode:  {eco_state}")
        print(f"  Database:  {db_path}")
        print(f"  Items:     {stats['total_items']} active")
        for tier, count in sorted(stats.get("by_tier", {}).items()):
            print(f"    {tier.upper():4s}: {count}")
        print(f"  FTS5:      {'yes' if stats['fts5_available'] else 'no'}"
              f" ({stats.get('fts_tokenizer', 'n/a')})")
        if stats.get("fts_tokenizer_mismatch"):
            _warn("Tokenizer mismatch — run: memctl reindex")
        print(f"  Mounts:    {len(mounts)}")
        for m in mounts:
            print(f"    {m.get('name', '?'):12s} -> {m.get('path', '?')}")
        if last_scan:
            print(f"  Last scan: {last_scan}")
        print(f"  Events:    {stats['events_count']}")


# ===========================================================================
# Command: consolidate
# ===========================================================================


def cmd_consolidate(args: argparse.Namespace) -> None:
    """Run deterministic consolidation pipeline."""
    from memctl.config import MemoryConfig
    from memctl.consolidate import ConsolidationPipeline

    db_path = _resolve_db(args)
    store = _open_store(db_path)
    config = MemoryConfig()
    pipeline = ConsolidationPipeline(store, config.consolidate)

    try:
        result = pipeline.run(scope=args.scope, dry_run=args.dry_run)
    except Exception as e:
        _warn(f"Consolidation failed: {e}")
        store.close()
        sys.exit(2)

    if getattr(args, "json", False):
        result["status"] = "ok"
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        label = " (dry run)" if args.dry_run else ""
        print(f"Consolidation complete{label}:")
        print(f"  Items processed: {result['items_processed']}")
        print(f"  Clusters found:  {result['clusters_found']}")
        print(f"  Items merged:    {result['items_merged']}")
        print(f"  Items promoted:  {result['items_promoted']}")
        if result.get("merge_chains"):
            print(f"\n  Merge chains:")
            for chain in result["merge_chains"]:
                src = ", ".join(chain.get("source_ids", []))
                dst = chain.get("merged_id", "(dry run)")
                print(f"    {src} → {dst}")

    store.close()


# ===========================================================================
# Command: loop  (bounded recall-answer loop)
# ===========================================================================


def cmd_loop(args: argparse.Namespace) -> None:
    """Run a bounded recall-answer loop with an LLM."""
    from memctl.loop import run_loop, replay_trace

    # Replay mode: just print the trace and exit
    if getattr(args, "replay", None):
        try:
            traces = replay_trace(args.replay)
        except FileNotFoundError:
            _warn(f"Trace file not found: {args.replay}")
            sys.exit(1)
        for t in traces:
            print(json.dumps(t.to_dict(), ensure_ascii=False))
        return

    # Read initial context from stdin
    if sys.stdin.isatty():
        _warn("[loop] No input on stdin. Pipe from 'memctl push' or provide context.")
        sys.exit(1)
    initial_context = sys.stdin.read()
    if not initial_context.strip():
        _warn("[loop] Empty input on stdin.")
        sys.exit(1)

    # Validate required --llm flag
    llm_cmd = args.llm
    if not llm_cmd:
        _warn("[loop] --llm is required (e.g. --llm 'claude -p')")
        sys.exit(1)

    # Read system prompt from file if path given
    user_system_prompt = None
    if getattr(args, "system_prompt", None):
        sp = args.system_prompt
        if os.path.isfile(sp):
            with open(sp, "r", encoding="utf-8") as f:
                user_system_prompt = f.read()
        else:
            user_system_prompt = sp

    # Open trace file if requested
    trace_file = None
    if getattr(args, "trace_file", None):
        trace_file = open(args.trace_file, "w", encoding="utf-8")

    db_path = _resolve_db(args)
    quiet = getattr(args, "quiet", False)

    try:
        result = run_loop(
            initial_context=initial_context,
            query=args.query,
            llm_cmd=llm_cmd,
            db_path=db_path,
            max_calls=args.max_calls,
            threshold=args.threshold,
            query_threshold=args.query_threshold,
            stable_steps=args.stable_steps,
            stop_on_no_new=not args.no_stop_on_no_new,
            protocol=args.protocol,
            llm_mode=args.llm_mode,
            system_prompt=user_system_prompt,
            budget=_resolve_budget(args),
            strict=getattr(args, "strict", False),
            trace=args.trace or trace_file is not None,
            trace_file=trace_file,
            quiet=quiet,
            timeout=args.timeout,
        )
    except ValueError as e:
        _warn(f"[loop] Protocol error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        _warn(f"[loop] LLM error: {e}")
        sys.exit(1)
    finally:
        if trace_file is not None:
            trace_file.close()

    # Print final answer to stdout (stdout purity: only the answer)
    print(result.answer)

    # Summary to stderr
    _info(
        f"[loop] {result.iterations} iteration(s), "
        f"stop={result.stop_reason}, converged={result.converged}"
    )

    # Exit code: --strict and not converged → 1
    if getattr(args, "strict", False) and not result.converged:
        sys.exit(1)


# ===========================================================================
# Command: mount  (register folder for sync)
# ===========================================================================


def cmd_mount(args: argparse.Namespace) -> None:
    """Register, list, or remove folder mounts."""
    from memctl.mount import register_mount, list_mounts, remove_mount

    db_path = _resolve_db(args)

    if getattr(args, "list", False):
        mounts = list_mounts(db_path)
        if getattr(args, "json", False):
            print(json.dumps(mounts, indent=2, ensure_ascii=False))
            return
        if not mounts:
            _info("No mounts registered.")
            return
        else:
            for m in mounts:
                synced = m["last_sync_at"] or "never"
                label = f" ({m['name']})" if m["name"] else ""
                print(f"  {m['mount_id']}{label}  {m['path']}  synced={synced}")
        return

    if getattr(args, "remove", None):
        ok = remove_mount(db_path, args.remove)
        if ok:
            _info(f"Removed mount: {args.remove}")
        else:
            _warn(f"Mount not found: {args.remove}")
            sys.exit(1)
        return

    # Register mode (default)
    if not args.path:
        _warn("Usage: memctl mount <path> [--name NAME] [--ignore PATTERN...]")
        sys.exit(1)

    ignore = getattr(args, "ignore", None) or []
    lang = getattr(args, "lang", None)
    name = getattr(args, "name", None)

    try:
        mount_id = register_mount(
            db_path, args.path,
            name=name,
            ignore_patterns=ignore,
            lang_hint=lang,
        )
    except (FileNotFoundError, NotADirectoryError) as e:
        _warn(f"Error: {e}")
        sys.exit(1)

    _info(f"Mounted: {mount_id} → {os.path.realpath(args.path)}")


# ===========================================================================
# Command: sync  (scan + ingest mounted folders)
# ===========================================================================


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync mounted folders into the memory store."""
    from memctl.sync import sync_mount, sync_all

    db_path = _resolve_db(args)
    full = getattr(args, "full", False)
    quiet = getattr(args, "quiet", False)

    if getattr(args, "path", None):
        # Sync specific path
        try:
            result = sync_mount(
                db_path, args.path,
                delta=not full,
                quiet=quiet,
            )
        except (FileNotFoundError, NotADirectoryError) as e:
            _warn(f"Error: {e}")
            sys.exit(1)

        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            _info(
                f"[sync] {result.files_new} new, {result.files_changed} changed, "
                f"{result.files_unchanged} unchanged, {result.chunks_created} chunks"
            )
    else:
        # Sync all mounts
        results = sync_all(db_path, delta=not full, quiet=quiet)
        if not results:
            _info("[sync] No mounts registered. Use 'memctl mount <path>' or 'memctl sync <path>'.")
            return
        if getattr(args, "json", False):
            print(json.dumps(
                {k: v.to_dict() for k, v in results.items()},
                indent=2, ensure_ascii=False,
            ))
        else:
            for path, r in results.items():
                _info(
                    f"[sync] {path}: {r.files_new} new, {r.files_changed} changed, "
                    f"{r.files_unchanged} unchanged, {r.chunks_created} chunks"
                )


# ===========================================================================
# Command: inspect  (structural injection block → stdout)
# ===========================================================================


def cmd_inspect(args: argparse.Namespace) -> None:
    """Generate a structural injection block from corpus metadata.

    With positional path: auto-mount + auto-sync + inspect (orchestration mode).
    Without positional path: inspect existing store (classic mode).
    """
    from memctl.inspect import inspect_mount, inspect_stats

    db_path = _resolve_db(args)
    budget = _resolve_budget(args)

    # --- Orchestration mode: positional path provided ---
    path = getattr(args, "path", None)
    if path is not None:
        from memctl.inspect import inspect_path

        # Warn if --mount filter is also given (path takes precedence)
        if getattr(args, "mount", None):
            _warn("--mount filter ignored when positional path is provided")

        sync_mode = (
            "never" if getattr(args, "no_sync", False)
            else getattr(args, "sync", "auto")
        )
        mount_mode = getattr(args, "mount_mode", "persist")
        ignore = getattr(args, "ignore", None) or None

        try:
            result = inspect_path(
                db_path, path,
                sync_mode=sync_mode,
                mount_mode=mount_mode,
                budget=budget,
                ignore_patterns=ignore,
                log=_info,
            )
        except (FileNotFoundError, NotADirectoryError) as e:
            _warn(str(e))
            sys.exit(1)
        except ValueError as e:
            _warn(str(e))
            sys.exit(1)

        if getattr(args, "json", False):
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            text = inspect_mount(
                db_path,
                mount_id=result.mount_id,
                mount_label=result.mount_label,
                budget=budget,
            )
            print(text)
        return

    # --- Classic mode: no positional path ---
    mount_filter = getattr(args, "mount", None)
    mount_id = None
    mount_label = None
    if mount_filter:
        from memctl.store import MemoryStore
        store = MemoryStore(db_path=db_path)
        try:
            m = store.read_mount(mount_filter)
        finally:
            store.close()
        if m is None:
            _warn(f"Mount not found: {mount_filter}")
            sys.exit(1)
        mount_id = m["mount_id"]
        mount_label = m.get("name") or m["path"]

    # Pass config thresholds if available
    inspect_config = getattr(getattr(args, "_config", None), "inspect", None)

    if getattr(args, "json", False):
        stats = inspect_stats(db_path, mount_id=mount_id, inspect_config=inspect_config)
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        text = inspect_mount(
            db_path,
            mount_id=mount_id,
            mount_label=mount_label,
            budget=budget,
        )
        print(text)


# ===========================================================================
# Command: ask  (one-shot folder Q&A)
# ===========================================================================


def cmd_ask(args: argparse.Namespace) -> None:
    """Answer a question about a folder (one-shot)."""
    from memctl.ask import ask_folder

    db_path = _resolve_db(args)

    # Resolve system prompt (text or file path)
    user_system_prompt = None
    if getattr(args, "system_prompt", None):
        sp = args.system_prompt
        if os.path.isfile(sp):
            with open(sp, "r", encoding="utf-8") as f:
                user_system_prompt = f.read()
        else:
            user_system_prompt = sp

    sync_mode = (
        "never" if getattr(args, "no_sync", False)
        else getattr(args, "sync", "auto")
    )

    try:
        result = ask_folder(
            path=args.path,
            question=args.question,
            llm_cmd=args.llm,
            db_path=db_path,
            sync_mode=sync_mode,
            mount_mode=getattr(args, "mount_mode", "persist"),
            budget=_resolve_budget(args),
            inspect_cap=getattr(args, "inspect_cap", 600),
            protocol=getattr(args, "protocol", "passive"),
            max_calls=getattr(args, "max_calls", 1),
            threshold=getattr(args, "threshold", 0.92),
            query_threshold=getattr(args, "query_threshold", 0.90),
            stable_steps=getattr(args, "stable_steps", 2),
            system_prompt=user_system_prompt,
            llm_mode=getattr(args, "llm_mode", "stdin"),
            timeout=getattr(args, "timeout", 300),
            ignore_patterns=getattr(args, "ignore", None) or None,
            log=_info,
        )
    except (FileNotFoundError, NotADirectoryError) as e:
        _warn(str(e))
        sys.exit(1)
    except ValueError as e:
        _warn(str(e))
        sys.exit(1)
    except RuntimeError as e:
        _warn(f"[ask] LLM error: {e}")
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.answer)


# ===========================================================================
# Command: chat  (interactive memory-backed REPL)
# ===========================================================================


def cmd_chat(args: argparse.Namespace) -> None:
    """Run the interactive memory-backed chat REPL."""
    from memctl.chat import chat_repl

    db_path = _resolve_db(args)

    # Resolve system prompt (text or file path)
    user_system_prompt = None
    if getattr(args, "system_prompt", None):
        sp = args.system_prompt
        if os.path.isfile(sp):
            with open(sp, "r", encoding="utf-8") as f:
                user_system_prompt = f.read()
        else:
            user_system_prompt = sp

    # Parse tags
    tags_str = getattr(args, "tags", "chat")
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    # Handle --folder: auto-mount/sync and get mount_id for scoped recall
    folder_mount_id = None
    folder_path = getattr(args, "folder", None)
    if folder_path:
        from memctl.inspect import inspect_path
        sync_mode = (
            "never" if getattr(args, "no_sync", False)
            else getattr(args, "sync", "auto")
        )
        try:
            ir = inspect_path(
                db_path, folder_path,
                sync_mode=sync_mode,
                mount_mode="persist",
                log=_info,
            )
            folder_mount_id = ir.mount_id
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            _warn(f"[chat] Folder error: {e}")
            sys.exit(1)

    # Config-driven readline history max
    cfg = getattr(args, "_config", None)
    history_max = cfg.chat.history_max if cfg else None

    chat_repl(
        args.llm,
        db_path=db_path,
        store_answers=getattr(args, "store", False),
        session_enabled=getattr(args, "session", False),
        history_turns=getattr(args, "history_turns", 5),
        session_budget=getattr(args, "session_budget", 4000),
        budget=_resolve_budget(args),
        tags=tags,
        protocol=getattr(args, "protocol", "passive"),
        max_calls=getattr(args, "max_calls", 1),
        threshold=getattr(args, "threshold", 0.92),
        system_prompt=user_system_prompt,
        llm_mode=getattr(args, "llm_mode", "stdin"),
        timeout=getattr(args, "timeout", 300),
        quiet=getattr(args, "quiet", False),
        sources=getattr(args, "sources", None),
        mount_id=folder_mount_id,
        readline_history_max=history_max,
    )


# ===========================================================================
# Command: export  (JSONL export → stdout)
# ===========================================================================


def cmd_export(args: argparse.Namespace) -> None:
    """Export memory items as JSONL to stdout."""
    from memctl.export_import import export_items

    db_path = _resolve_db(args)
    include_archived = getattr(args, "include_archived", False)

    count = export_items(
        db_path,
        tier=getattr(args, "tier", None),
        type_filter=getattr(args, "type", None),
        scope=getattr(args, "scope", None),
        exclude_archived=not include_archived,
        log=_info,
    )

    if getattr(args, "json", False):
        # Summary to stdout (JSONL already went to stdout, so this is separate)
        pass  # JSONL output IS the --json mode; no extra summary needed


# ===========================================================================
# Command: import  (JSONL import from file or stdin)
# ===========================================================================


def cmd_import(args: argparse.Namespace) -> None:
    """Import memory items from JSONL file or stdin."""
    from memctl.export_import import import_items

    db_path = _resolve_db(args)
    preserve_ids = getattr(args, "preserve_ids", False)
    dry_run = getattr(args, "dry_run", False)

    source_path = getattr(args, "file", None)
    if source_path:
        source = source_path
    else:
        source = sys.stdin

    result = import_items(
        db_path,
        source,
        preserve_ids=preserve_ids,
        dry_run=dry_run,
        log=_info,
    )

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        label = " (dry run)" if dry_run else ""
        print(f"Import complete{label}:")
        print(f"  Lines processed: {result.total_lines}")
        print(f"  Imported:        {result.imported}")
        print(f"  Skipped (dedup): {result.skipped_dedup}")
        print(f"  Skipped (policy):{result.skipped_policy}")
        print(f"  Errors:          {result.errors}")

    # Exit 1 when all lines failed (v0.7 exit code conformance)
    if result.errors > 0 and result.imported == 0:
        sys.exit(1)


# ===========================================================================
# Command: reindex  (rebuild FTS5 index)
# ===========================================================================


def cmd_reindex(args: argparse.Namespace) -> None:
    """Rebuild the FTS5 index, optionally with a new tokenizer."""
    import time

    db_path = _resolve_db(args)
    store = _open_store(db_path)

    old_tokenizer = store._fts_tokenizer
    new_tokenizer = (
        _resolve_fts(args.tokenizer) if getattr(args, "tokenizer", None)
        else old_tokenizer
    )
    changing = old_tokenizer != new_tokenizer
    item_count = store.stats()["total_items"]

    # -- Dry run: report plan and exit
    if getattr(args, "dry_run", False):
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "dry_run",
                "current_tokenizer": old_tokenizer,
                "new_tokenizer": new_tokenizer,
                "tokenizer_change": changing,
                "items_to_reindex": item_count,
            }, indent=2, ensure_ascii=False))
        else:
            _info(f"Current tokenizer: {old_tokenizer}")
            _info(f"New tokenizer:     {new_tokenizer}")
            _info(f"Items to reindex:  {item_count}")
            if not changing:
                _info("No tokenizer change — would rebuild in place.")
            else:
                _info("Tokenizer change detected — FTS table will be dropped and recreated.")
        store.close()
        return

    # -- Execute reindex
    t0 = time.monotonic()
    count = store.rebuild_fts(
        tokenizer=new_tokenizer if changing else None,
    )
    dt = time.monotonic() - t0

    if count < 0:
        _warn("FTS5 not available — cannot reindex.")
        store.close()
        sys.exit(1)

    # Log reindex event for auditability
    store._log_event("reindex", None, {
        "previous_tokenizer": old_tokenizer,
        "new_tokenizer": new_tokenizer,
        "tokenizer_changed": changing,
        "items_indexed": count,
        "duration_seconds": round(dt, 2),
    }, "")
    store._conn.commit()

    if getattr(args, "json", False):
        print(json.dumps({
            "status": "ok",
            "previous_tokenizer": old_tokenizer,
            "new_tokenizer": new_tokenizer,
            "tokenizer_changed": changing,
            "items_indexed": count,
            "duration_seconds": round(dt, 2),
        }, indent=2, ensure_ascii=False))
    else:
        _info(f"Reindexed {count} items in {dt:.1f}s")
        _info(f"  Previous: {old_tokenizer}")
        _info(f"  Current:  {new_tokenizer}")
        if changing:
            _info("  Tokenizer changed — FTS index rebuilt from scratch.")
        else:
            _info("  In-place rebuild (same tokenizer).")

    # Update eco config if present, so subsequent commands use the new tokenizer
    eco_config_path = Path(".claude/eco/config.json")
    if eco_config_path.exists():
        try:
            eco_cfg = json.loads(eco_config_path.read_text(encoding="utf-8"))
            eco_cfg["fts_tokenizer"] = new_tokenizer
            eco_config_path.write_text(
                json.dumps(eco_cfg, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _info(f"  Updated eco config: fts_tokenizer={new_tokenizer}")
        except Exception as e:
            logger.debug("Failed to update eco config: %s", e)

    store.close()


# ===========================================================================
# Command: reset  (truncate memory content)
# ===========================================================================


def cmd_reset(args: argparse.Namespace) -> None:
    """Reset memory — truncate all content, preserve schema."""
    db_path = _resolve_db(args)
    confirm = getattr(args, "confirm", False)
    dry_run = getattr(args, "dry_run", False)
    clear_mounts = getattr(args, "clear_mounts", False)

    if not dry_run and not confirm:
        _warn("Safety gate: --confirm is required to execute a reset.")
        _warn("Use --dry-run to preview what would be deleted.")
        sys.exit(1)

    store = _open_store(db_path)
    result = store.reset(
        preserve_mounts=not clear_mounts,
        dry_run=dry_run,
    )

    if result["dry_run"]:
        _info("Dry run — would delete:")
        total = 0
        for table, count in result.items():
            if table != "dry_run" and count > 0:
                _info(f"  {table}: {count}")
                total += count
        _info(f"  Total: {total} records")
    else:
        total = sum(v for k, v in result.items() if k != "dry_run")
        _info(f"Reset complete. Cleared {total} records.")
        if not clear_mounts:
            _info("Mount registrations preserved.")

    store.close()


# ===========================================================================
# Command: scripts-path  (print bundled scripts location)
# ===========================================================================


def cmd_scripts_path(args: argparse.Namespace) -> None:
    """Print the path to bundled installer scripts."""
    from importlib.resources import files
    scripts_dir = files("memctl") / "scripts"
    print(scripts_dir)


# ===========================================================================
# Command: serve  (start MCP server)
# ===========================================================================


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the memctl MCP server in foreground."""
    try:
        from memctl.mcp.server import create_server, build_parser as mcp_parser
    except ImportError:
        _warn("MCP dependencies not installed. Run: pip install memctl[mcp]")
        sys.exit(1)

    # Build server args from CLI args (pass through v0.8 flags)
    server_argv = ["--db", _resolve_db(args)]
    fts = getattr(args, "fts_tokenizer", None)
    if fts:
        server_argv.extend(["--fts-tokenizer", fts])
    if getattr(args, "verbose", False):
        server_argv.append("--verbose")
    # v0.8 passthrough flags
    db_root = getattr(args, "db_root", None)
    if db_root:
        server_argv.extend(["--db-root", db_root])
    if getattr(args, "secure", False):
        server_argv.append("--secure")
    if getattr(args, "no_rate_limit", False):
        server_argv.append("--no-rate-limit")
    audit_log = getattr(args, "audit_log", None)
    if audit_log:
        server_argv.extend(["--audit-log", audit_log])

    server_args = mcp_parser().parse_args(server_argv)

    # --check mode: validate configuration without opening the database.
    # The DB is created on first actual use, not at install time.
    if getattr(args, "check", False):
        from memctl import __version__
        db_path = server_args.db
        print(f"memctl MCP server OK (v{__version__}, db={db_path})")
        return

    try:
        mcp, _ = create_server(server_args)
    except ImportError:
        _warn("MCP dependencies not installed. Run: pip install memctl[mcp]")
        sys.exit(1)

    _info(f"memctl MCP server (db={server_args.db})")
    _info("Press Ctrl+C to stop.")
    mcp.run()


# ===========================================================================
# Shared argument helper
# ===========================================================================


def _add_pipe_arguments(p: argparse.ArgumentParser) -> None:
    """Register push/pipe arguments on a parser. Single source of truth."""
    p.add_argument("query", help="Natural language query for recall")
    p.add_argument(
        "--source", nargs="+", default=None,
        help="Files, directories, or globs to ingest (skipped if unchanged)",
    )
    p.add_argument(
        "--budget", type=int, default=None,
        help=f"Token budget (default: MEMCTL_BUDGET or 2200)",
    )
    p.add_argument("--tier", default=None, help="Filter by tier (stm/mtm/ltm)")
    p.add_argument(
        "--chunk-tokens", type=int, default=1800,
        help="Max tokens per chunk (default: 1800)",
    )
    p.add_argument("--tags", default=None, help="Comma-separated tags for ingested files")
    p.add_argument("--scope", default="project", help="Item scope (default: project)")
    p.set_defaults(func=cmd_push)


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """CLI entry point: memctl <command> [args]."""
    global _quiet

    # Shared parent with flags that work on all subcommands.
    # Using parents= propagates these into each subparser so
    # both `memctl --json stats` and `memctl stats --json` work.
    #
    # SUPPRESS defaults prevent subparser defaults from overriding
    # values parsed at the main-parser level (argparse parents quirk).
    _db_default = _env_str("MEMCTL_DB", ".memory/memory.db")
    _common = argparse.ArgumentParser(add_help=False)
    _common.add_argument(
        "--db", default=argparse.SUPPRESS,
        help=f"Path to SQLite database (default: {_db_default})",
    )
    _common.add_argument(
        "--quiet", "-q", action="store_true", default=argparse.SUPPRESS,
        help="Suppress stderr progress messages",
    )
    _common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="Machine-readable JSON output (stats, show, search)",
    )
    _common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="Enable verbose logging",
    )
    _common.add_argument(
        "--config", default=argparse.SUPPRESS,
        help="Path to config.json (default: auto-detect beside database)",
    )

    parser = argparse.ArgumentParser(
        prog="memctl",
        description="memctl — persistent structured memory for LLM orchestration",
        parents=[_common],
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # -- init --------------------------------------------------------------
    p_init = sub.add_parser("init", parents=[_common], help="Initialize a memory workspace")
    p_init.add_argument(
        "path", nargs="?", default=".memory",
        help="Workspace directory (default: .memory)",
    )
    p_init.add_argument("--force", action="store_true", help="Reinitialize existing workspace")
    p_init.add_argument(
        "--fts-tokenizer", default=None,
        help="FTS5 tokenizer preset: fr|en|raw (default: MEMCTL_FTS or fr)",
    )
    p_init.set_defaults(func=cmd_init)

    # -- push --------------------------------------------------------------
    p_push = sub.add_parser(
        "push", parents=[_common],
        help="Ingest + recall in one shot (injection block \u2192 stdout)",
    )
    _add_pipe_arguments(p_push)

    # -- pull --------------------------------------------------------------
    p_pull = sub.add_parser("pull", parents=[_common], help="Store LLM output from stdin into memory")
    p_pull.add_argument("--tags", default=None, help="Comma-separated tags")
    p_pull.add_argument("--title", default=None, help="Title for the stored note")
    p_pull.add_argument("--scope", default="project", help="Item scope (default: project)")
    p_pull.set_defaults(func=cmd_pull)

    # -- search ------------------------------------------------------------
    p_search = sub.add_parser("search", parents=[_common], help="Search memory items (FTS5)")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--tier", default=None, help="Filter by tier (stm/mtm/ltm)")
    p_search.add_argument("--type", default=None, help="Filter by type")
    p_search.add_argument("-k", type=int, default=10, help="Max results (default: 10)")
    p_search.set_defaults(func=cmd_search)

    # -- show --------------------------------------------------------------
    p_show = sub.add_parser("show", parents=[_common], help="Show memory item details")
    p_show.add_argument("id", help="Memory item ID")
    p_show.set_defaults(func=cmd_show)

    # -- stats -------------------------------------------------------------
    p_stats = sub.add_parser("stats", parents=[_common], help="Store statistics")
    p_stats.set_defaults(func=cmd_stats)

    # -- status ------------------------------------------------------------
    p_status = sub.add_parser("status", parents=[_common], help="Project memory health dashboard")
    p_status.set_defaults(func=cmd_status)

    # -- consolidate -------------------------------------------------------
    p_cons = sub.add_parser("consolidate", parents=[_common], help="Run deterministic consolidation")
    p_cons.add_argument("--scope", default="project", help="Scope filter (default: project)")
    p_cons.add_argument("--dry-run", action="store_true", help="Compute clusters but don't write")
    p_cons.set_defaults(func=cmd_consolidate)

    # -- loop --------------------------------------------------------------
    p_loop = sub.add_parser(
        "loop", parents=[_common],
        help="Bounded recall-answer loop with LLM",
    )
    p_loop.add_argument("query", help="Question to answer via iterative recall")
    p_loop.add_argument(
        "--llm", required=True,
        help="LLM command (e.g. 'claude -p', 'ollama run mistral')",
    )
    p_loop.add_argument(
        "--llm-mode", choices=["stdin", "file"], default="stdin",
        help="How to pass the prompt to the LLM (default: stdin)",
    )
    p_loop.add_argument(
        "--protocol", choices=["json", "regex", "passive"], default="json",
        help="LLM output protocol (default: json)",
    )
    p_loop.add_argument(
        "--system-prompt", default=None,
        help="User system prompt (text or file path, appended to protocol instructions)",
    )
    p_loop.add_argument(
        "--max-calls", type=int, default=3,
        help="Maximum LLM invocations (default: 3)",
    )
    p_loop.add_argument(
        "--threshold", type=float, default=0.92,
        help="Answer fixed-point similarity threshold (default: 0.92)",
    )
    p_loop.add_argument(
        "--query-threshold", type=float, default=0.90,
        help="Query cycle similarity threshold (default: 0.90)",
    )
    p_loop.add_argument(
        "--stable-steps", type=int, default=2,
        help="Consecutive stable steps for convergence (default: 2)",
    )
    p_loop.add_argument(
        "--no-stop-on-no-new", action="store_true",
        help="Continue even if recall returns no new items",
    )
    p_loop.add_argument(
        "--budget", type=int, default=None,
        help="Token budget for context (default: MEMCTL_BUDGET or 2200)",
    )
    p_loop.add_argument(
        "--trace", action="store_true",
        help="Emit JSONL trace to stderr",
    )
    p_loop.add_argument(
        "--trace-file", default=None,
        help="Write JSONL trace to file (stderr stays clean)",
    )
    p_loop.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if max-calls reached without convergence",
    )
    p_loop.add_argument(
        "--timeout", type=int, default=300,
        help="LLM subprocess timeout in seconds (default: 300)",
    )
    p_loop.add_argument(
        "--replay", default=None, metavar="TRACE.jsonl",
        help="Replay a trace file (no LLM calls)",
    )
    p_loop.set_defaults(func=cmd_loop)

    # -- mount -------------------------------------------------------------
    p_mount = sub.add_parser(
        "mount", parents=[_common],
        help="Register folder for sync",
    )
    p_mount.add_argument("path", nargs="?", default=None, help="Folder path to mount")
    p_mount.add_argument("--name", default=None, help="Human-readable label")
    p_mount.add_argument(
        "--ignore", nargs="+", default=None,
        help="Glob patterns to exclude during sync",
    )
    p_mount.add_argument(
        "--lang", default=None, choices=["fr", "en", "mix"],
        help="Language hint for FTS tokenizer selection",
    )
    p_mount.add_argument("--list", action="store_true", default=argparse.SUPPRESS, help="List mounts")
    p_mount.add_argument("--remove", default=None, metavar="ID", help="Remove mount by ID or name")
    p_mount.set_defaults(func=cmd_mount)

    # -- sync --------------------------------------------------------------
    p_sync = sub.add_parser(
        "sync", parents=[_common],
        help="Scan + ingest mounted folders",
    )
    p_sync.add_argument("path", nargs="?", default=None, help="Folder path (auto-registers)")
    p_sync.add_argument(
        "--full", action="store_true",
        help="Re-process all files (ignore delta cache)",
    )
    p_sync.set_defaults(func=cmd_sync)

    # -- inspect -----------------------------------------------------------
    p_inspect = sub.add_parser(
        "inspect", parents=[_common],
        help="Structural injection block → stdout (auto-mounts/syncs if path given)",
    )
    p_inspect.add_argument(
        "path", nargs="?", default=None,
        help="Folder to inspect (auto-mounts and auto-syncs as needed)",
    )
    p_inspect.add_argument(
        "--mount", default=None, metavar="ID_OR_NAME",
        help="Filter by registered mount (classic mode, no positional path)",
    )
    p_inspect.add_argument(
        "--budget", type=int, default=None,
        help="Token budget (default: MEMCTL_BUDGET or 2200)",
    )
    p_inspect.add_argument(
        "--sync", default="auto", choices=["auto", "always", "never"],
        help="Sync mode: auto (default), always, or never",
    )
    p_inspect.add_argument(
        "--no-sync", action="store_true", default=False,
        help="Skip sync (equivalent to --sync=never)",
    )
    p_inspect.add_argument(
        "--mount-mode", dest="mount_mode", default="persist",
        choices=["persist", "ephemeral"],
        help="Keep mount (persist, default) or remove after inspect (ephemeral)",
    )
    p_inspect.add_argument(
        "--ignore", nargs="+", default=None,
        help="Glob patterns to exclude when auto-mounting",
    )
    p_inspect.set_defaults(func=cmd_inspect)

    # -- ask ---------------------------------------------------------------
    p_ask = sub.add_parser(
        "ask", parents=[_common],
        help="One-shot folder Q&A",
    )
    p_ask.add_argument("path", help="Folder to ask about")
    p_ask.add_argument("question", help="Question to answer")
    p_ask.add_argument("--llm", required=True, help="LLM command (e.g. 'claude -p')")
    p_ask.add_argument(
        "--protocol", default="passive", choices=["json", "regex", "passive"],
        help="LLM output protocol (default: passive)",
    )
    p_ask.add_argument("--max-calls", type=int, default=1, help="Max loop iterations")
    p_ask.add_argument(
        "--budget", type=int, default=None,
        help="Total token budget (default: MEMCTL_BUDGET or 2200)",
    )
    p_ask.add_argument(
        "--inspect-cap", type=int, default=600,
        help="Tokens reserved for structural context (default: 600)",
    )
    p_ask.add_argument("--threshold", type=float, default=0.92, help="Answer similarity threshold")
    p_ask.add_argument("--query-threshold", type=float, default=0.90, help="Query cycle threshold")
    p_ask.add_argument("--stable-steps", type=int, default=2, help="Stable steps for convergence")
    p_ask.add_argument(
        "--sync", default="auto", choices=["auto", "always", "never"],
        help="Sync mode: auto (default), always, or never",
    )
    p_ask.add_argument(
        "--no-sync", action="store_true", default=False,
        help="Skip sync (equivalent to --sync=never)",
    )
    p_ask.add_argument(
        "--mount-mode", dest="mount_mode", default="persist",
        choices=["persist", "ephemeral"],
        help="Keep mount (persist, default) or remove after (ephemeral)",
    )
    p_ask.add_argument(
        "--ignore", nargs="+", default=None,
        help="Glob patterns to exclude",
    )
    p_ask.add_argument("--system-prompt", default=None, help="System prompt (text or file path)")
    p_ask.add_argument(
        "--llm-mode", default="stdin", choices=["stdin", "file"],
        help="How to pass prompt to LLM",
    )
    p_ask.add_argument("--timeout", type=int, default=300, help="LLM subprocess timeout (seconds)")
    p_ask.set_defaults(func=cmd_ask)

    # -- chat --------------------------------------------------------------
    p_chat = sub.add_parser(
        "chat", parents=[_common],
        help="Interactive memory-backed chat",
    )
    p_chat.add_argument("--llm", required=True, help="LLM command (e.g. 'claude -p')")
    p_chat.add_argument(
        "--protocol", default="passive", choices=["json", "regex", "passive"],
        help="LLM output protocol (default: passive)",
    )
    p_chat.add_argument("--max-calls", type=int, default=1, help="Max loop iterations per turn")
    p_chat.add_argument("--threshold", type=float, default=0.92, help="Answer similarity threshold")
    p_chat.add_argument(
        "--budget", type=int, default=_env_int("MEMCTL_BUDGET", 2200),
        help="Token budget for recall context",
    )
    p_chat.add_argument("--store", action="store_true", help="Persist each answer as STM item")
    p_chat.add_argument("--session", action="store_true", help="Enable in-memory session context")
    p_chat.add_argument("--history-turns", type=int, default=5, help="Session window size (turns)")
    p_chat.add_argument("--session-budget", type=int, default=4000, help="Session block char limit")
    p_chat.add_argument("--tags", default="chat", help="Tags for stored items (comma-separated)")
    p_chat.add_argument("--source", nargs="+", dest="sources", help="Pre-ingest files")
    p_chat.add_argument("--system-prompt", default=None, help="System prompt (text or file path)")
    p_chat.add_argument(
        "--llm-mode", default="stdin", choices=["stdin", "file"],
        help="How to pass prompt to LLM",
    )
    p_chat.add_argument("--timeout", type=int, default=300, help="LLM subprocess timeout (seconds)")
    p_chat.add_argument(
        "--folder", default=argparse.SUPPRESS,
        help="Scope recall to a folder (auto-mount/sync)",
    )
    p_chat.add_argument(
        "--sync", default="auto", choices=["auto", "always", "never"],
        help="Sync mode for --folder (default: auto)",
    )
    p_chat.add_argument(
        "--no-sync", action="store_true", default=False,
        help="Skip sync for --folder (equivalent to --sync=never)",
    )
    p_chat.set_defaults(func=cmd_chat)

    # -- export ------------------------------------------------------------
    p_export = sub.add_parser(
        "export", parents=[_common],
        help="Export memory items as JSONL to stdout",
    )
    p_export.add_argument("--tier", default=None, help="Filter by tier (stm/mtm/ltm)")
    p_export.add_argument("--type", default=None, help="Filter by type")
    p_export.add_argument("--scope", default=None, help="Filter by scope")
    p_export.add_argument(
        "--include-archived", action="store_true", default=False,
        help="Include archived items (default: exclude)",
    )
    p_export.set_defaults(func=cmd_export)

    # -- import ------------------------------------------------------------
    p_import = sub.add_parser(
        "import", parents=[_common],
        help="Import memory items from JSONL",
    )
    p_import.add_argument(
        "file", nargs="?", default=None,
        help="JSONL file to import (default: read stdin)",
    )
    p_import.add_argument(
        "--preserve-ids", action="store_true", default=False,
        help="Keep original item IDs (default: generate new IDs)",
    )
    p_import.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Count items without writing",
    )
    p_import.set_defaults(func=cmd_import)

    # -- reindex -----------------------------------------------------------
    p_reindex = sub.add_parser(
        "reindex", parents=[_common],
        help="Rebuild FTS5 index (optionally with new tokenizer)",
    )
    p_reindex.add_argument(
        "--tokenizer", default=None,
        help="Tokenizer preset (fr/en/raw) or full string (default: current)",
    )
    p_reindex.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show plan without executing",
    )
    p_reindex.set_defaults(func=cmd_reindex)

    # -- reset -------------------------------------------------------------
    p_reset = sub.add_parser(
        "reset", parents=[_common],
        help="Truncate all memory content (preserves schema + mounts)",
    )
    p_reset.add_argument(
        "--confirm", action="store_true", default=False,
        help="Required to execute the reset (safety gate)",
    )
    p_reset.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview what would be deleted without executing",
    )
    p_reset.add_argument(
        "--clear-mounts", action="store_true", default=False,
        help="Also clear mount registrations (default: preserve)",
    )
    p_reset.set_defaults(func=cmd_reset)

    # -- scripts-path ------------------------------------------------------

    p_scripts = sub.add_parser(
        "scripts-path", help="Print path to bundled installer scripts",
    )
    p_scripts.set_defaults(func=cmd_scripts_path)

    # -- serve -------------------------------------------------------------
    p_serve = sub.add_parser("serve", parents=[_common], help="Start MCP server")
    p_serve.add_argument(
        "--fts-tokenizer", default=None,
        help="FTS5 tokenizer preset: fr|en|raw (default: MEMCTL_FTS or fr)",
    )
    p_serve.add_argument(
        "--check", action="store_true", default=False,
        help="Verify server can start (create + tool count), then exit",
    )
    p_serve.add_argument(
        "--db-root", default=None,
        help="Constrain DB paths to this directory tree (MCP security)",
    )
    p_serve.add_argument(
        "--secure", action="store_true", default=False,
        help="Enable secure defaults (db-root=CWD if not set)",
    )
    p_serve.add_argument(
        "--no-rate-limit", action="store_true", default=False,
        help="Disable MCP rate limiting",
    )
    p_serve.add_argument(
        "--audit-log", default=None,
        help="Audit log file path (default: stderr)",
    )
    p_serve.set_defaults(func=cmd_serve)

    # -- Parse and dispatch ------------------------------------------------
    args = parser.parse_args()

    _quiet = getattr(args, "quiet", False)

    # -- Resolve config file -----------------------------------------------
    config_path = getattr(args, "config", None)
    if config_path is None:
        db_path = _resolve_db(args)
        candidate = os.path.join(os.path.dirname(os.path.abspath(db_path)), "config.json")
        if os.path.isfile(candidate):
            config_path = candidate

    from memctl.config import load_config
    _config = load_config(config_path)

    # Attach config to args so handlers can access it
    args._config = _config

    if getattr(args, "verbose", False):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except BrokenPipeError:
        # Handle broken pipe gracefully (e.g. memctl search | head)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        _warn(f"Internal error: {e}")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc(file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

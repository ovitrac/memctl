"""
memctl CLI — Unix-Composable Memory Commands

Commands:
    memctl init   [PATH]                     — scaffold store + .gitignore
    memctl push   "query" [--source ...]     — ingest + recall → stdout
    memctl pull   [--tags T] [--title T]     — stdin → proposals → store
    memctl search "query" [-k N] [--tier T]  — FTS5 search → stdout
    memctl show   <id>                       — display single memory item
    memctl stats                             — store metrics
    memctl consolidate [--dry-run]           — merge + promote STM items
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

_DEFAULT_CONFIG_YAML = """\
# memctl v0.1.0 — config.yaml is reserved for v0.2+
# CLI reads MEMCTL_* env vars and --flags only.
store:
  fts_tokenizer: "{fts}"
"""


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
    config_path = target / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            _DEFAULT_CONFIG_YAML.format(fts=fts), encoding="utf-8"
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

    # Attempt structured proposal extraction
    proposals = []
    try:
        from memctl.proposer import MemoryProposer
        proposer = MemoryProposer()
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
# Command: serve  (start MCP server)
# ===========================================================================


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the memctl MCP server in foreground."""
    try:
        from memctl.mcp.server import create_server, build_parser as mcp_parser
    except ImportError:
        _warn("MCP dependencies not installed. Run: pip install memctl[mcp]")
        sys.exit(1)

    # Build server args from CLI args
    server_argv = ["--db", _resolve_db(args)]
    fts = getattr(args, "fts_tokenizer", None)
    if fts:
        server_argv.extend(["--fts-tokenizer", fts])
    if getattr(args, "verbose", False):
        server_argv.append("--verbose")

    server_args = mcp_parser().parse_args(server_argv)

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

    # -- consolidate -------------------------------------------------------
    p_cons = sub.add_parser("consolidate", parents=[_common], help="Run deterministic consolidation")
    p_cons.add_argument("--scope", default="project", help="Scope filter (default: project)")
    p_cons.add_argument("--dry-run", action="store_true", help="Compute clusters but don't write")
    p_cons.set_defaults(func=cmd_consolidate)

    # -- serve -------------------------------------------------------------
    p_serve = sub.add_parser("serve", parents=[_common], help="Start MCP server")
    p_serve.add_argument(
        "--fts-tokenizer", default=None,
        help="FTS5 tokenizer preset: fr|en|raw (default: MEMCTL_FTS or fr)",
    )
    p_serve.set_defaults(func=cmd_serve)

    # -- Parse and dispatch ------------------------------------------------
    args = parser.parse_args()

    _quiet = getattr(args, "quiet", False)

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

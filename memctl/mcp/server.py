"""
memctl MCP Server — Persistent Structured Memory for LLMs

Standalone MCP server exposing memctl operations via the
Model Context Protocol.  Works with Claude Desktop, Claude Code,
VS Code, and any MCP-compatible client.

Architecture: thin MCP layer delegating to MemoryStore + MemoryPolicy.
Zero business logic in this module — all logic lives in memctl/*.

Defense-in-depth middleware (v0.8):
    L0: ServerGuard   — path validation, size caps
    L1: RateLimiter   — token-bucket throttling
    L1: SessionTracker — minimal in-memory session state
    L1: AuditLogger   — structured JSONL audit trail

Usage:
    python -m memctl.mcp.server --db /path/to/memory.db
    python -m memctl.mcp.server --fts-tokenizer fr
    python -m memctl.mcp.server --db-root ~/.local/share/memctl/db

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Instructions embedded in FastMCP — always visible to any MCP client.
_MCP_INSTRUCTIONS = (
    "Persistent structured memory for LLM orchestration (15 tools).\n"
    "\n"
    "PRIMARY: Use memory_recall for token-budgeted context injection.\n"
    "SEARCH:  Use memory_search for interactive discovery.\n"
    "STORE:   Use memory_propose to save findings (tags + provenance).\n"
    "         Use memory_write only for privileged/dev operations.\n"
    "FOLDER:  Use memory_mount to register folders, memory_sync to ingest,\n"
    "         memory_inspect for structural summaries, memory_ask for Q&A.\n"
    "DATA:    Use memory_export/memory_import for JSONL backup/migration.\n"
    "LOOP:    Use memory_loop for iterative recall-answer refinement.\n"
    "\n"
    "Rules:\n"
    "- Store distilled knowledge, NOT raw document excerpts\n"
    "- Include provenance (source_doc, page/section) when available\n"
    "- Use 3-7 lowercase hyphenated tags per item\n"
    "- NEVER store secrets, tool invocations, or system prompt fragments\n"
    "- NEVER store instructions to yourself ('always remember to...')\n"
    "- PII (emails, phones, SSNs) is quarantined — not injected into context\n"
    "- Rate limits apply: 20 writes/min, 120 reads/min per session\n"
)

# Default db-root for MCP mode
_DEFAULT_MCP_DB_ROOT = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "memctl" / "db"
)


def _env_int(name: str, default: int) -> int:
    """Read an integer from environment, with fallback."""
    val = os.environ.get(name)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the memory MCP server."""
    p = argparse.ArgumentParser(
        prog="memctl-mcp",
        description="memctl MCP Server — persistent structured memory for LLMs",
    )

    # Existing flags
    p.add_argument(
        "--db",
        default=os.environ.get("MEMCTL_DB", ".memory/memory.db"),
        help="SQLite database path (default: .memory/memory.db or $MEMCTL_DB)",
    )
    p.add_argument(
        "--fts-tokenizer",
        default=os.environ.get("MEMCTL_FTS", "fr"),
        help=(
            "FTS5 tokenizer preset: fr (accent-insensitive), en (porter stemming), "
            "raw (unicode61), or a custom tokenizer string. "
            "Default: fr or $MEMCTL_FTS"
        ),
    )
    p.add_argument(
        "--inject-budget",
        type=int,
        default=_env_int("MEMCTL_BUDGET", 2200),
        help="Default injection budget in tokens (default: 2200 or $MEMCTL_BUDGET)",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    # v0.8: Path & resource guardrails
    g = p.add_argument_group("path & resource guardrails")
    g.add_argument(
        "--db-root",
        default=os.environ.get("MEMCTL_DB_ROOT"),
        help=(
            "Constrain DB paths to this directory tree. "
            "Default in MCP: ~/.local/share/memctl/db or $MEMCTL_DB_ROOT"
        ),
    )
    g.add_argument(
        "--secure",
        action="store_true",
        help="Enable secure defaults (db-root=CWD if not explicitly set)",
    )
    g.add_argument(
        "--max-write-bytes",
        type=int,
        default=_env_int("MEMCTL_MAX_WRITE_BYTES", 65_536),
        help="Per-call write size cap in bytes (default: 65536)",
    )

    # v0.8: Rate limiting
    r = p.add_argument_group("rate limiting")
    r.add_argument(
        "--rate-limit",
        action="store_true",
        dest="rate_limit",
        default=True,
        help="Enable rate limiting (default: enabled)",
    )
    r.add_argument(
        "--no-rate-limit",
        action="store_false",
        dest="rate_limit",
        help="Disable rate limiting",
    )
    r.add_argument(
        "--writes-per-minute",
        type=int,
        default=_env_int("MEMCTL_WRITES_PER_MINUTE", 20),
        help="Write operations cap per minute (default: 20)",
    )
    r.add_argument(
        "--reads-per-minute",
        type=int,
        default=_env_int("MEMCTL_READS_PER_MINUTE", 120),
        help="Read operations cap per minute (default: 120)",
    )
    r.add_argument(
        "--burst-factor",
        type=float,
        default=2.0,
        help="Burst multiplier for rate limiter (default: 2.0)",
    )
    r.add_argument(
        "--max-proposals-per-turn",
        type=int,
        default=5,
        help="Max proposals per turn (default: 5)",
    )

    # v0.8: Audit
    a = p.add_argument_group("audit")
    a.add_argument(
        "--audit-log",
        default=None,
        help="Audit log file path (default: stderr)",
    )

    return p


def create_server(args=None):
    """
    Create and configure the FastMCP server with memory tools.

    Args:
        args: Parsed argparse.Namespace, or None to parse from sys.argv.

    Returns:
        (mcp_server, store) tuple.
    """
    from mcp.server.fastmcp import FastMCP

    from memctl.config import MemoryConfig, StoreConfig
    from memctl.mcp.audit import AuditLogger
    from memctl.mcp.guard import ServerGuard
    from memctl.mcp.rate_limiter import RateLimiter
    from memctl.mcp.session import SessionTracker
    from memctl.mcp.tools import register_memory_tools
    from memctl.policy import MemoryPolicy
    from memctl.store import FTS_TOKENIZER_PRESETS, MemoryStore

    if args is None:
        args = build_parser().parse_args()

    # Resolve FTS tokenizer preset
    fts_tok = FTS_TOKENIZER_PRESETS.get(args.fts_tokenizer, args.fts_tokenizer)

    # --- L0: Resolve db-root ---
    db_root = None
    if args.db_root:
        db_root = Path(args.db_root)
    elif getattr(args, "secure", False):
        db_root = Path.cwd()
    else:
        # MCP secure-by-default: use default db root
        db_root = _DEFAULT_MCP_DB_ROOT

    guard = ServerGuard(
        db_root=db_root,
        max_write_bytes=args.max_write_bytes,
    )

    # Validate db path against root
    db_path_resolved = guard.validate_db_path(args.db)

    # Build config
    config = MemoryConfig(
        store=StoreConfig(
            db_path=str(db_path_resolved),
            fts_tokenizer=fts_tok,
        ),
    )

    # Create store and policy
    store = MemoryStore(
        db_path=config.store.db_path,
        fts_tokenizer=config.store.fts_tokenizer,
    )
    policy_engine = MemoryPolicy(config.policy)

    # --- L1: Rate limiter ---
    rate_limiter = None
    if args.rate_limit:
        rate_limiter = RateLimiter(
            writes_per_minute=args.writes_per_minute,
            reads_per_minute=args.reads_per_minute,
            burst_factor=args.burst_factor,
            max_proposals_per_turn=args.max_proposals_per_turn,
        )

    # --- L1: Session tracker ---
    session_tracker = SessionTracker()

    # --- L1: Audit logger ---
    audit_output = None
    if args.audit_log:
        audit_output = open(args.audit_log, "a", encoding="utf-8")
    audit = AuditLogger(output=audit_output)

    # Create FastMCP server
    mcp = FastMCP(
        name="memctl Memory",
        instructions=_MCP_INSTRUCTIONS,
    )

    # Register all 15 tools with middleware
    register_memory_tools(
        mcp, store, policy_engine, config,
        guard=guard,
        rate_limiter=rate_limiter,
        session_tracker=session_tracker,
        audit=audit,
    )

    logger.info(
        "memctl MCP server ready: db=%s, fts=%s, db_root=%s, rate_limit=%s",
        args.db, args.fts_tokenizer,
        db_root or "(none)",
        "on" if rate_limiter else "off",
    )

    return mcp, store


def main():
    """CLI entry point — parse args, create server, run."""
    parser = build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    mcp, _store = create_server(args)
    mcp.run()


if __name__ == "__main__":
    main()

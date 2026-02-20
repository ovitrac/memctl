"""
memctl MCP Server — Persistent Structured Memory for LLMs

Standalone MCP server exposing memctl operations via the
Model Context Protocol.  Works with Claude Desktop, Claude Code,
VS Code, and any MCP-compatible client.

Architecture: thin MCP layer delegating to MemoryStore + MemoryPolicy.
Zero business logic in this module — all logic lives in memctl/*.

Usage:
    python -m memctl.mcp.server --db /path/to/memory.db
    python -m memctl.mcp.server --fts-tokenizer fr

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Instructions embedded in FastMCP — always visible to any MCP client.
_MCP_INSTRUCTIONS = (
    "Persistent structured memory for LLM orchestration (14 tools).\n"
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
)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the memory MCP server."""
    p = argparse.ArgumentParser(
        prog="memctl-mcp",
        description="memctl MCP Server — persistent structured memory for LLMs",
    )
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
        default=int(os.environ.get("MEMCTL_BUDGET", "2200")),
        help="Default injection budget in tokens (default: 2200 or $MEMCTL_BUDGET)",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
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
    from memctl.mcp.tools import register_memory_tools
    from memctl.policy import MemoryPolicy
    from memctl.store import FTS_TOKENIZER_PRESETS, MemoryStore

    if args is None:
        args = build_parser().parse_args()

    # Resolve FTS tokenizer preset
    fts_tok = FTS_TOKENIZER_PRESETS.get(args.fts_tokenizer, args.fts_tokenizer)

    # Build config
    config = MemoryConfig(
        store=StoreConfig(
            db_path=args.db,
            fts_tokenizer=fts_tok,
        ),
    )

    # Create store and policy
    store = MemoryStore(
        db_path=config.store.db_path,
        fts_tokenizer=config.store.fts_tokenizer,
    )
    policy_engine = MemoryPolicy(config.policy)

    # Create FastMCP server
    mcp = FastMCP(
        name="memctl Memory",
        instructions=_MCP_INSTRUCTIONS,
    )

    # Register all 14 tools
    register_memory_tools(mcp, store, policy_engine, config)

    logger.info(
        "memctl MCP server ready: db=%s, fts=%s",
        args.db, args.fts_tokenizer,
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

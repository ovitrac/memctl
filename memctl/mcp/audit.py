"""
MCP Audit Logger — Structured JSONL logging for MCP tool calls.

Layer 1 of MCP defense-in-depth: provides observability and abuse
detection through structured, schema-versioned audit records.

Privacy rules (v1 contract):
- Never log raw content beyond a 120-char preview
- Include SHA-256 hash for correlation without content storage
- DB paths are root-relative when db-root is set

The log() method is fire-and-forget: catches all exceptions
internally and never disrupts tool execution.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TextIO

AUDIT_SCHEMA_VERSION = 1
PREVIEW_MAX_CHARS = 120


class AuditLogger:
    """Structured JSONL audit logger for MCP tool calls."""

    def __init__(self, output: Optional[TextIO] = None):
        """
        Args:
            output: File handle for audit output. None → stderr.
        """
        self._output = output if output is not None else sys.stderr

    def new_rid(self) -> str:
        """Generate a new request ID (UUID4 hex string)."""
        return uuid.uuid4().hex

    def log(
        self,
        tool: str,
        rid: str,
        session_id: str,
        db_path: str,
        outcome: str,
        detail: Optional[Dict[str, Any]] = None,
        latency_ms: float = 0.0,
    ) -> None:
        """
        Write one JSONL audit record. Fire-and-forget — never raises.

        Args:
            tool: MCP tool name (e.g. "memory_write").
            rid: Request ID (from new_rid()).
            session_id: Session/connection ID or "default".
            db_path: DB path (should be root-relative via guard).
            outcome: "ok", "error", "rejected", or "rate_limited".
            detail: Tool-specific fields (see schema docs).
            latency_ms: Wall-clock latency in milliseconds.
        """
        try:
            record = {
                "v": AUDIT_SCHEMA_VERSION,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") +
                      f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
                "rid": rid,
                "tool": tool,
                "sid": session_id,
                "db": db_path,
                "outcome": outcome,
            }
            if detail:
                record["d"] = detail
            record["ms"] = round(latency_ms, 1)

            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            self._output.write(line + "\n")
            self._output.flush()
        except Exception:
            # Fire-and-forget: audit failures must never disrupt tool execution
            pass

    @staticmethod
    def make_content_detail(
        content: str,
        policy_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build safe audit detail fields for content-carrying tools.

        Privacy rules:
        - preview: first 120 chars, newlines → space, truncated with '…'
        - hash: SHA-256 hex digest (correlate without storing content)
        - bytes: total content size
        - policy: decision + rule when applicable

        Returns:
            Dict with bytes, hash, preview, and optionally policy.
        """
        content_bytes = len(content.encode("utf-8"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Preview: first N chars, sanitize newlines, truncate
        preview = content[:PREVIEW_MAX_CHARS].replace("\n", " ").replace("\r", "")
        if len(content) > PREVIEW_MAX_CHARS:
            preview = preview.rstrip() + "\u2026"  # …

        result: Dict[str, Any] = {
            "bytes": content_bytes,
            "hash": content_hash,
            "preview": preview,
        }
        if policy_result is not None:
            result["policy"] = policy_result

        return result

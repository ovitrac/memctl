"""
MCP Server Guard — Path validation and resource caps.

Layer 0 of MCP defense-in-depth: prevents path traversal, symlink
escape, write size abuse, and import batch overflow.

The guard is instantiated once at server startup and shared across
all tool calls via closure.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class GuardError(ValueError):
    """Raised when a guard check fails (path violation, size cap, etc.)."""


class ServerGuard:
    """Path validation and resource guardrails for MCP server."""

    def __init__(
        self,
        db_root: Optional[Path] = None,
        max_write_bytes: int = 65_536,
        max_write_bytes_per_minute: int = 524_288,
        max_import_items: int = 500,
        max_db_size_mb: Optional[int] = None,
    ):
        self._db_root: Optional[Path] = db_root.resolve() if db_root else None
        self._max_write_bytes = max_write_bytes
        self._max_write_bytes_per_minute = max_write_bytes_per_minute
        self._max_import_items = max_import_items
        self._max_db_size_mb = max_db_size_mb

        # Per-session cumulative write tracking (session_id → {window_start, bytes})
        self._write_budgets: Dict[str, dict] = {}

    @property
    def db_root(self) -> Optional[Path]:
        """Return the canonical db-root, or None if unset."""
        return self._db_root

    def validate_db_path(self, requested: str) -> Path:
        """
        Resolve and validate a database path against db-root.

        Algorithm:
        1. Pre-check: reject if any path segment is '..'
        2. Resolve: Path(requested).resolve(strict=False)
        3. Containment: resolved must be under db_root
        4. Return canonical path

        Raises GuardError on violation. If db_root is None, skip containment.
        """
        # Step 1: reject '..' segments before resolve (clear UX)
        raw = Path(requested)
        for part in raw.parts:
            if part == "..":
                raise GuardError(
                    f"Path traversal rejected: '..' in path '{requested}'"
                )

        # Step 2: canonical resolve (follows symlinks)
        if self._db_root and not raw.is_absolute():
            resolved = (self._db_root / raw).resolve()
        else:
            resolved = raw.resolve()

        # Step 3: containment check
        if self._db_root is not None:
            try:
                resolved.relative_to(self._db_root)
            except ValueError:
                raise GuardError(
                    f"Path outside db-root: '{resolved}' is not under '{self._db_root}'"
                )

        return resolved

    def relative_db_path(self, resolved: Path) -> str:
        """
        Return a root-relative path string for audit logging.

        If db_root is set, returns the relative portion.
        Otherwise returns the absolute path as string.
        Never leaks absolute paths when a root is configured.
        """
        if self._db_root is not None:
            try:
                return str(resolved.relative_to(self._db_root))
            except ValueError:
                pass
        return str(resolved)

    def check_write_size(self, content: str) -> None:
        """Raise GuardError if content exceeds max_write_bytes."""
        size = len(content.encode("utf-8"))
        if size > self._max_write_bytes:
            raise GuardError(
                f"Write size {size} bytes exceeds limit of {self._max_write_bytes} bytes"
            )

    def check_write_budget(self, session_id: str, content_bytes: int) -> None:
        """Track cumulative writes per minute. Raise GuardError if over budget."""
        import time

        now = time.monotonic()
        bucket = self._write_budgets.get(session_id)

        if bucket is None or (now - bucket["window_start"]) >= 60.0:
            # New window
            self._write_budgets[session_id] = {
                "window_start": now,
                "bytes": content_bytes,
            }
            return

        new_total = bucket["bytes"] + content_bytes
        if new_total > self._max_write_bytes_per_minute:
            raise GuardError(
                f"Write budget exceeded: {new_total} bytes in current minute "
                f"(limit: {self._max_write_bytes_per_minute} bytes/min)"
            )
        bucket["bytes"] = new_total

    def check_import_batch(self, count: int) -> None:
        """Raise GuardError if import batch exceeds max_import_items."""
        if count > self._max_import_items:
            raise GuardError(
                f"Import batch of {count} items exceeds limit of {self._max_import_items}"
            )

    def check_db_size(self, db_path: Path) -> None:
        """Log warning if DB exceeds max_db_size_mb. Non-blocking."""
        if self._max_db_size_mb is None:
            return
        try:
            size_mb = db_path.stat().st_size / (1024 * 1024)
            if size_mb > self._max_db_size_mb:
                logger.warning(
                    "Database %s is %.1f MB (limit: %d MB)",
                    db_path, size_mb, self._max_db_size_mb,
                )
        except OSError:
            pass

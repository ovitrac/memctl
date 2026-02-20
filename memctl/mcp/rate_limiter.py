"""
MCP Rate Limiter — Token-bucket throttling for MCP tool calls.

Layer 1 of MCP defense-in-depth: prevents volume abuse through
the MCP interface (runaway agents, DB flooding, DoS).

No threading locks — FastMCP is async single-threaded.

Accounting definitions (locked):
    write:   memory_write, memory_propose, memory_import,
             memory_consolidate, memory_sync
    read:    memory_recall, memory_search, memory_read,
             memory_export, memory_inspect, memory_ask, memory_loop
    exempt:  memory_stats, memory_mount (metadata only)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Set

# Tool classification (locked — tests enforce these)
WRITE_TOOLS: Set[str] = {
    "memory_write", "memory_propose", "memory_import",
    "memory_consolidate", "memory_sync",
}
READ_TOOLS: Set[str] = {
    "memory_recall", "memory_search", "memory_read",
    "memory_export", "memory_inspect", "memory_ask", "memory_loop",
}
EXEMPT_TOOLS: Set[str] = {
    "memory_stats", "memory_mount",
}


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded."""

    def __init__(self, retry_after_ms: int, message: str):
        self.retry_after_ms = retry_after_ms
        super().__init__(message)


@dataclass
class _Bucket:
    """Token bucket for rate limiting."""
    capacity: float
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)
    refill_rate: float = 0.0  # tokens per second

    def refill(self) -> None:
        """Refill tokens based on elapsed wall time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    def try_consume(self, n: int = 1) -> int:
        """
        Try to consume n tokens. Returns 0 on success,
        or milliseconds to wait if insufficient tokens.
        """
        self.refill()
        if self.tokens >= n:
            self.tokens -= n
            return 0
        deficit = n - self.tokens
        wait_ms = int((deficit / self.refill_rate) * 1000) if self.refill_rate > 0 else 60_000
        return wait_ms


@dataclass
class _SessionBuckets:
    """Per-session read and write buckets."""
    read: _Bucket
    write: _Bucket
    proposals_this_turn: int = 0


class RateLimiter:
    """Token-bucket rate limiter for MCP tool calls."""

    def __init__(
        self,
        writes_per_minute: int = 20,
        reads_per_minute: int = 120,
        burst_factor: float = 2.0,
        max_proposals_per_turn: int = 5,
    ):
        self._writes_per_minute = writes_per_minute
        self._reads_per_minute = reads_per_minute
        self._burst_factor = burst_factor
        self._max_proposals_per_turn = max_proposals_per_turn

        # Per-session buckets
        self._sessions: Dict[str, _SessionBuckets] = {}

    def _get_buckets(self, session_id: str) -> _SessionBuckets:
        """Get or create per-session buckets."""
        if session_id not in self._sessions:
            write_cap = self._writes_per_minute * self._burst_factor
            read_cap = self._reads_per_minute * self._burst_factor
            self._sessions[session_id] = _SessionBuckets(
                read=_Bucket(
                    capacity=read_cap,
                    tokens=read_cap,
                    refill_rate=self._reads_per_minute / 60.0,
                ),
                write=_Bucket(
                    capacity=write_cap,
                    tokens=write_cap,
                    refill_rate=self._writes_per_minute / 60.0,
                ),
            )
        return self._sessions[session_id]

    def check_read(self, session_id: str) -> None:
        """Consume a read token. Raise RateLimitExceeded if empty."""
        buckets = self._get_buckets(session_id)
        wait = buckets.read.try_consume(1)
        if wait > 0:
            raise RateLimitExceeded(
                wait,
                f"Read rate limit exceeded ({self._reads_per_minute}/min). "
                f"Retry after {wait}ms.",
            )

    def check_write(self, session_id: str) -> None:
        """Consume a write token. Raise RateLimitExceeded if empty."""
        buckets = self._get_buckets(session_id)
        wait = buckets.write.try_consume(1)
        if wait > 0:
            raise RateLimitExceeded(
                wait,
                f"Write rate limit exceeded ({self._writes_per_minute}/min). "
                f"Retry after {wait}ms.",
            )

    def check_write_n(self, session_id: str, n: int) -> None:
        """Consume n write tokens (for batch import). Raise if exceeded."""
        buckets = self._get_buckets(session_id)
        wait = buckets.write.try_consume(n)
        if wait > 0:
            raise RateLimitExceeded(
                wait,
                f"Write rate limit exceeded: {n} items would exceed "
                f"{self._writes_per_minute}/min. Retry after {wait}ms.",
            )

    def check_proposals(self, session_id: str, count: int) -> None:
        """Check per-turn proposal count. Raise if exceeded."""
        buckets = self._get_buckets(session_id)
        if buckets.proposals_this_turn + count > self._max_proposals_per_turn:
            raise RateLimitExceeded(
                0,
                f"Proposal limit exceeded: {buckets.proposals_this_turn + count} "
                f"proposals this turn (limit: {self._max_proposals_per_turn}).",
            )
        buckets.proposals_this_turn += count

    def reset_turn(self, session_id: str) -> None:
        """Reset per-turn counters (call at turn boundary)."""
        if session_id in self._sessions:
            self._sessions[session_id].proposals_this_turn = 0

    def classify_tool(self, tool_name: str) -> str:
        """Return 'write', 'read', or 'exempt' for a tool name."""
        if tool_name in WRITE_TOOLS:
            return "write"
        if tool_name in READ_TOOLS:
            return "read"
        return "exempt"

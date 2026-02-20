"""
MCP Session Tracker — Minimal in-memory session state.

Tracks per-session state (turn count, writes) keyed by MCP
connection/session ID. No persistence — resets on server restart.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict

# Default session ID when no MCP context is available
DEFAULT_SESSION_ID = "default"


@dataclass
class SessionState:
    """In-memory session state."""
    session_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    turn_count: int = 0
    writes_this_turn: int = 0

    def increment_turn(self) -> int:
        """Increment turn count and reset per-turn counters. Returns new count."""
        self.turn_count += 1
        self.writes_this_turn = 0
        return self.turn_count

    def record_write(self) -> None:
        """Record a write operation in the current turn."""
        self.writes_this_turn += 1


class SessionTracker:
    """In-memory session tracking keyed by session ID."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    def resolve_session_id(self, mcp_context_id: str | None = None) -> str:
        """
        Resolve session ID from MCP context.

        Primary: use MCP-provided session/connection ID.
        Fallback: DEFAULT_SESSION_ID singleton.
        """
        return mcp_context_id if mcp_context_id else DEFAULT_SESSION_ID

    def reset(self, session_id: str) -> None:
        """Remove session state entirely."""
        self._sessions.pop(session_id, None)

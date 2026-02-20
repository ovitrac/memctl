"""
Tests for memctl.mcp.session — MCP session tracking.

Invariants tested:
- S1: Session fallback to "default" when no MCP context
- S2: Session turn count increments deterministically

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.mcp.session import DEFAULT_SESSION_ID, SessionState, SessionTracker


@pytest.fixture
def tracker():
    """Fresh session tracker."""
    return SessionTracker()


# ── S1: Session fallback to "default" when no MCP context ───────────

class TestSessionFallback:
    """S1: resolve_session_id falls back to DEFAULT_SESSION_ID."""

    def test_resolve_none_returns_default(self, tracker):
        """None MCP context resolves to 'default'."""
        assert tracker.resolve_session_id(None) == DEFAULT_SESSION_ID

    def test_resolve_empty_string_returns_default(self, tracker):
        """Empty string MCP context also resolves to 'default'."""
        assert tracker.resolve_session_id("") == DEFAULT_SESSION_ID

    def test_resolve_explicit_id_preserved(self, tracker):
        """Explicit MCP context ID is used as-is."""
        sid = "conn-abc-123"
        assert tracker.resolve_session_id(sid) == sid


# ── S2: Session turn count increments deterministically ─────────────

class TestTurnCount:
    """S2: Turn count increases by exactly 1 per increment_turn()."""

    def test_initial_turn_count_is_zero(self, tracker):
        """New session starts at turn 0."""
        session = tracker.get_or_create("sess-1")
        assert session.turn_count == 0

    def test_increment_turn_returns_new_count(self, tracker):
        """increment_turn() returns the new count (1-indexed)."""
        session = tracker.get_or_create("sess-2")
        assert session.increment_turn() == 1
        assert session.increment_turn() == 2
        assert session.increment_turn() == 3
        assert session.turn_count == 3


# ── get_or_create idempotency ───────────────────────────────────────

class TestGetOrCreate:
    """get_or_create returns the same object for the same session ID."""

    def test_idempotent_same_object(self, tracker):
        """Two calls with the same ID return the exact same SessionState."""
        a = tracker.get_or_create("sess-x")
        b = tracker.get_or_create("sess-x")
        assert a is b

    def test_different_ids_different_sessions(self, tracker):
        """Different IDs produce distinct SessionState objects."""
        a = tracker.get_or_create("alpha")
        b = tracker.get_or_create("beta")
        assert a is not b
        assert a.session_id == "alpha"
        assert b.session_id == "beta"


# ── record_write tracking ──────────────────────────────────────────

class TestRecordWrite:
    """record_write increments writes_this_turn; reset by increment_turn."""

    def test_record_write_increments(self, tracker):
        """Each record_write() increments writes_this_turn by 1."""
        session = tracker.get_or_create("w")
        assert session.writes_this_turn == 0
        session.record_write()
        session.record_write()
        assert session.writes_this_turn == 2

    def test_increment_turn_resets_writes(self, tracker):
        """increment_turn() resets writes_this_turn to 0."""
        session = tracker.get_or_create("w2")
        session.record_write()
        session.record_write()
        session.record_write()
        assert session.writes_this_turn == 3
        session.increment_turn()
        assert session.writes_this_turn == 0


# ── reset removes session ──────────────────────────────────────────

class TestReset:
    """reset() removes the session; next get_or_create returns fresh state."""

    def test_reset_removes_session(self, tracker):
        """After reset, get_or_create returns a new SessionState."""
        original = tracker.get_or_create("doomed")
        original.increment_turn()
        original.increment_turn()
        assert original.turn_count == 2

        tracker.reset("doomed")

        fresh = tracker.get_or_create("doomed")
        assert fresh is not original
        assert fresh.turn_count == 0

    def test_reset_nonexistent_is_noop(self, tracker):
        """Resetting a session that does not exist does not raise."""
        tracker.reset("never-existed")  # should not raise

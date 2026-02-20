"""
Tests for memctl.chat — interactive memory-backed chat REPL.

Tests use injectable callables (mock recaller, mock loop_runner) for
deterministic unit testing without monkeypatching or real LLM calls.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.chat import (
    ChatSession,
    format_session_context,
    chat_turn,
    _has_uncertainty,
    _store_answer,
    _UNCERTAINTY_MARKERS,
)
from memctl.loop import LoopResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_recaller(db_path, query, limit=50):
    """Mock recaller that returns one deterministic item."""
    return [
        {
            "id": "MEM-test001",
            "title": "Test Item",
            "content": f"Context about {query}",
            "tier": "stm",
            "tags": ["test"],
            "confidence": 0.9,
        }
    ]


def _mock_empty_recaller(db_path, query, limit=50):
    """Mock recaller that returns nothing."""
    return []


def _mock_loop_runner(**kwargs):
    """Mock loop runner that echoes the query as the answer."""
    query = kwargs.get("query", "")
    context = kwargs.get("initial_context", "")
    return LoopResult(
        answer=f"Answer to: {query}",
        iterations=1,
        converged=True,
        stop_reason="llm_stop",
    )


def _mock_loop_runner_with_context(**kwargs):
    """Mock loop runner that includes context in the answer (for session testing)."""
    query = kwargs.get("query", "")
    context = kwargs.get("initial_context", "")
    has_session = "Session History" in context
    return LoopResult(
        answer=f"Answer to: {query} (session={'yes' if has_session else 'no'})",
        iterations=1,
        converged=True,
        stop_reason="llm_stop",
    )


def _mock_loop_runner_uncertain(**kwargs):
    """Mock loop runner that returns an uncertain answer."""
    return LoopResult(
        answer="I cannot find sufficient information to answer this question.",
        iterations=1,
        converged=True,
        stop_reason="llm_stop",
    )


# ---------------------------------------------------------------------------
# TestChatSession
# ---------------------------------------------------------------------------


class TestChatSession:
    """Tests for ChatSession and format_session_context."""

    def test_empty_session(self):
        """Empty session produces empty context string."""
        session = ChatSession()
        result = format_session_context(session)
        assert result == ""

    def test_session_formats_turns(self):
        """Session with turns formats Q/A pairs correctly."""
        session = ChatSession(
            history=[
                ("What is auth?", "Auth is authentication."),
                ("How does JWT work?", "JWT uses signed tokens."),
            ],
            turn_count=2,
        )
        result = format_session_context(session)
        assert "## Session History" in result
        assert "Q: What is auth?" in result
        assert "A: Auth is authentication." in result
        assert "Q: How does JWT work?" in result
        assert "A: JWT uses signed tokens." in result

    def test_history_window_limits_turns(self):
        """history_turns limits the number of included turns."""
        session = ChatSession(
            history=[
                (f"Q{i}", f"A{i}") for i in range(10)
            ],
            turn_count=10,
        )
        result = format_session_context(session, history_turns=3)
        # Only last 3 turns should be present
        assert "Q: Q7" in result
        assert "Q: Q8" in result
        assert "Q: Q9" in result
        assert "Q: Q0" not in result
        assert "Q: Q6" not in result

    def test_session_budget_limits_chars(self):
        """session_budget limits total characters."""
        long_answer = "A" * 2000
        session = ChatSession(
            history=[
                ("Q1", long_answer),
                ("Q2", long_answer),
                ("Q3", "Short answer."),
            ],
            turn_count=3,
        )
        # Budget only fits the last turn
        result = format_session_context(session, history_turns=10, budget_chars=200)
        assert "Q: Q3" in result
        assert "Q: Q2" not in result  # trimmed due to budget


# ---------------------------------------------------------------------------
# TestChatTurn
# ---------------------------------------------------------------------------


class TestChatTurn:
    """Tests for chat_turn — pure function with injectable dependencies."""

    def test_basic_turn(self, tmp_path):
        """Basic turn returns the LLM answer."""
        db_path = str(tmp_path / "test.db")
        answer = chat_turn(
            "What is auth?",
            "echo test",
            db_path=db_path,
            recaller=_mock_recaller,
            loop_runner=_mock_loop_runner,
        )
        assert answer == "Answer to: What is auth?"

    def test_turn_with_session(self, tmp_path):
        """Turn with session context includes session history."""
        db_path = str(tmp_path / "test.db")
        session = ChatSession(
            history=[("prev question", "prev answer")],
            turn_count=1,
        )
        answer = chat_turn(
            "follow-up?",
            "echo test",
            db_path=db_path,
            session=session,
            recaller=_mock_recaller,
            loop_runner=_mock_loop_runner_with_context,
        )
        assert "session=yes" in answer

    def test_turn_without_session(self, tmp_path):
        """Turn without session has no session block in context."""
        db_path = str(tmp_path / "test.db")
        answer = chat_turn(
            "standalone question",
            "echo test",
            db_path=db_path,
            session=None,
            recaller=_mock_recaller,
            loop_runner=_mock_loop_runner_with_context,
        )
        assert "session=no" in answer

    def test_turn_with_empty_recall(self, tmp_path):
        """Turn with empty recall still invokes the LLM."""
        db_path = str(tmp_path / "test.db")
        answer = chat_turn(
            "obscure topic",
            "echo test",
            db_path=db_path,
            recaller=_mock_empty_recaller,
            loop_runner=_mock_loop_runner,
        )
        assert answer == "Answer to: obscure topic"

    def test_protocol_passthrough(self, tmp_path):
        """Protocol parameter is forwarded to the loop runner."""
        received_kwargs = {}

        def capturing_runner(**kwargs):
            received_kwargs.update(kwargs)
            return LoopResult(answer="ok", iterations=1, converged=True, stop_reason="llm_stop")

        db_path = str(tmp_path / "test.db")
        chat_turn(
            "test",
            "echo test",
            db_path=db_path,
            protocol="json",
            recaller=_mock_empty_recaller,
            loop_runner=capturing_runner,
        )
        assert received_kwargs["protocol"] == "json"


# ---------------------------------------------------------------------------
# TestStoreAnswer
# ---------------------------------------------------------------------------


class TestStoreAnswer:
    """Tests for _store_answer — policy-governed storage."""

    def test_answer_stored_with_tags(self, tmp_path):
        """Answer is stored as STM with correct tags and title."""
        from memctl.store import MemoryStore
        db_path = str(tmp_path / "store.db")
        store = MemoryStore(db_path=db_path)
        _store_answer(store, "What is auth?", "Auth is authentication.", ["test"])
        items = store.search_fulltext("authentication", limit=10)
        store.close()
        assert len(items) >= 1
        item = items[0]
        assert item.tier == "stm"
        assert "chat" in item.tags
        assert "test" in item.tags
        assert item.title == "What is auth?"

    def test_policy_rejection_no_crash(self, tmp_path):
        """Policy-rejected content does not crash."""
        from memctl.store import MemoryStore
        db_path = str(tmp_path / "store.db")
        store = MemoryStore(db_path=db_path)
        # Content with a secret-like pattern
        _store_answer(
            store,
            "API key",
            "sk-1234567890abcdef1234567890abcdef1234567890abcdef",
            ["test"],
        )
        # Should not crash — just silently skip
        items = store.search_fulltext("sk-1234", limit=10)
        store.close()
        assert len(items) == 0  # rejected by policy

    def test_tags_merge(self, tmp_path):
        """User tags are merged with 'chat' tag."""
        from memctl.store import MemoryStore
        db_path = str(tmp_path / "store.db")
        store = MemoryStore(db_path=db_path)
        _store_answer(store, "test", "Some answer.", ["custom", "tags"])
        items = store.search_fulltext("answer", limit=10)
        store.close()
        assert len(items) >= 1
        all_tags = items[0].tags
        assert "chat" in all_tags
        assert "custom" in all_tags
        assert "tags" in all_tags

    def test_provenance(self, tmp_path):
        """Provenance is set to source_kind=chat."""
        from memctl.store import MemoryStore
        db_path = str(tmp_path / "store.db")
        store = MemoryStore(db_path=db_path)
        _store_answer(store, "test q", "test answer", [])
        items = store.search_fulltext("test answer", limit=10)
        store.close()
        assert len(items) >= 1
        prov = items[0].provenance
        assert prov.source_kind == "chat"
        assert prov.source_id == "memctl-chat"


# ---------------------------------------------------------------------------
# TestUncertaintyHint
# ---------------------------------------------------------------------------


class TestUncertaintyHint:
    """Tests for _has_uncertainty detection."""

    def test_uncertain_answer(self):
        """Answers with uncertainty markers are detected."""
        assert _has_uncertainty("I cannot find sufficient information.")
        assert _has_uncertainty("There is not enough context to answer.")
        assert _has_uncertainty("The answer is UNCLEAR based on available data.")

    def test_clean_answer(self):
        """Normal answers are not flagged."""
        assert not _has_uncertainty("The auth system uses JWT tokens.")
        assert not _has_uncertainty("Based on the code, the function returns True.")

    def test_case_insensitive(self):
        """Detection is case-insensitive."""
        assert _has_uncertainty("INSUFFICIENT data to answer")
        assert _has_uncertainty("No Information available")

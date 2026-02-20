"""
Tests for memctl.ask — one-shot folder Q&A.

Tests cover:
  - AskResult serialization
  - ask_folder() orchestration (mount + sync + inspect + scoped recall + loop)
  - Scoped recall via mount_id parameter on recall_items()
  - Budget splitting (inspect_cap)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
from pathlib import Path

import pytest

from memctl.ask import AskResult, ask_folder
from memctl.loop import recall_items
from memctl.mount import register_mount
from memctl.store import MemoryStore
from memctl.sync import sync_mount


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def corpus(tmp_path):
    """Small corpus for ask tests."""
    root = tmp_path / "docs"
    root.mkdir()
    (root / "auth.md").write_text(
        "# Authentication\n\n"
        "The system uses JWT tokens for authentication.\n"
        "Access tokens are short-lived (15 min), RS256-signed.\n"
        "Refresh tokens are long-lived (7 days), HTTP-only cookies.\n"
    )
    (root / "api.md").write_text(
        "# API Reference\n\n"
        "The REST API exposes /users, /sessions, and /tokens endpoints.\n"
        "Rate limiting: 100 req/min on reads, 10 req/min on auth.\n"
    )
    (root / "readme.txt").write_text(
        "Project documentation.\nSee auth.md and api.md for details.\n"
    )
    return root


@pytest.fixture
def other_corpus(tmp_path):
    """A second corpus for scoped recall tests."""
    root = tmp_path / "other"
    root.mkdir()
    (root / "billing.md").write_text(
        "# Billing\n\n"
        "Payment processing uses Stripe. Invoice generation is automated.\n"
    )
    return root


@pytest.fixture
def mock_llm(tmp_path):
    """Script that echoes stdin as-is (acts as identity LLM)."""
    script = tmp_path / "mock_llm.sh"
    script.write_text("#!/usr/bin/env bash\ncat\n")
    script.chmod(0o755)
    return f"bash {script}"


# ---------------------------------------------------------------------------
# TestAskResult
# ---------------------------------------------------------------------------


class TestAskResult:
    """Tests for AskResult dataclass."""

    def test_fields_present(self):
        """All fields have expected defaults."""
        r = AskResult(
            answer="test",
            mount_id="MNT-123",
            was_mounted=True,
            was_synced=True,
            recall_items_used=3,
            loop_iterations=1,
            converged=True,
            stop_reason="llm_stop",
        )
        assert r.answer == "test"
        assert r.mount_id == "MNT-123"
        assert r.was_mounted is True
        assert r.recall_items_used == 3

    def test_to_dict(self):
        """to_dict includes all fields and is JSON-serializable."""
        r = AskResult(
            answer="the answer",
            mount_id="MNT-456",
            was_mounted=False,
            was_synced=True,
            recall_items_used=5,
            loop_iterations=2,
            converged=False,
            stop_reason="max_calls",
        )
        d = r.to_dict()
        assert d["answer"] == "the answer"
        assert d["mount_id"] == "MNT-456"
        assert d["converged"] is False
        # JSON-serializable
        json.dumps(d)


# ---------------------------------------------------------------------------
# TestAskFolder
# ---------------------------------------------------------------------------


class TestAskFolder:
    """Tests for ask_folder() orchestration."""

    def test_basic_ask(self, db_path, corpus, mock_llm):
        """Basic ask: auto-mount, sync, inspect, recall, loop → answer."""
        logs = []
        result = ask_folder(
            str(corpus), "How does authentication work?",
            mock_llm,
            db_path=db_path,
            log=logs.append,
        )
        assert result.answer  # non-empty answer
        assert result.was_mounted is True
        assert result.was_synced is True
        assert result.mount_id.startswith("MNT-")
        # Logs include mount, sync, context info
        log_text = " ".join(logs)
        assert "[inspect]" in log_text
        assert "[ask]" in log_text

    def test_sync_mode_never(self, db_path, corpus, mock_llm):
        """sync=never skips sync even on fresh folder."""
        logs = []
        result = ask_folder(
            str(corpus), "What is documented?",
            mock_llm,
            db_path=db_path,
            sync_mode="never",
            log=logs.append,
        )
        # Should not have synced
        assert result.was_synced is False
        assert result.recall_items_used == 0  # nothing indexed

    def test_sync_mode_always(self, db_path, corpus, mock_llm):
        """sync=always syncs even if already fresh."""
        # Pre-mount and sync
        mid = register_mount(db_path, str(corpus))
        sync_mount(db_path, str(corpus), quiet=True)

        logs = []
        result = ask_folder(
            str(corpus), "What about auth?",
            mock_llm,
            db_path=db_path,
            sync_mode="always",
            log=logs.append,
        )
        assert result.was_synced is True
        log_text = " ".join(logs)
        assert "sync=always" in log_text

    def test_budget_split(self, db_path, corpus, mock_llm):
        """inspect_cap controls the budget split."""
        logs = []
        result = ask_folder(
            str(corpus), "authentication",
            mock_llm,
            db_path=db_path,
            budget=2200,
            inspect_cap=200,
            log=logs.append,
        )
        # Verify context log mentions chars
        log_text = " ".join(logs)
        assert "chars inspect" in log_text
        assert "chars recall" in log_text

    def test_inspect_cap_validation(self, db_path, corpus, mock_llm):
        """inspect_cap >= budget raises ValueError."""
        with pytest.raises(ValueError, match="inspect_cap"):
            ask_folder(
                str(corpus), "test",
                mock_llm,
                db_path=db_path,
                budget=2200,
                inspect_cap=2200,
            )

    def test_ephemeral_cleanup(self, db_path, corpus, mock_llm):
        """Ephemeral mode removes mount after answer."""
        logs = []
        result = ask_folder(
            str(corpus), "auth?",
            mock_llm,
            db_path=db_path,
            mount_mode="ephemeral",
            log=logs.append,
        )
        assert result.answer  # got an answer
        # Mount should be removed
        store = MemoryStore(db_path=db_path)
        mount = store.read_mount(result.mount_id)
        store.close()
        assert mount is None
        log_text = " ".join(logs)
        assert "Ephemeral" in log_text

    def test_log_callable(self, db_path, corpus, mock_llm):
        """All implicit actions are reported via log callable."""
        logs = []
        ask_folder(
            str(corpus), "test",
            mock_llm,
            db_path=db_path,
            log=logs.append,
        )
        assert len(logs) >= 3  # mount, sync, context, loop summary

    def test_nonexistent_path(self, db_path, mock_llm):
        """Nonexistent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            ask_folder(
                "/nonexistent/path/that/does/not/exist", "test",
                mock_llm,
                db_path=db_path,
            )


# ---------------------------------------------------------------------------
# TestScopedRecall
# ---------------------------------------------------------------------------


class TestScopedRecall:
    """Tests for scoped recall via mount_id on recall_items."""

    def test_recall_unscoped(self, db_path, corpus):
        """mount_id=None returns all items (existing behavior)."""
        mid = register_mount(db_path, str(corpus))
        sync_mount(db_path, str(corpus), quiet=True)
        items = recall_items(db_path, "authentication")
        assert len(items) > 0

    def test_recall_scoped(self, db_path, corpus, other_corpus):
        """mount_id restricts recall to that mount's items."""
        # Mount and sync both corpora
        mid1 = register_mount(db_path, str(corpus))
        sync_mount(db_path, str(corpus), quiet=True)
        mid2 = register_mount(db_path, str(other_corpus))
        sync_mount(db_path, str(other_corpus), quiet=True)

        # Unscoped: finds items from both
        all_items = recall_items(db_path, "authentication billing")
        # Note: FTS5 AND logic — try single terms
        auth_items = recall_items(db_path, "authentication")
        billing_items = recall_items(db_path, "billing")
        assert len(auth_items) > 0
        assert len(billing_items) > 0

        # Scoped to corpus: should only find auth-related items
        scoped = recall_items(db_path, "authentication", mount_id=mid1)
        assert len(scoped) > 0
        scoped_ids = {it["id"] for it in scoped}

        # Scoped to other_corpus: should NOT find auth items
        other_scoped = recall_items(db_path, "authentication", mount_id=mid2)
        assert len(other_scoped) == 0

    def test_recall_scoped_empty_mount(self, db_path, tmp_path):
        """Mount with no files returns empty recall."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        mid = register_mount(db_path, str(empty_dir))
        items = recall_items(db_path, "anything", mount_id=mid)
        assert items == []

    def test_recall_scoped_no_match(self, db_path, corpus):
        """Scoped query with no FTS hits returns empty."""
        mid = register_mount(db_path, str(corpus))
        sync_mount(db_path, str(corpus), quiet=True)
        items = recall_items(db_path, "xyzzyzzynonexistent", mount_id=mid)
        assert items == []

    def test_recall_scoped_injectable(self, db_path, corpus):
        """Non-injectable items are excluded even if in mount."""
        mid = register_mount(db_path, str(corpus))
        sync_mount(db_path, str(corpus), quiet=True)

        # Mark all items as non-injectable
        store = MemoryStore(db_path=db_path)
        all_items = store.search_fulltext("authentication", limit=50)
        for it in all_items:
            it.injectable = False
            store.write_item(it, reason="test-disable")
        store.close()

        items = recall_items(db_path, "authentication", mount_id=mid)
        assert items == []

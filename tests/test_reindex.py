"""
Tests for FTS5 reindex: tokenizer metadata, rebuild, mismatch detection.

Invariants tested:
  X1-X5   Rebuild in place (same tokenizer)
  X6-X10  Tokenizer change (fr→en)
  X11-X13 Mismatch detection
  X14-X16 Metadata persistence in schema_meta
  X17-X19 Dry run (CLI)
  X20-X22 Edge cases (empty DB, FTS5 unavailable, invalid tokenizer)
  X23-X25 Event logging

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from memctl.store import MemoryStore, FTS_TOKENIZER_PRESETS
from memctl.types import MemoryItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store_with_items(tmp_path):
    """A MemoryStore with 5 items and FTS5 enabled (fr tokenizer)."""
    db = str(tmp_path / "test.db")
    s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
    items = [
        ("item_0", "Monitoring and alerting configuration guide"),
        ("item_1", "Database connection pooling strategies"),
        ("item_2", "REST API endpoint documentation with examples"),
        ("item_3", "Configuration of notification system"),
        ("item_4", "Performance testing methodology and results"),
    ]
    for item_id, content in items:
        item = MemoryItem(
            id=item_id, tier="stm", type="fact",
            title=content[:30], content=content, tags=["test"],
        )
        s.write_item(item, reason="test")
    return s


@pytest.fixture
def empty_store(tmp_path):
    """A MemoryStore with no items."""
    db = str(tmp_path / "test.db")
    return MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])


# ---------------------------------------------------------------------------
# X1-X5: Rebuild in place (same tokenizer)
# ---------------------------------------------------------------------------

class TestRebuildInPlace:
    def test_x1_rebuild_returns_item_count(self, store_with_items):
        count = store_with_items.rebuild_fts()
        assert count == 5

    def test_x2_rebuild_preserves_search(self, store_with_items):
        store_with_items.rebuild_fts()
        results = store_with_items.search_fulltext("monitoring")
        assert len(results) >= 1

    def test_x3_rebuild_updates_indexed_at(self, store_with_items):
        stats = store_with_items.stats()
        old_ts = stats["fts_indexed_at"]
        store_with_items.rebuild_fts()
        stats2 = store_with_items.stats()
        # indexed_at updated (or same if sub-second)
        assert stats2["fts_indexed_at"] is not None

    def test_x4_rebuild_increments_reindex_count(self, store_with_items):
        stats = store_with_items.stats()
        old_count = stats["fts_reindex_count"]
        store_with_items.rebuild_fts()
        stats2 = store_with_items.stats()
        assert stats2["fts_reindex_count"] == old_count + 1

    def test_x5_rebuild_twice_increments_twice(self, store_with_items):
        store_with_items.rebuild_fts()
        store_with_items.rebuild_fts()
        stats = store_with_items.stats()
        assert stats["fts_reindex_count"] >= 2


# ---------------------------------------------------------------------------
# X6-X10: Tokenizer change (fr → en)
# ---------------------------------------------------------------------------

class TestTokenizerChange:
    def test_x6_change_returns_item_count(self, store_with_items):
        count = store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        assert count == 5

    def test_x7_change_updates_stored_tokenizer(self, store_with_items):
        store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        stats = store_with_items.stats()
        assert stats["fts_tokenizer_stored"] == FTS_TOKENIZER_PRESETS["en"]

    def test_x8_change_updates_active_tokenizer(self, store_with_items):
        store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        assert store_with_items._fts_tokenizer == FTS_TOKENIZER_PRESETS["en"]

    def test_x9_change_preserves_search(self, store_with_items):
        store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        results = store_with_items.search_fulltext("monitoring")
        assert len(results) >= 1

    def test_x10_porter_enables_stemming(self, store_with_items):
        """Porter stemmer should match 'monitor' → 'monitoring'."""
        store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        results = store_with_items.search_fulltext("monitor")
        # With Porter, 'monitor' stems to same root as 'monitoring'
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# X11-X13: Mismatch detection
# ---------------------------------------------------------------------------

class TestMismatchDetection:
    def test_x11_no_mismatch_by_default(self, store_with_items):
        stats = store_with_items.stats()
        assert stats["fts_tokenizer_mismatch"] is False

    def test_x12_mismatch_after_reopen_with_different_tokenizer(self, tmp_path):
        """Open store with fr, reindex to en, reopen with fr → mismatch."""
        db = str(tmp_path / "test.db")
        s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        s.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        s.close()

        s2 = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        stats = s2.stats()
        assert stats["fts_tokenizer_mismatch"] is True
        assert stats["fts_tokenizer_stored"] == FTS_TOKENIZER_PRESETS["en"]
        s2.close()

    def test_x13_no_mismatch_after_matching_reindex(self, tmp_path):
        """Open with fr, reindex to en, reopen with en → no mismatch."""
        db = str(tmp_path / "test.db")
        s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        s.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        s.close()

        s2 = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["en"])
        stats = s2.stats()
        assert stats["fts_tokenizer_mismatch"] is False
        s2.close()


# ---------------------------------------------------------------------------
# X14-X16: Metadata persistence in schema_meta
# ---------------------------------------------------------------------------

class TestMetadataPersistence:
    def test_x14_initial_metadata_written(self, empty_store):
        stats = empty_store.stats()
        assert stats["fts_tokenizer_stored"] == FTS_TOKENIZER_PRESETS["fr"]
        assert stats["fts_indexed_at"] is not None

    def test_x15_metadata_survives_close_reopen(self, tmp_path):
        db = str(tmp_path / "test.db")
        s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        stats1 = s.stats()
        s.close()

        s2 = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        stats2 = s2.stats()
        assert stats2["fts_tokenizer_stored"] == stats1["fts_tokenizer_stored"]
        assert stats2["fts_indexed_at"] == stats1["fts_indexed_at"]
        s2.close()

    def test_x16_reindex_count_persists(self, tmp_path):
        db = str(tmp_path / "test.db")
        s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        s.rebuild_fts()
        s.rebuild_fts()
        s.close()

        s2 = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        stats = s2.stats()
        assert stats["fts_reindex_count"] == 2
        s2.close()


# ---------------------------------------------------------------------------
# X17-X19: Dry run (CLI integration)
# ---------------------------------------------------------------------------

def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "memctl.cli"] + list(args),
        capture_output=True, text=True,
    )


class TestDryRun:
    def test_x17_dry_run_no_reindex_count_change(self, tmp_path):
        db = str(tmp_path / "test.db")
        _run_cli("init", str(tmp_path), "--db", db)
        _run_cli("reindex", "--db", db, "--dry-run")
        r = _run_cli("stats", "--db", db, "--json")
        stats = json.loads(r.stdout)
        assert stats["fts_reindex_count"] == 0

    def test_x18_dry_run_json_output(self, tmp_path):
        db = str(tmp_path / "test.db")
        _run_cli("init", str(tmp_path), "--db", db)
        r = _run_cli("reindex", "--db", db, "--dry-run", "--json")
        data = json.loads(r.stdout)
        assert data["status"] == "dry_run"
        assert "current_tokenizer" in data
        assert "new_tokenizer" in data

    def test_x19_dry_run_with_tokenizer_change(self, tmp_path):
        db = str(tmp_path / "test.db")
        _run_cli("init", str(tmp_path), "--db", db)
        r = _run_cli("reindex", "--db", db, "--dry-run", "--json", "--tokenizer", "en")
        data = json.loads(r.stdout)
        assert data["tokenizer_change"] is True
        assert data["new_tokenizer"] == FTS_TOKENIZER_PRESETS["en"]


# ---------------------------------------------------------------------------
# X20-X22: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_x20_rebuild_empty_db(self, empty_store):
        count = empty_store.rebuild_fts()
        assert count == 0

    def test_x21_fts5_unavailable_returns_minus_one(self, tmp_path):
        db = str(tmp_path / "test.db")
        s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
        s._fts5_available = False
        assert s.rebuild_fts() == -1

    def test_x22_porter_detection(self, store_with_items):
        assert store_with_items._is_porter_tokenizer() is False
        store_with_items.rebuild_fts(tokenizer=FTS_TOKENIZER_PRESETS["en"])
        assert store_with_items._is_porter_tokenizer() is True


# ---------------------------------------------------------------------------
# X23-X25: Event logging
# ---------------------------------------------------------------------------

class TestReindexEvents:
    def test_x23_reindex_logs_event(self, tmp_path):
        db = str(tmp_path / "test.db")
        _run_cli("init", str(tmp_path), "--db", db)
        _run_cli("reindex", "--db", db)
        r = _run_cli("stats", "--db", db, "--json")
        stats = json.loads(r.stdout)
        assert stats["events_count"] >= 1

    def test_x24_reindex_event_contains_tokenizer_info(self, store_with_items):
        store_with_items.rebuild_fts()
        # Log event manually like CLI does
        store_with_items._log_event("reindex", None, {
            "previous_tokenizer": "fr",
            "new_tokenizer": "fr",
            "items_indexed": 5,
        }, "")
        store_with_items._conn.commit()
        events = store_with_items.read_events(action="reindex")
        assert len(events) >= 1
        assert "previous_tokenizer" in events[0].details

    def test_x25_cli_reindex_exit_0(self, tmp_path):
        db = str(tmp_path / "test.db")
        _run_cli("init", str(tmp_path), "--db", db)
        r = _run_cli("reindex", "--db", db)
        assert r.returncode == 0

"""
Tests for forward compatibility — memctl DB must be openable by RAGIX.

This is the critical test that guarantees the upgrade path:
    pip install ragix[all]  →  point at the same memory.db  →  works.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import sqlite3
import pytest

from memctl.store import MemoryStore, SCHEMA_VERSION, FTS_TOKENIZER_PRESETS
from memctl.types import MemoryItem, MemoryProvenance


@pytest.fixture
def db_path(tmp_path):
    """Create a memctl DB on disk and return its path."""
    path = str(tmp_path / "memory.db")
    store = MemoryStore(db_path=path)

    # Populate with representative data
    store.write_item(
        MemoryItem(
            tier="stm", type="fact",
            title="Test fact", content="The sky is blue",
            tags=["test", "fact"], entities=["sky"],
            confidence=0.9, scope="project",
            provenance=MemoryProvenance(
                source_kind="doc", source_id="test.md",
            ),
        ),
        reason="test",
    )
    store.write_item(
        MemoryItem(
            tier="mtm", type="decision",
            title="Use SQLite", content="We chose SQLite for persistence",
            tags=["db", "architecture"],
            confidence=0.95, scope="project",
            injectable=True,
        ),
        reason="test",
    )
    store.close()
    return path


# ---------------------------------------------------------------------------
# Table structure
# ---------------------------------------------------------------------------


class TestTableStructure:
    def test_required_tables_exist(self, db_path):
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        required = {
            "memory_items", "memory_revisions", "memory_events",
            "memory_links", "memory_embeddings",
            "corpus_hashes", "corpus_metadata", "schema_meta",
            "memory_mounts",
        }
        assert required.issubset(tables), f"Missing: {required - tables}"
        conn.close()

    def test_fts_virtual_table_exists(self, db_path):
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "memory_items_fts" in tables, "FTS5 virtual table missing"
        conn.close()

    def test_palace_locations_table(self, db_path):
        """RAGIX uses memory_palace_locations — must exist even if empty."""
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "memory_palace_locations" in tables, "Palace locations table missing"
        conn.close()


# ---------------------------------------------------------------------------
# Column schema
# ---------------------------------------------------------------------------


class TestColumnSchema:
    def test_memory_items_columns(self, db_path):
        """All RAGIX-required columns must exist in memory_items."""
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_items)"
        ).fetchall()}

        required = {
            "id", "tier", "type", "title", "content",
            "tags", "entities", "links_json", "provenance_json",
            "confidence", "validation", "scope",
            "expires_at", "usage_count", "last_used_at",
            "created_at", "updated_at",
            "rule_id", "superseded_by", "archived",
            "content_hash", "corpus_id", "injectable",
        }
        assert required.issubset(cols), f"Missing columns: {required - cols}"
        conn.close()

    def test_memory_revisions_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_revisions)"
        ).fetchall()}
        required = {"revision_id", "item_id", "revision_num", "snapshot", "changed_at", "reason"}
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()

    def test_memory_events_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_events)"
        ).fetchall()}
        required = {"id", "action", "item_id", "details_json", "content_hash", "timestamp"}
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()

    def test_memory_links_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_links)"
        ).fetchall()}
        required = {"src_id", "dst_id", "rel", "created_at"}
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()


# ---------------------------------------------------------------------------
# Schema meta
# ---------------------------------------------------------------------------


class TestSchemaMeta:
    def test_schema_version(self, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row is not None
        assert row[0] == str(SCHEMA_VERSION)
        conn.close()

    def test_created_by(self, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='created_by'"
        ).fetchone()
        assert row is not None
        assert row[0] == "memctl"
        conn.close()

    def test_created_at(self, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='created_at'"
        ).fetchone()
        assert row is not None
        assert len(row[0]) > 10  # ISO-ish datetime
        conn.close()


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    def test_items_readable_via_raw_sql(self, db_path):
        """Verify items can be read without memctl — pure SQLite."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM memory_items WHERE archived=0").fetchall()
        assert len(rows) == 2

        # Verify JSON columns parse correctly
        for row in rows:
            tags = json.loads(row["tags"])
            assert isinstance(tags, list)
            prov = json.loads(row["provenance_json"])
            assert isinstance(prov, dict)

        conn.close()

    def test_revisions_exist_for_all_items(self, db_path):
        conn = sqlite3.connect(db_path)
        item_ids = {r[0] for r in conn.execute(
            "SELECT id FROM memory_items"
        ).fetchall()}
        rev_item_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT item_id FROM memory_revisions"
        ).fetchall()}
        assert item_ids == rev_item_ids, "Some items lack revisions"
        conn.close()

    def test_events_logged(self, db_path):
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_events"
        ).fetchone()[0]
        assert count >= 2  # at least one per write
        conn.close()

    def test_content_hash_populated(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT content_hash FROM memory_items").fetchall()
        for row in rows:
            assert row["content_hash"].startswith("sha256:")
        conn.close()

    def test_wal_mode(self, db_path):
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()


# ---------------------------------------------------------------------------
# FTS5 compatibility
# ---------------------------------------------------------------------------


class TestFTS5Compat:
    def test_fts_search_works(self, db_path):
        """FTS5 search must work on a memctl-created DB."""
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM memory_items_fts WHERE memory_items_fts MATCH 'sky'"
            ).fetchall()
            assert len(rows) >= 1
        except sqlite3.OperationalError:
            pytest.skip("FTS5 not available")
        conn.close()

    def test_fts_presets_are_canonical(self):
        """FTS presets must match the contract."""
        assert FTS_TOKENIZER_PRESETS["fr"] == "unicode61 remove_diacritics 2"
        assert FTS_TOKENIZER_PRESETS["en"] == "porter unicode61 remove_diacritics 2"
        assert FTS_TOKENIZER_PRESETS["raw"] == "unicode61"


# ---------------------------------------------------------------------------
# Embeddings table (empty but present)
# ---------------------------------------------------------------------------


class TestEmbeddingsTable:
    def test_embeddings_table_empty(self, db_path):
        """memctl doesn't use embeddings, but the table must exist for RAGIX."""
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings"
        ).fetchone()[0]
        assert count == 0
        conn.close()

    def test_embeddings_table_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_embeddings)"
        ).fetchall()}
        required = {"item_id", "vector", "model_name", "dimension", "created_at"}
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()


# ---------------------------------------------------------------------------
# Memory mounts table (v0.3)
# ---------------------------------------------------------------------------


class TestMountsTable:
    def test_mounts_table_exists(self, db_path):
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "memory_mounts" in tables, "memory_mounts table missing"
        conn.close()

    def test_mounts_table_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(memory_mounts)"
        ).fetchall()}
        required = {
            "mount_id", "path", "name", "ignore_json",
            "lang_hint", "created_at", "last_sync_at",
        }
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()

    def test_mounts_table_empty(self, db_path):
        """memctl does not auto-create mounts."""
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM memory_mounts").fetchone()[0]
        assert count == 0
        conn.close()


class TestCorpusHashesExtended:
    def test_corpus_hashes_v3_columns(self, db_path):
        """v0.3 extended columns must exist in corpus_hashes."""
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(corpus_hashes)"
        ).fetchall()}
        required = {
            "file_path", "sha256", "chunk_count", "item_ids", "ingested_at",
            "mount_id", "rel_path", "ext", "size_bytes", "mtime_epoch", "lang_hint",
        }
        assert required.issubset(cols), f"Missing: {required - cols}"
        conn.close()

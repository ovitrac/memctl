"""
Tests for memctl.store — MemoryStore CRUD, FTS5, dedup, schema.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import sqlite3
import pytest

from memctl.store import MemoryStore, SCHEMA_VERSION, FTS_TOKENIZER_PRESETS
from memctl.types import (
    CorpusMetadata,
    MemoryItem,
    MemoryLink,
    MemoryProvenance,
    content_hash,
)


@pytest.fixture
def store(tmp_path):
    """Create an in-memory store for testing."""
    s = MemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def disk_store(tmp_path):
    """Create a disk-backed store for testing."""
    db_path = str(tmp_path / "test.db")
    s = MemoryStore(db_path=db_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema_version(self, store):
        row = store._conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert row is not None
        assert row["value"] == str(SCHEMA_VERSION)

    def test_schema_created_by(self, store):
        row = store._conn.execute(
            "SELECT value FROM schema_meta WHERE key='created_by'"
        ).fetchone()
        assert row["value"] == "memctl"

    def test_all_tables_exist(self, store):
        tables = {
            r["name"] for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "memory_items", "memory_revisions", "memory_events",
            "memory_links", "memory_embeddings", "memory_mounts",
            "corpus_hashes", "corpus_metadata", "schema_meta",
        }
        assert required.issubset(tables), f"Missing: {required - tables}"

    def test_fts_virtual_table(self, store):
        if not store._fts5_available:
            pytest.skip("FTS5 not available")
        tables = {
            r["name"] for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memory_items_fts" in tables

    def test_memory_items_columns(self, store):
        cols = {
            r["name"] for r in store._conn.execute(
                "PRAGMA table_info(memory_items)"
            ).fetchall()
        }
        required = {
            "id", "tier", "type", "title", "content", "tags", "entities",
            "confidence", "validation", "scope", "corpus_id", "injectable",
            "superseded_by", "archived", "rule_id", "content_hash",
            "links_json", "provenance_json", "expires_at", "usage_count",
            "last_used_at", "created_at", "updated_at",
        }
        assert required.issubset(cols), f"Missing: {required - cols}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_write_and_read(self, store):
        item = MemoryItem(title="Test", content="Hello world")
        store.write_item(item, reason="test")

        retrieved = store.read_item(item.id)
        assert retrieved is not None
        assert retrieved.title == "Test"
        assert retrieved.content == "Hello world"

    def test_write_creates_revision(self, store):
        item = MemoryItem(title="Rev test", content="v1")
        store.write_item(item, reason="create")

        revisions = store._conn.execute(
            "SELECT * FROM memory_revisions WHERE item_id=?", (item.id,)
        ).fetchall()
        assert len(revisions) == 1
        assert revisions[0]["reason"] == "create"

    def test_write_creates_audit_event(self, store):
        item = MemoryItem(title="Evt test", content="v1")
        store.write_item(item, reason="test")

        events = store._conn.execute(
            "SELECT * FROM memory_events WHERE item_id=?", (item.id,)
        ).fetchall()
        assert len(events) >= 1
        assert events[0]["action"] == "write"

    def test_read_nonexistent(self, store):
        result = store.read_item("MEM-does-not-exist")
        assert result is None

    def test_read_items_batch(self, store):
        ids = []
        for i in range(3):
            item = MemoryItem(title=f"Item {i}", content=f"Content {i}")
            store.write_item(item, reason="test")
            ids.append(item.id)

        items = store.read_items(ids)
        assert len(items) == 3

    def test_read_items_empty(self, store):
        items = store.read_items([])
        assert items == []

    def test_update_item(self, store):
        item = MemoryItem(title="Original", content="v1")
        store.write_item(item, reason="create")

        store.update_item(item.id, {"title": "Updated", "tier": "mtm"})
        updated = store.read_item(item.id)
        assert updated.title == "Updated"
        assert updated.tier == "mtm"

    def test_usage_count_increments(self, store):
        item = MemoryItem(title="Usage test", content="x")
        store.write_item(item, reason="test")

        # read_item touches usage
        store.read_item(item.id)
        store.read_item(item.id)

        row = store._conn.execute(
            "SELECT usage_count FROM memory_items WHERE id=?", (item.id,)
        ).fetchone()
        assert row["usage_count"] >= 2


# ---------------------------------------------------------------------------
# List and count
# ---------------------------------------------------------------------------


class TestListCount:
    def test_list_items(self, store):
        for i in range(5):
            store.write_item(
                MemoryItem(title=f"Item {i}", content=f"C{i}", tier="stm"),
                reason="test",
            )
        items = store.list_items(tier="stm", limit=10)
        assert len(items) == 5

    def test_list_items_exclude_archived(self, store):
        item = MemoryItem(title="Archived", content="gone")
        store.write_item(item, reason="test")
        store.update_item(item.id, {"archived": True})

        items = store.list_items(exclude_archived=True, limit=10)
        assert len(items) == 0

        items = store.list_items(exclude_archived=False, limit=10)
        assert len(items) == 1

    def test_count_items(self, store):
        for tier in ["stm", "stm", "mtm"]:
            store.write_item(
                MemoryItem(title="T", content="C", tier=tier),
                reason="test",
            )
        assert store.count_items(tier="stm") == 2
        assert store.count_items(tier="mtm") == 1
        assert store.count_items() == 3


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


class TestFTS5:
    def test_fulltext_search(self, store):
        store.write_item(
            MemoryItem(title="Python guide", content="Python is a programming language"),
            reason="test",
        )
        store.write_item(
            MemoryItem(title="Rust guide", content="Rust is a systems language"),
            reason="test",
        )

        results = store.search_fulltext("Python", limit=10)
        assert len(results) >= 1
        assert any("Python" in it.title for it in results)

    def test_fulltext_empty_query(self, store):
        store.write_item(
            MemoryItem(title="Anything", content="Something"),
            reason="test",
        )
        results = store.search_fulltext("", limit=10)
        assert len(results) >= 1  # empty query returns all

    def test_fulltext_no_results(self, store):
        store.write_item(
            MemoryItem(title="Cat", content="Meow"),
            reason="test",
        )
        results = store.search_fulltext("xyznonexistent", limit=10)
        assert len(results) == 0

    def test_search_by_tags(self, store):
        store.write_item(
            MemoryItem(title="Tagged", content="Content", tags=["python", "api"]),
            reason="test",
        )
        store.write_item(
            MemoryItem(title="Other", content="Content", tags=["rust"]),
            reason="test",
        )
        results = store.search_by_tags(["python"], limit=10)
        assert len(results) >= 1
        assert all("python" in [t.lower() for t in it.tags] for it in results)

    def test_fulltext_with_tier_filter(self, store):
        store.write_item(
            MemoryItem(title="STM item", content="Important fact", tier="stm"),
            reason="test",
        )
        store.write_item(
            MemoryItem(title="LTM item", content="Important fact too", tier="ltm"),
            reason="test",
        )
        results = store.search_fulltext("Important", tier="ltm", limit=10)
        assert all(it.tier == "ltm" for it in results)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


class TestLinks:
    def test_write_and_read_link(self, store):
        link = MemoryLink(src_id="A", dst_id="B", rel="supports")
        store.write_link(link)

        links = store.read_links("A")
        assert len(links) >= 1
        assert any(l.dst_id == "B" and l.rel == "supports" for l in links)


# ---------------------------------------------------------------------------
# Corpus hashes
# ---------------------------------------------------------------------------


class TestCorpusHashes:
    def test_write_and_read_corpus_hash(self, store):
        store.write_corpus_hash("/path/to/file.md", "abc123", 3, ["id1", "id2"])
        result = store.read_corpus_hash("/path/to/file.md")
        assert result is not None
        assert result["sha256"] == "abc123"
        assert result["chunk_count"] == 3
        assert result["item_ids"] == ["id1", "id2"]

    def test_read_nonexistent_corpus_hash(self, store):
        result = store.read_corpus_hash("/nonexistent")
        assert result is None

    def test_idempotent_write(self, store):
        store.write_corpus_hash("/file", "hash1", 1, [])
        store.write_corpus_hash("/file", "hash2", 2, [])  # replace
        result = store.read_corpus_hash("/file")
        assert result["sha256"] == "hash2"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_empty_store_stats(self, store):
        s = store.stats()
        assert s["total_items"] == 0
        assert s["events_count"] == 0
        assert s["fts5_available"] in (True, False)

    def test_stats_with_items(self, store):
        store.write_item(MemoryItem(title="T", content="C", tier="stm"), reason="test")
        store.write_item(MemoryItem(title="T2", content="C2", tier="ltm"), reason="test")
        s = store.stats()
        assert s["total_items"] == 2
        assert s["by_tier"]["stm"] == 1
        assert s["by_tier"]["ltm"] == 1


# ---------------------------------------------------------------------------
# Export/Import
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_export_jsonl(self, store):
        store.write_item(MemoryItem(title="Export me", content="Data"), reason="test")
        jsonl = store.export_jsonl()
        lines = jsonl.strip().split("\n")
        assert len(lines) >= 1
        d = json.loads(lines[0])
        assert "title" in d

    def test_import_jsonl(self, store):
        item = MemoryItem(title="Imported", content="From file")
        jsonl = json.dumps(item.to_dict(), ensure_ascii=False)
        count = store.import_jsonl(jsonl)
        assert count == 1
        retrieved = store.read_item(item.id)
        assert retrieved is not None
        assert retrieved.title == "Imported"

    def test_round_trip_export_import(self, store):
        for i in range(3):
            store.write_item(
                MemoryItem(title=f"RT {i}", content=f"Content {i}"),
                reason="test",
            )
        jsonl = store.export_jsonl()

        # Import into fresh store
        store2 = MemoryStore(":memory:")
        count = store2.import_jsonl(jsonl)
        assert count == 3
        store2.close()


# ---------------------------------------------------------------------------
# FTS presets
# ---------------------------------------------------------------------------


class TestFTSPresets:
    def test_preset_fr(self):
        assert FTS_TOKENIZER_PRESETS["fr"] == "unicode61 remove_diacritics 2"

    def test_preset_en(self):
        assert FTS_TOKENIZER_PRESETS["en"] == "porter unicode61 remove_diacritics 2"

    def test_preset_raw(self):
        assert FTS_TOKENIZER_PRESETS["raw"] == "unicode61"


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------


class TestDiskPersistence:
    def test_persist_and_reopen(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        store = MemoryStore(db_path=db_path)
        item = MemoryItem(title="Persist", content="Survives restart")
        store.write_item(item, reason="test")
        item_id = item.id
        store.close()

        store2 = MemoryStore(db_path=db_path)
        retrieved = store2.read_item(item_id)
        assert retrieved is not None
        assert retrieved.title == "Persist"
        store2.close()

    def test_wal_mode(self, tmp_path):
        db_path = str(tmp_path / "wal.db")
        store = MemoryStore(db_path=db_path)
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        store.close()


# ---------------------------------------------------------------------------
# v0.3: Mount CRUD + extended corpus_hashes
# ---------------------------------------------------------------------------


class TestMountCRUD:
    def test_write_mount(self, store):
        mid = store.write_mount("/tmp/test_docs")
        assert mid.startswith("MNT-")

    def test_write_mount_idempotent(self, store):
        mid1 = store.write_mount("/tmp/test_docs")
        mid2 = store.write_mount("/tmp/test_docs")
        assert mid1 == mid2

    def test_write_mount_with_options(self, store):
        mid = store.write_mount(
            "/tmp/docs",
            name="docs",
            ignore_patterns=["*.log"],
            lang_hint="fr",
        )
        m = store.read_mount(mid)
        assert m is not None
        assert m["name"] == "docs"
        assert m["ignore_patterns"] == ["*.log"]
        assert m["lang_hint"] == "fr"

    def test_read_mount_by_path(self, store):
        store.write_mount("/tmp/docs", name="docs")
        m = store.read_mount("/tmp/docs")
        assert m is not None
        assert m["name"] == "docs"

    def test_read_mount_not_found(self, store):
        assert store.read_mount("nonexistent") is None

    def test_list_mounts_empty(self, store):
        assert store.list_mounts() == []

    def test_list_mounts(self, store):
        store.write_mount("/tmp/a", name="a")
        store.write_mount("/tmp/b", name="b")
        mounts = store.list_mounts()
        assert len(mounts) == 2
        names = {m["name"] for m in mounts}
        assert names == {"a", "b"}

    def test_remove_mount(self, store):
        mid = store.write_mount("/tmp/docs", name="docs")
        assert store.remove_mount(mid) is True
        assert store.list_mounts() == []

    def test_remove_mount_by_name(self, store):
        store.write_mount("/tmp/docs", name="docs")
        assert store.remove_mount("docs") is True
        assert store.list_mounts() == []

    def test_remove_mount_not_found(self, store):
        assert store.remove_mount("nonexistent") is False

    def test_update_mount_sync_time(self, store):
        mid = store.write_mount("/tmp/docs")
        m1 = store.read_mount(mid)
        assert m1["last_sync_at"] is None
        store.update_mount_sync_time(mid)
        m2 = store.read_mount(mid)
        assert m2["last_sync_at"] is not None


class TestCorpusHashExtended:
    def test_write_with_mount_metadata(self, store):
        store.write_corpus_hash(
            "/tmp/docs/file.md", "abc123",
            chunk_count=3, item_ids=["MEM-1", "MEM-2"],
            mount_id="MNT-test", rel_path="file.md",
            ext=".md", size_bytes=1024, mtime_epoch=1700000000,
            lang_hint="en",
        )
        h = store.read_corpus_hash("/tmp/docs/file.md")
        assert h is not None
        assert h["mount_id"] == "MNT-test"
        assert h["rel_path"] == "file.md"
        assert h["ext"] == ".md"
        assert h["size_bytes"] == 1024
        assert h["mtime_epoch"] == 1700000000
        assert h["lang_hint"] == "en"

    def test_backward_compat_no_mount_fields(self, store):
        """Existing callers (no mount metadata) still work."""
        store.write_corpus_hash("/tmp/file.md", "abc123", 2, ["MEM-1"])
        h = store.read_corpus_hash("/tmp/file.md")
        assert h["sha256"] == "abc123"
        assert h["mount_id"] is None
        assert h["rel_path"] is None

    def test_list_corpus_files(self, store):
        store.write_corpus_hash(
            "/a.md", "h1", mount_id="MNT-1", rel_path="a.md", ext=".md",
        )
        store.write_corpus_hash(
            "/b.py", "h2", mount_id="MNT-1", rel_path="b.py", ext=".py",
        )
        store.write_corpus_hash(
            "/c.md", "h3", mount_id="MNT-2", rel_path="c.md", ext=".md",
        )
        # All files
        all_files = store.list_corpus_files()
        assert len(all_files) == 3
        # Filtered by mount
        m1_files = store.list_corpus_files(mount_id="MNT-1")
        assert len(m1_files) == 2
        m2_files = store.list_corpus_files(mount_id="MNT-2")
        assert len(m2_files) == 1


class TestMigration:
    def test_v1_to_v2_migration(self, tmp_path):
        """Simulate a v1 database (no mount columns) opening with v2 code."""
        db_path = str(tmp_path / "v1.db")
        # Create a minimal v1 schema manually
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS corpus_hashes (
            file_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            item_ids TEXT NOT NULL DEFAULT '[]',
            ingested_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO schema_meta VALUES ('schema_version', '1')")
        conn.execute("""INSERT INTO corpus_hashes VALUES (
            '/old/file.md', 'oldhash', 2, '["MEM-old"]', '2026-01-01T00:00:00Z'
        )""")
        conn.commit()
        conn.close()

        # Open with v2 code — migration should add new columns
        store = MemoryStore(db_path=db_path)

        # Old data preserved
        h = store.read_corpus_hash("/old/file.md")
        assert h is not None
        assert h["sha256"] == "oldhash"
        assert h["mount_id"] is None  # NULL from migration

        # New columns usable
        store.write_corpus_hash(
            "/new/file.py", "newhash",
            mount_id="MNT-1", rel_path="file.py", ext=".py",
            size_bytes=512, mtime_epoch=1700000000,
        )
        h2 = store.read_corpus_hash("/new/file.py")
        assert h2["mount_id"] == "MNT-1"

        # memory_mounts table exists
        mounts = store.list_mounts()
        assert isinstance(mounts, list)

        store.close()

    def test_migration_idempotent(self, tmp_path):
        """Running migration twice is safe."""
        db_path = str(tmp_path / "idem.db")
        s1 = MemoryStore(db_path=db_path)
        s1.write_mount("/tmp/test")
        s1.close()
        # Re-open (migration runs again)
        s2 = MemoryStore(db_path=db_path)
        mounts = s2.list_mounts()
        assert len(mounts) == 1
        s2.close()


# ---------------------------------------------------------------------------
# last_event (v0.14)
# ---------------------------------------------------------------------------


class TestLastEvent:
    """Tests for store.last_event() public API."""

    def test_last_event_with_filter(self, store):
        """LE1: last_event returns timestamp filtered by action."""
        item = MemoryItem(
            tier="stm", type="note",
            title="test", content="test content",
            provenance=MemoryProvenance(),
        )
        store.write_item(item, reason="test")
        # write_item logs action="write"
        ts = store.last_event(actions=["write"])
        assert ts is not None

    def test_last_event_no_events(self, store):
        """LE2: last_event on empty store returns None."""
        ts = store.last_event()
        assert ts is None

    def test_last_event_unfiltered(self, store):
        """last_event without filter returns most recent of any event."""
        item = MemoryItem(
            tier="stm", type="note",
            title="test", content="test content",
            provenance=MemoryProvenance(),
        )
        store.write_item(item, reason="sync")
        ts = store.last_event()
        assert ts is not None

    def test_last_event_filter_miss(self, store):
        """last_event with non-matching filter returns None."""
        item = MemoryItem(
            tier="stm", type="note",
            title="test", content="test content",
            provenance=MemoryProvenance(),
        )
        store.write_item(item, reason="pull")
        ts = store.last_event(actions=["nonexistent_action"])
        assert ts is None

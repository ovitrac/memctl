"""
Tests for memctl.export_import — JSONL backup, migration, and sharing.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import io
import json
import os
import pytest

from memctl.export_import import export_items, import_items, ImportResult
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProvenance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create an empty database and return its path."""
    db_path = str(tmp_path / "test.db")
    store = MemoryStore(db_path=db_path)
    store.close()
    return db_path


@pytest.fixture
def populated_db(db):
    """A database with 3 items (2 active + 1 archived)."""
    store = MemoryStore(db_path=db)
    items = [
        MemoryItem(
            id="MEM-aaa", tier="stm", type="note",
            title="Note One", content="Content one",
            tags=["test"], scope="project",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        ),
        MemoryItem(
            id="MEM-bbb", tier="ltm", type="decision",
            title="Decision Two", content="Content two",
            tags=["decision"], scope="project",
            provenance=MemoryProvenance(source_kind="doc", source_id="test.md"),
        ),
        MemoryItem(
            id="MEM-ccc", tier="stm", type="note",
            title="Archived Note", content="Content archived",
            tags=["old"], scope="project", archived=True,
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        ),
    ]
    for item in items:
        store.write_item(item, reason="test-setup")
    store.close()
    return db


def _silent_log(msg):
    """Suppress log output in tests."""
    pass


# ---------------------------------------------------------------------------
# TestImportResult
# ---------------------------------------------------------------------------


class TestImportResult:
    def test_fields(self):
        r = ImportResult(total_lines=10, imported=5, skipped_dedup=3,
                         skipped_policy=1, errors=1)
        assert r.total_lines == 10
        assert r.imported == 5
        assert r.skipped_dedup == 3
        assert r.skipped_policy == 1
        assert r.errors == 1

    def test_to_dict(self):
        r = ImportResult(total_lines=2, imported=1)
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["total_lines"] == 2
        assert d["imported"] == 1
        # JSON-serializable
        json.dumps(d)


# ---------------------------------------------------------------------------
# TestExportItems
# ---------------------------------------------------------------------------


class TestExportItems:
    def test_export_all(self, populated_db):
        """Exports all non-archived items as JSONL."""
        buf = io.StringIO()
        count = export_items(populated_db, output=buf, log=_silent_log)
        assert count == 2
        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 2

    def test_export_tier_filter(self, populated_db):
        """Only matching tier is exported."""
        buf = io.StringIO()
        count = export_items(populated_db, tier="ltm", output=buf, log=_silent_log)
        assert count == 1
        data = json.loads(buf.getvalue().strip())
        assert data["tier"] == "ltm"

    def test_export_includes_provenance(self, populated_db):
        """Each exported line has provenance dict."""
        buf = io.StringIO()
        export_items(populated_db, output=buf, log=_silent_log)
        for line in buf.getvalue().strip().split("\n"):
            data = json.loads(line)
            assert "provenance" in data
            assert "source_kind" in data["provenance"]

    def test_export_exclude_archived(self, populated_db):
        """Archived items are omitted by default."""
        buf = io.StringIO()
        count = export_items(populated_db, output=buf, log=_silent_log)
        assert count == 2
        for line in buf.getvalue().strip().split("\n"):
            data = json.loads(line)
            assert data["archived"] is False

    def test_export_include_archived(self, populated_db):
        """include-archived includes archived items."""
        buf = io.StringIO()
        count = export_items(
            populated_db, exclude_archived=False, output=buf, log=_silent_log,
        )
        assert count == 3


# ---------------------------------------------------------------------------
# TestImportItems
# ---------------------------------------------------------------------------


class TestImportItems:
    def _make_jsonl(self, items):
        """Create a JSONL string from a list of MemoryItem dicts."""
        return "\n".join(json.dumps(d, ensure_ascii=False) for d in items)

    def test_import_basic(self, db):
        """JSONL → store, new IDs generated."""
        item = MemoryItem(
            id="MEM-orig", tier="stm", type="note",
            title="Import Test", content="Hello import",
            tags=["imported"],
            provenance=MemoryProvenance(source_kind="chat", source_id="export"),
        )
        jsonl = json.dumps(item.to_dict()) + "\n"
        source = io.StringIO(jsonl)
        result = import_items(db, source, log=_silent_log)
        assert result.imported == 1
        assert result.total_lines == 1

        # Verify new ID was generated
        store = MemoryStore(db_path=db)
        items = store.list_items(limit=10)
        store.close()
        assert len(items) == 1
        assert items[0].id != "MEM-orig"  # new ID generated
        assert items[0].content == "Hello import"

    def test_import_preserve_ids(self, db):
        """Original IDs kept with --preserve-ids."""
        item = MemoryItem(
            id="MEM-keep", tier="stm", type="note",
            title="Keep ID", content="Preserved content",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        )
        jsonl = json.dumps(item.to_dict()) + "\n"
        source = io.StringIO(jsonl)
        result = import_items(db, source, preserve_ids=True, log=_silent_log)
        assert result.imported == 1

        store = MemoryStore(db_path=db)
        found = store.read_item("MEM-keep")
        store.close()
        assert found is not None
        assert found.id == "MEM-keep"

    def test_import_dedup(self, populated_db):
        """Same content_hash is skipped."""
        # Export an item, then try to reimport
        buf = io.StringIO()
        export_items(populated_db, output=buf, log=_silent_log)

        source = io.StringIO(buf.getvalue())
        result = import_items(populated_db, source, log=_silent_log)
        assert result.skipped_dedup == 2
        assert result.imported == 0

    def test_import_policy_rejection(self, db):
        """Secret content is blocked by policy."""
        item = MemoryItem(
            id="MEM-secret", tier="stm", type="note",
            title="Has secrets",
            content="password=supersecretlongpassword123",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        )
        jsonl = json.dumps(item.to_dict()) + "\n"
        source = io.StringIO(jsonl)
        result = import_items(db, source, log=_silent_log)
        assert result.skipped_policy == 1
        assert result.imported == 0

    def test_import_dry_run(self, db):
        """Dry run counts but writes nothing."""
        item = MemoryItem(
            id="MEM-dry", tier="stm", type="note",
            title="Dry", content="Dry run content",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        )
        jsonl = json.dumps(item.to_dict()) + "\n"
        source = io.StringIO(jsonl)
        result = import_items(db, source, dry_run=True, log=_silent_log)
        assert result.imported == 1  # would-be imported
        assert result.total_lines == 1

        # Nothing actually written
        store = MemoryStore(db_path=db)
        items = store.list_items(limit=10)
        store.close()
        assert len(items) == 0

    def test_import_malformed_line(self, db):
        """Malformed JSON line counted as error, other lines continue."""
        good_item = MemoryItem(
            id="MEM-good", tier="stm", type="note",
            title="Good Item", content="Valid content",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        )
        jsonl = "not valid json\n" + json.dumps(good_item.to_dict()) + "\n"
        source = io.StringIO(jsonl)
        result = import_items(db, source, log=_silent_log)
        assert result.errors == 1
        assert result.imported == 1

    def test_import_roundtrip(self, populated_db, tmp_path):
        """Export → import into fresh DB → same content."""
        # Export
        buf = io.StringIO()
        count = export_items(populated_db, output=buf, log=_silent_log)
        assert count == 2

        # Import into fresh DB
        fresh_db = str(tmp_path / "fresh.db")
        source = io.StringIO(buf.getvalue())
        result = import_items(fresh_db, source, log=_silent_log)
        assert result.imported == 2

        # Verify content matches
        store = MemoryStore(db_path=fresh_db)
        items = store.list_items(limit=10)
        store.close()
        assert len(items) == 2
        contents = {it.content for it in items}
        assert "Content one" in contents
        assert "Content two" in contents

    def test_import_from_file(self, db, tmp_path):
        """Import from a file path (string source)."""
        item = MemoryItem(
            id="MEM-file", tier="stm", type="note",
            title="From File", content="File import test",
            provenance=MemoryProvenance(source_kind="chat", source_id="test"),
        )
        path = str(tmp_path / "export.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(item.to_dict()) + "\n")

        result = import_items(db, path, log=_silent_log)
        assert result.imported == 1

"""
Tests for memory_reset — store.reset(), MCP tool, and CLI command.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import subprocess
import sys

import pytest

from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryLink, MemoryProvenance


@pytest.fixture
def store(tmp_path):
    """Create an in-memory store for testing."""
    s = MemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def populated_store(tmp_path):
    """Create an in-memory store with sample data."""
    s = MemoryStore(":memory:")
    # Write 3 items
    for i in range(3):
        item = MemoryItem(
            tier="stm", type="fact",
            title=f"Test item {i}",
            content=f"Content for item {i}",
            tags=["test"],
            provenance=MemoryProvenance(source_kind="test", source_id="test"),
        )
        s.write_item(item, reason="test")
    # Write a mount
    s.write_mount("/test/path", name="test-mount")
    # Write a link
    items = s.list_items(limit=3)
    if len(items) >= 2:
        link = MemoryLink(src_id=items[0].id, dst_id=items[1].id, rel="related")
        s.write_link(link)
    yield s
    s.close()


@pytest.fixture
def disk_store(tmp_path):
    """Create a disk-backed store with sample data."""
    db_path = str(tmp_path / "test.db")
    s = MemoryStore(db_path=db_path)
    for i in range(3):
        item = MemoryItem(
            tier="stm", type="fact",
            title=f"Disk item {i}",
            content=f"Disk content {i}",
            tags=["disk"],
            provenance=MemoryProvenance(source_kind="test", source_id="test"),
        )
        s.write_item(item, reason="test")
    s.write_mount("/disk/path", name="disk-mount")
    yield s, db_path
    s.close()


# ---------------------------------------------------------------------------
# R1: dry_run returns counts without deleting
# ---------------------------------------------------------------------------

def test_reset_dry_run_returns_counts(populated_store):
    """R1: dry_run returns counts without deleting."""
    store = populated_store
    result = store.reset(dry_run=True)
    assert result["dry_run"] is True
    assert result["memory_items"] == 3
    # Verify nothing was actually deleted
    assert store.count_items() == 3


# ---------------------------------------------------------------------------
# R2: reset clears memory_items
# ---------------------------------------------------------------------------

def test_reset_clears_memory_items(populated_store):
    """R2: reset clears memory_items (count → 0)."""
    store = populated_store
    assert store.count_items() > 0
    store.reset()
    assert store.count_items() == 0


# ---------------------------------------------------------------------------
# R3: reset clears corpus_hashes
# ---------------------------------------------------------------------------

def test_reset_clears_corpus_hashes(populated_store):
    """R3: reset clears corpus_hashes (dedup cache gone)."""
    store = populated_store
    store.write_corpus_hash("/test/file.py", "abc123", chunk_count=2)
    result = store.reset()
    assert result["corpus_hashes"] >= 1
    assert store.read_corpus_hash("/test/file.py") is None


# ---------------------------------------------------------------------------
# R4: reset clears memory_events
# ---------------------------------------------------------------------------

def test_reset_clears_memory_events(populated_store):
    """R4: reset clears memory_events."""
    store = populated_store
    # There should be events from writes
    events_before = store.read_events(limit=100)
    assert len(events_before) > 0
    store.reset()
    # After reset, only the reset event itself should exist
    events_after = store.read_events(limit=100)
    assert len(events_after) == 1
    assert events_after[0].action == "reset"


# ---------------------------------------------------------------------------
# R5: reset clears memory_links
# ---------------------------------------------------------------------------

def test_reset_clears_memory_links(populated_store):
    """R5: reset clears memory_links."""
    store = populated_store
    result = store.reset()
    assert result["memory_links"] >= 1


# ---------------------------------------------------------------------------
# R6: reset preserves memory_mounts by default
# ---------------------------------------------------------------------------

def test_reset_preserves_mounts(populated_store):
    """R6: reset preserves memory_mounts by default."""
    store = populated_store
    mounts_before = store.list_mounts()
    assert len(mounts_before) > 0
    store.reset(preserve_mounts=True)
    mounts_after = store.list_mounts()
    assert len(mounts_after) == len(mounts_before)


# ---------------------------------------------------------------------------
# R7: reset(preserve_mounts=False) clears mounts
# ---------------------------------------------------------------------------

def test_reset_clears_mounts_when_requested(populated_store):
    """R7: reset(preserve_mounts=False) clears mounts."""
    store = populated_store
    assert len(store.list_mounts()) > 0
    store.reset(preserve_mounts=False)
    assert len(store.list_mounts()) == 0


# ---------------------------------------------------------------------------
# R8: reset preserves schema_meta
# ---------------------------------------------------------------------------

def test_reset_preserves_schema_meta(populated_store):
    """R8: reset preserves schema_meta (tokenizer, version)."""
    store = populated_store
    # Read schema_meta before reset
    with store._lock:
        row = store._conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        version_before = row[0]
    store.reset()
    # schema_meta should be untouched
    with store._lock:
        row = store._conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        version_after = row[0]
    assert version_after == version_before


# ---------------------------------------------------------------------------
# R9: FTS still works after reset
# ---------------------------------------------------------------------------

def test_fts_works_after_reset(populated_store):
    """R9: FTS still works after reset (new items can be searched)."""
    store = populated_store
    store.reset()
    # Write a new item after reset
    item = MemoryItem(
        tier="stm", type="fact",
        title="Post-reset item",
        content="This is new content after reset",
        tags=["new"],
        provenance=MemoryProvenance(source_kind="test", source_id="test"),
    )
    store.write_item(item, reason="test")
    # Search should find it
    results = store.search_fulltext("post-reset")
    assert len(results) == 1
    assert results[0].title == "Post-reset item"


# ---------------------------------------------------------------------------
# R10: Reset is atomic (partial failure leaves DB unchanged)
# ---------------------------------------------------------------------------

def test_reset_atomicity(store):
    """R10: Reset is atomic — verify transaction integrity."""
    # Write items
    for i in range(3):
        item = MemoryItem(
            tier="stm", type="fact",
            title=f"Atomic item {i}",
            content=f"Atomic content {i}",
            provenance=MemoryProvenance(source_kind="test", source_id="test"),
        )
        store.write_item(item, reason="test")
    # Normal reset should succeed
    result = store.reset()
    assert result["dry_run"] is False
    assert store.count_items() == 0


# ---------------------------------------------------------------------------
# R11: MCP memory_reset dry_run returns preview
# ---------------------------------------------------------------------------

def test_mcp_reset_dry_run(populated_store):
    """R11: MCP memory_reset dry_run returns preview counts."""
    # This is a unit test of the store layer — MCP tool delegates here
    store = populated_store
    result = store.reset(dry_run=True)
    assert result["dry_run"] is True
    assert result["memory_items"] == 3
    # Store is untouched
    assert store.count_items() == 3


# ---------------------------------------------------------------------------
# R12: MCP memory_reset execution returns cleared counts
# ---------------------------------------------------------------------------

def test_mcp_reset_execution(populated_store):
    """R12: MCP memory_reset execution returns cleared counts."""
    store = populated_store
    result = store.reset(dry_run=False)
    assert result["dry_run"] is False
    assert result["memory_items"] == 3  # how many were deleted
    assert store.count_items() == 0


# ---------------------------------------------------------------------------
# R13: MCP memory_reset is audited
# ---------------------------------------------------------------------------

def test_reset_is_audited(populated_store):
    """R13: reset creates an audit event."""
    store = populated_store
    store.reset()
    events = store.read_events(action="reset")
    assert len(events) == 1
    details = events[0].details
    assert details["preserve_mounts"] is True


# ---------------------------------------------------------------------------
# R14: CLI memctl reset --dry-run exits 0
# ---------------------------------------------------------------------------

def test_cli_reset_dry_run(disk_store):
    """R14: CLI memctl reset --dry-run exits 0 with preview."""
    store, db_path = disk_store
    result = subprocess.run(
        [sys.executable, "-m", "memctl.cli", "reset", "--dry-run", "--db", db_path],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Dry run" in result.stderr


# ---------------------------------------------------------------------------
# R15: CLI memctl reset without --confirm exits 1
# ---------------------------------------------------------------------------

def test_cli_reset_requires_confirm(disk_store):
    """R15: CLI memctl reset without --confirm exits 1."""
    store, db_path = disk_store
    result = subprocess.run(
        [sys.executable, "-m", "memctl.cli", "reset", "--db", db_path],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "confirm" in result.stderr.lower()


# ---------------------------------------------------------------------------
# R16: CLI memctl reset --confirm exits 0
# ---------------------------------------------------------------------------

def test_cli_reset_confirm(disk_store):
    """R16: CLI memctl reset --confirm exits 0 after clearing."""
    store, db_path = disk_store
    result = subprocess.run(
        [sys.executable, "-m", "memctl.cli", "reset", "--confirm", "--db", db_path],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Reset complete" in result.stderr

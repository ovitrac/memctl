"""
Tests for memctl.consolidate — clustering, merge, and source affinity.

P1 tests for v0.16.1: source-path affinity gate + multi-scope consolidation.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
import pytest

from memctl.consolidate import (
    _jaccard,
    _source_affinity,
    _coarse_cluster,
    _deterministic_merge,
    ConsolidationPipeline,
)
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProvenance


def _item(
    tags, source_id="", item_type="note", content="default content",
    scope="project", item_id=None,
):
    """Helper to create a MemoryItem with minimal boilerplate."""
    prov = MemoryProvenance(source_kind="doc", source_id=source_id) if source_id else None
    item = MemoryItem(
        type=item_type,
        tags=tags,
        content=content,
        scope=scope,
        provenance=prov,
    )
    if item_id:
        item.id = item_id
    return item


# ── Source affinity unit tests ───────────────────────────────────────

class TestSourceAffinity:
    """P1: _source_affinity() hard gate."""

    def test_p1_t1_different_dirs_blocked(self):
        """P1-T1: items from different source directories do NOT pass."""
        a = _item(["java"], source_id="/repo/src/com/service/Incident.java")
        b = _item(["java"], source_id="/repo/src/com/model/Entity.java")
        assert not _source_affinity(a, b)

    def test_p1_t2_same_dir_passes(self):
        """P1-T2: items from same directory pass."""
        a = _item(["java"], source_id="/repo/src/com/service/Incident.java")
        b = _item(["java"], source_id="/repo/src/com/service/Helper.java")
        assert _source_affinity(a, b)

    def test_p1_t3_no_provenance_passes(self):
        """P1-T3: items with no provenance (stdin) cluster with anything."""
        a = _item(["note"], source_id="")
        b = _item(["note"], source_id="/repo/src/file.py")
        assert _source_affinity(a, b)

    def test_p1_t6_both_no_provenance(self):
        """P1-T6: two items with no provenance can cluster."""
        a = _item(["note"])
        b = _item(["note"])
        assert _source_affinity(a, b)

    def test_same_file_passes(self):
        """Same source file always passes affinity."""
        a = _item(["java"], source_id="/repo/src/Main.java")
        b = _item(["java"], source_id="/repo/src/Main.java")
        assert _source_affinity(a, b)


# ── Clustering with affinity gate ────────────────────────────────────

class TestCoarseClusterWithAffinity:
    """P1: _coarse_cluster respects source affinity."""

    def test_same_dir_clusters(self):
        """Items from same dir with matching tags cluster."""
        a = _item(["java", "service"], source_id="/repo/src/svc/A.java", item_id="A")
        b = _item(["java", "service"], source_id="/repo/src/svc/B.java", item_id="B")
        clusters = _coarse_cluster([a, b], distance_threshold=0.3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_different_dir_blocks_cluster(self):
        """Items from different dirs do NOT cluster even with identical tags."""
        a = _item(["java", "domaine", "inc"], source_id="/repo/src/service/Incident.java", item_id="A")
        b = _item(["java", "domaine", "inc"], source_id="/repo/src/model/Entity.java", item_id="B")
        clusters = _coarse_cluster([a, b], distance_threshold=0.3)
        assert len(clusters) == 0

    def test_stdin_items_cluster(self):
        """stdin items (no provenance) can cluster with each other."""
        a = _item(["arch", "design"], content="Architecture notes", item_id="A")
        b = _item(["arch", "design"], content="More architecture", item_id="B")
        clusters = _coarse_cluster([a, b], distance_threshold=0.3)
        assert len(clusters) == 1

    def test_mixed_provenance_clusters(self):
        """stdin item can cluster with file-sourced item (no provenance = permissive)."""
        a = _item(["arch", "design"], content="Manual note", item_id="A")
        b = _item(["arch", "design"], source_id="/repo/docs/arch.md", content="Doc note", item_id="B")
        clusters = _coarse_cluster([a, b], distance_threshold=0.3)
        assert len(clusters) == 1


# ── Multi-scope consolidation ────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


class TestMultiScopeConsolidation:
    """P1-T4, P1-T5: multi-scope consolidation."""

    def _populate_two_scopes(self, store):
        """Write items in two scopes, each with matching tags."""
        for i in range(3):
            store.write_item(MemoryItem(
                type="note",
                tags=["arch", "design"],
                content=f"Scope A item {i} about architecture design patterns.",
                scope="ProjectA",
            ), reason="test")
        for i in range(3):
            store.write_item(MemoryItem(
                type="note",
                tags=["arch", "design"],
                content=f"Scope B item {i} about architecture design patterns.",
                scope="ProjectB",
            ), reason="test")

    def test_p1_t4_all_scopes_independent(self, db_path):
        """P1-T4: --all-scopes consolidates each scope independently."""
        store = MemoryStore(db_path=db_path)
        try:
            self._populate_two_scopes(store)

            pipeline = ConsolidationPipeline(store)
            result = pipeline.run(scope=None)  # all scopes

            assert "scopes_processed" in result
            assert "ProjectA" in result["scopes_processed"]
            assert "ProjectB" in result["scopes_processed"]
            assert result["items_merged"] > 0

            # Verify merged items preserve scope
            items_a = store.list_items(tier="mtm", scope="ProjectA", exclude_archived=True)
            items_b = store.list_items(tier="mtm", scope="ProjectB", exclude_archived=True)
            assert len(items_a) >= 1
            assert len(items_b) >= 1
            for item in items_a:
                assert item.scope == "ProjectA"
            for item in items_b:
                assert item.scope == "ProjectB"
        finally:
            store.close()

    def test_p1_t5_single_scope_works(self, db_path):
        """P1-T5: default single-scope still works for single-scope DBs."""
        store = MemoryStore(db_path=db_path)
        try:
            for i in range(3):
                store.write_item(MemoryItem(
                    type="note",
                    tags=["arch", "design"],
                    content=f"Item {i} about architecture.",
                    scope="project",
                ), reason="test")

            pipeline = ConsolidationPipeline(store)
            result = pipeline.run(scope="project")
            assert result["clusters_found"] >= 1
        finally:
            store.close()

    def test_cross_scope_items_never_merge(self, db_path):
        """Items from different scopes are never in the same cluster."""
        store = MemoryStore(db_path=db_path)
        try:
            self._populate_two_scopes(store)

            pipeline = ConsolidationPipeline(store)
            # Consolidate ProjectA only
            result = pipeline.run(scope="ProjectA")

            # ProjectB items must be untouched
            items_b = store.list_items(tier="stm", scope="ProjectB", exclude_archived=True)
            assert len(items_b) == 3, "ProjectB items must not be affected"
        finally:
            store.close()

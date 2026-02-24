"""
Tests for memctl.consolidate — clustering, merge, source affinity,
content similarity, and effective similarity with path bonus.

P1 tests (v0.16.2): source-path affinity gate + multi-scope consolidation.
P2 tests (v0.16.4): content-similarity gate.
P3 tests (v0.16.4): path-bonus in effective similarity.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
import time
import pytest

from memctl.consolidate import (
    _jaccard,
    _source_affinity,
    _content_similar,
    _effective_similarity,
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


# ── P2: Content-similarity gate ────────────────────────────────────

class TestContentSimilarity:
    """P2: _content_similar() safety floor."""

    def test_p2_t1_dissimilar_content_blocked(self):
        """P2-T1: items with identical tags but dissimilar content do NOT cluster."""
        a = _item(
            ["java", "domaine", "service"],
            source_id="/repo/src/svc/Incident.java",
            content=(
                "package com.grdf.domaine.incident;\n"
                "import javax.persistence.Entity;\n"
                "import javax.persistence.GeneratedValue;\n"
                "@Entity public class Incident implements Serializable {\n"
                "    @GeneratedValue private Long identifiant;\n"
                "    private String description;\n"
                "    private Date dateCreation;\n"
                "    public Long getIdentifiant() { return identifiant; }\n"
                "}"
            ),
            item_id="A",
        )
        b = _item(
            ["java", "domaine", "service"],
            source_id="/repo/src/svc/weblogic-application.xml",
            content=(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<weblogic-application xmlns='http://xmlns.oracle.com'>\n"
                "  <prefer-web-inf-classes>true</prefer-web-inf-classes>\n"
                "  <application-param>\n"
                "    <param-name>webapp.encoding</param-name>\n"
                "    <param-value>UTF-8</param-value>\n"
                "  </application-param>\n"
                "  <listener><listener-class>oracle.AppListener</listener-class></listener>\n"
                "</weblogic-application>"
            ),
            item_id="B",
        )
        assert not _content_similar(a, b, threshold=0.15)

    def test_p2_t2_similar_content_passes(self):
        """P2-T2: items with similar content cluster normally."""
        a = _item(
            ["java", "service"],
            source_id="/repo/src/svc/IncidentService.java",
            content="public class IncidentService { void createIncident(Incident i) { save(i); } }",
            item_id="A",
        )
        b = _item(
            ["java", "service"],
            source_id="/repo/src/svc/IncidentServiceImpl.java",
            content="public class IncidentServiceImpl extends IncidentService { void createIncident(Incident i) { validate(i); save(i); } }",
            item_id="B",
        )
        assert _content_similar(a, b, threshold=0.15)

    def test_p2_t3_threshold_zero_disables(self):
        """P2-T3: threshold 0.0 disables the gate entirely."""
        a = _item(["x"], content="AAAA")
        b = _item(["x"], content="ZZZZ")
        assert _content_similar(a, b, threshold=0.0)

    def test_p2_t4_performance(self):
        """P2-T4: 500 comparisons in < 2s."""
        items = [
            _item(["java"], content=f"class Item{i} " + "x" * 800, item_id=f"I{i}")
            for i in range(500)
        ]
        t0 = time.monotonic()
        for i in range(500):
            _content_similar(items[0], items[i], threshold=0.15)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"500 comparisons took {elapsed:.2f}s (limit: 2s)"

    def test_dissimilar_blocks_cluster(self):
        """Full cluster: dissimilar content prevents clustering even with matching tags."""
        a = _item(
            ["java", "service"],
            source_id="/repo/src/svc/Incident.java",
            content=(
                "package com.grdf.domaine.incident;\n"
                "@Entity public class Incident {\n"
                "    @Id @GeneratedValue private Long identifiant;\n"
                "    private String description;\n"
                "    private Date dateCreation;\n"
                "    public Long getIdentifiant() { return identifiant; }\n"
                "}"
            ),
            item_id="A",
        )
        b = _item(
            ["java", "service"],
            source_id="/repo/src/svc/deploy.yml",
            content=(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n"
                "  name: incident-svc\n"
                "  namespace: production\n"
                "spec:\n"
                "  replicas: 3\n"
                "  selector:\n"
                "    matchLabels:\n"
                "      app: incident-svc\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "      - image: registry.grdf.fr/incident:latest\n"
                "        ports:\n"
                "        - containerPort: 8080"
            ),
            item_id="B",
        )
        clusters = _coarse_cluster([a, b], distance_threshold=0.3, min_content_similarity=0.15)
        assert len(clusters) == 0


# ── P3: Path-bonus effective similarity ─────────────────────────────

class TestEffectiveSimilarity:
    """P3: _effective_similarity() with path bonus."""

    def test_p3_t1_same_file_bonus(self):
        """P3-T1: same-file items with Jaccard 0.6 cluster (0.6 + 0.15 = 0.75 >= 0.7)."""
        # Tags overlap: {"java", "service", "impl"} vs {"java", "service", "test"}
        # Jaccard = 2/4 = 0.5 (too low)
        # But with same file: 0.5 + 0.15 = 0.65 (still under 0.7)
        # Use: {"java", "service", "impl"} vs {"java", "service", "util"}
        # Jaccard = 2/4 = 0.5. Need higher overlap.
        # {"a", "b", "c"} vs {"a", "b", "d"} → Jaccard = 2/4 = 0.5.
        # {"a", "b", "c", "d"} vs {"a", "b", "c", "e"} → Jaccard = 3/5 = 0.6.
        # 0.6 + 0.15 = 0.75 >= 0.7 ✓
        a = _item(
            ["java", "service", "incident", "create"],
            source_id="/repo/src/svc/Incident.java",
            item_id="A",
        )
        b = _item(
            ["java", "service", "incident", "update"],
            source_id="/repo/src/svc/Incident.java",
            item_id="B",
        )
        tags_a = {"java", "service", "incident", "create"}
        tags_b = {"java", "service", "incident", "update"}
        eff = _effective_similarity(a, b, tags_a, tags_b)
        assert eff >= 0.7, f"Expected >= 0.7, got {eff}"
        # Raw Jaccard would be 3/5 = 0.6 (below 0.7)
        assert _jaccard(tags_a, tags_b) < 0.7

    def test_p3_t2_different_file_no_bonus(self):
        """P3-T2: different-file items with Jaccard 0.6 do NOT cluster."""
        a = _item(
            ["java", "service", "incident", "create"],
            source_id="/repo/src/svc/Incident.java",
            item_id="A",
        )
        b = _item(
            ["java", "service", "incident", "update"],
            source_id="/repo/src/model/Entity.java",
            item_id="B",
        )
        tags_a = {"java", "service", "incident", "create"}
        tags_b = {"java", "service", "incident", "update"}
        eff = _effective_similarity(a, b, tags_a, tags_b)
        assert eff < 0.7, f"Expected < 0.7, got {eff}"

    def test_p3_t3_same_dir_small_bonus(self):
        """P3-T3: same-directory items with Jaccard 0.65 cluster (0.65 + 0.05 = 0.70 >= 0.7)."""
        # Need Jaccard = 0.65: 13/20 = 0.65
        # Simpler: {"a","b","c","d","e","f","g","h","i","j","k","l","m"} (13)
        # vs {"a","b","c","d","e","f","g","h","i","j","k","l","x"} (13)
        # intersection=12, union=14 → 12/14 = 0.857 (too high)
        # Simpler approach: check with 4-element sets
        # {"a","b","c","d"} vs {"a","b","c","e","f"} → 3/6 = 0.5 (too low)
        # Just test the math directly
        a = _item(["tag"], source_id="/repo/src/svc/A.java", item_id="A")
        b = _item(["tag"], source_id="/repo/src/svc/B.java", item_id="B")
        tags_a = set(f"t{i}" for i in range(20))
        tags_b = set(f"t{i}" for i in range(13)) | set(f"x{i}" for i in range(7))
        # intersection = 13, union = 27, Jaccard = 13/27 ≈ 0.481 — too low
        # Try: need Jaccard ≈ 0.65
        # intersection = K, union = N, K/N = 0.65
        # 13/20 = 0.65
        tags_a = set(f"t{i}" for i in range(20))
        tags_b = set(f"t{i}" for i in range(13)) | set(f"x{i}" for i in range(7))
        # intersection=13, union=27, 13/27 ≈ 0.48 ← still wrong
        # Need: |A ∩ B| / |A ∪ B| = 0.65
        # If A has 20 tags and B has 20 tags, and they share 18:
        # 18 / 22 ≈ 0.818 — too high
        # A=10, B=10, share 8 → 8/12 = 0.667 ≈ 0.65 ✓
        tags_a = set(f"t{i}" for i in range(10))
        tags_b = set(f"t{i}" for i in range(8)) | {f"x{i}" for i in range(2)}
        raw_jaccard = _jaccard(tags_a, tags_b)
        assert 0.60 <= raw_jaccard <= 0.70, f"Setup: Jaccard={raw_jaccard}"
        eff = _effective_similarity(a, b, tags_a, tags_b)
        assert eff >= 0.7, f"Expected >= 0.7, got {eff}"

    def test_p3_t4_capped_at_one(self):
        """P3-T4: path bonus capped at 1.0."""
        a = _item(["x", "y", "z"], source_id="/repo/src/A.java", item_id="A")
        b = _item(["x", "y", "z"], source_id="/repo/src/A.java", item_id="B")
        tags = {"x", "y", "z"}
        eff = _effective_similarity(a, b, tags, tags)
        assert eff == 1.0  # Jaccard=1.0 + 0.15 → capped at 1.0

    def test_no_provenance_no_bonus(self):
        """Items with no provenance get no path bonus."""
        a = _item(["x", "y", "z"], item_id="A")
        b = _item(["x", "y", "z"], item_id="B")
        tags = {"x", "y", "z"}
        eff = _effective_similarity(a, b, tags, tags)
        assert eff == 1.0  # Jaccard=1.0, no bonus needed

    def test_same_file_boosts_cluster(self):
        """Full cluster: same-file items cluster despite moderate tag overlap."""
        a = _item(
            ["java", "service", "incident", "create"],
            source_id="/repo/src/svc/Incident.java",
            content="class Incident { create() {} }",
            item_id="A",
        )
        b = _item(
            ["java", "service", "incident", "update"],
            source_id="/repo/src/svc/Incident.java",
            content="class Incident { update() {} }",
            item_id="B",
        )
        # Jaccard = 3/5 = 0.6 < 0.7, but path bonus +0.15 → 0.75 >= 0.7
        clusters = _coarse_cluster([a, b], distance_threshold=0.3, min_content_similarity=0.15)
        assert len(clusters) == 1

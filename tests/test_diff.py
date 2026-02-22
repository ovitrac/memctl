"""
Tests for memctl.diff — item comparison and revision diff.

D1-D14: compute_diff and resolve_diff_targets.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.diff import compute_diff, resolve_diff_targets
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProvenance


@pytest.fixture
def store(tmp_path):
    """Temporary MemoryStore for diff tests."""
    db_path = str(tmp_path / "diff_test.db")
    s = MemoryStore(db_path=db_path)
    yield s
    s.close()


def _make_item(**kwargs):
    """Helper to create a MemoryItem with sensible defaults."""
    defaults = {
        "tier": "stm",
        "type": "fact",
        "title": "Test item",
        "content": "Default content.",
        "tags": ["test"],
        "provenance": MemoryProvenance(source_kind="tool", source_id="test"),
    }
    defaults.update(kwargs)
    return MemoryItem(**defaults)


# ---------------------------------------------------------------------------
# D1: Identical items → identical=True, score=1.0
# ---------------------------------------------------------------------------


class TestComputeDiff:
    def test_d1_identical_items(self):
        """D1: Identical items → identical=True, score=1.0."""
        a = _make_item(content="Hello world")
        b = _make_item(content="Hello world")
        result = compute_diff(a, b)
        assert result["identical"] is True
        assert result["similarity_score"] == 1.0
        assert result["content_diff"] == []
        assert result["metadata_changes"] == []

    def test_d2_different_content(self):
        """D2: Different content → content_diff has unified diff lines."""
        a = _make_item(content="Line one\nLine two\n")
        b = _make_item(content="Line one\nLine THREE\n")
        result = compute_diff(a, b)
        assert result["identical"] is False
        assert len(result["content_diff"]) > 0
        # Diff should contain removal and addition
        diff_text = "".join(result["content_diff"])
        assert "-Line two" in diff_text
        assert "+Line THREE" in diff_text

    def test_d3_different_tier(self):
        """D3: Different tier → metadata_changes reports field change."""
        a = _make_item(tier="stm")
        b = _make_item(tier="ltm")
        result = compute_diff(a, b)
        assert result["identical"] is False
        changes = {c["field"]: c for c in result["metadata_changes"]}
        assert "tier" in changes
        assert changes["tier"]["old"] == "stm"
        assert changes["tier"]["new"] == "ltm"

    def test_d4_different_tags(self):
        """D4: Different tags → change reported."""
        a = _make_item(tags=["alpha", "beta"])
        b = _make_item(tags=["alpha", "gamma"])
        result = compute_diff(a, b)
        changes = {c["field"]: c for c in result["metadata_changes"]}
        assert "tags" in changes

    def test_d5_multiple_metadata_diffs(self):
        """D5: Multiple metadata diffs → all reported."""
        a = _make_item(tier="stm", type="fact", confidence=0.5)
        b = _make_item(tier="ltm", type="note", confidence=0.9)
        result = compute_diff(a, b)
        fields = {c["field"] for c in result["metadata_changes"]}
        assert "tier" in fields
        assert "type" in fields
        assert "confidence" in fields

    def test_d6_only_excluded_fields_differ(self):
        """D6: Only excluded fields differ → identical=True."""
        a = _make_item(content="same content")
        b = _make_item(content="same content")
        # Excluded fields: id, created_at, updated_at, usage_count, etc.
        # These are different by construction (different UUIDs) but are excluded
        result = compute_diff(a, b)
        assert result["identical"] is True

    def test_d7_unified_diff_format(self):
        """D7: Unified diff format (has ---, +++, @@ markers)."""
        a = _make_item(content="old line\n")
        b = _make_item(content="new line\n")
        result = compute_diff(a, b, label_a="item_a", label_b="item_b")
        diff_text = "".join(result["content_diff"])
        assert "---" in diff_text
        assert "+++" in diff_text
        assert "@@" in diff_text

    def test_d8_similarity_score_range(self):
        """D8: Similarity score in [0.0, 1.0]."""
        a = _make_item(content="completely different text about apples")
        b = _make_item(content="unrelated words regarding oranges")
        result = compute_diff(a, b)
        assert 0.0 <= result["similarity_score"] <= 1.0


# ---------------------------------------------------------------------------
# D9-D14: resolve_diff_targets
# ---------------------------------------------------------------------------


class TestResolveDiffTargets:
    def test_d9_item_vs_item(self, store):
        """D9: resolve_diff_targets(store, id1, id2) — item vs item."""
        item_a = _make_item(content="first")
        item_b = _make_item(content="second")
        store.write_item(item_a, reason="test")
        store.write_item(item_b, reason="test")

        a, b, la, lb = resolve_diff_targets(store, item_a.id, id2=item_b.id)
        assert a.content == "first"
        assert b.content == "second"
        assert la == item_a.id
        assert lb == item_b.id

    def test_d10_item_vs_revision(self, store):
        """D10: resolve_diff_targets(store, id1, revision=1) — item vs revision."""
        item = _make_item(content="original content")
        store.write_item(item, reason="create")
        # Update to create revision
        store.update_item(item.id, {"content": "updated content"})

        a, b, la, lb = resolve_diff_targets(store, item.id, revision=1)
        assert a.content == "updated content"
        assert b.content == "original content"
        assert "rev1" in lb

    def test_d11_latest_revision(self, store):
        """D11: resolve_diff_targets(store, id1) — penultimate revision after update."""
        item = _make_item(content="v1 content")
        store.write_item(item, reason="create")
        store.update_item(item.id, {"content": "v2 content"})

        a, b, la, lb = resolve_diff_targets(store, item.id)
        assert a.content == "v2 content"
        # Penultimate revision is the initial write (v1)
        assert b.content == "v1 content"
        assert "current" in la

    def test_d12_missing_item(self, store):
        """D12: Missing item → ValueError."""
        with pytest.raises(ValueError, match="not found"):
            resolve_diff_targets(store, "MEM-nonexistent")

    def test_d13_single_revision_identical(self, store):
        """D13: Single revision (no update) → compares to same, reports identical."""
        item = _make_item(content="no changes")
        store.write_item(item, reason="create")
        # Only one revision exists — falls back to revisions[-1] which matches current
        a, b, la, lb = resolve_diff_targets(store, item.id)
        result = compute_diff(a, b)
        assert result["identical"] is True

    def test_d14_missing_revision_number(self, store):
        """D14: Missing revision number → ValueError with available list."""
        item = _make_item(content="v1")
        store.write_item(item, reason="create")
        store.update_item(item.id, {"content": "v2"})

        with pytest.raises(ValueError, match="Available"):
            resolve_diff_targets(store, item.id, revision=999)

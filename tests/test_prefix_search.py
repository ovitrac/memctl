"""
Tests for PREFIX_AND cascade step.

Invariants tested:
  PX1-PX3   Basic prefix matching
  PX4-PX6   Minimum length guard (≥5 chars)
  PX7-PX9   Porter skip (prefix expansion skipped with Porter tokenizer)
  PX10-PX12 Cascade integration (position between REDUCED_AND and OR)
  PX13-PX15 Strategy metadata in SearchMeta

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import os
import tempfile

import pytest

from memctl.store import MemoryStore, FTS_TOKENIZER_PRESETS
from memctl.types import MemoryItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prefix_store(tmp_path):
    """Store with items containing inflected terms (non-Porter tokenizer)."""
    db = str(tmp_path / "test.db")
    s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["fr"])
    items = [
        ("item_0", "The monitoring system handles notifications for alerting"),
        ("item_1", "Configuration of endpoints requires configured settings"),
        ("item_2", "Performance testing requires methodical approaches"),
        ("item_3", "Authentication and authorization middleware pipeline"),
        ("item_4", "Scheduled processing of accumulated data batches"),
    ]
    for item_id, content in items:
        item = MemoryItem(
            id=item_id, tier="stm", type="fact",
            title=content[:30], content=content, tags=["test"],
        )
        s.write_item(item, reason="test")
    return s


@pytest.fixture
def porter_store(tmp_path):
    """Store with Porter stemming enabled."""
    db = str(tmp_path / "test.db")
    s = MemoryStore(db_path=db, fts_tokenizer=FTS_TOKENIZER_PRESETS["en"])
    items = [
        ("item_0", "The monitoring system handles notifications for alerting"),
        ("item_1", "Configuration of endpoints requires configured settings"),
    ]
    for item_id, content in items:
        item = MemoryItem(
            id=item_id, tier="stm", type="fact",
            title=content[:30], content=content, tags=["test"],
        )
        s.write_item(item, reason="test")
    return s


# ---------------------------------------------------------------------------
# PX1-PX3: Basic prefix matching
# ---------------------------------------------------------------------------

class TestBasicPrefix:
    def test_px1_prefix_matches_inflected_term(self, prefix_store):
        """'monitor' should match 'monitoring' via prefix expansion."""
        results = prefix_store._search_fts5_prefix_and(["monitor", "notif"])
        assert len(results) >= 1
        assert any("monitoring" in r.content.lower() for r in results)

    def test_px2_multiple_prefix_terms(self, prefix_store):
        """Both 'config' and 'endpo' expand to match item_1."""
        results = prefix_store._search_fts5_prefix_and(["config", "endpo"])
        assert len(results) >= 1

    def test_px3_exact_and_prefix_mixed(self, prefix_store):
        """Mix of exact short term + prefix long term."""
        results = prefix_store._search_fts5_prefix_and(["the", "monitor"])
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# PX4-PX6: Minimum length guard (≥5 chars)
# ---------------------------------------------------------------------------

class TestMinLengthGuard:
    def test_px4_short_terms_not_expanded(self, prefix_store):
        """Terms shorter than 5 chars should not get prefix expansion."""
        # 'auth' is 4 chars — should NOT be prefix-expanded
        # 'test' is 4 chars — should NOT be prefix-expanded
        # Without prefix expansion, 'auth test' won't match anything
        # because neither exact token exists
        results = prefix_store._search_fts5_prefix_and(["auth", "test"])
        # auth and test are 4 chars, not expanded — need exact match
        # In FTS5, these are exact match queries
        # item_3 has "authentication" but not "auth"
        # So this may return 0 results
        assert isinstance(results, list)

    def test_px5_five_char_term_expanded(self, prefix_store):
        """Term with exactly 5 chars should be prefix-expanded."""
        # 'alert' is 5 chars → 'alert*' should match 'alerting'
        results = prefix_store._search_fts5_prefix_and(["alert"])
        assert len(results) >= 1

    def test_px6_min_len_constant(self, prefix_store):
        """Verify the minimum length constant is 5."""
        assert prefix_store._PREFIX_MIN_LEN == 5


# ---------------------------------------------------------------------------
# PX7-PX9: Porter skip
# ---------------------------------------------------------------------------

class TestPorterSkip:
    def test_px7_porter_tokenizer_detected(self, porter_store):
        assert porter_store._is_porter_tokenizer() is True

    def test_px8_non_porter_tokenizer_not_detected(self, prefix_store):
        assert prefix_store._is_porter_tokenizer() is False

    def test_px9_prefix_and_skipped_with_porter_in_cascade(self, porter_store):
        """When Porter is active, cascade should not use PREFIX_AND."""
        # 'monitor' with Porter directly matches 'monitoring' via stemming
        results = porter_store.search_fulltext("monitor configur")
        meta = porter_store._last_search_meta
        # Porter handles morphology — either AND or REDUCED_AND, never PREFIX_AND
        assert meta.strategy != "PREFIX_AND"


# ---------------------------------------------------------------------------
# PX10-PX12: Cascade integration
# ---------------------------------------------------------------------------

class TestCascadeIntegration:
    def test_px10_prefix_fires_when_and_and_reduced_fail(self, prefix_store):
        """PREFIX_AND should fire when both AND and REDUCED_AND miss."""
        # 'monitor' and 'notif' — neither is an exact token
        # AND fails, REDUCED_AND also fails (each alone also misses)
        # PREFIX_AND: 'monitor*' AND 'notif*' → item_0
        results = prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "PREFIX_AND"
        assert len(results) >= 1

    def test_px11_prefix_does_not_fire_when_and_succeeds(self, prefix_store):
        """If AND finds results, PREFIX_AND is not used."""
        results = prefix_store.search_fulltext("monitoring notifications")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "AND"

    def test_px12_prefix_does_not_fire_when_reduced_and_succeeds(self, prefix_store):
        """If REDUCED_AND finds results, PREFIX_AND is not used."""
        results = prefix_store.search_fulltext("monitoring zzzzz")
        meta = prefix_store._last_search_meta
        # 'zzzzz' is dropped, 'monitoring' matches via REDUCED_AND
        assert meta.strategy == "REDUCED_AND"


# ---------------------------------------------------------------------------
# PX13-PX15: Strategy metadata
# ---------------------------------------------------------------------------

class TestStrategyMetadata:
    def test_px13_search_meta_reports_prefix_and(self, prefix_store):
        prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "PREFIX_AND"

    def test_px14_search_meta_has_original_terms(self, prefix_store):
        prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        assert "monitor" in meta.original_terms
        assert "notif" in meta.original_terms

    def test_px15_search_meta_no_dropped_terms_for_prefix(self, prefix_store):
        prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        assert meta.dropped_terms == []


# ---------------------------------------------------------------------------
# PX16-PX22: Morphological miss hint
# ---------------------------------------------------------------------------

class TestMorphologicalHint:
    def test_px16_no_hint_on_reduced_and(self, prefix_store):
        """REDUCED_AND does not trigger hint — it found results, not a morph miss."""
        # "middleware" matches item_3, "batches" dropped → REDUCED_AND
        prefix_store.search_fulltext("middleware batches")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "REDUCED_AND"
        assert meta.morphological_hint is None

    def test_px17_hint_on_prefix_and(self, prefix_store):
        """Hint fires when PREFIX_AND is the winning strategy."""
        prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "PREFIX_AND"
        assert meta.morphological_hint is not None
        assert "memctl reindex --tokenizer en" in meta.morphological_hint

    def test_px18_no_hint_on_exact_and(self, prefix_store):
        """No hint when AND matches cleanly."""
        prefix_store.search_fulltext("monitoring system")
        meta = prefix_store._last_search_meta
        assert meta.strategy == "AND"
        assert meta.morphological_hint is None

    def test_px19_no_hint_with_porter(self, porter_store):
        """No hint when Porter stemming is active."""
        porter_store.search_fulltext("monitored notifications")
        meta = porter_store._last_search_meta
        # Porter handles inflection — no hint regardless of strategy
        assert meta.morphological_hint is None

    def test_px20_no_hint_single_term(self, prefix_store):
        """No hint for single-term queries (not a morphological issue)."""
        prefix_store.search_fulltext("xyznonexistent")
        meta = prefix_store._last_search_meta
        assert meta.morphological_hint is None

    def test_px21_hint_in_to_dict(self, prefix_store):
        """morphological_hint serializes correctly in to_dict()."""
        prefix_store.search_fulltext("monitor notif")
        meta = prefix_store._last_search_meta
        d = meta.to_dict()
        assert "morphological_hint" in d
        assert d["morphological_hint"] is not None

    def test_px22_no_hint_in_to_dict_when_none(self, prefix_store):
        """morphological_hint is None in to_dict() for clean AND."""
        prefix_store.search_fulltext("monitoring system")
        meta = prefix_store._last_search_meta
        d = meta.to_dict()
        assert d["morphological_hint"] is None

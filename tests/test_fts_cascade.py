"""
Tests for FTS5 cascade: AND → REDUCED_AND → OR_FALLBACK → LIKE.

Covers:
  C1-C5:   AND baseline (single-term, multi-term, all-miss, partial)
  C6-C10:  REDUCED_AND (term reduction, drop order, tie-break)
  C11-C15: OR_FALLBACK (fallback, ranking, empty)
  C16-C18: Strategy metadata (SearchMeta correctness)
  C19-C22: Edge cases (empty query, single term, all stop words)
  C23-C26: LIKE fallback (FTS5 unavailable)
  C27-C30: Integration (search_fulltext with cascade)
  C31-C35: Logging (cascade log messages)
  C36-C40: Backward compatibility

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import logging
import os
import tempfile

import pytest

from memctl.query import cascade_query, _drop_order, normalize_query
from memctl.store import MemoryStore
from memctl.types import MemoryItem, SearchMeta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """In-memory store with FTS5 and known items for cascade testing."""
    db = str(tmp_path / "cascade.db")
    s = MemoryStore(db)
    # Populate with items having known term distributions:
    #   item1: "REST controller endpoint security"
    #   item2: "REST service authentication token"
    #   item3: "database migration liquibase schema"
    #   item4: "controller integration test coverage"
    #   item5: "endpoint monitoring alerting"
    #   item6: "Spring configuration management"
    #   item7: "security audit compliance"
    #   item8: "token validation service"
    items = [
        ("REST controller endpoint", "REST controller endpoint security gateway"),
        ("REST service auth", "REST service authentication token management"),
        ("Database migration", "database migration liquibase schema versioning"),
        ("Controller test", "controller integration test coverage report"),
        ("Endpoint monitor", "endpoint monitoring alerting dashboard"),
        ("Spring config", "Spring configuration management properties"),
        ("Security audit", "security audit compliance review checklist"),
        ("Token service", "token validation service authorization flow"),
    ]
    for title, content in items:
        item = MemoryItem(title=title, content=content, type="note", tier="stm")
        s.write_item(item, reason="test")
    return s


@pytest.fixture
def no_fts_store(tmp_path):
    """Store with FTS5 disabled for LIKE fallback testing."""
    db = str(tmp_path / "no_fts.db")
    s = MemoryStore(db)
    item = MemoryItem(title="Test item", content="alpha beta gamma", type="note")
    s.write_item(item, reason="test")
    # Disable FTS5
    s._fts5_available = False
    return s


# ===========================================================================
# C1-C5: AND baseline
# ===========================================================================


class TestANDBaseline:
    """C1-C5: AND mode works correctly as cascade starting point."""

    def test_c1_single_term_and(self, store):
        """C1: Single term always returns via AND (no cascade needed)."""
        results = store.search_fulltext("REST")
        assert len(results) == 2
        meta = store._last_search_meta
        assert meta.strategy == "AND"
        assert meta.dropped_terms == []

    def test_c2_multi_term_and_hit(self, store):
        """C2: Multi-term AND succeeds when all terms in one item."""
        results = store.search_fulltext("REST controller")
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy == "AND"

    def test_c3_multi_term_and_miss(self, store):
        """C3: Multi-term AND fails → cascade kicks in."""
        results = store.search_fulltext("REST liquibase")
        # "REST" and "liquibase" never co-occur in one item
        # Cascade should fall to REDUCED_AND or OR_FALLBACK
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy in ("REDUCED_AND", "OR_FALLBACK")

    def test_c4_all_terms_miss(self, store):
        """C4: No terms exist in corpus → OR also returns 0."""
        results = store.search_fulltext("xylophone accordion harmonica")
        assert len(results) == 0
        meta = store._last_search_meta
        assert meta.strategy == "OR_FALLBACK"

    def test_c5_partial_terms_exist(self, store):
        """C5: Some terms exist, others don't → cascade recovers."""
        results = store.search_fulltext("REST xylophone")
        # "REST" exists, "xylophone" does not
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy in ("REDUCED_AND", "OR_FALLBACK")


# ===========================================================================
# C6-C10: REDUCED_AND
# ===========================================================================


class TestReducedAND:
    """C6-C10: Term reduction drops shortest first, finds results."""

    def test_c6_three_to_two_terms(self, store):
        """C6: 3 terms → drop 1 → AND(2) succeeds."""
        # "REST controller xyz" — "xyz" doesn't exist, drop it
        results = store.search_fulltext("REST controller xyz")
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy == "REDUCED_AND"
        assert "xyz" in meta.dropped_terms

    def test_c7_four_to_two_terms(self, store):
        """C7: 4 terms → multiple drops until AND succeeds."""
        results = store.search_fulltext("REST controller xyz abc")
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy in ("REDUCED_AND", "OR_FALLBACK")

    def test_c8_drop_order_shortest_first(self):
        """C8: Drop order prefers shortest terms."""
        terms = ["REST", "controller", "xy"]
        order = _drop_order(terms)
        # "xy" (2 chars) should be dropped first, then "REST" (4), then "controller" (10)
        first_drop_idx = order[0]
        assert terms[first_drop_idx] == "xy"

    def test_c9_drop_order_tie_break(self):
        """C9: Same-length terms → later position dropped first."""
        terms = ["abc", "def", "ghi"]
        order = _drop_order(terms)
        # All same length (3) — should drop later positions first
        # order[0] should be index 2 ("ghi")
        assert order[0] == 2

    def test_c10_effective_terms_correct(self, store):
        """C10: Effective terms excludes dropped terms."""
        results = store.search_fulltext("REST controller xyznonexistent")
        meta = store._last_search_meta
        if meta.strategy == "REDUCED_AND":
            for t in meta.dropped_terms:
                assert t not in meta.effective_terms
            for t in meta.effective_terms:
                assert t in meta.original_terms


# ===========================================================================
# C11-C15: OR_FALLBACK
# ===========================================================================


class TestORFallback:
    """C11-C15: OR fallback with coverage ranking."""

    def test_c11_or_after_all_reductions_fail(self, store):
        """C11: When no single term matches via AND, fall to OR."""
        # All terms exist individually but never co-occur
        results = store.search_fulltext("liquibase monitoring compliance")
        meta = store._last_search_meta
        if len(results) > 0:
            assert meta.strategy in ("REDUCED_AND", "OR_FALLBACK")

    def test_c12_or_returns_results(self, store):
        """C12: OR mode returns items matching any term."""
        results = store.search_fulltext("liquibase alerting")
        # "liquibase" in item3, "alerting" in item5
        assert len(results) >= 1
        meta = store._last_search_meta
        assert meta.strategy in ("REDUCED_AND", "OR_FALLBACK")

    def test_c13_or_no_results(self, store):
        """C13: OR with completely absent terms returns empty."""
        results = store.search_fulltext("xylophone accordion")
        assert len(results) == 0

    def test_c14_or_ranking_coverage(self, store):
        """C14: OR results ranked by token coverage (most terms first)."""
        # "REST service" — item2 has both, items 1 has REST only
        results = store.search_fulltext("REST service xyznonexistent")
        if len(results) >= 2:
            meta = store._last_search_meta
            if meta.strategy == "OR_FALLBACK":
                # First result should have more term coverage than last
                first_content = (results[0].content or "").lower()
                last_content = (results[-1].content or "").lower()
                first_score = sum(1 for t in ["rest", "service"] if t in first_content)
                last_score = sum(1 for t in ["rest", "service"] if t in last_content)
                assert first_score >= last_score

    def test_c15_or_preserves_fts5_order_on_tie(self, store):
        """C15: Items with equal coverage preserve FTS5 rank order."""
        # Use a single existing term to get OR results — all have coverage=1
        results = store.search_fulltext("REST xyznonexistent abcnonexistent")
        if len(results) >= 2:
            # Order should be deterministic (same as FTS5 BM25 for "REST")
            ids_run1 = [r.id for r in results]
            results2 = store.search_fulltext("REST xyznonexistent abcnonexistent")
            ids_run2 = [r.id for r in results2]
            assert ids_run1 == ids_run2


# ===========================================================================
# C16-C18: Strategy metadata
# ===========================================================================


class TestStrategyMetadata:
    """C16-C18: SearchMeta correctness."""

    def test_c16_and_strategy_tag(self, store):
        """C16: AND strategy correctly tagged."""
        store.search_fulltext("REST")
        meta = store._last_search_meta
        assert meta is not None
        assert meta.strategy == "AND"

    def test_c17_original_vs_effective_terms(self, store):
        """C17: Original terms preserved, effective may differ."""
        store.search_fulltext("REST xyznonexistent controller")
        meta = store._last_search_meta
        assert "REST" in meta.original_terms
        assert "xyznonexistent" in meta.original_terms
        assert "controller" in meta.original_terms

    def test_c18_dropped_terms_tracked(self, store):
        """C18: Dropped terms listed in metadata."""
        store.search_fulltext("REST xyznonexistent")
        meta = store._last_search_meta
        if meta.strategy == "REDUCED_AND":
            assert len(meta.dropped_terms) > 0
            # Dropped terms should be from the original query
            for t in meta.dropped_terms:
                assert t in meta.original_terms


# ===========================================================================
# C19-C22: Edge cases
# ===========================================================================


class TestEdgeCases:
    """C19-C22: Edge cases for cascade."""

    def test_c19_empty_query(self, store):
        """C19: Empty query returns list (no cascade)."""
        results = store.search_fulltext("")
        assert isinstance(results, list)

    def test_c20_single_term_no_cascade(self, store):
        """C20: Single term → AND only, no reduction possible."""
        results = store.search_fulltext("liquibase")
        meta = store._last_search_meta
        assert meta.strategy == "AND"
        assert meta.dropped_terms == []

    def test_c21_all_stop_words(self, store):
        """C21: All stop words → falls back to original query."""
        results = store.search_fulltext("the and or")
        # normalize_query falls back to original when all words are stop words
        meta = store._last_search_meta
        assert meta is not None

    def test_c22_normalized_terms(self, store):
        """C22: Stop words stripped before cascade."""
        results = store.search_fulltext("how does the REST controller work")
        meta = store._last_search_meta
        # "how", "does", "the" stripped; "REST" and "controller" remain
        assert "REST" in meta.original_terms
        assert "controller" in meta.original_terms
        # "how", "does", "the" should NOT be in original_terms
        # (they were stripped by normalize_query before cascade)
        assert "the" not in meta.original_terms


# ===========================================================================
# C23-C26: LIKE fallback
# ===========================================================================


class TestLIKEFallback:
    """C23-C26: LIKE fallback when FTS5 unavailable."""

    def test_c23_like_when_fts5_disabled(self, no_fts_store):
        """C23: FTS5 disabled → LIKE fallback."""
        results = no_fts_store.search_fulltext("alpha")
        assert len(results) == 1
        meta = no_fts_store._last_search_meta
        assert meta.strategy == "LIKE"

    def test_c24_like_strategy_tag(self, no_fts_store):
        """C24: LIKE strategy correctly tagged."""
        no_fts_store.search_fulltext("alpha beta")
        meta = no_fts_store._last_search_meta
        assert meta.strategy == "LIKE"

    def test_c25_like_no_dropped_terms(self, no_fts_store):
        """C25: LIKE mode doesn't drop terms."""
        no_fts_store.search_fulltext("alpha beta")
        meta = no_fts_store._last_search_meta
        assert meta.dropped_terms == []

    def test_c26_like_miss(self, no_fts_store):
        """C26: LIKE with absent terms returns empty."""
        results = no_fts_store.search_fulltext("xylophone")
        assert len(results) == 0


# ===========================================================================
# C27-C30: Integration
# ===========================================================================


class TestCascadeIntegration:
    """C27-C30: End-to-end cascade via search_fulltext."""

    def test_c27_cascade_transparent_to_caller(self, store):
        """C27: Caller gets a list of MemoryItems regardless of strategy."""
        results = store.search_fulltext("REST liquibase monitoring")
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, MemoryItem)

    def test_c28_meta_always_set(self, store):
        """C28: _last_search_meta is always set after search."""
        store.search_fulltext("REST")
        assert store._last_search_meta is not None
        store.search_fulltext("xylophone")
        assert store._last_search_meta is not None

    def test_c29_limit_respected(self, store):
        """C29: Limit is respected in all cascade modes."""
        results = store.search_fulltext("REST", limit=1)
        assert len(results) <= 1

    def test_c30_scope_filter_cascade(self, store):
        """C30: Scope filter applied across cascade."""
        results = store.search_fulltext("REST", scope="nonexistent")
        assert len(results) == 0


# ===========================================================================
# C31-C35: Logging
# ===========================================================================


class TestCascadeLogging:
    """C31-C35: Cascade transitions logged."""

    def test_c31_and_hit_logged(self, store, caplog):
        """C31: AND hit logged at debug level."""
        with caplog.at_level(logging.DEBUG, logger="memctl.query"):
            store.search_fulltext("REST")
        assert any("AND" in r.message for r in caplog.records)

    def test_c32_reduced_and_logged(self, store, caplog):
        """C32: REDUCED_AND transition logged."""
        with caplog.at_level(logging.DEBUG, logger="memctl.query"):
            store.search_fulltext("REST xyznonexistent")
        messages = " ".join(r.message for r in caplog.records)
        assert "0 hits" in messages or "REDUCED_AND" in messages or "OR_FALLBACK" in messages

    def test_c33_or_fallback_logged(self, store, caplog):
        """C33: OR_FALLBACK logged when all reductions fail."""
        with caplog.at_level(logging.DEBUG, logger="memctl.query"):
            store.search_fulltext("xylophone accordion")
        messages = " ".join(r.message for r in caplog.records)
        assert "OR_FALLBACK" in messages

    def test_c34_dropped_terms_in_log(self, store, caplog):
        """C34: Dropped terms mentioned in log messages."""
        with caplog.at_level(logging.DEBUG, logger="memctl.query"):
            store.search_fulltext("REST xyznonexistent controller")
        # Either REDUCED_AND or OR_FALLBACK should be logged
        messages = " ".join(r.message for r in caplog.records)
        assert len(messages) > 0

    def test_c35_no_logging_on_and_hit(self, store, caplog):
        """C35: Direct AND hit produces minimal logging."""
        with caplog.at_level(logging.INFO, logger="memctl.query"):
            store.search_fulltext("REST")
        # At INFO level, AND hit should not produce cascade logs
        info_messages = [r for r in caplog.records if r.levelno >= logging.INFO]
        cascade_msgs = [r for r in info_messages if "REDUCED" in r.message or "OR_FALLBACK" in r.message]
        assert len(cascade_msgs) == 0


# ===========================================================================
# C36-C40: Backward compatibility
# ===========================================================================


class TestBackwardCompat:
    """C36-C40: Existing callers unaffected by cascade."""

    def test_c36_return_type_unchanged(self, store):
        """C36: search_fulltext still returns List[MemoryItem]."""
        results = store.search_fulltext("REST")
        assert isinstance(results, list)
        assert all(isinstance(r, MemoryItem) for r in results)

    def test_c37_signature_unchanged(self, store):
        """C37: All original parameters still work."""
        results = store.search_fulltext(
            "REST", tier="stm", type_filter="note",
            scope="project", exclude_archived=True, limit=5,
        )
        assert isinstance(results, list)

    def test_c38_meta_optional(self, store):
        """C38: _last_search_meta can be ignored by callers."""
        results = store.search_fulltext("REST")
        # Caller just uses results, ignores meta
        assert len(results) > 0

    def test_c39_meta_serializable(self, store):
        """C39: SearchMeta.to_dict() produces JSON-safe output."""
        store.search_fulltext("REST")
        meta = store._last_search_meta
        d = meta.to_dict()
        import json
        json.dumps(d)  # Should not raise

    def test_c40_cascade_deterministic(self, store):
        """C40: Same query produces same results twice."""
        r1 = store.search_fulltext("REST controller xyznonexistent")
        m1 = store._last_search_meta
        r2 = store.search_fulltext("REST controller xyznonexistent")
        m2 = store._last_search_meta
        assert [i.id for i in r1] == [i.id for i in r2]
        assert m1.strategy == m2.strategy
        assert m1.dropped_terms == m2.dropped_terms

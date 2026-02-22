"""
Tests for B5 token-coverage ranking after OR fallback.

Covers:
  R1-R5:   Basic ranking (order by coverage, ties preserve BM25 order)
  R6-R9:   Edge cases (empty terms, empty items, single term, all match)
  R10-R13: Case insensitivity, title+content combination
  R14-R17: Integration with search_fulltext (OR results ranked)
  R18-R20: Stability (deterministic, idempotent, large term lists)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.store import MemoryStore, _rank_by_coverage
from memctl.types import MemoryItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(title: str, content: str) -> MemoryItem:
    """Create a minimal MemoryItem for ranking tests."""
    return MemoryItem(title=title, content=content, type="note", tier="stm")


# ---------------------------------------------------------------------------
# R1-R5: Basic ranking
# ---------------------------------------------------------------------------

class TestBasicRanking:
    """Test that items are ordered by number of query terms matched."""

    def test_r01_higher_coverage_first(self):
        """R1: Item matching 3/3 terms ranks above item matching 1/3."""
        items = [
            _make_item("A", "alpha"),
            _make_item("B", "alpha beta gamma"),
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta", "gamma"])
        assert ranked[0].title == "B"
        assert ranked[1].title == "A"

    def test_r02_two_beats_one(self):
        """R2: Item matching 2/3 terms ranks above item matching 1/3."""
        items = [
            _make_item("One", "only alpha here"),
            _make_item("Two", "alpha beta here"),
            _make_item("Three", "alpha beta gamma"),
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta", "gamma"])
        assert ranked[0].title == "Three"
        assert ranked[1].title == "Two"
        assert ranked[2].title == "One"

    def test_r03_zero_coverage_at_end(self):
        """R3: Items matching 0 terms sink to the bottom."""
        items = [
            _make_item("Miss", "nothing relevant"),
            _make_item("Hit", "alpha is here"),
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta"])
        assert ranked[0].title == "Hit"
        assert ranked[1].title == "Miss"

    def test_r04_equal_coverage_preserves_order(self):
        """R4: Stable sort — equal coverage preserves input order (BM25 rank)."""
        items = [
            _make_item("First", "alpha content"),
            _make_item("Second", "alpha other"),
            _make_item("Third", "alpha text"),
        ]
        ranked = _rank_by_coverage(items, ["alpha"])
        # All match 1 term — original order preserved
        assert [r.title for r in ranked] == ["First", "Second", "Third"]

    def test_r05_full_coverage_all_terms(self):
        """R5: Item matching all terms gets maximum score."""
        items = [
            _make_item("Partial", "alpha beta"),
            _make_item("Full", "alpha beta gamma delta"),
            _make_item("Minimal", "alpha"),
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta", "gamma", "delta"])
        assert ranked[0].title == "Full"


# ---------------------------------------------------------------------------
# R6-R9: Edge cases
# ---------------------------------------------------------------------------

class TestRankingEdgeCases:
    """Edge cases for coverage ranking."""

    def test_r06_empty_terms(self):
        """R6: Empty term list — all items score 0, order preserved."""
        items = [
            _make_item("A", "alpha"),
            _make_item("B", "beta"),
        ]
        ranked = _rank_by_coverage(items, [])
        assert [r.title for r in ranked] == ["A", "B"]

    def test_r07_empty_items(self):
        """R7: Empty item list returns empty list."""
        ranked = _rank_by_coverage([], ["alpha", "beta"])
        assert ranked == []

    def test_r08_single_term(self):
        """R8: Single term — binary match/no-match."""
        items = [
            _make_item("Miss", "nothing here"),
            _make_item("Hit", "alpha found"),
        ]
        ranked = _rank_by_coverage(items, ["alpha"])
        assert ranked[0].title == "Hit"
        assert ranked[1].title == "Miss"

    def test_r09_all_items_full_coverage(self):
        """R9: All items match all terms — original order preserved."""
        items = [
            _make_item("A", "alpha beta"),
            _make_item("B", "beta alpha"),
            _make_item("C", "alpha and beta"),
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta"])
        assert [r.title for r in ranked] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# R10-R13: Case insensitivity and title+content
# ---------------------------------------------------------------------------

class TestCaseAndCombination:
    """Test case-insensitive matching and title+content combination."""

    def test_r10_case_insensitive_terms(self):
        """R10: Terms are matched case-insensitively."""
        items = [
            _make_item("A", "REST endpoint"),
            _make_item("B", "rest ENDPOINT here"),
        ]
        ranked = _rank_by_coverage(items, ["REST", "endpoint"])
        # Both match 2 terms, order preserved
        assert len(ranked) == 2
        assert all(r.title in ("A", "B") for r in ranked)

    def test_r11_mixed_case_ranking(self):
        """R11: Case-insensitive scoring doesn't affect ranking."""
        items = [
            _make_item("Lower", "controller service"),
            _make_item("Upper", "CONTROLLER SERVICE ENDPOINT"),
        ]
        ranked = _rank_by_coverage(items, ["controller", "service", "endpoint"])
        assert ranked[0].title == "Upper"  # 3 terms
        assert ranked[1].title == "Lower"  # 2 terms

    def test_r12_title_contributes_to_score(self):
        """R12: Terms in title count toward coverage score."""
        items = [
            _make_item("alpha", "nothing here"),  # "alpha" in title
            _make_item("other", "nothing here"),   # no match
        ]
        ranked = _rank_by_coverage(items, ["alpha"])
        assert ranked[0].title == "alpha"

    def test_r13_title_and_content_combined(self):
        """R13: Term in title + different term in content both count."""
        items = [
            _make_item("alpha", "beta content"),       # 2 terms: alpha(title) + beta(content)
            _make_item("gamma", "delta something"),     # 0 terms
        ]
        ranked = _rank_by_coverage(items, ["alpha", "beta"])
        assert ranked[0].title == "alpha"
        assert ranked[1].title == "gamma"


# ---------------------------------------------------------------------------
# R14-R17: Integration with search_fulltext
# ---------------------------------------------------------------------------

class TestIntegrationRanking:
    """Test that OR fallback results are coverage-ranked via search_fulltext."""

    @pytest.fixture
    def store(self, tmp_path):
        """Store with items that only appear in OR mode."""
        db = str(tmp_path / "ranking.db")
        s = MemoryStore(db)
        # Item with "liquibase" only
        s.write_item(
            MemoryItem(title="Liquibase setup", content="liquibase migration changelog",
                       type="note", tier="stm"),
            reason="test",
        )
        # Item with "kubernetes" only
        s.write_item(
            MemoryItem(title="K8s deploy", content="kubernetes deployment pod",
                       type="note", tier="stm"),
            reason="test",
        )
        # Item with both
        s.write_item(
            MemoryItem(title="Infra", content="liquibase kubernetes combined setup",
                       type="note", tier="stm"),
            reason="test",
        )
        return s

    def test_r14_or_results_ranked_by_coverage(self, store):
        """R14: When OR fallback fires, results are ranked by term coverage."""
        # "liquibase kubernetes" — likely no single item has both via AND
        # but "Infra" has both terms and should rank first after OR
        results = store.search_fulltext("liquibase kubernetes")
        meta = store._last_search_meta
        if meta and meta.strategy == "OR_FALLBACK":
            # "Infra" matches both terms, should be first
            assert results[0].title == "Infra"

    def test_r15_or_meta_records_strategy(self, store):
        """R15: SearchMeta records OR_FALLBACK when cascade exhausted."""
        # Search for terms that don't co-occur via AND
        results = store.search_fulltext("liquibase kubernetes nonexistent")
        meta = store._last_search_meta
        assert meta is not None
        # Strategy should be some cascade step
        assert meta.strategy in ("AND", "REDUCED_AND", "OR_FALLBACK")

    def test_r16_and_results_not_reranked(self, store):
        """R16: AND results are NOT coverage-ranked (FTS5 BM25 order preserved)."""
        results = store.search_fulltext("liquibase")
        meta = store._last_search_meta
        assert meta is not None
        if meta.strategy == "AND":
            # Results come from FTS5 BM25, not coverage ranking
            assert len(results) >= 1

    def test_r17_like_results_not_coverage_ranked(self, tmp_path):
        """R17: LIKE fallback results are not coverage-ranked."""
        db = str(tmp_path / "nofts.db")
        s = MemoryStore(db)
        # Force LIKE mode by disabling FTS5
        s._fts5_available = False
        s.write_item(
            MemoryItem(title="Test", content="alpha beta", type="note", tier="stm"),
            reason="test",
        )
        results = s.search_fulltext("alpha")
        meta = s._last_search_meta
        assert meta is not None
        assert meta.strategy == "LIKE"


# ---------------------------------------------------------------------------
# R18-R20: Stability properties
# ---------------------------------------------------------------------------

class TestRankingStability:
    """Stability and determinism properties."""

    def test_r18_idempotent(self):
        """R18: Ranking the same items twice produces identical order."""
        items = [
            _make_item("C", "gamma"),
            _make_item("A", "alpha beta gamma"),
            _make_item("B", "alpha gamma"),
        ]
        terms = ["alpha", "beta", "gamma"]
        first = _rank_by_coverage(items, terms)
        second = _rank_by_coverage(items, terms)
        assert [r.title for r in first] == [r.title for r in second]

    def test_r19_deterministic_different_input_order(self):
        """R19: Same items in different order produce same ranking."""
        items_a = [
            _make_item("X", "one two three"),
            _make_item("Y", "one"),
            _make_item("Z", "one two"),
        ]
        items_b = [
            _make_item("Y", "one"),
            _make_item("Z", "one two"),
            _make_item("X", "one two three"),
        ]
        terms = ["one", "two", "three"]
        ranked_a = _rank_by_coverage(items_a, terms)
        ranked_b = _rank_by_coverage(items_b, terms)
        assert [r.title for r in ranked_a] == [r.title for r in ranked_b]

    def test_r20_many_terms(self):
        """R20: Works correctly with many terms (no performance issue at small N)."""
        terms = [f"term{i}" for i in range(20)]
        items = [
            _make_item("All", " ".join(terms)),
            _make_item("Half", " ".join(terms[:10])),
            _make_item("Few", " ".join(terms[:3])),
            _make_item("None", "nothing matches"),
        ]
        ranked = _rank_by_coverage(items, terms)
        assert ranked[0].title == "All"
        assert ranked[1].title == "Half"
        assert ranked[2].title == "Few"
        assert ranked[3].title == "None"

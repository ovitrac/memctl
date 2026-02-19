"""
Tests for memctl.similarity — stdlib text similarity for loop fixed-point detection.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest
from memctl.similarity import (
    normalize,
    tokenize,
    jaccard,
    sequence_ratio,
    similarity,
    is_fixed_point,
    is_query_cycle,
)


# ── Normalization ──────────────────────────────────────────────────────────


class TestNormalize:
    def test_lowercase(self):
        assert normalize("Hello WORLD") == "hello world"

    def test_strip_punctuation(self):
        assert normalize("hello, world!") == "hello world"

    def test_collapse_whitespace(self):
        assert normalize("hello   world\t\nfoo") == "hello world foo"

    def test_combined(self):
        assert normalize("  Hello,  WORLD!  ") == "hello world"

    def test_empty(self):
        assert normalize("") == ""

    def test_whitespace_only(self):
        assert normalize("   \t\n  ") == ""

    def test_punctuation_only(self):
        assert normalize("...!?") == ""

    def test_accented_preserved(self):
        # normalize does NOT strip accents — that's FTS5's job
        assert normalize("Sécurité") == "sécurité"

    def test_numbers_preserved(self):
        assert normalize("version 3.12") == "version 312"

    def test_hyphens_stripped(self):
        assert normalize("event-sourcing") == "eventsourcing"


# ── Tokenize ───────────────────────────────────────────────────────────────


class TestTokenize:
    def test_basic(self):
        assert tokenize("hello world foo") == ["hello", "world", "foo"]

    def test_empty(self):
        assert tokenize("") == []

    def test_single_token(self):
        assert tokenize("hello") == ["hello"]


# ── Jaccard ────────────────────────────────────────────────────────────────


class TestJaccard:
    def test_identical(self):
        assert jaccard("hello world", "hello world") == 1.0

    def test_identical_reordered(self):
        # Jaccard is order-insensitive
        assert jaccard("hello world", "world hello") == 1.0

    def test_no_overlap(self):
        assert jaccard("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        # {"hello", "world"} ∩ {"hello", "foo"} = {"hello"} → 1/3
        result = jaccard("hello world", "hello foo")
        assert abs(result - 1 / 3) < 1e-9

    def test_both_empty(self):
        assert jaccard("", "") == 1.0

    def test_one_empty(self):
        assert jaccard("hello", "") == 0.0
        assert jaccard("", "hello") == 0.0

    def test_case_insensitive(self):
        assert jaccard("Hello World", "hello world") == 1.0

    def test_punctuation_insensitive(self):
        assert jaccard("hello, world!", "hello world") == 1.0

    def test_duplicate_tokens(self):
        # set-based: duplicates don't matter
        assert jaccard("hello hello hello", "hello") == 1.0

    def test_superset(self):
        # {"a", "b", "c"} ∩ {"a", "b"} = {"a", "b"} → 2/3
        result = jaccard("a b c", "a b")
        assert abs(result - 2 / 3) < 1e-9


# ── SequenceRatio ──────────────────────────────────────────────────────────


class TestSequenceRatio:
    def test_identical(self):
        assert sequence_ratio("hello world", "hello world") == 1.0

    def test_completely_different(self):
        result = sequence_ratio("aaaa", "zzzz")
        assert result < 0.1

    def test_slight_edit(self):
        result = sequence_ratio(
            "the authentication flow uses JWT",
            "the authentication flow uses jwt tokens",
        )
        assert result > 0.7

    def test_both_empty(self):
        assert sequence_ratio("", "") == 1.0

    def test_one_empty(self):
        assert sequence_ratio("hello", "") == 0.0
        assert sequence_ratio("", "hello") == 0.0

    def test_order_matters(self):
        # Unlike Jaccard, SequenceMatcher is order-sensitive
        ratio_same = sequence_ratio("a b c d e", "a b c d e")
        ratio_reversed = sequence_ratio("a b c d e", "e d c b a")
        assert ratio_same > ratio_reversed

    def test_case_insensitive(self):
        assert sequence_ratio("Hello", "hello") == 1.0


# ── Combined similarity ───────────────────────────────────────────────────


class TestSimilarity:
    def test_identical(self):
        assert similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        result = similarity("aaaa bbbb cccc", "xxxx yyyy zzzz")
        assert result < 0.1

    def test_both_empty(self):
        assert similarity("", "") == 1.0

    def test_one_empty(self):
        assert similarity("hello", "") == 0.0

    def test_paraphrase_high(self):
        a = "The system uses JWT tokens for stateless authentication"
        b = "The system uses JWT tokens for stateless auth"
        result = similarity(a, b)
        assert result > 0.8

    def test_different_content_low(self):
        a = "The system uses JWT tokens for authentication"
        b = "We deployed PostgreSQL with connection pooling enabled"
        result = similarity(a, b)
        assert result < 0.3

    def test_default_weights(self):
        # Verify default weights: 0.4 Jaccard + 0.6 SequenceMatcher
        a, b = "hello world foo", "hello world bar"
        j = jaccard(a, b)
        s = sequence_ratio(a, b)
        expected = 0.4 * j + 0.6 * s
        assert abs(similarity(a, b) - expected) < 1e-9

    def test_custom_weights(self):
        a, b = "hello world", "world hello"
        j = jaccard(a, b)  # 1.0 (order-insensitive)
        s = sequence_ratio(a, b)  # < 1.0 (order-sensitive)
        # Jaccard-only should be 1.0
        assert similarity(a, b, jaccard_weight=1.0, sequence_weight=0.0) == 1.0
        # SequenceMatcher-only should be < 1.0
        assert similarity(a, b, jaccard_weight=0.0, sequence_weight=1.0) < 1.0

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            similarity("a", "b", jaccard_weight=-1)

    def test_zero_weights_raises(self):
        with pytest.raises(ValueError, match="positive"):
            similarity("a", "b", jaccard_weight=0, sequence_weight=0)

    def test_in_unit_range(self):
        """Similarity is always in [0.0, 1.0]."""
        pairs = [
            ("", ""),
            ("a", ""),
            ("hello", "hello"),
            ("foo bar baz", "baz bar foo"),
            ("completely different", "nothing alike at all"),
        ]
        for a, b in pairs:
            result = similarity(a, b)
            assert 0.0 <= result <= 1.0, f"Out of range for ({a!r}, {b!r}): {result}"


# ── Fixed-point detection ──────────────────────────────────────────────────


class TestIsFixedPoint:
    def test_identical_is_fixed(self):
        assert is_fixed_point("hello world", "hello world") is True

    def test_very_different_is_not_fixed(self):
        assert is_fixed_point("hello", "completely different text") is False

    def test_slight_paraphrase(self):
        a = "The authentication system uses JWT for token management"
        b = "The authentication system uses JWT for token management system"
        # Very close texts — above default threshold (0.92)
        assert is_fixed_point(a, b) is True

    def test_moderate_paraphrase_not_fixed(self):
        a = "The authentication system uses JWT for token management"
        b = "The authentication system uses JWT tokens for management"
        # Word reorder changes both Jaccard and sequence ratio — below 0.92
        assert is_fixed_point(a, b) is False
        # But still detectable with a lower threshold
        assert is_fixed_point(a, b, threshold=0.8) is True

    def test_custom_threshold(self):
        a = "hello world"
        b = "hello world foo"
        # With very low threshold, they match
        assert is_fixed_point(a, b, threshold=0.5) is True
        # With very high threshold, they don't
        assert is_fixed_point(a, b, threshold=0.99) is False

    def test_empty_texts_are_fixed(self):
        assert is_fixed_point("", "") is True


# ── Query cycle detection ─────────────────────────────────────────────────


class TestIsQueryCycle:
    def test_empty_query_is_cycle(self):
        assert is_query_cycle("", ["previous query"]) is True

    def test_whitespace_query_is_cycle(self):
        assert is_query_cycle("   ", ["previous query"]) is True

    def test_exact_repeat_is_cycle(self):
        assert is_query_cycle("auth flow", ["auth flow"]) is True

    def test_case_insensitive_repeat(self):
        assert is_query_cycle("Auth Flow", ["auth flow"]) is True

    def test_punctuation_insensitive_repeat(self):
        # Hyphens are punctuation → stripped → "authflow" vs "auth flow"
        # These don't match (hyphen joins tokens), which is correct behavior
        assert is_query_cycle("auth-flow!", ["auth flow"]) is False
        # But same punctuation pattern matches
        assert is_query_cycle("auth-flow!", ["auth-flow"]) is True

    def test_repeat_in_older_history(self):
        history = ["first query", "second query", "auth flow"]
        assert is_query_cycle("first query", history) is True

    def test_novel_query_is_not_cycle(self):
        history = ["auth flow", "token refresh"]
        assert is_query_cycle("database schema", history) is False

    def test_similar_to_last_is_cycle(self):
        history = ["authentication flow"]
        # Adding one word to a short query — still very similar
        assert is_query_cycle("authentication flow details", history, threshold=0.7) is True

    def test_near_identical_query_is_cycle(self):
        history = ["authentication flow with token refresh and session management details"]
        # Single-word difference on longer text — above default 0.90
        assert is_query_cycle(
            "authentication flow with token refresh and session management detail",
            history,
        ) is True

    def test_different_enough_is_not_cycle(self):
        history = ["authentication flow"]
        assert is_query_cycle("database connection pooling", history) is False

    def test_empty_history(self):
        # Novel query with no history — not a cycle
        assert is_query_cycle("auth flow", []) is False

    def test_custom_threshold(self):
        history = ["hello world"]
        # With very high threshold, even similar queries pass
        assert is_query_cycle("hello world foo", history, threshold=0.99) is False
        # With very low threshold, even different queries are cycles
        assert is_query_cycle("hello world foo", history, threshold=0.5) is True

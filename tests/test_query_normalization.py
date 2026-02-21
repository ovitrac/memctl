"""
Tests for memctl.query — Query normalization, identifier detection,
mode classification, and budget suggestion — v0.10.0 Phase 0.

Validates the deterministic, stdlib-only query processing pipeline:
  - normalize_query(): stop-word stripping with identifier preservation
  - _is_identifier(): code identifier detection (CamelCase, snake_case, etc.)
  - classify_mode(): exploration vs. modification intent classification
  - suggest_budget(): question-length-proportional budget recommendation

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.query import normalize_query, classify_mode, suggest_budget, _is_identifier


# ===========================================================================
# 1. normalize_query() — Stop word stripping with identifier preservation
# ===========================================================================


class TestNormalizeQuery:
    """Test FTS query normalization: stop-word removal, identifier keeping."""

    def test_french_nl_stripped(self):
        """French natural language: stop words removed, content words kept."""
        result = normalize_query("comment créer un incident dans le système")
        assert result == "créer incident système"

    def test_english_nl_stripped(self):
        """English NL with identifier: stop words removed, identifier kept."""
        result = normalize_query("how does SomeServiceImpl work")
        assert result == "SomeServiceImpl work"

    def test_already_keywords_unchanged(self):
        """Already-keyword input passes through unmodified."""
        result = normalize_query("PreAuthorize Controller")
        assert result == "PreAuthorize Controller"

    def test_camelcase_preserved(self):
        """CamelCase identifiers survive stop-word stripping."""
        result = normalize_query("how does SomeServiceImpl work")
        assert "SomeServiceImpl" in result

    def test_snake_case_preserved(self):
        """snake_case identifiers survive stop-word stripping."""
        result = normalize_query("the validate_path function")
        assert "validate_path" in result
        assert result == "validate_path function"

    def test_upper_case_preserved(self):
        """UPPER_CASE constants survive stop-word stripping."""
        result = normalize_query("what is SECRET_PATTERNS")
        assert "SECRET_PATTERNS" in result

    def test_single_stop_word_fallback(self):
        """Single stop word: fallback returns original (never empty)."""
        result = normalize_query("the")
        assert result == "the"

    def test_all_stop_words_fallback(self):
        """All stop words: fallback returns original (never empty)."""
        result = normalize_query("the a an in on at")
        assert result == "the a an in on at"

    def test_mixed_fr_en(self):
        """Mixed French/English: both language stop words stripped."""
        result = normalize_query("le SomeServiceImpl est important")
        assert "SomeServiceImpl" in result
        assert "important" in result
        # French stop words "le" and "est" must be stripped
        assert "le" not in result.split()
        assert "est" not in result.split()

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert normalize_query("") == ""

    def test_dotted_path_preserved(self):
        """Dotted Java-style paths survive stop-word stripping."""
        result = normalize_query("com.example.Foo")
        assert "com.example.Foo" in result

    def test_punctuation_handled(self):
        """Words with trailing commas: tokens include punctuation."""
        # normalize_query splits on whitespace; "créer," != "créer"
        # so comma-attached words are kept even if bare form is a stop word.
        result = normalize_query("créer, incident, système")
        assert "créer," in result or "créer" in result

    def test_single_keyword_unchanged(self):
        """Single non-stop-word keyword passes through unchanged."""
        assert normalize_query("PreAuthorize") == "PreAuthorize"

    def test_whitespace_only_returns_original(self):
        """Whitespace-only input: strip() produces empty → returns original."""
        result = normalize_query("   ")
        assert result == "   "


# ===========================================================================
# 2. _is_identifier() — Code identifier detection
# ===========================================================================


class TestIsIdentifier:
    """Test identifier pattern recognition."""

    def test_camelcase(self):
        assert _is_identifier("SomeServiceImpl") is True

    def test_snake_case(self):
        assert _is_identifier("validate_path") is True

    def test_upper_case_constant(self):
        assert _is_identifier("SECRET_PATTERNS") is True

    def test_dotted_path(self):
        assert _is_identifier("com.example.Foo") is True

    def test_plain_stop_word(self):
        assert _is_identifier("the") is False

    def test_plain_content_word(self):
        assert _is_identifier("incident") is False

    def test_short_upper(self):
        """Two-char UPPER not detected (regex requires 3+ after first)."""
        assert _is_identifier("AB") is False

    def test_dotted_trailing_dot(self):
        """Trailing dot is not a valid dotted path."""
        assert _is_identifier("com.example.") is False


# ===========================================================================
# 3. classify_mode() — Intent classification (exploration vs. modification)
# ===========================================================================


class TestClassifyMode:
    """Test exploration/modification classification."""

    def test_how_question_exploration(self):
        assert classify_mode("How does SomeServiceImpl work?") == "exploration"

    def test_add_action_modification(self):
        assert classify_mode("Add logging to SomeServiceImpl") == "modification"

    def test_where_question_exploration(self):
        assert classify_mode("Where is MSG_ERR_042 defined?") == "exploration"

    def test_replace_action_modification(self):
        assert classify_mode("Replace MSG_ERR_042 with MSG_ERR_043") == "modification"

    def test_explain_exploration(self):
        assert classify_mode("Explain the security model") == "exploration"

    def test_fix_action_modification(self):
        assert classify_mode("Fix the SQL query in SomeMapper") == "modification"

    def test_dependency_question_exploration(self):
        assert classify_mode("What modules depend on service layer?") == "exploration"

    def test_refactor_modification(self):
        result = classify_mode(
            "Refactor SomeController to use constructor injection"
        )
        assert result == "modification"

    def test_add_endpoint_modification(self):
        assert classify_mode("Add a new REST endpoint") == "modification"

    def test_list_exploration(self):
        assert classify_mode("List all JMS listeners") == "exploration"

    def test_french_modification(self):
        assert classify_mode("Ajouter un log dans le service") == "modification"

    def test_french_exploration(self):
        assert classify_mode("Comment fonctionne le module ?") == "exploration"


# ===========================================================================
# 4. suggest_budget() — Length-proportional budget
# ===========================================================================


class TestSuggestBudget:
    """Test budget suggestion based on question length."""

    def test_short_question(self):
        """Short question (< 80 chars) -> 600 tokens."""
        assert suggest_budget(40) == 600

    def test_medium_question(self):
        """Medium question (80-199 chars) -> 800 tokens."""
        assert suggest_budget(150) == 800

    def test_long_question(self):
        """Long question (200-399 chars) -> 1200 tokens."""
        assert suggest_budget(300) == 1200

    def test_very_long_question(self):
        """Very long question (400+ chars) -> 1500 tokens."""
        assert suggest_budget(500) == 1500

    def test_boundary_80(self):
        """Boundary at 80 chars: exactly 80 -> 800."""
        assert suggest_budget(80) == 800

    def test_boundary_200(self):
        """Boundary at 200 chars: exactly 200 -> 1200."""
        assert suggest_budget(200) == 1200

    def test_boundary_400(self):
        """Boundary at 400 chars: exactly 400 -> 1500."""
        assert suggest_budget(400) == 1500

    def test_zero_length(self):
        """Zero-length question -> 600 (minimum budget)."""
        assert suggest_budget(0) == 600

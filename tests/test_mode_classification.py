"""
Tests for mode classification — v0.10.0 Phase 0.

Focused validation of the 10-scenario classification matrix from the
eco mode design report, plus edge cases for ambiguous and French input.

classify_mode() uses deterministic verb-based classification:
  - Modification verbs take priority (action before comprehension).
  - Exploration is the safe default when no signal is detected.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.query import classify_mode


# ===========================================================================
# 1. Core 10-scenario matrix (T1-T5: exploration, D1-D5: modification)
# ===========================================================================


class TestCoreScenarios:
    """The 10 canonical scenarios from the eco mode design report."""

    # ── Exploration (T1-T5) ────────────────────────────────────────────

    def test_t1_how_does_impl_work(self):
        """T1: 'How does SomeServiceImpl work?' -> exploration."""
        assert classify_mode("How does SomeServiceImpl work?") == "exploration"

    def test_t2_where_is_constant_defined(self):
        """T2: 'Where is MSG_ERR_042 defined?' -> exploration."""
        assert classify_mode("Where is MSG_ERR_042 defined?") == "exploration"

    def test_t3_module_dependency_question(self):
        """T3: 'What modules depend on the service layer?' -> exploration."""
        assert classify_mode("What modules depend on the service layer?") == "exploration"

    def test_t4_explain_security_model(self):
        """T4: 'Explain the security model' -> exploration."""
        assert classify_mode("Explain the security model") == "exploration"

    def test_t5_list_jms_listeners(self):
        """T5: 'What JMS listeners exist?' -> exploration."""
        assert classify_mode("What JMS listeners exist?") == "exploration"

    # ── Modification (D1-D5) ───────────────────────────────────────────

    def test_d1_add_logging(self):
        """D1: 'Add logging to SomeServiceImpl' -> modification."""
        assert classify_mode("Add logging to SomeServiceImpl") == "modification"

    def test_d2_replace_constant(self):
        """D2: 'Replace MSG_ERR_042 with MSG_ERR_043' -> modification."""
        assert classify_mode("Replace MSG_ERR_042 with MSG_ERR_043") == "modification"

    def test_d3_refactor_controller(self):
        """D3: 'Refactor SomeController to use constructor injection' -> modification."""
        assert classify_mode(
            "Refactor SomeController to use constructor injection"
        ) == "modification"

    def test_d4_fix_sql_query(self):
        """D4: 'Fix the SQL query in SomeRowMapper' -> modification."""
        assert classify_mode("Fix the SQL query in SomeRowMapper") == "modification"

    def test_d5_add_rest_endpoint(self):
        """D5: 'Add a new REST endpoint for data export' -> modification."""
        assert classify_mode("Add a new REST endpoint for data export") == "modification"


# ===========================================================================
# 2. Edge cases — ambiguous, mixed, and bare inputs
# ===========================================================================


class TestEdgeCases:
    """Edge cases that stress the priority rules and defaults."""

    def test_mixed_explain_how_to_add(self):
        """Mixed: 'explain how to add logging' -> modification (action verb priority)."""
        assert classify_mode("explain how to add logging") == "modification"

    def test_ambiguous_check_config(self):
        """Ambiguous: 'check the configuration' -> exploration ('check' is exploration)."""
        assert classify_mode("check the configuration") == "exploration"

    def test_bare_identifier_default(self):
        """No signals: bare identifier -> exploration (safe default)."""
        assert classify_mode("SomeServiceImpl") == "exploration"

    def test_french_exploration_where(self):
        """French exploration: question word triggers exploration."""
        assert classify_mode("Où se trouve le contrôleur?") == "exploration"

    def test_french_modification_delete(self):
        """French modification: 'Supprimer' is a modification verb."""
        assert classify_mode("Supprimer le fichier de config") == "modification"

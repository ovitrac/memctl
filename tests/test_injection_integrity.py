"""
Tests for injection integrity — v0.10.0 Phase 0.

Validates the structural contract of format_combined_prompt() and the
budget-enforcement behavior of format_injection_block().

These tests ensure:
  - User questions are always the dominant signal (appear first, verbatim).
  - Injection blocks are clearly marked as reference-only material.
  - Mode hints produce the correct guidance text.
  - Budget constraints are respected for item content (char_budget = tokens * 4).
  - Edge cases (empty, whitespace, special chars, unicode) are handled.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.mcp.formatting import format_combined_prompt, format_injection_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Maximum overhead for header + footer metadata lines (generous upper bound).
_MAX_OVERHEAD = 300


def _make_items(n: int, content_size: int = 80) -> list:
    """Create n synthetic memory items for injection tests."""
    items = []
    for i in range(n):
        items.append({
            "id": f"item-{i:03d}",
            "tier": "stm",
            "validation": "unverified",
            "type": "note",
            "title": f"Item {i}",
            "content": f"Content block {i}. " + ("x" * content_size),
            "provenance": {"source_id": f"test-{i}.md", "source_kind": "file"},
            "tags": ["test"],
            "confidence": 0.8,
        })
    return items


# ===========================================================================
# 1. format_combined_prompt() — Structural integrity
# ===========================================================================


class TestCombinedPromptStructure:
    """Validate structural properties of the combined prompt."""

    def test_ii01_user_question_appears_first(self):
        """II-1: User question appears first and verbatim in output."""
        question = "How does the auth module work?"
        block = format_injection_block(_make_items(3), budget_tokens=800)
        result = format_combined_prompt(question, block)
        # Question must appear before injection block
        q_pos = result.index(question)
        block_pos = result.index("## Additional Context")
        assert q_pos < block_pos

    def test_ii02_injection_marked_reference_only(self):
        """II-2: Injection block is clearly marked as 'reference only'."""
        block = format_injection_block(_make_items(2), budget_tokens=600)
        result = format_combined_prompt("What is X?", block)
        assert "reference only" in result.lower()

    def test_ii03_empty_injection_no_context_section(self):
        """II-3: Empty injection block: no 'Additional Context' section."""
        result = format_combined_prompt("What is X?", "")
        assert "What is X?" in result
        assert "Additional Context" not in result

    def test_ii04_special_chars_preserved(self):
        """II-4: User question with special chars preserved verbatim."""
        question = 'She said "hello" and `code` with\nnewline'
        result = format_combined_prompt(question, "")
        assert question in result

    def test_ii05_french_unicode_preserved(self):
        """II-5: French question preserved (unicode)."""
        question = "Comment créer un incident?"
        result = format_combined_prompt(question, "")
        assert question in result

    def test_ii06_exploration_mode_hint(self):
        """II-6: mode_hint='exploration' adds correct guidance text."""
        result = format_combined_prompt(
            "How does X work?", "", mode_hint="exploration"
        )
        assert "exploration" in result.lower()
        assert "do not read or edit files" in result.lower()

    def test_ii07_modification_mode_hint(self):
        """II-7: mode_hint='modification' adds guidance with stale warning."""
        result = format_combined_prompt(
            "Fix the bug", "", mode_hint="modification"
        )
        assert "modification" in result.lower()
        assert "never edit using chunk content" in result.lower()

    def test_ii08_empty_mode_hint_no_mode_section(self):
        """II-8: mode_hint='' adds no mode section."""
        result = format_combined_prompt("What is X?", "", mode_hint="")
        assert "Mode:" not in result

    def test_ii09_short_question_large_block(self):
        """II-9: Short question + large block: question still first."""
        question = "What is X?"
        block = format_injection_block(_make_items(10), budget_tokens=1500)
        result = format_combined_prompt(question, block)
        # Question marker must appear before any injection content
        assert result.index(question) < result.index("Memory (Injected)")

    def test_ii10_markdown_in_injection_no_corruption(self):
        """II-10: Injection block with markdown headers doesn't corrupt structure."""
        items = _make_items(1)
        items[0]["content"] = "## Internal Header\n### Sub-header\nBody text."
        block = format_injection_block(items, budget_tokens=800)
        result = format_combined_prompt("What is X?", block)
        # Both structural markers must be present
        assert "## User Question" in result
        assert "## Additional Context" in result

    def test_ii11_answer_this_marker_present(self):
        """II-11: 'answer THIS' marker present in combined prompt."""
        block = format_injection_block(_make_items(1), budget_tokens=600)
        result = format_combined_prompt("What is X?", block)
        assert "answer THIS" in result

    def test_ii12_do_not_modify_marker_present(self):
        """II-12: 'do not modify or reinterpret' marker present."""
        result = format_combined_prompt("What is X?", "")
        assert "do not modify or reinterpret" in result.lower()

    def test_ii13_whitespace_only_injection_treated_as_empty(self):
        """II-13: Whitespace-only injection block treated as empty."""
        result = format_combined_prompt("What is X?", "   \n  \t  ")
        assert "Additional Context" not in result

    def test_ii14_multiline_question_preserved(self):
        """II-14: Multiline user question preserved verbatim."""
        question = "First line.\nSecond line.\nThird line."
        result = format_combined_prompt(question, "")
        assert question in result


# ===========================================================================
# 2. format_injection_block() — Budget enforcement
# ===========================================================================


class TestInjectionBlockBudget:
    """Validate budget constraints on injection blocks.

    The budget controls item content chars via char_budget = tokens * 4.
    The total output also includes header/footer metadata (~200 chars overhead).
    Tests allow for this overhead using _MAX_OVERHEAD.
    """

    def test_ii15_budget_800_respects_char_limit(self):
        """II-15: Budget 800 with 10 items: output within budget + overhead."""
        items = _make_items(10, content_size=200)
        block = format_injection_block(items, budget_tokens=800)
        assert len(block) <= 800 * 4 + _MAX_OVERHEAD

    def test_ii16_budget_600_respects_char_limit(self):
        """II-16: Budget 600 with items: output within budget + overhead."""
        items = _make_items(10, content_size=200)
        block = format_injection_block(items, budget_tokens=600)
        assert len(block) <= 600 * 4 + _MAX_OVERHEAD

    def test_ii15b_budget_800_fewer_items_than_available(self):
        """Budget 800 with large items: not all 10 items included."""
        items = _make_items(10, content_size=500)
        block = format_injection_block(items, budget_tokens=800)
        # With 500-char content per item, 800*4=3200 char budget can't fit all 10
        assert block.count("[STM:unverified]") < 10

    def test_ii16b_budget_600_truncates_more(self):
        """Budget 600 includes fewer items than budget 800."""
        items = _make_items(10, content_size=300)
        block_600 = format_injection_block(items, budget_tokens=600)
        block_800 = format_injection_block(items, budget_tokens=800)
        count_600 = block_600.count("[STM:unverified]")
        count_800 = block_800.count("[STM:unverified]")
        assert count_600 <= count_800

    def test_ii17_empty_items_returns_empty(self):
        """II-17: Empty items list returns empty string."""
        block = format_injection_block([], budget_tokens=800)
        assert block == ""

    def test_ii18_single_item_always_included(self):
        """II-18: Single item always included regardless of budget."""
        items = _make_items(1, content_size=500)
        block = format_injection_block(items, budget_tokens=10)
        # Even with tiny budget, the first item must appear
        assert "Item 0" in block
        assert "Content block 0" in block

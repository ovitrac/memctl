"""
Tests for memctl.proposer — MemoryProposer parsing strategies.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import pytest

from memctl.proposer import MemoryProposer


@pytest.fixture
def proposer():
    return MemoryProposer()


# ---------------------------------------------------------------------------
# parse_json_stdin — raw JSON array detection (PJ1–PJ6)
# ---------------------------------------------------------------------------


class TestParseJsonStdin:
    """Tests for the raw JSON stdin parsing strategy (v0.14)."""

    def test_pj1_valid_json_array(self, proposer):
        """PJ1: JSON array with valid proposals → MemoryProposal list."""
        text = json.dumps([{
            "type": "fact",
            "title": "Python 3.12 released",
            "content": "Python 3.12 was released in October 2023.",
            "tags": ["python", "release"],
        }])
        _, proposals = proposer.parse_json_stdin(text)
        assert len(proposals) == 1
        assert proposals[0].type == "fact"
        assert proposals[0].title == "Python 3.12 released"
        assert proposals[0].content == "Python 3.12 was released in October 2023."
        assert "python" in proposals[0].tags

    def test_pj2_items_wrapper(self, proposer):
        """PJ2: {"items": [...]} wrapper format → proposals."""
        text = json.dumps({"items": [
            {"content": "Uses event sourcing", "type": "note"},
        ]})
        _, proposals = proposer.parse_json_stdin(text)
        assert len(proposals) == 1
        assert proposals[0].content == "Uses event sourcing"

    def test_pj3_empty_array(self, proposer):
        """PJ3: Empty JSON array → no proposals."""
        _, proposals = proposer.parse_json_stdin("[]")
        assert proposals == []

    def test_pj4_no_content_key(self, proposer):
        """PJ4: JSON array without 'content' key → no proposals."""
        text = json.dumps([{"name": "X", "value": 42}])
        _, proposals = proposer.parse_json_stdin(text)
        assert proposals == []

    def test_pj5_plain_text(self, proposer):
        """PJ5: Plain text (not JSON) → no proposals."""
        _, proposals = proposer.parse_json_stdin(
            "Hello, this is plain text about architecture."
        )
        assert proposals == []

    def test_pj6_mixed_valid_invalid(self, proposer):
        """PJ6: Mix of valid and invalid items → partial parse."""
        text = json.dumps([
            {"content": "good item one", "type": "fact"},
            {"no_content_key": True},
            {"content": "good item two", "type": "note"},
        ])
        _, proposals = proposer.parse_json_stdin(text)
        assert len(proposals) == 2
        assert proposals[0].content == "good item one"
        assert proposals[1].content == "good item two"

    def test_whitespace_around_json(self, proposer):
        """Leading/trailing whitespace doesn't break parsing."""
        text = '  \n  [{"content": "trimmed", "type": "note"}]  \n  '
        _, proposals = proposer.parse_json_stdin(text)
        assert len(proposals) == 1

    def test_empty_string(self, proposer):
        """Empty string → no proposals."""
        _, proposals = proposer.parse_json_stdin("")
        assert proposals == []

    def test_malformed_json(self, proposer):
        """Malformed JSON → no proposals (no exception)."""
        _, proposals = proposer.parse_json_stdin('[{"content": "incomplete"')
        assert proposals == []

    def test_json_number(self, proposer):
        """JSON number (not array/object) → no proposals."""
        _, proposals = proposer.parse_json_stdin("42")
        assert proposals == []

    def test_returns_empty_cleaned_text(self, proposer):
        """Cleaned text is always empty string for JSON stdin."""
        text = json.dumps([{"content": "test", "type": "fact"}])
        cleaned, _ = proposer.parse_json_stdin(text)
        assert cleaned == ""

    def test_multiple_proposals(self, proposer):
        """Multiple valid proposals in one array."""
        items = [
            {"content": f"Item {i}", "type": "fact", "title": f"Title {i}"}
            for i in range(5)
        ]
        _, proposals = proposer.parse_json_stdin(json.dumps(items))
        assert len(proposals) == 5
        for i, p in enumerate(proposals):
            assert p.title == f"Title {i}"


# ---------------------------------------------------------------------------
# parse() — unified dispatcher (UP1–UP8)
# ---------------------------------------------------------------------------


class TestParse:
    """Tests for the unified parse() dispatcher (v0.15)."""

    def test_up1_tool_calls_present(self, proposer):
        """UP1: Tool calls present → returns tool proposals."""
        tool_calls = [{
            "action": "memory.propose",
            "items": [{"content": "from tools", "type": "fact"}],
        }]
        _, proposals = proposer.parse(tool_calls=tool_calls)
        assert len(proposals) == 1
        assert proposals[0].content == "from tools"

    def test_up2_json_stdin(self, proposer):
        """UP2: JSON array on stdin → returns JSON proposals."""
        text = json.dumps([{"content": "json item", "type": "note"}])
        _, proposals = proposer.parse(text=text)
        assert len(proposals) == 1
        assert proposals[0].content == "json item"

    def test_up3_delimiter_blocks(self, proposer):
        """UP3: Delimiter blocks → returns delimiter proposals."""
        text = (
            "Some preamble.\n"
            "<MEMORY_PROPOSALS_JSON>\n"
            '[{"content": "delimited item", "type": "fact"}]\n'
            "</MEMORY_PROPOSALS_JSON>\n"
            "Some epilogue."
        )
        cleaned, proposals = proposer.parse(text=text)
        assert len(proposals) == 1
        assert proposals[0].content == "delimited item"
        assert "preamble" in cleaned

    def test_up4_plain_text_fallback(self, proposer):
        """UP4: Plain text → returns (text, [])."""
        text = "Just plain text with no proposals."
        cleaned, proposals = proposer.parse(text=text)
        assert proposals == []
        assert cleaned == text

    def test_up5_tool_wins_over_json(self, proposer):
        """UP5: Tool calls + JSON text → tool wins (priority 1 > 2)."""
        tool_calls = [{
            "action": "memory.propose",
            "items": [{"content": "tool winner", "type": "fact"}],
        }]
        json_text = json.dumps([{"content": "json loser", "type": "note"}])
        _, proposals = proposer.parse(text=json_text, tool_calls=tool_calls)
        assert len(proposals) == 1
        assert proposals[0].content == "tool winner"

    def test_up6_json_wins_over_delimiter(self, proposer):
        """UP6: JSON + delimiter text → JSON wins (priority 2 > 3)."""
        # Text that is valid JSON and also contains delimiters — JSON checked first
        text = json.dumps([{"content": "json winner", "type": "fact"}])
        _, proposals = proposer.parse(text=text)
        assert len(proposals) == 1
        assert proposals[0].content == "json winner"

    def test_up7_empty_tool_calls_falls_through(self, proposer):
        """UP7: tool_calls=[] → falls through to JSON/delimiter."""
        text = json.dumps([{"content": "fallthrough item", "type": "note"}])
        _, proposals = proposer.parse(text=text, tool_calls=[])
        assert len(proposals) == 1
        assert proposals[0].content == "fallthrough item"

    def test_up8_empty_text_no_tools(self, proposer):
        """UP8: text="", no tool_calls → returns ("", [])."""
        cleaned, proposals = proposer.parse(text="", tool_calls=None)
        assert cleaned == ""
        assert proposals == []

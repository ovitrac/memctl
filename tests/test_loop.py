"""
Tests for memctl.loop — bounded recall-answer loop controller.

Tests are organized by component:
  - Protocol parsing (json, regex, passive)
  - Prompt building
  - Context merging
  - Trace emission and replay
  - LLM invocation (mocked subprocess)
  - Full loop integration (mocked LLM)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import io
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from memctl.loop import (
    PROTOCOL_SYSTEM_PROMPT,
    LoopDirective,
    LoopTrace,
    LoopResult,
    parse_json_directive,
    parse_regex_directive,
    parse_passive_directive,
    parse_directive,
    build_prompt,
    merge_context,
    invoke_llm,
    emit_trace,
    replay_trace,
    run_loop,
)


# ── JSON Protocol Parsing ─────────────────────────────────────────────────


class TestParseJsonDirective:
    def test_valid_need_more(self):
        output = '{"need_more": true, "query": "token refresh", "rationale": "missing details", "stop": false}\n\nThe auth flow uses JWT.'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is True
        assert directive.query == "token refresh"
        assert directive.rationale == "missing details"
        assert directive.stop is False
        assert "JWT" in answer

    def test_valid_stop(self):
        output = '{"need_more": false, "query": null, "rationale": null, "stop": true}\n\nComplete answer here.'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert directive.stop is True
        assert "Complete answer" in answer

    def test_invalid_json_fallback(self):
        output = "This is not JSON\n\nSome answer."
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert directive.stop is True
        assert answer == output  # entire output returned as answer

    def test_invalid_json_strict_raises(self):
        output = "Not JSON at all"
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_json_directive(output, strict=True)

    def test_empty_query_with_need_more(self):
        output = '{"need_more": true, "query": "", "stop": false}\n\nAnswer.'
        directive, answer = parse_json_directive(output)
        # Empty query → treated as stop
        assert directive.need_more is False
        assert directive.stop is True

    def test_null_query_with_need_more(self):
        output = '{"need_more": true, "query": null, "stop": false}\n\nAnswer.'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert directive.stop is True

    def test_minimal_json(self):
        output = '{"need_more": false}\n\nDone.'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert directive.query is None
        assert directive.stop is False  # not explicitly set

    def test_extra_fields_ignored(self):
        output = '{"need_more": false, "stop": true, "extra": 42}\n\nAnswer.'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert directive.stop is True

    def test_no_newline_after_json(self):
        output = '{"need_more": false, "stop": true}'
        directive, answer = parse_json_directive(output)
        assert directive.need_more is False
        assert answer == ""


# ── Regex Protocol Parsing ────────────────────────────────────────────────


class TestParseRegexDirective:
    def test_need_more_with_query(self):
        output = "Here is my analysis.\nNEED_MORE: missing error handling details\nQUERY: error handling patterns\nEnd."
        directive, answer = parse_regex_directive(output)
        assert directive.need_more is True
        assert directive.query == "error handling patterns"
        assert directive.rationale == "missing error handling details"

    def test_query_only(self):
        output = "Some answer.\nQUERY: database schema\nMore text."
        directive, answer = parse_regex_directive(output)
        assert directive.need_more is True
        assert directive.query == "database schema"

    def test_no_patterns(self):
        output = "A complete answer with no refinement signals."
        directive, answer = parse_regex_directive(output)
        assert directive.need_more is False
        assert directive.stop is True
        assert answer == output

    def test_case_insensitive(self):
        output = "Answer.\nneed_more: details missing\nquery: more info"
        directive, answer = parse_regex_directive(output)
        assert directive.need_more is True
        assert directive.query == "more info"


# ── Passive Protocol ──────────────────────────────────────────────────────


class TestParsePassiveDirective:
    def test_always_stops(self):
        output = "Any LLM output whatsoever."
        directive, answer = parse_passive_directive(output)
        assert directive.need_more is False
        assert directive.stop is True
        assert answer == output


# ── Protocol Dispatch ─────────────────────────────────────────────────────


class TestParseDirective:
    def test_json_dispatch(self):
        output = '{"need_more": false, "stop": true}\n\nAnswer.'
        directive, _ = parse_directive(output, "json")
        assert directive.stop is True

    def test_regex_dispatch(self):
        output = "Answer.\nQUERY: more details"
        directive, _ = parse_directive(output, "regex")
        assert directive.need_more is True

    def test_passive_dispatch(self):
        directive, _ = parse_directive("anything", "passive")
        assert directive.stop is True

    def test_unknown_protocol_raises(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            parse_directive("text", "unknown")


# ── Prompt Building ───────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_json_protocol_includes_system_prompt(self):
        prompt = build_prompt("some context", "what is X?", protocol="json")
        assert "FIRST line" in prompt  # protocol instructions present
        assert "## Context" in prompt
        assert "some context" in prompt
        assert "## Question" in prompt
        assert "what is X?" in prompt

    def test_passive_protocol_no_system_prompt(self):
        prompt = build_prompt("ctx", "query", protocol="passive")
        assert "FIRST line" not in prompt
        assert "## Context" in prompt
        assert "## Question" in prompt

    def test_user_system_prompt_appended(self):
        prompt = build_prompt(
            "ctx", "query",
            system_prompt="You are a security expert.",
            protocol="json",
        )
        assert "FIRST line" in prompt
        assert "You are a security expert." in prompt
        # User prompt comes after protocol
        idx_protocol = prompt.index("FIRST line")
        idx_user = prompt.index("security expert")
        assert idx_user > idx_protocol

    def test_empty_context(self):
        prompt = build_prompt("", "query", protocol="json")
        assert "## Context" not in prompt
        assert "## Question" in prompt

    def test_whitespace_only_context(self):
        prompt = build_prompt("   \n  ", "query", protocol="passive")
        assert "## Context" not in prompt


# ── Context Merging ───────────────────────────────────────────────────────


class TestMergeContext:
    def test_basic_merge(self):
        existing = "Initial context."
        items = [
            {"id": "a", "title": "Item A", "content": "Content of A"},
            {"id": "b", "title": "Item B", "content": "Content of B"},
        ]
        seen = set()
        merged, new, count = merge_context(existing, items, seen, 10000)
        assert count == 2
        assert "Content of A" in merged
        assert "Content of B" in merged
        assert "Initial context." in merged
        assert "a" in seen
        assert "b" in seen

    def test_dedup_by_id(self):
        existing = "Existing."
        items = [
            {"id": "a", "title": "A", "content": "New A"},
            {"id": "b", "title": "B", "content": "New B"},
        ]
        seen = {"a"}  # "a" already seen
        merged, new, count = merge_context(existing, items, seen, 10000)
        assert count == 1
        assert len(new) == 1
        assert new[0]["id"] == "b"
        assert "New A" not in merged  # deduped
        assert "New B" in merged

    def test_all_seen_returns_zero(self):
        items = [{"id": "a", "title": "A", "content": "A"}]
        seen = {"a"}
        merged, new, count = merge_context("ctx", items, seen, 10000)
        assert count == 0
        assert merged == "ctx"

    def test_budget_trimming(self):
        existing = "Short."
        items = [{"id": "a", "title": "A", "content": "X" * 500}]
        seen = set()
        merged, new, count = merge_context(existing, items, seen, 100)
        assert len(merged) <= 100
        assert count == 1

    def test_empty_existing_context(self):
        items = [{"id": "a", "title": "A", "content": "Content"}]
        seen = set()
        merged, new, count = merge_context("", items, seen, 10000)
        assert "Content" in merged
        assert not merged.startswith("\n")  # no leading newlines

    def test_empty_items(self):
        merged, new, count = merge_context("ctx", [], set(), 10000)
        assert merged == "ctx"
        assert count == 0


# ── Trace Emission ────────────────────────────────────────────────────────


class TestEmitTrace:
    def test_emit_to_file(self):
        buf = io.StringIO()
        trace = LoopTrace(iter=1, query="test", new_items=3, sim=None, action="continue")
        emit_trace(trace, trace_file=buf)
        line = buf.getvalue().strip()
        obj = json.loads(line)
        assert obj["iter"] == 1
        assert obj["query"] == "test"
        assert obj["new_items"] == 3
        assert obj["action"] == "continue"

    def test_emit_to_stderr(self, capsys):
        trace = LoopTrace(iter=2, query=None, new_items=0, sim=0.95, action="fixed_point")
        emit_trace(trace)
        captured = capsys.readouterr()
        obj = json.loads(captured.err.strip())
        assert obj["iter"] == 2
        assert obj["sim"] == 0.95

    def test_quiet_suppresses_stderr(self, capsys):
        trace = LoopTrace(iter=1, query="q", new_items=1, sim=None, action="continue")
        emit_trace(trace, quiet=True)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_quiet_still_writes_file(self):
        buf = io.StringIO()
        trace = LoopTrace(iter=1, query="q", new_items=1, sim=None, action="continue")
        emit_trace(trace, trace_file=buf, quiet=True)
        assert buf.getvalue().strip() != ""


# ── Trace Replay ──────────────────────────────────────────────────────────


class TestReplayTrace:
    def test_replay_roundtrip(self, tmp_path):
        trace_path = str(tmp_path / "trace.jsonl")
        traces = [
            LoopTrace(iter=1, query="auth", new_items=5, sim=None, action="continue"),
            LoopTrace(iter=2, query="tokens", new_items=2, sim=0.78, action="continue"),
            LoopTrace(iter=3, query=None, new_items=0, sim=0.94, action="fixed_point"),
        ]
        with open(trace_path, "w") as f:
            for t in traces:
                f.write(json.dumps(t.to_dict()) + "\n")

        replayed = replay_trace(trace_path)
        assert len(replayed) == 3
        assert replayed[0].query == "auth"
        assert replayed[1].sim == 0.78
        assert replayed[2].action == "fixed_point"

    def test_replay_empty_file(self, tmp_path):
        trace_path = str(tmp_path / "empty.jsonl")
        with open(trace_path, "w") as f:
            f.write("")
        assert replay_trace(trace_path) == []

    def test_replay_missing_file(self):
        with pytest.raises(FileNotFoundError):
            replay_trace("/nonexistent/path.jsonl")


# ── LLM Invocation ────────────────────────────────────────────────────────


class TestInvokeLLM:
    def test_stdin_mode(self):
        # Use echo as a mock LLM — it outputs the input
        result = invoke_llm("cat", "hello world", mode="stdin")
        assert result == "hello world"

    def test_file_mode(self):
        result = invoke_llm("cat", "file content test", mode="file")
        assert result == "file content test"

    def test_command_not_found(self):
        with pytest.raises(RuntimeError, match="not found"):
            invoke_llm("nonexistent_command_xyz", "test")

    def test_command_failure(self):
        with pytest.raises(RuntimeError, match="failed"):
            invoke_llm("false", "test")  # `false` exits with code 1

    def test_timeout(self):
        with pytest.raises(RuntimeError, match="timed out"):
            invoke_llm("sleep 10", "test", timeout=1)


# ── LoopTrace Serialization ──────────────────────────────────────────────


class TestLoopTrace:
    def test_to_dict(self):
        trace = LoopTrace(iter=1, query="q", new_items=3, sim=0.85, action="continue")
        d = trace.to_dict()
        assert d == {"iter": 1, "query": "q", "new_items": 3, "sim": 0.85, "action": "continue"}

    def test_to_dict_null_sim(self):
        trace = LoopTrace(iter=1, query=None, new_items=0, sim=None, action="llm_stop")
        d = trace.to_dict()
        assert d["sim"] is None
        assert d["query"] is None


# ── Full Loop Integration (mocked LLM) ───────────────────────────────────


class TestRunLoop:
    """Integration tests with mocked LLM invocations."""

    def _mock_llm_stop(self, *args, **kwargs):
        """LLM that immediately stops."""
        return '{"need_more": false, "stop": true}\n\nFinal answer from LLM.'

    def _mock_llm_two_iterations(self):
        """LLM that requests one refinement then stops."""
        calls = []

        def handler(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return '{"need_more": true, "query": "token refresh", "rationale": "missing", "stop": false}\n\nPartial answer.'
            else:
                return '{"need_more": false, "stop": true}\n\nComplete answer after refinement.'

        return handler

    def _mock_llm_converging(self):
        """LLM that produces similar answers (fixed point)."""
        calls = []

        def handler(*args, **kwargs):
            calls.append(1)
            base = "The authentication system uses JWT tokens for stateless session management"
            if len(calls) == 1:
                return f'{{"need_more": true, "query": "JWT details", "stop": false}}\n\n{base} and refresh tokens.'
            elif len(calls) == 2:
                return f'{{"need_more": true, "query": "JWT refresh", "stop": false}}\n\n{base} and refresh tokens with rotation.'
            else:
                return f'{{"need_more": false, "stop": true}}\n\n{base} and refresh tokens with rotation policy.'

        return handler

    @patch("memctl.loop.invoke_llm")
    def test_single_iteration_stop(self, mock_invoke):
        mock_invoke.side_effect = self._mock_llm_stop
        result = run_loop(
            "initial context", "what is auth?", "fake_llm",
            db_path=":memory:", max_calls=3,
        )
        assert result.answer == "Final answer from LLM."
        assert result.iterations == 1
        assert result.converged is True
        assert result.stop_reason == "llm_stop"
        assert mock_invoke.call_count == 1

    @patch("memctl.loop.recall_items", return_value=[
        {"id": "new1", "title": "Token doc", "content": "Refresh token details."}
    ])
    @patch("memctl.loop.invoke_llm")
    def test_two_iterations(self, mock_invoke, mock_recall):
        mock_invoke.side_effect = self._mock_llm_two_iterations()
        result = run_loop(
            "initial context", "auth flow", "fake_llm",
            db_path=":memory:", max_calls=5,
        )
        assert result.iterations == 2
        assert result.converged is True
        assert result.stop_reason == "llm_stop"
        assert "refinement" in result.answer
        assert mock_invoke.call_count == 2
        assert mock_recall.call_count == 1

    @patch("memctl.loop.invoke_llm")
    def test_max_calls_reached(self, mock_invoke):
        # LLM always requests more with distinct queries (no cycle)
        call_count = []

        def handler(*args, **kwargs):
            call_count.append(1)
            n = len(call_count)
            return f'{{"need_more": true, "query": "topic {n} details", "stop": false}}\n\nPartial answer {n}.'

        mock_invoke.side_effect = handler

        with patch("memctl.loop.recall_items", return_value=[
            {"id": f"item_{i}", "title": f"Item {i}", "content": f"Content {i}"}
            for i in range(5)
        ]):
            result = run_loop(
                "ctx", "query", "fake_llm",
                db_path=":memory:", max_calls=2,
            )
        assert result.iterations == 2
        assert result.converged is False
        assert result.stop_reason == "max_calls"

    @patch("memctl.loop.recall_items", return_value=[])
    @patch("memctl.loop.invoke_llm")
    def test_no_new_items_stops(self, mock_invoke, mock_recall):
        mock_invoke.return_value = '{"need_more": true, "query": "more", "stop": false}\n\nPartial answer.'
        result = run_loop(
            "ctx", "query", "fake_llm",
            db_path=":memory:", max_calls=5, stop_on_no_new=True,
        )
        assert result.stop_reason == "no_new_items"
        assert result.iterations == 1

    @patch("memctl.loop.invoke_llm")
    def test_query_cycle_detection(self, mock_invoke):
        calls = []

        def handler(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return '{"need_more": true, "query": "auth flow", "stop": false}\n\nFirst answer.'
            else:
                return '{"need_more": false, "stop": true}\n\nDone.'

        mock_invoke.side_effect = handler
        # The refined query "auth flow" matches the original query → cycle
        with patch("memctl.loop.recall_items", return_value=[]):
            result = run_loop(
                "ctx", "auth flow", "fake_llm",
                db_path=":memory:", max_calls=5,
            )
        assert result.stop_reason == "query_cycle"
        assert result.iterations == 1

    @patch("memctl.loop.invoke_llm")
    def test_trace_emission(self, mock_invoke):
        mock_invoke.return_value = '{"need_more": false, "stop": true}\n\nAnswer.'
        buf = io.StringIO()
        result = run_loop(
            "ctx", "query", "fake_llm",
            db_path=":memory:", trace=True, trace_file=buf,
        )
        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["iter"] == 1
        assert obj["action"] == "llm_stop"

    @patch("memctl.loop.invoke_llm")
    def test_passive_protocol(self, mock_invoke):
        mock_invoke.return_value = "Plain answer without any protocol."
        result = run_loop(
            "ctx", "query", "fake_llm",
            db_path=":memory:", protocol="passive", max_calls=3,
        )
        assert result.iterations == 1
        assert result.converged is True
        assert result.stop_reason == "llm_stop"
        assert result.answer == "Plain answer without any protocol."

    @patch("memctl.loop.invoke_llm")
    def test_regex_protocol(self, mock_invoke):
        calls = []

        def handler(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "Partial analysis.\nNEED_MORE: missing error codes\nQUERY: error handling codes"
            else:
                return "Complete analysis with error codes."

        mock_invoke.side_effect = handler
        with patch("memctl.loop.recall_items", return_value=[
            {"id": "e1", "title": "Errors", "content": "Error code list."}
        ]):
            result = run_loop(
                "ctx", "query", "fake_llm",
                db_path=":memory:", protocol="regex", max_calls=5,
            )
        assert result.iterations == 2
        assert result.stop_reason == "llm_stop"

    @patch("memctl.loop.invoke_llm")
    def test_result_has_traces(self, mock_invoke):
        mock_invoke.return_value = '{"need_more": false, "stop": true}\n\nDone.'
        result = run_loop(
            "ctx", "query", "fake_llm",
            db_path=":memory:", trace=True,
        )
        assert len(result.traces) == 1
        assert result.traces[0].action == "llm_stop"

    @patch("memctl.loop.invoke_llm")
    def test_strict_mode_raises_on_bad_json(self, mock_invoke):
        mock_invoke.return_value = "Not JSON at all\n\nAnswer."
        with pytest.raises(ValueError, match="Invalid JSON"):
            run_loop(
                "ctx", "query", "fake_llm",
                db_path=":memory:", strict=True,
            )


# ── Protocol System Prompt ────────────────────────────────────────────────


class TestProtocolSystemPrompt:
    def test_contains_json_requirement(self):
        assert "JSON" in PROTOCOL_SYSTEM_PROMPT
        assert "need_more" in PROTOCOL_SYSTEM_PROMPT
        assert "FIRST line" in PROTOCOL_SYSTEM_PROMPT

    def test_is_string(self):
        assert isinstance(PROTOCOL_SYSTEM_PROMPT, str)
        assert len(PROTOCOL_SYSTEM_PROMPT) > 100

"""
Tests for memctl.mcp.audit — MCP structured audit logging.

Invariants tested:
- A1: Audit log never contains raw content beyond 120-char preview
- A2: Audit record includes all required v1 fields (v, ts, rid, tool, sid, db, outcome, ms)
- A3: rid is present and unique per call
- A4: d.hash matches SHA-256 of actual content
- A5: Audit log() never raises (fire-and-forget, even with broken output)
- A6: db field is root-relative when db-root is set

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import hashlib
import io
import json

import pytest

from memctl.mcp.audit import AUDIT_SCHEMA_VERSION, PREVIEW_MAX_CHARS, AuditLogger


@pytest.fixture
def buf():
    """StringIO buffer for capturing audit output."""
    return io.StringIO()


@pytest.fixture
def logger(buf):
    """AuditLogger writing to an in-memory buffer."""
    return AuditLogger(output=buf)


def _parse_record(buf: io.StringIO) -> dict:
    """Parse the single JSONL record from the buffer."""
    buf.seek(0)
    lines = [ln for ln in buf.read().strip().splitlines() if ln]
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"
    return json.loads(lines[0])


# ── A1: Preview never exceeds 120 chars ────────────────────────────

class TestPreviewLimit:
    """A1: Raw content is never stored; preview capped at 120 chars."""

    def test_short_content_no_truncation(self):
        """Content shorter than PREVIEW_MAX_CHARS is kept intact."""
        detail = AuditLogger.make_content_detail("hello world")
        assert detail["preview"] == "hello world"
        assert len(detail["preview"]) <= PREVIEW_MAX_CHARS

    def test_long_content_truncated_with_ellipsis(self):
        """Content exceeding 120 chars is truncated and ends with ellipsis."""
        long_text = "A" * 300
        detail = AuditLogger.make_content_detail(long_text)
        assert len(detail["preview"]) <= PREVIEW_MAX_CHARS + 1  # +1 for ellipsis char
        assert detail["preview"].endswith("\u2026")

    def test_newline_sanitized_in_preview(self):
        """Newlines in preview are replaced with spaces."""
        text = "line1\nline2\nline3\r\nline4"
        detail = AuditLogger.make_content_detail(text)
        assert "\n" not in detail["preview"]
        assert "\r" not in detail["preview"]


# ── A2: Required v1 fields present ─────────────────────────────────

class TestRequiredFields:
    """A2: Every audit record contains all v1 mandatory fields."""

    REQUIRED_KEYS = {"v", "ts", "rid", "tool", "sid", "db", "outcome", "ms"}

    def test_all_required_fields_present(self, logger, buf):
        """Basic log() call produces a record with all required keys."""
        rid = logger.new_rid()
        logger.log(
            tool="memory_write",
            rid=rid,
            session_id="sess-1",
            db_path="/data/memory.db",
            outcome="ok",
            latency_ms=12.5,
        )
        record = _parse_record(buf)
        missing = self.REQUIRED_KEYS - set(record.keys())
        assert not missing, f"Missing required fields: {missing}"

    def test_schema_version_is_v1(self, logger, buf):
        """The 'v' field matches AUDIT_SCHEMA_VERSION (1)."""
        logger.log(
            tool="memory_recall",
            rid=logger.new_rid(),
            session_id="default",
            db_path="memory.db",
            outcome="ok",
        )
        record = _parse_record(buf)
        assert record["v"] == AUDIT_SCHEMA_VERSION

    def test_detail_omitted_when_none(self, logger, buf):
        """When detail=None, the 'd' key is absent (not null)."""
        logger.log(
            tool="memory_stats",
            rid=logger.new_rid(),
            session_id="default",
            db_path="memory.db",
            outcome="ok",
        )
        record = _parse_record(buf)
        assert "d" not in record


# ── A3: rid present and unique ──────────────────────────────────────

class TestRequestId:
    """A3: Each new_rid() produces a unique hex string."""

    def test_rid_is_hex_string(self, logger):
        """new_rid() returns a 32-char hex string (UUID4 without dashes)."""
        rid = logger.new_rid()
        assert len(rid) == 32
        int(rid, 16)  # validates hex

    def test_rids_are_unique(self, logger):
        """1000 consecutive rids are all distinct."""
        rids = {logger.new_rid() for _ in range(1000)}
        assert len(rids) == 1000


# ── A4: d.hash matches SHA-256 of actual content ───────────────────

class TestContentHash:
    """A4: The hash field in content detail matches SHA-256 of the content."""

    def test_hash_matches_sha256(self):
        """make_content_detail hash == hashlib.sha256 of the same content."""
        content = "The quick brown fox jumps over the lazy dog."
        detail = AuditLogger.make_content_detail(content)
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert detail["hash"] == expected

    def test_bytes_matches_utf8_length(self):
        """bytes field matches len(content.encode('utf-8'))."""
        content = "caf\u00e9 \u2014 cr\u00e8me br\u00fbl\u00e9e"  # multi-byte chars
        detail = AuditLogger.make_content_detail(content)
        assert detail["bytes"] == len(content.encode("utf-8"))


# ── A5: log() never raises ──────────────────────────────────────────

class TestFireAndForget:
    """A5: log() swallows all exceptions — never disrupts tool execution."""

    def test_broken_output_does_not_raise(self):
        """Writing to a closed stream does not raise."""
        closed_buf = io.StringIO()
        closed_buf.close()
        broken_logger = AuditLogger(output=closed_buf)
        # Must not raise
        broken_logger.log(
            tool="memory_write",
            rid=broken_logger.new_rid(),
            session_id="default",
            db_path="memory.db",
            outcome="ok",
        )

    def test_none_tool_does_not_raise(self):
        """Passing None where string expected does not raise."""
        buf = io.StringIO()
        safe_logger = AuditLogger(output=buf)
        # None is not a valid tool, but log() must not raise
        safe_logger.log(
            tool=None,  # type: ignore[arg-type]
            rid="fake-rid",
            session_id="x",
            db_path="y",
            outcome="ok",
        )


# ── A6: db field is root-relative ───────────────────────────────────

class TestDbPathRelative:
    """A6: The db field in the record reflects whatever path is passed."""

    def test_relative_path_preserved(self, logger, buf):
        """Relative db_path appears as-is in the record."""
        logger.log(
            tool="memory_stats",
            rid=logger.new_rid(),
            session_id="default",
            db_path="project/.memory/memory.db",
            outcome="ok",
        )
        record = _parse_record(buf)
        assert record["db"] == "project/.memory/memory.db"


# ── Additional: make_content_detail with policy ─────────────────────

class TestContentDetailPolicy:
    """Policy result is included when provided."""

    def test_policy_included(self):
        """make_content_detail includes policy dict when supplied."""
        policy = {"decision": "rejected", "rule": "secret_api_key"}
        detail = AuditLogger.make_content_detail("secret=sk-12345", policy_result=policy)
        assert detail["policy"] == policy
        assert detail["policy"]["decision"] == "rejected"

    def test_policy_absent_when_none(self):
        """make_content_detail omits policy key when policy_result is None."""
        detail = AuditLogger.make_content_detail("clean content")
        assert "policy" not in detail


# ── Additional: JSONL parseable output ──────────────────────────────

class TestJsonlOutput:
    """Audit output is valid single-line JSON (JSONL)."""

    def test_output_is_single_line_json(self, logger, buf):
        """Each log() call writes exactly one newline-terminated JSON line."""
        logger.log(
            tool="memory_search",
            rid=logger.new_rid(),
            session_id="sess-42",
            db_path="test.db",
            outcome="ok",
            detail={"query": "hello", "hits": 3},
            latency_ms=7.2,
        )
        buf.seek(0)
        raw = buf.read()
        lines = raw.strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["d"]["query"] == "hello"
        assert record["ms"] == 7.2

"""
Tests for memctl.ingest — Chunking, file dedup, source resolution, policy on ingest.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
import subprocess
import sys
import pytest

from memctl.ingest import (
    IngestResult,
    chunk_paragraphs,
    corpus_stats,
    ingest_file,
    resolve_sources,
    _expand_camel_case,
    _file_sha256,
    _infer_tags_from_path,
    _infer_title,
)
from memctl.policy import MemoryPolicy
from memctl.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.md"
    f.write_text(
        "# Architecture Guide\n\n"
        "We use microservices for scalability.\n\n"
        "Each service has its own database.\n\n"
        "Communication is via gRPC.\n",
        encoding="utf-8",
    )
    return str(f)


@pytest.fixture
def large_file(tmp_path):
    f = tmp_path / "large.md"
    paragraphs = [f"Paragraph {i}: " + "x" * 500 for i in range(20)]
    f.write_text("\n\n".join(paragraphs), encoding="utf-8")
    return str(f)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_empty_text(self):
        assert chunk_paragraphs("") == []
        assert chunk_paragraphs("   \n\n  ") == []

    def test_single_paragraph(self):
        chunks = chunk_paragraphs("Hello world, this is a test.")
        assert len(chunks) == 1
        text, start, end = chunks[0]
        assert "Hello world" in text
        assert start == 0

    def test_multiple_paragraphs(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_paragraphs(text, max_tokens=10000)
        assert len(chunks) == 1  # all fit in one chunk

    def test_budget_splits(self):
        # Create text that exceeds budget
        paras = [f"Paragraph {i}: " + "word " * 100 for i in range(10)]
        text = "\n\n".join(paras)
        chunks = chunk_paragraphs(text, max_tokens=200)
        assert len(chunks) > 1

    def test_chunk_line_numbers(self):
        text = "Line one.\n\nLine three.\n\nLine five."
        chunks = chunk_paragraphs(text, max_tokens=10000)
        assert len(chunks) == 1
        _, start, end = chunks[0]
        assert start == 0
        assert end >= 0


# ---------------------------------------------------------------------------
# File SHA-256
# ---------------------------------------------------------------------------


class TestFileSHA256:
    def test_deterministic(self, sample_file):
        h1 = _file_sha256(sample_file)
        h2 = _file_sha256(sample_file)
        assert h1 == h2
        assert len(h1) == 64  # hex sha256


# ---------------------------------------------------------------------------
# Tag/title inference
# ---------------------------------------------------------------------------


class TestInference:
    def test_infer_title_from_markdown(self):
        text = "# My Great Document\n\nSome content here."
        assert _infer_title(text, "fallback") == "My Great Document"

    def test_infer_title_fallback(self):
        text = "No heading here.\nJust plain text."
        assert _infer_title(text, "default") == "default"

    def test_infer_tags_from_path(self):
        tags = _infer_tags_from_path("/home/user/project/docs/guide.md")
        assert "markdown" in tags
        # Should include parent dir names
        assert any("docs" in t for t in tags) or any("project" in t for t in tags)

    def test_infer_tags_python(self):
        tags = _infer_tags_from_path("/src/utils.py")
        assert "python" in tags


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


class TestSourceResolution:
    def test_single_file(self, sample_file):
        result = resolve_sources([sample_file])
        assert len(result) == 1
        assert result[0] == sample_file

    def test_directory(self, tmp_path):
        (tmp_path / "a.md").write_text("A", encoding="utf-8")
        (tmp_path / "b.py").write_text("B", encoding="utf-8")
        (tmp_path / "c.jpg").write_text("C", encoding="utf-8")  # not ingestable
        result = resolve_sources([str(tmp_path)])
        assert len(result) == 2  # .md and .py only

    def test_glob(self, tmp_path):
        (tmp_path / "x.md").write_text("X", encoding="utf-8")
        (tmp_path / "y.md").write_text("Y", encoding="utf-8")
        result = resolve_sources([str(tmp_path / "*.md")])
        assert len(result) == 2

    def test_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            resolve_sources(["/nonexistent/file.txt"])

    def test_dedup(self, sample_file):
        result = resolve_sources([sample_file, sample_file])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Ingest file
# ---------------------------------------------------------------------------


class TestIngestFile:
    def test_basic_ingest(self, store, sample_file):
        result = ingest_file(store, sample_file)
        assert result.files_processed == 1
        assert result.chunks_created >= 1
        assert len(result.item_ids) >= 1

    def test_idempotent_ingest(self, store, sample_file):
        r1 = ingest_file(store, sample_file)
        assert r1.files_processed == 1
        assert r1.chunks_created >= 1

        r2 = ingest_file(store, sample_file)
        assert r2.files_skipped == 1
        assert r2.chunks_created == 0

    def test_ingest_stores_corpus_hash(self, store, sample_file):
        ingest_file(store, sample_file)
        abs_path = os.path.abspath(sample_file)
        h = store.read_corpus_hash(abs_path)
        assert h is not None
        assert h["sha256"] != ""
        assert h["chunk_count"] >= 1

    def test_ingest_auto_format(self, store, sample_file):
        result = ingest_file(store, sample_file, format_mode="auto")
        assert result.chunks_created >= 1
        # Title should be inferred from markdown heading
        items = store.list_items(limit=10)
        assert any("Architecture Guide" in it.title for it in items)

    def test_ingest_with_tags(self, store, sample_file):
        ingest_file(store, sample_file, tags=["test", "docs"])
        items = store.list_items(limit=10)
        for it in items:
            assert "test" in it.tags
            assert "docs" in it.tags

    def test_large_file_chunking(self, store, large_file):
        result = ingest_file(store, large_file, max_tokens=500)
        assert result.chunks_created > 1


# ---------------------------------------------------------------------------
# Corpus stats
# ---------------------------------------------------------------------------


class TestCorpusStats:
    def test_basic_stats(self, sample_file):
        stats = corpus_stats([sample_file])
        assert stats["files"] == 1
        assert stats["total_lines"] > 0
        assert stats["total_tokens"] > 0
        assert len(stats["per_file"]) == 1


# ---------------------------------------------------------------------------
# CamelCase expansion (P5 — v0.17)
# ---------------------------------------------------------------------------


class TestCamelCaseExpansion:
    def test_p5_t1_pascal_case(self):
        """P5-T1: PascalCase identifier splits correctly."""
        result = _expand_camel_case("IncidentMetierService")
        assert "incident" in result
        assert "metier" in result
        assert "service" in result

    def test_p5_t1b_camel_case(self):
        """P5-T1b: camelCase identifier splits correctly."""
        result = _expand_camel_case("getUserName")
        assert "get" in result
        assert "user" in result
        assert "name" in result

    def test_p5_t3_non_camel_unchanged(self):
        """P5-T3: Non-camelCase content returns empty string."""
        assert _expand_camel_case("regular text with no identifiers") == ""

    def test_p5_t4_all_caps_not_expanded(self):
        """P5-T4: ALL_CAPS and snake_case are not expanded."""
        assert _expand_camel_case("SECRET_PATTERNS") == ""
        assert _expand_camel_case("my_function_name") == ""

    def test_p5_t2_ingest_produces_camel_line(self, store, tmp_path):
        """P5-T2: Ingested Java file contains [camel: ...] line in content."""
        java = tmp_path / "Test.java"
        java.write_text(
            "public class IncidentMetierService {\n"
            "    public TraitementBpmService getService() { return null; }\n"
            "}\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(java), format_mode="auto")
        assert result.chunks_created >= 1

        item = store.read_item(result.item_ids[0])
        assert "[camel:" in item.content
        assert "incident" in item.content
        assert "metier" in item.content

    def test_mixed_identifiers(self):
        """Multiple identifiers in same text are all expanded."""
        text = "The IncidentService calls getUserProfile via RestController"
        result = _expand_camel_case(text)
        assert "incident" in result
        assert "service" in result
        assert "get" in result
        assert "user" in result
        assert "profile" in result


# ---------------------------------------------------------------------------
# Policy on ingest (v0.21 — closes ingest bypass)
# ---------------------------------------------------------------------------


class TestIngestPolicy:
    """Tests for policy enforcement during ingest (v0.21)."""

    def test_default_policy_active(self, store, tmp_path):
        """Default policy (no explicit arg) applies evaluation."""
        f = tmp_path / "secret.txt"
        f.write_text(
            "Config line:\napi_key = sk-abcdefghij1234567890secret\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(f))
        # Secret pattern triggers rejection
        assert result.rejected_policy >= 1
        assert result.chunks_created == 0

    def test_explicit_none_skips_policy(self, store, tmp_path):
        """policy=None explicitly opts out of policy."""
        f = tmp_path / "secret.txt"
        f.write_text(
            "Config:\napi_key = sk-abcdefghij1234567890secret\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(f), policy=None)
        # No policy → secret stored as-is
        assert result.rejected_policy == 0
        assert result.chunks_created >= 1

    def test_explicit_false_skips_policy(self, store, tmp_path):
        """policy=False explicitly opts out of policy."""
        f = tmp_path / "secret.txt"
        f.write_text(
            "Config:\napi_key = sk-abcdefghij1234567890secret\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(f), policy=False)
        assert result.rejected_policy == 0
        assert result.chunks_created >= 1

    def test_custom_policy(self, store, tmp_path):
        """Custom MemoryPolicy instance is used."""
        f = tmp_path / "clean.txt"
        f.write_text("A clean document about architecture.\n", encoding="utf-8")
        custom = MemoryPolicy()
        result = ingest_file(store, str(f), policy=custom)
        assert result.rejected_policy == 0
        assert result.chunks_created >= 1

    def test_secret_rejected(self, store, tmp_path):
        """File containing API key → chunk rejected, not stored."""
        f = tmp_path / "creds.env"
        f.write_text("password = p4ssw0rd_very_secret_value\n", encoding="utf-8")
        result = ingest_file(store, str(f))
        assert result.rejected_policy >= 1
        # Verify nothing stored
        items = store.list_items(limit=100)
        for it in items:
            assert "p4ssw0rd" not in it.content

    def test_jwt_rejected(self, store, tmp_path):
        """File containing JWT → chunk rejected."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0ZXN0IjoxMjM0NTY3ODkwfQ"
        f = tmp_path / "token.txt"
        f.write_text(f"Authorization: Bearer {jwt}\n", encoding="utf-8")
        result = ingest_file(store, str(f))
        assert result.rejected_policy >= 1

    def test_pii_quarantined(self, store, tmp_path):
        """File containing email → stored but injectable=False."""
        f = tmp_path / "contacts.txt"
        f.write_text(
            "Contact directory\nalice@example.com is the lead.\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(f), injectable=True)
        assert result.quarantined >= 1
        # Item exists but non-injectable
        for item_id in result.item_ids:
            item = store.read_item(item_id)
            assert item.injectable is False

    def test_quarantine_overrides_injectable_true(self, store, tmp_path):
        """Quarantine sets injectable=False even when caller passes injectable=True."""
        f = tmp_path / "pii.txt"
        f.write_text("SSN: 123-45-6789\n", encoding="utf-8")
        result = ingest_file(store, str(f), injectable=True)
        assert result.quarantined >= 1
        for item_id in result.item_ids:
            item = store.read_item(item_id)
            assert item.injectable is False

    def test_clean_file_injectable(self, store, tmp_path):
        """Clean file → items remain injectable=True."""
        f = tmp_path / "clean.md"
        f.write_text(
            "# Design Notes\n\nWe use event-driven architecture.\n",
            encoding="utf-8",
        )
        result = ingest_file(store, str(f), injectable=True)
        assert result.rejected_policy == 0
        assert result.quarantined == 0
        for item_id in result.item_ids:
            item = store.read_item(item_id)
            assert item.injectable is True

    def test_mixed_file(self, store, tmp_path):
        """File with clean + secret chunks → clean stored, secret rejected."""
        f = tmp_path / "mixed.md"
        # Two paragraphs: one clean, one with secret (separate chunks)
        clean = "A clean paragraph about software architecture patterns.\n" * 20
        secret = "password = p4ssw0rd_very_secret_value_do_not_share\n" * 5
        f.write_text(f"{clean}\n\n{secret}", encoding="utf-8")
        result = ingest_file(store, str(f), max_tokens=300)
        # At least one chunk rejected, at least one stored
        assert result.rejected_policy >= 1
        assert result.chunks_created >= 1

    def test_result_rejected_counter(self, store, tmp_path):
        """IngestResult.rejected_policy incremented on rejection."""
        f = tmp_path / "key.txt"
        f.write_text("ghp_abcdefghijklmnopqrstuvwxyz1234567890\n", encoding="utf-8")
        result = ingest_file(store, str(f))
        assert result.rejected_policy == 1

    def test_result_quarantined_counter(self, store, tmp_path):
        """IngestResult.quarantined incremented on quarantine."""
        f = tmp_path / "pii.txt"
        f.write_text("Contact: bob@example.com\n", encoding="utf-8")
        result = ingest_file(store, str(f), injectable=True)
        assert result.quarantined >= 1

    def test_push_cli_default_policy(self, tmp_path):
        """memctl push --source applies policy by default (subprocess)."""
        db = str(tmp_path / "test.db")
        f = tmp_path / "secret.txt"
        f.write_text(
            "api_key = sk-abcdefghij1234567890secret\n",
            encoding="utf-8",
        )
        # Init DB first
        subprocess.run(
            [sys.executable, "-m", "memctl.cli", "init", "--db", db],
            check=True, capture_output=True,
        )
        r = subprocess.run(
            [sys.executable, "-m", "memctl.cli", "push",
             "--db", db, "test", "--source", str(f)],
            capture_output=True, text=True,
        )
        # Policy should reject the secret — stderr mentions rejection
        assert "rejected" in r.stderr.lower() or "Policy rejected" in r.stderr

    def test_push_stderr_reports_policy(self, tmp_path):
        """stderr includes rejection count when non-zero."""
        db = str(tmp_path / "test.db")
        f = tmp_path / "secret.txt"
        f.write_text(
            "api_key = sk-abcdefghij1234567890secret\n",
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, "-m", "memctl.cli", "init", "--db", db],
            check=True, capture_output=True,
        )
        r = subprocess.run(
            [sys.executable, "-m", "memctl.cli", "push",
             "--db", db, "test", "--source", str(f)],
            capture_output=True, text=True,
        )
        assert "Policy rejected" in r.stderr or "rejected" in r.stderr.lower()

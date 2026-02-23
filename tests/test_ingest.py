"""
Tests for memctl.ingest — Chunking, file dedup, source resolution.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
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

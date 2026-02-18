"""
Tests for memctl.types — MemoryItem, MemoryProposal, MemoryEvent, MemoryLink.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import pytest

from memctl.types import (
    CorpusMetadata,
    MemoryEvent,
    MemoryItem,
    MemoryLink,
    MemoryProposal,
    MemoryProvenance,
    VALID_TIERS,
    VALID_TYPES,
    VALID_VALIDATION_STATES,
    _generate_id,
    _now_iso,
    content_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_generate_id_prefix(self):
        mid = _generate_id("MEM")
        assert mid.startswith("MEM-")
        assert len(mid) >= 16

    def test_generate_id_uniqueness(self):
        ids = {_generate_id("MEM") for _ in range(100)}
        assert len(ids) == 100

    def test_now_iso_format(self):
        ts = _now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts  # timezone-aware

    def test_content_hash_deterministic(self):
        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_content_hash_different(self):
        h1 = content_hash("hello")
        h2 = content_hash("world")
        assert h1 != h2


# ---------------------------------------------------------------------------
# MemoryProvenance
# ---------------------------------------------------------------------------


class TestMemoryProvenance:
    def test_default(self):
        p = MemoryProvenance()
        assert p.source_kind == "chat"
        assert p.source_id == ""
        assert p.chunk_ids == []
        assert p.content_hashes == []

    def test_round_trip(self):
        p = MemoryProvenance(
            source_kind="doc", source_id="/path/to/file.md",
            chunk_ids=["c1", "c2"], content_hashes=["sha256:abc"],
        )
        d = p.to_dict()
        p2 = MemoryProvenance.from_dict(d)
        assert p2.source_kind == "doc"
        assert p2.source_id == "/path/to/file.md"
        assert p2.chunk_ids == ["c1", "c2"]


# ---------------------------------------------------------------------------
# MemoryItem
# ---------------------------------------------------------------------------


class TestMemoryItem:
    def test_defaults(self):
        item = MemoryItem(title="Test", content="Hello")
        assert item.id.startswith("MEM-")
        assert item.tier == "stm"
        assert item.type == "note"
        assert item.validation == "unverified"
        assert item.confidence == 0.5
        assert item.injectable is True
        assert item.archived is False

    def test_content_hash_computed(self):
        item = MemoryItem(title="Test", content="deterministic")
        assert item.content_hash.startswith("sha256:")
        # Same content → same hash
        item2 = MemoryItem(title="Different", content="deterministic")
        assert item.content_hash == item2.content_hash

    def test_to_dict_round_trip(self):
        item = MemoryItem(
            tier="mtm", type="fact", title="Pi", content="3.14159",
            tags=["math", "constant"], entities=["pi"],
            confidence=0.95, scope="global",
        )
        d = item.to_dict()
        assert d["tier"] == "mtm"
        assert d["type"] == "fact"
        assert d["tags"] == ["math", "constant"]

        item2 = MemoryItem.from_dict(d)
        assert item2.title == "Pi"
        assert item2.tier == "mtm"
        assert item2.confidence == 0.95

    def test_from_dict_extra_fields_ignored(self):
        d = {"title": "Test", "content": "x", "unknown_field": "ignore"}
        item = MemoryItem.from_dict(d)
        assert item.title == "Test"

    def test_tags_are_list(self):
        item = MemoryItem(title="T", content="C", tags=["a", "b"])
        assert isinstance(item.tags, list)
        assert len(item.tags) == 2

    def test_unique_ids(self):
        items = [MemoryItem(title=f"Item {i}", content=f"Content {i}") for i in range(50)]
        ids = {it.id for it in items}
        assert len(ids) == 50


# ---------------------------------------------------------------------------
# MemoryProposal
# ---------------------------------------------------------------------------


class TestMemoryProposal:
    def test_defaults(self):
        p = MemoryProposal(title="Test", content="Hello")
        assert p.type == "note"
        assert p.tags == []
        assert p.why_store == ""
        assert p.scope == "project"

    def test_to_dict(self):
        p = MemoryProposal(
            title="Decision", content="Use SQLite", type="decision",
            tags=["db"], why_store="Critical choice",
        )
        d = p.to_dict()
        assert d["type"] == "decision"
        assert d["why_store"] == "Critical choice"

    def test_from_dict(self):
        d = {"title": "T", "content": "C", "type": "fact", "tags": ["x"]}
        p = MemoryProposal.from_dict(d)
        assert p.type == "fact"
        assert p.tags == ["x"]

    def test_from_dict_unknown_fields(self):
        d = {"title": "T", "content": "C", "bogus": 42}
        p = MemoryProposal.from_dict(d)
        assert p.title == "T"

    def test_to_memory_item(self):
        p = MemoryProposal(
            title="Fact", content="Earth orbits Sun",
            type="fact", tags=["astronomy"],
            provenance_hint={"source_kind": "doc", "source_id": "textbook.md"},
        )
        item = p.to_memory_item(tier="stm", scope="project")
        assert item.tier == "stm"
        assert item.type == "fact"
        assert item.title == "Fact"
        assert item.provenance.source_kind == "doc"
        assert item.provenance.source_id == "textbook.md"
        assert item.tags == ["astronomy"]

    def test_to_memory_item_default_provenance(self):
        p = MemoryProposal(title="T", content="C")
        item = p.to_memory_item()
        assert item.provenance.source_kind == "chat"


# ---------------------------------------------------------------------------
# MemoryEvent
# ---------------------------------------------------------------------------


class TestMemoryEvent:
    def test_creation(self):
        evt = MemoryEvent(action="write", item_id="MEM-123")
        assert evt.id.startswith("EVT-")
        assert evt.action == "write"
        assert evt.item_id == "MEM-123"

    def test_to_dict(self):
        evt = MemoryEvent(action="read", details={"key": "value"})
        d = evt.to_dict()
        assert d["action"] == "read"
        assert d["details"]["key"] == "value"


# ---------------------------------------------------------------------------
# MemoryLink
# ---------------------------------------------------------------------------


class TestMemoryLink:
    def test_creation(self):
        link = MemoryLink(src_id="A", dst_id="B", rel="supersedes")
        assert link.src_id == "A"
        assert link.dst_id == "B"
        assert link.rel == "supersedes"

    def test_round_trip(self):
        link = MemoryLink(src_id="X", dst_id="Y", rel="supports")
        d = link.to_dict()
        link2 = MemoryLink.from_dict(d)
        assert link2.src_id == "X"
        assert link2.rel == "supports"


# ---------------------------------------------------------------------------
# CorpusMetadata
# ---------------------------------------------------------------------------


class TestCorpusMetadata:
    def test_creation(self):
        cm = CorpusMetadata(
            corpus_id="CORP-1", corpus_label="Test corpus",
            doc_count=5, item_count=20, scope="project",
        )
        assert cm.corpus_id == "CORP-1"
        assert cm.doc_count == 5

    def test_round_trip(self):
        cm = CorpusMetadata(
            corpus_id="C1", corpus_label="L", parent_corpus_id="P1",
            doc_count=1, item_count=2, scope="s",
        )
        d = cm.to_dict()
        cm2 = CorpusMetadata.from_dict(d)
        assert cm2.parent_corpus_id == "P1"


# ---------------------------------------------------------------------------
# Validation sets
# ---------------------------------------------------------------------------


class TestValidationSets:
    def test_valid_tiers(self):
        assert "stm" in VALID_TIERS
        assert "mtm" in VALID_TIERS
        assert "ltm" in VALID_TIERS

    def test_valid_types(self):
        assert "fact" in VALID_TYPES
        assert "note" in VALID_TYPES
        assert "decision" in VALID_TYPES

    def test_valid_validation_states(self):
        assert "unverified" in VALID_VALIDATION_STATES
        assert "verified" in VALID_VALIDATION_STATES

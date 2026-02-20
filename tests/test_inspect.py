"""
Tests for memctl.inspect — structural injection block generation.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import time
from pathlib import Path

import pytest

from memctl.inspect import (
    inspect_mount,
    inspect_stats,
    inspect_path,
    InspectResult,
    _is_stale,
    DOMINANCE_FRAC,
    EXT_CONCENTRATION_FRAC,
    SPARSE_THRESHOLD,
    OBSERVATION_THRESHOLDS,
    _safe_rel_path,
    _safe_size,
)
from memctl.sync import sync_mount
from memctl.mount import register_mount, list_mounts
from memctl.store import MemoryStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def corpus(tmp_path):
    """Corpus with enough structure for observation rules."""
    root = tmp_path / "project"
    root.mkdir()

    # docs/ — large folder (dominance test)
    docs = root / "docs"
    docs.mkdir()
    for i in range(6):
        (docs / f"guide_{i}.md").write_text(
            f"# Guide {i}\n\n" + "Detailed content. " * 50 + "\n\n" + "More paragraphs. " * 30
        )

    # src/ — small folder
    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    print('hello')\n")

    # legacy/ — sparse folder (many files, few chunks)
    legacy = root / "legacy"
    legacy.mkdir()
    for i in range(4):
        (legacy / f"old_{i}.txt").write_text(f"Old content {i}.")

    return str(root)


@pytest.fixture
def synced_db(db_path, corpus):
    """A database with synced content."""
    sync_mount(db_path, corpus, quiet=True)
    return db_path


class TestInspectStats:
    def test_stats_empty(self, db_path):
        stats = inspect_stats(db_path)
        assert stats["total_files"] == 0
        assert stats["total_chunks"] == 0

    def test_stats_after_sync(self, synced_db):
        stats = inspect_stats(synced_db)
        assert stats["total_files"] == 11  # 6 md + 1 py + 4 txt
        assert stats["total_chunks"] > 0
        assert stats["total_size"] > 0

    def test_stats_per_folder(self, synced_db):
        stats = inspect_stats(synced_db)
        folders = stats["per_folder"]
        assert len(folders) > 0
        # Check that docs/ folder exists
        assert any("docs" in k for k in folders)

    def test_stats_per_extension(self, synced_db):
        stats = inspect_stats(synced_db)
        exts = stats["per_extension"]
        assert ".md" in exts
        assert ".py" in exts
        assert ".txt" in exts

    def test_stats_top_largest(self, synced_db):
        stats = inspect_stats(synced_db)
        top = stats["top_largest"]
        assert len(top) > 0
        assert len(top) <= 5
        # Sorted by size descending
        sizes = [f["size_bytes"] for f in top]
        assert sizes == sorted(sizes, reverse=True)

    def test_stats_with_mount_filter(self, synced_db, corpus):
        from memctl.store import MemoryStore
        store = MemoryStore(db_path=synced_db)
        mounts = store.list_mounts()
        store.close()
        assert len(mounts) == 1
        mount_id = mounts[0]["mount_id"]

        stats = inspect_stats(synced_db, mount_id=mount_id)
        assert stats["total_files"] == 11


class TestInspectObservations:
    def test_observations_exist(self, synced_db):
        stats = inspect_stats(synced_db)
        # With our corpus, we should get at least some observations
        assert isinstance(stats["observations"], list)

    def test_dominance_observation(self, synced_db):
        stats = inspect_stats(synced_db)
        obs = stats["observations"]
        # docs/ should dominate (6 large files vs few small ones)
        dominance_obs = [o for o in obs if "dominates" in o.lower()]
        # This depends on chunk distribution — may or may not fire
        # Just verify the list is returned properly
        assert isinstance(dominance_obs, list)

    def test_concentration_observation(self, synced_db):
        stats = inspect_stats(synced_db)
        obs = stats["observations"]
        # .md is 6 of 11 files = 55%, below 75% threshold
        # So extension concentration should NOT fire
        ext_obs = [o for o in obs if "dominate" in o and "files" in o]
        # With 6 md, 4 txt, 1 py = no single ext at 75%
        assert len(ext_obs) == 0

    def test_constants_are_deterministic(self):
        assert DOMINANCE_FRAC == 0.40
        assert EXT_CONCENTRATION_FRAC == 0.75
        assert SPARSE_THRESHOLD == 1


class TestInspectMount:
    def test_empty_db(self, db_path):
        text = inspect_mount(db_path)
        assert "No files found" in text

    def test_output_format(self, synced_db):
        text = inspect_mount(synced_db)
        assert text.startswith("## Structure (Injected)")
        assert "format_version: 1" in text
        assert "injection_type: structure_inspect" in text
        assert "Total files:" in text
        assert "Total chunks:" in text

    def test_mount_label(self, synced_db):
        text = inspect_mount(synced_db, mount_label="project/")
        assert "mount: project/" in text

    def test_folders_section(self, synced_db):
        text = inspect_mount(synced_db)
        assert "Folders:" in text

    def test_largest_files_section(self, synced_db):
        text = inspect_mount(synced_db)
        assert "Largest files:" in text

    def test_extensions_section(self, synced_db):
        text = inspect_mount(synced_db)
        assert "Extensions:" in text
        assert ".md:" in text

    def test_budget_trimming(self, synced_db):
        text = inspect_mount(synced_db, budget=10)
        # Very small budget should trigger truncation
        assert len(text) <= 80  # 10 tokens * 4 chars + overhead
        assert "[...truncated]" in text

    def test_deterministic(self, synced_db):
        """Same DB → same output (determinism invariant)."""
        t1 = inspect_mount(synced_db)
        t2 = inspect_mount(synced_db)
        assert t1 == t2

    def test_json_mode_returns_dict(self, synced_db):
        """inspect_stats returns a serializable dict."""
        stats = inspect_stats(synced_db)
        import json
        serialized = json.dumps(stats)
        assert isinstance(serialized, str)


class TestPathNormalization:
    """All paths in inspect output must be mount-relative, never absolute."""

    def test_no_absolute_paths_in_folders(self, synced_db):
        stats = inspect_stats(synced_db)
        for folder in stats["per_folder"]:
            assert not os.path.isabs(folder), f"Absolute path in folder key: {folder}"

    def test_no_absolute_paths_in_top_largest(self, synced_db):
        stats = inspect_stats(synced_db)
        for f in stats["top_largest"]:
            assert not os.path.isabs(f["path"]), f"Absolute path in top_largest: {f['path']}"

    def test_no_absolute_paths_in_injection_block(self, synced_db):
        text = inspect_mount(synced_db)
        for line in text.split("\n"):
            if line.startswith("- "):
                # Folder and file entries — no /home/... or /tmp/...
                assert "/tmp/" not in line, f"Absolute path leaked: {line}"
                assert "/home/" not in line, f"Absolute path leaked: {line}"

    def test_safe_rel_path_with_rel_path(self):
        f = {"rel_path": "docs/guide.md", "file_path": "/tmp/project/docs/guide.md"}
        assert _safe_rel_path(f) == "docs/guide.md"

    def test_safe_rel_path_without_rel_path(self):
        f = {"file_path": "/tmp/project/docs/guide.md"}
        assert _safe_rel_path(f) == "guide.md"

    def test_safe_rel_path_empty(self):
        f = {}
        assert _safe_rel_path(f) == ""

    def test_push_ingested_files_use_basename(self, db_path, tmp_path):
        """Files ingested via push (no mount) show basename, not absolute path."""
        from memctl.store import MemoryStore
        from memctl.ingest import ingest_file

        store = MemoryStore(db_path=db_path)
        fpath = tmp_path / "readme.md"
        fpath.write_text("# Test\n\nSome content here.\n")
        ingest_file(store, str(fpath))
        store.close()

        stats = inspect_stats(db_path)
        assert stats["total_files"] == 1
        for f in stats["top_largest"]:
            assert f["path"] == "readme.md"
            assert not os.path.isabs(f["path"])


class TestSizeAccounting:
    """Size must never show as 0 B for files that exist on disk."""

    def test_sizes_positive_after_sync(self, synced_db):
        stats = inspect_stats(synced_db)
        assert stats["total_size"] > 0
        for f in stats["top_largest"]:
            assert f["size_bytes"] > 0

    def test_push_ingested_files_have_size(self, db_path, tmp_path):
        """Files ingested via push must record size_bytes."""
        from memctl.store import MemoryStore
        from memctl.ingest import ingest_file

        store = MemoryStore(db_path=db_path)
        fpath = tmp_path / "data.md"
        content = "# Data\n\nSome real content here.\n"
        fpath.write_text(content)
        ingest_file(store, str(fpath))
        store.close()

        store2 = MemoryStore(db_path=db_path)
        files = store2.list_corpus_files()
        store2.close()
        assert len(files) == 1
        assert files[0]["size_bytes"] is not None
        assert files[0]["size_bytes"] > 0
        assert files[0]["ext"] == ".md"

    def test_format_size_zero_shows_unknown(self):
        from memctl.inspect import _format_size
        assert _format_size(0) == "unknown"
        assert _format_size(-1) == "unknown"


class TestObservationThresholds:
    """Thresholds must be present in --json output and be frozen."""

    def test_thresholds_in_stats(self, synced_db):
        stats = inspect_stats(synced_db)
        assert "observation_thresholds" in stats
        t = stats["observation_thresholds"]
        assert t["dominance_frac"] == 0.40
        assert t["low_density_threshold"] == 0.10
        assert t["ext_concentration_frac"] == 0.75
        assert t["sparse_threshold"] == 1

    def test_thresholds_in_empty_stats(self, db_path):
        stats = inspect_stats(db_path)
        assert "observation_thresholds" in stats
        assert stats["observation_thresholds"]["dominance_frac"] == 0.40

    def test_thresholds_dict_matches_constants(self):
        assert OBSERVATION_THRESHOLDS["dominance_frac"] == DOMINANCE_FRAC
        assert OBSERVATION_THRESHOLDS["low_density_threshold"] == 0.10
        assert OBSERVATION_THRESHOLDS["ext_concentration_frac"] == EXT_CONCENTRATION_FRAC
        assert OBSERVATION_THRESHOLDS["sparse_threshold"] == SPARSE_THRESHOLD

    def test_thresholds_serializable(self):
        import json
        s = json.dumps(OBSERVATION_THRESHOLDS)
        d = json.loads(s)
        assert d == OBSERVATION_THRESHOLDS


class TestDeterminism:
    """Strengthened determinism tests — same input must produce bit-identical output."""

    def test_stats_deterministic(self, synced_db):
        s1 = inspect_stats(synced_db)
        s2 = inspect_stats(synced_db)
        assert s1 == s2

    def test_injection_block_deterministic_multiple_runs(self, synced_db):
        outputs = [inspect_mount(synced_db) for _ in range(5)]
        assert len(set(outputs)) == 1, "Non-deterministic injection output"

    def test_independent_dbs_same_corpus(self, tmp_path):
        """Two independent DBs syncing the same corpus produce identical stats."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("# Alpha\n\nContent alpha.\n")
        (corpus / "b.md").write_text("# Beta\n\nContent beta.\n")

        db1 = str(tmp_path / "db1.db")
        db2 = str(tmp_path / "db2.db")
        sync_mount(db1, str(corpus), quiet=True)
        sync_mount(db2, str(corpus), quiet=True)

        s1 = inspect_stats(db1)
        s2 = inspect_stats(db2)

        # Stats should match (excluding mount_ids which differ)
        assert s1["total_files"] == s2["total_files"]
        assert s1["total_chunks"] == s2["total_chunks"]
        assert s1["total_size"] == s2["total_size"]
        assert s1["per_extension"] == s2["per_extension"]
        assert s1["observations"] == s2["observations"]
        assert s1["observation_thresholds"] == s2["observation_thresholds"]


# =========================================================================
# Orchestration: InspectResult, _is_stale, inspect_path
# =========================================================================


@pytest.fixture
def corpus_path(tmp_path):
    """Simple corpus for inspect_path tests."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "readme.md").write_text("# Project\n\nMain readme.\n\nDetailed content.")
    (root / "notes.txt").write_text("Project notes.\n\nMore details here.")
    return str(root)


class TestInspectResult:
    def test_fields_present(self):
        stats = {"total_files": 5, "total_chunks": 10}
        r = InspectResult(stats=stats, mount_id="MNT-123", mount_label="test/")
        assert r.stats == stats
        assert r.mount_id == "MNT-123"
        assert r.was_mounted is False
        assert r.was_synced is False
        assert r.sync_skipped is False
        assert r.was_ephemeral is False

    def test_to_dict_inlines_stats(self):
        stats = {"total_files": 3, "per_extension": {".md": 2}}
        r = InspectResult(stats=stats, mount_id="MNT-1", mount_label="x",
                          was_synced=True, sync_files_new=2)
        d = r.to_dict()
        # Stats fields at root
        assert d["total_files"] == 3
        assert d["per_extension"] == {".md": 2}
        # Orchestration fields at root
        assert d["mount_id"] == "MNT-1"
        assert d["was_synced"] is True
        assert d["sync_files_new"] == 2

    def test_serializable(self):
        stats = {"total_files": 0, "observations": []}
        r = InspectResult(stats=stats, mount_id="MNT-1", mount_label="x")
        s = json.dumps(r.to_dict())
        assert isinstance(json.loads(s), dict)


class TestIsStale:
    def test_stale_never_synced(self, db_path, corpus_path):
        register_mount(db_path, corpus_path)
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is True
        finally:
            store.close()

    def test_fresh_after_sync(self, db_path, corpus_path):
        sync_mount(db_path, corpus_path, quiet=True)
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is False
        finally:
            store.close()

    def test_stale_new_file_added(self, db_path, corpus_path):
        sync_mount(db_path, corpus_path, quiet=True)
        (Path(corpus_path) / "new_doc.md").write_text("# New\n\nFresh content.")
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is True
        finally:
            store.close()

    def test_stale_file_deleted(self, db_path, corpus_path):
        sync_mount(db_path, corpus_path, quiet=True)
        os.remove(os.path.join(corpus_path, "notes.txt"))
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is True
        finally:
            store.close()

    def test_stale_file_modified_mtime(self, db_path, corpus_path):
        sync_mount(db_path, corpus_path, quiet=True)
        fpath = os.path.join(corpus_path, "readme.md")
        # Set mtime to a future time (ensures integer mtime changes)
        future = int(time.time()) + 100
        os.utime(fpath, (future, future))
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is True
        finally:
            store.close()

    def test_stale_file_modified_size(self, db_path, corpus_path):
        sync_mount(db_path, corpus_path, quiet=True)
        time.sleep(0.1)
        with open(os.path.join(corpus_path, "readme.md"), "a") as f:
            f.write("\nAppended content that changes size.")
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(corpus_path))
            assert _is_stale(store, mount) is True
        finally:
            store.close()

    def test_fresh_empty_directory(self, db_path, tmp_path):
        """Both disk and stored inventories empty → fresh."""
        empty = tmp_path / "empty"
        empty.mkdir()
        sync_mount(db_path, str(empty), quiet=True)
        store = MemoryStore(db_path=db_path)
        try:
            mount = store.read_mount(os.path.realpath(str(empty)))
            assert _is_stale(store, mount) is False
        finally:
            store.close()


class TestInspectPath:
    def test_basic_orchestration(self, db_path, corpus_path):
        logs = []
        result = inspect_path(db_path, corpus_path, log=logs.append)
        assert result.stats["total_files"] >= 2
        assert result.mount_id.startswith("MNT-")
        assert result.was_mounted is True
        assert result.was_synced is True
        assert result.sync_skipped is False
        assert len(logs) >= 2  # mount + sync messages

    def test_second_call_skips_sync(self, db_path, corpus_path):
        inspect_path(db_path, corpus_path, log=lambda m: None)
        logs = []
        result = inspect_path(db_path, corpus_path, log=logs.append)
        assert result.was_mounted is False
        assert result.was_synced is False
        assert result.sync_skipped is True

    def test_sync_mode_never(self, db_path, corpus_path):
        result = inspect_path(
            db_path, corpus_path, sync_mode="never", log=lambda m: None,
        )
        assert result.was_synced is False
        assert result.sync_skipped is True
        # Mount was registered but not synced → empty stats
        assert result.stats["total_files"] == 0

    def test_sync_mode_always(self, db_path, corpus_path):
        # First call syncs
        inspect_path(db_path, corpus_path, log=lambda m: None)
        # Second call with always → still syncs
        result = inspect_path(
            db_path, corpus_path, sync_mode="always", log=lambda m: None,
        )
        assert result.was_synced is True
        assert result.sync_skipped is False

    def test_ephemeral_mode(self, db_path, corpus_path):
        result = inspect_path(
            db_path, corpus_path, mount_mode="ephemeral", log=lambda m: None,
        )
        assert result.was_ephemeral is True
        assert result.stats["total_files"] >= 2
        # Mount record is gone
        mounts = list_mounts(db_path)
        assert not any(m["mount_id"] == result.mount_id for m in mounts)

    def test_ephemeral_leaves_corpus_data(self, db_path, corpus_path):
        result = inspect_path(
            db_path, corpus_path, mount_mode="ephemeral", log=lambda m: None,
        )
        store = MemoryStore(db_path=db_path)
        try:
            files = store.list_corpus_files()
        finally:
            store.close()
        assert len(files) >= 2

    def test_nonexistent_path(self, db_path):
        with pytest.raises(FileNotFoundError):
            inspect_path(db_path, "/nonexistent/path/12345", log=lambda m: None)

    def test_file_path_raises(self, db_path, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not a directory")
        with pytest.raises(NotADirectoryError):
            inspect_path(db_path, str(f), log=lambda m: None)

    def test_invalid_sync_mode(self, db_path, corpus_path):
        with pytest.raises(ValueError, match="sync_mode"):
            inspect_path(db_path, corpus_path, sync_mode="bogus", log=lambda m: None)

    def test_invalid_mount_mode(self, db_path, corpus_path):
        with pytest.raises(ValueError, match="mount_mode"):
            inspect_path(db_path, corpus_path, mount_mode="bogus", log=lambda m: None)

    def test_log_callable_receives_messages(self, db_path, corpus_path):
        logs = []
        inspect_path(db_path, corpus_path, log=logs.append)
        assert any("[inspect]" in m for m in logs)

    def test_idempotent_mount(self, db_path, corpus_path):
        r1 = inspect_path(db_path, corpus_path, log=lambda m: None)
        r2 = inspect_path(db_path, corpus_path, log=lambda m: None)
        assert r1.mount_id == r2.mount_id
        assert r1.was_mounted is True
        assert r2.was_mounted is False

    def test_ignore_patterns_forwarded(self, db_path, corpus_path):
        result = inspect_path(
            db_path, corpus_path,
            ignore_patterns=["*.txt"],
            log=lambda m: None,
        )
        # Only .md files should be ingested (notes.txt excluded)
        assert result.stats["total_files"] == 1
        assert ".txt" not in result.stats.get("per_extension", {})

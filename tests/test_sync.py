"""
Tests for memctl.sync — file scanning and delta synchronization.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
import time
import pytest

from memctl.sync import scan_mount, sync_mount, sync_all, FileInfo, ScanResult, SyncResult
from memctl.mount import register_mount, list_mounts
from memctl.store import MemoryStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def corpus(tmp_path):
    """Create a small corpus folder."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    (folder / "arch.md").write_text("# Architecture\n\nLayered system design.\n\nModules interact via APIs.")
    (folder / "security.md").write_text("# Security\n\nAuthentication and authorization.\n\nOAuth2 flows.")
    (folder / "notes.txt").write_text("General notes about the project.\n\nTODO items here.")
    return str(folder)


@pytest.fixture
def corpus_with_subdirs(tmp_path):
    """Corpus with subdirectories."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("# Project\n\nMain readme.")

    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    print('hello')\n")
    (src / "utils.py").write_text("def helper():\n    return 42\n")

    docs = root / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nUser guide content.\n\nStep by step.")
    (docs / "api.md").write_text("# API\n\nEndpoint documentation.\n\nGET /users")

    return str(root)


class TestScanMount:
    def test_scan_finds_files(self, corpus):
        result = scan_mount(corpus)
        assert isinstance(result, ScanResult)
        assert len(result.files) == 3
        assert result.total_size > 0

    def test_scan_collects_extensions(self, corpus):
        result = scan_mount(corpus)
        assert ".md" in result.extensions
        assert ".txt" in result.extensions
        assert result.extensions[".md"] == 2

    def test_scan_file_metadata(self, corpus):
        result = scan_mount(corpus)
        for fi in result.files:
            assert isinstance(fi, FileInfo)
            assert os.path.isabs(fi.abs_path)
            assert fi.sha256 is None  # deferred — computed lazily during sync
            assert fi.size_bytes > 0
            assert fi.mtime_epoch > 0
            assert fi.ext in (".md", ".txt")

    def test_scan_ignore_patterns(self, corpus):
        result = scan_mount(corpus, ignore_patterns=["*.txt"])
        assert len(result.files) == 2
        exts = {f.ext for f in result.files}
        assert ".txt" not in exts

    def test_scan_ignore_subdirectory(self, corpus_with_subdirs):
        result = scan_mount(corpus_with_subdirs, ignore_patterns=["src/*"])
        paths = {f.rel_path for f in result.files}
        assert not any("src/" in p for p in paths)

    def test_scan_relative_paths(self, corpus_with_subdirs):
        result = scan_mount(corpus_with_subdirs)
        rel_paths = {f.rel_path for f in result.files}
        assert "README.md" in rel_paths
        assert os.path.join("src", "main.py") in rel_paths
        assert os.path.join("docs", "guide.md") in rel_paths

    def test_scan_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = scan_mount(str(empty))
        assert len(result.files) == 0
        assert result.total_size == 0

    def test_scan_skips_unknown_extensions(self, tmp_path):
        folder = tmp_path / "mixed"
        folder.mkdir()
        (folder / "data.bin").write_bytes(b"\x00\x01\x02")
        (folder / "note.md").write_text("# Note\n\nContent.")
        result = scan_mount(str(folder))
        assert len(result.files) == 1
        assert result.files[0].ext == ".md"


class TestSyncMount:
    def test_sync_creates_chunks(self, db_path, corpus):
        result = sync_mount(db_path, corpus, quiet=True)
        assert isinstance(result, SyncResult)
        assert result.files_scanned == 3
        assert result.files_new == 3
        assert result.chunks_created > 0

    def test_sync_auto_registers_mount(self, db_path, corpus):
        sync_mount(db_path, corpus, quiet=True)
        mounts = list_mounts(db_path)
        assert len(mounts) == 1
        assert mounts[0]["path"] == os.path.realpath(corpus)

    def test_sync_updates_last_sync_at(self, db_path, corpus):
        sync_mount(db_path, corpus, quiet=True)
        mounts = list_mounts(db_path)
        assert mounts[0]["last_sync_at"] is not None

    def test_sync_delta_skips_unchanged(self, db_path, corpus):
        r1 = sync_mount(db_path, corpus, quiet=True)
        assert r1.files_new == 3

        r2 = sync_mount(db_path, corpus, quiet=True)
        assert r2.files_unchanged == 3
        assert r2.files_new == 0
        assert r2.files_changed == 0
        assert r2.chunks_created == 0

    def test_sync_detects_changes(self, db_path, corpus):
        sync_mount(db_path, corpus, quiet=True)

        # Modify a file (need mtime to change)
        time.sleep(0.1)
        with open(os.path.join(corpus, "arch.md"), "w") as f:
            f.write("# Architecture v2\n\nCompletely rewritten.\n")

        r2 = sync_mount(db_path, corpus, quiet=True)
        assert r2.files_changed >= 1

    def test_sync_full_mode(self, db_path, corpus):
        sync_mount(db_path, corpus, quiet=True)

        r2 = sync_mount(db_path, corpus, delta=False, quiet=True)
        # Full mode: all files are re-processed (not "unchanged")
        assert r2.files_unchanged == 0

    def test_sync_with_existing_mount(self, db_path, corpus):
        register_mount(db_path, corpus, name="mycorpus")
        result = sync_mount(db_path, corpus, quiet=True)
        assert result.files_scanned == 3
        # Mount should still be there
        mounts = list_mounts(db_path)
        assert len(mounts) == 1

    def test_sync_corpus_hash_has_mount_metadata(self, db_path, corpus):
        sync_mount(db_path, corpus, quiet=True)
        store = MemoryStore(db_path=db_path)
        try:
            files = store.list_corpus_files()
            assert len(files) == 3
            for f in files:
                assert f["mount_id"] is not None
                assert f["mount_id"].startswith("MNT-")
                assert f["rel_path"] is not None
                assert f["ext"] in (".md", ".txt")
                assert f["size_bytes"] > 0
                assert f["mtime_epoch"] > 0
        finally:
            store.close()

    def test_sync_to_dict(self, db_path, corpus):
        result = sync_mount(db_path, corpus, quiet=True)
        d = result.to_dict()
        assert "mount_path" in d
        assert "files_scanned" in d
        assert "chunks_created" in d


class TestSyncAll:
    def test_sync_all_multiple_mounts(self, db_path, tmp_path):
        d1 = tmp_path / "a"
        d1.mkdir()
        (d1 / "file1.md").write_text("# A\n\nContent A.")
        d2 = tmp_path / "b"
        d2.mkdir()
        (d2 / "file2.md").write_text("# B\n\nContent B.")

        register_mount(db_path, str(d1), name="a")
        register_mount(db_path, str(d2), name="b")

        results = sync_all(db_path, quiet=True)
        assert len(results) == 2
        for path, r in results.items():
            assert r.files_scanned >= 1

    def test_sync_all_empty(self, db_path):
        results = sync_all(db_path, quiet=True)
        assert results == {}

    def test_sync_all_skips_missing_paths(self, db_path, tmp_path):
        d1 = tmp_path / "exists"
        d1.mkdir()
        (d1 / "file.md").write_text("# Test\n\nContent.")
        register_mount(db_path, str(d1), name="exists")

        # Manually insert a mount with a non-existent path
        store = MemoryStore(db_path=db_path)
        store.write_mount("/nonexistent/path/12345", name="ghost")
        store.close()

        results = sync_all(db_path, quiet=True)
        # Only the existing mount should be synced
        assert len(results) == 1


class TestSyncIdempotency:
    """Sync must be idempotent — multiple syncs of unchanged files produce zero work."""

    def test_triple_sync_no_new_work(self, db_path, corpus):
        r1 = sync_mount(db_path, corpus, quiet=True)
        assert r1.files_new == 3

        r2 = sync_mount(db_path, corpus, quiet=True)
        assert r2.files_new == 0
        assert r2.files_changed == 0
        assert r2.files_unchanged == 3
        assert r2.chunks_created == 0

        r3 = sync_mount(db_path, corpus, quiet=True)
        assert r3.files_new == 0
        assert r3.files_changed == 0
        assert r3.files_unchanged == 3
        assert r3.chunks_created == 0

    def test_touch_without_content_change(self, db_path, corpus):
        """Touch a file (update mtime) without changing content.
        Tier 3 should detect same sha256 and skip ingest."""
        sync_mount(db_path, corpus, quiet=True)

        # Touch a file — changes mtime but not content
        fpath = os.path.join(corpus, "arch.md")
        time.sleep(0.1)
        os.utime(fpath, None)  # update mtime to now

        r2 = sync_mount(db_path, corpus, quiet=True)
        # Size+mtime changed → hash check → same hash → unchanged
        assert r2.files_unchanged == 3
        assert r2.files_changed == 0
        assert r2.chunks_created == 0

    def test_content_change_detected(self, db_path, corpus):
        """Modify file content — must be detected as changed."""
        sync_mount(db_path, corpus, quiet=True)

        fpath = os.path.join(corpus, "arch.md")
        time.sleep(0.1)
        with open(fpath, "w") as f:
            f.write("# Architecture v2\n\nNew content.\n")

        r2 = sync_mount(db_path, corpus, quiet=True)
        assert r2.files_changed >= 1
        assert r2.chunks_created >= 1

    def test_new_file_detected(self, db_path, corpus):
        """Add a new file — must be detected as new."""
        sync_mount(db_path, corpus, quiet=True)

        new_file = os.path.join(corpus, "new.md")
        with open(new_file, "w") as f:
            f.write("# New File\n\nFresh content.\n")

        r2 = sync_mount(db_path, corpus, quiet=True)
        assert r2.files_new == 1
        assert r2.files_unchanged == 3

    def test_corpus_hashes_have_size_after_sync(self, db_path, corpus):
        """Every synced file must have size_bytes and ext in corpus_hashes."""
        sync_mount(db_path, corpus, quiet=True)
        store = MemoryStore(db_path=db_path)
        try:
            for f in store.list_corpus_files():
                assert f["size_bytes"] is not None and f["size_bytes"] > 0, \
                    f"Missing size_bytes for {f['file_path']}"
                assert f["ext"] is not None, \
                    f"Missing ext for {f['file_path']}"
        finally:
            store.close()

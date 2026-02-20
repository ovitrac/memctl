"""
Tests for memctl.mount — folder mount registration.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import os
import pytest

from memctl.mount import register_mount, list_mounts, remove_mount


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_folder(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "readme.md").write_text("# Hello\n\nSome content here.")
    (folder / "notes.txt").write_text("Notes file content.")
    return str(folder)


class TestRegisterMount:
    def test_register_returns_mount_id(self, db_path, sample_folder):
        mid = register_mount(db_path, sample_folder)
        assert mid.startswith("MNT-")

    def test_register_idempotent(self, db_path, sample_folder):
        mid1 = register_mount(db_path, sample_folder)
        mid2 = register_mount(db_path, sample_folder)
        assert mid1 == mid2

    def test_register_with_name(self, db_path, sample_folder):
        mid = register_mount(db_path, sample_folder, name="docs")
        mounts = list_mounts(db_path)
        assert len(mounts) == 1
        assert mounts[0]["name"] == "docs"

    def test_register_with_ignore(self, db_path, sample_folder):
        mid = register_mount(
            db_path, sample_folder,
            ignore_patterns=["*.log", "tmp/*"],
        )
        mounts = list_mounts(db_path)
        assert mounts[0]["ignore_patterns"] == ["*.log", "tmp/*"]

    def test_register_with_lang_hint(self, db_path, sample_folder):
        mid = register_mount(db_path, sample_folder, lang_hint="fr")
        mounts = list_mounts(db_path)
        assert mounts[0]["lang_hint"] == "fr"

    def test_register_nonexistent_path(self, db_path, tmp_path):
        with pytest.raises(FileNotFoundError):
            register_mount(db_path, str(tmp_path / "nonexistent"))

    def test_register_file_not_directory(self, db_path, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not a dir")
        with pytest.raises(NotADirectoryError):
            register_mount(db_path, str(f))

    def test_register_resolves_canonical(self, db_path, sample_folder):
        """Symlinks and relative paths resolve to the same canonical path."""
        mid = register_mount(db_path, sample_folder)
        # Re-register using os.path.join with redundant component
        mid2 = register_mount(db_path, os.path.join(sample_folder, ".", ""))
        assert mid == mid2

    def test_register_stores_absolute_path(self, db_path, sample_folder):
        register_mount(db_path, sample_folder)
        mounts = list_mounts(db_path)
        assert os.path.isabs(mounts[0]["path"])


class TestListMounts:
    def test_list_empty(self, db_path):
        assert list_mounts(db_path) == []

    def test_list_multiple(self, db_path, tmp_path):
        for name in ("a", "b", "c"):
            d = tmp_path / name
            d.mkdir()
            register_mount(db_path, str(d), name=name)
        mounts = list_mounts(db_path)
        assert len(mounts) == 3
        names = {m["name"] for m in mounts}
        assert names == {"a", "b", "c"}


class TestRemoveMount:
    def test_remove_by_id(self, db_path, sample_folder):
        mid = register_mount(db_path, sample_folder, name="docs")
        assert remove_mount(db_path, mid) is True
        assert list_mounts(db_path) == []

    def test_remove_by_name(self, db_path, sample_folder):
        register_mount(db_path, sample_folder, name="docs")
        assert remove_mount(db_path, "docs") is True
        assert list_mounts(db_path) == []

    def test_remove_nonexistent(self, db_path):
        assert remove_mount(db_path, "MNT-nonexistent") is False

    def test_remove_preserves_others(self, db_path, tmp_path):
        d1 = tmp_path / "a"
        d1.mkdir()
        d2 = tmp_path / "b"
        d2.mkdir()
        register_mount(db_path, str(d1), name="a")
        register_mount(db_path, str(d2), name="b")
        remove_mount(db_path, "a")
        mounts = list_mounts(db_path)
        assert len(mounts) == 1
        assert mounts[0]["name"] == "b"

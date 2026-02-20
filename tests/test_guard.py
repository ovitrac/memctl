"""
Tests for memctl.mcp.guard — ServerGuard path validation and resource caps.

Covers invariants:
  G1: validate_db_path rejects '..' traversal (pre-check, before resolve)
  G2: validate_db_path rejects symlinks escaping db-root
  G3: validate_db_path rejects absolute paths outside db-root
  G4: validate_db_path accepts valid relative paths within root
  G5: check_write_size rejects content > max_write_bytes
  G6: relative_db_path returns root-relative string (never leaks absolute)

Also: check_write_budget, check_import_batch, no-root mode, check_db_size warning.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import logging
import os
import time
from pathlib import Path

import pytest

from memctl.mcp.guard import GuardError, ServerGuard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_root(tmp_path):
    """Create a db-root directory with a nested subdirectory."""
    root = tmp_path / "dbroot"
    root.mkdir()
    (root / "sub").mkdir()
    return root


@pytest.fixture
def guard(db_root):
    """Guard with a defined db-root and tight caps for testing."""
    return ServerGuard(
        db_root=db_root,
        max_write_bytes=256,
        max_write_bytes_per_minute=1024,
        max_import_items=10,
        max_db_size_mb=1,
    )


@pytest.fixture
def permissive_guard():
    """Guard with no db-root (permissive path mode)."""
    return ServerGuard(db_root=None, max_write_bytes=128)


# ---------------------------------------------------------------------------
# G1: validate_db_path rejects '..' traversal (pre-check, before resolve)
# ---------------------------------------------------------------------------


class TestG1TraversalRejection:
    def test_dotdot_simple(self, guard):
        with pytest.raises(GuardError, match="Path traversal rejected"):
            guard.validate_db_path("../etc/passwd")

    def test_dotdot_nested(self, guard):
        with pytest.raises(GuardError, match="Path traversal rejected"):
            guard.validate_db_path("sub/../../outside/db")

    def test_dotdot_at_end(self, guard):
        with pytest.raises(GuardError, match="Path traversal rejected"):
            guard.validate_db_path("sub/..")


# ---------------------------------------------------------------------------
# G2: validate_db_path rejects symlinks escaping db-root
# ---------------------------------------------------------------------------


class TestG2SymlinkEscape:
    def test_symlink_outside_root(self, guard, db_root, tmp_path):
        """A symlink inside db-root that points outside must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        escape_db = outside / "escape.db"
        escape_db.touch()

        link = db_root / "sneaky.db"
        link.symlink_to(escape_db)

        with pytest.raises(GuardError, match="Path outside db-root"):
            guard.validate_db_path("sneaky.db")

    def test_symlink_within_root_accepted(self, guard, db_root):
        """A symlink inside db-root that points to another file inside is OK."""
        real = db_root / "real.db"
        real.touch()
        link = db_root / "alias.db"
        link.symlink_to(real)

        result = guard.validate_db_path("alias.db")
        assert result == real.resolve()


# ---------------------------------------------------------------------------
# G3: validate_db_path rejects absolute paths outside db-root
# ---------------------------------------------------------------------------


class TestG3AbsolutePathRejection:
    def test_absolute_outside_root(self, guard, tmp_path):
        outside = tmp_path / "elsewhere" / "memory.db"
        with pytest.raises(GuardError, match="Path outside db-root"):
            guard.validate_db_path(str(outside))

    def test_absolute_inside_root_accepted(self, guard, db_root):
        target = db_root / "inside.db"
        result = guard.validate_db_path(str(target))
        assert result == target.resolve()


# ---------------------------------------------------------------------------
# G4: validate_db_path accepts valid relative paths within root
# ---------------------------------------------------------------------------


class TestG4ValidRelativePaths:
    def test_simple_filename(self, guard, db_root):
        result = guard.validate_db_path("memory.db")
        assert result == (db_root / "memory.db").resolve()

    def test_nested_path(self, guard, db_root):
        result = guard.validate_db_path("sub/memory.db")
        assert result == (db_root / "sub" / "memory.db").resolve()

    def test_dot_prefix(self, guard, db_root):
        """Single dot is not traversal — './file' is valid."""
        result = guard.validate_db_path("./memory.db")
        assert result == (db_root / "memory.db").resolve()


# ---------------------------------------------------------------------------
# G5: check_write_size rejects content > max_write_bytes
# ---------------------------------------------------------------------------


class TestG5WriteSizeLimit:
    def test_within_limit(self, guard):
        guard.check_write_size("x" * 100)  # 100 bytes < 256

    def test_at_exact_limit(self, guard):
        guard.check_write_size("x" * 256)  # exactly at limit

    def test_over_limit(self, guard):
        with pytest.raises(GuardError, match="Write size .* exceeds limit"):
            guard.check_write_size("x" * 257)

    def test_multibyte_utf8(self, guard):
        """Size is measured in UTF-8 bytes, not characters."""
        # Each e-acute is 2 bytes in UTF-8; 200 chars = 400 bytes > 256
        with pytest.raises(GuardError, match="Write size .* exceeds limit"):
            guard.check_write_size("\u00e9" * 200)


# ---------------------------------------------------------------------------
# G6: relative_db_path returns root-relative string (never leaks absolute)
# ---------------------------------------------------------------------------


class TestG6RelativeDbPath:
    def test_returns_relative_when_root_set(self, guard, db_root):
        resolved = (db_root / "sub" / "memory.db").resolve()
        rel = guard.relative_db_path(resolved)
        assert rel == os.path.join("sub", "memory.db")
        assert not os.path.isabs(rel)

    def test_returns_absolute_when_no_root(self, permissive_guard, tmp_path):
        resolved = (tmp_path / "some.db").resolve()
        result = permissive_guard.relative_db_path(resolved)
        assert os.path.isabs(result)

    def test_outside_path_falls_back_to_absolute(self, guard, tmp_path):
        """If resolved is outside root, relative_db_path returns absolute."""
        outside = (tmp_path / "elsewhere" / "db").resolve()
        result = guard.relative_db_path(outside)
        assert os.path.isabs(result)


# ---------------------------------------------------------------------------
# check_write_budget — per-minute cumulative tracking
# ---------------------------------------------------------------------------


class TestWriteBudget:
    def test_first_write_always_passes(self, guard):
        guard.check_write_budget("sess1", 512)

    def test_cumulative_within_budget(self, guard):
        guard.check_write_budget("sess2", 500)
        guard.check_write_budget("sess2", 500)  # total 1000 < 1024

    def test_cumulative_over_budget(self, guard):
        guard.check_write_budget("sess3", 800)
        with pytest.raises(GuardError, match="Write budget exceeded"):
            guard.check_write_budget("sess3", 300)  # total 1100 > 1024

    def test_window_resets_after_60s(self, guard, monkeypatch):
        """After 60 seconds the budget window resets."""
        timestamps = iter([1000.0, 1061.0])

        monkeypatch.setattr(time, "monotonic", lambda: next(timestamps))

        guard.check_write_budget("sess4", 1000)
        # Second call sees t=1061 — 61s past window_start=1000 — resets
        guard.check_write_budget("sess4", 1000)


# ---------------------------------------------------------------------------
# check_import_batch
# ---------------------------------------------------------------------------


class TestImportBatch:
    def test_within_limit(self, guard):
        guard.check_import_batch(10)  # exactly at limit

    def test_over_limit(self, guard):
        with pytest.raises(GuardError, match="Import batch of 11 items exceeds limit"):
            guard.check_import_batch(11)


# ---------------------------------------------------------------------------
# No-root mode (permissive)
# ---------------------------------------------------------------------------


class TestNoRootMode:
    def test_db_root_is_none(self, permissive_guard):
        assert permissive_guard.db_root is None

    def test_allows_any_absolute_path(self, permissive_guard, tmp_path):
        p = str(tmp_path / "any" / "where.db")
        result = permissive_guard.validate_db_path(p)
        assert result == Path(p).resolve()

    def test_still_rejects_dotdot(self, permissive_guard):
        """Even without root, '..' traversal is rejected for hygiene."""
        with pytest.raises(GuardError, match="Path traversal rejected"):
            permissive_guard.validate_db_path("../sneak")


# ---------------------------------------------------------------------------
# check_db_size — warning only (non-blocking)
# ---------------------------------------------------------------------------


class TestDbSizeWarning:
    def test_warns_when_over_limit(self, guard, db_root, caplog):
        big_db = db_root / "big.db"
        big_db.write_bytes(b"\x00" * (2 * 1024 * 1024))  # 2 MB > 1 MB limit

        with caplog.at_level(logging.WARNING):
            guard.check_db_size(big_db)

        assert "big.db" in caplog.text

    def test_no_warning_when_under_limit(self, guard, db_root, caplog):
        small_db = db_root / "small.db"
        small_db.write_bytes(b"\x00" * 100)

        with caplog.at_level(logging.WARNING):
            guard.check_db_size(small_db)

        assert caplog.text == ""

    def test_no_op_when_limit_unset(self, db_root, caplog):
        g = ServerGuard(db_root=db_root, max_db_size_mb=None)
        big_db = db_root / "big.db"
        big_db.write_bytes(b"\x00" * (2 * 1024 * 1024))

        with caplog.at_level(logging.WARNING):
            g.check_db_size(big_db)

        assert caplog.text == ""

    def test_missing_file_no_crash(self, guard, db_root):
        """check_db_size on a non-existent file does not raise."""
        guard.check_db_size(db_root / "nonexistent.db")

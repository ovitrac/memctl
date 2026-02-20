"""
Tests for CLI exit code conformance across all 16 commands.

Exit code contract:
    0  Success (including idempotent no-op)
    1  Operational error (bad args, empty input, policy rejection)
    2  Internal failure (unexpected exception)

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import subprocess
import sys
import pytest

PYTHON = sys.executable
CLI = [PYTHON, "-m", "memctl.cli"]


def run(args, *, env=None, stdin=None, check=False):
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        CLI + args,
        capture_output=True,
        text=True,
        env=merged_env,
        input=stdin,
        timeout=30,
    )


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test" / "memory.db")
    r = run(["init", str(tmp_path / "test"), "--db", db_path, "-q"])
    assert r.returncode == 0
    return db_path


@pytest.fixture
def populated_db(db, tmp_path):
    sample = tmp_path / "test.md"
    sample.write_text("# Test\n\nSample content.\n", encoding="utf-8")
    r = run(["push", "test", "--source", str(sample), "--db", db, "-q"])
    assert r.returncode == 0
    return db


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInitExitCodes:
    def test_init_success(self, tmp_path):
        r = run(["init", str(tmp_path / "ws"), "--db", str(tmp_path / "ws/memory.db")])
        assert r.returncode == 0

    def test_init_idempotent(self, db, tmp_path):
        """Re-init same workspace exits 0."""
        ws = os.path.dirname(db)
        r = run(["init", ws, "--db", db])
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


class TestPushExitCodes:
    def test_push_success(self, populated_db):
        r = run(["push", "test", "--db", populated_db, "-q"])
        assert r.returncode == 0

    def test_push_no_results(self, db):
        """No results returns 0 (zero matches is not an error)."""
        r = run(["push", "nonexistent_query_xyz", "--db", db, "-q"])
        assert r.returncode == 0

    def test_push_bad_source(self, db):
        """Non-existent source file returns 1."""
        r = run(["push", "query", "--source", "/nonexistent/file.txt", "--db", db, "-q"])
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearchExitCodes:
    def test_search_success(self, populated_db):
        r = run(["search", "test", "--db", populated_db, "-q"])
        assert r.returncode == 0

    def test_search_no_results(self, db):
        r = run(["search", "nonexistent_query_xyz", "--db", db, "-q"])
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShowExitCodes:
    def test_show_not_found(self, db):
        r = run(["show", "MEM-nonexistent", "--db", db, "-q"])
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStatsExitCodes:
    def test_stats_success(self, db):
        r = run(["stats", "--db", db, "-q"])
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# import exit code fix (v0.7)
# ---------------------------------------------------------------------------


class TestImportExitCodes:
    def test_import_all_errors_exit_1(self, db):
        """When all lines are malformed JSON, should exit 1."""
        bad_jsonl = "not json\nalso not json\n"
        r = run(["import", "--db", db, "-q"], stdin=bad_jsonl)
        assert r.returncode == 1

    def test_import_success_exit_0(self, db):
        """Valid JSONL import should exit 0."""
        item = {
            "id": "MEM-test123",
            "title": "Test item",
            "content": "A test note for import",
            "tier": "stm",
            "type": "note",
            "tags": [],
            "scope": "project",
        }
        jsonl = json.dumps(item) + "\n"
        r = run(["import", "--db", db, "-q"], stdin=jsonl)
        assert r.returncode == 0

    def test_import_partial_errors_exit_0(self, db):
        """Mixed valid/invalid lines should exit 0 if some succeed."""
        good = json.dumps({
            "id": "MEM-partial1",
            "title": "Good item",
            "content": "Valid content",
            "tier": "stm", "type": "note",
            "tags": [], "scope": "project",
        })
        bad = "malformed json line"
        jsonl = good + "\n" + bad + "\n"
        r = run(["import", "--db", db, "-q"], stdin=jsonl)
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


class TestExportExitCodes:
    def test_export_success(self, db):
        r = run(["export", "--db", db, "-q"])
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


class TestConsolidateExitCodes:
    def test_consolidate_success(self, db):
        r = run(["consolidate", "--db", db, "-q"])
        assert r.returncode == 0

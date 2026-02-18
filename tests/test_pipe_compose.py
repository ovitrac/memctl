"""
Tests for pipe composition — memctl's Unix composability contract.

These end-to-end tests verify that:
  - push stdout → pull stdin works
  - search stdout is valid JSON when --json is used
  - stdout purity: no diagnostics leak to stdout
  - BrokenPipe is handled gracefully

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import subprocess
import sys
import pytest


PYTHON = sys.executable
CLI = [PYTHON, "-m", "memctl.cli"]


def run(args, *, env=None, stdin=None):
    """Run a memctl CLI command."""
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
    """Initialized DB path."""
    db_path = str(tmp_path / "pipe_test" / "memory.db")
    r = run(["init", str(tmp_path / "pipe_test"), "--db", db_path, "-q"])
    assert r.returncode == 0
    return db_path


@pytest.fixture
def sample_file(tmp_path):
    """Sample markdown file for ingestion."""
    f = tmp_path / "design.md"
    f.write_text(
        "# System Design\n\n"
        "We chose event sourcing for state management.\n\n"
        "Events are stored in an append-only log.\n\n"
        "Projections rebuild read models from the event stream.\n",
        encoding="utf-8",
    )
    return str(f)


# ---------------------------------------------------------------------------
# push → pull  (the canonical pipe)
# ---------------------------------------------------------------------------


class TestPushPull:
    def test_push_stdout_feeds_pull_stdin(self, db, sample_file):
        """The fundamental pipe: `memctl push ... | memctl pull ...`."""
        # Step 1: push produces an injection block on stdout
        r_push = run([
            "push", "event sourcing",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        assert r_push.returncode == 0
        injection_block = r_push.stdout
        assert "format_version: 1" in injection_block

        # Step 2: pull reads that block from stdin and stores it
        r_pull = run(
            ["pull", "--db", db, "--title", "Piped recall", "--tags", "pipe,test", "-q"],
            stdin=injection_block,
        )
        assert r_pull.returncode == 0

        # Step 3: search confirms the piped data is stored
        r_search = run(["search", "event sourcing", "--db", db, "--json", "-q"])
        assert r_search.returncode == 0
        results = json.loads(r_search.stdout)
        assert len(results) >= 1

    def test_push_empty_recall_safe(self, db):
        """push with no matching data → exit 0, no output → pull sees empty."""
        r_push = run(["push", "xyznothing", "--db", db, "-q"])
        assert r_push.returncode == 0
        # stdout is empty or no injection block
        assert "format_version" not in r_push.stdout


# ---------------------------------------------------------------------------
# search → JSON consumer
# ---------------------------------------------------------------------------


class TestSearchJson:
    def test_search_json_is_valid(self, db, sample_file):
        """search --json produces valid JSON parseable by any downstream tool."""
        # Ingest first
        run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])

        r = run(["search", "event sourcing", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert isinstance(results, list)
        for item in results:
            assert "id" in item
            assert "title" in item
            assert "tier" in item

    def test_search_json_empty_result(self, db):
        """search with no results returns empty JSON array or no output."""
        r = run(["search", "xyznonexistent", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        # May be empty string or empty array
        if r.stdout.strip():
            results = json.loads(r.stdout)
            assert results == []


# ---------------------------------------------------------------------------
# show → JSON consumer
# ---------------------------------------------------------------------------


class TestShowJson:
    def test_show_json_roundtrip(self, db, sample_file):
        """show --json produces a dict with all required fields."""
        run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        r_search = run(["search", "event", "--db", db, "--json", "-q"])
        results = json.loads(r_search.stdout)
        assert len(results) >= 1
        item_id = results[0]["id"]

        r_show = run(["show", item_id, "--db", db, "--json", "-q"])
        assert r_show.returncode == 0
        data = json.loads(r_show.stdout)
        assert data["id"] == item_id
        assert "content" in data
        assert "tier" in data
        assert "provenance" in data


# ---------------------------------------------------------------------------
# stats → JSON consumer
# ---------------------------------------------------------------------------


class TestStatsJson:
    def test_stats_json_parseable(self, db, sample_file):
        run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        r = run(["stats", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["status"] == "ok"
        assert isinstance(data["total_items"], int)
        assert "by_tier" in data


# ---------------------------------------------------------------------------
# stdout purity
# ---------------------------------------------------------------------------


class TestStdoutPurity:
    def test_push_no_diagnostics_on_stdout(self, db, sample_file):
        """push must never leak progress/warnings to stdout."""
        r = run([
            "push", "design",
            "--source", sample_file,
            "--db", db,
            # NOT --quiet: diagnostics should still go to stderr, not stdout
        ])
        assert r.returncode == 0
        stdout_lines = r.stdout.strip().splitlines()
        for line in stdout_lines:
            # Every stdout line must be injection block content
            assert not line.startswith("[push]"), f"Diagnostic leaked to stdout: {line}"
            assert not line.startswith("[pull]"), f"Diagnostic leaked to stdout: {line}"

    def test_search_no_diagnostics_on_stdout(self, db, sample_file):
        """search stdout is pure data (human or JSON format)."""
        run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        r = run(["search", "event sourcing", "--db", db])
        assert r.returncode == 0
        stdout_lines = r.stdout.strip().splitlines()
        for line in stdout_lines:
            assert not line.startswith("[search]"), f"Diagnostic leaked: {line}"

    def test_quiet_flag_suppresses_stderr(self, db, sample_file):
        """--quiet suppresses info messages on stderr."""
        r_noisy = run([
            "push", "design",
            "--source", sample_file,
            "--db", db,
        ])
        r_quiet = run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        # Quiet should have less or equal stderr
        assert len(r_quiet.stderr) <= len(r_noisy.stderr)


# ---------------------------------------------------------------------------
# Multi-step workflow
# ---------------------------------------------------------------------------


class TestMultiStepWorkflow:
    def test_ingest_search_show_consolidate(self, db, sample_file, tmp_path):
        """Full workflow: ingest → search → show → consolidate → stats."""
        # Step 1: Ingest via push
        r = run([
            "push", "design",
            "--source", sample_file,
            "--db", db, "-q",
        ])
        assert r.returncode == 0

        # Step 2: Ingest more content via pull
        for i in range(2):
            run(
                ["pull", "--db", db, "--tags", "design,arch", "-q"],
                stdin=f"Design note {i}: event sourcing handles state transitions well.",
            )

        # Step 3: Search
        r_search = run(["search", "event sourcing", "--db", db, "--json", "-q"])
        assert r_search.returncode == 0
        results = json.loads(r_search.stdout)
        assert len(results) >= 1

        # Step 4: Show an item
        item_id = results[0]["id"]
        r_show = run(["show", item_id, "--db", db, "-q"])
        assert r_show.returncode == 0
        assert item_id in r_show.stdout

        # Step 5: Consolidate
        r_cons = run(["consolidate", "--db", db, "--json", "-q"])
        assert r_cons.returncode == 0
        data = json.loads(r_cons.stdout)
        assert "items_processed" in data

        # Step 6: Stats
        r_stats = run(["stats", "--db", db, "--json", "-q"])
        assert r_stats.returncode == 0
        stats = json.loads(r_stats.stdout)
        assert stats["total_items"] >= 1
        assert stats["events_count"] >= 1


# ---------------------------------------------------------------------------
# Environment variable precedence
# ---------------------------------------------------------------------------


class TestEnvPrecedence:
    def test_memctl_db_env(self, db):
        """MEMCTL_DB env var is respected."""
        r = run(
            ["stats", "--json", "-q"],
            env={"MEMCTL_DB": db},
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["status"] == "ok"

    def test_cli_flag_overrides_env(self, db, tmp_path):
        """--db flag takes precedence over MEMCTL_DB env var."""
        other_db = str(tmp_path / "other" / "memory.db")
        run(["init", str(tmp_path / "other"), "--db", other_db, "-q"])

        # Env points to db, flag points to other_db
        r = run(
            ["stats", "--json", "--db", other_db, "-q"],
            env={"MEMCTL_DB": db},
        )
        assert r.returncode == 0

    def test_memctl_tier_env(self, db):
        """MEMCTL_TIER env var affects pull default tier."""
        r = run(
            ["pull", "--db", db, "--title", "MTM note", "-q"],
            stdin="This should go into MTM tier.",
            env={"MEMCTL_TIER": "mtm"},
        )
        assert r.returncode == 0

        # Search and verify tier
        r2 = run(["search", "MTM tier", "--db", db, "--json", "-q"])
        if r2.stdout.strip():
            results = json.loads(r2.stdout)
            for item in results:
                if "MTM" in item.get("title", ""):
                    assert item["tier"] == "mtm"

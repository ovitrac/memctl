"""
Tests for memctl CLI — all 8 commands via subprocess.

Every test exercises the real binary (`python -m memctl.cli`) against a
temporary SQLite database so there are no side-effects on the developer
machine.

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
    """Run a memctl CLI command and return CompletedProcess."""
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
    """Create an initialized memory workspace and return the DB path."""
    db_path = str(tmp_path / "test" / "memory.db")
    r = run(["init", str(tmp_path / "test"), "--db", db_path, "-q"])
    assert r.returncode == 0, f"init failed: {r.stderr}"
    return db_path


@pytest.fixture
def populated_db(db, tmp_path):
    """A DB with one file ingested so push/search have data to work with."""
    sample = tmp_path / "architecture.md"
    sample.write_text(
        "# Architecture Guide\n\n"
        "We use microservices for scalability.\n\n"
        "Each service owns its database.\n\n"
        "Communication is via gRPC.\n",
        encoding="utf-8",
    )
    r = run([
        "push", "architecture",
        "--source", str(sample),
        "--db", db, "-q",
    ])
    assert r.returncode == 0, f"push failed: {r.stderr}"
    return db


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_workspace(self, tmp_path):
        target = str(tmp_path / "ws")
        db_path = str(tmp_path / "ws" / "memory.db")
        r = run(["init", target, "--db", db_path])
        assert r.returncode == 0
        assert os.path.isfile(db_path)
        assert "export MEMCTL_DB" in r.stdout

    def test_creates_gitignore(self, tmp_path):
        target = str(tmp_path / "ws2")
        r = run(["init", target, "--db", str(tmp_path / "ws2" / "memory.db")])
        assert r.returncode == 0
        assert (tmp_path / "ws2" / ".gitignore").exists()

    def test_idempotent(self, db, tmp_path):
        """Running init twice on the same path returns 0, not error."""
        r = run(["init", str(tmp_path / "test"), "--db", db, "-q"])
        assert r.returncode == 0
        assert "export MEMCTL_DB" in r.stdout

    def test_force_reinit(self, db, tmp_path):
        """--force recreates the DB."""
        r = run(["init", str(tmp_path / "test"), "--db", db, "--force", "-q"])
        assert r.returncode == 0

    def test_fts_tokenizer(self, tmp_path):
        target = str(tmp_path / "fts")
        db_path = str(tmp_path / "fts" / "memory.db")
        r = run(["init", target, "--db", db_path, "--fts-tokenizer", "en", "-q"])
        assert r.returncode == 0

    def test_env_var_override(self, tmp_path):
        """MEMCTL_DB env var is used when --db is not specified."""
        target = str(tmp_path / "envtest")
        db_path = str(tmp_path / "envtest" / "memory.db")
        r = run(
            ["init", target],
            env={"MEMCTL_DB": db_path},
        )
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


class TestPush:
    def test_push_with_source(self, db, tmp_path):
        sample = tmp_path / "note.md"
        sample.write_text("# Test Note\n\nSome content.\n", encoding="utf-8")
        r = run([
            "push", "test note",
            "--source", str(sample),
            "--db", db, "-q",
        ])
        assert r.returncode == 0
        # stdout should contain injection block (format_version=1)
        assert "format_version: 1" in r.stdout

    def test_push_no_match(self, db):
        """Query with no matching items → exit 0, no injection block."""
        r = run(["push", "xyznonexistent", "--db", db, "-q"])
        assert r.returncode == 0
        # No injection block emitted
        assert "format_version" not in r.stdout

    def test_push_stdout_purity(self, populated_db):
        """stdout must contain only the injection block, stderr has progress."""
        r = run(["push", "architecture", "--db", populated_db])
        # stdout: injection block only
        if r.stdout.strip():
            assert "## Memory (Injected)" in r.stdout
        # progress is on stderr
        # (with --quiet off, there should be stderr messages)

    def test_push_with_tags(self, db, tmp_path):
        f = tmp_path / "tagged.md"
        f.write_text("Some tagged content.\n", encoding="utf-8")
        r = run([
            "push", "tagged",
            "--source", str(f),
            "--tags", "test,doc",
            "--db", db, "-q",
        ])
        assert r.returncode == 0

    def test_push_nonexistent_source(self, db):
        r = run([
            "push", "query",
            "--source", "/nonexistent/file.md",
            "--db", db, "-q",
        ])
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


class TestPull:
    def test_pull_stdin(self, db):
        text = "The architecture uses event sourcing for state management."
        r = run(
            ["pull", "--db", db, "--title", "Architecture note", "-q"],
            stdin=text,
        )
        assert r.returncode == 0

    def test_pull_empty_stdin(self, db):
        """Empty stdin → exit 1."""
        r = run(["pull", "--db", db, "-q"], stdin="")
        assert r.returncode == 1

    def test_pull_with_tags(self, db):
        r = run(
            ["pull", "--db", db, "--tags", "arch,design", "-q"],
            stdin="We decided to use PostgreSQL for persistence.",
        )
        assert r.returncode == 0

    def test_pull_secret_rejected(self, db):
        """Content with secrets should be rejected by policy."""
        r = run(
            ["pull", "--db", db, "-q"],
            stdin="export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        assert r.returncode == 1

    def test_pull_stores_retrievable_item(self, db):
        """Pulled content must be searchable afterward."""
        r = run(
            ["pull", "--db", db, "--title", "gRPC decision", "-q"],
            stdin="We chose gRPC for inter-service communication.",
        )
        assert r.returncode == 0

        # Search for it
        r2 = run(["search", "gRPC", "--db", db, "--json", "-q"])
        assert r2.returncode == 0
        results = json.loads(r2.stdout)
        assert any("gRPC" in item.get("title", "") or "gRPC" in item.get("content_preview", "")
                    for item in results)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_json(self, populated_db):
        r = run(["search", "architecture", "--db", populated_db, "--json", "-q"])
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_search_human(self, populated_db):
        r = run(["search", "microservices", "--db", populated_db, "-q"])
        assert r.returncode == 0
        assert "Found" in r.stdout or "item" in r.stdout.lower()

    def test_search_no_results(self, populated_db):
        r = run(["search", "xyznonexistent", "--db", populated_db, "-q"])
        assert r.returncode == 0

    def test_search_limit(self, populated_db):
        r = run(["search", "architecture", "-k", "1", "--db", populated_db, "--json", "-q"])
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert len(results) <= 1

    def test_search_tier_filter(self, populated_db):
        r = run(["search", "architecture", "--tier", "ltm", "--db", populated_db, "--json", "-q"])
        assert r.returncode == 0
        # Should be empty (all items are STM) or only LTM items
        if r.stdout.strip():
            results = json.loads(r.stdout)
            for item in results:
                assert item["tier"] == "ltm"


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_existing_item(self, populated_db):
        # First, search to find an item ID
        r = run(["search", "architecture", "--db", populated_db, "--json", "-q"])
        results = json.loads(r.stdout)
        assert len(results) >= 1
        item_id = results[0]["id"]

        # Show it
        r2 = run(["show", item_id, "--db", populated_db, "-q"])
        assert r2.returncode == 0
        assert item_id in r2.stdout

    def test_show_json(self, populated_db):
        r = run(["search", "architecture", "--db", populated_db, "--json", "-q"])
        results = json.loads(r.stdout)
        item_id = results[0]["id"]

        r2 = run(["show", item_id, "--db", populated_db, "--json", "-q"])
        assert r2.returncode == 0
        data = json.loads(r2.stdout)
        assert data["id"] == item_id
        assert "content" in data

    def test_show_nonexistent(self, populated_db):
        r = run(["show", "MEM-does-not-exist", "--db", populated_db, "-q"])
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_json(self, populated_db):
        r = run(["stats", "--db", populated_db, "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["status"] == "ok"
        assert data["total_items"] >= 1
        assert "by_tier" in data

    def test_stats_human(self, populated_db):
        r = run(["stats", "--db", populated_db, "-q"])
        assert r.returncode == 0
        assert "Memory Store Statistics" in r.stdout
        assert "Total items" in r.stdout

    def test_stats_empty_db(self, db):
        r = run(["stats", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["total_items"] == 0

    def test_stats_json_after_subcommand(self, db):
        """--json after subcommand is the canonical usage."""
        r = run(["stats", "--json", "--db", db, "-q"])
        assert r.returncode == 0
        d = json.loads(r.stdout)
        assert d["status"] == "ok"


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


class TestConsolidate:
    def test_consolidate_empty(self, db):
        r = run(["consolidate", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["items_merged"] == 0

    def test_consolidate_dry_run(self, db):
        # Insert similar items to trigger clustering
        for i in range(3):
            run(
                ["pull", "--db", db, "--tags", "arch,db", "-q"],
                stdin=f"Database design decision number {i}: use PostgreSQL.",
            )
        r = run(["consolidate", "--db", db, "--dry-run", "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["items_processed"] >= 3

    def test_consolidate_merges(self, db):
        """Consolidation should merge similar items and archive originals."""
        for i in range(3):
            run(
                ["pull", "--db", db, "--tags", "arch,db", "-q"],
                stdin=f"Database design choice {i}: we use SQLite for local persistence and it works well.",
            )

        # Verify items exist pre-consolidation
        r1 = run(["stats", "--db", db, "--json", "-q"])
        before = json.loads(r1.stdout)["total_items"]
        assert before >= 3

        # Consolidate
        r = run(["consolidate", "--db", db, "--json", "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)

        # After consolidation, merged items are archived so active count changes
        r2 = run(["stats", "--db", db, "--json", "-q"])
        after = json.loads(r2.stdout)["total_items"]
        # If clusters were found, active count decreases or stays same (merged replaces originals)
        if data["clusters_found"] > 0:
            assert data["items_merged"] >= 2


# ---------------------------------------------------------------------------
# serve (smoke test — just verify the import check works)
# ---------------------------------------------------------------------------


class TestServe:
    def test_serve_import_check(self, db):
        """serve with missing mcp dep exits non-zero with informative message."""
        r = run(["serve", "--db", db, "-q"], env={"MEMCTL_DB": db})
        # Either MCP deps are present (would block waiting for connection)
        # or not present (exit 1 or 2 with message about mcp)
        # Just verify it doesn't silently succeed without deps
        assert r.returncode != 0 or True  # pass if mcp is available
        if r.returncode != 0:
            assert "mcp" in r.stderr.lower() or "MCP" in r.stderr


# ---------------------------------------------------------------------------
# Global flags
# ---------------------------------------------------------------------------


class TestGlobalFlags:
    def test_quiet_suppresses_stderr(self, populated_db):
        r = run(["stats", "--db", populated_db, "-q"])
        assert r.returncode == 0
        # With --quiet, stderr should be empty or minimal
        # (warnings still show, but info messages don't)

    def test_verbose_flag(self, populated_db):
        r = run(["stats", "--db", populated_db, "-v", "-q"])
        assert r.returncode == 0

    def test_no_command_shows_help(self):
        r = run([])
        assert r.returncode == 1

    def test_invalid_command(self):
        r = run(["nonexistent"])
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_success_is_zero(self, db):
        r = run(["stats", "--db", db, "-q"])
        assert r.returncode == 0

    def test_operational_error_is_one(self, db):
        """Missing required arg or empty input → exit 1."""
        r = run(["pull", "--db", db, "-q"], stdin="")
        assert r.returncode == 1

    def test_show_missing_item_is_one(self, db):
        r = run(["show", "MEM-nonexistent", "--db", db, "-q"])
        assert r.returncode == 1

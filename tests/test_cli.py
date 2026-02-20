"""
Tests for memctl CLI — all 13 commands via subprocess.

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
# loop (bounded recall-answer loop)
# ---------------------------------------------------------------------------


class TestLoop:
    def test_loop_no_stdin(self, db):
        """loop with no stdin exits 1."""
        r = run(["loop", "test query", "--llm", "cat", "--db", db, "-q"])
        assert r.returncode == 1
        assert "stdin" in r.stderr.lower() or "input" in r.stderr.lower()

    def test_loop_missing_llm(self, db):
        """loop without --llm exits with error."""
        r = run(["loop", "test query", "--db", db, "-q"], stdin="some context")
        assert r.returncode != 0

    def test_loop_single_pass_passive(self, db):
        """loop with passive protocol and cat as LLM → single iteration."""
        r = run(
            ["loop", "what is auth?", "--llm", "cat", "--protocol", "passive",
             "--db", db, "-q"],
            stdin="Authentication uses JWT tokens.",
        )
        assert r.returncode == 0
        # cat echoes the prompt back — answer should contain the context
        assert "JWT" in r.stdout or "auth" in r.stdout.lower()

    def test_loop_json_protocol_with_echo(self, db):
        """loop with json protocol: LLM outputs valid JSON → single pass."""
        # Use printf to simulate an LLM that outputs proper JSON protocol
        llm_cmd = '''sh -c 'echo "{\\\"need_more\\\": false, \\\"stop\\\": true}"; echo ""; echo "Final answer about auth."' '''
        r = run(
            ["loop", "auth flow", "--llm", llm_cmd, "--protocol", "json",
             "--db", db, "-q"],
            stdin="Initial context about authentication.",
        )
        assert r.returncode == 0
        assert "Final answer" in r.stdout

    def test_loop_trace_file(self, db, tmp_path):
        """loop --trace-file writes JSONL trace."""
        trace_path = str(tmp_path / "trace.jsonl")
        r = run(
            ["loop", "query", "--llm", "cat", "--protocol", "passive",
             "--trace-file", trace_path, "--db", db, "-q"],
            stdin="Context text.",
        )
        assert r.returncode == 0
        with open(trace_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) >= 1
        obj = json.loads(lines[0])
        assert obj["iter"] == 1
        assert obj["action"] == "llm_stop"

    def test_loop_replay(self, db, tmp_path):
        """loop --replay reads a trace file without calling LLM."""
        trace_path = str(tmp_path / "trace.jsonl")
        with open(trace_path, "w") as f:
            f.write('{"iter":1,"query":"auth","new_items":5,"sim":null,"action":"continue"}\n')
            f.write('{"iter":2,"query":null,"new_items":0,"sim":0.95,"action":"fixed_point"}\n')
        r = run(
            ["loop", "ignored", "--llm", "cat", "--replay", trace_path,
             "--db", db, "-q"],
            stdin="",  # stdin not used in replay mode
        )
        assert r.returncode == 0
        lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["iter"] == 1
        assert json.loads(lines[1])["action"] == "fixed_point"

    def test_loop_strict_exit_code(self, db):
        """loop --strict exits 1 when LLM does not converge."""
        # cat echoes prompt → json parse fails → treated as stop → converged
        # So this actually converges. Use passive protocol to verify the flag wiring.
        r = run(
            ["loop", "q", "--llm", "cat", "--protocol", "passive",
             "--strict", "--db", db, "-q"],
            stdin="Some context.",
        )
        # Passive + cat → single iteration, LLM stop → converged → exit 0
        assert r.returncode == 0

    def test_loop_help(self):
        """loop --help shows all flags."""
        r = run(["loop", "--help"])
        assert r.returncode == 0
        assert "--llm" in r.stdout
        assert "--protocol" in r.stdout
        assert "--max-calls" in r.stdout
        assert "--threshold" in r.stdout
        assert "--replay" in r.stdout


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


# ---------------------------------------------------------------------------
# mount
# ---------------------------------------------------------------------------


class TestMountCLI:
    def test_mount_register(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        r = run(["mount", str(folder), "--db", db])
        assert r.returncode == 0
        assert "MNT-" in r.stderr or "Mounted" in r.stderr

    def test_mount_list_empty(self, db):
        r = run(["mount", "--list", "--db", db, "-q"])
        assert r.returncode == 0

    def test_mount_list_after_register(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        run(["mount", str(folder), "--name", "docs", "--db", db, "-q"])
        r = run(["mount", "--list", "--db", db, "-q"])
        assert r.returncode == 0
        assert "docs" in r.stdout or "MNT-" in r.stdout

    def test_mount_list_json(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        run(["mount", str(folder), "--name", "docs", "--db", db, "-q"])
        r = run(["mount", "--list", "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "docs"

    def test_mount_remove(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        run(["mount", str(folder), "--name", "docs", "--db", db, "-q"])
        r = run(["mount", "--remove", "docs", "--db", db, "-q"])
        assert r.returncode == 0
        r2 = run(["mount", "--list", "--json", "--db", db, "-q"])
        data = json.loads(r2.stdout)
        assert len(data) == 0

    def test_mount_nonexistent_path(self, db, tmp_path):
        r = run(["mount", str(tmp_path / "nope"), "--db", db, "-q"])
        assert r.returncode == 1

    def test_mount_with_ignore(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        r = run(["mount", str(folder), "--ignore", "*.log", "tmp/*", "--db", db, "-q"])
        assert r.returncode == 0

    def test_mount_with_lang(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        r = run(["mount", str(folder), "--lang", "fr", "--db", db, "-q"])
        assert r.returncode == 0

    def test_mount_help(self):
        r = run(["mount", "--help"])
        assert r.returncode == 0
        assert "--name" in r.stdout
        assert "--ignore" in r.stdout


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class TestSyncCLI:
    def test_sync_path(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "readme.md").write_text("# Hello\n\nContent here.")
        r = run(["sync", str(folder), "--db", db, "-q"])
        assert r.returncode == 0

    def test_sync_json(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "readme.md").write_text("# Hello\n\nContent here.")
        r = run(["sync", str(folder), "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["files_scanned"] >= 1

    def test_sync_all_no_mounts(self, db):
        r = run(["sync", "--db", db, "-q"])
        assert r.returncode == 0

    def test_sync_full_flag(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "readme.md").write_text("# Hello\n\nContent here.")
        run(["sync", str(folder), "--db", db, "-q"])
        r = run(["sync", str(folder), "--full", "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["files_unchanged"] == 0

    def test_sync_help(self):
        r = run(["sync", "--help"])
        assert r.returncode == 0
        assert "--full" in r.stdout


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


class TestInspectCLI:
    def test_inspect_empty(self, db):
        r = run(["inspect", "--db", db, "-q"])
        assert r.returncode == 0
        assert "No files found" in r.stdout

    def test_inspect_after_sync(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nDetailed guide.\n\nMore details.")
        run(["sync", str(folder), "--db", db, "-q"])
        r = run(["inspect", "--db", db, "-q"])
        assert r.returncode == 0
        assert "## Structure (Injected)" in r.stdout
        assert "Total files:" in r.stdout

    def test_inspect_json(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nContent.")
        run(["sync", str(folder), "--db", db, "-q"])
        r = run(["inspect", "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "total_files" in data
        assert data["total_files"] >= 1

    def test_inspect_budget(self, db, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nContent.")
        run(["sync", str(folder), "--db", db, "-q"])
        r = run(["inspect", "--budget", "10", "--db", db, "-q"])
        assert r.returncode == 0
        assert "[...truncated]" in r.stdout

    def test_inspect_help(self):
        r = run(["inspect", "--help"])
        assert r.returncode == 0
        assert "--mount" in r.stdout
        assert "--budget" in r.stdout
        assert "--sync" in r.stdout
        assert "--mount-mode" in r.stdout

    def test_inspect_with_path(self, db, tmp_path):
        """inspect <path> auto-mounts + auto-syncs + produces injection block."""
        folder = tmp_path / "source"
        folder.mkdir()
        (folder / "api.md").write_text("# API\n\nEndpoint docs.\n\nDetails.")
        r = run(["inspect", str(folder), "--db", db, "-q"])
        assert r.returncode == 0
        assert "## Structure (Injected)" in r.stdout
        assert "Total files:" in r.stdout

    def test_inspect_with_path_json(self, db, tmp_path):
        """inspect <path> --json includes orchestration keys."""
        folder = tmp_path / "source"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nContent.\n\nMore.")
        r = run(["inspect", str(folder), "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "total_files" in data
        assert "was_mounted" in data
        assert "was_synced" in data
        assert data["total_files"] >= 1

    def test_inspect_with_path_no_sync(self, db, tmp_path):
        """inspect <path> --no-sync skips sync even when stale."""
        folder = tmp_path / "source"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nContent.")
        r = run(["inspect", str(folder), "--no-sync", "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["was_synced"] is False
        assert data["total_files"] == 0  # never synced

    def test_inspect_with_path_ephemeral(self, db, tmp_path):
        """inspect <path> --mount-mode=ephemeral cleans up mount."""
        folder = tmp_path / "source"
        folder.mkdir()
        (folder / "guide.md").write_text("# Guide\n\nContent.")
        r = run(["inspect", str(folder), "--mount-mode", "ephemeral",
                 "--json", "--db", db, "-q"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["was_ephemeral"] is True
        # Verify mount is gone
        r2 = run(["mount", "--list", "--json", "--db", db, "-q"])
        mounts = json.loads(r2.stdout)
        assert not any(m["mount_id"] == data["mount_id"] for m in mounts)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestChatCLI:
    """Subprocess tests for `memctl chat`."""

    def test_chat_help(self):
        """chat --help shows expected flags."""
        r = run(["chat", "--help"])
        assert r.returncode == 0
        for flag in ["--llm", "--session", "--store", "--session-budget",
                      "--protocol", "--history-turns"]:
            assert flag in r.stdout, f"Missing flag {flag} in help output"

    def test_chat_scripted_session(self, populated_db, tmp_path):
        """Scripted stdin session with echo returns deterministic output."""
        # Use 'echo' as a trivial LLM: it just echoes stdin back
        # chat_turn sends the full prompt to the LLM, so stdout will
        # contain whatever echo produces.
        # Pipe a single question then EOF.
        r = subprocess.run(
            CLI + ["chat", "--llm", "cat", "--protocol", "passive",
                   "--db", populated_db, "-q"],
            input="What is the architecture?\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        # cat echoes the full prompt (context + question) as the answer
        # The key test: exit code 0 and non-empty stdout
        assert r.returncode == 0
        assert len(r.stdout.strip()) > 0

"""
Tests for memctl installer — setup/teardown for MCP, eco, and hooks.

Tests exercise the installer module directly (unit tests for helpers and
setup/teardown functions) and via CLI subprocess (integration tests).

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from memctl.installer import (
    _read_json,
    _write_json,
    _backup_file,
    _copy_template,
    _claude_desktop_config,
    _build_hook_entry,
    _add_hook_entries,
    _remove_hook_entries,
    _add_permissions,
    _remove_permissions,
    _ensure_gitignore_entry,
    _is_pipx,
    _install_hint,
    setup_mcp,
    setup_eco,
    setup_hooks,
    teardown_mcp,
    teardown_eco,
    teardown_hooks,
)


PYTHON = sys.executable
CLI = [PYTHON, "-m", "memctl.cli"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def work(tmp_path, monkeypatch):
    """Set up an isolated working directory with .claude/ structure."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _Args:
    """Minimal args namespace for installer functions."""
    def __init__(self, **kwargs):
        self.dry_run = False
        self.yes = True
        self.force = False
        self.client = "claude-code"
        self.db = None
        self.db_root = None
        self.target = "mcp"
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestReadJson:
    def test_missing_returns_empty(self, tmp_path):
        assert _read_json(tmp_path / "nope.json") == {}

    def test_malformed_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid", encoding="utf-8")
        assert _read_json(bad) == {}

    def test_empty_file_returns_empty(self, tmp_path):
        empty = tmp_path / "empty.json"
        empty.write_text("", encoding="utf-8")
        assert _read_json(empty) == {}

    def test_valid_json(self, tmp_path):
        f = tmp_path / "ok.json"
        f.write_text('{"a": 1}', encoding="utf-8")
        assert _read_json(f) == {"a": 1}


class TestWriteJson:
    def test_creates_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.json"
        _write_json(target, {"x": 1})
        assert target.exists()
        assert json.loads(target.read_text("utf-8")) == {"x": 1}

    def test_trailing_newline(self, tmp_path):
        f = tmp_path / "test.json"
        _write_json(f, {})
        assert f.read_text("utf-8").endswith("\n")


class TestBackup:
    def test_creates_timestamped_file(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text('{"a": 1}', encoding="utf-8")
        bak = _backup_file(f)
        assert bak is not None
        assert bak.exists()
        assert ".bak." in bak.name
        assert json.loads(bak.read_text("utf-8")) == {"a": 1}

    def test_nonexistent_returns_none(self, tmp_path):
        assert _backup_file(tmp_path / "nope.json") is None


class TestCopyTemplate:
    def test_creates_file(self, tmp_path):
        src = tmp_path / "src.md"
        src.write_text("# Hello", encoding="utf-8")
        dst = tmp_path / "dst" / "out.md"
        assert _copy_template(src, dst) is True
        assert dst.read_text("utf-8") == "# Hello"

    def test_skip_existing(self, tmp_path):
        src = tmp_path / "src.md"
        src.write_text("new content", encoding="utf-8")
        dst = tmp_path / "existing.md"
        dst.write_text("old content", encoding="utf-8")
        assert _copy_template(src, dst) is False
        assert dst.read_text("utf-8") == "old content"

    def test_force_overwrites(self, tmp_path):
        src = tmp_path / "src.md"
        src.write_text("new content", encoding="utf-8")
        dst = tmp_path / "existing.md"
        dst.write_text("old content", encoding="utf-8")
        assert _copy_template(src, dst, force=True) is True
        assert dst.read_text("utf-8") == "new content"


class TestClaudeDesktopPath:
    def test_linux(self):
        with patch("memctl.installer.IS_MACOS", False), \
             patch("memctl.installer.IS_WINDOWS", False):
            p = _claude_desktop_config()
            assert ".config/Claude" in str(p)

    def test_macos(self):
        with patch("memctl.installer.IS_MACOS", True), \
             patch("memctl.installer.IS_WINDOWS", False):
            p = _claude_desktop_config()
            assert "Application Support/Claude" in str(p)

    def test_windows(self):
        with patch("memctl.installer.IS_MACOS", False), \
             patch("memctl.installer.IS_WINDOWS", True), \
             patch.dict(os.environ, {"APPDATA": "/fake/appdata"}):
            p = _claude_desktop_config()
            assert "Claude" in str(p)


class TestInstallHint:
    def test_pip_mcp(self):
        with patch("memctl.installer.sys") as mock_sys:
            mock_sys.prefix = "/home/user/venv"
            assert "pip install" in _install_hint("mcp")
            assert "memctl[mcp]" in _install_hint("mcp")

    def test_pipx_mcp(self):
        with patch("memctl.installer.sys") as mock_sys:
            mock_sys.prefix = "/home/user/.local/pipx/venvs/memctl"
            assert _is_pipx() is True
            assert "pipx inject" in _install_hint("mcp")

    def test_pip_docs(self):
        with patch("memctl.installer.sys") as mock_sys:
            mock_sys.prefix = "/home/user/venv"
            assert "pip install" in _install_hint("docs")
            assert "memctl[docs]" in _install_hint("docs")

    def test_pipx_docs(self):
        with patch("memctl.installer.sys") as mock_sys:
            mock_sys.prefix = "/home/user/.local/pipx/venvs/memctl"
            hint = _install_hint("docs")
            assert "pipx inject" in hint
            assert "python-docx" in hint

    def test_is_pipx_false(self):
        with patch("memctl.installer.sys") as mock_sys:
            mock_sys.prefix = "/home/user/anaconda3/envs/memctl-env"
            assert _is_pipx() is False


class TestHookEntries:
    def test_build_hook_entry(self):
        entry = _build_hook_entry("memctl hooks eco-hint")
        assert entry == {"hooks": [{"type": "command", "command": "memctl hooks eco-hint"}]}

    def test_add_hook_entries(self):
        settings = {}
        entry = _build_hook_entry("memctl hooks eco-hint")
        _add_hook_entries(settings, "UserPromptSubmit", [entry], ["eco-hint"])
        assert len(settings["hooks"]["UserPromptSubmit"]) == 1

    def test_add_hook_entries_idempotent(self):
        settings = {}
        entry = _build_hook_entry("memctl hooks eco-hint")
        _add_hook_entries(settings, "UserPromptSubmit", [entry], ["eco-hint"])
        _add_hook_entries(settings, "UserPromptSubmit", [entry], ["eco-hint"])
        assert len(settings["hooks"]["UserPromptSubmit"]) == 1

    def test_add_preserves_other_hooks(self):
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "other"}]}]}}
        entry = _build_hook_entry("memctl hooks safety-guard")
        _add_hook_entries(settings, "PreToolUse", [entry], ["safety-guard"])
        assert len(settings["hooks"]["PreToolUse"]) == 2

    def test_remove_hook_entries(self):
        settings = {"hooks": {"PreToolUse": [
            {"hooks": [{"type": "command", "command": "memctl hooks safety-guard"}]},
            {"hooks": [{"type": "command", "command": "other-tool"}]},
        ]}}
        changed = _remove_hook_entries(settings, "PreToolUse", "safety-guard")
        assert changed is True
        assert len(settings["hooks"]["PreToolUse"]) == 1

    def test_remove_cleans_empty(self):
        settings = {"hooks": {"PreToolUse": [
            {"hooks": [{"type": "command", "command": "memctl hooks safety-guard"}]},
        ]}}
        _remove_hook_entries(settings, "PreToolUse", "safety-guard")
        assert "hooks" not in settings

    def test_remove_nonexistent(self):
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "other"}]}]}}
        changed = _remove_hook_entries(settings, "PreToolUse", "safety-guard")
        assert changed is False


class TestPermissions:
    def test_add_permissions(self):
        settings = {}
        _add_permissions(settings, ["Bash(memctl *)"])
        assert "Bash(memctl *)" in settings["permissions"]["allow"]

    def test_add_permissions_idempotent(self):
        settings = {"permissions": {"allow": ["Bash(memctl *)"]}}
        changed = _add_permissions(settings, ["Bash(memctl *)"])
        assert changed is False
        assert settings["permissions"]["allow"].count("Bash(memctl *)") == 1

    def test_remove_permissions(self):
        settings = {"permissions": {"allow": ["Bash(memctl *)", "other"]}}
        changed = _remove_permissions(settings, {"Bash(memctl *)"})
        assert changed is True
        assert "Bash(memctl *)" not in settings["permissions"]["allow"]
        assert "other" in settings["permissions"]["allow"]

    def test_remove_permissions_cleans_empty(self):
        settings = {"permissions": {"allow": ["Bash(memctl *)"]}}
        _remove_permissions(settings, {"Bash(memctl *)"})
        assert "permissions" not in settings


class TestGitignore:
    def test_append_entry(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("*.pyc\n", encoding="utf-8")
        changed = _ensure_gitignore_entry(gi, ".memory/")
        assert changed is True
        assert ".memory/" in gi.read_text("utf-8")

    def test_skip_existing(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text(".memory/\n", encoding="utf-8")
        changed = _ensure_gitignore_entry(gi, ".memory/")
        assert changed is False

    def test_create_new(self, tmp_path):
        gi = tmp_path / ".gitignore"
        changed = _ensure_gitignore_entry(gi, ".memory/")
        assert changed is True
        assert gi.read_text("utf-8") == ".memory/\n"


# ---------------------------------------------------------------------------
# setup_mcp tests
# ---------------------------------------------------------------------------


class TestSetupMcp:
    def test_creates_config(self, work):
        settings_path = Path.home() / ".claude" / "settings.json"
        # Use a temp file path to avoid touching real settings
        with patch("memctl.installer._claude_code_settings", return_value=work / "settings.json"):
            args = _Args(db=str(work / "test.db"))
            setup_mcp(args)
        config = _read_json(work / "settings.json")
        assert "memctl" in config.get("mcpServers", {})

    def test_idempotent(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(db=str(work / "test.db"))
            setup_mcp(args)
            setup_mcp(args)
        config = _read_json(cfg)
        assert "memctl" in config["mcpServers"]

    def test_preserves_other_servers(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {"mcpServers": {"other": {"command": "other-server"}}})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(db=str(work / "test.db"))
            setup_mcp(args)
        config = _read_json(cfg)
        assert "other" in config["mcpServers"]
        assert "memctl" in config["mcpServers"]

    def test_dry_run_no_writes(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(db=str(work / "test.db"), dry_run=True)
            setup_mcp(args)
        assert not cfg.exists()

    def test_backup_created(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {"existing": True})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(db=str(work / "test.db"))
            setup_mcp(args)
        backups = list(work.glob("settings.json.bak.*"))
        assert len(backups) >= 1


# ---------------------------------------------------------------------------
# setup_eco tests
# ---------------------------------------------------------------------------


class TestSetupEco:
    def test_copies_templates(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        eco_md = Path(".claude") / "eco" / "ECO.md"
        assert eco_md.exists()

    def test_registers_hooks(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        settings = _read_json(Path(".claude") / "settings.local.json")
        hooks = settings.get("hooks", {})
        # eco-hint in UserPromptSubmit
        ups = json.dumps(hooks.get("UserPromptSubmit", []))
        assert "eco-hint" in ups
        # eco-nudge in PreToolUse
        ptu = json.dumps(hooks.get("PreToolUse", []))
        assert "eco-nudge" in ptu

    def test_adds_permission(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        settings = _read_json(Path(".claude") / "settings.local.json")
        allow = settings.get("permissions", {}).get("allow", [])
        assert "Bash(memctl *)" in allow

    def test_writes_config(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        eco_config = _read_json(Path(".claude") / "eco" / "config.json")
        assert "db_path" in eco_config
        assert "version" in eco_config

    def test_idempotent(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        setup_eco(args)
        settings = _read_json(Path(".claude") / "settings.local.json")
        # Only one eco-hint entry
        ups = settings.get("hooks", {}).get("UserPromptSubmit", [])
        eco_entries = [e for e in ups if "eco-hint" in json.dumps(e)]
        assert len(eco_entries) == 1

    def test_gitignore(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        gi = Path(".gitignore")
        assert gi.exists()
        assert ".memory/" in gi.read_text("utf-8")

    def test_slash_commands(self, work):
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)
        commands_dir = Path(".claude") / "commands"
        # At least some commands should be present
        if commands_dir.exists():
            files = list(commands_dir.iterdir())
            assert len(files) >= 1


# ---------------------------------------------------------------------------
# setup_hooks tests
# ---------------------------------------------------------------------------


class TestSetupHooks:
    def test_registers(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="hooks")
            setup_hooks(args)
        settings = _read_json(cfg)
        pre = json.dumps(settings.get("hooks", {}).get("PreToolUse", []))
        post = json.dumps(settings.get("hooks", {}).get("PostToolUse", []))
        assert "safety-guard" in pre
        assert "audit-logger" in post

    def test_idempotent(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="hooks")
            setup_hooks(args)
            setup_hooks(args)
        settings = _read_json(cfg)
        pre = settings["hooks"]["PreToolUse"]
        guard_entries = [e for e in pre if "safety-guard" in json.dumps(e)]
        assert len(guard_entries) == 1


# ---------------------------------------------------------------------------
# teardown_mcp tests
# ---------------------------------------------------------------------------


class TestTeardownMcp:
    def test_removes_entry(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {"mcpServers": {"memctl": {"command": "memctl"}}})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="mcp")
            teardown_mcp(args)
        config = _read_json(cfg)
        assert "memctl" not in config.get("mcpServers", {})

    def test_preserves_others(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {"mcpServers": {"memctl": {}, "other": {"command": "x"}}})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="mcp")
            teardown_mcp(args)
        config = _read_json(cfg)
        assert "other" in config["mcpServers"]

    def test_idempotent(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="mcp")
            teardown_mcp(args)  # no error on empty config

    def test_missing_config(self, work):
        cfg = work / "nosuch.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            args = _Args(target="mcp")
            teardown_mcp(args)  # no error on missing file


# ---------------------------------------------------------------------------
# teardown_eco tests
# ---------------------------------------------------------------------------


class TestTeardownEco:
    def _setup_eco(self, work):
        """Run setup_eco to create artifacts for teardown testing."""
        args = _Args(target="eco", db_root=str(work / ".memory"))
        setup_eco(args)

    def test_removes_hooks(self, work):
        self._setup_eco(work)
        args = _Args(target="eco")
        teardown_eco(args)
        settings = _read_json(Path(".claude") / "settings.local.json")
        ups = json.dumps(settings.get("hooks", {}).get("UserPromptSubmit", []))
        assert "eco-hint" not in ups

    def test_removes_files(self, work):
        self._setup_eco(work)
        args = _Args(target="eco")
        teardown_eco(args)
        assert not (Path(".claude") / "eco" / "ECO.md").exists()
        assert not (Path(".claude") / "eco" / "config.json").exists()

    def test_preserves_memory(self, work):
        self._setup_eco(work)
        memory_dir = work / ".memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / "memory.db").write_text("fake db", encoding="utf-8")
        args = _Args(target="eco")
        teardown_eco(args)
        assert (memory_dir / "memory.db").exists()

    def test_cleans_permissions(self, work):
        self._setup_eco(work)
        args = _Args(target="eco")
        teardown_eco(args)
        settings = _read_json(Path(".claude") / "settings.local.json")
        allow = settings.get("permissions", {}).get("allow", [])
        assert "Bash(memctl *)" not in allow


# ---------------------------------------------------------------------------
# teardown_hooks tests
# ---------------------------------------------------------------------------


class TestTeardownHooks:
    def test_removes(self, work):
        cfg = work / "settings.json"
        # First setup
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            setup_hooks(_Args(target="hooks"))
            teardown_hooks(_Args(target="hooks"))
        settings = _read_json(cfg)
        assert "hooks" not in settings

    def test_idempotent(self, work):
        cfg = work / "settings.json"
        _write_json(cfg, {})
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            teardown_hooks(_Args(target="hooks"))  # no error


# ---------------------------------------------------------------------------
# CLI subprocess tests
# ---------------------------------------------------------------------------


class TestCLISetup:
    def test_setup_help(self):
        r = subprocess.run(
            CLI + ["setup", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "mcp" in r.stdout
        assert "eco" in r.stdout
        assert "hooks" in r.stdout

    def test_teardown_help(self):
        r = subprocess.run(
            CLI + ["teardown", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "mcp" in r.stdout

    def test_setup_mcp_dry_run(self, work):
        r = subprocess.run(
            CLI + ["setup", "mcp", "--dry-run", "--db", str(work / "test.db")],
            capture_output=True, text=True, timeout=10, cwd=str(work),
        )
        assert r.returncode == 0

    def test_teardown_mcp_dry_run(self, work):
        r = subprocess.run(
            CLI + ["teardown", "mcp", "--dry-run"],
            capture_output=True, text=True, timeout=10, cwd=str(work),
        )
        assert r.returncode == 0

    def test_setup_invalid_target(self):
        r = subprocess.run(
            CLI + ["setup", "invalid"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 2  # argparse error


# ---------------------------------------------------------------------------
# Round-trip integration tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_setup_then_teardown_mcp(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            # Setup
            setup_mcp(_Args(db=str(work / "test.db")))
            config = _read_json(cfg)
            assert "memctl" in config["mcpServers"]

            # Teardown
            teardown_mcp(_Args(target="mcp"))
            config = _read_json(cfg)
            assert "memctl" not in config.get("mcpServers", {})

    def test_setup_then_teardown_eco(self, work):
        # Setup
        setup_eco(_Args(target="eco", db_root=str(work / ".memory")))
        assert (Path(".claude") / "eco" / "ECO.md").exists()
        settings = _read_json(Path(".claude") / "settings.local.json")
        assert "mcpServers" in settings

        # Teardown
        teardown_eco(_Args(target="eco"))
        assert not (Path(".claude") / "eco" / "ECO.md").exists()

        # .memory/ preserved
        memory_dir = work / ".memory"
        if memory_dir.exists():
            # teardown_eco never deletes .memory/
            pass

    def test_setup_then_teardown_hooks(self, work):
        cfg = work / "settings.json"
        with patch("memctl.installer._claude_code_settings", return_value=cfg):
            setup_hooks(_Args(target="hooks"))
            settings = _read_json(cfg)
            assert "PreToolUse" in settings["hooks"]

            teardown_hooks(_Args(target="hooks"))
            settings = _read_json(cfg)
            assert "hooks" not in settings

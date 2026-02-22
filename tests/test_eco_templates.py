"""
Tests for eco template validation — slash commands, hint script, installer.

Verifies that all eco templates are correctly structured and reference
the expected MCP tools, ensuring install/uninstall consistency.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from importlib.resources import files
from pathlib import Path

import pytest


def _templates_dir() -> Path:
    """Return the path to memctl/templates/eco/."""
    return Path(str(files("memctl") / "templates" / "eco"))


def _scripts_dir() -> Path:
    """Return the path to memctl/scripts/."""
    return Path(str(files("memctl") / "scripts"))


# ---------------------------------------------------------------------------
# T1: All 5 command files exist
# ---------------------------------------------------------------------------

def test_all_command_files_exist():
    """T1: All 5 command files exist in templates/eco/commands/."""
    commands_dir = _templates_dir() / "commands"
    expected = ["scan.md", "remember.md", "recall.md", "reindex.md", "forget.md"]
    for name in expected:
        assert (commands_dir / name).is_file(), f"Missing command template: {name}"


# ---------------------------------------------------------------------------
# T2: Each command .md contains $ARGUMENTS
# ---------------------------------------------------------------------------

def test_commands_contain_arguments_placeholder():
    """T2: Each command .md contains $ARGUMENTS."""
    commands_dir = _templates_dir() / "commands"
    for name in ["scan.md", "remember.md", "recall.md", "reindex.md", "forget.md"]:
        content = (commands_dir / name).read_text(encoding="utf-8")
        assert "$ARGUMENTS" in content, f"{name} missing $ARGUMENTS placeholder"


# ---------------------------------------------------------------------------
# T3: scan.md references memory_inspect
# ---------------------------------------------------------------------------

def test_scan_references_memory_inspect():
    """T3: scan.md references memory_inspect."""
    content = (_templates_dir() / "commands" / "scan.md").read_text(encoding="utf-8")
    assert "memory_inspect" in content


# ---------------------------------------------------------------------------
# T4: remember.md references memory_propose
# ---------------------------------------------------------------------------

def test_remember_references_memory_propose():
    """T4: remember.md references memory_propose."""
    content = (_templates_dir() / "commands" / "remember.md").read_text(encoding="utf-8")
    assert "memory_propose" in content


# ---------------------------------------------------------------------------
# T5: recall.md references memory_recall
# ---------------------------------------------------------------------------

def test_recall_references_memory_recall():
    """T5: recall.md references memory_recall."""
    content = (_templates_dir() / "commands" / "recall.md").read_text(encoding="utf-8")
    assert "memory_recall" in content


# ---------------------------------------------------------------------------
# T6: reindex.md references memory_reindex
# ---------------------------------------------------------------------------

def test_reindex_references_memory_reindex():
    """T6: reindex.md references memory_reindex."""
    content = (_templates_dir() / "commands" / "reindex.md").read_text(encoding="utf-8")
    assert "memory_reindex" in content


# ---------------------------------------------------------------------------
# T7: forget.md references memory_reset
# ---------------------------------------------------------------------------

def test_forget_references_memory_reset():
    """T7: forget.md references memory_reset."""
    content = (_templates_dir() / "commands" / "forget.md").read_text(encoding="utf-8")
    assert "memory_reset" in content


# ---------------------------------------------------------------------------
# T8: eco-hint.sh contains config.json (config-driven DB path)
# ---------------------------------------------------------------------------

def test_eco_hint_references_config():
    """T8: eco-hint.sh contains config.json reference."""
    content = (_templates_dir() / "eco-hint.sh").read_text(encoding="utf-8")
    assert "config.json" in content


# ---------------------------------------------------------------------------
# T9: eco-hint.sh does NOT contain hardcoded .memory/memory.db in branch logic
# ---------------------------------------------------------------------------

def test_eco_hint_no_hardcoded_db_in_branches():
    """T9: eco-hint.sh uses config-driven DB path, not hardcoded."""
    content = (_templates_dir() / "eco-hint.sh").read_text(encoding="utf-8")
    # The default fallback is fine, but the branch logic should use $DB_PATH
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        # The elif/else branches should reference $DB_PATH, not .memory/memory.db
        if stripped.startswith("elif") or stripped.startswith("if"):
            if ".memory/memory.db" in stripped:
                pytest.fail(
                    f"eco-hint.sh has hardcoded .memory/memory.db in branch: {stripped}"
                )


# ---------------------------------------------------------------------------
# T10: install_eco.sh references commands/*.md
# ---------------------------------------------------------------------------

def test_installer_references_command_templates():
    """T10: install_eco.sh references commands/*.md."""
    content = (_scripts_dir() / "install_eco.sh").read_text(encoding="utf-8")
    assert "commands/" in content
    # Should iterate over the 5 command files
    for name in ["scan.md", "remember.md", "recall.md", "reindex.md", "forget.md"]:
        assert name in content, f"install_eco.sh does not reference {name}"


# ---------------------------------------------------------------------------
# T11: install_eco.sh writes config.json
# ---------------------------------------------------------------------------

def test_installer_writes_config():
    """T11: install_eco.sh writes config.json."""
    content = (_scripts_dir() / "install_eco.sh").read_text(encoding="utf-8")
    assert "config.json" in content
    assert "db_path" in content


# ---------------------------------------------------------------------------
# T12: uninstall_eco.sh lists all 5 command names
# ---------------------------------------------------------------------------

def test_uninstaller_lists_all_commands():
    """T12: uninstall_eco.sh references all 5 command file names."""
    content = (_scripts_dir() / "uninstall_eco.sh").read_text(encoding="utf-8")
    for name in ["scan.md", "remember.md", "recall.md", "reindex.md", "forget.md"]:
        assert name in content, f"uninstall_eco.sh does not reference {name}"


# ---------------------------------------------------------------------------
# T13-T17: CLI fallback commands are correct (no phantom commands)
# ---------------------------------------------------------------------------

def test_scan_cli_fallback_uses_sync_and_inspect():
    """T13: scan.md CLI fallback references memctl sync + memctl inspect."""
    content = (_templates_dir() / "commands" / "scan.md").read_text(encoding="utf-8")
    assert "memctl sync" in content
    assert "memctl inspect" in content


def test_recall_cli_fallback_uses_search():
    """T14: recall.md CLI fallback uses 'memctl search', warns against 'memctl recall'."""
    content = (_templates_dir() / "commands" / "recall.md").read_text(encoding="utf-8")
    assert "memctl search" in content
    # Template should warn that `memctl recall` does not exist
    assert "NOT" in content and "recall" in content


def test_remember_cli_fallback_uses_pull():
    """T15: remember.md CLI fallback uses 'memctl pull', warns against phantom commands."""
    content = (_templates_dir() / "commands" / "remember.md").read_text(encoding="utf-8")
    assert "memctl pull" in content
    # Template should warn against phantom CLI commands
    assert "NOT" in content


def test_reindex_cli_fallback_uses_reindex():
    """T16: reindex.md CLI fallback uses 'memctl reindex'."""
    content = (_templates_dir() / "commands" / "reindex.md").read_text(encoding="utf-8")
    assert "memctl reindex" in content


def test_forget_cli_fallback_uses_reset():
    """T17: forget.md CLI fallback uses 'memctl reset', warns against 'memctl forget'."""
    content = (_templates_dir() / "commands" / "forget.md").read_text(encoding="utf-8")
    assert "memctl reset" in content
    # Template should warn that `memctl forget` does not exist
    assert "NOT" in content and "forget" in content

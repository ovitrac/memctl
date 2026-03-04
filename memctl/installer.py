"""
memctl installer — Cross-platform setup and teardown for MCP, eco, and hooks.

Replaces the five Bash-only install/uninstall scripts with pure Python.
All functions use only Python stdlib — no compiled or optional dependencies.

Three setup targets:
    setup_mcp(args)    — register memctl MCP server for Claude Code / Desktop
    setup_eco(args)    — install eco mode (hooks, slash commands, strategy)
    setup_hooks(args)  — install safety-guard + audit-logger hooks

Three teardown targets:
    teardown_mcp(args)    — remove MCP server entries
    teardown_eco(args)    — remove eco artifacts (preserves .memory/)
    teardown_hooks(args)  — remove safety/audit hook entries

All operations support --dry-run, --yes, --force, and are idempotent.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

_SOURCE_TAG = "memctl"
_DRY_RUN = False

# Slash commands installed by eco mode
_SLASH_COMMANDS = [
    "eco.md", "scan.md", "recall.md", "remember.md", "reindex.md",
    "forget.md", "consolidate.md", "status.md", "export.md", "diff.md",
]

# Permission patterns managed by eco setup
_ECO_PERMISSIONS = [
    "Bash(memctl *)",
]


# ---------------------------------------------------------------------------
# Installation route detection (pipx vs pip)
# ---------------------------------------------------------------------------

def _is_pipx() -> bool:
    """Detect whether memctl is running inside a pipx-managed venv."""
    # pipx venvs live under ~/.local/pipx/venvs/ (or similar)
    return "pipx" in sys.prefix.replace("\\", "/").lower()


def _install_hint(extra: str) -> str:
    """Return an install command hint appropriate to the current environment.

    Examples:
        _install_hint("mcp")  → 'pipx inject memctl "mcp[cli]"'  (if pipx)
        _install_hint("mcp")  → 'pip install memctl[mcp]'         (otherwise)
        _install_hint("docs") → 'pipx inject memctl python-docx python-pptx ...' (if pipx)
        _install_hint("docs") → 'pip install memctl[docs]'        (otherwise)
    """
    if _is_pipx():
        if extra == "mcp":
            return 'pipx inject memctl "mcp[cli]"'
        elif extra == "docs":
            return "pipx inject memctl python-docx python-pptx openpyxl odfpy pypdf"
        return f'pipx inject memctl "memctl[{extra}]"'
    return f"pip install memctl[{extra}]"


# ---------------------------------------------------------------------------
# Colored stderr output (TTY-aware)
# ---------------------------------------------------------------------------

def _is_tty() -> bool:
    """Check if stderr is a terminal."""
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def _setup_info(msg: str) -> None:
    if _is_tty():
        print(f"\033[0;34m[info]\033[0m  {msg}", file=sys.stderr)
    else:
        print(f"[info]  {msg}", file=sys.stderr)


def _setup_warn(msg: str) -> None:
    if _is_tty():
        print(f"\033[0;33m[warn]\033[0m  {msg}", file=sys.stderr)
    else:
        print(f"[warn]  {msg}", file=sys.stderr)


def _setup_ok(msg: str) -> None:
    if _is_tty():
        print(f"\033[0;32m[ok]\033[0m    {msg}", file=sys.stderr)
    else:
        print(f"[ok]    {msg}", file=sys.stderr)


def _setup_fail(msg: str) -> None:
    if _is_tty():
        print(f"\033[0;31m[error]\033[0m {msg}", file=sys.stderr)
    else:
        print(f"[error] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# JSON file helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    """Read a JSON file, returning {} on missing or malformed input."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        return json.loads(text)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    """Write JSON with indent=2 and trailing newline. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _backup_file(path: Path) -> Path | None:
    """Create a timestamped backup. Returns backup path or None if source missing."""
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.parent / f"{path.name}.bak.{ts}"
    shutil.copy2(str(path), str(backup))
    return backup


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _templates_dir() -> Path:
    """Return the templates/ directory bundled with the memctl package."""
    return Path(__file__).parent / "templates"


def _copy_template(src: Path, dst: Path, *, force: bool = False) -> bool:
    """Copy a template file. Skip if exists unless force=True. Returns True if copied."""
    if dst.exists() and not force:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return True


# ---------------------------------------------------------------------------
# Claude config path resolution
# ---------------------------------------------------------------------------

def _claude_desktop_config() -> Path:
    """Return the Claude Desktop config path for the current platform."""
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _claude_code_settings() -> Path:
    """Return the Claude Code global settings path."""
    return Path.home() / ".claude" / "settings.json"


def _claude_code_local_settings() -> Path:
    """Return the Claude Code project-local settings path."""
    return Path(".claude") / "settings.local.json"


# ---------------------------------------------------------------------------
# Hook entry management
# ---------------------------------------------------------------------------

def _build_hook_entry(command: str) -> dict:
    """Build a Claude Code hook entry dict."""
    return {"hooks": [{"type": "command", "command": command}]}


def _has_matching_entry(entries: list, match_str: str) -> bool:
    """Check if any entry in the list contains match_str when serialized."""
    for e in entries:
        if match_str in json.dumps(e):
            return True
    return False


def _add_hook_entries(settings: dict, event: str, entries: list[dict],
                      match_strs: list[str]) -> bool:
    """Add hook entries if not already present (by match string). Returns changed.

    entries and match_strs must be parallel lists. Each entry is added only if
    no existing entry contains the corresponding match string.
    """
    if "hooks" not in settings:
        settings["hooks"] = {}
    hooks_list = settings["hooks"].get(event, [])

    changed = False
    for entry, match_str in zip(entries, match_strs):
        # Remove old entries with same match string (idempotent replacement)
        before = len(hooks_list)
        hooks_list = [e for e in hooks_list if match_str not in json.dumps(e)]
        # Add new entry
        hooks_list.append(entry)
        if len(hooks_list) != before:
            changed = True  # replaced an old entry
        else:
            changed = True  # added a new entry

    settings["hooks"][event] = hooks_list
    return changed


def _remove_hook_entries(settings: dict, event: str, match_str: str) -> bool:
    """Remove hook entries containing match_str. Clean empty lists. Returns changed."""
    hooks = settings.get("hooks", {})
    if event not in hooks:
        return False

    before = len(hooks[event])
    hooks[event] = [e for e in hooks[event] if match_str not in json.dumps(e)]
    after = len(hooks[event])

    if not hooks[event]:
        del hooks[event]
    if not hooks:
        if "hooks" in settings:
            del settings["hooks"]

    return after < before


# ---------------------------------------------------------------------------
# Permission management
# ---------------------------------------------------------------------------

def _add_permissions(settings: dict, patterns: list[str]) -> bool:
    """Add permission patterns to settings.permissions.allow. Returns changed."""
    if "permissions" not in settings:
        settings["permissions"] = {}
    if "allow" not in settings["permissions"]:
        settings["permissions"]["allow"] = []

    allow = settings["permissions"]["allow"]
    changed = False
    for pattern in patterns:
        if pattern not in allow:
            allow.append(pattern)
            changed = True
    return changed


def _remove_permissions(settings: dict, patterns: set[str]) -> bool:
    """Remove permission patterns from settings.permissions.allow. Returns changed."""
    perms = settings.get("permissions", {})
    if "allow" not in perms:
        return False

    before = len(perms["allow"])
    perms["allow"] = [e for e in perms["allow"] if e not in patterns]

    if not perms["allow"]:
        del perms["allow"]
    if not perms:
        if "permissions" in settings:
            del settings["permissions"]

    return len(perms.get("allow", [])) < before or (before > 0 and "allow" not in perms)


# ---------------------------------------------------------------------------
# Gitignore helper
# ---------------------------------------------------------------------------

def _ensure_gitignore_entry(path: Path, entry: str) -> bool:
    """Append entry to .gitignore if not already present. Returns changed."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if entry in text.splitlines():
            return False
        # Ensure trailing newline before appending
        if text and not text.endswith("\n"):
            text += "\n"
        text += entry + "\n"
        path.write_text(text, encoding="utf-8")
    else:
        path.write_text(entry + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------

def _confirm(msg: str, yes: bool = False) -> bool:
    """Ask for confirmation. Returns True if user agrees or --yes was passed."""
    if yes:
        return True
    try:
        answer = input(f"{msg} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------------------
# setup_mcp
# ---------------------------------------------------------------------------

def setup_mcp(args) -> None:
    """Install memctl as an MCP server for Claude Code and/or Claude Desktop."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run
    force = getattr(args, "force", False)
    yes = getattr(args, "yes", False)
    client = getattr(args, "client", "claude-code")

    _setup_info("memctl setup mcp")

    # Step 1: Check Python version
    if sys.version_info < (3, 10):
        _setup_fail(f"Python >= 3.10 required (found {sys.version})")
        sys.exit(1)
    _setup_ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    # Step 2: Check mcp importable
    try:
        import importlib
        importlib.import_module("mcp")
        _setup_ok("mcp package available")
    except ImportError:
        hint = _install_hint("mcp")
        _setup_warn(f"mcp package not found — install with: {hint}")
        _setup_warn("MCP server will not start until mcp is installed")

    # Step 3: Resolve DB path
    db_path = getattr(args, "db", None) or os.environ.get("MEMCTL_DB")
    if not db_path:
        db_path = str(Path.home() / ".local" / "share" / "memctl" / "memory.db")
    db_path = str(Path(db_path).resolve())
    _setup_info(f"Database: {db_path}")

    # Step 4: Build MCP server entry
    mcp_entry = {
        "command": "memctl",
        "args": ["serve", "--db", db_path],
    }

    # Step 5: Configure clients
    clients = _resolve_clients(client)
    for c in clients:
        config_path = _resolve_client_config(c)
        _setup_info(f"Configuring {c}: {config_path}")

        if dry_run:
            _setup_info(f"[dry-run] Would set mcpServers.memctl in {config_path}")
            continue

        config = _read_json(config_path)
        if config_path.exists():
            bak = _backup_file(config_path)
            if bak:
                _setup_info(f"Backup: {bak}")

        if "mcpServers" not in config:
            config["mcpServers"] = {}
        config["mcpServers"]["memctl"] = mcp_entry
        _write_json(config_path, config)
        _setup_ok(f"mcpServers.memctl configured in {config_path}")

    # Step 6: Init workspace if needed
    db_dir = Path(db_path).parent
    if not db_dir.exists() and not dry_run:
        db_dir.mkdir(parents=True, exist_ok=True)
        _setup_ok(f"Created directory: {db_dir}")

    # Step 7: Verify server
    if not dry_run:
        try:
            result = subprocess.run(
                ["memctl", "serve", "--check", "--db", db_path],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                _setup_ok("Server verification passed")
            else:
                _setup_warn("Server verification failed (non-fatal)")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            _setup_warn("Could not verify server (memctl not in PATH or timeout)")

    _setup_ok("MCP setup complete")


# ---------------------------------------------------------------------------
# setup_eco
# ---------------------------------------------------------------------------

def setup_eco(args) -> None:
    """Install eco mode: hooks, slash commands, strategy file, permissions."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run
    force = getattr(args, "force", False)
    yes = getattr(args, "yes", False)
    db_root = getattr(args, "db_root", None) or ".memory"

    _setup_info("memctl setup eco")

    claude_dir = Path(".claude")
    settings_path = _claude_code_local_settings()
    eco_dir = claude_dir / "eco"
    commands_dir = claude_dir / "commands"
    templates = _templates_dir()
    eco_templates = templates / "eco"

    # Step 1: Register MCP server with --db-root
    db_path = str(Path(db_root) / "memory.db")
    mcp_entry = {
        "command": "memctl",
        "args": ["serve", "--db-root", db_root],
    }

    if dry_run:
        _setup_info(f"[dry-run] Would set mcpServers.memctl in {settings_path}")
    else:
        settings = _read_json(settings_path)
        if settings_path.exists():
            _backup_file(settings_path)
        if "mcpServers" not in settings:
            settings["mcpServers"] = {}
        settings["mcpServers"]["memctl"] = mcp_entry
        _write_json(settings_path, settings)
        _setup_ok(f"MCP server registered in {settings_path}")

    # Step 2: Register eco-hint hook (UserPromptSubmit)
    eco_hint_entry = _build_hook_entry("memctl hooks eco-hint")
    if dry_run:
        _setup_info("[dry-run] Would register UserPromptSubmit eco-hint hook")
    else:
        settings = _read_json(settings_path)
        _add_hook_entries(settings, "UserPromptSubmit",
                          [eco_hint_entry], ["eco-hint"])
        _write_json(settings_path, settings)
        _setup_ok("eco-hint hook registered (UserPromptSubmit)")

    # Step 3: Register eco-nudge hook (PreToolUse)
    eco_nudge_entry = _build_hook_entry("memctl hooks eco-nudge")
    if dry_run:
        _setup_info("[dry-run] Would register PreToolUse eco-nudge hook")
    else:
        settings = _read_json(settings_path)
        _add_hook_entries(settings, "PreToolUse",
                          [eco_nudge_entry], ["eco-nudge"])
        _write_json(settings_path, settings)
        _setup_ok("eco-nudge hook registered (PreToolUse)")

    # Step 4: Add permissions
    if dry_run:
        _setup_info("[dry-run] Would add Bash(memctl *) permission")
    else:
        settings = _read_json(settings_path)
        _add_permissions(settings, _ECO_PERMISSIONS)
        _write_json(settings_path, settings)
        _setup_ok("Bash(memctl *) auto-approved")

    # Step 5: Copy ECO.md strategy file
    eco_md_src = eco_templates / "ECO.md"
    eco_md_dst = eco_dir / "ECO.md"
    if dry_run:
        _setup_info(f"[dry-run] Would copy ECO.md → {eco_md_dst}")
    else:
        if eco_md_src.exists():
            copied = _copy_template(eco_md_src, eco_md_dst, force=force)
            if copied:
                _setup_ok(f"ECO.md → {eco_md_dst}")
            else:
                _setup_info(f"ECO.md already exists: {eco_md_dst} (use --force to overwrite)")
        else:
            _setup_warn(f"Template not found: {eco_md_src}")

    # Step 6: Write eco config
    eco_config_path = eco_dir / "config.json"
    if dry_run:
        _setup_info(f"[dry-run] Would write {eco_config_path}")
    else:
        from memctl import __version__
        eco_config = {"db_path": db_path, "version": __version__}
        _write_json(eco_config_path, eco_config)
        _setup_ok(f"Eco config → {eco_config_path}")

    # Step 7: Copy slash commands
    commands_src = eco_templates / "commands"
    if dry_run:
        _setup_info(f"[dry-run] Would copy {len(_SLASH_COMMANDS)} slash commands")
    else:
        copied_count = 0
        for cmd_name in _SLASH_COMMANDS:
            src = commands_src / cmd_name
            dst = commands_dir / cmd_name
            if src.exists():
                if _copy_template(src, dst, force=force):
                    copied_count += 1
            else:
                # eco.md template lives in eco/ not eco/commands/
                alt_src = eco_templates / cmd_name
                if alt_src.exists():
                    if _copy_template(alt_src, dst, force=force):
                        copied_count += 1
        _setup_ok(f"{copied_count} slash command(s) installed → {commands_dir}")

    # Step 8: Append .memory/ to .gitignore
    gitignore = Path(".gitignore")
    if dry_run:
        _setup_info("[dry-run] Would ensure .memory/ in .gitignore")
    else:
        if _ensure_gitignore_entry(gitignore, ".memory/"):
            _setup_ok(".memory/ added to .gitignore")
        else:
            _setup_info(".memory/ already in .gitignore")

    # Step 9: Check extraction capabilities (info only)
    _check_extraction_capabilities()

    # Step 10: Init workspace
    db_dir = Path(db_root)
    if not db_dir.exists() and not dry_run:
        db_dir.mkdir(parents=True, exist_ok=True)
        _setup_ok(f"Created directory: {db_dir}")

    _setup_ok("Eco setup complete")


# ---------------------------------------------------------------------------
# setup_hooks
# ---------------------------------------------------------------------------

def setup_hooks(args) -> None:
    """Install safety-guard and audit-logger hooks (global settings)."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run
    yes = getattr(args, "yes", False)

    _setup_info("memctl setup hooks")

    settings_path = _claude_code_settings()

    guard_entry = _build_hook_entry("memctl hooks safety-guard")
    logger_entry = _build_hook_entry("memctl hooks audit-logger")

    if dry_run:
        _setup_info(f"[dry-run] Would register PreToolUse safety-guard in {settings_path}")
        _setup_info(f"[dry-run] Would register PostToolUse audit-logger in {settings_path}")
        _setup_ok("Hooks setup complete (dry-run)")
        return

    settings = _read_json(settings_path)
    if settings_path.exists():
        _backup_file(settings_path)

    _add_hook_entries(settings, "PreToolUse",
                      [guard_entry], ["safety-guard"])
    _add_hook_entries(settings, "PostToolUse",
                      [logger_entry], ["audit-logger"])

    _write_json(settings_path, settings)
    _setup_ok(f"safety-guard registered (PreToolUse) in {settings_path}")
    _setup_ok(f"audit-logger registered (PostToolUse) in {settings_path}")
    _setup_ok("Hooks setup complete")


# ---------------------------------------------------------------------------
# teardown_mcp
# ---------------------------------------------------------------------------

def teardown_mcp(args) -> None:
    """Remove memctl MCP server entries from client configs."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run
    client = getattr(args, "client", "claude-code")

    _setup_info("memctl teardown mcp")

    clients = _resolve_clients(client)
    for c in clients:
        config_path = _resolve_client_config(c)
        if not config_path.exists():
            _setup_info(f"Not found: {config_path} (nothing to remove)")
            continue

        _setup_info(f"Processing: {config_path}")

        if dry_run:
            _setup_info(f"[dry-run] Would remove mcpServers.memctl from {config_path}")
            continue

        config = _read_json(config_path)
        changed = False

        if "mcpServers" in config and "memctl" in config["mcpServers"]:
            del config["mcpServers"]["memctl"]
            if not config["mcpServers"]:
                del config["mcpServers"]
            changed = True

        if changed:
            _backup_file(config_path)
            _write_json(config_path, config)
            _setup_ok(f"Removed mcpServers.memctl from {config_path}")
        else:
            _setup_info(f"No memctl entry in {config_path}")

    _setup_ok("MCP teardown complete")
    _setup_info("Note: .memory/ data is preserved")


# ---------------------------------------------------------------------------
# teardown_eco
# ---------------------------------------------------------------------------

def teardown_eco(args) -> None:
    """Remove eco mode artifacts. Preserves .memory/ data."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run

    _setup_info("memctl teardown eco")

    claude_dir = Path(".claude")
    settings_path = _claude_code_local_settings()
    eco_dir = claude_dir / "eco"
    commands_dir = claude_dir / "commands"
    hooks_dir = claude_dir / "hooks"

    removed = 0

    # Step 1: Remove hook files
    for hook_name in ("eco-hint.sh", "eco-nudge.sh"):
        hook_file = hooks_dir / hook_name
        if hook_file.exists():
            if dry_run:
                _setup_info(f"[dry-run] Would remove: {hook_file}")
            else:
                hook_file.unlink()
                _setup_ok(f"Removed: {hook_file}")
            removed += 1

    # Step 2: Remove eco-hint and eco-nudge from settings
    if settings_path.exists():
        if dry_run:
            _setup_info(f"[dry-run] Would remove eco hooks from {settings_path}")
        else:
            settings = _read_json(settings_path)
            bak = _backup_file(settings_path)
            if bak:
                _setup_info(f"Backup: {bak}")

            changed = False
            if _remove_hook_entries(settings, "UserPromptSubmit", "eco-hint"):
                changed = True
                _setup_ok("Removed eco-hint from UserPromptSubmit")
            if _remove_hook_entries(settings, "PreToolUse", "eco-nudge"):
                changed = True
                _setup_ok("Removed eco-nudge from PreToolUse")

            # Remove eco permissions
            if _remove_permissions(settings, set(_ECO_PERMISSIONS)):
                changed = True
                _setup_ok("Removed Bash(memctl *) permission")

            if changed:
                _write_json(settings_path, settings)

    # Step 3: Remove ECO.md
    eco_file = eco_dir / "ECO.md"
    if eco_file.exists():
        if dry_run:
            _setup_info(f"[dry-run] Would remove: {eco_file}")
        else:
            eco_file.unlink()
            _setup_ok(f"Removed: {eco_file}")
        removed += 1

    # Step 4: Remove eco config
    eco_config = eco_dir / "config.json"
    if eco_config.exists():
        if dry_run:
            _setup_info(f"[dry-run] Would remove: {eco_config}")
        else:
            eco_config.unlink()
            _setup_ok(f"Removed: {eco_config}")
        removed += 1

    # Step 5: Remove slash commands
    for cmd_name in _SLASH_COMMANDS:
        cmd_file = commands_dir / cmd_name
        if cmd_file.exists():
            if dry_run:
                _setup_info(f"[dry-run] Would remove: {cmd_file}")
            else:
                cmd_file.unlink()
                _setup_ok(f"Removed: {cmd_file}")
            removed += 1

    # Step 6: Clean up empty directories
    if not dry_run:
        for d in (eco_dir, commands_dir):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
                _setup_ok(f"Removed empty directory: {d}")

    _setup_ok(f"Eco teardown complete ({removed} artifact(s))")
    _setup_info("Note: .memory/ data is preserved")


# ---------------------------------------------------------------------------
# teardown_hooks
# ---------------------------------------------------------------------------

def teardown_hooks(args) -> None:
    """Remove safety-guard and audit-logger hook entries from global settings."""
    global _DRY_RUN
    dry_run = getattr(args, "dry_run", False)
    _DRY_RUN = dry_run

    _setup_info("memctl teardown hooks")

    settings_path = _claude_code_settings()
    if not settings_path.exists():
        _setup_info(f"Not found: {settings_path} (nothing to remove)")
        _setup_ok("Hooks teardown complete")
        return

    if dry_run:
        _setup_info(f"[dry-run] Would remove safety-guard from PreToolUse")
        _setup_info(f"[dry-run] Would remove audit-logger from PostToolUse")
        _setup_ok("Hooks teardown complete (dry-run)")
        return

    settings = _read_json(settings_path)
    _backup_file(settings_path)

    changed = False
    if _remove_hook_entries(settings, "PreToolUse", "safety-guard"):
        changed = True
        _setup_ok("Removed safety-guard from PreToolUse")
    if _remove_hook_entries(settings, "PostToolUse", "audit-logger"):
        changed = True
        _setup_ok("Removed audit-logger from PostToolUse")

    if changed:
        _write_json(settings_path, settings)

    _setup_ok("Hooks teardown complete")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_clients(client: str) -> list[str]:
    """Resolve --client to a list of client names."""
    if client == "all":
        return ["claude-code", "claude-desktop"]
    return [client]


def _resolve_client_config(client: str) -> Path:
    """Resolve client name to config file path."""
    if client == "claude-code":
        return _claude_code_settings()
    elif client == "claude-desktop":
        return _claude_desktop_config()
    else:
        _setup_fail(f"Unknown client: {client}")
        sys.exit(1)


def _check_extraction_capabilities() -> None:
    """Check optional doc extraction packages (info only, not fatal)."""
    packages = {
        "docx": "python-docx",
        "pptx": "python-pptx",
        "openpyxl": "openpyxl",
        "odf": "odfpy",
        "pypdf": "pypdf",
    }
    available = []
    missing = []
    for mod_name, pkg_name in packages.items():
        try:
            __import__(mod_name)
            available.append(pkg_name)
        except ImportError:
            missing.append(pkg_name)

    if available:
        _setup_info(f"Document extraction: {', '.join(available)}")
    if missing:
        hint = _install_hint("docs")
        _setup_info(f"Optional (not installed): {', '.join(missing)}")
        _setup_info(f"Install with: {hint}")

"""
memctl hooks — Pure-Python implementations of Claude Code hooks.

Cross-platform equivalents of the shell-based hooks in templates/.
All functions use only Python stdlib — no compiled or optional dependencies.

Four hooks:
    eco-hint      UserPromptSubmit  — inject eco mode context
    eco-nudge     PreToolUse        — contextual eco reminder for search tools
    safety-guard  PreToolUse        — block dangerous shell commands
    audit-logger  PostToolUse       — log all tool actions

Entry points:
    run_hook(name)          — dispatch to a named hook (used by CLI)
    hook_eco_hint()         — direct call (used by .py template wrappers)
    hook_eco_nudge()        — direct call
    hook_safety_guard()     — direct call
    hook_audit_logger()     — direct call

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Deny patterns — destructive commands (safety-guard)
# ---------------------------------------------------------------------------

_DENY_PATTERNS = [
    r"rm -rf",
    r"rm -r /",
    r"rmdir --ignore",
    r"dd if=",
    r"mkfs\.",
    r"fdisk",
    r"sudo ",
    r"su -",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    r"curl.*\|.*bash",
    r"shutdown",
    r"reboot",
    r"init 0",
]

_GIT_DENY = [
    r"git push --force",
    r"git push -f ",
    r"git reset --hard",
    r"git clean -fd",
    r"git branch -D",
]

# Bash(find/ls) exploration detection (eco-nudge)
_FIND_LS_RE = re.compile(
    r"^find\s|.*\|\s*find\s|.*\|find\s|ls\s+-.*R|find\s+\.|find\s+/"
)

# Narrow file path detection: ends with a file extension (.py, .java, etc.)
_FILE_EXT_RE = re.compile(r"\.[a-zA-Z]{1,6}$")

# Broad glob detection: contains **, *.*, or */
_BROAD_GLOB_RE = re.compile(r"\*\*|\*\.\*|\*/")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_eco_db_path() -> str:
    """Read .claude/eco/config.json → db_path, fallback MEMCTL_DB, then .memory/memory.db."""
    eco_config = Path(".claude/eco/config.json")
    if eco_config.is_file():
        try:
            with open(eco_config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            db = cfg.get("db_path", "")
            if db:
                return db
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return os.environ.get("MEMCTL_DB", ".memory/memory.db")


def _count_items(db_path: str) -> int:
    """SELECT COUNT(*) FROM memory_items. Returns 0 on any error."""
    try:
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def _eco_disabled() -> bool:
    """True if .memory/.eco-disabled sentinel exists."""
    return Path(".memory/.eco-disabled").is_file()


def _read_stdin_json() -> dict | None:
    """Read and parse JSON from stdin. Returns None on any error."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Hook: eco-hint  (UserPromptSubmit)
# ---------------------------------------------------------------------------


def hook_eco_hint() -> None:
    """Inject eco mode context into Claude Code (UserPromptSubmit).

    4-way branch:
      1. Disabled    → empty context (zero overhead)
      2. No DB       → bootstrap hint (suggests /scan)
      3. Cold start  → memory nearly empty (< 10 items), suggests memctl push
      4. DB populated → situational hint (item count + escalation ladder)

    stdout: JSON with additionalContext key
    stderr: none
    exit: always 0
    """
    # Branch 1: eco disabled
    if _eco_disabled():
        print('{"additionalContext": ""}')
        return

    db_path = _resolve_eco_db_path()

    # Branch 2: no DB yet
    if not Path(db_path).is_file():
        print(json.dumps({
            "additionalContext": (
                "memctl eco mode is active but no memory database exists yet. "
                "Run /scan to index your project and bootstrap memory. "
                "Example: /scan ."
            )
        }))
        return

    # Count items for branch selection
    item_count = _count_items(db_path)

    # Branch 3: Cold start — DB exists but nearly empty
    if item_count < 10:
        print(json.dumps({
            "additionalContext": (
                f"memctl eco mode is active but memory is nearly empty ({item_count} items).\\n"
                "\\nCold start — ingest your codebase first:\\n"
                "  memctl push 'project description' --source /path/to/codebase/\\n"
                "or use the slash command:\\n"
                "  /scan /path/to/codebase/\\n"
                "\\nThis indexes all files into memory. After ingestion, use /recall <keywords> to search.\\n"
                "\\nDo NOT explore with find, ls, Grep, or Glob before ingesting — "
                "memory tools are faster and persist across sessions."
            )
        }))
        return

    # Branch 4: DB populated — situational hint (item count + escalation ladder)
    print(json.dumps({
        "additionalContext": (
            f"memctl eco mode is active — {item_count} indexed items.\\n"
            "\\nFor codebase exploration (architecture, cross-file patterns, conventions), "
            "follow this order:\\n"
            "0. memctl push --source <path> — ingest first if exploring a new directory "
            "not yet indexed\\n"
            "1. memory_inspect — structural overview (file tree, sizes, observations)\\n"
            "2. memory_recall or /recall <keywords> — FTS5 search, token-budgeted "
            "(use 2-3 identifiers or domain terms)\\n"
            "3. Native Grep/Glob — only after recall returns 0 results despite query refinement\\n"
            "4. Native Read — for editing or line-level precision on a specific known file\\n"
            "\\nEco wins over native tools when: exploring architecture across many files, "
            "searching cross-file patterns, querying binary formats (.docx/.pdf/.pptx), "
            "resuming prior session knowledge.\\n"
            "\\nWhen eco is ON, structure answers as: Retrieved (statements supported by "
            "recalled chunks, cite source paths) then Analysis (your reasoning, hypotheses, "
            "next steps).\\n"
            "\\nBypass eco for: editing files, reading a single known small file, git operations, "
            "targeted symbol lookup in a known file."
        )
    }))


# ---------------------------------------------------------------------------
# Hook: eco-nudge  (PreToolUse)
# ---------------------------------------------------------------------------


def hook_eco_nudge() -> None:
    """PreToolUse hook: contextual eco reminder for search tools.

    Fires before Grep, Glob, and Bash(find) tool calls. Never blocks (exit 0).
    Injects a short stderr reminder when eco is ON, DB is populated (>= 200 items),
    and the search looks like exploration.

    stdin: JSON (tool_name, tool_input)
    stdout: none
    stderr: nudge messages
    exit: always 0
    """
    data = _read_stdin_json()
    if data is None:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    pattern = tool_input.get("pattern", "")
    path_arg = tool_input.get("path", "")
    bash_cmd = tool_input.get("command", "")

    # Only inspect search-like tools
    if tool_name == "Bash":
        if not _FIND_LS_RE.search(bash_cmd):
            return
    elif tool_name not in ("Grep", "Glob"):
        return

    # Check eco state: sentinel absent = eco ON
    if _eco_disabled():
        return

    # Resolve DB path
    db_path = _resolve_eco_db_path()

    # No DB — nudge toward ingestion
    if not Path(db_path).is_file():
        print(
            '[eco] No memory database. Ingest first: '
            'memctl push "description" --source /path/ or /scan /path/',
            file=sys.stderr,
        )
        return

    # Count indexed items
    item_count = _count_items(db_path)

    # Cold start: memory exists but nearly empty — strong nudge toward push
    if item_count < 10:
        print(
            f'[eco] Memory nearly empty ({item_count} items). '
            'Ingest first: memctl push "description" --source /path/ or /scan /path/',
            file=sys.stderr,
        )
        return

    # Small stores (< 200 items): no nudge — not enough data to justify
    if item_count < 200:
        return

    # -- Grep: check if this looks like exploration --
    if tool_name == "Grep":
        # Skip narrow lookups: targeting a single specific file
        if path_arg and _FILE_EXT_RE.search(path_arg):
            return

        # Skip trivial patterns: very short single-token lookups
        plen = len(pattern)
        has_space = " " in pattern or "\t" in pattern

        if plen < 6 and not has_space:
            return

        print(
            f'[eco] {item_count} indexed items. '
            f'For cross-file search, try: /recall {pattern}',
            file=sys.stderr,
        )

    # -- Glob: check if this is a broad traversal --
    elif tool_name == "Glob":
        if _BROAD_GLOB_RE.search(pattern):
            print(
                f'[eco] {item_count} indexed items. '
                'For structural overview, try: memory_inspect',
                file=sys.stderr,
            )

    # -- Bash(find): intercept find/ls exploration --
    elif tool_name == "Bash":
        print(
            f'[eco] {item_count} indexed items. '
            'memory_inspect provides structural overview without find/ls. '
            'Try: memory_inspect or /recall <keywords>',
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Hook: safety-guard  (PreToolUse)
# ---------------------------------------------------------------------------


def hook_safety_guard() -> None:
    """PreToolUse hook: block dangerous shell commands.

    Only inspects Bash tool calls; all other tools pass through.

    stdin: JSON (tool_name, tool_input)
    stdout: none
    stderr: block message (on deny)
    exit: 0 (allowed) or 2 (blocked)
    """
    data = _read_stdin_json()
    if data is None:
        # Fail open on malformed JSON
        return

    tool_name = data.get("tool_name", "")

    # Only inspect Bash tool calls
    if tool_name != "Bash":
        return

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return
    command = tool_input.get("command", "")

    # Check against deny patterns
    for pat in _DENY_PATTERNS + _GIT_DENY:
        if re.search(pat, command):
            print(
                f'[memctl guard] BLOCKED: command matches deny pattern "{pat}"',
                file=sys.stderr,
            )
            sys.exit(2)


# ---------------------------------------------------------------------------
# Hook: audit-logger  (PostToolUse)
# ---------------------------------------------------------------------------


def hook_audit_logger() -> None:
    """PostToolUse hook: log all tool actions.

    Appends timestamped line to .agent_logs/memctl_commands.log.

    stdin: JSON (tool_name, tool_input)
    stdout: none
    stderr: none
    exit: always 0
    """
    data = _read_stdin_json()
    if data is None:
        return

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input", {})

    # Summarize input (first 120 chars of first string value)
    summary = ""
    if isinstance(tool_input, dict):
        for v in tool_input.values():
            if isinstance(v, str) and v.strip():
                summary = v[:120].replace("\n", " ")
                break

    entry = f"{tool_name}: {summary}"

    log_dir = Path(".agent_logs")
    log_file = log_dir / "memctl_commands.log"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {entry}\n")
    except OSError:
        pass  # Fail silently — never block tool execution


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HOOKS = {
    "eco-hint": hook_eco_hint,
    "eco-nudge": hook_eco_nudge,
    "safety-guard": hook_safety_guard,
    "audit-logger": hook_audit_logger,
}

HOOK_NAMES = sorted(_HOOKS.keys())


def run_hook(name: str) -> None:
    """Dispatch to a named hook function.

    Args:
        name: One of 'eco-hint', 'eco-nudge', 'safety-guard', 'audit-logger'.

    Raises:
        SystemExit(1): If name is not a recognized hook.
    """
    func = _HOOKS.get(name)
    if func is None:
        print(f"Unknown hook: {name}", file=sys.stderr)
        print(f"Available hooks: {', '.join(HOOK_NAMES)}", file=sys.stderr)
        sys.exit(1)
    func()

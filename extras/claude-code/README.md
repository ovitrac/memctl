# Claude Code Integration — Optional Hooks

Optional defense-in-depth hooks for Claude Code users running memctl
as an MCP server under automated agent workflows.

## What the hooks do

| Hook | Event | Action |
|------|-------|--------|
| `memctl_safety_guard.sh` | PreToolUse | Blocks dangerous shell commands (rm -rf, sudo, dd, git push --force, etc.) |
| `memctl_audit_logger.sh` | PostToolUse | Logs all tool actions to `.agent_logs/memctl_commands.log` |

## What the hooks do NOT do

- They do **not** replace memctl's built-in policy engine (secrets, injection, PII detection)
- They do **not** auto-inject memory context (memctl is explicit and CLI-driven)
- They are **not** required for memctl to function — they are an additional safety layer

## Install

```bash
scripts/install_claude_hooks.sh
```

Options:
- `--dry-run`: preview without modifying
- `--yes`: skip confirmation prompt

## Uninstall

```bash
scripts/uninstall_mcp.sh --hooks-only
```

## Supported clients

- Claude Code (primary)
- Claude Desktop: hooks are not applicable (no hook system)

## Requirements

- `python3` in PATH (for JSON parsing in hooks)
- Claude Code with hooks support (`.claude/settings.json`)

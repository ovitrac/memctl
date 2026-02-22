You are resetting the memctl memory database.

Argument received: $ARGUMENTS

This is a DESTRUCTIVE operation.

## Primary path: MCP tools (preferred)

If the `memory_reset` MCP tool is available:

- **"all"** (i.e. `/forget all`):
  Call `memory_reset(dry_run=true)` first.
  Display the preview: items, events, links that would be deleted.
  Tell the user: "Run `/forget all --confirm` to execute."

- **"all --confirm"** (i.e. `/forget all --confirm`):
  Call `memory_reset(dry_run=false, preserve_mounts=true)`.
  Display: records cleared, mounts preserved.
  Finish with: "Memory cleared. Use /scan to rebuild from project files."

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl reset`
(NOT `memctl forget` — that command does not exist):

```bash
# Preview (dry run)
memctl reset --db .memory/memory.db --dry-run

# Execute (requires --confirm safety gate)
memctl reset --db .memory/memory.db --confirm
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Safety gate

- **No argument or anything other than "all"**:
  Do NOT call any tool. Respond:
  "Safety gate: `/forget` requires `all` as argument.
  Usage: `/forget all` — preview what would be deleted.
  Then: `/forget all --confirm` — execute the reset.
  Synced content can be rebuilt with `/scan`. Manual observations
  (from `/remember`) will be lost. Export first with `memory_export`."

## Notes

- This truncates all content tables (items, events, links, sync cache).
- Mount registrations and FTS settings are preserved.
- The operation is atomic, audited, and goes through memctl middleware.

You are checking the health of memctl memory for this project.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_status` MCP tool is available:

- Call `memory_status()`.
  Present the results as a concise dashboard: eco mode state, item counts
  by tier, FTS health, mount points, last scan timestamp.

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl status`:

```bash
memctl status --db .memory/memory.db
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- This is a **read-only** command. It never modifies the database.
- If the database does not exist yet, it reports eco mode state and
  suggests `/scan` to bootstrap.

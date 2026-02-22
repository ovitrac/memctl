You are scanning a project folder to populate memctl memory.

Argument received: $ARGUMENTS

This is the **bootstrap command** — it creates the memory database and indexes
a folder in one step.

## Primary path: MCP tools (preferred)

If the `memory_inspect` MCP tool is available, use it:

- **With a path argument** (e.g. `/scan src/` or `/scan .`):
  Call `memory_inspect(path="<argument>")`.
  It auto-mounts, auto-syncs, and produces a structural overview.

- **No argument** (just `/scan`):
  Call `memory_inspect(path=".")`.

- **With `--full` flag** (e.g. `/scan . --full`):
  Call `memory_sync(path="<argument>", full=true)` first,
  then `memory_inspect(path="<argument>")`.

## Fallback: CLI commands

If MCP tools are not available, use these exact CLI commands.
The DB path comes from `.claude/eco/config.json` (`db_path` field),
or defaults to `.memory/memory.db`.

```bash
# 1. Mount and sync (path is POSITIONAL, not --path)
memctl sync --db .memory/memory.db <path>

# 2. Inspect
memctl inspect --db .memory/memory.db <path>
```

If `/scan` is run with no argument, use `.` as the path.

## Display

Show the structural overview: file count, chunk count, size,
extension breakdown, largest files, observations.

Finish with: "Memory populated. Next steps:"
- "/recall <keywords> — search your memory"
- "/remember <text> — store an observation"

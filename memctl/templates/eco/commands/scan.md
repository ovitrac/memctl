You are scanning a project folder to populate memctl memory.

Argument received: $ARGUMENTS

This is the **bootstrap command** — it creates the memory database and indexes
a folder in one step.

Behavior:

- **With a path argument** (e.g. `/scan src/` or `/scan .`):
  Call the `memory_inspect` MCP tool with `path` set to the argument.
  `memory_inspect` auto-mounts, auto-syncs, and produces a structural overview.
  Display the structural overview (file count, chunk count, extension breakdown,
  largest files, observations).
  Finish with: "Memory populated. Use /recall <query> to search."

- **No argument** (just `/scan`):
  Call `memory_inspect` with `path` set to `"."` (current directory).
  Display the result as above.

- **With `--full` flag** (e.g. `/scan . --full`):
  Call `memory_sync` with `path` set to the path argument and `full=true`
  to force a complete re-sync. Then call `memory_inspect` for the overview.

After scanning, suggest next steps:
- "/recall <keywords> — search your memory"
- "/remember <text> — store an observation"

You are searching memctl memory for relevant content.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_recall` MCP tool is available:

- **With query terms** (e.g. `/recall authentication flow`):
  Call `memory_recall(query="<argument>")`.
  Display: matched items, tokens used, FTS strategy, hints if any.

- **No argument**: Ask for 2-3 keywords.

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl search`
(NOT `memctl recall` — that command does not exist).

```bash
memctl search --db .memory/memory.db "<query>"
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- Identifiers (class/function names) work best.
- FTS cascade runs automatically (AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK).
- If zero results: try identifiers, run /scan, consider /reindex en.

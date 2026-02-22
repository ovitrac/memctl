You are exporting memctl memory items for backup or transfer.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_export` MCP tool is available:

- **Full export** (just `/export`):
  Call `memory_export()`.
  Report: items exported, output file path.

- **Filtered export** (e.g. `/export ltm`):
  Call `memory_export(tier="ltm")`.

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl export`:

```bash
# Full export (all active items):
memctl export --db .memory/memory.db > .memory/backup_$(date +%Y%m%d).jsonl

# Filter by tier:
memctl export --db .memory/memory.db --tier ltm > .memory/backup_ltm.jsonl
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- Output format is JSONL (one JSON object per line).
- Archived items are excluded by default. Use `--include-archived` to include.
- The exported file can be re-imported with `memctl import <file>`.
- **Recommended before `/forget all`** to preserve observations.

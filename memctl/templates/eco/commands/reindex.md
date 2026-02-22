You are rebuilding the memctl FTS5 search index.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_reindex` MCP tool is available:

- **With preset** (e.g. `/reindex en`):
  Call `memory_reindex(tokenizer="<argument>", dry_run=true)`.
  Display preview: current tokenizer, new tokenizer, item count.
  Do NOT execute. Tell the user: "Run `/reindex en --confirm` to apply."

- **With preset + `--confirm`** (e.g. `/reindex en --confirm`):
  Call `memory_reindex(tokenizer="<preset>", dry_run=false)`.
  Display: items reindexed, tokenizer change, duration.

- **No argument** (just `/reindex`):
  Call `memory_reindex(dry_run=true)`.
  Display current tokenizer and explain presets:
  - `fr` — French-safe (accents normalized, no stemming). Default.
  - `en` — English with Porter stemming ("monitored" matches "monitoring").
  - `raw` — No normalization, maximum precision.

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl reindex`:

```bash
# Preview (dry run)
memctl reindex --db .memory/memory.db --tokenizer en --dry-run

# Execute
memctl reindex --db .memory/memory.db --tokenizer en
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

No conversational confirmation loop. Preview is always non-destructive.
Execution requires explicit `--confirm` flag (MCP) or omitting `--dry-run` (CLI).

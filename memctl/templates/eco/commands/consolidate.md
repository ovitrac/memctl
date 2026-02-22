You are consolidating memctl memory — merging similar short-term items and
promoting high-value findings to long-term memory.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_consolidate` MCP tool is available:

- **Preview** (just `/consolidate`):
  Call `memory_consolidate(dry_run=true)`.
  Display: clusters found, items that would be merged, promotions pending.
  Do NOT execute. Tell the user: "Run `/consolidate --confirm` to apply."

- **Execute** (`/consolidate --confirm`):
  Call `memory_consolidate(dry_run=false)`.
  Display: items merged, items promoted, merge chains.

## Fallback: CLI commands

If MCP tools are not available, the CLI command is `memctl consolidate`:

```bash
# Preview (dry run):
memctl consolidate --db .memory/memory.db --dry-run

# Execute:
memctl consolidate --db .memory/memory.db
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- Consolidation is **deterministic** — no LLM involved. Same input → same output.
- Merge winner: longest content → earliest created_at → lexicographic ID.
- Originals are archived (not deleted) and linked via `supersedes` relation.
- Auto-consolidation may trigger during `/remember` if STM exceeds threshold.

No conversational confirmation loop. Preview is always non-destructive.
Execution requires explicit `--confirm` flag (MCP) or omitting `--dry-run` (CLI).

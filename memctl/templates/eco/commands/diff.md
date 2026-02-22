You are comparing two memory items or showing how an item changed over time.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_diff` MCP tool is available:

- **Two items**: `memory_diff(id1="MEM-xxx", id2="MEM-yyy")`
- **Item vs revision**: `memory_diff(id1="MEM-xxx", revision=N)`
- **Current vs latest**: `memory_diff(id1="MEM-xxx")` (revision=0 = latest)

Display the unified diff and metadata changes. Report similarity score.
If identical, say so. This is a **read-only** operation.

## Fallback: CLI commands

If MCP tools are not available, use `memctl diff`:

```bash
# Compare two items:
memctl diff MEM-xxx MEM-yyy --db .memory/memory.db

# Item vs specific revision:
memctl diff MEM-xxx --revision 1 --db .memory/memory.db

# Current vs latest revision:
memctl diff MEM-xxx --latest --db .memory/memory.db

# JSON output:
memctl diff MEM-xxx MEM-yyy --db .memory/memory.db --json
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- This is a **read-only** operation — no mutations.
- Content diff uses unified diff format (like `git diff`).
- Metadata diff covers: title, tier, type, tags, validation, confidence, scope,
  injectable, archived, expires_at.
- Excluded from diff: id, created_at, updated_at, usage_count, content_hash.
- Do NOT use a phantom `memctl compare` command — it does NOT exist.

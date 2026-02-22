You are storing an observation as a persistent memory item.

Argument received: $ARGUMENTS

## Primary path: MCP tools (preferred)

If the `memory_propose` MCP tool is available:

- **With text argument** (e.g. `/remember The auth service uses JWT with 15-min tokens`):
  Call `memory_propose` with a JSON array containing one item:
  `[{"title": "<5-10 word summary>", "content": "<full text>",
    "tags": ["manual", "observation"], "type": "fact"}]`
  Display: accepted/rejected status and item ID if stored.

- **No argument**: Ask for the observation text.

## Fallback: CLI commands

If MCP tools are not available, use `memctl pull` (NOT `memctl remember` or
`memctl propose` — those commands do not exist). Pipe a JSON proposal via stdin:

```bash
echo '[{"title":"<summary>","content":"<full text>","tags":["manual","observation"],"type":"fact"}]' \
  | memctl pull --db .memory/memory.db
```

Read the DB path from `.claude/eco/config.json` (`db_path` field),
or default to `.memory/memory.db`.

## Notes

- Stored in STM by default. Policy engine applies (secrets, injection, PII).
- Use `/recall` to retrieve later. Use `memory_consolidate` to promote.

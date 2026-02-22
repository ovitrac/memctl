You are rebuilding the memctl FTS5 search index.

Argument received: $ARGUMENTS

Behavior:

- **With preset** (e.g. `/reindex en`):
  Call `memory_reindex` with `dry_run=true` and `tokenizer` set to the argument.
  Display preview: current tokenizer, new tokenizer, item count.
  Do NOT execute. Tell the user: "Run `/reindex en --confirm` to apply."

- **With preset + `--confirm`** (e.g. `/reindex en --confirm`):
  Call `memory_reindex` with `dry_run=false` and `tokenizer` set to the preset.
  Display: items reindexed, tokenizer change, duration.

- **No argument** (just `/reindex`):
  Call `memory_reindex` with `dry_run=true`.
  Display current tokenizer and explain presets:
  - `fr` — French-safe (accents normalized, no stemming). Default.
  - `en` — English with Porter stemming ("monitored" ↔ "monitoring").
  - `raw` — No normalization, maximum precision.

No conversational confirmation loop. Preview is always non-destructive.
Execution requires explicit `--confirm` flag.

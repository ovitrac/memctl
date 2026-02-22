You are managing the eco mode toggle for memctl.

Argument received: $ARGUMENTS

Behavior:

- **"on"** → Run `rm -f .claude/eco/.disabled`, then confirm:
  "eco mode enabled. Using memory_inspect, memory_recall, and persistent memory.
  Read .claude/eco/ECO.md for the full strategy."
  Then run `memctl stats --db .memory/memory.db 2>/dev/null` and display a brief summary
  (item count, tier breakdown). If the DB does not exist yet, say "No memory database yet — it will be created on first use."

- **"off"** → Run `touch .claude/eco/.disabled`, then confirm:
  "eco mode disabled. Using native Read/View only. No memory, no recall, no structural exploration."
  This is the vanilla Claude Code baseline.

- **"status"** → Check if `.claude/eco/.disabled` exists.
  If absent: report eco ON, then run `memctl stats --db .memory/memory.db 2>/dev/null` and show item count + last sync info.
  If present: report eco OFF.

- **No argument** → Same as "status".

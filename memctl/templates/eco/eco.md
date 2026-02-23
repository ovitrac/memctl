You are managing the eco mode toggle for memctl.

Argument received: $ARGUMENTS

**Primary path (MCP):** Call `memory_eco(action="on"|"off"|"status")`.
**Fallback (CLI):** Run `memctl eco on|off|status`.

Behavior:

- **"on"** → Call `memory_eco(action="on")`. Confirm:
  "eco mode enabled. Using memory_inspect, memory_recall, and persistent memory.
  Read .claude/eco/ECO.md for the full strategy."
  Then call `memory_status()` and display a brief summary (item count, tier breakdown).
  If the DB does not exist yet, say "No memory database yet.
  Run /scan to index your project and create the memory database.
  Example: /scan . (indexes current directory)."

- **"off"** → Call `memory_eco(action="off")`. Confirm:
  "eco mode disabled. Using native Read/View only. No memory, no recall, no structural exploration."
  This is the vanilla Claude Code baseline.

- **"status"** → Call `memory_eco(action="status")`.
  If eco_mode is "active": report eco ON, then call `memory_status()` and show item count + last sync info.
  If the DB does not exist yet, say "No memory database yet.
  Run /scan to index your project and create the memory database.
  Example: /scan . (indexes current directory)."
  If eco_mode is "disabled": report eco OFF.
  If eco_mode is "not installed": report eco not installed and suggest install_eco.sh.

- **No argument** → Same as "status".

**Tip:** Use `/remember` to store your observations. Raw content is
indexed automatically — your analytical findings are not. Use
`type="decision"` for architectural findings (auto-promotes to LTM).

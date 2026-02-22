# eco mode — Deterministic structural retrieval for Claude Code

Native Claude reads files. eco Claude queries architecture.

eco mode is a behavioral layer that replaces sequential file browsing with
deterministic structural retrieval and persistent cross-file reasoning.
Same task, better answers, fewer tokens, lower latency.

## What it does

| Native Claude | eco Claude |
|---------------|------------|
| Sequential file exploration | Structural + indexed retrieval |
| `head -200` truncation (imports, boilerplate) | Surgical chunk retrieval (exact function, exact algorithm) |
| Blind browsing — misses architecture in tests | Cross-file invariant discovery |
| No memory across sessions | Cumulative persistent memory (STM/MTM/LTM) |
| Binary files (.docx, .pdf, .xlsx) are opaque | Text extraction, chunking, full-text search |
| Sentence-based guessing | Deterministic keyword matching |

## Why it matters

The strongest wins are not token savings. They are:

1. **Surgical chunk retrieval** — eco returns `consolidate.py` lines 229-271 (the merge
   algorithm), not the module docstring. Native Claude reads 200+ lines of boilerplate
   before reaching the implementation.

2. **Cross-file invariant discovery** — eco surfaces architectural invariants from test
   files that sequential browsing never reaches. Architecture lives in tests. eco finds it.

3. **Bounded cost** — 2,700 tokens (eco) vs 14,400 tokens (native) for the same 5
   developer questions. ~5.3x reduction. Credible, measurable, repeatable.

## How it works

1. A **hook** (`UserPromptSubmit`) injects a ~50-token hint every turn, telling Claude
   that eco tools are available and preferred. Conditional on a flag file — toggleable
   at runtime via the `/eco` slash command.
2. A **strategy file** (`ECO.md`) provides detailed behavioral instructions that Claude
   reads on demand — escalation ladder, tool usage rules, fallback boundaries.
3. The **MCP server** provides 14 `memory_*` tools for structural exploration, selective
   recall, persistent memory, and binary format extraction.

eco mode is **advisory for retrieval, not restrictive for editing**. Claude uses native
`Read`/`View` when it needs raw content for editing, diffing, or line-level operations.

## The escalation ladder

Before falling back to native file reads, Claude follows this hierarchy:

1. `memory_inspect` — structural overview
2. `memory_recall` — selective content retrieval
3. `memory_loop` — iterative refinement (bounded)
4. Native `Read`/`View` — last resort for editing or line-level precision

## Toggle: /eco command

eco mode is ON by default. Toggle it live with the `/eco` slash command:

```
/eco status    → Check current state + memory stats
/eco on        → Enable eco mode (remove flag file)
/eco off       → Disable eco mode (create flag file)
/eco           → Same as /eco status
```

**Mechanism:** The hook checks for `.claude/eco/.disabled`. If the file exists,
the hook injects empty context (eco OFF). If absent, eco context is injected (eco ON).
This is a single syscall — no subprocess, no DB query, no latency.

**Demo flow (4 acts):**

```
Act 1 — Hard question     → "How does memctl enforce defense-in-depth?"
                             Native Claude reads 5+ files sequentially, misses test invariants.
Act 2 — eco ON            → memory_inspect + 2 recalls → full 4-layer answer in ~1,200 tokens.
Act 3 — FTS discipline    → Failing natural-language query → refined keyword query → success.
                             "eco rewards precision."
Act 4 — Persistence       → New session. Same question. Instant answer. No re-exploration.
                             "Claude no longer explores. It remembers."
```

Run `demos/eco_demo.sh` for a scripted version of this flow.

## Installation

```bash
# One-shot setup
pip install "memctl[mcp]"
bash "$(memctl scripts-path)/install_eco.sh" --db-root .memory
```

This single command:
- Registers the memctl MCP server with `--db-root .memory`
- Installs the eco hook (`.claude/hooks/eco-hint.sh`)
- Installs the strategy file (`.claude/eco/ECO.md`)
- Installs the `/eco` slash command (`.claude/commands/eco.md`)
- Validates server startup
- Adds `.memory/` to `.gitignore`
- Reports extraction capabilities

Options:

| Flag | Effect |
|------|--------|
| `--db-root PATH` | Where to store `memory.db` (default: `.memory/`) |
| `--dry-run` | Show what would be done without making changes |
| `--yes` | Skip confirmation prompts |
| `--force` | Overwrite existing ECO.md and hooks without backup |

## Uninstallation

```bash
bash "$(memctl scripts-path)/uninstall_eco.sh"
```

Removes eco hook, strategy file, and `/eco` slash command. Does **not** remove:
- `.memory/memory.db` (your knowledge — never deleted)
- MCP server config (use `bash "$(memctl scripts-path)/uninstall_mcp.sh"` for that)
- v0.8 safety hooks (PreToolUse/PostToolUse)

## Parallel with CloakMCP

eco mode follows the same operational pattern as
[CloakMCP](https://github.com/ovitrac/CloakMCP):

| Aspect | CloakMCP | eco (memctl) |
|--------|----------|--------------|
| Install | `pip install` + config | `pip install` + one script |
| After install | Transparent secret sanitization | Transparent token-efficient retrieval |
| Enforcement | Hook-based, deterministic | Hook-based, deterministic |
| Persistence | Encrypted vault | SQLite + FTS5 + WAL |
| User action | None (automatic) | None (automatic) |

Both intercept what reaches the LLM and replace raw content with a more efficient
representation, transparently. They can coexist — different hook events, orthogonal purposes.

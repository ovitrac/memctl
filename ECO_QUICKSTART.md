# eco Mode Quickstart — Claude Code Edition

**Version**: 0.12.0 | **Time to first eco session: ~5 minutes**

eco mode replaces Claude's sequential file browsing with deterministic structural
retrieval and persistent cross-file reasoning. Same questions, better answers, fewer
tokens, and memory that survives between sessions.

---

## 1. Install and Enable

### Prerequisites

- Python 3.10+ with `pip`
- Claude Code installed and working
- A project directory you want to explore

### One-shot install

```bash
cd /path/to/your-project
pip install "memctl[mcp]"
bash "$(memctl scripts-path)/install_eco.sh" --db-root .memory
```

This single command:
- Registers the memctl MCP server with project-scoped storage (`.memory/memory.db`)
- Installs the eco hook (`.claude/hooks/eco-hint.sh`)
- Installs the strategy file (`.claude/eco/ECO.md`)
- Installs the `/eco` slash command (`.claude/commands/eco.md`)
- Validates server startup
- Adds `.memory/` to `.gitignore`
- Reports extraction capabilities (docx, pdf, xlsx, etc.)

### Enable eco mode

eco is installed but **OFF by default**. Enable it:

```bash
memctl eco on
```

Or from within Claude Code:

```
/eco on
```

### Verify

```
/eco status
```

You should see: `eco mode enabled` followed by memory stats. If you see
`command not found`, the install script did not complete — re-run it.

### What got installed

```
your-project/
├── .memory/
│   └── memory.db              ← SQLite + FTS5 + WAL (your knowledge)
├── .claude/
│   ├── hooks/
│   │   └── eco-hint.sh        ← Injects ~50 tokens per turn when eco is ON
│   ├── eco/
│   │   └── ECO.md             ← Strategy file Claude reads on demand
│   └── commands/
│       └── eco.md             ← /eco slash command behavior
└── .gitignore                 ← Updated: .memory/ excluded
```

---

## 2. Your First eco Session

Start Claude Code in your project:

```bash
claude
```

With eco mode enabled, Claude now has 14 `memory_*` MCP tools. Here is what a
typical first interaction looks like.

### Step 1: Explore the project structure

Ask Claude:

```
What is the structure of this project?
```

**Without eco:** Claude calls `Read` on 5-10 files sequentially, reads 200 lines of
each, gets imports and boilerplate, misses the architecture.

**With eco:** Claude calls `memory_inspect("src/")` — one call, ~600 tokens — and
gets a complete structural digest:

```
src/ — 35 files, 71 chunks, 357 KB
  Dominant: .py (26 files), .md (9 files)
  Largest: cli.py (57 KB), store.py (54 KB)
  Observation: src/ dominates content (78% of chunks)
```

### Step 2: Ask a focused question

```
How does authentication work in this project?
```

**Without eco:** Claude reads `auth.py` from line 1, gets the module docstring and
imports, maybe reaches the first function. Asks to read more files. Multiple round trips.

**With eco:** Claude calls `memory_recall("authentication")` — retrieves the exact
chunks where authentication is implemented, across multiple files, scoped by token
budget. One call, precise answer.

### Step 3: Store a finding

Claude discovers something important during exploration. With eco, it calls
`memory_propose` to store the finding:

```
"The project uses JWT for stateless auth with 15-min access tokens.
 Refresh tokens stored in HttpOnly cookies."
```

This observation persists across sessions. Next time you (or a teammate) ask
about authentication, Claude recalls it instantly — no re-exploration.

### Step 4: Start a new session

Close Claude Code and reopen it. Ask the same question:

```
How does authentication work?
```

**Without eco:** Claude starts from scratch. Reads files again. Same cost, same latency.

**With eco:** `memory_recall("authentication")` returns the stored finding immediately.
Claude remembers what it learned. No re-exploration.

---

## 3. How eco Changes Claude's Behavior

eco mode injects a ~50-token hint every turn via the `UserPromptSubmit` hook. This
tells Claude that 14 `memory_*` MCP tools are available and preferred for exploration.

Claude still has full access to native `Read`, `Edit`, `Write`, and all other tools.
eco is **advisory for retrieval, not restrictive for editing**.

### What Claude does differently

| Task | Without eco | With eco |
|------|-------------|----------|
| Explore a directory | `Read` 5-10 files sequentially | `memory_inspect` — one call |
| Find a function | `Glob` + `Read` + `Grep` | `memory_recall("FunctionName")` |
| Understand architecture | Read README + 3-4 source files | `memory_recall("architecture")` |
| Edit a file | `Read` then `Edit` | `memory_recall` to LOCATE, then `Read` + `Edit` |
| Read a .docx/.pdf | Cannot (binary) | `memory_recall` — text extracted at sync time |
| Remember across sessions | Cannot | `memory_propose` → `memory_recall` |

### Intent classification

eco mode automatically classifies your intent from the first verb:

- **Exploration** (how, where, what, explain, find, search...): Answer using memory.
  Do not read files unless context is insufficient.
- **Modification** (add, fix, refactor, create, delete, update...): Use memory to
  LOCATE the right file, then use native `Read` + `Edit` to modify it.

This is deterministic (verb matching, no ML). See `classify_mode()` in `memctl/query.py`.

---

## 4. The Escalation Ladder

eco mode follows a strict retrieval hierarchy. Claude tries each level before falling
back to the next:

```
Level 1: memory_inspect     ← Structural overview (file tree, sizes, observations)
    ↓ insufficient?
Level 2: memory_recall      ← Selective content retrieval (FTS5, token-budgeted)
    ↓ insufficient?
Level 3: memory_loop        ← Iterative refinement (bounded, convergence-detecting)
    ↓ insufficient?
Level 4: Native Read/View   ← Last resort (editing, diffing, line-level precision)
```

**Key rule:** Do not skip levels. Do not jump to native `Read` after a single failed
recall. Narrow the query, scope to a subfolder, try different keywords — then fall back.

### When native Read is expected

Native file access is correct and normal for:

- **Editing a specific file** — need exact line content for `str_replace`
- **Diffing two files** — need raw content side-by-side
- **A file you already know the path to** — no need to search
- **After the escalation ladder is exhausted** — all eco levels tried

eco covers **exploration and comprehension**. Native Read covers **mutation and
line-level precision**. They are complementary.

---

## 5. Query Tips

memctl uses SQLite FTS5 full-text search with **AND logic**: every term must match
within a single item. This is powerful but requires keyword discipline.

### What works

**Identifiers (always best):**

```
IncidentServiceImpl          → finds the service
PreAuthorize                 → finds secured controllers
JmsListener                  → finds messaging consumers
SECRET_PATTERNS              → finds the pattern list
```

**Domain term pairs:**

```
incident workflow            → finds workflow specs + code
security authentication      → finds auth-related classes
Redis caching                → finds caching strategy
```

**Natural language (since v0.10.0+):** Stop words are stripped automatically.
A cascade (AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK) auto-recovers when strict AND fails. For stemming: `memctl reindex --tokenizer en`.

```
"what is the incident escalation workflow"
  → normalized: "incident escalation workflow"  → AND match

"what REST conventions do the endpoints follow"
  → AND miss → REDUCED_AND("REST conventions endpoints"): match
  → fts_strategy: REDUCED_AND
```

### What does not work

```
"how are the services monitored in production"
  → MISS: "monitored" ≠ "Monitoring" (no stemming)
  → Fix: use "Prometheus monitoring" instead

"how does the notification system work"
  → MISS: "notification" ≠ "notifications" (no plural)
  → Fix: use "NotificationServiceImpl" instead
```

### The rule

Think like a developer using `grep`, not like a user asking a chatbot.
Use the exact identifier from the codebase. eco rewards precision.

### Recovery when you get 0 results

1. Remove the broadest term — keep only identifiers
2. Try the class/function name directly
3. Scope to a subfolder
4. Split into two narrower queries
5. Escalate to `memory_loop`
6. Only then fall back to native `Read`

---

## 6. Session Workflow Patterns

### Pattern 1: Onboarding onto a new codebase

```
/eco on
> "What is the structure of this project?"         → memory_inspect
> "How does authentication work?"                   → memory_recall
> "What are the main API endpoints?"                → memory_recall
> "How are tests organized?"                        → memory_inspect("tests/")
```

Claude learns the architecture. Findings are stored automatically.
Next session: instant recall, no re-exploration.

### Pattern 2: Deep investigation

```
> "How does the consolidation algorithm work?"      → memory_recall
> "What are the edge cases in merge logic?"         → memory_loop (iterative)
> "Show me the tie-breaking rules"                  → memory_recall("tie-break")
```

The loop controller refines queries automatically, detects convergence,
and stops when the answer stabilizes.

### Pattern 3: Edit with eco guidance

```
> "Add logging to the sync function"                → modification intent detected
  Claude calls memory_recall("sync") to find the right file
  Then calls native Read to get current content
  Then calls native Edit to add logging
```

eco locates. Native tools modify. This is the designed workflow.

### Pattern 4: Binary document exploration

```
> "What does the project spec say about security?"  → memory_recall("security")
  If specs/*.docx or specs/*.pdf are synced, their content is searchable.
  Native Claude cannot read these formats at all.
```

### Pattern 5: End-of-session consolidation

```
> "Consolidate my findings"                         → memory_consolidate
  Clusters similar STM items by type + tag overlap
  Merges each cluster (longest content wins)
  Promotes to MTM tier
  No LLM calls — fully deterministic
```

This moves knowledge from short-term (STM) to medium-term (MTM).
High-usage MTM items eventually promote to long-term (LTM).

---

## 7. Binary Format Superpowers

Native Claude Code cannot read binary files. eco mode extracts text at sync time,
making these formats fully searchable:

| Format | Examples | Dependency |
|--------|----------|------------|
| Word | `.docx` | `pip install memctl[docs]` |
| OpenDocument Text | `.odt` | None (stdlib) |
| PowerPoint | `.pptx` | `pip install memctl[docs]` |
| OpenDocument Presentation | `.odp` | None (stdlib) |
| Excel | `.xlsx` | `pip install memctl[docs]` |
| OpenDocument Spreadsheet | `.ods` | None (stdlib) |
| PDF | `.pdf` | `pdftotext` (poppler-utils) |

### Example

```bash
# Sync a folder containing specs and reports
memctl mount docs/ && memctl sync docs/
```

Now in Claude Code:

```
> "What does the architecture document say about scalability?"
  → memory_recall("scalability")
  → Returns chunks from docs/architecture.docx, even though Claude cannot natively read .docx
```

This is one of eco mode's strongest advantages over vanilla Claude Code.

---

## 8. Toggle and Customize

### Live toggle

```
/eco on       Enable eco mode
/eco off      Disable eco mode (vanilla Claude Code)
/eco status   Check state + show memory stats
```

The toggle is instant — it creates or removes a flag file (`.claude/eco/.disabled`).
No subprocess, no restart needed.

### Customization

**Token budget:** Control how much context eco injects per recall.

```bash
# In your shell / .env
export MEMCTL_BUDGET=1500    # Default: 2200
```

**FTS tokenizer:** Choose the right preset for your codebase language.

```bash
# French-safe (default) — normalizes accents
memctl init --fts-tokenizer fr

# English with Porter stemming — "monitored" matches "monitoring"
memctl init --fts-tokenizer en

# Raw — no normalization
memctl init --fts-tokenizer raw
```

**Ignore patterns:** Exclude files from sync.

```bash
memctl mount src/ --ignore "*.min.js" --ignore "vendor/*"
```

---

## 9. Coexistence with CloakMCP

eco mode and [CloakMCP](https://github.com/ovitrac/CloakMCP) serve orthogonal
purposes and can coexist in the same project:

| Aspect | CloakMCP | eco (memctl) |
|--------|----------|--------------|
| Purpose | Secret sanitization | Token-efficient retrieval |
| Hook event | SessionStart / SessionEnd | UserPromptSubmit |
| What it intercepts | Secrets in files | File exploration requests |
| Persistence | Encrypted vault | SQLite + FTS5 + WAL |
| Conflicts | None | None |

Both use Claude Code hooks but on **different events**. They do not interfere.

**Recommended setup for sensitive codebases:**

```bash
# 1. Install CloakMCP (secrets protection)
cd /path/to/project
pip install -e /path/to/CloakMCP
scripts/install_claude.sh

# 2. Install eco mode (token-efficient exploration)
pip install "memctl[mcp]"
bash "$(memctl scripts-path)/install_eco.sh" --db-root .memory
memctl eco on
```

Secrets are sanitized before Claude sees them. eco mode provides efficient
retrieval over the sanitized codebase. Complementary, not competing.

---

## 10. FAQ

### Does eco mode slow down Claude?

No. The hook adds ~50 tokens per turn (a single sentence). MCP tool calls are
local SQLite queries — sub-millisecond. The net effect is usually faster because
Claude makes fewer file reads.

### Can I use eco mode on a monorepo?

Yes. Use scoped mounts to partition the repository:

```bash
memctl mount services/auth/ --name auth
memctl mount services/payments/ --name payments
memctl sync
```

Then in Claude Code, scope recalls to a specific mount:

```
> "How does the auth service validate tokens?" → scoped to auth mount
```

### Does eco mode work offline?

Yes. Everything is local: SQLite database, FTS5 search, no network calls.
memctl itself never contacts any external service.

### What happens if I delete `.memory/memory.db`?

You lose all stored knowledge (findings, synced content, memory tiers). The next
`memory_inspect` or `memory_recall` will trigger auto-mount and auto-sync, rebuilding
the index from your source files. Stored observations (proposals) are lost permanently.

### Can teammates share a memory database?

The `.memory/` directory is `.gitignore`'d by default. Each developer builds their
own local knowledge. This is intentional: different developers explore different
parts of the codebase, and their memory reflects their individual workflow.

For shared team knowledge, use `memctl export` / `memctl import` to exchange
curated findings.

### Is the English stemming tokenizer better?

The `en` preset uses Porter stemming, which means "monitored" matches "monitoring"
and "notification" matches "notifications". This reduces 0-result frustrations but
is less precise for code identifiers. Choose based on your codebase:

| Codebase | Recommended | Why |
|----------|-------------|-----|
| Multilingual / French | `fr` (default) | Accent normalization without English-specific stemming |
| English-only | `en` | Better NL query recall via stemming |
| Code-heavy, minimal prose | `raw` | Maximum precision for identifiers |

### How much disk space does it use?

For a 500-file project (~5 MB of source), expect ~2-5 MB for `memory.db`.
SQLite with FTS5 is very space-efficient.

---

## 11. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `/eco` command not found | Slash command not installed | Re-run `bash "$(memctl scripts-path)/install_eco.sh"` |
| eco ON but Claude ignores it | Hook not firing | Check `.claude/hooks/eco-hint.sh` exists and is executable |
| 0 results from recall | FTS5 AND logic, too many terms | Use 2-3 identifiers, not sentences. See [Query Tips](#5-query-tips) |
| Stale results after code change | Database not re-synced | `memctl sync` or `memory_sync()` via MCP |
| `.docx` / `.pdf` not searchable | Docs extra or poppler missing | `pip install "memctl[docs]"` and/or `sudo apt install poppler-utils` |
| `memory_inspect` returns empty | Folder not mounted or empty | `memctl mount PATH && memctl sync PATH` |
| eco OFF after restart | `.claude/eco/.disabled` exists | `/eco on` or `rm .claude/eco/.disabled` |
| Hook error in Claude Code | Syntax error in hook script | Check `bash -n .claude/hooks/eco-hint.sh` for parse errors |
| MCP server not starting | Wrong db path or mcp not installed | `memctl serve --check --db .memory/memory.db` |
| Knowledge lost between sessions | Database deleted or moved | Check `.memory/memory.db` exists; re-sync if needed |

---

## 12. Next Steps

| Want to... | Read |
|-----------|------|
| Understand the full eco strategy | [extras/eco/ECO.md](extras/eco/ECO.md) |
| Run the 4-act demo | `bash demos/eco_demo.sh` |
| Evaluate eco with your team | [extras/eco/PILOT.md](extras/eco/PILOT.md) |
| Learn the general memctl CLI | [QUICKSTART.md](QUICKSTART.md) |
| Set up defense-in-depth hooks | [README.md — Defense in Depth](README.md#defense-in-depth-v08) |
| See all 14 MCP tools | [README.md — MCP Tools](README.md#mcp-tools) |
| Explore the Python API | [README.md — Python API](README.md#python-api) |

---

*memctl v0.12.0 — Olivier Vitrac, Adservio Innovation Lab*

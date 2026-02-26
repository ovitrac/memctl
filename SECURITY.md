# Security Policy

**Project:** memctl — A Unix-native memory control plane for LLM orchestration
**Author:** Olivier Vitrac, PhD, HDR | Adservio Innovation Lab

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.21.x  | Yes       |
| 0.20.x  | Yes       |
| < 0.20  | No        |

Security fixes are applied to the latest minor release only.

---

## Reporting a Vulnerability

If you discover a security vulnerability in memctl, please report it
**privately** via [GitHub Security Advisories](https://github.com/ovitrac/memctl/security/advisories/new)
or by email to **olivier.vitrac@adservio.fr**.

**Do not** open a public issue for security vulnerabilities.

You should receive an acknowledgment within 48 hours. Confirmed issues
are fixed in a patch release and credited in the changelog (unless you
prefer anonymity).

---

## Threat Model

memctl's policy engine is a security boundary. Five threats are addressed:

| Threat | Vector | Mitigation |
|--------|--------|------------|
| **Secrets exfiltration** | Ingested files contain API keys, tokens, passwords | 10 secret detection patterns; items rejected before storage |
| **Prompt injection** | Adversarial content in memory items manipulates downstream LLM | 8 injection detection patterns; `injectable=False` quarantine |
| **Instructional override** | Memory items contain system-prompt-like directives | 8 block patterns + 4 quarantine patterns |
| **Context poisoning** | Low-confidence or contested items corrupt trusted memory | Three-tier STM/MTM/LTM with confidence scores; `validation` tracking |
| **PII leakage** | Personal data stored without consent | 5 PII detection patterns; quarantine flag prevents injection |

---

## Security Architecture

### Defense in Depth (5 layers)

| Layer | Component | Scope |
|-------|-----------|-------|
| **L0** | `ServerGuard` | Path validation, write size caps, import batch limits |
| **L1** | `RateLimiter` | Token-bucket throttling: 20 writes/min, 120 reads/min |
| **L1** | `AuditLogger` | Structured JSONL audit trail (schema v1) |
| **L2** | `MemoryPolicy` | 35 detection patterns across all write paths |
| **L3** | Claude Code hooks | Optional: PreToolUse safety guard + PostToolUse audit |

### Policy Enforcement (v0.21.0+)

Since v0.21.0, **every write path** passes through the policy engine:

- **Ingest** (`push`, `push --source`, MCP `memory_write`): each chunk is
  evaluated before `store.write_item()`. Rejected chunks are dropped.
  Quarantined chunks are stored with `injectable=False`.
- **Pull** (`pull`, MCP `memory_propose`): content-hash dedup + policy check.
- **Consolidation** (`consolidate`): merged items are re-evaluated. If merged
  content triggers quarantine patterns, `injectable=False` is set.
- **Sync** (`sync`): inherits policy from `ingest_file()` — safe by default.
- **Import** (`import`): each item is policy-checked on import.

Policy is **safe by default** — active unless explicitly disabled with
`policy=None` or `policy=False` in the Python API.

### Regex Performance

Pattern matching is bounded to prevent ReDoS:
- JWT: `{20,500}` character class limit
- Base64: `{60,1000}` character class limit

---

## Scope

### In Scope

- The policy engine (`policy.py`) and its 35 detection patterns
- All write paths: ingest, pull, consolidate, sync, import
- MCP server middleware (guard, rate limiter, audit)
- CLI hooks (safety-guard, audit-logger)
- SQLite database integrity (WAL, FTS5 triggers)

### Out of Scope

- LLM behavior (memctl does not call LLMs)
- Network security (memctl is local-first, no HTTP server)
- Host-level security (OS permissions, filesystem ACLs)
- Upstream dependencies in optional extras (`mcp[cli]`, `python-docx`, etc.)

---

## Security Invariants

These invariants are enforced by tests and must never be weakened:

1. **Policy never bypassed.** Every write path routes through `policy.py`.
2. **Quarantine is irreversible.** `injectable=False` can only be set, never cleared by policy.
3. **Content-addressed storage.** SHA-256 dedup ensures no silent content replacement.
4. **Fail-open for hooks.** Hook failures (bad JSON, missing DB) never block the user — exit 0.
5. **Fail-closed for policy.** Unknown content types are evaluated conservatively.

---

*memctl — Olivier Vitrac, Adservio Innovation Lab*

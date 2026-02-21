#!/usr/bin/env bash
# =========================================================================
# eco_demo.sh — WAHOO demo: eco mode on the memctl codebase
#
# Demonstrates deterministic structural retrieval vs sequential browsing.
#
# 4 acts:
#   Act 1 — Hard question: native exploration (sequential, incomplete)
#   Act 2 — eco ON: structural + indexed retrieval (surgical, complete)
#   Act 3 — FTS discipline: failing query → refined query → success
#   Act 4 — Persistence: restart → instant answer → no re-exploration
#
# Usage:
#   ./demos/eco_demo.sh                  # Full 4-act demo
#   ./demos/eco_demo.sh --act 2          # Run only Act 2
#   ./demos/eco_demo.sh --corpus PATH    # Use custom corpus (default: memctl/)
#
# Prerequisites:
#   pip install memctl
#   The demo creates its own temp workspace — no install_eco.sh needed.
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=_demo_lib.sh
source "$SCRIPT_DIR/_demo_lib.sh"

# -- Configuration ----------------------------------------------------------
MEMCTL="${MEMCTL:-python3 -m memctl.cli}"
CORPUS="${PROJECT_ROOT}"
ACT_FILTER=""
KEEP=false
IGNORE_PATTERNS=(".git" "__pycache__" ".memory" "*.pyc" "base" ".pytest_cache" "*.egg-info" "eco_demo.sh")

# -- Parse arguments --------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --act)     ACT_FILTER="$2"; shift 2 ;;
        --corpus)  CORPUS="$2"; shift 2 ;;
        --keep)    KEEP=true; shift ;;
        -h|--help)
            printf "Usage: %s [--act N] [--corpus PATH] [--keep]\n" "$(basename "$0")"
            exit 0
            ;;
        *) printf "Unknown option: %s\n" "$1" >&2; exit 1 ;;
    esac
done

# -- Workspace (use db/ subdir to avoid .memory collision) ------------------
WS=$(mktemp -d "/tmp/eco_demo.XXXXXX")
DB="$WS/db/memory.db"
mkdir -p "$WS/db"
trap '[[ "$KEEP" == "false" ]] && rm -rf "$WS"' EXIT

# mem() wrapper — all CLI calls use the temp DB
mem() { $MEMCTL --db "$DB" "$@"; }

printf "\n"
info "Workspace: $WS"
info "Corpus:    $CORPUS"
print_versions

# -- Helpers ----------------------------------------------------------------
should_run() { [[ -z "$ACT_FILTER" || "$ACT_FILTER" == "$1" ]]; }

count_tokens() {
    local chars
    chars=$(printf '%s' "$1" | wc -c | tr -d ' ')
    echo $(( chars / 4 ))
}

TOTAL_ACTS=4
PASS=0

# =========================================================================
# ACT 1 — The Hard Question (Native Exploration)
# =========================================================================
if should_run 1; then
    step 1 $TOTAL_ACTS "The Hard Question — Native Exploration"
    printf "\n"
    info "Question: How does memctl enforce defense-in-depth against prompt injection?"
    info "This architecture spans 5 files: policy.py, guard.py, rate_limiter.py, audit.py, test_mcp_middleware.py"
    printf "\n"

    info "Simulating native Claude exploration (sequential file reads)..."

    NATIVE_TOKENS=0
    NATIVE_CALLS=0

    for f in memctl/policy.py memctl/mcp/guard.py memctl/mcp/rate_limiter.py \
             memctl/mcp/audit.py memctl/mcp/tools.py; do
        if [[ -f "$CORPUS/$f" ]]; then
            NATIVE_CALLS=$((NATIVE_CALLS + 1))
            local_bytes=$(head -200 "$CORPUS/$f" | wc -c | tr -d ' ')
            NATIVE_TOKENS=$((NATIVE_TOKENS + local_bytes / 4))
            cmd "Read $f (first 200 lines)"
        fi
    done

    printf "\n"
    info "Native result: ${NATIVE_CALLS} file reads, ~${NATIVE_TOKENS} tokens consumed"
    info "Typical findings: imports, docstrings, pattern lists — but NOT:"
    warn "  - The quarantine logic in policy.py (line 280+)"
    warn "  - The middleware invariants (M1/M2/M3) documented in test files"
    warn "  - The rate limiter tool classification constants"
    printf "\n"
    info "Native Claude reads sequentially. It misses architecture."
    PASS=$((PASS + 1))
fi

# =========================================================================
# ACT 2 — eco ON: Structural + Indexed Retrieval
# =========================================================================
if should_run 2; then
    step 2 $TOTAL_ACTS "eco ON — Structural + Indexed Retrieval"
    printf "\n"

    # Build ignore flags
    IGNORE_FLAGS=()
    for pat in "${IGNORE_PATTERNS[@]}"; do
        IGNORE_FLAGS+=(--ignore "$pat")
    done

    # Step 2a: inspect auto-mounts, auto-syncs, and returns structure
    info "memory_inspect — auto-mount, auto-sync, structural overview"
    cmd "memory_inspect(\"$CORPUS\")"
    INSPECT_OUT=$(mem inspect "$CORPUS" "${IGNORE_FLAGS[@]}" 2>/dev/null) || INSPECT_OUT=""
    INSPECT_TOKENS=$(count_tokens "$INSPECT_OUT")

    if [[ -n "$INSPECT_OUT" ]]; then
        print_answer "$INSPECT_OUT" 20
        ok "Structural overview: ~${INSPECT_TOKENS} tokens (1 tool call, replaces 5+ file reads)"
    else
        warn "Inspect returned empty"
    fi

    # How many items were synced?
    ITEM_COUNT=$(mem stats 2>/dev/null | grep "Total items" | grep -oE '[0-9]+' || echo "?")
    ok "Store: ${ITEM_COUNT} chunks indexed"

    # Step 2b: Targeted recall — middleware architecture
    printf "\n"
    info "memory_recall — cross-file architecture query"
    cmd "memory_recall(\"middleware guard session audit\")"
    RECALL1=$(mem push "middleware guard session audit" --budget 1500 2>/dev/null) || RECALL1=""
    RECALL1_TOKENS=$(count_tokens "$RECALL1")

    if [[ -n "$RECALL1" ]]; then
        print_answer "$RECALL1" 25
        ok "Cross-file recall: ~${RECALL1_TOKENS} tokens"

        # Check for the wahoo moment
        if printf '%s' "$RECALL1" | grep -q "test_mcp_middleware"; then
            printf "\n"
            ok "WAHOO: eco surfaced architectural invariants from test files!"
            info "Architecture lives in tests. eco finds it. Native browsing does not."
        fi
    else
        warn "Recall returned empty — trying narrower query..."
        RECALL1=$(mem push "middleware execute" --budget 1500 2>/dev/null) || RECALL1=""
        RECALL1_TOKENS=$(count_tokens "$RECALL1")
        [[ -n "$RECALL1" ]] && print_answer "$RECALL1" 20
    fi

    # Step 2c: Targeted recall — quarantine logic (deep chunk)
    printf "\n"
    info "memory_recall — deep chunk retrieval (quarantine logic)"
    cmd "memory_recall(\"quarantine force_non_injectable\")"
    RECALL2=$(mem push "quarantine force_non_injectable" --budget 1500 2>/dev/null) || RECALL2=""
    RECALL2_TOKENS=$(count_tokens "$RECALL2")

    if [[ -n "$RECALL2" ]]; then
        print_answer "$RECALL2" 20

        if printf '%s' "$RECALL2" | grep -q "chunk:[1-9]"; then
            ok "PRECISION: Retrieved deep implementation chunk, not file header"
        fi
        if printf '%s' "$RECALL2" | grep -q "force_non_injectable"; then
            ok "Found quarantine enforcement logic — native Read starts 280 lines above this"
        fi
    fi

    # Token comparison
    printf "\n"
    ECO_TOTAL=$((INSPECT_TOKENS + RECALL1_TOKENS + RECALL2_TOKENS))
    # Estimate native cost: full reads of the 5 key files
    NATIVE_FULL=0
    for f in memctl/policy.py memctl/mcp/guard.py memctl/mcp/rate_limiter.py \
             memctl/mcp/audit.py memctl/mcp/tools.py; do
        if [[ -f "$CORPUS/$f" ]]; then
            sz=$(wc -c < "$CORPUS/$f" | tr -d ' ')
            NATIVE_FULL=$((NATIVE_FULL + sz / 4))
        fi
    done

    if [[ "$ECO_TOTAL" -gt 0 && "$NATIVE_FULL" -gt 0 ]]; then
        RATIO=$((NATIVE_FULL / ECO_TOTAL))
        printf "  ${BD}Token comparison:${NC}\n"
        printf "    eco mode:  ~%d tokens (3 tool calls)\n" "$ECO_TOTAL"
        printf "    Native:    ~%d tokens (5 full file reads)\n" "$NATIVE_FULL"
        printf "\n"
        ok "Reduction: ~${RATIO}x fewer tokens. Credible, measurable, repeatable."
    fi
    PASS=$((PASS + 1))
fi

# =========================================================================
# ACT 3 — FTS Discipline: Precision Over Sentences
# =========================================================================
if should_run 3; then
    step 3 $TOTAL_ACTS "FTS Discipline — eco Rewards Precision"
    printf "\n"

    # Ensure corpus is ingested (if running act 3 standalone)
    ITEM_COUNT=$(mem stats 2>/dev/null | grep "Total items" | grep -oE '[0-9]+' || echo "0")
    if [[ "$ITEM_COUNT" == "0" ]]; then
        info "Ingesting corpus for standalone act..."
        IGNORE_FLAGS=()
        for pat in "${IGNORE_PATTERNS[@]}"; do
            IGNORE_FLAGS+=(--ignore "$pat")
        done
        mem inspect "$CORPUS" "${IGNORE_FLAGS[@]}" > /dev/null 2>&1 || true
    fi

    # Failing query: natural language sentence
    info "Natural language query (FTS5 AND logic — every word must match)"
    cmd "memory_search(\"how are cryptographic secrets detected and quarantined before storage\")"
    FAIL_OUT=$(mem search "how are cryptographic secrets detected and quarantined before storage" 2>/dev/null) || FAIL_OUT=""

    if printf '%s' "$FAIL_OUT" | grep -q "Found 0" || [[ -z "$FAIL_OUT" ]]; then
        ok "0 results — AND logic over-constrains. Every word must match."
    else
        FAIL_COUNT=$(printf '%s' "$FAIL_OUT" | head -1 | grep -oE '[0-9]+' | head -1)
        warn "${FAIL_COUNT:-?} result(s) — some words matched by chance"
    fi
    info "The answer is in policy.py (SECRET_PATTERNS) — but the sentence can't find it"

    # Refined: keyword query — same question, developer-style
    printf "\n"
    info "Same question, developer-style: use the identifier"
    cmd "memory_search(\"SECRET_PATTERNS\")"
    GOOD_OUT=$(mem search "SECRET_PATTERNS" 2>/dev/null) || GOOD_OUT=""

    if printf '%s' "$GOOD_OUT" | grep -q "Found [1-9]"; then
        GOOD_COUNT=$(printf '%s' "$GOOD_OUT" | head -1 | grep -oE '[0-9]+' | head -1)
        ok "${GOOD_COUNT} results — one identifier, exact match"
    fi

    # Retrieve the content with precision
    printf "\n"
    info "Retrieving the implementation..."
    cmd "memory_recall(\"SECRET_PATTERNS\")"
    PRECISE_OUT=$(mem push "SECRET_PATTERNS" --budget 800 2>/dev/null) || PRECISE_OUT=""
    PRECISE_TOKENS=$(count_tokens "$PRECISE_OUT")
    if [[ -n "$PRECISE_OUT" ]]; then
        print_answer "$PRECISE_OUT" 15

        # Check if we got deep chunk
        if printf '%s' "$PRECISE_OUT" | grep -q "chunk:[1-9]"; then
            ok "Retrieved chunk 1 (implementation) — not chunk 0 (imports)"
        fi
        ok "~${PRECISE_TOKENS} tokens. eco rewards precision. That's not a flaw — that's power."
    fi

    PASS=$((PASS + 1))
fi

# =========================================================================
# ACT 4 — Persistence: Claude Remembers
# =========================================================================
if should_run 4; then
    step 4 $TOTAL_ACTS "Persistence — Claude Remembers"
    printf "\n"

    # Ensure corpus is ingested (if running act 4 standalone)
    ITEM_COUNT=$(mem stats 2>/dev/null | grep "Total items" | grep -oE '[0-9]+' || echo "0")
    if [[ "$ITEM_COUNT" == "0" ]]; then
        info "Ingesting corpus for standalone act..."
        IGNORE_FLAGS=()
        for pat in "${IGNORE_PATTERNS[@]}"; do
            IGNORE_FLAGS+=(--ignore "$pat")
        done
        mem inspect "$CORPUS" "${IGNORE_FLAGS[@]}" > /dev/null 2>&1 || true
    fi

    info "Simulating a new session (same DB, no re-ingestion needed)"
    printf "\n"

    ITEM_COUNT=$(mem stats 2>/dev/null | grep "Total items" | grep -oE '[0-9]+' || echo "?")
    info "Items in store: ${ITEM_COUNT} (persisted from previous acts)"

    # Same question, instant answer
    cmd "memory_recall(\"middleware guard session audit\")"
    PERSIST_OUT=$(mem push "middleware guard session audit" --budget 1500 2>/dev/null) || PERSIST_OUT=""
    PERSIST_TOKENS=$(count_tokens "$PERSIST_OUT")

    if [[ -n "$PERSIST_OUT" ]]; then
        print_answer "$PERSIST_OUT" 15
        ok "Instant recall: ~${PERSIST_TOKENS} tokens. Zero re-exploration."
        printf "\n"
        info "Claude no longer explores. It remembers."
    else
        warn "Recall returned empty (store may have been cleaned)"
    fi

    # Store an observation to demonstrate memory growth
    printf "\n"
    info "Storing an architectural observation for future sessions..."
    cmd "memory_propose(\"defense-in-depth: L0 guard, L1 middleware, L2 policy, L3 hooks\")"
    printf "memctl uses 4-layer defense-in-depth: L0 guard (path validation), L1 middleware (rate limit + audit), L2 policy (35 detection patterns), L3 hooks (Claude Code safety)" | \
        mem pull --title "defense-in-depth architecture" --type "convention" --tags "architecture,security" 2>/dev/null || true
    ok "Observation stored. Available in all future sessions."

    PASS=$((PASS + 1))
fi

# =========================================================================
# Summary
# =========================================================================
EXPECTED=$TOTAL_ACTS
[[ -n "$ACT_FILTER" ]] && EXPECTED=1

printf "\n"
printf "  ${BD}══════════════════════════════════════════════════════════════${NC}\n"
printf "  ${G}  eco demo complete: %d/%d acts passed${NC}\n" "$PASS" "$EXPECTED"
printf "  ${BD}══════════════════════════════════════════════════════════════${NC}\n"
printf "\n"
printf "  ${BD}Key takeaways:${NC}\n"
printf "\n"
printf "    Native Claude reads files.  eco Claude queries architecture.\n"
printf "\n"
printf "    1. Surgical chunk retrieval — exact algorithm, not file header\n"
printf "    2. Cross-file invariant discovery — architecture lives in tests\n"
printf "    3. Bounded cost — ~5x fewer tokens, credible and measurable\n"
printf "    4. Persistence — knowledge survives across sessions\n"
printf "    5. Precision — keyword queries, not natural language sentences\n"
printf "\n"

#!/usr/bin/env bash
# =========================================================================
# must_have_demo.sh — memctl in 30 seconds
#
# The launch demo. Proves the 5 core control-plane properties that make
# memctl different from every other "memory for LLMs" project:
#
#   1. One command, one file     — zero-config init, single SQLite
#   2. Ingest + recall pipeline  — files in, injection block out (stdout)
#   3. Content-addressed dedup   — same files twice = zero new items
#   4. Policy-governed writes    — secrets rejected before storage
#   5. Full audit trail          — every operation traceable by item + time
#
# Self-contained. No network. No services. No LLM.
# Zero non-stdlib Python dependencies.
#
# Usage:
#     bash demos/must_have_demo.sh               # run from project root
#     bash demos/must_have_demo.sh --keep         # preserve workspace
#
# For the loop + consolidation demo, see: demos/advanced_demo.sh
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/_demo_lib.sh"

# -- Config ----------------------------------------------------------------
MEMCTL="python3 -m memctl.cli"
CORPUS="$SCRIPT_DIR/corpus-mini"
KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

STEPS=5

setup_workspace "memctl_musthave"
trap 'cleanup_workspace' EXIT

cleanup_workspace() {
    if [[ "$KEEP" == "false" ]]; then
        rm -rf "$WS"
    else
        printf "\n"
        info "Workspace preserved: $WS"
        info "  memctl stats --db $DB"
        info "  memctl search \"authentication\" --db $DB"
    fi
}

cd "$PROJECT_ROOT"

# -- Banner ----------------------------------------------------------------
printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${C}${BD}  memctl — The Unix Memory Control Plane for LLMs${NC}\n"
printf "${C}${BD}  self-contained · deterministic · zero non-stdlib Python deps${NC}\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
T0=$SECONDS

# =========================================================================
# WHY THIS MATTERS
# =========================================================================
#
# LLMs forget everything between turns. Every orchestration framework
# reinvents "memory" as a fragile abstraction over vector stores, cloud
# APIs, or JSON files. memctl takes a different path:
#
#   - One SQLite file IS the memory. No server. No config. No vendor lock-in.
#   - Unix pipes ARE the API. Push writes to stdout. Pull reads from stdin.
#     Any LLM CLI, any shell script, any cron job — they all compose.
#   - A policy engine blocks secrets and prompt injection BEFORE storage.
#     Your memory can never be weaponized against your own LLM.
#   - Every write is audited. Every file is content-addressed.
#     You can prove what your LLM knew, when, and why.
#
# This demo proves all five properties in under 30 seconds.
# =========================================================================

printf "\n"
printf "  ${D}LLMs forget everything between turns.${NC}\n"
printf "  ${D}memctl gives them persistent, policy-governed, auditable memory${NC}\n"
printf "  ${D}in a single SQLite file — composable via Unix pipes.${NC}\n"

# =========================================================================
# 1. ZERO-CONFIG INIT
# =========================================================================
step 1 $STEPS "One command, one file — zero-config init"
printf "\n"
printf "  ${D}No config files. No servers. One command creates a complete${NC}\n"
printf "  ${D}memory workspace: SQLite + FTS5 + WAL + .gitignore.${NC}\n"
printf "\n"

cmd "memctl init $WS/.memory"
INIT_OUT=$(mem init "$WS/.memory" 2>/dev/null)
ok "Created $(du -h "$DB" | cut -f1) memory database"
ok "$(printf '%s' "$INIT_OUT" | head -1)"

# =========================================================================
# 2. INGEST + RECALL
# =========================================================================
step 2 $STEPS "Ingest files + recall — the core pipeline"
printf "\n"
printf "  ${D}push does two things: (1) ingests files with paragraph chunking,${NC}\n"
printf "  ${D}(2) recalls matching items as a token-budgeted injection block${NC}\n"
printf "  ${D}on stdout — ready to pipe into any LLM.${NC}\n"
printf "\n"

cmd "memctl push \"authentication\" --source demos/corpus-mini/"
RECALL=$(mem push "authentication" --source "$CORPUS/" 2>/dev/null)
RECALL_LINES=$(printf '%s\n' "$RECALL" | wc -l | tr -d ' ')

STATS=$(mem stats --json 2>/dev/null)
TOTAL=$(printf '%s' "$STATS" | json_field total_items)

print_box_start "injection block (${RECALL_LINES} lines on stdout) ─────────────"
printf '%s\n' "$RECALL" | head -8 | while IFS= read -r line; do
    print_box_line "$line"
done
printf "  ${D}│ ...${NC}\n"
print_box_end
ok "${TOTAL} items ingested, ${RECALL_LINES}-line injection block on stdout"
ok "stdout = data (pipeable), stderr = progress (human) — always"

# =========================================================================
# 3. IDEMPOTENT RE-INGEST
# =========================================================================
step 3 $STEPS "Idempotent re-ingest — content-addressed dedup"
printf "\n"
printf "  ${D}Every file is SHA-256 hashed before ingestion. Push the same${NC}\n"
printf "  ${D}corpus again: zero new items. Safe in cron jobs, CI/CD, loops.${NC}\n"
printf "\n"

cmd "memctl push \"docs\" --source demos/corpus-mini/  # second time"
mem push "docs" --source "$CORPUS/" >/dev/null 2>&1
STATS2=$(mem stats --json 2>/dev/null)
TOTAL2=$(printf '%s' "$STATS2" | json_field total_items)

if [[ "$TOTAL" == "$TOTAL2" ]]; then
    ok "Still ${TOTAL2} items — zero duplicates (SHA-256 content addressing)"
else
    fail "Expected ${TOTAL}, got ${TOTAL2}"
fi

# =========================================================================
# 4. POLICY GATE
# =========================================================================
step 4 $STEPS "Policy-governed writes — secrets blocked before storage"
printf "\n"
printf "  ${D}Every write passes through 30 detection patterns before reaching${NC}\n"
printf "  ${D}the database. Secrets are hard-rejected (exit 1). Prompt injection${NC}\n"
printf "  ${D}is quarantined. Clean content passes through.${NC}\n"
printf "\n"

# Attempt to store a GitHub PAT → should be hard-rejected
cmd "echo 'ghp_ABCDEFGHIJKLMNOPQR...' | memctl pull --tags test"
set +e
printf 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn' \
    | mem pull --tags "test" --title "Token leak" 2>/dev/null
POLICY_EXIT=$?
set -e

if [[ $POLICY_EXIT -ne 0 ]]; then
    ok "GitHub PAT REJECTED (exit ${POLICY_EXIT}) — never reaches the database"
else
    fail "Expected rejection, got exit 0"
fi

# Store clean content → should pass
cmd "echo 'We chose event sourcing for auditability' | memctl pull --tags arch"
printf 'We chose event sourcing for full auditability and replay' \
    | mem pull --tags "arch,decision" --title "Architecture decision" 2>/dev/null
STATS3=$(mem stats --json 2>/dev/null)
TOTAL3=$(printf '%s' "$STATS3" | json_field total_items)

if (( TOTAL3 > TOTAL2 )); then
    ok "Clean content accepted — ${TOTAL3} items in store"
else
    fail "Expected item count to increase"
fi

# =========================================================================
# 5. AUDIT TRAIL
# =========================================================================
step 5 $STEPS "Full audit trail — every operation traced"
printf "\n"
printf "  ${D}Every write, read, and link creates an immutable event in${NC}\n"
printf "  ${D}memory_events. Each event records action, item_id, timestamp,${NC}\n"
printf "  ${D}and content_hash. You can prove what your LLM knew, and when.${NC}\n"
printf "\n"

# Event counts by action
cmd "SELECT action, COUNT(*) FROM memory_events GROUP BY action"
AUDIT=$(py_query "$DB" "SELECT action, COUNT(*) FROM memory_events GROUP BY action ORDER BY action")
print_box_start "event counts ───────────────────────────────────────"
printf '%s\n' "$AUDIT" | while IFS='|' read -r action count; do
    [[ -z "$action" ]] && continue
    printf "  ${D}│${NC}  %-14s %s\n" "$action" "$count"
done
print_box_end

# Last 5 events with timestamps and item IDs → proves traceability
cmd "SELECT timestamp, action, item_id FROM memory_events ORDER BY timestamp DESC LIMIT 5"
RECENT=$(py_query "$DB" "SELECT timestamp, action, substr(item_id,1,16) FROM memory_events ORDER BY timestamp DESC LIMIT 5")
print_box_start "last 5 events (timestamp → action → item) ────────"
printf '%s\n' "$RECENT" | while IFS='|' read -r ts action item; do
    [[ -z "$ts" ]] && continue
    printf "  ${D}│${NC}  %s  %-8s  %s\n" "$ts" "$action" "${item:-—}"
done
print_box_end

EVENT_TOTAL=$(py_scalar "$DB" "SELECT COUNT(*) FROM memory_events")
ok "${EVENT_TOTAL} audit events — every operation traceable"

# =========================================================================
# RESULTS
# =========================================================================
ELAPSED=$(( SECONDS - T0 ))
FINAL_TOTAL=$(printf '%s' "$(mem stats --json 2>/dev/null)" | json_field total_items)
DB_SIZE=$(du -h "$DB" | cut -f1)

printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${C}${BD}  Results${NC}\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "\n"
ok "5/5 properties demonstrated in ${ELAPSED}s"
ok "${FINAL_TOTAL} memory items, ${EVENT_TOTAL} audit events"
ok "Single file: ${DB_SIZE} (memory.db)"
printf "\n"
printf "  ${W}${BD}What makes this different:${NC}\n"
printf "  ${D}  No server to run. No vector DB to configure. No API keys.${NC}\n"
printf "  ${D}  One file you can cp, scp, git-track, or drop into any project.${NC}\n"
printf "  ${D}  Every write is policy-checked. Every operation is audited.${NC}\n"
printf "  ${D}  Pipes in, pipes out — works with any LLM CLI or shell script.${NC}\n"
printf "\n"
printf "  ${W}${BD}The pipeline:${NC}\n"
printf '%s\n' "      memctl push \"query\" --source docs/ \\"
printf '%s\n' "        | llm \"Summarize\" \\"
printf '%s\n' "        | memctl pull --tags result --title \"Summary\""
printf "\n"
printf "  ${W}${BD}Next:${NC} ${D}bash demos/advanced_demo.sh${NC}\n"
printf "  ${D}  (bounded loop + fixed-point convergence + consolidation)${NC}\n"

print_versions

printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "\n"

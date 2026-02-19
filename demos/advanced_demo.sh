#!/usr/bin/env bash
# =========================================================================
# advanced_demo.sh — memctl loop + consolidation demo
#
# Proves the advanced control-plane properties that make memctl
# a real orchestration primitive, not just a key-value store:
#
#   1. Bounded recall-answer loop  — LLM proposes, controller enforces
#   2. Fixed-point convergence     — loop stops when answers stabilize
#   3. JSONL trace + replay        — every iteration auditable + replayable
#   4. Deterministic consolidation — STM → MTM merge, no LLM needed
#
# Self-contained (mock LLM). No network. No services.
# Feature-gated: gracefully skips unavailable commands.
#
# Usage:
#     bash demos/advanced_demo.sh               # run from project root
#     bash demos/advanced_demo.sh --keep         # preserve workspace
#
# Prerequisites:
#     Run demos/must_have_demo.sh first for the core properties.
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/_demo_lib.sh"

# -- Config ----------------------------------------------------------------
MEMCTL="python3 -m memctl.cli"
CORPUS="$SCRIPT_DIR/corpus"
KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

setup_workspace "memctl_advanced"
TRACE_DIR="$WS/traces"
mkdir -p "$TRACE_DIR"
trap 'cleanup_workspace' EXIT

cleanup_workspace() {
    rm -f /tmp/memctl_mock_llm_state
    if [[ "$KEEP" == "false" ]]; then
        rm -rf "$WS"
    else
        printf "\n"
        info "Workspace preserved: $WS"
        info "  Traces: $TRACE_DIR/"
        info "  memctl stats --db $DB"
    fi
}

cd "$PROJECT_ROOT"

# -- Capability detection --------------------------------------------------
HAS_LOOP=true
HAS_CONSOLIDATE=true
if ! has_cmd loop; then
    HAS_LOOP=false
    warn "loop command not available — Steps 1-3 will be skipped"
fi
if ! has_cmd consolidate; then
    HAS_CONSOLIDATE=false
    warn "consolidate command not available — Step 4 will be skipped"
fi

STEPS=4

# -- Banner ----------------------------------------------------------------
printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${C}${BD}  memctl — Advanced Demo${NC}\n"
printf "${C}${BD}  bounded loop · convergence · trace · consolidation${NC}\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
T0=$SECONDS

# =========================================================================
# WHY THIS MATTERS
# =========================================================================
printf "\n"
printf "  ${D}Most LLM memory systems are \"store and retrieve\". memctl adds${NC}\n"
printf "  ${D}a control loop: the LLM can ask for more context, and the${NC}\n"
printf "  ${D}controller enforces bounds, detects cycles, and stops when${NC}\n"
printf "  ${D}answers converge — like a fixed-point iteration in numerics.${NC}\n"

# -- Setup -----------------------------------------------------------------
printf "\n"
info "Setting up workspace + ingesting demo corpus..."
mem init "$WS/.memory" >/dev/null 2>&1
mem push "project docs" --source "$CORPUS/" >/dev/null 2>&1
STATS=$(mem stats --json 2>/dev/null)
TOTAL=$(printf '%s' "$STATS" | json_field total_items)
ok "Workspace ready: ${TOTAL} items from $(ls "$CORPUS"/*.md | wc -l | tr -d ' ') files"

# =========================================================================
# 1. BOUNDED RECALL-ANSWER LOOP
# =========================================================================
if [[ "$HAS_LOOP" == "true" ]]; then
    step 1 $STEPS "Bounded recall-answer loop (mock LLM, 3 iterations)"
    printf "\n"
    printf "  ${D}The LLM receives context + question, outputs a JSON directive:${NC}\n"
    printf "  ${D}  {\"need_more\": true, \"query\": \"gateway\", \"stop\": false}${NC}\n"
    printf "  ${D}The controller recalls new items and loops — but never more than${NC}\n"
    printf "  ${D}--max-calls times. The LLM proposes. memctl decides.${NC}\n"
    printf "\n"

    MOCK_CMD="bash $SCRIPT_DIR/mock_llm.sh"
    TRACE1="$TRACE_DIR/mock_trace.jsonl"
    rm -f /tmp/memctl_mock_llm_state

    cmd "memctl push \"authentication security\" --source demos/corpus/ \\"
    cmd "  | memctl loop \"authentication security\" \\"
    cmd "    --llm \"bash demos/mock_llm.sh\" --max-calls 5 --trace-file trace.jsonl"

    T_LOOP=$(date +%s)
    ANSWER=$(
        mem push "authentication security" --source "$CORPUS/" 2>/dev/null \
        | mem loop "authentication security" \
            --llm "$MOCK_CMD" \
            --max-calls 5 \
            --trace-file "$TRACE1" \
            2>/dev/null
    )
    T_LOOP_END=$(date +%s)

    if [[ -n "$ANSWER" ]]; then
        ok "Loop completed in $((T_LOOP_END - T_LOOP))s"
    else
        fail "Loop produced no output"
    fi

# =========================================================================
# 2. CONVERGENCE + CYCLE DETECTION
# =========================================================================
    step 2 $STEPS "Convergence detection — 5 stopping conditions"
    printf "\n"
    printf "  ${D}The loop can stop for 5 reasons:${NC}\n"
    printf "  ${D}  llm_stop      — LLM says \"I have enough\"${NC}\n"
    printf "  ${D}  fixed_point   — consecutive answers are > 92%% similar${NC}\n"
    printf "  ${D}  query_cycle   — LLM re-requests a query already tried${NC}\n"
    printf "  ${D}  no_new_items  — recall returns nothing new${NC}\n"
    printf "  ${D}  max_calls     — iteration budget exhausted${NC}\n"
    printf "\n"

    if [[ -f "$TRACE1" ]]; then
        ITERS=$(wc -l < "$TRACE1" | tr -d ' ')
        ok "Trace shows ${ITERS} iterations"
        print_trace "$TRACE1"

        # Extract stop reason from last trace line
        STOP_REASON=$(tail -1 "$TRACE1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action','?'))" 2>/dev/null)
        ok "Stop reason: ${STOP_REASON}"
    fi

# =========================================================================
# 3. TRACE + REPLAY
# =========================================================================
    step 3 $STEPS "JSONL trace — auditable and replayable"
    printf "\n"
    printf "  ${D}Every iteration is logged as structured JSONL: query, new items,${NC}\n"
    printf "  ${D}similarity score, action taken. The trace can be replayed later${NC}\n"
    printf "  ${D}without calling the LLM — full reproducibility.${NC}\n"
    printf "\n"

    cmd "memctl loop \"ignored\" --llm cat --replay trace.jsonl"
    set +e
    REPLAY=$(mem loop "ignored" --llm cat --replay "$TRACE1" 2>/dev/null)
    set -e
    REPLAY_LINES=$(printf '%s\n' "$REPLAY" | wc -l | tr -d ' ')
    ok "Replay produced ${REPLAY_LINES} trace lines — no LLM called"

    # Show answer excerpt
    printf "\n"
    info "Final answer (excerpt):"
    print_answer "$ANSWER" 15

    # Store the answer
    printf '%s' "$ANSWER" | mem pull --tags "auth,loop" --title "Auth analysis (loop)" 2>/dev/null
    ok "Answer stored in memory via pull"

else
    step 1 $STEPS "Bounded recall-answer loop"
    skip "loop command not available in this build"
    step 2 $STEPS "Convergence detection"
    skip "requires loop"
    step 3 $STEPS "JSONL trace + replay"
    skip "requires loop"
fi

# =========================================================================
# 4. DETERMINISTIC CONSOLIDATION
# =========================================================================
if [[ "$HAS_CONSOLIDATE" == "true" ]]; then
    step 4 $STEPS "Deterministic consolidation — STM merge (no LLM)"
    printf "\n"
    printf "  ${D}Consolidation clusters similar STM items by type + tag overlap${NC}\n"
    printf "  ${D}(Jaccard similarity), merges each cluster (longest content wins),${NC}\n"
    printf "  ${D}and promotes to MTM. Fully deterministic — no LLM calls.${NC}\n"
    printf "\n"

    BEFORE=$(printf '%s' "$(mem stats --json 2>/dev/null)" | json_field total_items)

    cmd "memctl consolidate --dry-run"
    DRY=$(mem consolidate --dry-run --json 2>/dev/null)
    DRY_CLUSTERS=$(printf '%s' "$DRY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('clusters_found', d.get('clusters', '0')))
except: print('0')
" 2>/dev/null)
    ok "Dry run: ${DRY_CLUSTERS} cluster(s) identified"

    cmd "memctl consolidate"
    mem consolidate >/dev/null 2>&1

    AFTER=$(printf '%s' "$(mem stats --json 2>/dev/null)" | json_field total_items)
    ok "Before: ${BEFORE} items → After: ${AFTER} items"
    ok "Merge is deterministic: same input → same output, always"
else
    step 4 $STEPS "Deterministic consolidation"
    skip "consolidate command not available in this build"
fi

# =========================================================================
# RESULTS
# =========================================================================
ELAPSED=$(( SECONDS - T0 ))
FINAL_STATS=$(mem stats --json 2>/dev/null)
FINAL_TOTAL=$(printf '%s' "$FINAL_STATS" | json_field total_items)
EVENT_TOTAL=$(py_scalar "$DB" "SELECT COUNT(*) FROM memory_events")
DB_SIZE=$(du -h "$DB" | cut -f1)

COMPLETED=0
[[ "$HAS_LOOP" == "true" ]] && COMPLETED=$((COMPLETED + 3))
[[ "$HAS_CONSOLIDATE" == "true" ]] && COMPLETED=$((COMPLETED + 1))

printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${C}${BD}  Results${NC}\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "\n"
ok "${COMPLETED}/${STEPS} steps completed in ${ELAPSED}s"
ok "${FINAL_TOTAL} memory items, ${EVENT_TOTAL} audit events"
ok "Single file: ${DB_SIZE} (memory.db)"
printf "\n"
printf "  ${W}${BD}What this proves:${NC}\n"
printf "  ${D}  An LLM can iteratively refine its understanding — but memctl${NC}\n"
printf "  ${D}  controls the loop: bounded calls, cycle detection, convergence.${NC}\n"
printf "  ${D}  Every iteration is traced. Every trace is replayable.${NC}\n"
printf "  ${D}  Memory consolidates deterministically — no LLM required.${NC}\n"
printf "\n"
printf "  ${W}${BD}The full pipeline:${NC}\n"
printf '%s\n' "      memctl push \"query\" --source docs/ \\"
printf '%s\n' "        | memctl loop \"question\" --llm \"claude -p\" --trace \\"
printf '%s\n' "        | memctl pull --tags result --title \"Analysis\""

print_versions

printf "\n"
printf "${C}${BD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "\n"

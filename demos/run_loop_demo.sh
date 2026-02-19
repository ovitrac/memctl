#!/usr/bin/env bash
# =========================================================================
# memctl loop Demo — Bounded Recall-Answer Loop
#
# Demonstrates the memctl loop command with three modes:
#   Act 1: Mock LLM  — deterministic, no dependencies, always works
#   Act 2: Ollama     — sovereign local LLM (granite3.1:2b via Ollama)
#   Act 3: Claude     — cloud LLM (Claude via claude CLI)
#
# Usage:
#     ./run_loop_demo.sh                  # Mock only (default)
#     ./run_loop_demo.sh --ollama         # Mock + Ollama (granite3.1:2b)
#     ./run_loop_demo.sh --claude         # Mock + Claude
#     ./run_loop_demo.sh --all            # Mock + Ollama + Claude
#     ./run_loop_demo.sh --keep           # Preserve workspace after demo
#     ./run_loop_demo.sh --help
#
# Prerequisites:
#     pip install -e .                    # memctl installed
#     ollama pull granite3.1:2b           # for --ollama
#     claude --version                    # for --claude
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -uo pipefail

# =========================================================================
# COLORS
# =========================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
WHITE='\033[1;37m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# =========================================================================
# HELPERS
# =========================================================================

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}${BOLD}  memctl loop — Bounded Recall-Answer Loop Demo${NC}"
    echo -e "${CYAN}${BOLD}  convergence · cycle detection · trace · composability${NC}"
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

act_header() {
    local num="$1" title="$2"
    echo ""
    echo -e "${MAGENTA}${BOLD}┌──────────────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${MAGENTA}${BOLD}│  Act ${num}: ${title}$(printf '%*s' $((58 - ${#title})) '')   │${NC}"
    echo -e "${MAGENTA}${BOLD}└──────────────────────────────────────────────────────────────────────┘${NC}"
    echo ""
}

section() { echo -e "\n${WHITE}${BOLD}--- $1 ---${NC}\n"; }
info()    { echo -e "  ${BLUE}[info]${NC}  $1"; }
ok()      { echo -e "  ${GREEN}[ok]${NC}    $1"; }
warn()    { echo -e "  ${YELLOW}[warn]${NC}  $1"; }
fail()    { echo -e "  ${RED}[FAIL]${NC}  $1"; }

cmd() { echo -e "  ${YELLOW}\$${NC} ${DIM}$1${NC}"; }

print_answer() {
    local text="$1" max_lines="${2:-40}"
    echo -e "  ${DIM}┌─ answer ─────────────────────────────────────────────┐${NC}"
    echo "$text" | head -n "$max_lines" | while IFS= read -r line; do
        echo -e "  ${DIM}│${NC} $line"
    done
    local total_lines
    total_lines=$(echo "$text" | wc -l)
    if (( total_lines > max_lines )); then
        echo -e "  ${DIM}│ ... ($((total_lines - max_lines)) more lines)${NC}"
    fi
    echo -e "  ${DIM}└──────────────────────────────────────────────────────┘${NC}"
}

print_trace() {
    local trace_file="$1"
    echo -e "  ${DIM}┌─ trace ──────────────────────────────────────────────┐${NC}"
    while IFS= read -r line; do
        # Pretty-print key fields from JSONL
        local iter query action sim
        iter=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('iter','?'))" 2>/dev/null)
        query=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('query') or '-')" 2>/dev/null)
        action=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('action','?'))" 2>/dev/null)
        sim=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('sim'); print(f'{s:.4f}' if s else '-')" 2>/dev/null)
        echo -e "  ${DIM}│${NC}  iter=${BOLD}${iter}${NC}  query=${CYAN}${query}${NC}  sim=${sim}  action=${GREEN}${action}${NC}"
    done < "$trace_file"
    echo -e "  ${DIM}└──────────────────────────────────────────────────────┘${NC}"
}

elapsed() {
    local label="$1" secs="$2"
    echo -e "\n  ${DIM}⏱  ${label}: ${secs}s${NC}"
}

# =========================================================================
# CONFIGURATION
# =========================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MEMCTL="python3 -m memctl.cli"

KEEP=false
RUN_OLLAMA=false
RUN_CLAUDE=false
OLLAMA_MODEL="granite3.1:2b"

while [[ $# -gt 0 ]]; do
    case $1 in
        --ollama)    RUN_OLLAMA=true; shift ;;
        --claude)    RUN_CLAUDE=true; shift ;;
        --all)       RUN_OLLAMA=true; RUN_CLAUDE=true; shift ;;
        --keep)      KEEP=true; shift ;;
        --model)     OLLAMA_MODEL="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--ollama] [--claude] [--all] [--keep] [--model MODEL]"
            echo ""
            echo "  --ollama    Run Act 2 with Ollama (${OLLAMA_MODEL})"
            echo "  --claude    Run Act 3 with Claude CLI"
            echo "  --all       Run all three acts"
            echo "  --keep      Preserve workspace after demo"
            echo "  --model M   Ollama model (default: ${OLLAMA_MODEL})"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# =========================================================================
# WORKSPACE SETUP
# =========================================================================

WORKSPACE=$(mktemp -d /tmp/memctl_loop_demo.XXXXXX)
DB="${WORKSPACE}/.memory/memory.db"
TRACE_DIR="${WORKSPACE}/traces"
mkdir -p "$TRACE_DIR"

cleanup() {
    if [[ "$KEEP" == "false" ]]; then
        rm -rf "$WORKSPACE"
    else
        echo ""
        info "Workspace preserved: ${WORKSPACE}"
        info "  Database: ${DB}"
        info "  Traces:   ${TRACE_DIR}/"
    fi
    # Clean mock LLM state
    rm -f /tmp/memctl_mock_llm_state
}
trap cleanup EXIT

mem() {
    $MEMCTL --db "$DB" -q "$@"
}

# =========================================================================
# MAIN
# =========================================================================

cd "$PROJECT_ROOT"
banner

# -- Initialize workspace and ingest corpus --------------------------------

section "Setup: initialize workspace + ingest demo corpus"

cmd "memctl init ${WORKSPACE}/.memory"
mem init "${WORKSPACE}/.memory" 2>/dev/null
ok "Workspace initialized: ${WORKSPACE}/.memory"

cmd "memctl push \"project documentation\" --source demos/corpus/"
INGEST_OUTPUT=$(mem push "project documentation" --source demos/corpus/ 2>&1 >/dev/null)
ok "Corpus ingested: $(echo "$INGEST_OUTPUT" | grep -o '[0-9]* chunks' || echo 'done')"

STATS=$(mem stats --json 2>/dev/null)
TOTAL=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_items'])" 2>/dev/null)
ok "Store has ${TOTAL} memory items"


# =========================================================================
# Act 1: Mock LLM — deterministic, always works
# =========================================================================

act_header "1" "Mock LLM — deterministic 3-iteration loop"

info "Using demos/mock_llm.sh as a deterministic LLM simulator."
info "The mock requests 2 refinements, then produces a final answer."
echo ""

MOCK_CMD="bash ${SCRIPT_DIR}/mock_llm.sh"
TRACE1="${TRACE_DIR}/mock_trace.jsonl"

# Reset mock state
rm -f /tmp/memctl_mock_llm_state

section "1a. Run the loop (max 5 calls, expect 3 iterations)"

cmd "memctl push \"authentication security\" --source demos/corpus/ \\"
cmd "| memctl loop \"authentication security analysis\" --llm \"bash demos/mock_llm.sh\" \\"
cmd "    --max-calls 5 --trace-file ${TRACE1}"

T0=$(date +%s)
ANSWER1=$(
    mem push "authentication security" --source demos/corpus/ 2>/dev/null \
    | mem loop "authentication security analysis" \
        --llm "$MOCK_CMD" \
        --max-calls 5 \
        --trace-file "$TRACE1" \
        2>/dev/null
)
T1=$(date +%s)

if [[ -n "$ANSWER1" ]]; then
    ok "Loop completed"
    elapsed "Duration" $((T1 - T0))
else
    fail "Loop produced no output"
fi

section "1b. Trace analysis"

if [[ -f "$TRACE1" ]]; then
    ITERS=$(wc -l < "$TRACE1" | tr -d ' ')
    ok "Trace has ${ITERS} iteration(s)"
    print_trace "$TRACE1"
else
    fail "No trace file produced"
fi

section "1c. Final answer (excerpt)"

print_answer "$ANSWER1" 25

section "1d. Replay the trace"

cmd "memctl loop \"ignored\" --llm cat --replay ${TRACE1}"
REPLAY=$(mem loop "ignored" --llm cat --replay "$TRACE1" 2>/dev/null)
REPLAY_LINES=$(echo "$REPLAY" | wc -l | tr -d ' ')
ok "Replay produced ${REPLAY_LINES} trace entries"

section "1e. Store the answer in memory"

cmd "echo \"\$ANSWER\" | memctl pull --tags auth,loop,mock --title \"Auth analysis (mock loop)\""
echo "$ANSWER1" | mem pull --tags "auth,loop,mock" --title "Auth analysis (mock loop)" 2>/dev/null
ok "Answer stored in memory"

STATS2=$(mem stats --json 2>/dev/null)
TOTAL2=$(echo "$STATS2" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_items'])" 2>/dev/null)
ok "Store now has ${TOTAL2} items (was ${TOTAL})"


# =========================================================================
# Act 2: Ollama — sovereign local LLM
# =========================================================================

if [[ "$RUN_OLLAMA" == "true" ]]; then
    act_header "2" "Ollama (${OLLAMA_MODEL}) — sovereign local loop"

    # Check Ollama availability
    if ! command -v ollama &>/dev/null; then
        warn "Ollama not installed. Skipping Act 2."
        warn "Install: https://ollama.com/download"
    elif ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
        warn "Model ${OLLAMA_MODEL} not available. Pulling..."
        cmd "ollama pull ${OLLAMA_MODEL}"
        ollama pull "$OLLAMA_MODEL" 2>/dev/null || {
            fail "Failed to pull ${OLLAMA_MODEL}. Skipping Act 2."
            RUN_OLLAMA=false
        }
    fi

    if [[ "$RUN_OLLAMA" == "true" ]]; then
        TRACE2="${TRACE_DIR}/ollama_trace.jsonl"

        info "LLM: ollama run ${OLLAMA_MODEL}"
        info "Protocol: json (first-line JSON envelope)"
        info "Max calls: 3, threshold: 0.92"
        echo ""

        section "2a. Run the loop"

        cmd "memctl push \"database security architecture\" --source demos/corpus/ \\"
        cmd "| memctl loop \"Analyze the database security architecture\" \\"
        cmd "    --llm \"ollama run ${OLLAMA_MODEL}\" --max-calls 3 --trace-file ${TRACE2}"

        T0=$(date +%s)
        ANSWER2=$(
            mem push "database security architecture" --source demos/corpus/ 2>/dev/null \
            | mem loop "Analyze the database security architecture and identify potential vulnerabilities" \
                --llm "ollama run ${OLLAMA_MODEL}" \
                --max-calls 3 \
                --threshold 0.92 \
                --trace-file "$TRACE2" \
                --timeout 120 \
                2>/dev/null
        )
        T1=$(date +%s)

        if [[ -n "$ANSWER2" ]]; then
            ok "Loop completed"
            elapsed "Duration" $((T1 - T0))
        else
            fail "Loop produced no output"
        fi

        section "2b. Trace"
        if [[ -f "$TRACE2" ]]; then
            ITERS2=$(wc -l < "$TRACE2" | tr -d ' ')
            ok "Trace has ${ITERS2} iteration(s)"
            print_trace "$TRACE2"
        fi

        section "2c. Answer (excerpt)"
        print_answer "${ANSWER2:-<empty>}" 30

        section "2d. Store result"
        if [[ -n "$ANSWER2" ]]; then
            echo "$ANSWER2" | mem pull --tags "db,security,loop,ollama" --title "DB security analysis (Ollama)" 2>/dev/null
            ok "Answer stored"
        fi
    fi
fi


# =========================================================================
# Act 3: Claude — cloud LLM
# =========================================================================

if [[ "$RUN_CLAUDE" == "true" ]]; then
    act_header "3" "Claude — cloud LLM loop"

    # Check Claude CLI availability
    if ! command -v claude &>/dev/null; then
        warn "Claude CLI not installed. Skipping Act 3."
        warn "Install: npm install -g @anthropic-ai/claude-code"
    else
        TRACE3="${TRACE_DIR}/claude_trace.jsonl"

        # IMPORTANT: --setting-sources "" prevents Claude from reading
        # CLAUDE.md files, which would interfere with the JSON protocol
        CLAUDE_CMD='claude -p --setting-sources ""'

        info "LLM: claude -p --setting-sources \"\""
        info "  (--setting-sources \"\" disables CLAUDE.md to avoid protocol interference)"
        info "Protocol: json (first-line JSON envelope)"
        info "Max calls: 3, threshold: 0.92"
        echo ""

        section "3a. Run the loop"

        cmd "memctl push \"system architecture design\" --source demos/corpus/ \\"
        cmd "| memctl loop \"Provide a comprehensive analysis of the system architecture\" \\"
        cmd "    --llm 'claude -p --setting-sources \"\"' --max-calls 3 --trace-file ${TRACE3}"

        T0=$(date +%s)
        ANSWER3=$(
            mem push "system architecture design" --source demos/corpus/ 2>/dev/null \
            | mem loop "Provide a comprehensive analysis of the system architecture, covering security, database design, and scalability" \
                --llm "$CLAUDE_CMD" \
                --max-calls 3 \
                --threshold 0.92 \
                --trace-file "$TRACE3" \
                --timeout 60 \
                2>/dev/null
        )
        T1=$(date +%s)

        if [[ -n "$ANSWER3" ]]; then
            ok "Loop completed"
            elapsed "Duration" $((T1 - T0))
        else
            fail "Loop produced no output"
        fi

        section "3b. Trace"
        if [[ -f "$TRACE3" ]]; then
            ITERS3=$(wc -l < "$TRACE3" | tr -d ' ')
            ok "Trace has ${ITERS3} iteration(s)"
            print_trace "$TRACE3"
        fi

        section "3c. Answer (excerpt)"
        print_answer "${ANSWER3:-<empty>}" 30

        section "3d. Store result"
        if [[ -n "$ANSWER3" ]]; then
            echo "$ANSWER3" | mem pull --tags "arch,security,loop,claude" --title "Architecture analysis (Claude)" 2>/dev/null
            ok "Answer stored"
        fi
    fi
fi


# =========================================================================
# Finale: summary
# =========================================================================

section "Summary"

FINAL_STATS=$(mem stats --json 2>/dev/null)
FINAL_TOTAL=$(echo "$FINAL_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_items'])" 2>/dev/null)

ok "Total items in store: ${FINAL_TOTAL}"
ok "Traces saved in: ${TRACE_DIR}/"
ls -1 "$TRACE_DIR"/*.jsonl 2>/dev/null | while read -r f; do
    lines=$(wc -l < "$f" | tr -d ' ')
    name=$(basename "$f")
    ok "  ${name}: ${lines} iteration(s)"
done

echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}${BOLD}  Pipeline pattern:${NC}"
echo -e "${DIM}    memctl push \"query\" --source docs/ \\${NC}"
echo -e "${DIM}    | memctl loop \"question\" --llm \"claude -p\" --max-calls 3 --trace \\${NC}"
echo -e "${DIM}    | memctl pull --tags analysis --title \"Loop result\"${NC}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ "$KEEP" == "true" ]]; then
    info "Workspace preserved: ${WORKSPACE}"
    info "  Replay: memctl loop q --llm cat --replay ${TRACE_DIR}/mock_trace.jsonl --db ${DB}"
fi

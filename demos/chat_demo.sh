#!/usr/bin/env bash
# =========================================================================
# chat_demo.sh — memctl chat proof-of-concept
#
# Demonstrates the interactive chat REPL with a scripted session.
# Uses mock_chat_llm.sh as the LLM backend (no real LLM needed).
#
# Usage:
#   bash demos/chat_demo.sh
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR=$(mktemp -d /tmp/memctl_chat_demo.XXXXXX)
DB="$DEMO_DIR/memory.db"

# Colors (TTY-aware)
if [[ -t 1 ]]; then
    BOLD=$(printf '\033[1m')
    DIM=$(printf '\033[2m')
    GREEN=$(printf '\033[32m')
    CYAN=$(printf '\033[36m')
    RESET=$(printf '\033[0m')
else
    BOLD="" DIM="" GREEN="" CYAN="" RESET=""
fi

cleanup() { rm -rf "$DEMO_DIR"; }
trap cleanup EXIT

printf "%s=== memctl chat demo ===%s\n\n" "$BOLD" "$RESET"

# Step 1: Initialize + ingest corpus
printf "%s[1/3]%s Initializing workspace and ingesting corpus...\n" "$GREEN" "$RESET"
python -m memctl.cli init "$DEMO_DIR" --db "$DB" -q >/dev/null
python -m memctl.cli push "architecture" \
    --source "$SCRIPT_DIR/corpus-mini/" \
    --db "$DB" -q >/dev/null
printf "  %sDone: corpus-mini ingested%s\n\n" "$DIM" "$RESET"

# Step 2: Reset mock LLM state
export MOCK_LLM_STATE="$DEMO_DIR/mock_state"

# Step 3: Run two separate chat turns to show clean Q&A
printf "%s[2/3]%s Running chat session (mock LLM, passive protocol)...\n" "$GREEN" "$RESET"
printf "  %sEach question gets one LLM call (passive, no refinement)%s\n\n" "$DIM" "$RESET"

# Turn 1
Q1="How does authentication work?"
printf "%s> %s%s\n" "$CYAN" "$Q1" "$RESET"
printf '%s\n' "$Q1" \
    | python -m memctl.cli chat \
        --llm "bash $SCRIPT_DIR/mock_chat_llm.sh" \
        --protocol passive --store \
        --db "$DB" -q
printf "\n"

# Turn 2 (fresh mock state for second answer)
Q2="What about session management?"
printf "%s> %s%s\n" "$CYAN" "$Q2" "$RESET"
printf '%s\n' "$Q2" \
    | python -m memctl.cli chat \
        --llm "bash $SCRIPT_DIR/mock_chat_llm.sh" \
        --protocol passive --store \
        --db "$DB" -q
printf "\n"

printf "%s--- End of chat ---%s\n\n" "$DIM" "$RESET"

# Step 4: Verify storage
printf "%s[3/3]%s Verifying...\n" "$GREEN" "$RESET"
STORED=$(python -c "
from memctl.store import MemoryStore
s = MemoryStore(db_path='$DB')
items = s.search_fulltext('authentication', limit=50)
chat_items = [i for i in items if 'chat' in i.tags]
print(len(chat_items))
s.close()
")
printf "  Chat answers stored as STM: %s\n" "$STORED"
printf "  %sChat REPL exited cleanly on EOF%s\n\n" "$DIM" "$RESET"

printf "%s=== Demo complete ===%s\n" "$BOLD" "$RESET"

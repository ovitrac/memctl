#!/usr/bin/env bash
# =========================================================================
# _demo_lib.sh — Shared helpers for memctl demos
#
# Source this file, do not execute it directly.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/_demo_lib.sh"
#
# Provides:
#   Colors (TTY-aware)   — R, G, Y, B, C, M, W, D, BD, NC
#   Logging              — ok, fail, info, warn, cmd, step
#   Display              — print_answer, print_trace
#   Workspace            — setup_workspace, mem()
#   Capability detection — has_cmd, has_flag
#   Database queries     — py_query
#   Version info         — print_versions
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
# =========================================================================

# -- Colors (TTY-aware) ----------------------------------------------------
if [[ -t 1 ]]; then
    R='\033[0;31m'  G='\033[0;32m'  Y='\033[1;33m'  B='\033[0;34m'
    C='\033[0;36m'  M='\033[0;35m'  W='\033[1;37m'  D='\033[2m'
    BD='\033[1m'    NC='\033[0m'
else
    R='' G='' Y='' B='' C='' M='' W='' D='' BD='' NC=''
fi

# -- Logging ---------------------------------------------------------------
ok()   { printf "  ${G}[ok]${NC}    %s\n" "$1"; }
fail() { printf "  ${R}[FAIL]${NC}  %s\n" "$1"; }
info() { printf "  ${B}[info]${NC}  %s\n" "$1"; }
warn() { printf "  ${Y}[warn]${NC}  %s\n" "$1"; }
skip() { printf "  ${Y}[SKIP]${NC}  %s\n" "$1"; }
cmd()  { printf "  ${Y}\$${NC} ${D}%s${NC}\n" "$1"; }

step() {
    local n="$1" total="$2"; shift 2
    printf "\n  ${M}${BD}[%s/%s]${NC} ${W}${BD}%s${NC}\n" "$n" "$total" "$*"
}

# -- Display ---------------------------------------------------------------
print_box_start() { printf "  ${D}┌─ %s ─${NC}\n" "$1"; }
print_box_line()  { printf "  ${D}│${NC} %s\n"     "$1"; }
print_box_end()   { printf "  ${D}└──────────────────────────────────────────────────────┘${NC}\n"; }

print_answer() {
    local text="$1" max_lines="${2:-30}"
    print_box_start "answer ─────────────────────────────────────────────"
    local n=0
    while IFS= read -r line; do
        (( ++n > max_lines )) && break
        print_box_line "$line"
    done <<< "$text"
    local total_lines
    total_lines=$(printf '%s\n' "$text" | wc -l | tr -d ' ')
    if (( total_lines > max_lines )); then
        printf "  ${D}│ ... (%d more lines)${NC}\n" "$((total_lines - max_lines))"
    fi
    print_box_end
}

print_trace() {
    local trace_file="$1"
    print_box_start "trace ──────────────────────────────────────────────"
    while IFS= read -r line; do
        local iter query action
        iter=$(printf '%s' "$line"  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('iter','?'))" 2>/dev/null)
        query=$(printf '%s' "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('query') or '-')" 2>/dev/null)
        action=$(printf '%s' "$line"| python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('action','?'))" 2>/dev/null)
        printf "  ${D}│${NC}  iter=${BD}%s${NC}  query=${C}%s${NC}  action=${G}%s${NC}\n" "$iter" "$query" "$action"
    done < "$trace_file"
    print_box_end
}

# -- Workspace -------------------------------------------------------------
# Call setup_workspace to create a temp dir.  Sets WS, DB.
# Caller must set KEEP=true/false before calling.
setup_workspace() {
    local prefix="${1:-memctl_demo}"
    WS=$(mktemp -d "/tmp/${prefix}.XXXXXX")
    DB="$WS/.memory/memory.db"
}

# mem() wrapper — call after setup_workspace and setting MEMCTL
mem() { $MEMCTL --db "$DB" -q "$@"; }

# -- Capability detection --------------------------------------------------
# has_cmd "loop"   → true if memctl exposes the "loop" subcommand
# has_flag "loop" "--trace"  → true if memctl loop supports --trace
has_cmd() {
    $MEMCTL --help 2>/dev/null | grep -qE "(^|[[:space:]])$1([[:space:]]|$)"
}

has_flag() {
    $MEMCTL "$1" --help 2>/dev/null | grep -q -- "$2"
}

# -- Database queries via Python sqlite3 -----------------------------------
# py_query DB "SQL"  → prints result rows as pipe-separated values
py_query() {
    local db="$1" sql="$2"
    python3 -c "
import sqlite3, sys
conn = sqlite3.connect('$db')
for row in conn.execute('''$sql''').fetchall():
    print('|'.join(str(c) for c in row))
conn.close()
" 2>/dev/null
}

# py_scalar DB "SQL"  → prints a single scalar value
py_scalar() {
    local db="$1" sql="$2"
    python3 -c "
import sqlite3
conn = sqlite3.connect('$db')
print(conn.execute('''$sql''').fetchone()[0])
conn.close()
" 2>/dev/null
}

# -- JSON field extraction (no jq dependency) ------------------------------
json_field() {
    local field="$1"
    python3 -c "import sys,json; print(json.load(sys.stdin).get('$field', '?'))"
}

# -- Version info ----------------------------------------------------------
print_versions() {
    local memctl_ver sqlite_ver python_ver
    memctl_ver=$(python3 -c "import memctl; print(memctl.__version__)" 2>/dev/null || echo "?")
    sqlite_ver=$(python3 -c "import sqlite3; print(sqlite3.sqlite_version)" 2>/dev/null || echo "?")
    python_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "?")
    printf "\n"
    info "memctl ${memctl_ver} | Python ${python_ver} | SQLite ${sqlite_ver}"
}

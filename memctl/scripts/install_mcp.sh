#!/usr/bin/env bash
# install_mcp.sh — Install and configure memctl MCP server
#
# Sets up memctl as an MCP server for Claude Code or Claude Desktop.
# Idempotent: safe to re-run. Creates timestamped backups before editing config.
#
# Usage:
#   bash "$(memctl scripts-path)/install_mcp.sh" [OPTIONS]
#
# Options:
#   --client TARGET    claude-code (default), claude-desktop, all
#   --python PATH      Python interpreter (default: python3 or $VIRTUAL_ENV/bin/python)
#   --db PATH          Database path (default: ~/.local/share/memctl/memory.db)
#   --yes              Non-interactive mode (required for --client all)
#   --dry-run          Show what would be done without making changes
#   -h, --help         Show this help
#
# Supported platforms: macOS, Linux
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

readonly SCRIPT_NAME="$(basename "$0")"
readonly DEFAULT_DB="${HOME}/.local/share/memctl/memory.db"

# ---------------------------------------------------------------------------
# Colors (TTY-aware)
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_YELLOW=$'\033[0;33m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_BOLD=$'\033[1m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_BOLD="" C_RESET=""
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf "%s[info]%s  %s\n" "$C_BLUE"  "$C_RESET" "$*"; }
ok()    { printf "%s[ok]%s    %s\n" "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf "%s[warn]%s  %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
fail()  { printf "%s[error]%s %s\n" "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} $SCRIPT_NAME [OPTIONS]

Install and configure memctl as an MCP server.

${C_BOLD}Options:${C_RESET}
  --client TARGET    Target client: claude-code (default), claude-desktop, all
  --python PATH      Python interpreter (default: python3)
  --db PATH          Database path (default: ~/.local/share/memctl/memory.db)
  --yes              Non-interactive mode (required for --client all)
  --dry-run          Show what would be done without making changes
  -h, --help         Show this help

${C_BOLD}Examples:${C_RESET}
  $SCRIPT_NAME                                # Claude Code (default)
  $SCRIPT_NAME --client claude-desktop        # Claude Desktop
  $SCRIPT_NAME --client all --yes             # Both clients, non-interactive
  $SCRIPT_NAME --python /usr/bin/python3.12   # Specific Python
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

CLIENT="claude-code"
PYTHON_CMD=""
DB_PATH="$DEFAULT_DB"
YES=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --client)
            CLIENT="${2:?--client requires a value: claude-code, claude-desktop, all}"
            shift 2
            ;;
        --python)
            PYTHON_CMD="${2:?--python requires a path}"
            shift 2
            ;;
        --db)
            DB_PATH="${2:?--db requires a path}"
            shift 2
            ;;
        --yes)
            YES=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            fail "Unknown option: $1 (see --help)"
            ;;
    esac
done

# Validate --client
case "$CLIENT" in
    claude-code|claude-desktop|all) ;;
    *) fail "Invalid --client value: $CLIENT (expected: claude-code, claude-desktop, all)" ;;
esac

# --client all requires --yes
if [[ "$CLIENT" == "all" && "$YES" == "false" ]]; then
    fail "--client all requires --yes (non-interactive mode)"
fi

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

detect_platform() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      fail "Unsupported platform: $(uname -s). Only macOS and Linux are supported." ;;
    esac
}

PLATFORM="$(detect_platform)"

# ---------------------------------------------------------------------------
# Step 1: Verify prerequisites
# ---------------------------------------------------------------------------

info "Step 1/5: Checking prerequisites"

# Resolve Python interpreter
if [[ -n "$PYTHON_CMD" ]]; then
    # Explicit --python
    if ! command -v "$PYTHON_CMD" &>/dev/null; then
        fail "Python not found at: $PYTHON_CMD"
    fi
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
    # Active virtualenv
    PYTHON_CMD="${VIRTUAL_ENV}/bin/python"
    if ! command -v "$PYTHON_CMD" &>/dev/null; then
        PYTHON_CMD="python3"
    fi
else
    PYTHON_CMD="python3"
fi

if ! command -v "$PYTHON_CMD" &>/dev/null; then
    fail "python3 not found in PATH. Install Python 3.10+ or use --python PATH."
fi

# Verify Python version >= 3.10
PY_VERSION=$("$PYTHON_CMD" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) \
    || fail "Failed to determine Python version from: $PYTHON_CMD"

PY_MAJOR=$("$PYTHON_CMD" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON_CMD" -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
    fail "Python >= 3.10 required, found $PY_VERSION"
fi

ok "Python $PY_VERSION ($PYTHON_CMD)"

# Check pip
if "$PYTHON_CMD" -m pip --version &>/dev/null; then
    ok "pip available"
else
    warn "pip not found in this Python environment — install may fail"
fi

# Check if memctl is already importable
MEMCTL_INSTALLED=false
if "$PYTHON_CMD" -c "import memctl" &>/dev/null; then
    MEMCTL_VERSION=$("$PYTHON_CMD" -c "from memctl import __version__; print(__version__)")
    ok "memctl $MEMCTL_VERSION already installed"
    MEMCTL_INSTALLED=true
else
    info "memctl not yet installed (will install in step 2)"
fi

# ---------------------------------------------------------------------------
# Step 2: Install memctl[mcp]
# ---------------------------------------------------------------------------

info "Step 2/5: Installing memctl[mcp]"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would run: $PYTHON_CMD -m pip install -U \"memctl[mcp]\""
else
    if "$PYTHON_CMD" -m pip install -U "memctl[mcp]" 2>&1 | while IFS= read -r line; do
        # Show only key lines, suppress noise
        case "$line" in
            *Successfully*|*already*|*Requirement*|*Installing*|*Collecting*)
                printf "  %s\n" "$line"
                ;;
        esac
    done; then
        ok "memctl[mcp] installed"
    else
        fail "pip install failed. Check your Python environment."
    fi
fi

# Verify import after install
if [[ "$DRY_RUN" == "false" ]]; then
    if ! "$PYTHON_CMD" -c "import memctl" &>/dev/null; then
        fail "memctl not importable after install. Check your Python environment."
    fi
    MEMCTL_VERSION=$("$PYTHON_CMD" -c "from memctl import __version__; print(__version__)")
    ok "memctl $MEMCTL_VERSION ready"
fi

# ---------------------------------------------------------------------------
# Step 3: Initialize workspace
# ---------------------------------------------------------------------------

info "Step 3/5: Initializing workspace"

DB_DIR="$(dirname "$DB_PATH")"

if [[ -f "$DB_PATH" ]]; then
    ok "Database exists: $DB_PATH"
else
    if [[ "$DRY_RUN" == "true" ]]; then
        info "[dry-run] Would run: memctl init $DB_DIR --db $DB_PATH"
    else
        mkdir -p "$DB_DIR"
        "$PYTHON_CMD" -m memctl.cli init "$DB_DIR" --db "$DB_PATH" -q \
            || fail "memctl init failed"
        ok "Workspace initialized: $DB_DIR"
    fi
fi

# ---------------------------------------------------------------------------
# Step 4: Configure MCP client(s)
# ---------------------------------------------------------------------------

info "Step 4/5: Configuring MCP client"

# Resolve config file paths per client
resolve_config_path() {
    local client="$1"
    case "$client" in
        claude-code)
            echo "${HOME}/.claude/settings.json"
            ;;
        claude-desktop)
            case "$PLATFORM" in
                macos)
                    echo "${HOME}/Library/Application Support/Claude/claude_desktop_config.json"
                    ;;
                linux)
                    echo "${HOME}/.config/Claude/claude_desktop_config.json"
                    ;;
            esac
            ;;
    esac
}

# Update a single JSON config file: insert/update mcpServers.memctl
update_config() {
    local config_path="$1"
    local db="$2"
    local py="$3"
    local dry="$4"

    if [[ "$dry" == "true" ]]; then
        info "[dry-run] Would update: $config_path"
        info "[dry-run]   Set mcpServers.memctl → memctl serve --db $db"
        return 0
    fi

    "$py" -c "
import json, os, shutil, sys
from datetime import datetime

config_path = sys.argv[1]
db_path = sys.argv[2]

# Read existing or create empty
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}

    # Timestamped backup
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = config_path + '.bak.' + ts
    shutil.copy2(config_path, backup)
    print(f'  Backup: {backup}')
else:
    config = {}
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

# Insert/update memctl server entry
if 'mcpServers' not in config:
    config['mcpServers'] = {}

config['mcpServers']['memctl'] = {
    'command': 'memctl',
    'args': ['serve', '--db', db_path]
}

with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'  Updated: {config_path}')
" "$config_path" "$db"
}

# Build list of clients to configure
CLIENTS=()
case "$CLIENT" in
    claude-code)    CLIENTS=("claude-code") ;;
    claude-desktop) CLIENTS=("claude-desktop") ;;
    all)            CLIENTS=("claude-code" "claude-desktop") ;;
esac

# For --client all, show plan before executing
if [[ "$CLIENT" == "all" ]]; then
    info "Will configure the following files:"
    for c in "${CLIENTS[@]}"; do
        cfg="$(resolve_config_path "$c")"
        printf "  - %s (%s)\n" "$cfg" "$c"
    done
fi

for c in "${CLIENTS[@]}"; do
    cfg="$(resolve_config_path "$c")"
    info "Configuring $c → $cfg"
    update_config "$cfg" "$DB_PATH" "$PYTHON_CMD" "$DRY_RUN"
    ok "$c configured"
done

# ---------------------------------------------------------------------------
# Step 5: Verify server
# ---------------------------------------------------------------------------

info "Step 5/5: Verifying server"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would run: memctl serve --check --db $DB_PATH"
else
    if "$PYTHON_CMD" -m memctl.cli serve --check --db "$DB_PATH" 2>/dev/null; then
        ok "Server verification passed"
    else
        warn "Server check failed — MCP dependencies may not be installed correctly"
        warn "Try: $PYTHON_CMD -m pip install -U 'memctl[mcp]'"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "\n"
printf "%s%s=== memctl MCP Setup Complete ===%s\n" "$C_BOLD" "$C_GREEN" "$C_RESET"
printf "\n"
printf "  Python:    %s (%s)\n" "$PYTHON_CMD" "${PY_VERSION:-?}"
printf "  Database:  %s\n" "$DB_PATH"
for c in "${CLIENTS[@]}"; do
    cfg="$(resolve_config_path "$c")"
    printf "  Config:    %s (%s)\n" "$cfg" "$c"
done
printf "\n"

if [[ "$DRY_RUN" == "true" ]]; then
    printf "  %s[dry-run] No changes were made.%s\n\n" "$C_YELLOW" "$C_RESET"
else
    printf "  Restart your MCP client to activate memctl.\n"
    printf "  Test with: memctl serve --check --db %s\n\n" "$DB_PATH"
fi

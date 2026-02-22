#!/usr/bin/env bash
# uninstall_mcp.sh — Remove memctl MCP config and/or Claude Code hooks
#
# Removes memctl entries from MCP client config and hook entries from
# Claude Code settings. Preserves .memory/ data (never deletes user data).
#
# Usage:
#   bash "$(memctl scripts-path)/uninstall_mcp.sh" [OPTIONS]
#
# Options:
#   --client TARGET    claude-code (default), claude-desktop, all
#   --hooks-only       Only remove hooks, keep MCP config
#   --mcp-only         Only remove MCP config, keep hooks
#   --dry-run          Show what would be done without making changes
#   -h, --help         Show this help
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"

# Colors (TTY-aware)
if [[ -t 1 ]]; then
    readonly C_GREEN=$'\033[0;32m' C_YELLOW=$'\033[0;33m'
    readonly C_BLUE=$'\033[0;34m' C_RED=$'\033[0;31m'
    readonly C_BOLD=$'\033[1m' C_RESET=$'\033[0m'
else
    readonly C_GREEN="" C_YELLOW="" C_BLUE="" C_RED="" C_BOLD="" C_RESET=""
fi

info()  { printf "%s[info]%s  %s\n" "$C_BLUE"  "$C_RESET" "$*"; }
ok()    { printf "%s[ok]%s    %s\n" "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf "%s[warn]%s  %s\n" "$C_YELLOW" "$C_RESET" "$*" >&2; }
fail()  { printf "%s[error]%s %s\n" "$C_RED"    "$C_RESET" "$*" >&2; exit 1; }

# Parse arguments
CLIENT="claude-code"
HOOKS_ONLY=false
MCP_ONLY=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --client)    CLIENT="${2:?--client requires a value}"; shift 2 ;;
        --hooks-only) HOOKS_ONLY=true; shift ;;
        --mcp-only)   MCP_ONLY=true; shift ;;
        --dry-run)    DRY_RUN=true; shift ;;
        -h|--help)
            printf "%sUsage:%s %s [--client TARGET] [--hooks-only|--mcp-only] [--dry-run]\n\n" \
                "$C_BOLD" "$C_RESET" "$SCRIPT_NAME"
            printf "Remove memctl MCP config and/or Claude Code hooks.\n\n"
            printf "Options:\n"
            printf "  --client TARGET    claude-code|claude-desktop|all (default: claude-code)\n"
            printf "  --hooks-only       Only remove hooks\n"
            printf "  --mcp-only         Only remove MCP config\n"
            printf "  --dry-run          Preview without modifying\n"
            exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[[ "$HOOKS_ONLY" == "true" && "$MCP_ONLY" == "true" ]] && \
    fail "--hooks-only and --mcp-only are mutually exclusive"

# Platform detection
detect_platform() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      fail "Unsupported platform: $(uname -s)" ;;
    esac
}
PLATFORM="$(detect_platform)"

# Config path resolution
resolve_config_path() {
    local client="$1"
    case "$client" in
        claude-code) echo "${HOME}/.claude/settings.json" ;;
        claude-desktop)
            case "$PLATFORM" in
                macos) echo "${HOME}/Library/Application Support/Claude/claude_desktop_config.json" ;;
                linux) echo "${HOME}/.config/Claude/claude_desktop_config.json" ;;
            esac ;;
    esac
}

# Remove memctl from a JSON config file
remove_from_config() {
    local config_path="$1"
    local remove_mcp="$2"
    local remove_hooks="$3"

    [[ -f "$config_path" ]] || { info "Not found: $config_path (nothing to remove)"; return 0; }

    if [[ "$DRY_RUN" == "true" ]]; then
        [[ "$remove_mcp" == "true" ]] && info "[dry-run] Would remove mcpServers.memctl from $config_path"
        [[ "$remove_hooks" == "true" ]] && info "[dry-run] Would remove memctl hooks from $config_path"
        return 0
    fi

    python3 -c "
import json, os, shutil, sys
from datetime import datetime

config_path = sys.argv[1]
remove_mcp = sys.argv[2] == 'true'
remove_hooks = sys.argv[3] == 'true'

with open(config_path, 'r', encoding='utf-8') as f:
    try:
        config = json.load(f)
    except json.JSONDecodeError:
        print(f'  Skipped (malformed JSON): {config_path}')
        sys.exit(0)

changed = False

# Remove MCP server entry
if remove_mcp and 'mcpServers' in config and 'memctl' in config['mcpServers']:
    del config['mcpServers']['memctl']
    if not config['mcpServers']:
        del config['mcpServers']
    changed = True
    print(f'  Removed mcpServers.memctl')

# Remove hooks
if remove_hooks and 'hooks' in config:
    for event in ['PreToolUse', 'PostToolUse']:
        if event in config['hooks']:
            before = len(config['hooks'][event])
            config['hooks'][event] = [
                e for e in config['hooks'][event]
                if 'memctl_' not in json.dumps(e)
            ]
            after = len(config['hooks'][event])
            if after < before:
                changed = True
                print(f'  Removed {before - after} memctl hook(s) from {event}')
            if not config['hooks'][event]:
                del config['hooks'][event]
    if not config['hooks']:
        del config['hooks']

if changed:
    # Timestamped backup
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = config_path + '.bak.' + ts
    shutil.copy2(config_path, backup)
    print(f'  Backup: {backup}')

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'  Updated: {config_path}')
else:
    print(f'  No memctl entries found in: {config_path}')
" "$config_path" "$remove_mcp" "$remove_hooks"
}

# Build client list
CLIENTS=()
case "$CLIENT" in
    claude-code)    CLIENTS=("claude-code") ;;
    claude-desktop) CLIENTS=("claude-desktop") ;;
    all)            CLIENTS=("claude-code" "claude-desktop") ;;
    *) fail "Invalid --client: $CLIENT" ;;
esac

# Determine what to remove
REMOVE_MCP=true
REMOVE_HOOKS=true
[[ "$HOOKS_ONLY" == "true" ]] && REMOVE_MCP=false
[[ "$MCP_ONLY" == "true" ]] && REMOVE_HOOKS=false

info "Removing memctl from: ${CLIENTS[*]}"

for c in "${CLIENTS[@]}"; do
    cfg="$(resolve_config_path "$c")"
    info "Processing: $cfg ($c)"
    remove_from_config "$cfg" "$REMOVE_MCP" "$REMOVE_HOOKS"
done

ok "Uninstall complete"
info "Note: .memory/ data is preserved (use 'rm -rf .memory/' to remove manually)"

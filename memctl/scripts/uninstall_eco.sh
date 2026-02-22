#!/usr/bin/env bash
# uninstall_eco.sh — Remove memctl eco mode artifacts
#
# Removes the eco hook, strategy file, and /eco slash command. Does NOT remove:
#   - .memory/memory.db (user data — never deleted)
#   - MCP server config (use uninstall_mcp.sh for that)
#   - v0.8 safety hooks (PreToolUse/PostToolUse)
#   - .gitignore entries
#
# Usage:
#   bash "$(memctl scripts-path)/uninstall_eco.sh" [--dry-run]
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"

# Target paths
readonly CLAUDE_DIR=".claude"
readonly HOOK_FILE="${CLAUDE_DIR}/hooks/eco-hint.sh"
readonly ECO_DIR="${CLAUDE_DIR}/eco"
readonly ECO_FILE="${ECO_DIR}/ECO.md"
readonly COMMAND_FILE="${CLAUDE_DIR}/commands/eco.md"
readonly SETTINGS_FILE="${CLAUDE_DIR}/settings.local.json"

# Colors (TTY-aware)
if [[ -t 1 ]]; then
    readonly C_RED=$'\033[0;31m' C_GREEN=$'\033[0;32m'
    readonly C_YELLOW=$'\033[0;33m' C_BLUE=$'\033[0;34m'
    readonly C_CYAN=$'\033[0;36m' C_BOLD=$'\033[1m' C_RESET=$'\033[0m'
else
    readonly C_RED="" C_GREEN="" C_YELLOW="" C_BLUE=""
    readonly C_CYAN="" C_BOLD="" C_RESET=""
fi

info()  { printf "%s[info]%s  %s\n" "$C_BLUE"   "$C_RESET" "$*"; }
ok()    { printf "%s[ok]%s    %s\n" "$C_GREEN"   "$C_RESET" "$*"; }
warn()  { printf "%s[warn]%s  %s\n" "$C_YELLOW"  "$C_RESET" "$*" >&2; }

usage() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} $SCRIPT_NAME [--dry-run]

Remove memctl eco mode (hook + strategy file).
Does NOT remove .memory/memory.db or MCP server config.

${C_BOLD}Options:${C_RESET}
  --dry-run    Show what would be done without making changes
  -h, --help   Show this help
EOF
    exit 0
}

# Parse arguments
DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage ;;
        *) printf "%s[error]%s Unknown option: %s\n" "$C_RED" "$C_RESET" "$1" >&2; exit 1 ;;
    esac
done

printf "\n%s%s  memctl eco mode — uninstaller%s\n\n" "$C_BOLD" "$C_CYAN" "$C_RESET"

REMOVED=0

# ---------------------------------------------------------------------------
# Remove eco hook
# ---------------------------------------------------------------------------

if [[ -f "$HOOK_FILE" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        info "[dry-run] Would remove: $HOOK_FILE"
    else
        rm "$HOOK_FILE"
        ok "Removed: $HOOK_FILE"
    fi
    REMOVED=$((REMOVED + 1))
else
    info "Hook not found: $HOOK_FILE (already removed)"
fi

# ---------------------------------------------------------------------------
# Remove ECO.md strategy file
# ---------------------------------------------------------------------------

if [[ -f "$ECO_FILE" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        info "[dry-run] Would remove: $ECO_FILE"
    else
        rm "$ECO_FILE"
        ok "Removed: $ECO_FILE"
    fi
    REMOVED=$((REMOVED + 1))
else
    info "Strategy file not found: $ECO_FILE (already removed)"
fi

# Remove eco directory if empty
if [[ -d "$ECO_DIR" ]]; then
    if [[ "$DRY_RUN" == "false" ]]; then
        rmdir "$ECO_DIR" 2>/dev/null && ok "Removed empty directory: $ECO_DIR" || true
    fi
fi

# ---------------------------------------------------------------------------
# Remove /eco slash command
# ---------------------------------------------------------------------------

if [[ -f "$COMMAND_FILE" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        info "[dry-run] Would remove: $COMMAND_FILE"
    else
        rm "$COMMAND_FILE"
        ok "Removed: $COMMAND_FILE"
    fi
    REMOVED=$((REMOVED + 1))
else
    info "Slash command not found: $COMMAND_FILE (already removed)"
fi

# ---------------------------------------------------------------------------
# Remove UserPromptSubmit hook registration from settings
# ---------------------------------------------------------------------------

if [[ -f "$SETTINGS_FILE" ]]; then
    info "Updating $SETTINGS_FILE"
    if [[ "$DRY_RUN" == "true" ]]; then
        info "[dry-run] Would remove UserPromptSubmit eco-hint entry"
    else
        python3 -c "
import json, os, shutil, sys
from datetime import datetime

settings_path = sys.argv[1]

with open(settings_path, 'r', encoding='utf-8') as f:
    try:
        config = json.load(f)
    except json.JSONDecodeError:
        sys.exit(0)

# Backup
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
backup = settings_path + '.bak.' + ts
shutil.copy2(settings_path, backup)
print(f'  Backup: {backup}')

changed = False

# Remove eco-hint from UserPromptSubmit
hooks = config.get('hooks', {})
if 'UserPromptSubmit' in hooks:
    before = len(hooks['UserPromptSubmit'])
    hooks['UserPromptSubmit'] = [
        e for e in hooks['UserPromptSubmit']
        if 'eco-hint' not in json.dumps(e)
    ]
    after = len(hooks['UserPromptSubmit'])
    if after < before:
        changed = True
    # Remove empty list
    if not hooks['UserPromptSubmit']:
        del hooks['UserPromptSubmit']
    # Remove empty hooks section
    if not hooks:
        del config['hooks']

# Remove memctl from mcpServers only if no other tools reference it
# (we do NOT remove MCP server config — that's uninstall_mcp.sh's job)

if changed:
    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print('  Hook registration removed')
else:
    print('  No eco-hint hook found in settings')
" "$SETTINGS_FILE"
    fi
    REMOVED=$((REMOVED + 1))
else
    info "Settings file not found: $SETTINGS_FILE"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "\n"
if [[ "$REMOVED" -gt 0 ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        printf "  %s[dry-run] No changes were made.%s\n" "$C_YELLOW" "$C_RESET"
    else
        printf "  %seco mode removed.%s\n" "$C_GREEN" "$C_RESET"
    fi
else
    printf "  Nothing to remove — eco mode was not installed.\n"
fi

printf "\n  Preserved:\n"
printf "    .memory/memory.db    (your knowledge — never deleted)\n"
printf "    MCP server config    (use uninstall_mcp.sh to remove)\n"
printf "    Safety hooks         (PreToolUse/PostToolUse)\n"
printf "\n"

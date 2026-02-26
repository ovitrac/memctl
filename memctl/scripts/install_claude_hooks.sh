#!/usr/bin/env bash
# install_claude_hooks.sh — Install memctl Claude Code hooks
#
# Registers safety guard (PreToolUse) and audit logger (PostToolUse)
# hooks into Claude Code settings. Idempotent: safe to re-run.
#
# Usage:
#   bash "$(memctl scripts-path)/install_claude_hooks.sh" [--dry-run] [--yes]
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly SETTINGS_FILE="${HOME}/.claude/settings.json"

# Resolve hook paths relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly HOOKS_DIR="${SCRIPT_DIR}/../templates/hooks"

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
DRY_RUN=false
YES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --yes)     YES=true; shift ;;
        -h|--help)
            printf "%sUsage:%s %s [--dry-run] [--yes]\n\n" "$C_BOLD" "$C_RESET" "$SCRIPT_NAME"
            printf "Install memctl Claude Code hooks (safety guard + audit logger).\n"
            exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

# Verify memctl is installed (required for cross-platform CLI hooks)
if ! command -v memctl &>/dev/null; then
    warn "memctl command not found in PATH. Hooks will use 'memctl hooks <name>'."
    warn "Ensure memctl is installed: pip install memctl"
fi

# Verify hook templates exist (reference copies)
[[ -f "${HOOKS_DIR}/memctl_safety_guard.sh" ]] || fail "Hook not found: ${HOOKS_DIR}/memctl_safety_guard.sh"
[[ -f "${HOOKS_DIR}/memctl_audit_logger.sh" ]] || fail "Hook not found: ${HOOKS_DIR}/memctl_audit_logger.sh"

# Confirm unless --yes
if [[ "$YES" == "false" && "$DRY_RUN" == "false" ]]; then
    printf "This will update: %s\n" "$SETTINGS_FILE"
    printf "Hooks:\n"
    printf "  PreToolUse:  memctl hooks safety-guard\n"
    printf "  PostToolUse: memctl hooks audit-logger\n"
    printf "\nProceed? [y/N] "
    read -r answer
    [[ "$answer" =~ ^[Yy] ]] || { info "Aborted."; exit 0; }
fi

info "Installing Claude Code hooks"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would update: $SETTINGS_FILE"
    info "[dry-run] PreToolUse → memctl hooks safety-guard"
    info "[dry-run] PostToolUse → memctl hooks audit-logger"
    exit 0
fi

# Update settings.json with Python (no jq dependency)
# Uses cross-platform CLI commands instead of .sh file paths
python3 -c "
import json, os, shutil, sys
from datetime import datetime

settings_path = sys.argv[1]

# Read or create
if os.path.exists(settings_path):
    with open(settings_path, 'r', encoding='utf-8') as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}
    # Timestamped backup
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = settings_path + '.bak.' + ts
    shutil.copy2(settings_path, backup)
    print(f'  Backup: {backup}')
else:
    config = {}
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)

# Ensure hooks structure
if 'hooks' not in config:
    config['hooks'] = {}

# PreToolUse — cross-platform CLI command
pre = config['hooks'].get('PreToolUse', [])
guard_entry = {'hooks': [{'type': 'command', 'command': 'memctl hooks safety-guard'}]}
# Remove existing memctl guard entries (catches both .sh and CLI)
pre = [e for e in pre if 'safety-guard' not in json.dumps(e)
       and 'memctl_safety_guard' not in json.dumps(e)]
pre.append(guard_entry)
config['hooks']['PreToolUse'] = pre

# PostToolUse — cross-platform CLI command
post = config['hooks'].get('PostToolUse', [])
logger_entry = {'hooks': [{'type': 'command', 'command': 'memctl hooks audit-logger'}]}
# Remove existing memctl logger entries (catches both .sh and CLI)
post = [e for e in post if 'audit-logger' not in json.dumps(e)
        and 'memctl_audit_logger' not in json.dumps(e)]
post.append(logger_entry)
config['hooks']['PostToolUse'] = post

with open(settings_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'  Updated: {settings_path}')
" "$SETTINGS_FILE"

ok "Hooks installed"
printf "\n  PreToolUse:  memctl hooks safety-guard\n"
printf "  PostToolUse: memctl hooks audit-logger\n\n"

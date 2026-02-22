#!/usr/bin/env bash
# install_eco.sh — One-shot installer for memctl eco mode
#
# Sets up token-efficient file exploration for Claude Code:
#   1. Verifies memctl[mcp] is installed
#   2. Registers the MCP server with --db-root (project-scoped)
#   3. Installs the eco hook (.claude/hooks/eco-hint.sh)
#   4. Installs the strategy file (.claude/eco/ECO.md)
#   5. Installs the /eco slash command (.claude/commands/eco.md)
#   6. Validates server startup
#   7. Adds .memory/ to .gitignore
#   8. Reports extraction capabilities
#
# Usage:
#   bash "$(memctl scripts-path)/install_eco.sh" [OPTIONS]
#
# Options:
#   --db-root PATH    Where to store memory.db (default: .memory)
#   --dry-run         Show what would be done without making changes
#   --yes             Skip confirmation prompts
#   --force           Overwrite existing ECO.md and hooks without backup
#   -h, --help        Show this help
#
# Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

readonly SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly SCRIPT_DIR

# Templates live alongside scripts in the package (memctl/templates/eco/)
readonly ECO_TEMPLATES="${SCRIPT_DIR}/../templates/eco"
readonly DEFAULT_DB_ROOT=".memory"

# Target paths (project-local)
readonly CLAUDE_DIR=".claude"
readonly HOOKS_DIR="${CLAUDE_DIR}/hooks"
readonly ECO_DIR="${CLAUDE_DIR}/eco"
readonly COMMANDS_DIR="${CLAUDE_DIR}/commands"
readonly HOOK_FILE="${HOOKS_DIR}/eco-hint.sh"
readonly ECO_FILE="${ECO_DIR}/ECO.md"
readonly COMMAND_FILE="${COMMANDS_DIR}/eco.md"

# Claude Code settings
readonly SETTINGS_FILE="${CLAUDE_DIR}/settings.local.json"

# ---------------------------------------------------------------------------
# Colors (TTY-aware)
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_YELLOW=$'\033[0;33m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_CYAN=$'\033[0;36m'
    readonly C_BOLD=$'\033[1m'
    readonly C_DIM=$'\033[2m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_CYAN=""
    readonly C_BOLD="" C_DIM="" C_RESET=""
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf "%s[info]%s  %s\n" "$C_BLUE"   "$C_RESET" "$*"; }
ok()    { printf "%s[ok]%s    %s\n" "$C_GREEN"   "$C_RESET" "$*"; }
warn()  { printf "%s[warn]%s  %s\n" "$C_YELLOW"  "$C_RESET" "$*" >&2; }
fail()  { printf "%s[error]%s %s\n" "$C_RED"     "$C_RESET" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} $SCRIPT_NAME [OPTIONS]

One-shot installer for memctl eco mode.
Token-efficient file exploration and persistent memory for Claude Code.

${C_BOLD}Options:${C_RESET}
  --db-root PATH    Where to store memory.db (default: .memory)
  --dry-run         Show what would be done without making changes
  --yes             Skip confirmation prompts
  --force           Overwrite existing files without backup
  -h, --help        Show this help

${C_BOLD}Examples:${C_RESET}
  $SCRIPT_NAME                        # Install with defaults
  $SCRIPT_NAME --db-root .memory      # Explicit project-scoped DB
  $SCRIPT_NAME --dry-run              # Preview changes
  $SCRIPT_NAME --yes --force          # Non-interactive, overwrite
EOF
    exit 0
}

backup_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        local ts
        ts=$(date +%Y%m%d_%H%M%S)
        cp "$file" "${file}.bak.${ts}"
        info "Backup: ${file}.bak.${ts}"
    fi
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

DB_ROOT="$DEFAULT_DB_ROOT"
DRY_RUN=false
YES=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db-root)
            DB_ROOT="${2:?--db-root requires a path}"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --yes)
            YES=true
            shift
            ;;
        --force)
            FORCE=true
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

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

printf "\n%s%s  memctl eco mode — installer%s\n\n" "$C_BOLD" "$C_CYAN" "$C_RESET"

# ---------------------------------------------------------------------------
# Step 1: Verify memctl[mcp] is installed
# ---------------------------------------------------------------------------

info "Step 1/8: Checking prerequisites"

# Find Python
PYTHON_CMD=""
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PYTHON_CMD="${VIRTUAL_ENV}/bin/python"
fi
if [[ -z "$PYTHON_CMD" ]] || ! command -v "$PYTHON_CMD" &>/dev/null; then
    PYTHON_CMD="python3"
fi
if ! command -v "$PYTHON_CMD" &>/dev/null; then
    fail "python3 not found. Install Python 3.10+ first."
fi

# Check memctl is importable
if ! "$PYTHON_CMD" -c "import memctl" &>/dev/null; then
    fail "memctl is not installed. Run: pip install \"memctl[mcp]\""
fi

MEMCTL_VERSION=$("$PYTHON_CMD" -c "from memctl import __version__; print(__version__)")
ok "memctl $MEMCTL_VERSION"

# Check MCP dependencies
MCP_OK=false
if "$PYTHON_CMD" -c "import mcp" &>/dev/null; then
    MCP_OK=true
    ok "MCP dependencies available"
else
    warn "MCP package not installed. Run: pip install \"memctl[mcp]\""
    warn "eco mode requires MCP support."
fi

# Check template files exist
[[ -f "${ECO_TEMPLATES}/eco-hint.sh" ]] || fail "Template not found: ${ECO_TEMPLATES}/eco-hint.sh"
[[ -f "${ECO_TEMPLATES}/ECO.md" ]]      || fail "Template not found: ${ECO_TEMPLATES}/ECO.md"
[[ -f "${ECO_TEMPLATES}/eco.md" ]]      || fail "Template not found: ${ECO_TEMPLATES}/eco.md"

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

if [[ "$YES" == "false" && "$DRY_RUN" == "false" ]]; then
    printf "  This will:\n"
    printf "    1. Register memctl MCP server (db-root: %s)\n" "$DB_ROOT"
    printf "    2. Install eco hook at %s\n" "$HOOK_FILE"
    printf "    3. Install strategy file at %s\n" "$ECO_FILE"
    printf "    4. Install /eco slash command at %s\n" "$COMMAND_FILE"
    printf "    5. Add %s/ to .gitignore\n" "$DB_ROOT"
    printf "\n  Proceed? [y/N] "
    read -r answer
    [[ "$answer" =~ ^[Yy] ]] || { info "Aborted."; exit 0; }
fi

# ---------------------------------------------------------------------------
# Step 2: Register MCP server
# ---------------------------------------------------------------------------

info "Step 2/8: Registering MCP server"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would update: $SETTINGS_FILE"
    info "[dry-run]   mcpServers.memctl → memctl serve --db-root $DB_ROOT"
else
    "$PYTHON_CMD" -c "
import json, os, shutil, sys
from datetime import datetime

settings_path = sys.argv[1]
db_root = sys.argv[2]
force = sys.argv[3] == 'true'

# Read or create
if os.path.exists(settings_path):
    with open(settings_path, 'r', encoding='utf-8') as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}
    # Backup
    if not force:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = settings_path + '.bak.' + ts
        shutil.copy2(settings_path, backup)
        print(f'  Backup: {backup}')
else:
    config = {}
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)

# Insert/update memctl server entry
if 'mcpServers' not in config:
    config['mcpServers'] = {}

config['mcpServers']['memctl'] = {
    'command': 'memctl',
    'args': ['serve', '--db-root', db_root]
}

with open(settings_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'  Updated: {settings_path}')
" "$SETTINGS_FILE" "$DB_ROOT" "$FORCE"
    ok "MCP server registered (db-root: $DB_ROOT)"
fi

# ---------------------------------------------------------------------------
# Step 3: Install eco hook
# ---------------------------------------------------------------------------

info "Step 3/8: Installing eco hook"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would install: $HOOK_FILE"
    info "[dry-run] Would register UserPromptSubmit hook in $SETTINGS_FILE"
else
    mkdir -p "$HOOKS_DIR"

    # Backup existing hook
    if [[ -f "$HOOK_FILE" && "$FORCE" == "false" ]]; then
        backup_file "$HOOK_FILE"
    fi

    # Copy hook template
    cp "${ECO_TEMPLATES}/eco-hint.sh" "$HOOK_FILE"
    chmod +x "$HOOK_FILE"
    ok "Hook installed: $HOOK_FILE"

    # Register in settings.local.json
    "$PYTHON_CMD" -c "
import json, os, sys

settings_path = sys.argv[1]
hook_path = sys.argv[2]

# Read existing config
if os.path.exists(settings_path):
    with open(settings_path, 'r', encoding='utf-8') as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}
else:
    config = {}

# Ensure hooks structure
if 'hooks' not in config:
    config['hooks'] = {}

# UserPromptSubmit — add eco-hint, preserve existing hooks
hooks_list = config['hooks'].get('UserPromptSubmit', [])
eco_entry = {'hooks': [{'type': 'command', 'command': hook_path}]}
# Remove previous eco-hint entries (idempotent)
hooks_list = [e for e in hooks_list
              if 'eco-hint' not in json.dumps(e)]
hooks_list.append(eco_entry)
config['hooks']['UserPromptSubmit'] = hooks_list

with open(settings_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'  Hook registered in {settings_path}')
" "$SETTINGS_FILE" "$HOOK_FILE"
fi

# ---------------------------------------------------------------------------
# Step 4: Install ECO.md strategy file
# ---------------------------------------------------------------------------

info "Step 4/8: Installing strategy file"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would install: $ECO_FILE"
else
    mkdir -p "$ECO_DIR"

    # Backup existing
    if [[ -f "$ECO_FILE" && "$FORCE" == "false" ]]; then
        backup_file "$ECO_FILE"
    fi

    cp "${ECO_TEMPLATES}/ECO.md" "$ECO_FILE"
    ok "Strategy file installed: $ECO_FILE"
fi

# ---------------------------------------------------------------------------
# Step 5: Install /eco slash command
# ---------------------------------------------------------------------------

info "Step 5/8: Installing /eco slash command"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would install: $COMMAND_FILE"
else
    mkdir -p "$COMMANDS_DIR"

    # Backup existing
    if [[ -f "$COMMAND_FILE" && "$FORCE" == "false" ]]; then
        backup_file "$COMMAND_FILE"
    fi

    cp "${ECO_TEMPLATES}/eco.md" "$COMMAND_FILE"
    ok "Slash command installed: $COMMAND_FILE (/eco on|off|status)"
fi

# ---------------------------------------------------------------------------
# Step 6: Validate server
# ---------------------------------------------------------------------------

info "Step 6/8: Validating server"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would run: memctl serve --check --db-root $DB_ROOT"
elif [[ "$MCP_OK" == "true" ]]; then
    if "$PYTHON_CMD" -m memctl.cli serve --check --db-root "$DB_ROOT" 2>/dev/null; then
        ok "Server validation passed"
    else
        warn "Server check failed — MCP server may not start correctly"
    fi
else
    warn "Skipping validation (MCP package not installed)"
fi

# ---------------------------------------------------------------------------
# Step 6: Update .gitignore
# ---------------------------------------------------------------------------

info "Step 7/8: Checking .gitignore"

GITIGNORE_ENTRY="${DB_ROOT}/"

if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] Would add '$GITIGNORE_ENTRY' to .gitignore if absent"
else
    if [[ -f ".gitignore" ]]; then
        if grep -qF "$GITIGNORE_ENTRY" .gitignore 2>/dev/null; then
            ok ".gitignore already contains $GITIGNORE_ENTRY"
        else
            printf "\n# memctl eco mode — memory database\n%s\n" "$GITIGNORE_ENTRY" >> .gitignore
            ok "Added $GITIGNORE_ENTRY to .gitignore"
        fi
    else
        warn "No .gitignore found. Consider adding $GITIGNORE_ENTRY to prevent committing the database."
    fi
fi

# ---------------------------------------------------------------------------
# Step 7: Report extraction capabilities
# ---------------------------------------------------------------------------

info "Step 8/8: Checking extraction capabilities"

"$PYTHON_CMD" -c "
import sys

checks = [
    ('docx', 'python-docx',  'docx'),
    ('pdf',  'pdftotext',    None),
    ('xlsx', 'openpyxl',     'openpyxl'),
    ('pptx', 'python-pptx',  'pptx'),
    ('odt',  'stdlib',       None),
    ('ods',  'stdlib',       None),
    ('odp',  'stdlib',       None),
]

for ext, dep, module in checks:
    if dep == 'stdlib':
        status = '\033[0;32mOK (stdlib)\033[0m'
    elif dep == 'pdftotext':
        import shutil
        if shutil.which('pdftotext'):
            status = '\033[0;32mOK\033[0m'
        else:
            status = '\033[0;33mMISSING\033[0m (install poppler-utils)'
    else:
        try:
            __import__(module)
            status = '\033[0;32mOK\033[0m'
        except ImportError:
            status = '\033[0;33mMISSING\033[0m (pip install {})'.format(dep)

    print(f'  .{ext:5s} ({dep:12s}): {status}')
"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "\n%s%s  eco mode installed%s\n\n" "$C_BOLD" "$C_GREEN" "$C_RESET"
printf "  DB root:   %s\n" "$DB_ROOT"
printf "  Hook:      %s\n" "$HOOK_FILE"
printf "  Strategy:  %s\n" "$ECO_FILE"
printf "  Command:   %s (/eco on|off|status)\n" "$COMMAND_FILE"
printf "  Settings:  %s\n" "$SETTINGS_FILE"
printf "\n"

if [[ "$DRY_RUN" == "true" ]]; then
    printf "  %s[dry-run] No changes were made.%s\n\n" "$C_YELLOW" "$C_RESET"
else
    printf "  Restart Claude Code to activate eco mode.\n"
    printf "  Memory persists across sessions in %s/memory.db\n\n" "$DB_ROOT"
fi

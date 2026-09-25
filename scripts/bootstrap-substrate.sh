#!/usr/bin/env bash
# Substrate bootstrap — machine entry for the substrate manual's bring-up items
# (substrate-gate-suite, gate 4). The manual itself lives in the declaration
# repo (hdot123/infraro, docs/substrate-map.md); this script is the executable
# wrapper its bring-up items resolve to on a fresh engine checkout.
#
# Usage:
#   scripts/bootstrap-substrate.sh --dry-run   # verify, execute nothing
#   scripts/bootstrap-substrate.sh             # run bring-up verification
#   scripts/bootstrap-substrate.sh --verbose   # run with per-step logging
#
# Exit codes: 0 = bring-up path verified (or dry-run plan complete);
#             1 = a bring-up item failed; 2 = usage error.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"

DRY_RUN=false
VERBOSE=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [--dry-run] [--verbose] [--help]

Machine entry for the substrate manual bring-up items (gate 4).

Options:
  --dry-run   Print each bring-up item without executing it
  --verbose   Log every step
  --help      This help
EOF
}

log() { printf '[bootstrap] %s\n' "$*"; }

# Run one bring-up item. In dry-run mode the item is printed, not executed.
run_item() {
    local item="$1"
    local fn="$2"
    if $DRY_RUN; then
        log "DRY-RUN item '$item' -> $fn"
        return 0
    fi
    log "item '$item'"
    "$fn"
}

item_git_available() { git --version >/dev/null 2>&1; }
item_python_available() { python3 --version >/dev/null 2>&1; }
item_gates_present() {
    local count
    count=$(find "$REPO_ROOT/substrate/gates" -maxdepth 1 -name 'gate[0-9]*.py' | wc -l)
    [[ "$count" -ge 5 ]]
}
item_gates_compile() {
    local gate
    for gate in "$REPO_ROOT"/substrate/gates/gate[0-9]*.py; do
        python3 -m py_compile "$gate"
    done
}
item_registry_present() { [[ -f "$REPO_ROOT/substrate/gate0-exemptions.md" ]]; }

main() {
    local failed=0
    log "substrate bring-up (repo: $REPO_ROOT)"

    run_item "git-available" item_git_available || failed=1
    run_item "python-available" item_python_available || failed=1
    run_item "gate-scripts-present" item_gates_present || failed=1
    run_item "stock-registry-present" item_registry_present || failed=1
    run_item "gate-scripts-compile" item_gates_compile || failed=1

    if $DRY_RUN; then
        log "dry-run complete: no changes were made"
    elif [[ "$failed" -eq 0 ]]; then
        log "bring-up path verified: all items green"
    else
        log "bring-up FAILED: at least one item is red"
    fi
    return "$failed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --verbose) VERBOSE=true ;;
        -h | --help) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
    shift
done

if $VERBOSE; then
    log "verbose mode on"
fi
main

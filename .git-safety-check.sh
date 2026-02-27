#!/bin/bash
# Git Safety Check - Blocks forbidden git commands
# This script should be sourced or used as a wrapper

FORBIDDEN_PATTERNS=(
    "git reset --hard"
    "git reset --hard "
    "git push --force"
    "git push -f"
    "git stash"
    "git stash push"
    "git stash save"
    "git clean -f"
    "git checkout ."
    "git restore ."
)

check_command() {
    local cmd="$1"

    # Allow read-only git commands
    if [[ "$cmd" =~ ^git[[:space:]]+(status|diff|log|show|branch|remote) ]]; then
        return 0
    fi

    # Check for forbidden patterns
    for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
        if [[ "$cmd" == *"$pattern"* ]]; then
            echo "❌ BLOCKED: Forbidden git command detected" >&2
            echo "" >&2
            echo "Command: $cmd" >&2
            echo "Pattern: $pattern" >&2
            echo "" >&2
            echo "⚠️  Git operations MUST use ops agent:" >&2
            echo "   Task(subagent_type='ops', description='...', prompt='...')" >&2
            echo "" >&2
            echo "Why? Ops agent has safety checks that prevent data loss." >&2
            echo "See: CLAUDE.md line 195 and ops.md line 14" >&2
            return 1
        fi
    done

    # Warn about other git commands (not read-only)
    if [[ "$cmd" =~ ^git[[:space:]] ]]; then
        echo "⚠️  WARNING: Git command detected - should you delegate to ops agent?" >&2
        echo "Command: $cmd" >&2
        echo "" >&2
        read -p "Continue anyway? [y/N] " -n 1 -r >&2
        echo >&2
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi

    return 0
}

# If sourced, export the function
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    export -f check_command
fi

# If executed directly with command argument
if [[ "${BASH_SOURCE[0]}" == "${0}" ]] && [[ $# -gt 0 ]]; then
    check_command "$*"
    exit $?
fi

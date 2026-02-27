#!/bin/bash
# Git Audit - Check recent git history for forbidden commands
# Run this to audit Claude Code agent behavior

set -euo pipefail

echo "=== Git Safety Audit ==="
echo "Checking last 20 commits for forbidden git commands..."
echo ""

FORBIDDEN_PATTERNS=(
    "git reset --hard"
    "git push --force"
    "git push -f"
    "git clean -f"
    "git checkout ."
    "git restore ."
)

violations=0

# Check last 20 commit messages (excluding mentions in documentation)
while IFS= read -r commit_hash commit_subject; do
    # Skip commits that are obviously documenting forbidden commands
    if [[ "$commit_subject" =~ (FORBIDDEN|forbidden|Add.*to.*list|Document|Update.*ops) ]]; then
        continue
    fi

    for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
        if [[ "$commit_subject" == *"$pattern"* ]]; then
            ((violations++))
            echo "❌ VIOLATION in commit $commit_hash:"
            echo "   Subject: $commit_subject"
            echo "   Pattern: $pattern"
            echo ""
        fi
    done
done < <(git log -20 --pretty=format:"%h %s")

echo "=== Audit Complete ==="
if [[ $violations -eq 0 ]]; then
    echo "✅ No violations found in last 20 commits"
    echo ""
    echo "Note: This checks commit messages only. Actual bash commands"
    echo "executed by agents are not logged in git history."
    exit 0
else
    echo "❌ Found $violations violation(s)"
    echo ""
    echo "Reminder: ALL git operations must use ops agent"
    echo "See: CLAUDE.md line 195"
    exit 1
fi

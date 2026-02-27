# Git Safety Enforcement

This directory contains tools to enforce git safety rules for Claude Code agents.

## Problem

Claude Code agents (Sonnet, Opus, Haiku) can run git commands directly via the Bash tool, which bypasses safety checks. This can lead to:

- `git reset --hard` destroying uncommitted work
- `git stash` hiding work that gets forgotten
- `git push --force` overwriting remote history
- `git checkout .` discarding local changes

**All git operations should be delegated to the ops agent**, which has safety protocols.

## Solution

### 1. Git Safety Check Script

**File:** `../.git-safety-check.sh`

Blocks forbidden git commands before execution.

**Usage:**
```bash
# Test a command before running
./.git-safety-check.sh "git reset --hard"
# ❌ BLOCKED: Forbidden git command detected

./.git-safety-check.sh "git status"
# ✅ Allowed (read-only command)
```

**Blocked commands:**
- `git reset --hard` (destroys uncommitted work)
- `git push --force` / `git push -f` (overwrites remote)
- `git stash` / `git stash push` (hides work)
- `git clean -f` (deletes files)
- `git checkout .` / `git restore .` (discards changes)

**Detects command chaining:**
```bash
./.git-safety-check.sh "sleep 10 && git reset --hard"
# ❌ BLOCKED (catches chained commands)
```

**Allowed read-only commands:**
- `git status`
- `git diff`
- `git log`
- `git show`
- `git branch`
- `git remote`

**Other git commands:**
Shows a warning and prompts for confirmation (interactive mode).

### 2. Git Audit Script

**File:** `./git-audit.sh`

Audits recent git history for violations.

**Usage:**
```bash
./.claude/git-audit.sh

# ==> Git Safety Audit ===
# Checking last 20 commits for forbidden git commands...
# Checking recent bash history for forbidden git commands...
#
# ✅ No violations found
```

**What it checks:**
- Last 20 commit messages
- Last 100 bash history entries (if available)

**Exit codes:**
- `0`: No violations
- `1`: Violations found

### 3. CLAUDE.md Integration

**Location:** `../CLAUDE.md` (top of file)

Added critical safety section that appears first:
```markdown
## ⚠️ CRITICAL SAFETY RULES - READ FIRST

### Git Operations: MANDATORY DELEGATION

BEFORE running ANY git command, STOP and check:
...
```

## Workflow Integration

### For Claude Code Agents

**Before any git operation:**
1. ❌ Do NOT run git commands directly via Bash tool
2. ✅ Instead, delegate to ops agent:
   ```python
   Task(
       subagent_type="ops",
       description="commit and create PR",
       prompt="Commit current changes and create a PR"
   )
   ```

**Allowed exceptions (read-only):**
- `git status` - check working tree status
- `git diff` - view changes
- `git log` - view history
- `git show` - view specific commit

### For Users

**Periodic audit:**
```bash
# Check for violations
./.claude/git-audit.sh
```

**Manual command validation:**
```bash
# Before running a git command, check if it's safe
./.git-safety-check.sh "git <command>"
```

## Why This Matters

**Real incident (2026-02-20):**
1. Agent ran `git commit` directly (should have delegated)
2. Attempted `git push origin main` → rejected (branch protection)
3. Created branch, pushed successfully
4. Ran **`git reset --hard origin/main`** to "clean up" ← VIOLATION
5. This destroyed the local commit (fortunately it existed on the branch)

**Impact:** No data lost (commit was on remote branch), but violated safety protocol.

**Root cause:** Agent entered "implementation mode" and forgot to delegate.

## Prevention

1. **Prominent reminders** in CLAUDE.md (top of file, before workflow)
2. **Safety check script** blocks forbidden commands
3. **Audit script** catches violations after the fact
4. **Documentation** in ops.md (line 14-35) and CLAUDE.md (line 195+)

## Testing

```bash
# Test blocking
./.git-safety-check.sh "git reset --hard"
# Should show: ❌ BLOCKED

# Test chaining detection
./.git-safety-check.sh "echo hello && git stash"
# Should show: ❌ BLOCKED

# Test allowed command
./.git-safety-check.sh "git status"
# Should exit 0 (success)

# Run audit
./.claude/git-audit.sh
# Should check last 20 commits and bash history
```

## Future Enhancements

Potential improvements:
1. Integrate safety check into ops agent startup
2. Add pre-commit hook that validates commit messages
3. Create git aliases that enforce ops agent usage
4. Add telemetry to track delegation vs direct usage

## References

- **CLAUDE.md:** Line 1-40 (critical safety rules), line 227+ (ops delegation)
- **ops.md:** Line 14-35 (forbidden commands), line 472-482 (reminders)
- **Git safety check:** `../.git-safety-check.sh`
- **Git audit:** `./git-audit.sh`

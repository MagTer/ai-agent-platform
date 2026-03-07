Review current changes against main using Gemini 3.1 Pro before creating a PR. Gemini sees the full diff in one pass and checks for issues CI won't catch.

Spawn subagent_type="gemini-analyst" with this prompt:

---

Run from /home/magnus/dev/ai-agent-platform

Get the current diff against main and pipe it to Gemini for review:

```bash
git diff main | gemini -m gemini-3.1-pro-preview --yolo -p "You are reviewing a git diff before a PR is created. Analyze the changes below and report:

## Security
Vulnerabilities introduced: injection, XSS, SSRF, secrets in code, auth bypasses, unsafe subprocess calls.

## Correctness
Logic errors, missing error handling, off-by-one, race conditions, broken async patterns.

## Test Coverage
New code paths with no corresponding test. Missing edge case tests.

## Breaking Changes
API contract changes, DB schema changes without migrations, removed functionality, changed behavior.

## Code Quality
Violations of project standards: relative imports, use of Any, sync I/O in async context, missing type hints, complexity > 18.

## Style
Deviations from project conventions (see CLAUDE.md): wrong language (Swedish in code), emojis, smart quotes.

For each finding: file path, line number, severity (CRITICAL/HIGH/MEDIUM/LOW), and suggested fix.
If no issues found in a section, write: None found.

--- DIFF BELOW ---
\$(cat -)
"
```

Save output to .claude/diff-review.md and report a summary of findings.

Additional context: $ARGUMENTS

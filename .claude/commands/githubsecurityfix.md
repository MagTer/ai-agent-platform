Fetch all open code-scanning alerts from the GitHub Security tab using:

```
gh api "repos/{owner}/{repo}/code-scanning/alerts?per_page=100&state=open"
```

## Phase 1: Fetch and group alerts

Run the gh api command above to get all open alerts. Group them by severity:
- **HIGH / CRITICAL** alerts
- **MEDIUM** alerts
- **LOW** alerts

If there are zero alerts, report that and stop.

## Phase 2: Parallel analysis (cost-optimized)

Spawn up to 3 parallel architect agents (one per severity group that has alerts), each with run_in_background=true. Pass each agent only its group's alerts (rule ID, file path, line number, description).

**Model assignment:**
- **HIGH/CRITICAL group** [OPUS] -- use subagent_type="architect" (default Opus). Deep reasoning catches subtle security issues, multi-step exploit chains, and non-obvious fix strategies.
- **MEDIUM group** [SONNET] -- use subagent_type="architect" with model="sonnet". Known patterns with clear fixes.
- **LOW group** [SONNET] -- use subagent_type="architect" with model="sonnet". Straightforward remediation.

Each analysis agent must:
1. Read the source file at each flagged line to understand context
2. Determine the appropriate fix per alert:
   - **py/log-injection**: Sanitize user input before logging (replace newlines/control chars)
   - **py/stack-trace-exposure**: Return generic error messages to clients, log full traces server-side only
   - **py/clear-text-logging-sensitive-data**: Redact sensitive values (tokens, keys, credentials) before logging
   - **py/path-injection**: Validate and sanitize file paths, use allowlists or Path.resolve() with prefix checks
   - **py/incomplete-url-substring-sanitization**: Use proper URL parsing (urllib.parse) instead of substring checks
   - Other rules: Analyze and apply OWASP best practices
3. Output a structured fix plan: for each alert, the file path, line, current code, and proposed fix

## Phase 3: Implement fixes

After all analysis agents complete, consolidate their fix plans and spawn a single engineer agent (subagent_type="engineer", model="sonnet") to:

1. Create a new branch named `fix/codeql-security-alerts`
2. Implement all fixes from the consolidated plan
3. Run `./stack check` to verify nothing is broken
4. If checks pass, offer to commit and create a PR with a summary of all fixes

Use a single engineer to avoid file conflicts from parallel edits.

Additional context from user: $ARGUMENTS

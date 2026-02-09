Fetch all open code-scanning alerts from the GitHub Security tab using:

```
gh api "repos/{owner}/{repo}/code-scanning/alerts?per_page=100&state=open"
```

For each open alert, read the source file at the flagged line to understand the context.

Group alerts by rule type and severity (HIGH first, then MEDIUM, then LOW).

For each alert, determine the appropriate fix:
- **py/log-injection**: Sanitize user input before logging (replace newlines/control chars)
- **py/stack-trace-exposure**: Return generic error messages to clients, log full traces server-side only
- **py/clear-text-logging-sensitive-data**: Redact sensitive values (tokens, keys, credentials) before logging
- **py/path-injection**: Validate and sanitize file paths, use allowlists or Path.resolve() with prefix checks
- **py/incomplete-url-substring-sanitization**: Use proper URL parsing (urllib.parse) instead of substring checks
- Other rules: Analyze and apply OWASP best practices

Create a fix plan summarizing all changes, then:

1. Create a new branch named `fix/codeql-security-alerts`
2. Implement all fixes
3. Run `./stack check` to verify nothing is broken
4. If checks pass, offer to commit and create a PR with a summary of all fixes

Additional context from user: $ARGUMENTS

Use subagent_type="architect" which runs on Opus for the initial analysis and planning, then use subagent_type="engineer" to implement the fixes.

---
name: quality-check
description: Run the mandatory quality assurance script (code_check.py) before completing any code changes. Automatically triggered when finishing tasks involving code modifications, new features, bug fixes, or refactoring. Ensures all linting, formatting, type checking, and tests pass.
allowed-tools: Bash(python:*)
model: claude-sonnet-4-5-20250929
---

# Quality Assurance Workflow

## When This Skill Activates

You should use this skill:
- Before marking ANY task as complete that involved code changes
- After writing new code, editing existing code, or refactoring
- After adding new tests or modifying existing tests
- When the user explicitly asks to verify code quality
- Before creating a pull request or commit

## Critical Requirement

**You MUST run the quality check script before completing any code-related task:**

```bash
python scripts/code_check.py
```

This is **NON-NEGOTIABLE**. The user's GEMINI.md and project standards explicitly require this.

## What the Script Does

The `code_check.py` script runs (in order):

1. **Ruff** - Linting and auto-fixes (local mode)
2. **Black** - Code formatting (auto-format in local mode)
3. **Mypy** - Strict type checking
4. **Pytest** - Unit and integration tests
5. **Semantic Tests** - End-to-end tests (local only, skipped in CI)

## Workflow

### 1. Run the Quality Check

Always run from the repository root:

```bash
python scripts/code_check.py
```

### 2. If It Passes (Green Output)

- Mark your task as complete
- Inform the user that all quality checks passed
- Proceed with next steps (commit, PR, etc.)

### 3. If It Fails (Red Output)

**DO NOT mark the task as complete.** Instead:

1. **Read the error output carefully**
2. **Identify the issue:**
   - **Ruff errors:** Linting issues (unused imports, undefined variables, etc.)
   - **Black errors:** Formatting issues (should auto-fix in local mode)
   - **Mypy errors:** Type checking failures (missing type hints, `Any` usage, incompatible types)
   - **Pytest errors:** Test failures (assertion errors, exceptions, missing fixtures)

3. **Fix the errors:**
   - For Ruff: Fix the code issues highlighted
   - For Black: Should auto-fix, but verify formatting is correct
   - For Mypy: Add type hints, use strict types (`list[str]` not `List[str]`), eliminate `Any`
   - For Pytest: Fix failing tests, add missing test cases, update assertions

4. **Re-run the script:**
   ```bash
   python scripts/code_check.py
   ```

5. **Repeat until it passes**

## Common Issues and Solutions

### Mypy: Strict Typing

**Problem:** `error: Incompatible return value type (got "Any", expected "str")`

**Solution:**
- Never use `Any` type
- Use lowercase generics: `list[str]`, `dict[str, int]`, not `List[str]`, `Dict[str, int]`
- Add explicit type hints to all function signatures
- Use Protocol classes for interface definitions

**Example:**
```python
# Bad
def get_items() -> Any:
    return fetch_data()

# Good
def get_items() -> list[str]:
    return fetch_data()
```

### Ruff: Import and Code Quality

**Problem:** `F401 'module.Class' imported but unused`

**Solution:**
- Remove unused imports
- Use `__all__` to explicitly export symbols if needed
- Let Ruff auto-fix with `--fix` (already enabled in local mode)

### Pytest: Test Failures

**Problem:** `AssertionError: assert False`

**Solution:**
- Review the test logic and fix the implementation
- If the test is wrong, update the test
- If implementing new features, ensure tests are written first (TDD approach)
- Check that you're using the correct mocks (`MockLLMClient`, `InMemoryAsyncSession`)

### Black: Formatting

**Problem:** `would reformat file.py`

**Solution:**
- Black should auto-format in local mode
- If it fails, manually run: `python -m black .`
- Check for syntax errors that prevent formatting

## Critical Constraints

### DO NOT:
- Skip the quality check to "save time"
- Mark a task complete if code_check.py fails
- Set `CI=true` environment variable locally (breaks the script)
- Use `# noqa` comments without strong justification
- Disable type checking with `# type: ignore` without explanation

### DO:
- Run the full script, not individual tools
- Fix all errors before proceeding
- Ask the user if you encounter repeated failures
- Run the script from the repository root
- Trust the script's configuration (it's already optimized)

## Environment Notes

- The script auto-detects virtual environments and restarts via Poetry if needed
- It auto-installs missing dependencies
- It sets up PYTHONPATH correctly for imports
- Local mode enables auto-fix; CI mode only checks

## Success Criteria

The quality check is successful when:
- All output shows green checkmarks (✅)
- No red error messages (❌) appear
- The script exits with code 0
- You see: "All quality checks completed successfully."

## If You Get Stuck

If the quality check fails repeatedly:

1. Read the full error output
2. Identify which tool is failing (Ruff, Black, Mypy, Pytest)
3. Focus on fixing that specific tool's errors
4. Ask the user for guidance if the error is unclear
5. Consider running individual tools to isolate the issue:
   ```bash
   python -m ruff check .
   python -m mypy
   python -m pytest -v
   ```

## Remember

**"Code First, Verify Always."**

Running `python scripts/code_check.py` is not optional. It's the quality gate that ensures this production-grade platform maintains its standards.

---

**After running this skill:**
- Update your todo list to mark the quality check task as complete
- Only then mark the original task as complete
- Inform the user of the results

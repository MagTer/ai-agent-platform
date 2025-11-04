# Development Practices

## Tooling

- **Poetry** manages dependencies and virtual environments.
- **Code check script** (`poetry run python scripts/code_check.py`) runs Ruff `--fix`,
  Black (100-character profile), mypy, and pytest in the same order and with the same
  paths that CI uses. Ruff and Black apply fixes automatically; mypy and pytest halt
  the run on failure.
- **Pre-commit hooks** (`poetry run pre-commit install`) optionally wire the same tool
  chain into Git so formatting and typing run before each commit.

## Local Setup

1. Install Poetry >= 1.8 (`pip install poetry`).
2. Run `poetry install` from the repository root.
3. Activate the shell with `poetry shell` or prefix commands with `poetry run`.
4. Copy `.env` to `.env.local` if you need environment-specific overrides.

## Workflow

- Branch naming: `feature/<description>` for new features, `fix/<description>` for bug fixes.
- Open PRs with linked issues and include the CI badge results.
- Each commit should focus on a logical change set with passing tests.

## Dependency Maintenance

- Run `python scripts/deps_check.py` at least once per sprint (or before any
  release) to spot outdated dependencies. The script returns exit code `0`
  when everything is current, `1` for runtime updates, `2` for development
  updates, and `3` when both need attention.
- Follow up on any findings with `poetry update <package>` (or `poetry update`
  for wider refreshes). Capture release notes and breaking changes in the PR
  description so reviewers understand the impact.
- If the script exits with `4`, Poetry could not reach the registry. Retry with
  a working network connection before proceeding.
- CI runs the same script in the "Dependency freshness" job with `--quiet`.
  A non-zero exit code marks the job as failed; check the job logs locally with
  the full command to see the exact packages that need attention.

## Coding Standards

- Type annotate all functions and methods.
- Prefer small, pure functions and deterministic behaviour.
- Avoid global state; use dependency injection and FastAPI dependencies.
- Keep docstrings and documentation in sync with the implementation.
- Follow conventional commit messages where practical.

## Docker Compose

- `python -m stack up` ensures the stack is running; re-running the command is safe.
- `python -m stack down` stops containers; add `--remove-volumes` for a clean slate.
- Use `python -m stack logs` for quick debugging.

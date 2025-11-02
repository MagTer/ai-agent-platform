# Development Practices

## Tooling

- **Poetry** manages dependencies and virtual environments.
- **Ruff** enforces linting (`poetry run ruff check .`).
- **Black** formats code (`poetry run black .`).
- **Mypy** provides optional static typing (`poetry run mypy src`).
- **Pytest** executes the unit test suite (`poetry run pytest`).

## Local Setup

1. Install Poetry >= 1.8 (`pip install poetry`).
2. Run `poetry install` from the repository root.
3. Activate the shell with `poetry shell` or prefix commands with `poetry run`.
4. Copy `.env` to `.env.local` if you need environment-specific overrides.

## Workflow

- Branch naming: `feature/<description>` for new features, `fix/<description>` for bug fixes.
- Open PRs with linked issues and include the CI badge results.
- Each commit should focus on a logical change set with passing tests.

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

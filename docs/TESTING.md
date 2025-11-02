# Testing

## Overview
- Test suite uses `pytest` with Poetry-managed dependencies.
- Focus areas: configuration loading, service orchestration, tool behaviour, and stack CLI utilities.
- Ruff and mypy enforce formatting and typing; see `docs/architecture/04_dev_practices.md` for guidance.

## Running Tests
```bash
poetry run pytest -v
```

Run linting alongside tests for CI parity:
```bash
poetry run ruff check .
poetry run mypy src
```

## Coverage Expectations
- `src/agent/tests/` exercises configuration models, service logic, and tool interactions.
- `src/stack/tests/` covers CLI argument handling and Docker Compose helpers (use mocks to avoid shelling out).
- Stack status rendering and health aggregation live in `src/stack/tests/test_health.py`.
- Add regression tests when introducing new tools or capabilities; prefer pure functions with deterministic behaviour.

## Integration & Smoke Tests
After `python -m stack up`, execute the smoke commands listed in `docs/OPERATIONS.md` to validate the running containers. Capture command outputs in PR descriptions when relevant.

## Notes
- Tests should not reach the public internet or require GPUs; mock external services.
- Keep fixtures lightweight and reusable; share constants in `conftest.py` if multiple modules depend on them.
- When adding dependencies for testing, declare them under `[tool.poetry.group.dev.dependencies]` in `pyproject.toml`.

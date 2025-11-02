# Testing Overview

The agent platform relies on pytest for unit and service-level regression coverage. All
commands below assume you have installed the project dependencies with `poetry install`.

## Running the full suite

```bash
poetry run pytest -v
```

Pytest is configured to search the first-party test directories under `tests/`,
`src/agent/tests/`, and `src/stack/tests/`, so a single invocation exercises the
end-to-end checks that CI runs.

## Linting and static analysis

To mirror the CI gates, run the quality tooling before pushing changes:

```bash
poetry run ruff check .
poetry run black --check src tests fetcher indexer ragproxy embedder
poetry run mypy src
```

These commands validate code style, formatting, and typing for all first-party modules and
Python service entrypoints (such as the fetcher, indexer, and rag proxy).

## Integration and smoke expectations

The repository includes docker-compose resources that power local integration tests. After
starting the stack with `python -m stack up`, execute the smoke checks listed in
`docs/OPERATIONS.md` to confirm container health. When modifying Docker Compose files,
validate the rendered configuration with:

```bash
docker compose -f docker-compose.yml config
```

Record the output of any manual smoke tests in pull requests when relevant.

## Test authoring guidelines

- Keep fixtures light-weight and reusable; share common constants through `conftest.py`.
- Avoid hitting public networks or GPU resources; mock external services instead.
- Declare any additional testing dependencies under `[tool.poetry.group.dev.dependencies]`
  in `pyproject.toml`.
- Add regression tests alongside new features, especially for stack orchestration and tool
  integrations.

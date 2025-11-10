# Testing Overview

The agent platform relies on pytest for unit and service-level regression coverage. All
commands below assume you have installed the project dependencies with `poetry install`.
Review the [Contributing Guide](../contributing.md) before making changes so your local
testing aligns with the Codex compliance checklist.

## Running the full suite

Use the consolidated helper to match CI locally:

```bash
poetry run python scripts/code_check.py
```

The script executes the following in order:

1. `ruff check --fix .`
2. `black src tests fetcher indexer ragproxy embedder`
3. `mypy src`
4. `pytest`

Pytest is configured to search the first-party test directories under `tests/`,
`src/agent/tests/`, and `src/stack/tests/`, so a single invocation exercises the
end-to-end checks that CI runs. Ruff and Black apply fixes in-place so that rerunning the
script clears style violations automatically.

## Optional automations

Install the repository's pre-commit hooks to trigger the same linting, formatting, and
type-checking automatically on staged changes:

```bash
poetry run pre-commit install
```

Pre-commit runs Ruff with `--fix`, Black with the 100-character profile, and mypy against
`src/`, matching the paths that CI enforces.

## Integration and smoke expectations

The repository includes docker-compose resources that power local integration tests. After
starting the stack with `python -m stack up`, execute the smoke checks listed in
`docs/OPERATIONS.md` to confirm container health. When modifying Docker Compose files,
validate the rendered configuration with:

```bash
docker compose -f docker-compose.yml config
```

Docker Compose already configures Ollama to use the NVIDIA runtime, so the
default `docker-compose.yml` works even on GPU hosts. Record the output of any
manual smoke tests in pull requests when relevant.

## Test authoring guidelines

- Keep fixtures light-weight and reusable; share common constants through `conftest.py`.
- Avoid hitting public networks or GPU resources; mock external services instead.
- Declare any additional testing dependencies under `[tool.poetry.group.dev.dependencies]`
  in `pyproject.toml`.
- Add regression tests alongside new features, especially for stack orchestration and tool
  integrations.

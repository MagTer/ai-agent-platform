# Contributing Guide (Codex)

This guide captures the workflow expectations for AI coding assistants working on the
AI Agent Platform. Follow it for every change, even when a human reviewer is not in the
loop.

## AI coding rules
- **Framework syntax**: Write FastAPI and Pydantic code using the v2 APIs exclusively.
  Avoid deprecated v1 signatures such as `BaseModel.Config` or `@app.on_event`.
- **Python style**: Honour the repository toolchain (Ruff, Black, mypy) and the
  conventions described in [`docs/STYLE.md`](./STYLE.md).
- **Deterministic changes**: Keep commands, scripts, and generated artefacts idempotent.
  Document any non-deterministic behaviour in the relevant runbook.
- **Documentation updates**: Synchronise `docs/` with behavioural changes. The delivery
  and operations guides must reflect any new workflows, dependencies, or runtime
  requirements introduced by your change.

## Required local checks
Run these commands before committing. The PR template requires you to confirm each one.

```bash
poetry install
./stack check
```

The consolidated helper (`./stack check`) executes Ruff, Black, mypy, and pytest in sequence.
Re-run it until all issues are fixed. When iterating on type-only changes, run
`./stack typecheck` and `./stack test` individually to ensure the fixes hold without the
auto-formatters. Use `./stack lint` for a fast lint-and-format pass.

## Dependency and documentation hygiene
- **Dependencies**: Update `pyproject.toml` and regenerate `poetry.lock` when adding or
  upgrading packages. Record the rationale in your PR description. If you remove
  dependencies, clean up import paths and tooling configuration as part of the same change.
- **Documentation**: Update the affected files under `docs/` (including runbooks and
  workflow guides) whenever the behaviour changes. Link new documents from the
  documentation index so future contributors can find them.
- **Tooling scripts**: When modifying developer scripts (for example, in `scripts/`),
  ensure the usage is documented in this guide or the relevant runbook.

## Pull request expectations
- Use the Codex-specific PR template and mark every checklist item. The checklist is a
  gate—do not request review until all boxes are ticked.
- Summarise the change, the tests you ran, and any follow-up tasks for human reviewers.
- Include links to updated documentation or diagrams so reviewers can verify the context
  quickly.

# Delivery Model — How We Work

## Cadence
- Deliver in 2–3 hour increments that produce runnable artefacts, updated docs, and automated checks.
- Use focused PRs and run `poetry run python scripts/code_check.py` (Ruff fix, Black, mypy, pytest) before pushing; surface the key `poetry run ruff check .` and `poetry run pytest -v` results in the description.

## Definition of Done (DoD)
- Stack CLI commands (`python -m stack up/down/status`) succeed twice consecutively.
- `.env.template` reflects any new configuration keys consumed by the agent or stack CLI.
- Smoke tests in `docs/OPERATIONS.md` pass and are updated when behaviour changes.
- Documentation and capability catalog entries match the implemented features.
- CI workflow (`.github/workflows/ci.yml`) passes lint and test jobs.

## Acceptance Criteria
- Outcome-first: articulate what the user gains after the change (e.g., "Agent can call filesystem summariser").
- Tests: unit/integration coverage proves the behaviour; include CLI smoke commands if the change is operational.
- Documentation: update both the relevant architecture doc and high-level summaries.

## Tooling Conventions
- Poetry manages dependencies; update `pyproject.toml` and run `poetry lock` when adding packages.
- Formatting via `ruff` and `black`; typing enforced with `mypy` as referenced in `docs/architecture/04_dev_practices.md`.
- Stack CLI logic lives in `src/stack/`; prefer pure functions and unit tests under `src/stack/tests/`.
- Avoid PowerShell scripts for orchestration; use Python modules or documented CLI commands instead.

## Branches & Labels
- Branch naming: `feature/*`, `fix/*`, `chore/*` depending on scope.
- Apply labels such as `agent`, `stack-cli`, `docs`, `infra`, or `bugfix` to communicate impact.

## Review Checklist
- Costs understood: default to local inference; document any external API spend.
- Secrets handled via `.env` loading and not committed to git.
- Health checks, logs, and smoke tests described for new services or tools.
- Capability catalog and roadmap updated if the change alters user-facing functionality.

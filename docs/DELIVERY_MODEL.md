# Delivery Model — How We Work

## Cadence
- Work in 2-3 hour iterations (MVP steps), each producing a runnable artifact or verifiable result.
- Use small PRs with a smoke test (curl/health/logs) in the PR description.

## Definition of Done (DoD)
- Code/config runs locally without manual edits.
- `compose/.env.template` updated for any new variables.
- Smoke tests documented (copy/paste commands).
- Reset/rollback documented (scripts or notes).
- Security respected (no secrets in repo).

## Acceptance Criteria (per step)
- Outcome-driven: “After this step we can X”.
- Exit criteria: described and testable.
- Docs updated: README / OPERATIONS / RUNBOOKS as needed.

## File/Script Conventions
- PowerShell scripts: ASCII-only, `Push-Location/Pop-Location`, idempotent.
- Compose: validate with `docker compose config`.
- Keep scripts to run, not to generate files (files are committed).

## Branches & Labels
- Branches: `feat/*`, `fix/*`, `chore/*`.
- Labels: `mvp-step`, `docs`, `infra`, `bug`.

## Review Checklist
- Cost impact understood (default to local models).
- Secrets handled via `compose/.env` / credential stores.
- Health checks & logs easy to access (scripts included).
- The capability (not just component) is clearly improved.


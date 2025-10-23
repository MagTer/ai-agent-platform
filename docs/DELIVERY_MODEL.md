# Delivery Model — How We Work

## Cadence
- Work in **2–3 hour iterations** (“MVP steps”), each resulting in a runnable artifact or verifiable result.
- Use **small PRs** with a smoke test (curl/health/logs) in the PR description.

## Definition of Done (DoD)
- Code/config runs locally without manual edits
- `.env.template` updated for any new variables
- Tests or smoke checks documented (copy-paste commands)
- Rollback/reset documented (scripts or notes)
- Security respected (no secrets in repo)

## Acceptance Criteria (per step)
- **Outcome-driven**: “After this step we can *X*”
- **Exit criteria**: described and testable
- **Docs updated**: README / OPERATIONS / RUNBOOKS as needed

## File/Script Conventions
- PowerShell scripts: ASCII-only, `Push-Location/Pop-Location`, idempotent
- Compose: validate with `docker compose config`
- Keep **scripts to run**, not to generate code files

## Labels & Branches
- Branches: `feat/*`, `fix/*`, `chore/*`
- Labels: `mvp-step`, `docs`, `infra`, `bug`

## Review Checklist
- Does this reduce cost or at least not increase it inadvertently?
- Are secrets handled via `.env`/credentials?
- Are health checks and logs easy to read (scripts/log commands included)?
- Is the **capability** (not just component) clearly improved?


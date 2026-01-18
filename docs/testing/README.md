# Testing Documentation

This directory contains the testing runbooks that agents and humans should consult before running or modifying the CI pipeline.

- [`00_overview.md`](./00_overview.md) – local testing guidance, the `./stack check` command, and optional pre-commit hooks.
- [`01_ci.md`](./01_ci.md) – GitHub Actions job breakdown, quality gate checklist, and Docker Compose validation step.

## Testing Commands

**DO NOT** run `pytest`, `ruff`, or `poetry run` manually. These commands will fail due to missing environment variables and path configurations.

**Always** use the unified quality check script:

```bash
./stack check
```

**Available commands:**
- `./stack check` - Full check (lint, format, typecheck, test)
- `./stack lint` - Ruff + Black only (fast)
- `./stack typecheck` - Mypy only
- `./stack test` - Pytest only
- `./stack check --no-fix` - CI mode (no auto-fix)

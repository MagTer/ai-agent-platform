## Summary
- 

## Testing
- [ ] `poetry run ruff check .`
- [ ] `poetry run black --check src tests fetcher indexer ragproxy embedder scripts`
- [ ] `poetry run pytest`
- [ ] `poetry run python scripts/deps_check.py --quiet`

> **Note:** Contributors (including Codex) must run and pass all of the above checks locally before marking the task as done.

## Codex compliance
- [ ] I followed the AI coding rules in [docs/contributing.md](../docs/contributing.md) (FastAPI/Pydantic v2 syntax, repo tooling, deterministic outputs).
- [ ] I updated documentation, runbooks, and capability references for user-facing changes.
- [ ] I captured dependency changes in `pyproject.toml`/`poetry.lock` and documented the impact.
- [ ] I linked relevant documentation updates or diagrams in this PR description.

## Additional context
- 

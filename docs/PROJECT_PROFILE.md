# Project Profile — AI Agent Platform

## Vision
Deliver a local, containerised agent platform with actionable skills, transparent operations, and modern Python tooling.

## Persona & Response Style
- Role: Senior assistant and advisor for IT product ownership (security, operations, AI).
- Language: English only for documentation, code, and chat responses.
- Style: Lead with concise answers; expand when requested. Mark uncertainty with `(assumption)`.
- Delivery: Provide complete outputs in each reply—no hidden steps.

## Non-Functional Constraints
- Cost: Prefer local LLMs through LiteLLM ➜ Ollama; document any premium usage.
- Security: Secrets remain in `.env`; avoid committing credentials or tokens.
- Portability: Docker Compose orchestrates services; optional cloud packaging comes later.
- Observability: Health checks and structured logs exposed by the agent and stack CLI.
- Recoverability: Persistent volumes for models/data plus documented backup strategies.

## Current Architecture (Python Core)
- **Client**: Open WebUI (Reasoning, Research, Actions presets) configured to call the FastAPI agent.
- **Agent**: FastAPI service (`src/agent/`) managing prompts, conversation state (SQLite), and tool execution.
- **LLM Gateway**: LiteLLM proxies to Ollama-hosted Qwen 2.5 models and optional remote providers.
- **Memory**: Qdrant stores embeddings; `config/tools.yaml` registers memory-aware tools such as `web_fetch`.
- **Stack Management**: Typer-based CLI (`python -m stack`) handles Compose lifecycle, health checks, and logs.

## Outcome-Oriented View
- Conversational completions with optional memory/tool metadata.
- Research flows using the web_fetch tool and Qdrant context.
- Documented roadmap for expanding tools (filesystem, calendar, Git, etc.).

## Onboarding Checklist
1. Read `docs/README.md` for documentation map and working agreements, then review the
   [Contributing Guide](./contributing.md) for Codex-specific rules and required checks.
2. Install Poetry and run `poetry install` to set up the virtual environment.
3. Copy `.env.template` ➜ `.env`, fill secrets, and run `python -m stack up`.
4. Execute lint (`poetry run ruff check .`) and tests (`poetry run pytest -v`) before submitting changes.
5. Update docs and capability catalog to mirror behaviour adjustments.

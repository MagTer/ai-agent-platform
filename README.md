# AI Agent Platform (Local, Containerized)

> **For AI assistants (Codex) — Start here.**
> The documentation index under [`/docs`](./docs) is your navigation hub: start at `docs/README.md`, then follow its links to detailed references.

## Overview

This repository contains a local-optimised AI Agent Platform running on Docker. It orchestrates a suite of services including:

-   **Agent**: FastAPI server with LiteLLM, Qdrant memory, and tool execution.
-   **Open WebUI**: User interface for chat and agent interaction.
-   **Services**: LiteLLM (OpenRouter gateway), Qdrant (vector DB), SearXNG (search), and more.

## Quick Links

-   **[Getting Started](./docs/getting_started.md)**: Steps to launch the stack locally.
-   **[Development Guide](./docs/development.md)**: How to contribute, add skills, and run checks.
-   **[Architecture](./docs/architecture/README.md)**: detailed system topology and service map.
-   **[Operations](./docs/OPERATIONS.md)**: Runbooks and maintenance.

## Key Features

-   **Python-based Orchestration**: A `stack` CLI wraps Docker Compose for easy management.
-   **Modular Skills**: Add capabilities via simple Markdown definitions.
-   **Integrated RAG**: Built-in retrieval pipeline with ingestion and re-ranking.
-   **Observability**: OpenTelemetry tracing and structured logging.

## Pre-requisites

-   Docker & Docker Compose
-   Python 3.11+
-   Poetry (for dependency management)

## Getting Started

```bash
# Quick launch
cp .env.template .env
poetry install
python -m stack up
```

See [Getting Started](./docs/getting_started.md) for full instructions.
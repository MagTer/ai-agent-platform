# Refactoring Notes: Unified Monorepo & Gemini CLI

## Overview

The repository has been refactored into a unified monorepo structure to better organize services and support new capabilities like the Gemini Advanced CLI integration.

## Structure Changes

All service components are now located in the `services/` directory:

- **`services/agent`**: The core Python application (FastAPI, Orchestrator).
  - Contains `src/`, `Dockerfile`, and `pyproject.toml`.
  - **`services/agent/src/modules/`**: Now contains consolidated capabilities:
      - `embedder`: Sentence-transformer logic.
      - `webfetch`: Web-fetch/scraping logic.
      - `ragproxy`: RAG proxy logic.
      - `context7`: Context management logic.
- **`services/litellm`**, **`services/qdrant`**, etc.: Configuration directories for supporting services.

## Gemini CLI Integration

The Agent container now includes the `@google/gemini-cli` Node.js tool, allowing the platform to leverage Google Gemini Advanced features (if a subscription is active).

### Authentication

To use the Gemini CLI, you must provide a valid Google API key.

1.  **Environment Variable**: Set `GOOGLE_API_KEY` in your `.env` file.
    ```bash
    GOOGLE_API_KEY=your_key_here
    ```
    The CLI will automatically detect this variable.

2.  **Manual Login (Dev Mode)**:
    If running locally or debugging, you can authenticate via the browser:
    ```bash
    gemini login
    ```
    *Note: In a headless container environment, using the API key is the recommended approach.*

### Python Adapter

A new adapter class `GeminiCLIModel` is available in `services/agent/src/core/models/gemini_cli.py`.

```python
from core.models.gemini_cli import GeminiCLIModel

model = GeminiCLIModel()
response = model.generate_content("Explain quantum computing")
print(response)
```

## Docker Updates

The `agent` service in `docker-compose.yml` now builds from `./services/agent`. Ensure you run:

```bash
python -m stack up --build
```
(or `docker-compose up --build`) to regenerate the images with the new directory structure and Node.js dependencies.

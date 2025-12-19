# Getting Started (Local)

## Quick Start

1.  **Copy the environment template and customize**
    ```bash
    cp .env.template .env
    ```
    > **Note**: The Docker Compose definition configures Ollama to use the NVIDIA runtime by default. Adjust `OLLAMA_VISIBLE_DEVICES` or `OLLAMA_DRIVER_CAPABILITIES` in `.env` if needed.

2.  **Install Python dependencies**
    ```bash
    poetry install
    ```

3.  **Launch the stack (idempotent)**
    ```bash
    python -m stack up
    ```

4.  **Check container status**
    ```bash
    python -m stack status
    ```

5.  **Access the UI**
    Open [http://localhost:3000](http://localhost:3000) for Open WebUI. The UI is connected to the agent service, enabling full reasoning traces.
    You can also access the API directly at [http://localhost:8000/v1/agent](http://localhost:8000/v1/agent).

> **Platform Note**: Windows users should run these commands inside a Poetry shell (`poetry shell`). Linux/macOS users can run them directly if poetry is in the path.

## Stack CLI Summary

The platform uses a Python-based orchestration CLI (`stack`) that wraps Docker Compose and provides enhanced status reporting.

| Command | Description |
|---------|-------------|
| `poetry run stack up` | Start or restart the stack in detached mode. |
| `poetry run stack down` | Stop the stack (add `--remove-volumes` to purge data). |
| `poetry run stack status` | Render container status and health checks. |
| `poetry run stack logs [service]` | Tail logs for a specific service (e.g., `openwebui`). |
| `poetry run stack health` | Probe service health endpoints. |

For a full list of automation utilities, referring to the running `poetry run stack --help`.

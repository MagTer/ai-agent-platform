FROM python:3.11-slim

ENV POETRY_VERSION=1.8.2 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

ENV PYTHONPATH="/app/src:${PYTHONPATH}"

# Install Node.js and npm (required for Gemini CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential git ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && npm install -g @google/gemini-cli@latest \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN poetry install --no-root --only main

COPY src ./src

EXPOSE 8000

CMD ["uvicorn", "agent.core.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
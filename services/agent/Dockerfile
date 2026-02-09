FROM python:3.11-slim

# Build argument to optionally include Node.js and Gemini CLI
# Set to "true" to enable Node.js installation (adds ~350MB to image)
ARG INCLUDE_NODEJS=false

ENV POETRY_VERSION=1.8.2 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHONPATH="/app/src"

# Install system dependencies and Poetry
# Split into two layers: build dependencies (removed) and runtime dependencies (kept)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates gnupg \
    # Build dependencies (will be removed after Poetry installation)
    build-essential \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry \
    # Conditionally install Node.js if INCLUDE_NODEJS=true
    && if [ "$INCLUDE_NODEJS" = "true" ]; then \
        mkdir -p /etc/apt/keyrings \
        && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
        && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
        && apt-get update && apt-get install -y nodejs \
        && npm install -g @google/gemini-cli@latest; \
    fi \
    # Remove build dependencies to reduce image size
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml poetry.lock ./
COPY alembic.ini ./
COPY alembic ./alembic

# Upgrade pip and wheel to fix base image CVEs
# pip: CVE-2025-8869 (symlink extraction), CVE-2026-1703 (path traversal in wheels)
# wheel: CVE-2026-24049
RUN pip install --no-cache-dir --upgrade "pip>=26.0" "wheel>=0.46.2"

# Use --no-cache to reduce image size
# Remove Poetry after install - it's only needed as a build tool, not at runtime.
# This also eliminates HIGH CVEs in Poetry's vendored deps (jaraco.context, wheel).
RUN poetry install --no-root --only main --no-cache \
    && rm -rf /opt/poetry /usr/local/bin/poetry

COPY src ./src
COPY entrypoint.sh ./entrypoint.sh

# Create non-root user for security (container should not run as root)
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser \
    && chown -R appuser:appgroup /app \
    && chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "interfaces.http.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory", "--proxy-headers", "--forwarded-allow-ips", "*"]

#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL..."

# Extract host and port from POSTGRES_URL
# Format: postgresql+asyncpg://user:pass@host:port/dbname
if [[ -n "$POSTGRES_URL" ]]; then
    # Extract host:port from URL
    DB_HOST=$(echo "$POSTGRES_URL" | sed -E 's|.*@([^:/]+):?([0-9]*)/.*|\1|')
    DB_PORT=$(echo "$POSTGRES_URL" | sed -E 's|.*@[^:/]+:([0-9]+)/.*|\1|')
    DB_PORT=${DB_PORT:-5432}

    # Wait for database to be reachable (max 30 seconds)
    for i in {1..30}; do
        if timeout 1 bash -c "cat < /dev/null > /dev/tcp/$DB_HOST/$DB_PORT" 2>/dev/null; then
            echo "PostgreSQL is available at $DB_HOST:$DB_PORT"
            break
        fi
        echo "Waiting for PostgreSQL at $DB_HOST:$DB_PORT... ($i/30)"
        sleep 1
    done
fi

# Run database migrations
echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

# Execute the main command (uvicorn)
echo "Starting application..."
exec "$@"

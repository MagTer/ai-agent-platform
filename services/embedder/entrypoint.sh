#!/bin/sh
set -e

# Define cache paths
CACHE_DIR="/root/.cache"
PRESEED_DIR="/model_cache"

# Ensure destination directory exists
mkdir -p "$CACHE_DIR"

# Copy pre-seeded models if cache is empty
if [ -d "$PRESEED_DIR" ] && [ -z "$(ls -A $CACHE_DIR)" ]; then
    echo "Seeding cache from image..."
    cp -rn "$PRESEED_DIR"/* "$CACHE_DIR/" || true
    echo "Cache seeded."
fi

# Execute the main command
exec "$@"

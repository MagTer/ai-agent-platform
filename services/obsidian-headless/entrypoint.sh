#!/bin/sh
set -e

if [ -z "$OBSIDIAN_AUTH_TOKEN" ]; then
    echo "ERROR: OBSIDIAN_AUTH_TOKEN is not set. Run 'npx obsidian-headless@latest ob login' to obtain a token." >&2
    exit 1
fi

exec ob sync --continuous

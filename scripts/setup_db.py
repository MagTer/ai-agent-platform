#!/usr/bin/env python3
"""
Database Setup Script

This script initializes the database schema by running Alembic migrations.
It is intended to be run locally (outside Docker) to prepare the database
exposed on localhost:5432.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_SERVICE_DIR = REPO_ROOT / "services" / "agent"
DEFAULT_LOCAL_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db"

# Styling
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_info(msg: str) -> None:
    print(f"{BLUE}ℹ️  {msg}{RESET}")


def print_success(msg: str) -> None:
    print(f"{GREEN}✅ {msg}{RESET}")


def print_error(msg: str) -> None:
    print(f"{RED}❌ {msg}{RESET}")


def main() -> None:
    print(f"\n{BLUE}🚀 Starting Database Setup...{RESET}")

    # 1. Ensure Poetry is available to run commands in the venv
    poetry_bin = shutil.which("poetry")
    if not poetry_bin:
        print_error("Poetry executable not found.")
        print_error("Please install Poetry first.")
        sys.exit(1)

    # 2. Check if Docker Postgres is likely up (simple check)
    # We won't block strictly, but it's good to know.

    # 3. Configure Environment
    # We need to override POSTGRES_URL to point to localhost because
    # we are running this script from the host machine, distinct from the
    # container network name 'postgres'.
    env = os.environ.copy()

    # If the user hasn't explicitly set a custom URL, default to localhost
    if "POSTGRES_URL" not in env:
        print_info(f"Setting POSTGRES_URL to {DEFAULT_LOCAL_DB_URL}")
        env["POSTGRES_URL"] = DEFAULT_LOCAL_DB_URL
    else:
        print_info(f"Using existing POSTGRES_URL: {env['POSTGRES_URL']}")

    # 4. Run Alembic Upgrade
    print_info("Running 'alembic upgrade head'...")

    cmd = [poetry_bin, "run", "alembic", "upgrade", "head"]

    try:
        # Run inside services/agent where alembic.ini is usually located
        # or where the python package structure expects it.
        # Based on file listing, alembic.ini is in services/agent.
        subprocess.run(cmd, cwd=AGENT_SERVICE_DIR, env=env, check=True)  # noqa: S603

        print_success("Database migrations applied successfully.")

    except subprocess.CalledProcessError as e:
        print_error(f"Migration failed with exit code {e.returncode}.")
        print_error("Ensure that:")
        print_error("1. The Postgres container is running (docker compose up -d postgres).")
        print_error("2. The database 'agent_db' exists (default image creates it).")
        print_error("3. You have the correct credentials.")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()

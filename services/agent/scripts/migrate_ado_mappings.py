#!/usr/bin/env python3
"""Migrate ADO team mappings from ado_mappings.yaml to the ado_team_configs database table.

Usage:
    python scripts/migrate_ado_mappings.py [--dry-run]

Run inside the agent container:
    docker exec ai-agent-platform-dev-agent-1 python /app/scripts/migrate_ado_mappings.py
    docker exec ai-agent-platform-dev-agent-1 python /app/scripts/migrate_ado_mappings.py --dry-run
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Locate ado_mappings.yaml
# ---------------------------------------------------------------------------

YAML_CANDIDATES = [
    Path("/app/config/ado_mappings.yaml"),  # Docker mount
    Path(__file__).resolve().parent.parent / "config" / "ado_mappings.yaml",
]


def load_yaml() -> dict:
    for candidate in YAML_CANDIDATES:
        if candidate.exists():
            print(f"Loading: {candidate}")
            with open(candidate, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    print("ERROR: ado_mappings.yaml not found in any of:")
    for p in YAML_CANDIDATES:
        print(f"  {p}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------


async def run_migration(dry_run: bool) -> None:
    # Import here so PYTHONPATH must include src/
    from sqlalchemy import select

    from core.db.engine import AsyncSessionLocal
    from core.db.models import AdoTeamConfig

    data = load_yaml()
    defaults = data.get("defaults", {})
    teams: dict = data.get("teams", {})

    print("\n=== ADO Mappings Migration ===")
    print(f"Dry run: {dry_run}")
    print("\nGlobal defaults:")
    print(f"  area_path    = {defaults.get('area_path', '(not set)')}")
    print(f"  default_type = {defaults.get('default_type', '(not set)')}")
    print(f"\nTeams to migrate ({len(teams)}):")
    for i, (alias, cfg) in enumerate(teams.items()):
        tags = cfg.get("default_tags", [])
        print(
            f"  [{i:2d}] {alias:<20} "
            f"area={cfg.get('area_path', '')!r:<45} "
            f"type={cfg.get('default_type', ''):<15} "
            f"tags={tags}"
        )

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return

    # Confirm
    answer = input("\nProceed with migration? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return

    async with AsyncSessionLocal() as session:
        # Check if data already exists
        existing_count_result = await session.execute(select(AdoTeamConfig))
        existing_rows = existing_count_result.scalars().all()
        if existing_rows:
            print(f"\nWARNING: ado_team_configs already has {len(existing_rows)} row(s).")
            overwrite = input("Overwrite existing data? [y/N] ").strip().lower()
            if overwrite != "y":
                print("Aborted.")
                return
            for row in existing_rows:
                await session.delete(row)
            await session.flush()
            print(f"Deleted {len(existing_rows)} existing rows.")

        inserted = 0

        # Insert global defaults row
        if defaults.get("area_path") and defaults.get("default_type"):
            defaults_row = AdoTeamConfig(
                id=uuid.uuid4(),
                alias=None,
                display_name=None,
                area_path=defaults["area_path"],
                owner=None,
                default_type=defaults["default_type"],
                default_tags=[],
                is_default=True,
                sort_order=0,
            )
            session.add(defaults_row)
            inserted += 1
            print("  + Global defaults row inserted")
        else:
            print("  ! Skipping defaults row (area_path or default_type missing)")

        # Insert team rows
        for sort_order, (alias, cfg) in enumerate(teams.items()):
            area_path = cfg.get("area_path", "")
            default_type = cfg.get("default_type", "User Story")
            if not area_path:
                print(f"  ! Skipping team '{alias}' (no area_path)")
                continue

            team_row = AdoTeamConfig(
                id=uuid.uuid4(),
                alias=alias,
                display_name=cfg.get("display_name"),
                area_path=area_path,
                owner=cfg.get("owner"),
                default_type=default_type,
                default_tags=cfg.get("default_tags", []),
                is_default=False,
                sort_order=sort_order,
            )
            session.add(team_row)
            inserted += 1
            print(f"  + Team '{alias}' inserted (sort_order={sort_order})")

        await session.commit()
        print(f"\nMigration complete: {inserted} rows inserted.")

        # Verify read-back
        verify_result = await session.execute(
            select(AdoTeamConfig).order_by(AdoTeamConfig.sort_order, AdoTeamConfig.alias)
        )
        verify_rows = verify_result.scalars().all()
        print(f"\nVerification: {len(verify_rows)} rows in ado_team_configs:")
        for row in verify_rows:
            label = "[defaults]" if row.is_default else f"[{row.alias}]"
            print(f"  {label:<25} area={row.area_path!r:<45} type={row.default_type}")

    print(
        "\nNext steps:"
        "\n  1. Verify teams appear in Admin Portal -> ADO Config"
        "\n  2. Test a real ADO request through the agent"
        "\n  3. Delete services/agent/config/ado_mappings.yaml once confirmed"
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    asyncio.run(run_migration(dry_run=dry_run))


if __name__ == "__main__":
    main()

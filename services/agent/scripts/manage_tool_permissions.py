"""Manage tool permissions for contexts.

This script allows you to:
- List all available tools
- View current permissions for a context
- Grant/revoke tool access for a context
- Reset permissions (allow all) for a context
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import AsyncSessionLocal
from core.db.models import Context, ToolPermission
from core.runtime.config import get_settings
from core.tools.loader import load_tool_registry


async def list_available_tools() -> list[str]:
    """Get list of all available tools."""
    settings = get_settings()
    registry = load_tool_registry(settings.tools_config_path)
    return sorted(registry.available())


async def list_contexts(session: AsyncSession) -> list[tuple[UUID, str]]:
    """Get list of all contexts."""
    stmt = select(Context)
    result = await session.execute(stmt)
    contexts = result.scalars().all()
    return [(ctx.id, ctx.name) for ctx in contexts]


async def get_context_permissions(session: AsyncSession, context_id: UUID) -> dict[str, bool]:
    """Get current tool permissions for a context."""
    stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
    result = await session.execute(stmt)
    permissions = result.scalars().all()
    return {perm.tool_name: perm.allowed for perm in permissions}


async def set_tool_permission(
    session: AsyncSession,
    context_id: UUID,
    tool_name: str,
    allowed: bool,
) -> None:
    """Set permission for a specific tool in a context."""
    # Check if permission already exists
    stmt = select(ToolPermission).where(
        ToolPermission.context_id == context_id,
        ToolPermission.tool_name == tool_name,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing permission
        existing.allowed = allowed
        action = "updated"
    else:
        # Create new permission
        permission = ToolPermission(
            context_id=context_id,
            tool_name=tool_name,
            allowed=allowed,
        )
        session.add(permission)
        action = "created"

    await session.commit()
    status = "allowed" if allowed else "denied"
    print(f"✅ {action.capitalize()} permission: {tool_name} = {status}")


async def delete_tool_permission(
    session: AsyncSession,
    context_id: UUID,
    tool_name: str,
) -> None:
    """Delete a tool permission (revert to default allow-all)."""
    stmt = select(ToolPermission).where(
        ToolPermission.context_id == context_id,
        ToolPermission.tool_name == tool_name,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        await session.delete(existing)
        await session.commit()
        print(f"✅ Removed permission for {tool_name} (now allowed by default)")
    else:
        print(f"⚠️  No permission found for {tool_name}")


async def reset_context_permissions(
    session: AsyncSession,
    context_id: UUID,
) -> None:
    """Delete all permissions for a context (allow all tools)."""
    stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
    result = await session.execute(stmt)
    permissions = result.scalars().all()

    if not permissions:
        print("ℹ️  No permissions to reset")
        return

    for perm in permissions:
        await session.delete(perm)

    await session.commit()
    print(f"✅ Removed {len(permissions)} permissions (all tools now allowed)")


async def interactive_menu() -> None:
    """Interactive menu for managing tool permissions."""
    async with AsyncSessionLocal() as session:
        while True:
            print("\n" + "=" * 60)
            print("Tool Permission Management")
            print("=" * 60)
            print("1. List all available tools")
            print("2. List all contexts")
            print("3. View permissions for a context")
            print("4. Grant tool access (allow)")
            print("5. Revoke tool access (deny)")
            print("6. Remove specific permission (revert to default)")
            print("7. Reset all permissions for a context (allow all)")
            print("8. Exit")
            print("=" * 60)

            choice = input("\nEnter choice (1-8): ").strip()

            if choice == "1":
                # List tools
                print("\nAvailable Tools:")
                print("-" * 60)
                tools = await list_available_tools()
                for i, tool in enumerate(tools, 1):
                    print(f"{i:3d}. {tool}")
                print(f"\nTotal: {len(tools)} tools")

            elif choice == "2":
                # List contexts
                print("\nAvailable Contexts:")
                print("-" * 60)
                contexts = await list_contexts(session)
                if not contexts:
                    print("No contexts found. Create a context first.")
                    continue

                for i, (_ctx_id, ctx_name) in enumerate(contexts, 1):
                    print(f"{i:3d}. {ctx_name}")
                print(f"\nTotal: {len(contexts)} contexts")

            elif choice == "3":
                # View permissions
                contexts = await list_contexts(session)
                if not contexts:
                    print("No contexts found.")
                    continue

                print("\nSelect context:")
                for i, (_ctx_id, ctx_name) in enumerate(contexts, 1):
                    print(f"{i}. {ctx_name}")

                ctx_idx = input("Context number: ").strip()
                try:
                    ctx_id, ctx_name = contexts[int(ctx_idx) - 1]
                except (ValueError, IndexError):
                    print("❌ Invalid context selection")
                    continue

                permissions = await get_context_permissions(session, ctx_id)

                print(f"\nPermissions for context '{ctx_name}':")
                print("-" * 60)

                if not permissions:
                    print("No permissions defined (all tools allowed by default)")
                else:
                    for tool_name, allowed in sorted(permissions.items()):
                        status = "✅ ALLOW" if allowed else "❌ DENY"
                        print(f"{status:10s} {tool_name}")
                    print(f"\nTotal: {len(permissions)} explicit permissions")

            elif choice in ("4", "5"):
                # Grant/Revoke
                allowed = choice == "4"
                action = "grant" if allowed else "revoke"

                contexts = await list_contexts(session)
                if not contexts:
                    print("No contexts found.")
                    continue

                print(f"\nSelect context to {action} access:")
                for i, (_ctx_id, ctx_name) in enumerate(contexts, 1):
                    print(f"{i}. {ctx_name}")

                ctx_idx = input("Context number: ").strip()
                try:
                    ctx_id, ctx_name = contexts[int(ctx_idx) - 1]
                except (ValueError, IndexError):
                    print("❌ Invalid context selection")
                    continue

                tool_name = input(f"\nTool name to {action}: ").strip()

                if not tool_name:
                    print("❌ Tool name cannot be empty")
                    continue

                await set_tool_permission(session, ctx_id, tool_name, allowed)

            elif choice == "6":
                # Remove permission
                contexts = await list_contexts(session)
                if not contexts:
                    print("No contexts found.")
                    continue

                print("\nSelect context:")
                for i, (_ctx_id, ctx_name) in enumerate(contexts, 1):
                    print(f"{i}. {ctx_name}")

                ctx_idx = input("Context number: ").strip()
                try:
                    ctx_id, ctx_name = contexts[int(ctx_idx) - 1]
                except (ValueError, IndexError):
                    print("❌ Invalid context selection")
                    continue

                tool_name = input("\nTool name to remove permission for: ").strip()

                if not tool_name:
                    print("❌ Tool name cannot be empty")
                    continue

                await delete_tool_permission(session, ctx_id, tool_name)

            elif choice == "7":
                # Reset all
                contexts = await list_contexts(session)
                if not contexts:
                    print("No contexts found.")
                    continue

                print("\nSelect context to reset:")
                for i, (_ctx_id, ctx_name) in enumerate(contexts, 1):
                    print(f"{i}. {ctx_name}")

                ctx_idx = input("Context number: ").strip()
                try:
                    ctx_id, ctx_name = contexts[int(ctx_idx) - 1]
                except (ValueError, IndexError):
                    print("❌ Invalid context selection")
                    continue

                confirm = input(f"\nReset all permissions for '{ctx_name}'? (yes/no): ").strip()

                if confirm.lower() == "yes":
                    await reset_context_permissions(session, ctx_id)
                else:
                    print("Cancelled")

            elif choice == "8":
                print("\nGoodbye!")
                break

            else:
                print("❌ Invalid choice")


async def cli_mode() -> None:
    """Command-line mode for scripting."""
    if len(sys.argv) < 3:
        print("Usage:")
        print("  List tools:      python manage_tool_permissions.py list-tools")
        print("  List contexts:   python manage_tool_permissions.py list-contexts")
        print("  View permissions: python manage_tool_permissions.py view <context_id>")
        print("  Grant access:    python manage_tool_permissions.py allow <context_id> <tool_name>")
        print("  Revoke access:   python manage_tool_permissions.py deny <context_id> <tool_name>")
        print(
            "  Remove permission: python manage_tool_permissions.py remove <context_id> <tool_name>"
        )
        print("  Reset context:   python manage_tool_permissions.py reset <context_id>")
        print("\nOr run without arguments for interactive mode")
        sys.exit(1)

    command = sys.argv[1]

    async with AsyncSessionLocal() as session:
        if command == "list-tools":
            tools = await list_available_tools()
            for tool in tools:
                print(tool)

        elif command == "list-contexts":
            contexts = await list_contexts(session)
            for ctx_id, ctx_name in contexts:
                print(f"{ctx_id}\t{ctx_name}")

        elif command == "view":
            context_id = UUID(sys.argv[2])
            permissions = await get_context_permissions(session, context_id)

            if not permissions:
                print("No permissions (all tools allowed)")
            else:
                for tool_name, allowed in sorted(permissions.items()):
                    status = "allow" if allowed else "deny"
                    print(f"{tool_name}\t{status}")

        elif command == "allow":
            context_id = UUID(sys.argv[2])
            tool_name = sys.argv[3]
            await set_tool_permission(session, context_id, tool_name, True)

        elif command == "deny":
            context_id = UUID(sys.argv[2])
            tool_name = sys.argv[3]
            await set_tool_permission(session, context_id, tool_name, False)

        elif command == "remove":
            context_id = UUID(sys.argv[2])
            tool_name = sys.argv[3]
            await delete_tool_permission(session, context_id, tool_name)

        elif command == "reset":
            context_id = UUID(sys.argv[2])
            await reset_context_permissions(session, context_id)

        else:
            print(f"Unknown command: {command}")
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Interactive mode
        asyncio.run(interactive_menu())
    else:
        # CLI mode
        asyncio.run(cli_mode())

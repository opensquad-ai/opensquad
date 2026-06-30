"""
OpenSquad Admin Command-Line Tool

Usage:
    python -m opensquad.admin <command> [options]

Commands:
    reset_password <email> <new_password>   Reset the password for a specified user
    list_users                              List all registered users
    help                                    Show help information

Examples:
    python -m opensquad.admin reset_password user@example.com NewPass123
    python -m opensquad.admin list_users
"""

import asyncio
import sys
from pathlib import Path

# Add project root directory to Python path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Bootstrap workspace so syscfg resolves the correct data directory
from opensquad.workspace_utils import bootstrap_workspace

bootstrap_workspace()


async def reset_password(email: str, new_password: str) -> bool:
    """Reset user password

    Args:
        email: User email address
        new_password: New password

    Returns:
        True on success, False on failure
    """
    # Import required modules
    from passlib.context import CryptContext
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Create password hashing context
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    # Database file path (use workspace-aware path)
    from opensquad.system_config import syscfg

    db_path = Path(syscfg.workspace_db_path("chat.db"))

    if not db_path.exists():
        print(f"Error: Database file does not exist: {db_path}")
        return False

    # Create database connection
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            # Use raw SQL query to find user
            query = text("SELECT id, name, email FROM users WHERE email = :email")
            result = await session.execute(query, {"email": email})
            user_row = result.fetchone()

            if not user_row:
                print(f"Error: No user found with email '{email}'")
                return False

            user_id, user_name, user_email = user_row

            # Generate new password hash
            new_hashed_password = pwd_context.hash(new_password)

            # Update password
            update_query = text("""
                UPDATE users
                SET hashed_password = :hashed_password
                WHERE email = :email
            """)
            await session.execute(update_query, {"hashed_password": new_hashed_password, "email": email})
            await session.commit()

            print("Successfully reset user password")
            print(f"  User ID: {user_id}")
            print(f"  Username: {user_name}")
            print(f"  Email: {user_email}")
            print(f"  New password: {new_password}")
            return True

    except Exception as e:
        print(f"Error: Failed to reset password: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        await engine.dispose()


async def list_users() -> bool:
    """List all registered users

    Returns:
        True on success, False on failure
    """
    # Import required modules
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    # Database file path (use workspace-aware path)
    from opensquad.system_config import syscfg

    db_path = Path(syscfg.workspace_db_path("chat.db"))

    if not db_path.exists():
        print(f"Error: Database file does not exist: {db_path}")
        return False

    # Create database connection
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            # Use raw SQL query to avoid enum type issues
            query = text("""
                SELECT id, name, email, status, created_at
                FROM users
                ORDER BY created_at
            """)
            result = await session.execute(query)
            users = result.fetchall()

            if not users:
                print("No users found")
                return True

            print(f"\nFound {len(users)} user(s):\n")
            print(f"{'ID':<8} {'Username':<20} {'Email':<30} {'Status':<10} {'Registered At'}")
            print("-" * 100)

            for user in users:
                user_id, name, email, status, created_at = user

                # Format time (handle string or datetime)
                if isinstance(created_at, str):
                    created_date = created_at[:16]  # Truncate to YYYY-MM-DD HH:MM
                else:
                    created_date = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "N/A"

                print(f"{user_id:<8} {name:<20} {email:<30} {status:<10} {created_date}")

            print()
            return True

    except Exception as e:
        print(f"Error: Failed to query users: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        await engine.dispose()


def show_help():
    """Show help information"""
    print(__doc__)


def main():
    """Main function"""
    if len(sys.argv) < 2:
        show_help()
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "help" or command == "-h" or command == "--help":
        show_help()
        sys.exit(0)

    elif command == "reset_password":
        if len(sys.argv) != 4:
            print("Error: reset_password command requires 2 arguments")
            print("Usage: python -m opensquad.admin reset_password <email> <new_password>")
            sys.exit(1)

        email = sys.argv[2]
        new_password = sys.argv[3]

        # Validate new password length
        if len(new_password) < 6:
            print("Error: New password must be at least 6 characters long")
            sys.exit(1)

        success = asyncio.run(reset_password(email, new_password))
        sys.exit(0 if success else 1)

    elif command == "list_users":
        if len(sys.argv) != 2:
            print("Error: list_users command takes no arguments")
            print("Usage: python -m opensquad.admin list_users")
            sys.exit(1)

        success = asyncio.run(list_users())
        sys.exit(0 if success else 1)

    else:
        print(f"Error: Unknown command '{command}'")
        print("Available commands: reset_password, list_users, help")
        print("Run 'python -m opensquad.admin help' for detailed help")
        sys.exit(1)


if __name__ == "__main__":
    main()

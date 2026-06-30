"""
Quick data migration script

Purpose: Migrate user data from the old location to the new workspace
Use case: After upgrading to the workspace architecture, migrate legacy accounts and data
"""

import os
import shutil
import sys

# Add project root directory to Python path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _root)

from opensquad.system_config import syscfg
from opensquad.workspace_utils import bootstrap_workspace, detect_legacy_data


def main():
    print("=" * 70)
    print("OpenSquad Data Migration Tool")
    print("=" * 70)
    print()

    # Initialize workspace
    print("1. Initializing workspace...")
    workspace = bootstrap_workspace()
    print(f"   Workspace path: {workspace}")
    print()

    # Detect legacy data
    print("2. Detecting legacy data...")
    legacy_root = syscfg.get_builtin_root()
    legacy_items = detect_legacy_data(legacy_root)

    if not legacy_items:
        print("   [OK] No data requiring migration found")
        print()
        return

    print(f"   Found {len(legacy_items)} item(s) to migrate:")
    for item in legacy_items:
        print(f"     - {item}")
    print()

    # Confirm migration
    print("3. Starting migration...")
    response = input("   Proceed with migration? [y/N]: ").strip().lower()
    if response != "y":
        print("   Migration cancelled")
        return
    print()

    # Migrate key data
    migrated = 0
    errors = []

    # Migrate database
    old_db = os.path.join(legacy_root, "gateway", "backend", "chat.db")
    if os.path.exists(old_db):
        new_db = syscfg.workspace_db_path("chat.db")
        try:
            os.makedirs(os.path.dirname(new_db), exist_ok=True)
            if not os.path.exists(new_db):
                shutil.copy2(old_db, new_db)
                print("   [OK] Migrated database: chat.db")
                migrated += 1
            else:
                print("   - Skipped (target already exists): chat.db")
        except Exception as e:
            errors.append(f"chat.db: {e}")
            print(f"   [FAIL] Failed: chat.db - {e}")

    # Migrate web sessions
    old_sessions = os.path.join(legacy_root, "gateway", "backend", "ai_web_sessions.json")
    if os.path.exists(old_sessions):
        new_sessions = syscfg.workspace_gateway_dir("backend", "ai_web_sessions.json")
        try:
            os.makedirs(os.path.dirname(new_sessions), exist_ok=True)
            if not os.path.exists(new_sessions):
                shutil.copy2(old_sessions, new_sessions)
                print("   [OK] Migrated session data: ai_web_sessions.json")
                migrated += 1
            else:
                print("   - Skipped (target already exists): ai_web_sessions.json")
        except Exception as e:
            errors.append(f"ai_web_sessions.json: {e}")
            print(f"   [FAIL] Failed: ai_web_sessions.json - {e}")

    # Migrate uploaded files
    old_uploads = os.path.join(legacy_root, "data", "uploads")
    if os.path.exists(old_uploads) and os.path.isdir(old_uploads):
        new_uploads = syscfg.workspace_uploads_dir()
        try:
            os.makedirs(new_uploads, exist_ok=True)
            # Copy only files that don't already exist
            count = 0
            for item in os.listdir(old_uploads):
                old_item = os.path.join(old_uploads, item)
                new_item = os.path.join(new_uploads, item)
                if not os.path.exists(new_item):
                    if os.path.isfile(old_item):
                        shutil.copy2(old_item, new_item)
                        count += 1
                    elif os.path.isdir(old_item):
                        shutil.copytree(old_item, new_item)
                        count += 1
            if count > 0:
                print(f"   [OK] Migrated uploaded files: {count} file(s)/directory(ies)")
                migrated += 1
        except Exception as e:
            errors.append(f"uploads: {e}")
            print(f"   [FAIL] Failed: uploads - {e}")

    # Migrate agents data
    old_agents = os.path.join(legacy_root, "agents")
    if os.path.exists(old_agents) and os.path.isdir(old_agents):
        new_agents = syscfg.workspace_agents_dir()
        try:
            os.makedirs(new_agents, exist_ok=True)
            count = 0
            for agent_name in os.listdir(old_agents):
                old_agent = os.path.join(old_agents, agent_name)
                new_agent = os.path.join(new_agents, agent_name)
                if os.path.isdir(old_agent) and not os.path.exists(new_agent):
                    shutil.copytree(old_agent, new_agent)
                    count += 1
            if count > 0:
                print(f"   [OK] Migrated agents: {count} agent(s)")
                migrated += 1
        except Exception as e:
            errors.append(f"agents: {e}")
            print(f"   [FAIL] Failed: agents - {e}")

    print()
    print("=" * 70)
    print("Migration complete")
    print("=" * 70)
    print(f"  Successfully migrated: {migrated} item(s)")
    if errors:
        print(f"  Failed: {len(errors)} item(s)")
        for err in errors:
            print(f"    - {err}")
    print()
    print(f"  Workspace path: {workspace}")
    print(f"  Database path: {syscfg.workspace_db_path('chat.db')}")
    print()
    print("Tip: You can now restart OpenSquad; your accounts and data should be restored.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()

# -*- coding: utf-8 -*-
"""
Force migration to a new workspace

Purpose: Forcefully create a new workspace and migrate all data.
"""
import os
import sys
import shutil

# Add project root directory to Python path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _root)

from opensquad.system_config import syscfg

def main():
    print("="*70)
    print("OpenSquad Force Migration Tool")
    print("="*70)
    print()
    
    # Set new workspace path
    default_workspace = os.path.join(os.path.expanduser("~"), "Documents", "OpenSquad-Workspace")
    print(f"Target workspace: {default_workspace}")
    print()
    
    # Force set workspace
    syscfg.set_workspace(default_workspace)
    
    # Initialize workspace structure
    print("1. Initializing workspace structure...")
    syscfg.ensure_workspace_structure()
    
    # Copy system_config.json
    src_config = os.path.join(_root, "system_config.json")
    dst_config = os.path.join(default_workspace, "system_config.json")
    if os.path.exists(src_config) and not os.path.exists(dst_config):
        shutil.copy2(src_config, dst_config)
        print(f"   Copied config file")
    
    # Create metadata
    import json
    from datetime import datetime, timezone
    metadata = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "migrated_from": _root
    }
    metadata_path = syscfg.workspace_metadata_dir("workspace.json")
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"   Created metadata")
    print()
    
    # Start migrating data
    print("2. Migrating data...")
    migrated = 0
    
    # Migrate database
    old_db = os.path.join(_root, "opensquad", "gateway", "backend", "chat.db")
    if os.path.exists(old_db):
        new_db = syscfg.workspace_db_path("chat.db")
        os.makedirs(os.path.dirname(new_db), exist_ok=True)
        if not os.path.exists(new_db):
            shutil.copy2(old_db, new_db)
            print(f"   Database: chat.db")
            migrated += 1
    
    # Migrate web sessions
    old_sessions = os.path.join(_root, "opensquad", "gateway", "backend", "ai_web_sessions.json")
    if os.path.exists(old_sessions):
        new_sessions = syscfg.workspace_gateway_dir("backend", "ai_web_sessions.json")
        os.makedirs(os.path.dirname(new_sessions), exist_ok=True)
        if not os.path.exists(new_sessions):
            shutil.copy2(old_sessions, new_sessions)
            print(f"   Web sessions: ai_web_sessions.json")
            migrated += 1
    
    # Migrate uploads
    old_uploads = os.path.join(_root, "data", "uploads")
    if os.path.exists(old_uploads):
        new_uploads = syscfg.workspace_uploads_dir()
        os.makedirs(new_uploads, exist_ok=True)
        count = 0
        for item in os.listdir(old_uploads):
            old_item = os.path.join(old_uploads, item)
            new_item = os.path.join(new_uploads, item)
            if not os.path.exists(new_item):
                try:
                    if os.path.isfile(old_item):
                        shutil.copy2(old_item, new_item)
                        count += 1
                    elif os.path.isdir(old_item):
                        shutil.copytree(old_item, new_item)
                        count += 1
                except Exception as e:
                    print(f"      Warning: skipping {item} - {e}")
        if count > 0:
            print(f"   Uploads: {count} items")
            migrated += 1
    
    # Migrate agents
    old_agents = os.path.join(_root, "agents")
    if os.path.exists(old_agents):
        new_agents = syscfg.workspace_agents_dir()
        os.makedirs(new_agents, exist_ok=True)
        count = 0
        for agent_name in os.listdir(old_agents):
            old_agent = os.path.join(old_agents, agent_name)
            new_agent = os.path.join(new_agents, agent_name)
            if os.path.isdir(old_agent) and not os.path.exists(new_agent):
                try:
                    shutil.copytree(old_agent, new_agent)
                    count += 1
                except Exception as e:
                    print(f"      Warning: skipping {agent_name} - {e}")
        if count > 0:
            print(f"   Agents: {count} items")
            migrated += 1
    
    print()
    print("="*70)
    print("Migration complete!")
    print("="*70)
    print(f"  Successfully migrated: {migrated} items")
    print(f"  Workspace: {default_workspace}")
    print()
    
    # Save workspace record
    from opensquad.workspace_utils import save_last_workspace
    save_last_workspace(default_workspace)
    print("  Workspace record saved")
    print()
    print("You can now restart OpenSquad -- your account and data have been restored!")
    print("="*70)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

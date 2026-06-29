# -*- coding: utf-8 -*-
"""
OpenSquad Data Migration Tool

Legacy data migration tool: migrate user data from the installation directory to an independent workspace.
"""
import os
import shutil
import json
from pathlib import Path
from typing import List, Dict, Callable
from datetime import datetime, timezone


class MigrationReport:
    """Migration report"""
    def __init__(self):
        self.success: List[str] = []
        self.failed: List[Dict] = []
        self.skipped: List[str] = []
        self.warnings: List[str] = []
    
    def add_success(self, item: str):
        self.success.append(item)
    
    def add_failed(self, item: str, error: str):
        self.failed.append({"item": item, "error": error})
    
    def add_skipped(self, item: str):
        self.skipped.append(item)
    
    def add_warning(self, message: str):
        self.warnings.append(message)
    
    def to_dict(self):
        return {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "success_count": len(self.success),
            "failed_count": len(self.failed),
            "skipped_count": len(self.skipped),
            "warning_count": len(self.warnings),
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "warnings": self.warnings
        }


class LegacyDataMigrator:
    """Legacy data migration tool"""
    
    def __init__(self, install_dir: str, target_workspace: str, mode: str = "copy",
                 overwrite: bool = False):
        """
        Args:
            install_dir: OpenSquad installation directory
            target_workspace: target workspace path
            mode: "copy" or "move"
            overwrite: True=overwrite existing target (with backup), False=skip existing target (default)
        """
        self.install_dir = Path(install_dir)
        self.target_workspace = Path(target_workspace)
        self.mode = mode
        self.overwrite = overwrite
        self.report = MigrationReport()
    
    def migrate(self, progress_callback: Callable[[str], None] = None) -> MigrationReport:
        """
        Execute the migration.
        
        Args:
            progress_callback: progress callback function, receives progress message string
        
        Returns:
            Migration report
        """
        def log(msg: str):
            if progress_callback:
                progress_callback(msg)
            else:
                print(msg)
        
        log("[Migration] Starting data migration...")
        log(f"[Migration] Source: {self.install_dir}")
        log(f"[Migration] Target: {self.target_workspace}")
        log(f"[Migration] Mode: {self.mode}")
        
        # Define directories/files to migrate
        # Format: (source path relative to install_dir, target path relative to workspace, required)
        migration_items = [
            # Database
            ("gateway/backend/chat.db", "gateway/backend/chat.db", False),
            ("gateway/backend/ai_web_sessions.json", "gateway/backend/ai_web_sessions.json", False),
            
            # Agents directory (full migration)
            ("agents", "agents", False),
            
            # Session data
            ("sessions", "data/sessions", False),
            ("gateway/backend/sessions", "gateway/backend/sessions", False),
            
            # Uploaded files
            ("data/uploads", "data/uploads", False),
            ("gateway/backend/uploads", "gateway/backend/uploads", False),
            
            # Log files
            ("data/logs", "data/logs", False),
            
            # Conversation history
            ("ai_his_talk", "data/ai_his_talk", False),
            ("data/ai_his_talk", "data/ai_his_talk", False),
            
            # Plugin data
            ("data/plugins", "data/plugins", False),
            
            # Audit logs
            ("data/audit", "data/audit", False),
            
            # Configuration files
            ("system_config.json", "system_config.json", True),
            ("data/mcp_global.json", "data/mcp_global.json", False),
            
            # Task data
            ("gateway/backend/tasks", "gateway/backend/tasks", False),
        ]
        
        total = len(migration_items)
        for idx, (src_rel, dst_rel, required) in enumerate(migration_items, 1):
            src_path = self.install_dir / src_rel
            dst_path = self.target_workspace / dst_rel
            
            log(f"[Migration] [{idx}/{total}] Processing: {src_rel}")
            
            if not src_path.exists():
                if required:
                    self.report.add_warning(f"Required item not found: {src_rel}")
                else:
                    self.report.add_skipped(f"{src_rel} (not exist)")
                continue
            
            try:
                # Ensure target directory exists
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                
                # If target already exists
                if dst_path.exists():
                    if not self.overwrite:
                        # Skip conflict: do not overwrite target, but in move mode still delete source
                        # (target already has data, source can safely be removed)
                        if self.mode == "move":
                            try:
                                if src_path.is_dir():
                                    shutil.rmtree(str(src_path))
                                else:
                                    src_path.unlink()
                                self.report.add_skipped(f"{src_rel} (target exists, source removed)")
                            except Exception as _del_err:
                                self.report.add_skipped(f"{src_rel} (target exists, source removal failed: {_del_err})")
                        else:
                            self.report.add_skipped(f"{src_rel} (target already exists)")
                        continue
                    else:
                        # Overwrite mode: backup first, then overwrite
                        backup_path = dst_path.parent / f"{dst_path.name}.backup_{int(datetime.now().timestamp())}"
                        shutil.move(str(dst_path), str(backup_path))
                        self.report.add_warning(f"Backed up existing {dst_rel} to {backup_path.name}")
                
                # Execute migration
                if src_path.is_dir():
                    if self.mode == "copy":
                        shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                    else:  # move
                        shutil.move(str(src_path), str(dst_path))
                else:
                    if self.mode == "copy":
                        shutil.copy2(src_path, dst_path)
                    else:  # move
                        shutil.move(str(src_path), str(dst_path))
                
                self.report.add_success(f"{src_rel} -> {dst_rel}")
                
            except Exception as e:
                self.report.add_failed(src_rel, str(e))
                log(f"[Migration] ERROR: {e}")
        
        # Save migration report
        report_path = self.target_workspace / ".opensquad" / "migration_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(self.report.to_dict(), f, indent=2, ensure_ascii=False)
        
        log(f"[Migration] Completed!")
        log(f"[Migration] Success: {len(self.report.success)}, Failed: {len(self.report.failed)}, Skipped: {len(self.report.skipped)}")
        log(f"[Migration] Report saved to: {report_path}")
        
        return self.report


def handle_legacy_migration(install_dir: str) -> str:
    """
    Handle the interactive flow for legacy data migration.
    
    Returns:
        The final confirmed workspace path
    """
    from opensquad.workspace_utils import get_default_workspace_path
    
    print("\n" + "="*60)
    print("Legacy user data detected in installation directory")
    print("="*60)
    print("\nRecommended: migrate to an independent workspace to:")
    print("  - Keep user data safe when upgrading OpenSquad")
    print("  - Support multiple independent project workspaces")
    print("  - Fully isolate code and data")
    print("\nPlease choose:")
    print("  1. Migrate to a new workspace (recommended)")
    print("  2. Continue using the installation directory as workspace (backward compatible)")
    print("  3. Exit and handle later")
    
    choice = input("\nEnter your choice (1/2/3): ").strip()
    
    if choice == "1":
        # Migrate to new workspace
        default_workspace = get_default_workspace_path()
        custom_path = input(f"\nWorkspace path [leave blank to use default: {default_workspace}]: ").strip()
        workspace_path = custom_path if custom_path else default_workspace
        
        # Choose migration mode
        print("\nMigration mode:")
        print("  1. Copy (recommended, keeps original data as backup)")
        print("  2. Move (saves disk space, but cannot be rolled back)")
        mode_choice = input("Choose (1/2) [default: 1]: ").strip()
        mode = "move" if mode_choice == "2" else "copy"
        
        # Initialize workspace structure
        from opensquad import system_config as syscfg
        print(f"\n[1/3] Initializing workspace: {workspace_path}")
        syscfg.init_workspace(workspace_path, copy_config=False)  # Config is copied by migration tool
        
        # Execute migration
        print(f"\n[2/3] Migrating data (mode: {mode})...")
        migrator = LegacyDataMigrator(install_dir, workspace_path, mode=mode)
        report = migrator.migrate(progress_callback=lambda msg: print(f"  {msg}"))
        
        # Display migration results
        print(f"\n[3/3] Migration complete!")
        print(f"  [OK] Success: {len(report.success)} item(s)")
        if report.failed:
            print(f"  [FAIL] Failed: {len(report.failed)} item(s)")
            for item in report.failed:
                print(f"    - {item['item']}: {item['error']}")
        if report.warnings:
            print(f"  [WARN] Warnings: {len(report.warnings)} item(s)")
            for warning in report.warnings:
                print(f"    - {warning}")
        
        if mode == "copy":
            print(f"\nNote: Original data is still retained in the installation directory.")
            print(f"After confirming the workspace is working correctly, you may delete it manually to free disk space.")
        
        return workspace_path
    
    elif choice == "2":
        # Continue using installation directory
        print("\nUsing installation directory as workspace (backward-compatible mode)")
        from opensquad import system_config as syscfg
        syscfg.set_workspace(install_dir)
        return install_dir
    
    else:
        # Exit
        print("\nExited. Please restart later and complete workspace configuration.")
        import sys
        sys.exit(0)

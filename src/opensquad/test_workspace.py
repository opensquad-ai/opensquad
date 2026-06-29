# -*- coding: utf-8 -*-
"""
Workspace mechanism tests

Verifies:
1. Correctness of workspace path functions
2. Workspace initialization
3. Workspace records
"""
import os
import sys

# Add project root directory to Python path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _root)

from opensquad.system_config import syscfg
from opensquad.workspace_utils import (
    get_default_workspace_path,
    bootstrap_workspace,
    detect_legacy_data
)

def test_workspace_paths():
    """Test workspace path functions"""
    print("="*60)
    print("Test 1: Workspace path functions")
    print("="*60)

    # Set test workspace
    test_workspace = r"C:\test_workspace"
    syscfg.set_workspace(test_workspace)

    # Test all path functions
    tests = [
        ("workspace_data_dir", syscfg.workspace_data_dir(), r"C:\test_workspace\data"),
        ("workspace_data_dir('uploads')", syscfg.workspace_data_dir("uploads"), r"C:\test_workspace\data\uploads"),
        ("workspace_agents_dir", syscfg.workspace_agents_dir(), r"C:\test_workspace\agents"),
        ("workspace_agents_dir('nexus_router')", syscfg.workspace_agents_dir("nexus_router"), r"C:\test_workspace\agents\nexus_router"),
        ("workspace_gateway_dir('backend')", syscfg.workspace_gateway_dir("backend"), r"C:\test_workspace\gateway\backend"),
        ("workspace_db_path", syscfg.workspace_db_path(), r"C:\test_workspace\gateway\backend\chat.db"),
        ("workspace_sessions_dir", syscfg.workspace_sessions_dir(), r"C:\test_workspace\data\sessions"),
        ("workspace_logs_dir", syscfg.workspace_logs_dir(), r"C:\test_workspace\data\logs"),
        ("workspace_uploads_dir", syscfg.workspace_uploads_dir(), r"C:\test_workspace\data\uploads"),
        ("workspace_metadata_dir", syscfg.workspace_metadata_dir(), r"C:\test_workspace\.opensquad"),
        ("builtin_resources_dir('plugins')", syscfg.builtin_resources_dir("plugins"), None),  # only verify no exception
    ]

    passed = 0
    failed = 0

    for name, result, expected in tests:
        if expected is None:
            # Only verify no exception is raised
            print(f"  [OK] {name}: {result}")
            passed += 1
        elif result == expected:
            print(f"  [OK] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name}")
            print(f"    Expected: {expected}")
            print(f"    Got:      {result}")
            failed += 1

    print(f"\nResult: {passed} passed, {failed} failed\n")
    return failed == 0


def test_workspace_init():
    """Test workspace initialization"""
    print("="*60)
    print("Test 2: Workspace initialization")
    print("="*60)

    import tempfile
    import shutil

    # Create a temporary test workspace
    test_dir = tempfile.mkdtemp(prefix="opensquad_test_")
    print(f"  Created test workspace: {test_dir}")

    try:
        # Initialize workspace
        syscfg.init_workspace(test_dir, copy_config=False)

        # Verify directory structure
        expected_dirs = [
            ".opensquad",
            "data/uploads",
            "data/logs/gateway",
            "data/sessions",
            "data/ai_his_talk",
            "data/plugins",
            "data/audit",
            "agents",
            "gateway/backend/sessions",
            "gateway/backend/tasks",
            "gateway/backend/uploads",
        ]

        all_exist = True
        for d in expected_dirs:
            full_path = os.path.join(test_dir, d)
            if os.path.exists(full_path):
                print(f"  [OK] {d}")
            else:
                print(f"  [FAIL] {d} (not found)")
                all_exist = False

        # Verify metadata file
        meta_file = os.path.join(test_dir, ".opensquad", "workspace.json")
        if os.path.exists(meta_file):
            print(f"  [OK] Metadata file exists")
            import json
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                print(f"    Version: {meta.get('version')}")
                print(f"    Created at: {meta.get('created_at')}")
        else:
            print(f"  [FAIL] Metadata file not found")
            all_exist = False

        print(f"\nResult: {'passed' if all_exist else 'failed'}\n")
        return all_exist

    finally:
        # Clean up test directory
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"  Cleaned up test workspace: {test_dir}\n")


def test_default_workspace():
    """Test default workspace path"""
    print("="*60)
    print("Test 3: Default workspace path")
    print("="*60)

    default_path = get_default_workspace_path()
    print(f"  Default workspace path: {default_path}")

    # On Windows, should be under Documents
    if sys.platform == "win32":
        expected_part = "Documents\\OpenSquad-Workspace"
        if expected_part in default_path:
            print(f"  [OK] Path contains {expected_part}")
            result = True
        else:
            print(f"  [FAIL] Path does not contain {expected_part}")
            result = False
    else:
        expected_part = "Documents/OpenSquad-Workspace"
        if expected_part in default_path:
            print(f"  [OK] Path contains {expected_part}")
            result = True
        else:
            print(f"  [FAIL] Path does not contain {expected_part}")
            result = False

    print(f"\nResult: {'passed' if result else 'failed'}\n")
    return result


def test_legacy_detection():
    """Test legacy data detection"""
    print("="*60)
    print("Test 4: Legacy data detection")
    print("="*60)

    install_dir = syscfg.get_builtin_root()
    has_legacy = detect_legacy_data(install_dir)

    print(f"  Install directory: {install_dir}")
    print(f"  Legacy data detected: {'yes' if has_legacy else 'no'}")

    # Check common legacy data indicators
    legacy_paths = [
        os.path.join(install_dir, "gateway", "backend", "chat.db"),
        os.path.join(install_dir, "agents"),
        os.path.join(install_dir, "data", "uploads"),
    ]

    for path in legacy_paths:
        if os.path.exists(path):
            print(f"  Found: {os.path.relpath(path, install_dir)}")

    print(f"\nResult: detection complete\n")
    return True


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("OpenSquad Workspace Mechanism Tests")
    print("="*60 + "\n")

    results = []

    # Run tests
    results.append(("Workspace path functions", test_workspace_paths()))
    results.append(("Workspace initialization", test_workspace_init()))
    results.append(("Default workspace path", test_default_workspace()))
    results.append(("Legacy data detection", test_legacy_detection()))

    # Summary
    print("="*60)
    print("Test Summary")
    print("="*60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "[OK] passed" if result else "[FAIL] failed"
        print(f"  {status}: {name}")

    print(f"\nTotal: {passed}/{total} passed")
    print("="*60)

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

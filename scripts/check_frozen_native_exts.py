"""Static check: verify frozen bundle has C extensions (.pyd/.so) for packages
that require them.

Catches the class of bug where PyInstaller's collect_submodules() was used
instead of collect_all() for a package with native extensions, resulting in
an empty __init__.py placeholder and `AttributeError: module 'X' has no
attribute 'Y'` at runtime (e.g. httptools.HttpRequestParser).

Usage: python scripts/check_frozen_native_exts.py [bundle_dir]
  bundle_dir defaults to build/backend-win/run
"""

import sys
from pathlib import Path

# Packages known to ship C extensions (.pyd on Windows, .so on Linux/macOS).
# Each entry: (package_name, expected_native_prefixes)
# expected_native_prefixes: list of filename prefixes the .pyd/.so should start with.
NATIVE_PACKAGES = {
    "httptools": ["parser"],
    "pydantic_core": ["_pydantic_core"],
    "cryptography": ["_rust"],  # modern cryptography ships _rust.pyd only (OpenSSL moved into _rust)
    "uvicorn": [],  # no native ext of its own, but collect_all pulls httptools
}

# Additional standalone .pyd files that must exist in _internal/
STANDALONE_NATIVES = {
    "win32": ["python311.dll"],
}


def main() -> int:
    bundle_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/backend-win/run")
    internal = bundle_dir / "_internal"
    if not internal.is_dir():
        print(f"FAIL: _internal/ not found at {internal}")
        print("      Run scripts\\build_backend.bat first.")
        return 1

    failures: list[str] = []
    platform_ext = ".pyd" if sys.platform == "win32" else ".so"

    for pkg_name, expected_prefixes in NATIVE_PACKAGES.items():
        pkg_dir = internal / pkg_name
        if not pkg_dir.is_dir():
            # Package may not be installed on this platform — skip
            print(f"SKIP: {pkg_name}/ not in bundle (may be platform-specific)")
            continue

        # Find all native extension files anywhere under the package dir
        # (some packages put .pyd in subdirs, e.g. httptools/parser/parser.pyd,
        #  cryptography/hazmat/bindings/_rust.pyd)
        native_files = list(pkg_dir.rglob(f"*{platform_ext}"))
        if not native_files:
            if expected_prefixes:
                failures.append(
                    f"{pkg_name}/: no {platform_ext} files found — likely collected "
                    f"with collect_submodules instead of collect_all. "
                    f"Expected files starting with: {expected_prefixes}"
                )
                print(f"FAIL: {pkg_name}/ has no native extensions ({platform_ext})")
            else:
                print(f"OK: {pkg_name}/ ({len(native_files)} native files)")
        else:
            names = [f.relative_to(pkg_dir).as_posix() for f in native_files]
            print(f"OK: {pkg_name}/ has {len(native_files)} native file(s): {names}")

    # Check standalone natives
    platform_key = "win32" if sys.platform == "win32" else "unix"
    for fname in STANDALONE_NATIVES.get(platform_key, []):
        if not (internal / fname).is_file():
            # python311.dll may be at bundle root, not _internal/
            alt = bundle_dir / fname
            if alt.is_file():
                print(f"OK: {fname} (at bundle root)")
            else:
                failures.append(f"Missing {fname} in {internal}")
                print(f"FAIL: {fname} not found")

    if failures:
        print(f"\nFAIL: {len(failures)} native extension check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: All native extension checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

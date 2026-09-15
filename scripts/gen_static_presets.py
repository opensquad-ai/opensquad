"""Generate src/opensquad/gateway/backend/app/ai_web/model_presets_static.py from a
good model_preset_cache.json, so the full vendor/model list ships as a bundled
offline fallback.

Usage:
    python scripts/gen_static_presets.py <source_cache.json>

The payload is emitted as a zlib-compressed, base64-armored JSON string inside a
Python module — i.e. still *code*, not a data file. That matters because frozen
(PyInstaller) builds do not collect package data reliably; see
``scripts/check_backend_bundle.py``, which guards the regression where
``collect_data_files("opensquad")`` swept build trees into the backend bundle.
Compressing keeps the guarantee while shrinking a 151-provider / 5305-model
catalog from ~2 MB of Python literals to ~108 KB.
"""

import base64
import json
import os
import sys
import textwrap
import zlib

_WRAP_AT = 100

_HEADER = '''"""Bundled offline vendor/model preset data (auto-generated).

Regenerate from a known-good cache file with::

    python scripts/gen_static_presets.py <source_cache.json>

This is the *initial fallback* used when there is no persisted cache yet and the
live refresh (models.dev / OpenRouter) is unreachable, so users can still
configure providers offline with the full vendor/model catalog. A successful
online refresh overwrites the writable disk cache (``model_preset_cache.json``).

WHY A COMPRESSED LITERAL AND NOT A .json FILE
---------------------------------------------
The payload is emitted as *code* so it survives frozen / PyInstaller builds,
where package data files are not collected reliably — see
``scripts/check_backend_bundle.py`` (written to catch the regression where
``collect_data_files("opensquad")`` swept build trees into the backend bundle).
Keeping the guarantee but compressing cuts a {providers}-provider / {models}-model
catalog from ~2 MB of Python literals (54,576 lines) to ~108 KB wrapped over ~1k lines.
"""

from __future__ import annotations

import base64
import json
import zlib

# Auto-generated from {source}: {providers} providers, {models} models.
# zlib-compressed, base64-armored UTF-8 JSON.
_PAYLOAD = (
'''

_FOOTER = """
)

STATIC_PRESETS: dict = json.loads(zlib.decompress(base64.b64decode(_PAYLOAD)))
"""


def encode(data: dict) -> str:
    """Return the module source that embeds ``data`` as a compressed payload."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return "\n".join(f'    "{chunk}"' for chunk in textwrap.wrap(blob, _WRAP_AT))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: gen_static_presets.py <source_cache.json>")
        sys.exit(2)
    src = sys.argv[1]
    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    providers = data.get("providers", [])
    total_models = sum(len(p.get("models", [])) for p in providers)
    print(f"read {len(providers)} providers, {total_models} models from {src}")

    out_path = os.path.normpath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "src",
            "opensquad",
            "gateway",
            "backend",
            "app",
            "ai_web",
            "model_presets_static.py",
        )
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(
            _HEADER.format(
                providers=len(providers),
                models=total_models,
                source=os.path.basename(src),
            )
        )
        f.write(encode(data))
        f.write(_FOOTER)
    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    main()

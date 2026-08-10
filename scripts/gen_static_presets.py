"""Generate src/opensquad/gateway/backend/app/ai_web/model_presets_static.py from a
good model_preset_cache.json, so the full vendor/model list ships as a bundled
offline fallback.

Usage:
    python scripts/gen_static_presets.py <source_cache.json>
"""

import json
import os
import sys


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

    # Emit STATIC_PRESETS as a Python literal (bundled as code, robust in frozen
    # builds where data files may not be packaged).
    from pprint import pformat

    body = pformat(data, width=160, sort_dicts=False)

    header = (
        '"""\n'
        "Bundled offline vendor/model preset data (auto-generated from a good\n"
        "model_preset_cache.json).\n"
        "\n"
        "This is the *initial fallback* used when there is no persisted cache yet\n"
        "and the live refresh (models.dev / OpenRouter) is unreachable. It lets users\n"
        "configure providers offline with the full vendor/model catalog.\n"
        "\n"
        "A successful online refresh overwrites the writable disk cache\n"
        "(``model_preset_cache.json``), which then becomes the source for later boots.\n"
        '"""\n'
        "\n"
        f"# Auto-generated from {os.path.basename(src)}: {len(providers)} providers, {total_models} models.\n"
        "\n"
        "STATIC_PRESETS = "
    )

    out_path = os.path.join(
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
    out_path = os.path.normpath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(body)
        f.write("\n")
    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    main()

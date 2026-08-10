import json

for lang, p in [
    ("zh", r"c:\ai_work\pro0\opensquad_deploy_test\src\opensquad\gateway\nexuschat-pro\locales\zh.json"),
    ("en", r"c:\ai_work\pro0\opensquad_deploy_test\src\opensquad\gateway\nexuschat-pro\locales\en.json"),
]:
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    presets = data["themeSettings"]["presets"]
    old_paper = presets.pop("paper", None)
    if old_paper is not None:
        # Rename paper to rose.
        rose_name = "玫瑰" if lang == "zh" else "Rose"
        rose_desc = (
            "纯净白底带一抹极淡暖调，深色下线条克制不抢眼"
            if lang == "zh"
            else "Clean white surfaces with a barely-warm tint; dark mode keeps lines quiet"
        )
        presets["rose"] = {"name": rose_name, "desc": rose_desc}
        pure_white_name = "纯白" if lang == "zh" else "Pure White"
        pure_white_desc = (
            "真正的纯白主题，浅色深色下都保持白底"
            if lang == "zh"
            else "True white surfaces in both light and dark mode"
        )
        presets["pureWhite"] = {"name": pure_white_name, "desc": pure_white_desc}

    # Pretty-print without changing the original file structure too much.
    # We do a compact 2-space dump so the diff stays reviewable.
    out = json.dumps(data, ensure_ascii=False, indent=2)
    # The original files end without a trailing newline; we keep that.
    with open(p, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"{lang}: wrote {len(out)} bytes; presets now: {list(presets.keys())}")

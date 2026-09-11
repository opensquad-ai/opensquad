import os

p = r"c:\ai_work\pro0\opensquad_deploy_test\src\opensquad\gateway\nexuschat-pro\utils\themeEngine.ts"
data = open(p, encoding="utf-8").read()

# 1. Rename paper preset to rose and add pure-white preset.
# Match the paper block as it actually appears, followed by the violet block,
# and the closing `];`.
old_lines = [
    "  {",
    "    id: 'paper',",
    "    // Neutral charcoal \u2014 no hue cast so the white surfaces stay truly white",
    "    primary: '#1F1F1F',",
    "    // In dark mode, invert to a light neutral so primary-coloured text",
    '    // (e.g. "Connect Provider" button label, "AI" badges) stays legible',
    "    // on the near-black surface. The hue/feel is preserved.",
    "    darkPrimary: '#E6E6E6',",
    "    surfaceHue: 0,",
    "    light: { bg: '#FFFFFF', panel: '#FFFFFF', border: '#ECEEF1' },",
    "    dark: { bg: '#0A0B0D', panel: '#121316', border: '#1E2025' },",
    "    i18nNameKey: 'themeSettings.presets.paper.name',",
    "    i18nDescKey: 'themeSettings.presets.paper.desc',",
    "  },",
]
old = "\n".join(old_lines)

new_lines = [
    "  {",
    '    // Renamed from "paper" \u2014 what was sold as "pure paper white" actually',
    "    // reads as a faint rose / warm tint once the purity slider tints the",
    "    // surfaces, so we now call it what it looks like.",
    "    id: 'rose',",
    "    primary: '#1F1F1F',",
    "    // In dark mode, invert to a light neutral so primary-coloured text",
    '    // (e.g. "Connect Provider" button label, "AI" badges) stays legible',
    "    // on the near-black surface. The hue/feel is preserved.",
    "    darkPrimary: '#E6E6E6',",
    "    surfaceHue: 0,",
    "    light: { bg: '#FFFFFF', panel: '#FFFFFF', border: '#ECEEF1' },",
    "    dark: { bg: '#0A0B0D', panel: '#121316', border: '#1E2025' },",
    "    i18nNameKey: 'themeSettings.presets.rose.name',",
    "    i18nDescKey: 'themeSettings.presets.rose.desc',",
    "  },",
    "  {",
    "    // True white \u2014 both light and dark modes keep the page on a true white",
    "    // surface (dark mode just nudges the page wash to an almost-imperceptible",
    "    // off-white so the user can still tell which side of system mode they're",
    "    // on). Primary stays a deep neutral in both modes because the button",
    "    // background is white, so no `darkPrimary` override is needed.",
    "    id: 'pure-white',",
    "    primary: '#1F1F1F',",
    "    surfaceHue: 0,",
    "    light: { bg: '#FFFFFF', panel: '#FFFFFF', border: '#F0F0F0' },",
    "    dark: { bg: '#F7F7F7', panel: '#FFFFFF', border: '#E8E8E8' },",
    "    i18nNameKey: 'themeSettings.presets.pureWhite.name',",
    "    i18nDescKey: 'themeSettings.presets.pureWhite.desc',",
    "  },",
]
new = "\n".join(new_lines)
print("FOUND" if old in data else "NOT FOUND")
data = data.replace(old, new, 1)

tmp = p + ".tmp"
open(tmp, "w", encoding="utf-8").write(data)
os.replace(tmp, p)
print("WROTE", len(data))

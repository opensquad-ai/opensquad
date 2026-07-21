# OpenSquad UI Design System

Product: local-first multi-agent collaboration (tooling SaaS).  
Audience: developers / power users.  
Job: calm, readable workspace — not a marketing site.

## Skill stack (how we design)

| Layer | Source | Use |
|-------|--------|-----|
| Direction | Anthropic `frontend-design` | Brief before code: palette, type roles, layout, one signature |
| Polish | Impeccable (`quieter` / `typeset` / `audit`) | Softer contrast, spacing, motion; no bounce/glow |
| Anchor | garden `linear` recipe | Hairline panels, modest radius, single low-chroma accent |
| Tokens dump | UI UX Pro Max (once) | Only when refreshing this file — not every chat |
| Chinese copy | qiaomu-design (typography rules) | Settings / i18n strings; 盘古之白, sentence case |
| Landing only | Taste Skill | Marketing pages — **not** the app chrome |

## Aesthetic direction

**Signature:** warm parchment / soft charcoal surfaces + one muted ink-green (or theme primary) accent used sparingly (<5% of pixels).

**Tone:** quiet professional (Linear × Claude Desktop), soft — not playful, not maximalist.

## Color tokens (runtime)

Driven by `nexuschat-pro/utils/themeEngine.ts` → CSS vars:

| Token | Role |
|-------|------|
| `--color-bg` | App wash (equals rail in light hierarchy) |
| `--color-rail` | Side rails: session list + workspace files (deeper) |
| `--color-stage` | Center stage: Agent Web chat (lighter) |
| `--color-panel` | Raised surfaces, modals, bubbles |
| `--color-border` | Hairline separators |
| `--color-primary` | Accent only (active, CTA, focus) |
| `--color-text-main` / `--color-text-muted` | Body / secondary |

**Agent Web hierarchy (luxury reference):** sides use `bg-rail` (slightly deeper / more saturated); center chat uses `bg-stage` (lighter). Never hardcode near-white (`#f8f8f8`) for workspace.

Never hardcode purple/indigo gradients for brand chrome. Prefer `var(--color-primary)`.

Use Tailwind: `bg-rail`, `bg-stage`, `bg-bgLight`, `bg-panel`, `border-border`, `text-textMain`, `text-textMuted`, `bg-primary`.

## Typography

| Role | Stack |
|------|--------|
| UI | `"DM Sans", "Noto Sans SC", system-ui, sans-serif` |
| Chat (optional serif) | `"Source Serif 4", "Noto Serif SC", Georgia, serif` |
| Mono | `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` |

- Body ~14–15px, line-height 1.5–1.55  
- Avoid Inter as a “designed” default  
- Chinese: prefer system/Noto stacks; avoid decorative CJK display fonts in chrome

## Spacing / radius / shadow

- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48  
- Radius: **6** (controls) / **12** (cards) / **16** (modals). Never >16 for product chrome.  
- Shadow: soft single layer only  
  - `0 1px 2px rgba(0,0,0,0.06)` light  
  - `0 1px 2px rgba(0,0,0,0.35)` dark  
- Borders: 1px hairline via `--color-border`. Prefer border over heavy shadow for separation.

## Motion

- Hover / toggle: **150–200ms** ease-out  
- Layout: up to **350ms** `cubic-bezier(0.22, 1, 0.36, 1)`  
- Respect `prefers-reduced-motion`  
- No bounce, elastic spring, or colored glow

## Soft UI checklist (quieter)

- [ ] Active nav: soft tint + hairline, not loud fills  
- [ ] Modal headers: panel + border, not full-bleed primary bars  
- [ ] Icon buttons: `rounded-lg`, muted → primary on hover  
- [ ] Lists: subtle hover wash; selected = primary/8–12% + border  
- [ ] Contrast: text vs bg meets ~4.5:1 for body  
- [ ] One accent color family per screen

## Anti-patterns (ban)

- Purple–pink / indigo–violet brand gradients  
- Multi-layer glow shadows, neon accents  
- Emoji as navigation icons  
- Three equal feature cards / pill-stat strips in product chrome  
- Cream `#F4F1EA` + terracotta + serif display as a default marketing cliché (OK only if user explicitly wants Claude parchment *and* we still keep product chrome quiet)  
- Changing CSS vars outside `themeStore` / `themeEngine`

## Component conventions

```tsx
// Preferred soft control
className="rounded-lg border border-border bg-panel px-3 py-2 text-sm text-textMain
  transition-colors duration-150 hover:bg-bgLight focus:outline-none focus:ring-2 focus:ring-primary/25"

// Preferred quiet modal shell
className="rounded-2xl border border-border bg-panel shadow-soft"
```

Use Tailwind semantic colors: `bg-rail`, `bg-stage`, `bg-bgLight`, `bg-panel`, `border-border`, `text-textMain`, `text-textMuted`, `bg-primary`, `text-primary`.

## Settings layout

Unified settings modal (`SystemConfigPage`):

```
┌ Settings ──────────────────── ✕ ┐
├ Left nav ┬ Right content ───────┤
│ Theme    │  (panel)             │
│ Workspace│                      │
│ Ports    │           [完成/保存] │
│ Advanced │                      │
│ About    │                      │
└──────────┴──────────────────────┘
```

- Theme primary uses a **small circle swatch** + hue popover (pill trigger).  
- Palette icon / `openThemeSettings` opens settings on the Theme tab.  
- Agent Web three-column surfaces: `bg-rail` (sides) + `bg-stage` (center).

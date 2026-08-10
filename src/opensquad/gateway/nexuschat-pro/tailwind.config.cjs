/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './index.html',
    './index.tsx',
    './App.tsx',
    './components/**/*.{tsx,ts}',
    './services/**/*.{tsx,ts}',
    './utils/**/*.{tsx,ts}',
    './electron/**/*.{tsx,ts}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
        chat: ['var(--font-chat)'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        soft: '0.375rem', // 6px
        card: '0.75rem', // 12px
        modal: '1rem', // 16px
      },
      boxShadow: {
        soft: '0 1px 2px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.04)',
        'soft-lg': '0 4px 16px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.04)',
        'soft-dark': '0 1px 2px rgba(0, 0, 0, 0.35)',
      },
      transitionTimingFunction: {
        soft: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      transitionDuration: {
        soft: '160ms',
      },
      colors: {
        // The theme CSS vars are space-separated RGB triplets (e.g. "61 52 40"),
        // so they have to be wrapped in `rgb()` to become a real colour value.
        // Using the `rgb(var(--x) / <alpha-value>)` form lets Tailwind's
        // opacity modifiers like `bg-primary/40` resolve to
        // `rgb(var(--color-primary) / 0.4)` without falling back to
        // `currentColor` (which in dark mode is light — the original
        // source of the "white border" bug).
        primary: 'rgb(var(--color-primary) / <alpha-value>)',
        /** Text colour that contrasts with `primary` — use on filled buttons
         *  so labels stay legible regardless of whether the current theme
         *  gives us a dark or a light primary (e.g. the rose preset in
         *  dark mode inverts primary to a light grey). */
        onPrimary: 'rgb(var(--color-on-primary) / <alpha-value>)',
        bgLight: 'rgb(var(--color-bg) / <alpha-value>)',
        /** One step darker than the page background — used for tooltips,
         *  progress-bar tracks, badges and embedded panels. */
        bgDark: 'color-mix(in srgb, rgb(var(--color-text-main)) 6%, rgb(var(--color-bg)))',
        bgPage: 'rgb(var(--color-bg) / <alpha-value>)',
        /** Side rails: session list + workspace files (deeper) */
        rail: 'rgb(var(--color-rail) / <alpha-value>)',
        /** Nested wells inside rails (deeper still) */
        nest: 'rgb(var(--color-nest) / <alpha-value>)',
        /** Center stage / page wash behind cards */
        stage: 'rgb(var(--color-stage) / <alpha-value>)',
        chatBubbleSelf: 'rgb(var(--color-bubble-self) / <alpha-value>)',
        chatBubbleOther: 'rgb(var(--color-bubble-other) / <alpha-value>)',
        panel: 'rgb(var(--color-panel) / <alpha-value>)',
        // The theme CSS vars are space-separated RGB triplets (so Tailwind's
        // `<alpha-value>` modifier can compose them in `rgb(...)` correctly).
        // We wrap them in `rgb()` here so `color-mix` sees a real colour.
        // Use color-mix so opacity modifiers like border-border/60 actually apply
        border: 'color-mix(in srgb, rgb(var(--color-border)) calc(<alpha-value> * 100%), transparent)',
        textMain: 'rgb(var(--color-text-main) / <alpha-value>)',
        textMuted: 'rgb(var(--color-text-muted) / <alpha-value>)',
      },
    },
  },
  plugins: [],
};

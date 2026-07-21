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
        primary: 'var(--color-primary)',
        bgLight: 'var(--color-bg)',
        bgPage: 'var(--color-bg)',
        /** Side rails: session list + workspace files (deeper) */
        rail: 'var(--color-rail)',
        /** Center stage: Agent Web chat (lighter) */
        stage: 'var(--color-stage)',
        chatBubbleSelf: 'var(--color-bubble-self)',
        chatBubbleOther: 'var(--color-bubble-other)',
        panel: 'var(--color-panel)',
        border: 'var(--color-border)',
        textMain: 'var(--color-text-main)',
        textMuted: 'var(--color-text-muted)',
      },
    },
  },
  plugins: [],
};

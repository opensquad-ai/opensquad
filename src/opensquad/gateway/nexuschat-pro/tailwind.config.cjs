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
        sans: ['Inter', 'sans-serif'],
      },
      colors: {
        primary: 'var(--color-primary)',
        bgLight: 'var(--color-bg)',
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

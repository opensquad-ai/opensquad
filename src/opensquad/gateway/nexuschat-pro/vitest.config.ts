import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['utils/**/*.test.ts', '**/*.test.ts'],
    exclude: ['node_modules', 'dist', 'dist-electron'],
  },
});

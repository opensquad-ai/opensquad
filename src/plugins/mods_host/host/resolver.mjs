// Module resolution hook for mod imports.
//
// Two things bare Node ESM cannot do for a mod, both found on real published
// mods rather than invented here:
//
// 1. `claude-code` is a bare specifier, and mods live in
//    `<workspace>/mods/<mod>/hooks/`, so walking up finds nothing. We answer it
//    rather than planting a `node_modules` in the user's workspace (state we do
//    not own, and which the packager strips).
// 2. TypeScript mods write extensionless relative imports (`from './format'`).
//    ESM requires the extension, so we try the TS/bundler-conventional suffixes.
//
// Registered once from `host.mjs` via `module.register()`.

import { statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const SHIM = new URL('./claude-code.mjs', import.meta.url).href

/** Suffixes tried for an extensionless relative specifier, in order. */
const SUFFIXES = ['', '.ts', '.tsx', '.mjs', '.js', '.cjs', '/index.ts', '/index.tsx', '/index.mjs', '/index.js']

function firstExisting(candidate) {
  try {
    return statSync(candidate).isFile() ? candidate : ''
  } catch {
    return ''
  }
}

export async function resolve(specifier, context, next) {
  if (specifier === 'claude-code' || specifier.startsWith('claude-code/')) {
    return { url: SHIM, shortCircuit: true, format: 'module' }
  }

  const relative = specifier.startsWith('./') || specifier.startsWith('../')
  // Only extensionless specifiers need the search; anything with a suffix is
  // Node's business (including `./model.ts`, which type stripping handles).
  if (relative && !/\.[a-z]+$/i.test(specifier) && context.parentURL) {
    const base = new URL(specifier, context.parentURL)
    if (base.protocol === 'file:') {
      const basePath = fileURLToPath(base)
      for (const suffix of SUFFIXES) {
        const found = firstExisting(basePath + suffix)
        if (found) return { url: new URL(`file://${found.replace(/\\/g, '/')}`).href, shortCircuit: true }
      }
    }
  }

  return next(specifier, context)
}

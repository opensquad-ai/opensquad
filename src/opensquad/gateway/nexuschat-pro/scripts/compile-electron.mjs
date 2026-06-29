/**
 * Compile Electron main/preload to CommonJS .cjs files.
 * package.json has "type": "module", so .js would be treated as ESM.
 */
import { spawnSync } from 'node:child_process'
import { readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const outDir = path.join(root, 'dist-electron')

const tsc = spawnSync(
  process.platform === 'win32' ? 'npx.cmd' : 'npx',
  ['tsc', '-p', 'tsconfig.electron.json'],
  { cwd: root, stdio: 'inherit', shell: process.platform === 'win32' },
)
if (tsc.status !== 0) process.exit(tsc.status ?? 1)

for (const file of readdirSync(outDir)) {
  if (!file.endsWith('.js')) continue
  const cjsName = file.replace(/\.js$/, '.cjs')
  renameSync(path.join(outDir, file), path.join(outDir, cjsName))
}

// Node does not resolve require('./foo') → foo.cjs; patch local relative imports.
for (const file of readdirSync(outDir)) {
  if (!file.endsWith('.cjs')) continue
  const filePath = path.join(outDir, file)
  const patched = readFileSync(filePath, 'utf-8').replace(
    /require\("\.\/([^"]+)"\)/g,
    (match, mod) => (mod.endsWith('.cjs') ? match : `require("./${mod}.cjs")`),
  )
  writeFileSync(filePath, patched)
}

# Vendored: sucrase

`mods_host` needs to turn a mod's `.tsx` into runnable JS, because Node 24's
native type stripping handles `.ts` but **not JSX**.

| | |
|---|---|
| Upstream | `sucrase` |
| Version | 3.35.1 |
| Licence | MIT (see `LICENSE.sucrase`) |
| What is vendored | **one bundled file**, `sucrase.bundle.mjs` (289 KB), built from the single entry `export { transform } from 'sucrase'` |

## Why a bundle instead of a dependency

`opensquad_backend.spec:53` filters `node_modules` out of everything under
`src/plugins/**`, so a normal dependency would be **silently missing from the
shipped wheel and desktop bundle** — the same class of defect as the missing
`prompts` directory. One plain file next to the host cannot be filtered away.

## How to rebuild

```bash
mkdir -p /tmp/sucrase-build && cd /tmp/sucrase-build
npm init -y && npm install sucrase@3.35.1 esbuild --no-audit --no-fund
printf "export { transform } from 'sucrase'\n" > entry.mjs
./node_modules/.bin/esbuild entry.mjs --bundle --format=esm --platform=node \
  --minify --outfile=sucrase.bundle.mjs
cp sucrase.bundle.mjs <repo>/src/plugins/mods_host/host/vendor/
cp node_modules/sucrase/LICENSE <repo>/src/plugins/mods_host/host/vendor/LICENSE.sucrase
```

The transform is **syntax only**: no bundling, no plugins, no code execution, and
the mod's own build scripts are never run. `mods_host` uses it for JSX (and
TypeScript) rewriting on the way in; nothing else from sucrase is reachable.

// Minimal `claude-code` shim.
//
// Real mods are written against a package named `claude-code`:
//
//   import { atom, read, update } from 'claude-code'
//   import type { EngineInterface, Register, Timer } from 'claude-code'
//
// Without it every such mod fails at import time. Type-only imports are erased,
// so only the value surface has to exist. The resolver next to this file maps the
// bare specifier here (see `resolver.mjs`).
//
// `atom` is a reactive cell keyed by `{plugin, key}`: `read`/`update` operate on
// it and subscribers are notified on change. Redrawing is still the mod's job
// (`$.ui.invalidate`), which is why this needs no handle on the host.

const cells = new Map()

const cellKey = (def) => `${def?.plugin ?? ''}\u0000${def?.key ?? ''}`

function cellFor(def) {
  const key = cellKey(def)
  let cell = cells.get(key)
  if (!cell) {
    cell = { value: undefined, subscribers: new Set() }
    cells.set(key, cell)
  }
  return cell
}

export function atom(def, initial) {
  const cell = cellFor(def)
  if (cell.value === undefined && initial !== undefined) cell.value = initial
  return { __modsAtom: true, key: cellKey(def) }
}

// Real mods call these as `read($, atom)` / `update($, atom, fn)` — the engine
// handle comes first, per the SDK's type surface. Accept the atom-first shape too:
// guessing wrong here silently no-ops instead of failing loudly.
const atomOf = (first, second) => (second === undefined ? first : second)

export function read(first, second) {
  return cells.get(atomOf(first, second)?.key)?.value
}

export function update(first, second, third) {
  const target = third === undefined ? first : second
  const next = third === undefined ? second : third
  const cell = cells.get(target?.key)
  if (!cell) return undefined
  cell.value = typeof next === 'function' ? next(cell.value) : next
  for (const fn of cell.subscribers) {
    try {
      fn(cell.value)
    } catch {
      /* one bad subscriber must not break the others */
    }
  }
  return cell.value
}

// `memberOf(def, member)` — the per-member cell of a collection atom. Real mods
// use it for per-row UI state: `memberOf(OPEN, e)` inside a `ui.render` handler
// is "the open flag of *this* row", and `memberOf(OPEN, { requestId })` reaches
// the same cell from outside a render (that is how a fold toggles the single row
// it unfolds into). Identity is the render request id; a member with no
// `requestId` falls back to its own scalar fields so it stays deterministic
// rather than silently sharing another member's cell.
const memberKeyOf = (member) => {
  if (member === null || member === undefined) return ''
  if (typeof member === 'string' || typeof member === 'number') return String(member)
  const id = member.requestId ?? member.props?.requestId
  if (id !== undefined && id !== null) return String(id)
  return Object.keys(member)
    .sort()
    .map((k) => {
      const v = member[k]
      return `${k}=${v === null || typeof v !== 'object' ? String(v) : '{...}'}`
    })
    .join(',')
}

export function memberOf(def, member) {
  const key = `${cellKey(def)}\u0000${memberKeyOf(member)}`
  if (!cells.has(key)) {
    // Seed from the base cell so a member starts out where `atom(def, initial)` left it.
    cells.set(key, { value: cells.get(cellKey(def))?.value, subscribers: new Set() })
  }
  return { __modsAtom: true, key }
}

/** Subscribe to an atom. Returns an unsubscribe function. */
export function subscribe(first, second, third) {
  const target = third === undefined ? first : second
  const fn = third === undefined ? second : third
  const cell = cells.get(target?.key)
  if (!cell || typeof fn !== 'function') return () => {}
  cell.subscribers.add(fn)
  return () => cell.subscribers.delete(fn)
}

/** `define`/`derive` are declared by the type surface; kept as honest no-ops. */
export function define(value) {
  return value
}

export function derive(fn) {
  return typeof fn === 'function' ? fn() : fn
}

export default { atom, memberOf, read, update, subscribe, define, derive }

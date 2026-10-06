#!/usr/bin/env node
// OpenSquad mod host — NDJSON over stdio. Protocol: docs/mods-bridge-m0.md §2.
//
// One pipe, both directions. Frames are one JSON object per line, UTF-8, no
// embedded newlines. Requests we originate use ids `h<n>`; the Python side uses
// `p<n>`, so the two id spaces never collide and each side can tell a response
// from an inbound request by looking up its own pending table.

import { createInterface } from 'node:readline'
import { register as registerLoader } from 'node:module'
import path from 'node:path'
import process from 'node:process'
import { pathToFileURL } from 'node:url'
import { createRuntime } from './runtime.mjs'

// Mods import a bare `claude-code` package; answer it ourselves (see resolver.mjs).
// Must happen before the first dynamic import of a mod.
registerLoader('./resolver.mjs', import.meta.url)

const API_VERSION = 1
const HOST_CALL_TIMEOUT_MS = 8000

const send = (msg) => {
  process.stdout.write(JSON.stringify(msg) + '\n')
}
// A notification (no id) runs its handler but must not answer: `session.end` is
// fire-and-forget from a synchronous unload. Silently dropping the reply here
// keeps every call site honest instead of scattering null checks.
const respond = (id, result) => {
  if (id === undefined || id === null) return
  send({ id, result })
}
const respondError = (id, code, message) => {
  if (id === undefined || id === null) return
  send({ id, error: { code, message } })
}

// ── host → Python calls (the reentrancy path, used by $ gates) ──────────────
let hostSeq = 0
const pending = new Map()

function callParent(method, params, timeoutMs = HOST_CALL_TIMEOUT_MS) {
  const id = `h${++hostSeq}`
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id)
      reject(new Error(`${method} timed out after ${timeoutMs}ms`))
    }, timeoutMs)
    pending.set(id, {
      resolve: (v) => {
        clearTimeout(timer)
        resolve(v)
      },
      reject: (e) => {
        clearTimeout(timer)
        reject(e)
      },
    })
    send({ id, method, params })
  })
}

// ── render elements ─────────────────────────────────────────────────────────
//
// Real mods get their element constructors from the host:
//   const { Box, Text, Button } = $.ui.resolve(e)
// Each factory returns a plain serializable node, which is what makes the tree
// transportable and validatable in Python (铁律 3). `Button.onPress` is a
// function and cannot cross the wire, so the host keeps it and hands the tree an
// action id instead — the host owns the invocation (铁律 2).
// Action ids are assigned *per render, by traversal position* (`AbovePrompt:2`),
// not from a global counter. Two reasons, both found by test:
//   * a counter makes every re-render look like a different tree, so the plugin's
//     "did the band change?" dedupe never fires;
//   * the registry then grows forever on a host that re-renders each turn.
let renderSeq = 0
const pendingActions = new Map() // this render only: placeholder seq -> handler
// Live actions, **keyed by the surface they belong to**. A render may only
// replace its own bucket, never the whole registry: the plugin pushes several
// slots per hook (`AbovePrompt`, then each open pane, then `AssistantMessage`),
// so a single registry cleared on every render left the earlier surfaces
// pointing at nothing — a band or pane button answered `unknown_action` as soon
// as any later slot was drawn. Because an unchanged tree is deduped by the
// plugin and never redrawn, those buttons then stayed dead for the rest of the
// turn instead of recovering on the next render.
const actionBuckets = new Map() // bucketKey -> string[] of ids it owns
const actionIndex = new Map() // stable id -> handler
// Panes a mod has asked the surface to place: paneId -> mod. One entry per open
// pane, so Python can render each one's `ui.render {component:'Pane'}`.
const openPanes = new Map()

function node(type, props) {
  const { children, ...rest } = props || {}
  const out = { type, props: rest }
  if (children !== undefined) out.children = Array.isArray(children) ? children : [children]
  return out
}

const ELEMENTS = Object.freeze({
  Text: (props) => node('Text', props),
  Box: (props) => node('Box', props),
  Markdown: (props) => node('Markdown', props),
  Code: (props) => node('Code', props),
  Button: (props = {}) => {
    const { onPress, ...rest } = props
    if (typeof onPress === 'function') {
      // A placeholder; `ui.render` remaps it to a stable positional id once the
      // whole tree is known.
      const seq = ++renderSeq
      pendingActions.set(seq, onPress)
      rest.action = `#${seq}`
    }
    return node('Button', rest)
  },
})

// ── per-mod in-memory state (`$.state`) ─────────────────────────────────────
const stateByMod = new Map()

function stateFor(mod) {
  let bag = stateByMod.get(mod)
  if (!bag) {
    bag = new Map()
    stateByMod.set(mod, bag)
  }
  return {
    get: (key) => bag.get(String(key)),
    set: (key, value) => {
      bag.set(String(key), value)
      return true
    },
    delete: (key) => bag.delete(String(key)),
    keys: () => [...bag.keys()],
    update: (key, fn) => {
      const next = typeof fn === 'function' ? fn(bag.get(String(key))) : fn
      bag.set(String(key), next)
      return next
    },
  }
}

// ── $ surface (M0: plugin / clock / ui / telemetry) ────────────────────────
function emitLog(mod, level, args) {
  const message = args
    .map((a) => (typeof a === 'string' ? a : JSON.stringify(a)))
    .join(' ')
  // Notification (no id) — the parent does not reply to these.
  try {
    send({ method: 'log', params: { mod, level, message } })
  } catch {
    /* stdout already gone; nothing useful to do */
  }
}

function makeDollar(mod, root) {
  const logAt = (level) => (...args) => emitLog(mod, level, args)
  // Every privileged call carries the caller's identity: gates are per-mod
  // (`$.store` is a per-mod namespace, and per-mod authorization is M1's job).
  const ask = (method, params) => callParent(method, { ...params, _mod: mod })
  return {
    // Explicit (not shorthand) so tests/test_mods_wired_surface.py can see the
    // member in the source it advertises.
    plugin: { name: mod, root: root },
    clock: {
      now: () => Date.now(),
      sleep: (ms) => new Promise((r) => setTimeout(r, Number(ms) || 0)),
      after: (ms, fn) => setTimeout(fn, Number(ms) || 0),
      every: (ms, fn) => setInterval(fn, Number(ms) || 0),
    },
    // `$.ui.log` is what real guard mods call; `$.telemetry.*` is the other
    // documented logging surface. Both land in the host log stream.
    ui: {
      log: logAt('info'),
      notice: logAt('info'),
      toast: logAt('info'),
      // The element factories. Real mods destructure these, so this is the whole
      // render contract: no SDK package needed for a mod that only draws.
      resolve: () => ELEMENTS,
      // A redraw request. The host renders on its own schedule (Python asks), so
      // this only needs to be callable and counted.
      invalidate: () => {
        invalidations += 1
        return true
      },
      // Pane placement: the host records the request and the surface opens a
      // tab for it. Returning `isPlaced` truthy is what tells a mod *not* to
      // fall back to the band.
      open: (spec = {}) => {
        const id = String(spec?.id || spec?.key || '')
        if (!id) return { isPlaced: false, reason: 'pane spec needs an id' }
        openPanes.set(id, { mod, spec: { ...spec } })
        return { isPlaced: true, id }
      },
      close: (spec = {}) => {
        const id = String(spec?.id || '')
        return openPanes.delete(id)
      },
    },
    telemetry: {
      log: (name, data) => emitLog(mod, 'info', [name, data ?? '']),
      mark: (name, data) => emitLog(mod, 'info', [name, data ?? '']),
    },
    // Privileged members round-trip to Python, which owns every gate. These are
    // the reentrancy path: the mod awaits them from inside a hook handler while
    // our own `tool.call` request is still outstanding on the same pipe.
    session: {
      cwd: async () => {
        const r = await ask('gate.session.cwd', {})
        return r?.cwd ?? ''
      },
      id: async () => {
        const r = await ask('gate.session.id', {})
        return r?.id ?? ''
      },
    },
    fs: {
      read: async (path) => {
        const r = await ask('gate.fs.read', { path: String(path) })
        if (r?.error) throw new Error(r.error)
        return r?.text ?? ''
      },
      exists: async (path) => {
        const r = await ask('gate.fs.exists', { path: String(path) })
        if (r?.error) return false
        return !!r?.exists
      },
      write: async (path, text) => {
        const r = await ask('gate.fs.write', { path: String(path), text: String(text ?? '') })
        if (r?.error) throw new Error(r.error)
        return true
      },
      list: async (path) => {
        const r = await ask('gate.fs.list', { path: String(path ?? '.') })
        if (r?.error) throw new Error(r.error)
        return r?.entries ?? []
      },
      stat: async (path) => {
        const r = await ask('gate.fs.stat', { path: String(path) })
        if (r?.error) throw new Error(r.error)
        return r?.stat ?? null
      },
    },
    // Session-scoped KV, kept in the host process (one host per agent, so it
    // lives as long as the agent does — Claude Code scopes it to the session;
    // the difference is recorded in the matrix).
    state: stateFor(mod),
    // The four capabilities that leave the mod's own world. Default-deny: Python
    // answers with a named refusal unless the user granted it for this mod.
    // (Not a sandbox — a mod can reach `node:fs` without asking; see the matrix.)
    process: {
      run: async (command, args, options) => {
        const r = await ask('gate.process.run', {
          command: String(command ?? ''),
          args: Array.isArray(args) ? args.map(String) : [],
          timeout_ms: Number(options?.timeoutMs) || 0,
        })
        if (r?.error) throw new Error(r.error)
        return { code: r.code, stdout: r.stdout, stderr: r.stderr }
      },
    },
    http: {
      fetch: async (url, options) => {
        const target = typeof url === 'string' ? url : String(url?.url ?? '')
        const r = await ask('gate.http.fetch', {
          url: target,
          method: String(options?.method || 'GET').toUpperCase(),
          body: options?.body ?? null,
        })
        if (r?.error) throw new Error(r.error)
        return { status: r.status, text: r.text }
      },
    },
    env: {
      get: (key) => process.env[String(key)],
      set: async (key, value) => {
        const r = await ask('gate.env.set', { key: String(key), value: String(value ?? '') })
        if (r?.error) throw new Error(r.error)
        return true
      },
    },
    // Slash commands. `register` puts the mod's command into the agent's command
    // registry; `list` is what quick-buttons reads to build its panel.
    command: {
      register: async (spec) => {
        const r = await ask('gate.command.register', spec || {})
        if (r?.error) throw new Error(r.error)
        return r?.command ?? null
      },
      list: async () => {
        const r = await ask('gate.command.list', {})
        if (r?.error) throw new Error(r.error)
        return r?.commands ?? []
      },
      run: async (name, ...args) => {
        // Two hops on purpose: the mod asks Python, Python asks the host, the
        // host runs the `command.run` chain — so the invocation stays owned by
        // the host and every mod sees the same dispatch.
        const r = await ask('gate.command.run', { name: String(name || ''), args })
        if (r?.error) throw new Error(r.error)
        return r?.verdict ?? null
      },
    },
    // Per-mod persisted KV (`data/mods/store/<mod>.json`, 4 MiB cap).
    store: {
      get: async (key) => {
        const r = await ask('gate.store.get', { key: String(key) })
        if (r?.error) throw new Error(r.error)
        return r?.value
      },
      set: async (key, value) => {
        const r = await ask('gate.store.set', { key: String(key), value })
        if (r?.error) throw new Error(r.error)
        return true
      },
      delete: async (key) => {
        const r = await ask('gate.store.delete', { key: String(key) })
        if (r?.error) throw new Error(r.error)
        return true
      },
      keys: async () => {
        const r = await ask('gate.store.keys', {})
        if (r?.error) throw new Error(r.error)
        return r?.keys ?? []
      },
    },
  }
}

// ── $ tracing (diagnostic; off unless asked for) ────────────────────────────
// Real mods swallow `$` failures on purpose ("recording must never stop the
// edit"), so a handler that threw nothing may still have reached for a member
// we do not serve. With OPENSQUAD_MODS_HOST_TRACE=1 every miss is recorded and
// reported through `host.stats`.
const TRACE = process.env.OPENSQUAD_MODS_HOST_TRACE === '1'
const missingDollar = Object.create(null)
let invalidations = 0

function traceDollar(dollar) {
  const miss = (name) => {
    missingDollar[name] = (missingDollar[name] || 0) + 1
    return undefined
  }
  const unknownNamespace = (ns) =>
    new Proxy(
      {},
      {
        get(_t, prop) {
          if (typeof prop === 'symbol') return undefined
          return miss(`${ns}.${String(prop)}`)
        },
      },
    )
  const out = {}
  for (const key of Object.keys(dollar)) {
    out[key] = new Proxy(dollar[key], {
      get(target, prop) {
        if (prop in target) return target[prop]
        if (typeof prop === 'symbol') return undefined
        return miss(`${key}.${String(prop)}`)
      },
    })
  }
  return new Proxy(out, {
    get(target, prop) {
      if (prop in target) return target[prop]
      if (typeof prop === 'symbol') return undefined
      return unknownNamespace(String(prop))
    },
  })
}

// ── JSX runtime for `.tsx` mods ─────────────────────────────────────────────
//
// sucrase's classic JSX emits `React.createElement(Box, {...}, ...)`, where `Box`
// is the local const a mod got from `$.ui.resolve(e)`. So the only thing missing
// at runtime is the `React` identifier itself — and `createElement` just has to
// call that factory.
globalThis.React = {
  createElement: (type, props, ...children) => {
    const flattened = children.length === 1 ? children[0] : children
    if (type === globalThis.React.Fragment) return node('Box', { ...(props || {}), children: flattened })
    if (typeof type === 'function') return type({ ...(props || {}), children: flattened })
    // An element the host does not hand out (e.g. `Input`): keep the name so
    // Python's whitelist can drop it *with a reason* instead of crashing here.
    return { type: String(type ?? 'unknown'), props: { ...(props || {}) } }
  },
  Fragment: Symbol('mods.fragment'),
}

// ── mod loading ─────────────────────────────────────────────────────────────
const runtime = createRuntime({ log: (level, msg) => emitLog('mods_host', level, [msg]) })
const loaded = []
const diagnostics = []

/** Suffix marking a build artefact next to its source. */
const BUILD_SUFFIX = '.mods-build.mjs'

/**
 * Return the path to import: the source itself, or a transpiled sibling.
 *
 * `.ts` and `.tsx` are both transpiled here rather than relying on Node's native
 * type stripping: that needs Node >=22.6, so a `.ts` mod on Node 20 failed with
 * an obscure syntax error while `.tsx` worked. Going through the vendored sucrase
 * in both cases makes the host's floor a single, lower one (`module.register`,
 * i.e. Node >=20.6) and keeps the two extensions on one code path.
 *
 * The build file is written **next to the source** so the mod's own relative
 * imports (`./model.ts`) still resolve — a cache directory would break them. It
 * is a plain artefact, only rewritten when the source is newer, and nothing in
 * the scanner's view (it reads declared modules, not the directory).
 */
async function prepareModule(absPath) {
  const lower = absPath.toLowerCase()
  const isTsx = lower.endsWith('.tsx')
  if (!isTsx && !lower.endsWith('.ts')) return absPath

  const fs = await import('node:fs')
  const buildPath = absPath.replace(/\.tsx?$/i, BUILD_SUFFIX)
  const src = await fs.promises.stat(absPath)
  try {
    const built = await fs.promises.stat(buildPath)
    if (built.mtimeMs >= src.mtimeMs) return buildPath
  } catch {
    /* no build artefact yet */
  }

  const { transform } = await import('./vendor/sucrase.bundle.mjs')
  const code = await fs.promises.readFile(absPath, 'utf8')
  const out = transform(code, {
    // NOT `imports`: that transform rewrites ESM into CJS (`exports is not
    // defined in ES module scope`). We keep the module syntax and let Node run it.
    transforms: isTsx ? ['jsx', 'typescript'] : ['typescript'],
    jsxRuntime: 'classic',
    production: true,
  }).code
  await fs.promises.writeFile(buildPath, out, 'utf8')
  return buildPath
}

async function loadMod(mod) {
  // The directory name is the stable identity gates are keyed on; the manifest
  // name is only a display label.
  const modId = mod.dir_name || mod.name
  const { name, root, modules } = mod
  const base = makeDollar(modId, root)
  const dollar = TRACE ? traceDollar(base) : base
  const options = { name, root, apiVersion: API_VERSION }
  let registered = 0
  const on = (event, matcher, handler) => {
    const reg = runtime.on(event, matcher, handler, { mod: name, dollar })
    registered += 1
    return reg
  }
  for (const rel of modules || []) {
    let mod
    let target
    try {
      target = pathToFileURL(path.resolve(root, await prepareModule(path.resolve(root, rel)))).href
    } catch (err) {
      diagnostics.push({ mod: name, kind: 'transpile_failed', detail: `${rel}: ${err?.message || err}` })
      continue
    }
    try {
      mod = await import(target)
    } catch (err) {
      diagnostics.push({ mod: name, kind: 'import_failed', detail: `${rel}: ${err?.message || err}` })
      continue
    }
    if (typeof mod.register !== 'function') {
      diagnostics.push({ mod: name, kind: 'no_register_export', detail: rel })
      continue
    }
    try {
      await mod.register(on, options)
    } catch (err) {
      diagnostics.push({ mod: name, kind: 'register_threw', detail: `${rel}: ${err?.message || err}` })
    }
  }
  loaded.push({ name, registered })
  return registered
}

// ── request handling ────────────────────────────────────────────────────────
function toolCallPayload(params) {
  const toolName = String(params.tool_name ?? params.tool ?? '')
  const args = params.arguments && typeof params.arguments === 'object' ? params.arguments : {}
  // Tool-specific fields are spread first so a mod written against the real
  // reference (`e.command` for a Bash-like tool) works; the canonical keys then
  // win, so a tool argument named `tool` cannot shadow the tool name.
  return Object.freeze({
    ...args,
    tool: toolName,
    tool_name: toolName,
    arguments: args,
    agent_id: params.agent_id ?? null,
  })
}

async function handle(msg) {
  const { id, method, params = {} } = msg
  switch (method) {
    case 'ping':
      return respond(id, {
        pong: true,
        host: 'mods_host',
        node: process.version,
        pid: process.pid,
        apiVersion: API_VERSION,
      })
    case 'init': {
      if (params.apiVersion !== undefined && params.apiVersion !== API_VERSION) {
        diagnostics.push({
          mod: '-',
          kind: 'api_version_mismatch',
          detail: `host=${API_VERSION} caller=${params.apiVersion}`,
        })
      }
      const mods = Array.isArray(params.mods) ? params.mods : []
      let registered = 0
      for (const mod of mods) registered += await loadMod(mod)
      return respond(id, { loaded, registered, events: runtime.events(), diagnostics })
    }
    case 'clock.sleep':
      // Also the S0 timeout probe: the parent asks for longer than its budget.
      {
        const ms = Math.max(0, Math.min(Number(params.ms) || 0, 30000))
        await new Promise((r) => setTimeout(r, ms))
        return respond(id, { sleptMs: ms })
      }
    case 'tool.call': {
      const verdict = await runtime.run('tool.call', toolCallPayload(params))
      return respond(id, verdict)
    }
    case 'turn.step':
    case 'session.start':
    case 'session.end':
    case 'turn.start':
    case 'turn.complete': {
      // `agentId` is deliberately absent: it means "an agent-originated turn",
      // which we cannot tell apart yet (M1). Claiming it would suppress mods
      // that only want main-loop turns — e.g. replay-theater's turn.complete.
      const verdict = await runtime.run(method, Object.freeze({ ...params }))
      return respond(id, verdict)
    }
    case 'ui.render': {
      const component = String(params.component || '')
      renderSeq = 0
      pendingActions.clear()
      // Every matching mod draws its own row — collect, don't short-circuit.
      const geometry = {
        maxRows: Number(params.maxRows) || 20,
        bodyColumns: Number(params.bodyColumns) || 100,
      }
      const nodes = await runtime.collect(
        'ui.render',
        // Both shapes on purpose: real mods disagree — replay-theater reads
        // `e.maxRows`, cache-meter reads `e.props.bodyColumns`.
        Object.freeze({
          component,
          requestId: String(params.requestId || ''),
          ...geometry,
          props: { component, ...geometry },
        }),
      )
      // Bind this tree's buttons, replacing only *this surface's* bucket. Ids
      // carry the slot and the pane instance so two panes cannot collide now
      // that other surfaces survive.
      const bucketKey = `${component}\u0000${String(params.requestId || '')}`
      // Ids keep their historical shape for slots (`AbovePrompt:1`) so the
      // wire contract does not change; a pane instance, which coexists with
      // its siblings, adds its own id to stay unique across panes.
      const idSpace = params.requestId ? `${component}:${String(params.requestId)}` : component
      for (const stale of actionBuckets.get(bucketKey) || []) actionIndex.delete(stale)
      const owned = []
      let bound = 0
      const bind = (item) => {
        if (!item || typeof item !== 'object') return
        const action = item.props?.action
        if (item.type === 'Button' && typeof action === 'string' && action.startsWith('#')) {
          const handler = pendingActions.get(Number(action.slice(1)))
          const stable = `${idSpace}:${++bound}`
          if (typeof handler === 'function') {
            actionIndex.set(stable, handler)
            owned.push(stable)
          }
          item.props.action = stable
        }
        for (const child of item.children || []) bind(child)
      }
      for (const each of nodes) bind(each)
      actionBuckets.set(bucketKey, owned)
      return respond(id, { nodes, invalidations })
    }
    case 'action.invoke': {
      const fn = actionIndex.get(String(params.action || ''))
      if (typeof fn !== 'function') return respondError(id, 'unknown_action', String(params.action || ''))
      // The host owns the invocation and its reporting (铁律 2). The callback
      // closes over its own `$`, so it is called with no arguments.
      const verdict = await fn()
      return respond(id, { ok: true, verdict: verdict ?? null })
    }
    case 'command.dispatch': {
      // Run the mods' `command.run` handlers for one command name. This is how a
      // user-invoked slash command reaches the mod, and how a mod runs another
      // command itself.
      const verdict = await runtime.run(
        'command.run',
        Object.freeze({
          command: String(params.command || ''),
          args: Array.isArray(params.args) ? params.args : [],
          sid: String(params.sid || ''),
        }),
      )
      return respond(id, { verdict: verdict && typeof verdict === 'object' ? verdict : null })
    }
    case 'pane.list':
      return respond(id, {
        panes: [...openPanes.entries()].map(([paneId, entry]) => ({ id: paneId, mod: entry.mod })),
      })
    case 'host.stats':
      return respond(id, {
        traced: TRACE,
        invocations: runtime.stats().invocations,
        missingDollar: { ...missingDollar },
        actions: actionIndex.size,
        invalidations,
      })
    default:
      return respondError(id, 'bad_method', `unknown method: ${method}`)
  }
}

// ── stdio loop ──────────────────────────────────────────────────────────────
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity })

rl.on('line', (line) => {
  const text = line.trim()
  if (!text) return
  let msg
  try {
    msg = JSON.parse(text)
  } catch (err) {
    emitLog('mods_host', 'error', [`bad JSON frame: ${err?.message || err}`])
    return
  }
  if (!msg || typeof msg !== 'object') return

  if (msg.id !== undefined && pending.has(msg.id)) {
    const p = pending.get(msg.id)
    pending.delete(msg.id)
    if (msg.error) p.reject(new Error(msg.error.message || 'parent call failed'))
    else p.resolve(msg.result)
    return
  }
  if (typeof msg.method === 'string') {
    handle(msg).catch((err) => respondError(msg.id, 'internal', String(err?.message || err)))
  }
})

rl.on('close', () => {
  // stdin EOF means the parent is gone. Exit so we can never be orphaned; the
  // Python side also kills us on unload (two independent reaping paths).
  process.exit(0)
})

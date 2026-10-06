// Event chain runtime for the mod host.
//
// Contract verified against the authoritative reference
// (code.claude.com/docs/zh-CN/plugins/mods/reference) — see
// docs/mods-bridge-m0.md §2.5:
//
//   export function register(on, options)
//   on(eventName, matcher?, handler) -> { catch(handler) }
//   handler: async ($, e, next)
//
// `next` is the downstream middleware: a handler that does NOT call it
// short-circuits the chain (that is how `{deny: reason}` refuses). `e` is the
// frozen event payload — rewrite with `next({ ...e, field })`.
//
// cordis replaces this in M0.5 (docs/mods-bridge-m0.md §1.4); this file exists
// so a protocol bug cannot be confused with a cordis RC bug.

const CATCH_BUDGET_MS = 1000 // reference: `.catch` gets 1s

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
      timer.unref?.()
    }),
  ])
}

function matches(matcher, e) {
  if (!matcher || typeof matcher !== 'object') return true
  return Object.entries(matcher).every(([key, want]) => {
    if (want === undefined) return true
    return String(e?.[key]) === String(want)
  })
}

export function createRuntime({ log } = {}) {
  /** @type {Map<string, Array<object>>} */
  const handlers = new Map()
  // Diagnostic only: lets the smoke script distinguish "the mod never ran" from
  // "the mod ran and quietly did nothing" — real mods swallow $ failures by
  // design, so a throw count is not evidence that a handler was reached.
  const invocations = Object.create(null)

  function on(event, matcher, handler, { mod, dollar } = {}) {
    if (typeof matcher === 'function') {
      // `on(event, handler)` — matcher omitted.
      handler = matcher
      matcher = undefined
    }
    if (typeof event !== 'string' || !event) {
      throw new TypeError('on(): event must be a non-empty string')
    }
    if (typeof handler !== 'function') {
      throw new TypeError(`on(${event}): handler must be a function`)
    }
    const entry = { event, mod, matcher, fn: handler, catcher: null, dollar }
    if (!handlers.has(event)) handlers.set(event, [])
    handlers.get(event).push(entry)
    // The reference returns a registration object whose only method is .catch.
    return {
      catch(fn) {
        if (typeof fn === 'function') entry.catcher = fn
        return this
      },
    }
  }

  function modsFor(event) {
    return handlers.get(event) || []
  }

  function events() {
    return [...handlers.keys()]
  }

  async function invoke(entry, e, next) {
    invocations[entry.event] = (invocations[entry.event] || 0) + 1
    try {
      return await entry.fn(entry.dollar, e, next)
    } catch (err) {
      log?.('error', `mod ${entry.mod} threw on ${entry.event}: ${err?.message || err}`)
      if (entry.catcher) {
        try {
          return await withTimeout(
            Promise.resolve(entry.catcher(entry.dollar, e, next)),
            CATCH_BUDGET_MS,
            `${entry.event} .catch`,
          )
        } catch (err2) {
          log?.('error', `mod ${entry.mod} .catch failed: ${err2?.message || err2}`)
        }
      }
      // Swallow: one bad mod must not break the chain for the others.
      return undefined
    }
  }

  // Middleware chain. Returns the verdict, or `{ next: true }` when no mod
  // produced one — the caller then keeps its own behaviour.
  async function run(event, payload) {
    const chain = modsFor(event)
    let index = 0
    const next = async (e) => {
      while (index < chain.length) {
        const entry = chain[index++]
        if (!matches(entry.matcher, e)) continue
        return await invoke(entry, e, next)
      }
      return undefined
    }
    const verdict = await next(payload)
    return verdict && typeof verdict === 'object' ? verdict : { next: true }
  }

  // Collect-mode, for `ui.render`: every matching handler draws its own row, so
  // there is no chain to short-circuit. Returns every non-undefined verdict.
  async function collect(event, payload) {
    const out = []
    for (const entry of modsFor(event)) {
      if (!matches(entry.matcher, payload)) continue
      const verdict = await invoke(entry, payload, async () => undefined)
      if (verdict !== undefined) out.push(verdict)
    }
    return out
  }

  function stats() {
    return { invocations: { ...invocations } }
  }

  return { on, run, collect, modsFor, events, stats }
}

// Fixture mod for S1/S2 — written in the canonical module shape:
//
//   export function register(on, options)
//   on(eventName, matcher?, handler) -> { catch(handler) }
//   handler: async ($, e, next)      // no `next(e)` call == short-circuit
//
// Three handlers on one event, so the chain's three behaviours are all covered:
// deny, throw→.catch, and pass-through.

const DENIED = 'demo.echo'
const EXPLODES = 'demo.boom'

export function register(on, options) {
  // A mod-contributed slash command: registered at session start, dispatched by
  // the host, answered with `{ text }` which the agent speaks.
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'demo', description: 'say hello from the fixture mod' })
    return next(e)
  })

  on('command.run', { command: 'demo' }, async ($, e) => {
    return { text: `deny-demo: hello (args=${(e.args || []).join('|')})` }
  })

  // 1. deny — and prove `$` is usable while handling (this log is a plain
  //    notification; awaiting a host gate is S2's job).
  on('tool.call', { tool: DENIED }, async ($, e, next) => {
    $.ui.log(`deny-demo: blocking ${e.tool}`)
    return { deny: `deny-demo: ${e.tool} is blocked by the fixture mod` }
  }).catch(async ($, e, next) => {
    return { deny: `deny-demo: guard itself failed for ${e.tool}` }
  })

  // 2. throw — the .catch fallback must decide, not the chain.
  on('tool.call', { tool: EXPLODES }, async ($, e, next) => {
    throw new Error('deny-demo: intentional explosion')
  }).catch(async ($, e, next) => {
    $.ui.log('deny-demo: .catch fired')
    return { deny: `deny-demo: denied via .catch for ${e.tool}` }
  })

  // 3. tool-specific fields are spread onto the event, like the reference does
  //    for a Bash-like tool. A guard written against `e.command` must work.
  on('tool.call', { tool: 'demo.shell' }, async ($, e, next) => {
    if (typeof e.command !== 'string') return { deny: 'deny-demo: e.command missing' }
    return { deny: `deny-demo: saw command ${e.command}` }
  })

  // 4. reentrancy — the handler awaits a privileged member, which round-trips
  //    host→Python→host *while* our own tool.call request is still outstanding.
  //    A non-duplex transport (or a mis-ordered frame dispatcher) deadlocks here.
  on('tool.call', { tool: 'demo.cwd' }, async ($, e, next) => {
    const cwd = await $.session.cwd()
    return { deny: `deny-demo: cwd=${cwd}` }
  })

  // 5. persistence — per-mod KV must survive between handlers.
  on('tool.call', { tool: 'demo.remember' }, async ($, e, next) => {
    const before = await $.store.get('runs')
    const runs = (typeof before === 'number' ? before : 0) + 1
    await $.store.set('runs', runs)
    const keys = await $.store.keys()
    return { deny: `deny-demo: runs=${runs} keys=${keys.join('+')}` }
  })

  // 6. the band: elements come from `$.ui.resolve(e)`, exactly like real mods.
  //    Reading state here also exercises a *third* reentrancy site: a host gate
  //    awaited from inside a render handler.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const runs = Number(await $.store.get('runs')) || 0
    const pinged = Number(await $.store.get('pinged')) || 0
    return Box({
      flexDirection: 'row',
      gap: 2,
      children: [
        Text({ bold: true, color: 'magenta', children: 'deny-demo' }),
        Text({ children: `slot=${e.component} runs=${runs} pinged=${pinged}` }),
        // An attribute outside the whitelist, to prove it is dropped not fatal.
        Text({ children: 'stray', notARealProp: 1 }),
        // Refused element, to prove it is dropped with a reason.
        { type: 'Client', props: { module: './evil.js' } },
        // `onPress` cannot cross the wire: the host hoists it to an action id and
        // calls it back with no arguments (it closes over its own `$`).
        Button({
          key: 'ping',
          label: 'Ping',
          onPress: async () => {
            const n = Number(await $.store.get('pinged')) || 0
            await $.store.set('pinged', n + 1)
            return 'pong'
          },
        }),
      ],
    })
  })

  // 6b. session state — in-memory, per mod, lives as long as the host does.
  on('tool.call', { tool: 'demo.state' }, async ($, e, next) => {
    await $.state.set('counter', (Number($.state.get('counter')) || 0) + 1)
    $.state.update('label', () => 'labelled')
    const keys = $.state.keys().sort()
    return { deny: `deny-demo: state=${$.state.get('counter')} keys=${keys.join('+')}` }
  })

  // 7. pass-through — matcher omitted, must simply delegate downstream.
  on('tool.call', async ($, e, next) => {
    return next(e)
  })
}

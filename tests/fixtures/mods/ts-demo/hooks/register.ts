// Fixture for P6-B1: a TypeScript mod, run as-is on Node 24's type stripping,
// importing the `claude-code` SDK surface that the host answers with a shim.
//
// Type-only imports are erased at runtime; the value import must resolve.
import { atom, memberOf, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

type Counter = { hits: number }

const hits = atom({ plugin: 'ts-demo', key: 'hits' } as const, 0)
const seen = atom({ plugin: 'ts-demo', key: 'seen' } as const, 0)

export const register: Register = (on: (event: string, matcher: unknown, handler: unknown) => void) => {
  on('tool.call', { tool: 'demo.typed' }, async ($: EngineInterface, e: { tool: string }, next: () => unknown) => {
    const before: number = read(hits) ?? 0
    update(hits, (value: number) => (value ?? 0) + 1)
    const after: number = read(hits) ?? 0
    // `memberOf` gives this member its own cell, reached with the `$`-first shape.
    // `elsewhere` is a *different* member, read to prove the cells are not shared.
    const member = memberOf(seen, e)
    const mine: number = read($, member) ?? 0
    update($, member, mine + 1)
    const elsewhere: number = read($, memberOf(seen, { requestId: 'demo-elsewhere' })) ?? 0
    return {
      deny: `ts-demo: typed ts works, hits ${before} -> ${after}, seen ${mine} -> ${mine + 1}, elsewhere ${elsewhere}`,
    }
  })
}

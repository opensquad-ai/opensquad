// Fixture for P6-B2: JSX in a mod module.
//
// The element names are local consts from `$.ui.resolve(e)`, exactly like the
// real `.tsx` mods — so classic JSX compiles to `React.createElement(Box, …)`
// and the host only has to provide `React`.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

const hits = atom({ plugin: 'tsx-demo', key: 'hits' } as const, 0)

export const register: Register = (on: any) => {
  on('tool.call', { tool: 'demo.jsx' }, async ($: EngineInterface, e: any, next: any) => {
    update(hits, (v: number) => (v ?? 0) + 1)
    return { deny: `tsx-demo: jsx works, hits=${read(hits)}` }
  })

  on('ui.render', { component: 'AbovePrompt' }, ($: EngineInterface, e: any) => {
    const { Box, Text } = $.ui.resolve(e)
    const label: string = `tsx-demo hits=${read(hits) ?? 0}`
    return (
      <Box flexDirection="row" gap={2}>
        <Text bold color="cyan">
          {label}
        </Text>
      </Box>
    )
  })
}

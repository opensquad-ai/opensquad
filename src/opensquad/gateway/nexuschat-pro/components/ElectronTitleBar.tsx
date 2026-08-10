import React, { useCallback, useEffect, useState } from 'react'
import { Minus, Square, X } from 'lucide-react'

const MENU_ITEMS = ['File', 'Edit', 'View', 'Window', 'Help'] as const

export const ElectronTitleBar: React.FC = () => {
  const env = window.electronEnv
  const [isMaximized, setIsMaximized] = useState(false)

  useEffect(() => {
    if (!env?.isElectron || env.platform !== 'win32') return
    let cancelled = false
    env.isMaximized?.().then((value) => {
      if (!cancelled) setIsMaximized(value)
    })
    const unsubscribe = env.onMaximizedChanged?.((value) => setIsMaximized(value))
    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [env])

  const popupMenu = useCallback((label: string) => {
    void window.electronEnv?.popupMenu?.(label.toLowerCase())
  }, [])

  const windowControl = useCallback(async (action: 'minimize' | 'maximize' | 'close') => {
    await window.electronEnv?.windowControl?.(action)
    if (action === 'maximize' && window.electronEnv?.isMaximized) {
      setIsMaximized(await window.electronEnv.isMaximized())
    }
  }, [])

  if (!env?.isElectron || env.platform !== 'win32') return null

  return (
    <div
      className="electron-titlebar h-8 flex items-stretch shrink-0 bg-bgLight text-textMain border-b border-border transition-colors duration-300"
      style={{ WebkitAppRegion: 'drag', backgroundColor: 'rgb(var(--color-bg))' }}
    >
      <div className="flex items-stretch h-full" style={{ WebkitAppRegion: 'no-drag' }}>
        <div
          className="flex items-center justify-center w-8 h-full shrink-0 ml-1 mr-0.5"
          title="OpenSquad"
        >
          <div
            className="flex items-center justify-center w-[22px] h-[22px] rounded-[5px] border border-border/50"
            style={{ backgroundColor: 'color-mix(in srgb, rgb(var(--color-text-main)) 5%, rgb(var(--color-bg)))' }}
          >
            <img
              src="/logo.svg"
              alt="OpenSquad"
              className="w-3.5 h-3.5 rounded-[3px] select-none pointer-events-none"
              draggable={false}
            />
          </div>
        </div>
        {MENU_ITEMS.map((label) => (
          <button
            key={label}
            type="button"
            className="px-3 h-full text-[12px] hover:bg-primary/10 transition-colors"
            onClick={() => popupMenu(label)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex-1 min-w-0" />

      <div className="flex items-stretch h-full" style={{ WebkitAppRegion: 'no-drag' }}>
        <button
          type="button"
          aria-label="Minimize"
          className="w-11 h-full inline-flex items-center justify-center hover:bg-primary/10 transition-colors"
          onClick={() => void windowControl('minimize')}
        >
          <Minus size={14} strokeWidth={1.75} />
        </button>
        <button
          type="button"
          aria-label={isMaximized ? 'Restore' : 'Maximize'}
          className="w-11 h-full inline-flex items-center justify-center hover:bg-primary/10 transition-colors"
          onClick={() => void windowControl('maximize')}
        >
          <Square size={12} strokeWidth={1.75} />
        </button>
        <button
          type="button"
          aria-label="Close"
          className="w-11 h-full inline-flex items-center justify-center hover:bg-red-500 hover:text-white transition-colors"
          onClick={() => void windowControl('close')}
        >
          <X size={14} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  )
}

import React from 'react'
import { ElectronTitleBar } from './ElectronTitleBar'

interface ElectronShellProps {
  children: React.ReactNode
  className?: string
}

export const ElectronShell: React.FC<ElectronShellProps> = ({ children, className = '' }) => (
  <div
    className={`h-full w-full flex flex-col overflow-hidden bg-bgLight text-textMain transition-colors duration-soft ease-soft mobile-safe-shell font-sans ${className}`.trim()}
    style={{ backgroundColor: 'var(--color-bg)' }}
  >
    <ElectronTitleBar />
    <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
  </div>
)

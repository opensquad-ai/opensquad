import { app, dialog, Menu } from 'electron'

export type ElectronMenuId = 'file' | 'edit' | 'view' | 'window' | 'help'

const MENU_IDS: ElectronMenuId[] = ['file', 'edit', 'view', 'window', 'help']

export function buildElectronPopupMenus(): Record<ElectronMenuId, Menu> {
  return {
    file: Menu.buildFromTemplate([
      { role: 'close' },
      { type: 'separator' },
      { role: 'quit' },
    ]),
    edit: Menu.buildFromTemplate([
      { role: 'undo' },
      { role: 'redo' },
      { type: 'separator' },
      { role: 'cut' },
      { role: 'copy' },
      { role: 'paste' },
      { role: 'selectAll' },
    ]),
    view: Menu.buildFromTemplate([
      { role: 'reload' },
      { role: 'forceReload' },
      { role: 'toggleDevTools' },
      { type: 'separator' },
      { role: 'resetZoom' },
      { role: 'zoomIn' },
      { role: 'zoomOut' },
      { type: 'separator' },
      { role: 'togglefullscreen' },
    ]),
    window: Menu.buildFromTemplate([
      { role: 'minimize' },
      { role: 'zoom' },
      { type: 'separator' },
      { role: 'close' },
    ]),
    help: Menu.buildFromTemplate([
      {
        label: 'About NexusChat Pro',
        click: () => {
          dialog.showMessageBox({
            type: 'info',
            title: 'About',
            message: 'NexusChat Pro',
            detail: `OpenSquad Desktop Client\nVersion ${app.getVersion()}`,
          })
        },
      },
    ]),
  }
}

export function isElectronMenuId(value: string): value is ElectronMenuId {
  return (MENU_IDS as string[]).includes(value)
}

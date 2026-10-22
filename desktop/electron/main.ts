import { app, BrowserWindow, Notification, ipcMain, shell } from 'electron'
import fs from 'node:fs'
import path from 'node:path'

app.setName('ForgeAgent')

function migrateProfile() {
  const previous = path.join(app.getPath('appData'), 'KoreAgent Next')
  const current = app.getPath('userData')
  if (!fs.existsSync(previous)) return
  for (const folder of ['Local Storage', 'IndexedDB']) {
    const source = path.join(previous, folder)
    const destination = path.join(current, folder)
    if (fs.existsSync(source) && !fs.existsSync(destination)) {
      fs.mkdirSync(current, { recursive: true })
      fs.cpSync(source, destination, { recursive: true })
    }
  }
}

function surfaceUrl() {
  return process.env.VITE_DEV_SERVER_URL || (!app.isPackaged ? 'http://127.0.0.1:5173' : '')
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 980, minHeight: 650, title: 'ForgeAgent',
    backgroundColor: '#eef3f8',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  })
  const developmentUrl = surfaceUrl()
  if (developmentUrl) win.loadURL(developmentUrl)
  else win.loadFile(path.join(__dirname, '../../dist/index.html'))
}

app.whenReady().then(() => {
  try { migrateProfile() } catch (error) { console.warn('Could not migrate desktop profile:', error) }
  ipcMain.handle('open-external', (_e, url: string) => shell.openExternal(url))
  ipcMain.handle('notify', (_e, title: string, body: string) => new Notification({ title, body }).show())
  createWindow(); app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow() })
})
app.on('window-all-closed', () => app.quit())

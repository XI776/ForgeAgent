import { contextBridge, ipcRenderer } from 'electron'
contextBridge.exposeInMainWorld('forgeAgent', {
  openExternal: (url: string) => ipcRenderer.invoke('open-external', url),
  notify: (title: string, body: string) => ipcRenderer.invoke('notify', title, body),
})

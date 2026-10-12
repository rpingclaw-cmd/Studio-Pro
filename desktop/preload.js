// The few desktop abilities the page may use: the app's own file window, "show in folder",
// the real path of a dropped file, and the version. Nothing else from the computer is exposed.
'use strict';
const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('studioApp', {
  pick: (kind) => ipcRenderer.invoke('pick', String(kind || 'any')),
  showItem: (p) => ipcRenderer.invoke('showItem', String(p || '')),
  pathOf: (file) => { try { return webUtils.getPathForFile(file); } catch (e) { return ''; } },
  version: (process.argv.find((a) => a.startsWith('--studio-version=')) || '').split('=')[1] || '',
});

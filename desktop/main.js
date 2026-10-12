// Studio Pro — the desktop app. It starts its own Studio Engine (Python, bundled), waits until it answers,
// then shows the editor (served by the engine on 127.0.0.1 only). Closing the window stops the engine.
'use strict';
const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const ROOT = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
const RES = (...p) => path.join(ROOT, ...p);
// the bundled Python and tools: inside the installed app, or desktop/build/ while developing
const BUNDLE = (...p) => (app.isPackaged ? RES(...p) : path.join(__dirname, 'build', ...p));
const IS_WIN = process.platform === 'win32';
const TOKEN = crypto.randomBytes(24).toString('base64url');
let engine = null, port = 0, win = null, quitting = false, restarts = 0;
const logDir = path.join(app.getPath('userData'), 'logs');
fs.mkdirSync(logDir, { recursive: true });
const log = fs.createWriteStream(path.join(logDir, 'engine.log'), { flags: 'a' });
const say = (t) => log.write(`[${new Date().toISOString()}] ${t}\n`);

if (!app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

/** The Python that runs the engine: the bundled one in the installed app, `uv` while developing. */
function engineCommand() {
  const args = ['-m', 'studio_engine.server', '--app', '--port', String(port)];
  const bundled = IS_WIN ? BUNDLE('python', 'python.exe') : BUNDLE('python', 'bin', 'python3');
  if (fs.existsSync(bundled)) return { cmd: bundled, args, cwd: RES('engine') };
  return { cmd: 'uv', args: ['run', '--project', RES('engine'), 'python', ...args], cwd: RES('engine') };
}

function startEngine() {
  const { cmd, args, cwd } = engineCommand();
  const env = {
    ...process.env,
    STUDIO_ENGINE_TOKEN: TOKEN,
    STUDIO_UI_DIR: RES('ui'),
    PYTHONPATH: RES('engine'),
    PYTHONNOUSERSITE: '1',
    PYTHONUTF8: '1',
  };
  if (fs.existsSync(BUNDLE('bin'))) env.STUDIO_BIN = BUNDLE('bin');
  say(`starting engine: ${cmd} ${args.join(' ')}`);
  engine = spawn(cmd, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  engine.stdout.on('data', (d) => log.write(d));
  engine.stderr.on('data', (d) => log.write(d));
  engine.on('exit', (code) => {
    say(`engine stopped (code ${code})`);
    engine = null;
    if (quitting) return;
    if (restarts++ < 3) { startEngine(); waitForEngine().then(() => win && win.reload()).catch(fatal); }
    else fatal(new Error('The Studio Engine keeps stopping.'));
  });
}

function hello() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/hello', timeout: 1500 }, (r) => {
      let b = ''; r.on('data', (d) => (b += d)); r.on('end', () => resolve(r.statusCode === 200));
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function waitForEngine(seconds = 90) {
  const t0 = Date.now();
  while (Date.now() - t0 < seconds * 1000) {
    if (await hello()) return;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error('The Studio Engine did not start.');
}

function fatal(e) {
  say('fatal: ' + (e && e.stack || e));
  dialog.showErrorBox('Studio Pro', `${e.message}\n\nDetails are in:\n${path.join(logDir, 'engine.log')}`);
  quitting = true;
  app.quit();
}

const PICK = {
  video: [{ name: 'Videos', extensions: ['mov', 'mp4', 'm4v', 'mkv', 'avi', 'mts', 'm2ts', 'webm'] }],
  audio: [{ name: 'Audio', extensions: ['wav', 'mp3', 'm4a', 'aac', 'flac', 'ogg', 'opus', 'aif', 'aiff'] }],
  transcript: [{ name: 'Transcripts', extensions: ['txt', 'docx', 'srt', 'vtt'] }],
  cover: [{ name: 'Pictures and videos', extensions: ['jpg', 'jpeg', 'png', 'webp', 'mov', 'mp4', 'm4v'] }],
};
ipcMain.handle('pick', async (_e, kind) => {
  const r = await dialog.showOpenDialog(win, { properties: ['openFile'], filters: [...(PICK[kind] || []), { name: 'All files', extensions: ['*'] }] });
  return r.canceled ? '' : r.filePaths[0];
});
ipcMain.handle('showItem', (_e, p) => { if (typeof p === 'string' && fs.existsSync(p)) shell.showItemInFolder(p); });

function menu() {
  const send = (js) => () => win && win.webContents.executeJavaScript(js).catch(() => {});
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: 'File', submenu: [
      { label: 'Choose video…', accelerator: 'CmdOrCtrl+O', click: send("engPick('video')") },
      { label: 'Choose separate audio…', click: send("engPick('audio')") },
      { type: 'separator' },
      { label: 'Preferences…', accelerator: 'CmdOrCtrl+,', click: send('prefsOpen()') },
      { type: 'separator' }, { role: 'quit' } ] },
    { role: 'editMenu' },
    { label: 'View', submenu: [
      { label: 'Editor', accelerator: 'CmdOrCtrl+1', click: send('edOpen()') },
      { label: 'Guided steps', accelerator: 'CmdOrCtrl+2', click: send('edClose()') },
      { type: 'separator' }, { role: 'togglefullscreen' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
      { type: 'separator' }, { role: 'reload' }, { role: 'toggleDevTools' } ] },
    { label: 'Help', submenu: [
      { label: 'Keyboard shortcuts', accelerator: 'F1', click: send("edOpen();document.querySelector('#edHelp').hidden=false") },
      { label: 'Show the log files', click: () => shell.openPath(logDir) },
      { label: 'About Studio Pro', click: () => dialog.showMessageBox(win, { message: `Studio Pro ${app.getVersion()}`, detail: 'Talks and podcasts: sync, clean sound, edit by text, Reels, YouTube.\nFree software (GPL-3.0). Uses FFmpeg, DeepFilterNet, Silero VAD, OpenCV YuNet.' }) } ] },
  ]));
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1024, minHeight: 640, show: false, backgroundColor: '#0d0d0f',
    title: 'Studio Pro', autoHideMenuBar: false,
    icon: fs.existsSync(path.join(__dirname, 'build', 'icon.png')) ? path.join(__dirname, 'build', 'icon.png') : undefined,
    webPreferences: { additionalArguments: [`--studio-version=${app.getVersion()}`], preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  const origin = `http://127.0.0.1:${port}`;
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(origin)) { e.preventDefault(); shell.openExternal(url); } });
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https:\/\//.test(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.once('ready-to-show', () => { win.maximize(); win.show(); });
  await win.loadURL(`${origin}/#engine=${TOKEN}`);
  if (process.env.STUDIO_SELFTEST) selfTest(process.env.STUDIO_SELFTEST);
}

/** Smoke test used by the automatic builds (and by support): the engine answers, the page connects to it,
 *  the editor opens and nothing on the page fails. Writes a screenshot + result, then quits (exit code 0 = good). */
async function selfTest(dir) {
  const errors = [];
  win.webContents.on('console-message', (e) => { if (e.level === 'error') errors.push(e.message); });
  const ev = (js) => win.webContents.executeJavaScript(js).catch((e) => 'ERR ' + e.message);
  const t0 = Date.now();
  let ok = false, info = {};
  while (Date.now() - t0 < 90000) {
    info = await ev(`({on: typeof ENG!=='undefined' && ENG.on, editor: !!document.querySelector('#ed:not([hidden])'),
      status: (document.querySelector('#engStatus')||{}).textContent, hw: typeof ENG!=='undefined' && ENG.hw && ENG.hw.summary})`);
    if (info && info.on && info.editor && info.hw) { ok = true; break; }
    await new Promise((r) => setTimeout(r, 500));
  }
  await new Promise((r) => setTimeout(r, 1500));
  fs.mkdirSync(dir, { recursive: true });
  const img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(dir, 'selftest.png'), img.toPNG());
  const bad = errors.filter((m) => !/favicon/i.test(m));
  const result = { ok: ok && !bad.length, info, errors: bad, version: app.getVersion(), platform: process.platform };
  fs.writeFileSync(path.join(dir, 'selftest.json'), JSON.stringify(result, null, 2));
  say('selftest ' + JSON.stringify(result));
  quitting = true;
  app.exit(result.ok ? 0 : 1);
}

app.whenReady().then(async () => {
  try {
    port = await freePort();
    startEngine();
    menu();
    await waitForEngine();
    await createWindow();
  } catch (e) { fatal(e); }
});
app.on('before-quit', () => {
  quitting = true;
  if (engine) { try { engine.stdin.end(); } catch (e) {} setTimeout(() => { try { engine && engine.kill(); } catch (e) {} }, 2500); }
});
app.on('window-all-closed', () => app.quit());

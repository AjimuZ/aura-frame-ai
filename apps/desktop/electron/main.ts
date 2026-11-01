// AuraFrame AI - Electron main process entry point.
// Creates a single hardened BrowserWindow and registers the typed IPC handlers.
// The renderer (Angular) has no direct filesystem or Node access; everything flows
// through validated IPC channels defined in the ipc/ modules.

import { app, BrowserWindow, dialog, protocol } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { registerFilesystemIpc } from './ipc/filesystem.ipc';
import { registerAnalysisIpc, disposeAnalysisIpc, isAnalysisRunning } from './ipc/analysis.ipc';
import { registerSettingsIpc } from './ipc/settings.ipc';
import { dataRoot } from './ipc/paths';
import { LibraryRegistryStore } from './ipc/library-store';
import { registerLibrariesIpc } from './ipc/libraries.ipc';

const isDev = process.env['AURA_DEV'] === '1';
const DEV_SERVER_URL = 'http://localhost:4200';
const ANGULAR_DIST = path.join(__dirname, '..', 'dist', 'aura-frame', 'browser');

// A privileged custom scheme so the renderer can display local thumbnails without
// exposing the filesystem or weakening webSecurity. Must be declared before app ready.
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'afmedia',
    privileges: { standard: true, secure: true, supportFetchAPI: true, bypassCSP: false },
  },
]);

const MIME_BY_EXT: Record<string, string> = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
};

// Serves a local image file, but only if it resolves inside the working directory.
// This keeps the trust boundary narrow: originals and arbitrary paths are not exposed.
function handleMediaRequest(libraries: LibraryRegistryStore, request: Request): Response {
  const libraryId = new URL(request.url).searchParams.get('libraryId') ?? '';
  const requested = new URL(request.url).searchParams.get('path') ?? '';
  try {
    const resolved = libraries.resolveWorkingFile(libraryId, requested);
    if (
      !resolved ||
      !fs.existsSync(resolved) ||
      !fs.statSync(resolved).isFile()
    ) {
      return new Response('Not found', { status: 404 });
    }
    const mime = MIME_BY_EXT[path.extname(resolved).toLowerCase()] ?? 'application/octet-stream';
    return new Response(fs.readFileSync(resolved), { headers: { 'content-type': mime } });
  } catch {
    return new Response('Not found', { status: 404 });
  }
}

// Builds the main application window with a narrow, sandboxed trust boundary.
function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1280,
    minHeight: 720,
    backgroundColor: '#2D2F42',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  window.once('ready-to-show', () => window.show());

  if (isDev) {
    void window.loadURL(DEV_SERVER_URL);
  } else {
    void window.loadFile(path.join(ANGULAR_DIST, 'index.html'));
  }

  return window;
}

// Prevent the renderer from opening external windows or navigating away from the app.
function hardenNavigation(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    const allowed = isDev ? DEV_SERVER_URL : 'file://';
    if (!url.startsWith(allowed)) {
      event.preventDefault();
    }
  });
}

void app.whenReady().then(() => {
  try {
    const libraries = new LibraryRegistryStore(dataRoot());
    libraries.initialize();
    protocol.handle('afmedia', (request) => handleMediaRequest(libraries, request));
    registerSettingsIpc();
    registerFilesystemIpc(libraries);
    registerLibrariesIpc(libraries, isAnalysisRunning);

    const window = createWindow();
    hardenNavigation(window);
    registerAnalysisIpc(() => BrowserWindow.getAllWindows(), libraries);

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        const next = createWindow();
        hardenNavigation(next);
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox('AuraFrame library store failed to start', message);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  disposeAnalysisIpc();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

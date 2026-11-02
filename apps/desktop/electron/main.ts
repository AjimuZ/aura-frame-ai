// AuraFrame AI - Electron main process entry point.
// Creates a single hardened BrowserWindow and registers the typed IPC handlers.
// The renderer (Angular) has no direct filesystem or Node access; everything flows
// through validated IPC channels defined in the ipc/ modules.

import { app, BrowserWindow, dialog, protocol } from 'electron';
import * as path from 'node:path';
import { registerFilesystemIpc } from './ipc/filesystem.ipc';
import {
  registerAnalysisIpc,
  disposeAnalysisIpc,
  isAnalysisRunning,
  reportServicesUnavailable,
} from './ipc/analysis.ipc';
import { registerSettingsIpc } from './ipc/settings.ipc';
import { dataRoot, repoRoot } from './ipc/paths';
import { LibraryRegistryStore } from './ipc/library-store';
import { registerLibrariesIpc } from './ipc/libraries.ipc';
import { ApiClient, gatewayPort } from './api-client';
import { ComposeStack, TOKEN_FILE, composeDirectory, takeInheritedSessionToken } from './compose-stack';
import { handleMediaRequest } from './media-proxy';
import { installQuitHandling } from './quit-handling';

const isDev = process.env['AURA_DEV'] === '1';
const DEV_SERVER_URL = 'http://localhost:4200';
const ANGULAR_DIST = path.join(__dirname, '..', 'dist', 'aura-frame', 'browser');
const SERVICES_LEFT_MESSAGE =
  'Docker did not finish in time, so the app is closing without confirmation. The analysis services may still ' +
  'be running, or may still come up, with your photo folder mounted read-only. To stop them, run ' +
  '"./start.sh --stop" from the project folder.';
// Taken when this module loads, so the processes that main spawns afterwards do not inherit the variable; only the
// docker calls of the stack get the token, on purpose. Helper processes Electron started earlier may still hold it.
const inheritedToken = takeInheritedSessionToken(process.env, {
  development: isDev,
  tokenFile: path.join(repoRoot(), TOKEN_FILE),
});

// A privileged custom scheme so the renderer can display thumbnails without seeing a
// path or the token, or weakening webSecurity. Must be declared before app ready.
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'afmedia',
    privileges: { standard: true, secure: true, supportFetchAPI: true, bypassCSP: false },
  },
]);

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
  window.webContents.on('will-navigate', (event) => {
    const allowed = isDev ? DEV_SERVER_URL : 'file://';
    if (!event.url.startsWith(allowed)) {
      event.preventDefault();
    }
  });
}

void app.whenReady().then(() => {
  try {
    const libraries = new LibraryRegistryStore(dataRoot());
    libraries.initialize();
    const stack = new ComposeStack({
      composeDir: composeDirectory(process.env, repoRoot()),
      exportsDir: libraries.exportsRoot(),
      inheritedToken,
      onEvent: (event) => {
        if (event.reason === 'token-refused') reportServicesUnavailable();
      },
    });
    const api = new ApiClient({ port: gatewayPort(process.env), session: stack });
    protocol.handle('afmedia', (request) => handleMediaRequest({ api, libraries }, request));
    registerSettingsIpc();
    const bridge = { api, servicesUp: () => stack.isUp() };
    registerFilesystemIpc(libraries, bridge);
    registerLibrariesIpc(libraries, isAnalysisRunning, bridge);

    const window = createWindow();
    hardenNavigation(window);
    registerAnalysisIpc(() => BrowserWindow.getAllWindows(), libraries, { api, stack });

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

// Quits only after Docker has stopped the services, so they do not keep the GPU after the window is gone.
installQuitHandling(app, {
  dispose: disposeAnalysisIpc,
  onTimeout: () => dialog.showErrorBox('AuraFrame could not stop its services', SERVICES_LEFT_MESSAGE),
});

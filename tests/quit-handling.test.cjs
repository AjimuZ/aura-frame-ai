// Tests how the app leaves: every path stops the services within a bound and cannot loop. The last part loads the
// real compiled main process against a stand-in for electron, so its wiring is exercised too. It starts no Docker.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { installQuitHandling, QUIT_WAIT_MS } = require('../dist-electron/quit-handling.js');

// A stand-in for Electron's app: `quit` emits `before-quit`, and the quit goes on unless a listener prevents it.
function newFakeApp() {
  const listeners = new Map();
  const app = {
    exits: [],
    quitsLetThrough: 0,
    prevented: 0,
    on(event, listener) {
      listeners.set(event, listener);
    },
    emit(event, payload) {
      listeners.get(event)?.(payload);
    },
    quit() {
      let prevented = false;
      app.emit('before-quit', {
        preventDefault: () => {
          prevented = true;
          app.prevented += 1;
        },
      });
      if (!prevented) app.quitsLetThrough += 1;
    },
    exit(code) {
      app.exits.push(code);
    },
  };
  return app;
}

function newDeferred() {
  const deferred = {};
  deferred.promise = new Promise((resolve, reject) => {
    deferred.resolve = resolve;
    deferred.reject = reject;
  });
  return deferred;
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Installs the handlers with a dispose that stays pending until the test settles it.
function install(options = {}) {
  const app = newFakeApp();
  const deferred = newDeferred();
  const counts = { dispose: 0, timeout: 0 };
  installQuitHandling(app, {
    dispose: () => {
      counts.dispose += 1;
      return deferred.promise;
    },
    onTimeout: () => {
      counts.timeout += 1;
    },
    platform: 'win32',
    ...options,
  });
  return { app, deferred, counts };
}

test('a quit without window-all-closed is held until the services are stopped, then the app exits', async () => {
  const { app, deferred, counts } = install();
  app.quit();
  await tick();
  assert.equal(app.prevented, 1);
  assert.equal(app.quitsLetThrough, 0);
  assert.equal(counts.dispose, 1);
  assert.deepEqual(app.exits, []);

  deferred.resolve();
  await tick();
  assert.deepEqual(app.exits, [0]);
  assert.equal(counts.timeout, 0);
});

test('a repeated quit during the wait starts no second stop, and the exit emits no further quit', async () => {
  const { app, deferred, counts } = install();
  app.quit();
  app.quit();
  app.emit('window-all-closed');
  await tick();
  assert.equal(counts.dispose, 1);
  assert.equal(app.prevented, 3);

  deferred.resolve();
  await tick();
  assert.deepEqual(app.exits, [0]);
  assert.equal(app.prevented, 3, 'leaving must not emit before-quit again');
});

test('closing the last window quits, except on macOS where the app stays and only the services stop', async () => {
  const windows = install();
  windows.app.emit('window-all-closed');
  assert.equal(windows.app.prevented, 1, 'it must go through before-quit');
  windows.deferred.resolve();
  await tick();
  assert.deepEqual(windows.app.exits, [0]);

  const mac = install({ platform: 'darwin' });
  mac.app.emit('window-all-closed');
  assert.equal(mac.counts.dispose, 1);
  assert.equal(mac.app.prevented, 0);
  mac.deferred.resolve();
  await tick();
  assert.deepEqual(mac.app.exits, []);
});

test('a hung Docker cannot keep the process alive: at the bound the person is told and the app exits', async () => {
  const { app, deferred, counts } = install({ timeoutMs: 5 });
  app.quit();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(counts.timeout, 1);
  assert.deepEqual(app.exits, [0]);

  deferred.resolve();
  await tick();
  assert.deepEqual(app.exits, [0], 'a late stop must not exit twice');
  assert.equal(counts.timeout, 1);
  assert.equal(QUIT_WAIT_MS, 600_000);
});

test('a stop that rejects still ends the app, without claiming the bound was reached', async () => {
  const { app, deferred, counts } = install();
  app.quit();
  deferred.reject(new Error('failed'));
  await tick();
  assert.deepEqual(app.exits, [0]);
  assert.equal(counts.timeout, 0);
});

test('the real main process sends nothing before a start, and its quit paths stop and exit', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-main-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push(req.url);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('[]');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  process.env.AURAFRAME_PORT = String(server.address().port);

  const handlers = new Map();
  const app = newFakeApp();
  app.whenReady = () => Promise.resolve();
  app.getPath = () => root;
  class FakeWindow {
    static getAllWindows() {
      return [];
    }
    webContents = { setWindowOpenHandler() {}, on() {}, send() {} };
    once() {}
    show() {}
    loadFile() {
      return Promise.resolve();
    }
    loadURL() {
      return Promise.resolve();
    }
  }
  const errors = [];
  const electronPath = require.resolve('electron');
  require.cache[electronPath] = {
    id: electronPath,
    filename: electronPath,
    loaded: true,
    exports: {
      app,
      BrowserWindow: FakeWindow,
      dialog: { showErrorBox: (title, message) => errors.push({ title, message }) },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
      protocol: { registerSchemesAsPrivileged() {}, handle() {} },
      safeStorage: { isEncryptionAvailable: () => false },
      shell: {},
    },
  };

  require('../dist-electron/main.js');
  await tick();
  assert.deepEqual(errors, [], 'the main process must start');
  const { activeLibraryId } = handlers.get('libraries:get')({});

  assert.deepEqual(await handlers.get('photos:get')({}, activeLibraryId), []);
  assert.equal(await handlers.get('review:get')({}, activeLibraryId), null);
  assert.equal((await handlers.get('analysis:status')({}, activeLibraryId)).step, 'idle');
  await assert.rejects(handlers.get('data:clear')({}, activeLibraryId), /not reachable/);
  assert.deepEqual(requests, []);

  app.quit();
  assert.equal(app.prevented, 1);
  await tick();
  await tick();
  assert.deepEqual(app.exits, [0]);
  assert.equal(app.quitsLetThrough, 0);
});

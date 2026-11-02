// Tests the renderer's trust boundary through the real compiled main process and preload, each loaded against a
// stand-in for electron: no Node in the page, no network source in its CSP, `window.auraFrame` as its only addition,
// and no navigation or window away from the app. It starts no Electron, Docker or server. What a real Chromium
// renderer does with these settings is not observed here; Electron and Chromium enforce them.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const REPO_ROOT = path.resolve(__dirname, '..');
const DIST_ELECTRON = path.join(REPO_ROOT, 'dist-electron');
const APP_DIST = path.join(REPO_ROOT, 'dist', 'aura-frame', 'browser');
const SOURCE_INDEX = path.join(REPO_ROOT, 'apps', 'desktop', 'angular', 'src', 'index.html');
const BUILT_INDEX = path.join(APP_DIST, 'index.html');
// Synthetic; set so that development mode adopts it and never reads the ignored token file of a real session.
const SYNTHETIC_TOKEN = 'a'.repeat(64);
// The only sources the page may name: its own files, inline styles, in-memory data and the thumbnail scheme that
// main answers itself. None of them reaches a network.
const ALLOWED_CSP_SOURCES = new Set(["'self'", "'unsafe-inline'", 'data:', 'blob:', 'afmedia:']);

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Loads dist-electron/main.js afresh against a stand-in for electron and returns the windows it built, the IPC
// channels it registered and the error dialogs it showed. Requires `npm run build:electron` to have run.
async function loadMain(t, { development }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-renderer-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const key of Object.keys(require.cache)) {
    if (key.startsWith(DIST_ELECTRON + path.sep)) delete require.cache[key];
  }
  const previousDev = process.env.AURA_DEV;
  t.after(() => {
    if (previousDev === undefined) delete process.env.AURA_DEV;
    else process.env.AURA_DEV = previousDev;
  });
  if (development) process.env.AURA_DEV = '1';
  else delete process.env.AURA_DEV;
  process.env.AURAFRAME_API_TOKEN = SYNTHETIC_TOKEN;

  const windows = [];
  const handlers = new Map();
  const errors = [];
  const appListeners = [];
  const app = {
    on: (event, listener) => appListeners.push({ event, listener }),
    emit: (event) => appListeners.filter((entry) => entry.event === event).forEach((entry) => entry.listener()),
    whenReady: () => Promise.resolve(),
    getPath: () => root,
    quit() {},
    exit() {},
  };
  class FakeWindow {
    static getAllWindows() {
      return [];
    }
    constructor(options) {
      this.options = options;
      this.loaded = null;
      this.openHandlers = [];
      this.navigationHandlers = [];
      this.webContents = {
        setWindowOpenHandler: (handler) => this.openHandlers.push(handler),
        on: (event, handler) => {
          if (event === 'will-navigate') this.navigationHandlers.push(handler);
        },
        send() {},
      };
      windows.push(this);
    }
    once() {}
    show() {}
    loadFile(file) {
      this.loaded = { file };
      return Promise.resolve();
    }
    loadURL(url) {
      this.loaded = { url };
      return Promise.resolve();
    }
  }
  const electronPath = require.resolve('electron');
  require.cache[electronPath] = {
    id: electronPath,
    filename: electronPath,
    loaded: true,
    exports: {
      app,
      BrowserWindow: FakeWindow,
      dialog: { showErrorBox: (title, message) => errors.push({ title, message }) },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler), on() {} },
      protocol: { registerSchemesAsPrivileged() {}, handle() {} },
      safeStorage: { isEncryptionAvailable: () => false },
      shell: {},
    },
  };

  require(path.join(DIST_ELECTRON, 'main.js'));
  await tick();
  return { app, windows, handlers, errors };
}

// Runs dist-electron/preload.js in an empty V8 context, with a require that serves only `electron`, as in
// Electron's sandbox. Returns what it exposed, the modules and electron members it asked for, and the IPC traffic.
// A reference to any Node or page global would throw here, since the context has none.
function loadPreload() {
  const requested = [];
  const electronMembers = new Set();
  const exposed = [];
  const invoked = [];
  const subscriptions = [];
  const sentinel = { invokeResult: true };
  const ipcRenderer = {
    invoke: (channel, ...args) => {
      invoked.push({ channel, args });
      return sentinel;
    },
    on: (channel, listener) => subscriptions.push({ channel, listener, removed: false }),
    removeListener: (channel, listener) => {
      for (const entry of subscriptions) {
        if (entry.channel === channel && entry.listener === listener) entry.removed = true;
      }
    },
  };
  const contextBridge = new Proxy(
    { exposeInMainWorld: (key, value) => exposed.push({ key, value }) },
    {
      get(target, name) {
        electronMembers.add(`contextBridge.${String(name)}`);
        return target[name];
      },
    },
  );
  const electron = new Proxy(
    { contextBridge, ipcRenderer },
    {
      get(target, name) {
        electronMembers.add(String(name));
        return target[name];
      },
    },
  );
  const sandboxRequire = (name) => {
    requested.push(name);
    if (name === 'electron') return electron;
    throw new Error(`a sandboxed preload cannot load ${name}`);
  };

  const source = fs.readFileSync(path.join(DIST_ELECTRON, 'preload.js'), 'utf8');
  const wrapper = vm.runInContext(
    `(function (exports, require, module, __filename, __dirname) {${source}\n})`,
    vm.createContext({}),
    { filename: 'preload.js' },
  );
  const module = { exports: {} };
  wrapper(module.exports, sandboxRequire, module, 'preload.js', '.');
  return { requested, electronMembers, exposed, invoked, subscriptions, sentinel };
}

// Returns the CSP of an index.html as a map from directive to its source list. Throws when there is none.
function readCsp(file) {
  const html = fs.readFileSync(file, 'utf8');
  const match = /http-equiv="Content-Security-Policy"\s+content="([^"]*)"/.exec(html);
  assert.ok(match, `${path.relative(REPO_ROOT, file)} must declare a Content-Security-Policy`);
  const directives = new Map();
  for (const part of match[1].split(';')) {
    const [name, ...sources] = part.trim().split(/\s+/);
    if (name) directives.set(name, sources);
  }
  return directives;
}

// Returns the problems that would give the page a network route; empty when its CSP allows none.
// Fetches, sockets and beacons follow connect-src; images, scripts, styles, fonts, media and frames follow their
// own directive or default-src. Links and forms are navigations, which the navigation lock refuses.
function networkProblems(csp) {
  const problems = [];
  if (JSON.stringify(csp.get('default-src')) !== JSON.stringify(["'self'"])) {
    problems.push(`default-src is ${csp.get('default-src')}, not 'self' alone`);
  }
  if (JSON.stringify(csp.get('script-src')) !== JSON.stringify(["'self'"])) {
    problems.push(`script-src is ${csp.get('script-src')}, not 'self' alone`);
  }
  for (const [name, sources] of csp) {
    for (const source of sources) {
      if (!ALLOWED_CSP_SOURCES.has(source)) problems.push(`${name} allows ${source}`);
    }
  }
  return problems;
}

test('the window gets the real preload, context isolation and the sandbox, and no Node or relaxed setting', async (t) => {
  const { windows, errors } = await loadMain(t, { development: false });
  assert.deepEqual(errors, [], 'the main process must start');
  assert.equal(windows.length, 1);
  assert.deepEqual(windows[0].options.webPreferences, {
    preload: path.join(DIST_ELECTRON, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  });
  assert.deepEqual(windows[0].loaded, { file: BUILT_INDEX });
});

test('the preload adds only window.auraFrame, made of functions, and uses nothing beyond the bridge and IPC', () => {
  const { requested, electronMembers, exposed } = loadPreload();
  assert.deepEqual(requested, ['electron']);
  assert.deepEqual(
    exposed.map((entry) => entry.key),
    ['auraFrame'],
  );
  assert.ok(Object.keys(exposed[0].value).length > 0);
  assert.deepEqual([...electronMembers].sort(), ['contextBridge', 'contextBridge.exposeInMainWorld']);
  const api = exposed[0].value;
  for (const [name, value] of Object.entries(api)) {
    assert.equal(typeof value, 'function', `auraFrame.${name} must be a function, not a Node or IPC object`);
  }
});

test('each bridge function makes one invoke on a channel that main handles, and passes only its arguments', async (t) => {
  const { handlers } = await loadMain(t, { development: false });
  const { exposed, invoked, sentinel, electronMembers } = loadPreload();
  const api = exposed[0].value;
  const channels = new Set();
  for (const [name, fn] of Object.entries(api)) {
    if (name === 'onProgress') continue;
    const args = Array.from({ length: fn.length }, (_, index) => `${name}-arg-${index}`);
    const before = invoked.length;
    assert.equal(fn(...args), sentinel, `auraFrame.${name} must return the invoke result alone`);
    assert.equal(invoked.length, before + 1, `auraFrame.${name} must make exactly one invoke`);
    const call = invoked[invoked.length - 1];
    assert.deepEqual(call.args, args);
    assert.ok(handlers.has(call.channel), `main must handle ${call.channel} (auraFrame.${name})`);
    assert.ok(!channels.has(call.channel), `${call.channel} must belong to one function`);
    channels.add(call.channel);
  }
  assert.ok(channels.size > 0);
  assert.deepEqual([...electronMembers].sort(), ['contextBridge', 'contextBridge.exposeInMainWorld', 'ipcRenderer']);
});

test('progress events reach the page as their payload only, and the returned function unsubscribes', () => {
  const { exposed, subscriptions } = loadPreload();
  const received = [];
  const unsubscribe = exposed[0].value.onProgress((...args) => received.push(args));
  assert.equal(subscriptions.length, 1);
  assert.equal(subscriptions[0].channel, 'analysis:event');

  const payload = { type: 'progress', percent: 10 };
  subscriptions[0].listener({ sender: { send() {} }, ports: [] }, payload);
  assert.deepEqual(received, [[payload]], 'the IPC event, which carries the sender, must not reach the page');

  unsubscribe();
  assert.equal(subscriptions[0].removed, true);
});

test('the page CSP names no network source, in the source and in the built page', (t) => {
  assert.deepEqual(networkProblems(readCsp(SOURCE_INDEX)), []);
  assert.deepEqual(readCsp(SOURCE_INDEX).get('connect-src'), ["'self'", 'afmedia:']);
  if (!fs.existsSync(BUILT_INDEX)) {
    t.skip('dist/aura-frame/browser/index.html is missing; run npm run build:angular');
    return;
  }
  assert.deepEqual(networkProblems(readCsp(BUILT_INDEX)), []);
});

test('a CSP that opens a network route is reported', () => {
  const opened = new Map([
    ['default-src', ["'self'"]],
    ['script-src', ["'self'"]],
    ['connect-src', ["'self'", 'http://127.0.0.1:18420']],
    ['img-src', ['https:', '*']],
  ]);
  assert.deepEqual(networkProblems(opened), [
    'connect-src allows http://127.0.0.1:18420',
    'img-src allows https:',
    'img-src allows *',
  ]);
});

// Calls every navigation handler of a window with the URL; returns true when none of them prevented it.
function navigationAllowed(window, url) {
  let prevented = false;
  for (const handler of window.navigationHandlers) {
    handler({ url, preventDefault: () => (prevented = true) });
  }
  return !prevented;
}

test('the built app cannot navigate away from its own page or open a window', async (t) => {
  const { app, windows } = await loadMain(t, { development: false });
  app.emit('activate');
  assert.equal(windows.length, 2, 'activate with no window open builds a second window');

  const page = pathToFileURL(BUILT_INDEX).href;
  for (const window of windows) {
    assert.equal(window.navigationHandlers.length, 1);
    assert.equal(window.openHandlers.length, 1);
    assert.deepEqual(window.openHandlers[0]({ url: 'https://example.com/' }), { action: 'deny' });
    assert.deepEqual(window.openHandlers[0]({ url: page }), { action: 'deny' });

    assert.equal(navigationAllowed(window, page), true);
    assert.equal(navigationAllowed(window, `${page}#/review`), true);
    for (const url of [
      'https://example.com/',
      'http://127.0.0.1:18420/health',
      'http://localhost:4200/',
      pathToFileURL(path.join(APP_DIST, 'assets', 'logo.svg')).href,
      pathToFileURL(os.tmpdir()).href,
      'file://server/share/dist/aura-frame/browser/index.html',
      'data:text/html,page',
      'not a url',
    ]) {
      assert.equal(navigationAllowed(window, url), false, `navigation to ${url} must be refused`);
    }
  }
});

test('in development the window stays on the dev server origin, and a look-alike host is refused', async (t) => {
  const { windows } = await loadMain(t, { development: true });
  assert.equal(windows.length, 1);
  assert.deepEqual(windows[0].loaded, { url: 'http://localhost:4200' });
  assert.equal(windows[0].options.webPreferences.sandbox, true);

  assert.equal(navigationAllowed(windows[0], 'http://localhost:4200/'), true);
  assert.equal(navigationAllowed(windows[0], 'http://localhost:4200/#/review'), true);
  for (const url of [
    'http://localhost:4200.example.com/',
    'http://localhost:42000/',
    'https://localhost:4200/',
    'http://127.0.0.1:18420/health',
    pathToFileURL(BUILT_INDEX).href,
  ]) {
    assert.equal(navigationAllowed(windows[0], url), false, `navigation to ${url} must be refused`);
  }
});

// Tests the Electron handlers that finish on the host: single-photo export, library deletion and the gate that
// keeps both silent while the services are down. A fake HTTP server stands in for the API, and a temporary folder
// for the library's photo folder. All photos here are synthetic bytes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

// The handlers import `ipcMain` and `dialog` from electron, which only exist inside the app.
const handlers = new Map();
const dialogCalls = [];
let nextDialogResult = { canceled: true };
const electronPath = require.resolve('electron');
require.cache[electronPath] = {
  id: electronPath,
  filename: electronPath,
  loaded: true,
  exports: {
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    dialog: {
      showSaveDialog: async (options) => {
        dialogCalls.push(options);
        return nextDialogResult;
      },
    },
    shell: { openPath: async () => '' },
    safeStorage: { isEncryptionAvailable: () => false },
    app: { getPath: () => os.tmpdir() },
  },
};

const { registerFilesystemIpc } = require('../dist-electron/ipc/filesystem.ipc.js');
const { registerLibrariesIpc } = require('../dist-electron/ipc/libraries.ipc.js');
const { ApiClient } = require('../dist-electron/api-client.js');
const { LibraryRegistryStore } = require('../dist-electron/ipc/library-store.js');

const FIRST_ID = '11111111-1111-4111-8111-111111111111';
const SECOND_ID = '22222222-2222-4222-8222-222222222222';
const TOKEN = 'c0ffee'.repeat(10) + 'abcd';
const PHOTO_ID = 'IMG_0123456789';
const PHOTO_BYTES = Buffer.from('synthetic original bytes');

// Starts a fake API on a free loopback port that records each request and answers from `api.routes`.
async function startFakeApi(t) {
  const api = { requests: [], routes: new Map() };
  const server = http.createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      api.requests.push({ method: req.method, url: req.url, headers: req.headers });
      const route = api.routes.get(`${req.method} ${req.url}`);
      if (route) {
        route(res);
      } else {
        res.writeHead(404, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ code: 'not_found' }));
      }
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  api.port = server.address().port;
  api.json = (res, status, body) => {
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  };
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  return api;
}

// Builds a registry with two libraries, a photo folder holding one synthetic photo, and the handlers wired to a fake
// API. `state.up` is the gate the real code reads from the stack; `state.calls` counts what reached the API client.
async function setup(t, { up = true, storedPath = '/photos/trips/synthetic.jpg' } = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-bridge-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const folder = path.join(root, 'host-photos');
  fs.mkdirSync(path.join(folder, 'trips'), { recursive: true });
  const original = path.join(folder, 'trips', 'synthetic.jpg');
  fs.writeFileSync(original, PHOTO_BYTES);
  fs.mkdirSync(path.join(root, 'out'));

  const ids = [FIRST_ID, SECOND_ID];
  const libraries = new LibraryRegistryStore(path.join(root, 'data'), { createId: () => ids.shift() });
  libraries.initialize();
  libraries.createLibrary('Second');
  libraries.setInputFolder(FIRST_ID, folder);

  const api = await startFakeApi(t);
  const state = { up, calls: 0 };
  api.routes.set(`GET /api/libraries/${FIRST_ID}/photos`, (res) =>
    api.json(res, 200, [{ id: PHOTO_ID, fileName: 'synthetic.jpg', filePath: storedPath }]),
  );
  const session = { authorizationHeader: () => `Bearer ${TOKEN}`, isUp: () => state.up };
  const client = new ApiClient({ port: api.port, session });
  // Counts the calls that reach the client, so a test can tell the handler's own gate from the client's.
  const counted = {
    listPhotos: (...args) => (state.calls++, client.listPhotos(...args)),
    clearData: (...args) => (state.calls++, client.clearData(...args)),
  };
  const backend = { api: counted, servicesUp: () => state.up };

  handlers.clear();
  dialogCalls.length = 0;
  nextDialogResult = { canceled: true };
  registerFilesystemIpc(libraries, backend);
  registerLibrariesIpc(libraries, () => false, backend);
  const invoke = (channel, ...args) => Promise.resolve().then(() => handlers.get(channel)({}, ...args));
  return { root, folder, original, libraries, api, state, invoke };
}

function destination(root, name = 'copy.jpg') {
  return path.join(root, 'out', name);
}

// Rejects unless the failure's text holds none of the given secrets.
async function rejectsWithout(promise, secrets, pattern) {
  let failure;
  try {
    await promise;
  } catch (error) {
    failure = error;
  }
  assert.ok(failure, 'the call was expected to reject');
  if (pattern) assert.match(failure.message, pattern);
  for (const secret of secrets) {
    assert.ok(!failure.message.includes(secret), `the error message leaked ${secret}`);
    assert.ok(!String(failure.message).includes(TOKEN), 'the token leaked');
  }
}

test('export:photo finds the photo through the API, maps its stored path to the host folder and copies the original', async (t) => {
  const { root, original, api, invoke, folder } = await setup(t);
  nextDialogResult = { canceled: false, filePath: destination(root) };

  const reply = await invoke('export:photo', FIRST_ID, PHOTO_ID);

  assert.equal(reply, true);
  assert.deepEqual(fs.readFileSync(destination(root)), PHOTO_BYTES);
  assert.deepEqual(fs.readFileSync(original), PHOTO_BYTES, 'the original is untouched');
  assert.equal(api.requests.length, 1);
  assert.equal(api.requests[0].method, 'GET');
  assert.equal(api.requests[0].url, `/api/libraries/${FIRST_ID}/photos`);
  assert.equal(api.requests[0].headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(dialogCalls.length, 1);
  assert.equal(path.basename(dialogCalls[0].defaultPath), 'synthetic.jpg');
  assert.ok(!JSON.stringify(reply).includes(folder), 'the reply carries no path');
});

test('a cancelled save dialog copies nothing and resolves to false', async (t) => {
  const { root, invoke } = await setup(t);
  nextDialogResult = { canceled: true };
  assert.equal(await invoke('export:photo', FIRST_ID, PHOTO_ID), false);
  assert.ok(!fs.existsSync(destination(root)));
});

test('export:photo refuses a stored path that leaves the library folder, before any dialog opens', async (t) => {
  const stored = [
    '/photos/../outside.jpg',
    '/photos/trips/../../outside.jpg',
    '/photos/trips/./synthetic.jpg',
    '/photos//trips/synthetic.jpg',
    '/photos/trips\\..\\..\\outside.jpg',
    '/photos/trips/synthetic.jpg\0.png',
    '/other/outside.jpg',
    'photos/trips/synthetic.jpg',
    '/photos/',
    '',
  ];
  for (const storedPath of stored) {
    const { root, folder, invoke } = await setup(t, { storedPath });
    fs.writeFileSync(path.join(root, 'outside.jpg'), 'must never be copied');
    nextDialogResult = { canceled: false, filePath: destination(root) };

    await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, root, storedPath || 'zzz'], /./);
    assert.equal(dialogCalls.length, 0, `a dialog opened for ${JSON.stringify(storedPath)}`);
    assert.ok(!fs.existsSync(destination(root)), `a copy was made for ${JSON.stringify(storedPath)}`);
  }
});

test('export:photo refuses a link inside the folder that points outside it', async (t) => {
  const { root, folder, invoke } = await setup(t, { storedPath: '/photos/escape/secret.jpg' });
  const outside = path.join(root, 'elsewhere');
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'secret.jpg'), 'must never be copied');
  try {
    fs.symlinkSync(outside, path.join(folder, 'escape'), 'junction');
  } catch {
    t.skip('links are not permitted here');
    return;
  }
  nextDialogResult = { canceled: false, filePath: destination(root) };

  await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, root], /outside the library photo folder/);
  assert.equal(dialogCalls.length, 0);
  assert.ok(!fs.existsSync(destination(root)));
});

test('export:photo refuses a destination equal to the original, however it is written', async (t) => {
  const { root, folder, original, invoke } = await setup(t);
  const spellings = [original, path.join(folder, 'trips', '..', 'trips', 'synthetic.jpg')];
  if (process.platform === 'win32') spellings.push(original.toUpperCase());
  for (const filePath of spellings) {
    nextDialogResult = { canceled: false, filePath };
    await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, root], /different from the original/);
    assert.deepEqual(fs.readFileSync(original), PHOTO_BYTES);
  }
});

test('export:photo reports a missing photo, a missing original and a library without a folder with fixed messages', async (t) => {
  const { root, folder, original, api, invoke } = await setup(t);
  nextDialogResult = { canceled: false, filePath: destination(root) };

  await rejectsWithout(invoke('export:photo', FIRST_ID, 'no-such-photo'), [folder], /not found/);
  await rejectsWithout(invoke('export:photo', FIRST_ID, ''), [folder], /Invalid photo id/);
  await rejectsWithout(invoke('export:photo', FIRST_ID, 42), [folder], /Invalid photo id/);

  fs.rmSync(original);
  await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, original], /no longer available/);

  const served = api.requests.length;
  await rejectsWithout(invoke('export:photo', SECOND_ID, PHOTO_ID), [folder], /no photo folder/);
  assert.equal(api.requests.length, served, 'a library without a folder sends nothing');
  assert.equal(dialogCalls.length, 0);
});

test('export:photo hides an API failure behind a fixed message', async (t) => {
  const { api, folder, invoke } = await setup(t);
  api.routes.set(`GET /api/libraries/${FIRST_ID}/photos`, (res) =>
    api.json(res, 500, { code: 'internal', message: `boom at ${folder}` }),
  );
  await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, 'boom'], /refused the request/);

  // An answer that echoes the token is dropped by the client before the handler sees it.
  api.routes.set(`GET /api/libraries/${FIRST_ID}/photos`, (res) =>
    api.json(res, 200, [{ id: PHOTO_ID, filePath: `/photos/trips/synthetic.jpg`, note: TOKEN }]),
  );
  await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder], /unusable answer/);
});

test('export:photo and libraries:delete send nothing while the services are not up', async (t) => {
  const { api, state, libraries, folder, root, invoke } = await setup(t, { up: false });
  nextDialogResult = { canceled: false, filePath: destination(root) };

  await rejectsWithout(invoke('export:photo', FIRST_ID, PHOTO_ID), [folder, root], /services are not running/);
  await rejectsWithout(invoke('libraries:delete', SECOND_ID), [folder, root], /was not deleted/);

  assert.deepEqual(api.requests, [], 'nothing may reach the port, so the token cannot be handed to another listener');
  assert.equal(state.calls, 0, 'the handlers must stop before they call the client');
  assert.equal(dialogCalls.length, 0);
  assert.ok(libraries.hasLibrary(SECOND_ID), 'the library stays registered, so its data is not left behind');
});

test('libraries:delete clears the stored data through the API with the token before the library leaves the registry', async (t) => {
  const { api, libraries, invoke } = await setup(t);
  const stillRegistered = [];
  api.routes.set(`DELETE /api/libraries/${SECOND_ID}/data`, (res) => {
    stillRegistered.push(libraries.hasLibrary(SECOND_ID));
    api.json(res, 200, { removed: 3 });
  });

  const state = await invoke('libraries:delete', SECOND_ID);

  assert.deepEqual(stillRegistered, [true], 'the data is removed while the library is still registered');
  assert.equal(api.requests.length, 1);
  assert.equal(api.requests[0].method, 'DELETE');
  assert.equal(api.requests[0].url, `/api/libraries/${SECOND_ID}/data`);
  assert.equal(api.requests[0].headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(api.requests[0].headers.host, `127.0.0.1:${api.port}`);
  assert.deepEqual(state.libraries.map((library) => library.id), [FIRST_ID]);
  assert.ok(!libraries.hasLibrary(SECOND_ID));
  assert.ok(!JSON.stringify(state).includes(TOKEN));
});

test('libraries:delete keeps the library when the API fails, and says so without server text', async (t) => {
  const { api, libraries, folder, invoke } = await setup(t);
  api.routes.set(`DELETE /api/libraries/${SECOND_ID}/data`, (res) =>
    api.json(res, 500, { code: 'internal', message: `boom at ${folder}` }),
  );
  await rejectsWithout(invoke('libraries:delete', SECOND_ID), [folder, 'boom'], /refused the request/);
  assert.ok(libraries.hasLibrary(SECOND_ID));
});

test('libraries:delete refuses the last library and an unknown one without sending anything', async (t) => {
  const { api, libraries, invoke } = await setup(t);
  api.routes.set(`DELETE /api/libraries/${SECOND_ID}/data`, (res) => api.json(res, 200, {}));
  await invoke('libraries:delete', SECOND_ID);
  api.requests.length = 0;
  api.routes.set(`DELETE /api/libraries/${FIRST_ID}/data`, (res) => api.json(res, 200, {}));

  await rejectsWithout(invoke('libraries:delete', FIRST_ID), [], /last local library/);
  await rejectsWithout(invoke('libraries:delete', '33333333-3333-4333-8333-333333333333'), [], /Unknown local library/);
  assert.deepEqual(api.requests, []);
  assert.ok(libraries.hasLibrary(FIRST_ID));
});

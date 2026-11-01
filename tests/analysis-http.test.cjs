// Tests the Electron analysis handlers against a fake HTTP server: the token and Host header on every request,
// no request before a successful start, the token absent from every IPC reply and event, the event stream, the
// fallbacks when the API is down, and the order of `up` and `down` when the app closes during a start.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

// The handlers import `ipcMain` from electron, which only exists inside the app. This stand-in records them.
const handlers = new Map();
const electronPath = require.resolve('electron');
require.cache[electronPath] = {
  id: electronPath,
  filename: electronPath,
  loaded: true,
  exports: {
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    safeStorage: { isEncryptionAvailable: () => false },
    app: { getPath: () => os.tmpdir() },
  },
};

const {
  registerAnalysisIpc,
  disposeAnalysisIpc,
  isAnalysisRunning,
} = require('../dist-electron/ipc/analysis.ipc.js');
const { ApiClient, ApiClientError, gatewayPort } = require('../dist-electron/api-client.js');
const { ComposeStack } = require('../dist-electron/compose-stack.js');
const { LibraryRegistryStore } = require('../dist-electron/ipc/library-store.js');
const { installQuitHandling } = require('../dist-electron/quit-handling.js');

const REPO_ROOT = path.resolve(__dirname, '..');
const LIBRARY_ID = '11111111-1111-4111-8111-111111111111';
const TOKEN = 'c0ffee'.repeat(10) + 'abcd';
const PHOTO_ID = 'IMG_0123456789';
const SESSION = { slotAssignments: { main_photo: PHOTO_ID }, locks: { main_photo: true }, shortlist: [PHOTO_ID] };

function frame(event) {
  return `data: ${JSON.stringify(event)}\n\n`;
}

// Starts a fake API on a free loopback port. It records each request; `routes` and `streams` replace its answers.
async function startFakeApi(t) {
  const api = {
    requests: [],
    routes: new Map(),
    // One function per connection to the events route, in order; with none left, the stream ends at once.
    streams: [],
    status: { libraryId: LIBRARY_ID, step: 'indexing', running: true, paused: false, current: 0, total: 0, message: 'Working' },
    openStreams: [],
  };
  const defaults = {
    'POST /runs': (res) => send(res, 202, api.status),
    'GET /runs/current': (res) => send(res, 200, api.status),
    'POST /runs/current/pause': (res) => send(res, 200, { ...api.status, paused: true }),
    'POST /runs/current/resume': (res) => send(res, 200, api.status),
    'POST /runs/current/cancel': (res) => send(res, 200, { ...api.status, message: 'Cancelling the analysis.' }),
    'GET /photos': (res) => send(res, 200, [{ id: PHOTO_ID, fileName: 'synthetic.jpg' }]),
    'GET /review': (res) => send(res, 200, SESSION),
    'PUT /review': (res, body) => send(res, 200, JSON.parse(body)),
    'DELETE /data': (res) => send(res, 200, { removed: 1 }),
    'POST /exports': (res) => send(res, 200, { exported: 1 }),
    'GET /events': (res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.flushHeaders();
      const stream = api.streams.shift();
      if (stream) stream(res);
      else res.end();
    },
  };
  function send(res, status, body) {
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  }
  api.send = send;
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8');
      api.requests.push({ method: req.method, url: req.url, headers: req.headers, body });
      const suffix = req.url.replace(`/api/libraries/${LIBRARY_ID}`, '');
      const key = `${req.method} ${suffix}`;
      const route = api.routes.get(key) ?? defaults[key];
      if (route) route(res, body);
      else send(res, 404, { code: 'not_found', message: 'Not found' });
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  api.port = server.address().port;
  api.close = () => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  };
  t.after(() => api.close());
  return api;
}

// Registers the handlers against a fake API, a real ComposeStack with a fake spawn, and a temporary library.
async function setup(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-analysis-'));
  const folder = path.join(root, 'synthetic-photos');
  fs.mkdirSync(folder);
  const libraries = new LibraryRegistryStore(path.join(root, 'data'), { createId: () => LIBRARY_ID });
  libraries.initialize();

  const api = await startFakeApi(t);
  const spawnCalls = [];
  const stack = new ComposeStack({
    composeDir: root,
    createToken: () => TOKEN,
    baseEnv: {},
    spawn: (command, args, spawnOptions) => {
      const call = {
        command,
        args,
        options: spawnOptions,
        servedBefore: api.requests.length,
        // Whether another docker command was still running when this one was spawned.
        overlapped: spawnCalls.some((earlier) => !earlier.closed),
        closed: false,
      };
      spawnCalls.push(call);
      const listeners = {};
      const close = () => {
        call.closed = true;
        listeners.close?.(options.dockerExit ?? 0);
      };
      if (options.dockerGate) options.dockerGate.then(close);
      else setImmediate(close);
      return {
        on(event, listener) {
          listeners[event] = listener;
          return this;
        },
      };
    },
  });
  const client = new ApiClient({ port: api.port, session: stack });

  const events = [];
  const window = {
    isDestroyed: () => false,
    webContents: { send: (channel, event) => events.push({ channel, event }) },
  };
  handlers.clear();
  registerAnalysisIpc(() => [window], libraries, { api: client, stack, reconnectDelayMs: 1 });

  const replies = [];
  const invoke = async (channel, ...args) => {
    try {
      const reply = await handlers.get(channel)({}, ...args);
      replies.push(reply);
      return reply;
    } catch (error) {
      replies.push({ rejected: error.message, stack: error.stack });
      throw error;
    }
  };
  t.after(async () => {
    await disposeAnalysisIpc();
    fs.rmSync(root, { recursive: true, force: true });
  });
  const config = { inputFolder: folder, runGemma: false, preset: 'fast', includeSubfolders: true };
  return { api, folder, invoke, replies, events, spawnCalls, config };
}

async function waitFor(condition, label) {
  const deadline = Date.now() + 5000;
  while (!condition()) {
    if (Date.now() > deadline) assert.fail(`timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

function eventTypes(events) {
  return events.map(({ event }) => event.type);
}

function assertNoToken(values) {
  const text = JSON.stringify(values);
  assert.ok(!text.includes(TOKEN), 'the token leaked');
  assert.ok(!text.includes('Bearer'), 'an Authorization value leaked');
}

// Returns a promise for a fake Docker to wait on, and the function that lets it finish.
function newGate() {
  let open;
  const gate = new Promise((resolve) => {
    open = resolve;
  });
  return { gate, open };
}

// Calls every handler that talks to the API and checks the answers given while no request may leave main.
async function assertClosedAnswers(invoke) {
  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), []);
  assert.equal(await invoke('review:get', LIBRARY_ID), null);
  await assert.rejects(invoke('review:save', LIBRARY_ID, SESSION), /not reachable/);
  await assert.rejects(invoke('export:finalSet', LIBRARY_ID, [PHOTO_ID]), /not reachable/);
}

test('before any start, main sends no request, so nothing on the port can receive the token', async (t) => {
  const { api, invoke, replies, events } = await setup(t);
  await assertClosedAnswers(invoke);
  await assert.rejects(invoke('data:clear', LIBRARY_ID), /not reachable/);
  const status = await invoke('analysis:status', LIBRARY_ID);
  assert.equal(status.step, 'idle');
  assert.equal(status.running, false);
  assert.deepEqual(api.requests, []);
  assertNoToken({ replies, events });
});

test('a start that is in flight or failed opens no request either', async (t) => {
  const { gate, open } = newGate();
  const pending = await setup(t, { dockerGate: gate });
  await pending.invoke('analysis:start', LIBRARY_ID, pending.config);
  await assertClosedAnswers(pending.invoke);
  assert.equal((await pending.invoke('analysis:status', LIBRARY_ID)).running, true);
  assert.deepEqual(pending.api.requests, []);
  open();
  await waitFor(() => pending.api.requests.length > 0, 'the requests after a successful up');
  await disposeAnalysisIpc();

  const failed = await setup(t, { dockerExit: 1 });
  await failed.invoke('analysis:start', LIBRARY_ID, failed.config);
  await waitFor(() => !isAnalysisRunning(), 'the failed start to end');
  await assertClosedAnswers(failed.invoke);
  await assert.rejects(failed.invoke('data:clear', LIBRARY_ID), /not reachable/);
  const status = await failed.invoke('analysis:status', LIBRARY_ID);
  assert.equal(status.step, 'error', 'the last known status stands in');
  assert.deepEqual(failed.api.requests, []);
});

test('requests flow after a successful start and stop again once the services are stopped', async (t) => {
  const { api, invoke, config } = await setup(t);
  api.status = { ...api.status, step: 'done', running: false, message: 'Finished' };
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');
  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), [
    { id: PHOTO_ID, fileName: 'synthetic.jpg', libraryId: LIBRARY_ID, hasThumbnail: false },
  ]);

  await disposeAnalysisIpc();
  const served = api.requests.length;
  await assertClosedAnswers(invoke);
  await assert.rejects(invoke('data:clear', LIBRARY_ID), /not reachable/);
  assert.equal((await invoke('analysis:status', LIBRARY_ID)).step, 'idle');
  assert.equal(api.requests.length, served);
});

test('the photo list reaches the renderer without any stored path', async (t) => {
  const { api, invoke, config } = await setup(t);
  const thumbnailPath = '/data/libraries/synthetic/working/thumbs/0123456789abcdef.jpg';
  const filePath = '/photos/synthetic-folder/synthetic.jpg';
  api.routes.set('GET /photos', (res) =>
    api.send(res, 200, [
      { id: PHOTO_ID, fileName: 'synthetic.jpg', filePath, thumbnailPath },
      { id: 'IMG_ABCDEF0123', fileName: 'other.jpg', filePath, thumbnailPath: '' },
    ]),
  );
  api.status = { ...api.status, step: 'done', running: false, message: 'Finished' };
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');

  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), [
    { id: PHOTO_ID, fileName: 'synthetic.jpg', libraryId: LIBRARY_ID, hasThumbnail: true },
    { id: 'IMG_ABCDEF0123', fileName: 'other.jpg', libraryId: LIBRARY_ID, hasThumbnail: false },
  ]);
});

test('every API request carries the token and Host header, and no reply or event carries the token', async (t) => {
  const { api, invoke, replies, events, config } = await setup(t);
  let endStream;
  api.streams.push((res) => {
    res.write(frame({ type: 'progress', step: 'indexing', current: 1, total: 2, message: 'Indexing' }));
    endStream = () => {
      res.write(frame({ type: 'done', message: 'Finished' }));
      res.end();
    };
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => endStream, 'the event stream');
  await invoke('analysis:pause', LIBRARY_ID);
  await invoke('analysis:resume', LIBRARY_ID);
  assert.equal((await invoke('analysis:status', LIBRARY_ID)).running, true);
  await invoke('analysis:cancel', LIBRARY_ID);
  api.status = { ...api.status, step: 'done', running: false };
  endStream();
  await waitFor(() => !isAnalysisRunning(), 'the run to end');

  const photos = await invoke('photos:get', LIBRARY_ID);
  assert.deepEqual(photos, [{ id: PHOTO_ID, fileName: 'synthetic.jpg', libraryId: LIBRARY_ID, hasThumbnail: false }]);
  assert.deepEqual(await invoke('review:get', LIBRARY_ID), SESSION);
  await invoke('review:save', LIBRARY_ID, { ...SESSION, unexpected: 'dropped' });
  await invoke('export:finalSet', LIBRARY_ID, [PHOTO_ID]);
  await invoke('data:clear', LIBRARY_ID);

  const seen = api.requests.map(({ method, url }) => `${method} ${url.replace(`/api/libraries/${LIBRARY_ID}`, '')}`);
  for (const route of [
    'POST /runs', 'GET /events', 'POST /runs/current/pause', 'POST /runs/current/resume', 'GET /runs/current',
    'POST /runs/current/cancel', 'GET /photos', 'GET /review', 'PUT /review', 'POST /exports', 'DELETE /data',
  ]) {
    assert.ok(seen.includes(route), `no request for ${route}`);
  }
  for (const request of api.requests) {
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`, request.url);
    assert.equal(request.headers.host, `127.0.0.1:${api.port}`, request.url);
  }

  const saved = api.requests.find((request) => request.method === 'PUT');
  assert.deepEqual(JSON.parse(saved.body), SESSION);
  const exported = api.requests.find((request) => request.url.endsWith('/exports'));
  assert.deepEqual(JSON.parse(exported.body), { ids: [PHOTO_ID] });

  assert.ok(events.length > 0);
  assert.ok(events.every(({ channel }) => channel === 'analysis:event'));
  assertNoToken({ replies, events });
});

test('a start mounts the folder through compose first and names no path to the API', async (t) => {
  const { api, folder, invoke, spawnCalls, config } = await setup(t);
  await invoke('analysis:start', LIBRARY_ID, { ...config, extra: 'dropped', ignoreDuplicates: true });
  await waitFor(() => api.requests.some(({ url }) => url.endsWith('/runs')), 'the run request');

  assert.equal(spawnCalls.length, 1);
  assert.equal(spawnCalls[0].servedBefore, 0, 'compose must finish before the first API request');
  assert.equal(spawnCalls[0].options.env.AURAFRAME_PHOTOS_DIR, fs.realpathSync(folder));
  assert.ok(!spawnCalls[0].args.some((arg) => arg.includes(folder) || arg.includes(TOKEN)));

  const started = api.requests.find(({ url }) => url.endsWith('/runs'));
  assert.deepEqual(JSON.parse(started.body), { runGemma: false, preset: 'fast', includeSubfolders: true });
  assert.ok(!api.requests.some(({ url, body }) => `${url}${body}`.includes('synthetic-photos')));
});

test('an unknown preset falls back to the default instead of a refusal', async (t) => {
  const { api, invoke, config } = await setup(t);
  await invoke('analysis:start', LIBRARY_ID, { ...config, preset: 'nonsense' });
  await waitFor(() => api.requests.some(({ url }) => url.endsWith('/runs')), 'the run request');
  const started = api.requests.find(({ url }) => url.endsWith('/runs'));
  assert.equal(JSON.parse(started.body).preset, 'balanced');
});

test('stream frames reach the windows in the bridge shape, under the run library', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  const accent = Buffer.from(frame({ type: 'log', level: 'info', message: 'café' }));
  const middle = accent.indexOf(0xc3) + 1;
  api.streams.push((res) => {
    res.write(': keepalive\n\n');
    res.write(frame({ type: 'progress', step: 'quality', current: 1, total: 4, message: 'Scoring', libraryId: 'other' }));
    res.write('data: not json\n\n');
    res.write(frame({ type: 'review_session', session: {} }));
    res.write(`data: ${JSON.stringify({ type: 'photo_scored', photoId: PHOTO_ID, finalScore: 7, category: 'x' })}\r\n\r\n`);
    res.write(accent.subarray(0, middle));
    setTimeout(() => {
      res.write(accent.subarray(middle));
      res.write(frame({ type: 'done', message: 'Finished' }));
      api.status = { ...api.status, step: 'done', running: false, message: 'Finished' };
      res.end();
    }, 5);
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');

  assert.deepEqual(eventTypes(events), ['progress', 'photo_scored', 'log', 'done']);
  assert.ok(events.every(({ event }) => event.libraryId === LIBRARY_ID));
  assert.equal(events[2].event.message, 'café');
  const status = await invoke('analysis:status', LIBRARY_ID);
  assert.equal(status.step, 'done');
  assert.equal(status.running, false);
});

test('a refusal or a refused connection gives fixed messages and never the token', async (t) => {
  const { api, invoke, replies, events, config } = await setup(t);
  const plain = (res) => api.send(res, 401, { code: 'invalid_token', message: 'A valid session token is required.' });
  api.routes.set('POST /runs', plain);

  // The refused run still brought the services up, so the requests after it do reach the server.
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the refused run to end');
  assert.deepEqual(events.at(-1).event, {
    type: 'error',
    libraryId: LIBRARY_ID,
    message: 'The analysis service refused the request.',
  });

  const echoing = (res) => api.send(res, 401, { code: 'invalid_token', message: `bad ${TOKEN}`, detail: TOKEN });
  const echoed = ['POST /exports', 'DELETE /data', 'PUT /review', 'GET /photos', 'GET /review', 'GET /runs/current'];
  for (const key of echoed) {
    api.routes.set(key, echoing);
  }
  await assert.rejects(invoke('export:finalSet', LIBRARY_ID, [PHOTO_ID]), /unusable answer/);
  await assert.rejects(invoke('data:clear', LIBRARY_ID), /unusable answer/);
  await assert.rejects(invoke('review:save', LIBRARY_ID, SESSION), /unusable answer/);
  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), []);
  assert.equal(await invoke('review:get', LIBRARY_ID), null);
  assert.equal((await invoke('analysis:status', LIBRARY_ID)).step, 'error');
  assert.ok(api.requests.some(({ url }) => url.endsWith('/photos')), 'the echoing routes were reached');

  await api.close();
  await assert.rejects(invoke('export:finalSet', LIBRARY_ID, [PHOTO_ID]), /not reachable/);
  await assert.rejects(invoke('data:clear', LIBRARY_ID), /not reachable/);
  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), []);
  assert.equal(await invoke('review:get', LIBRARY_ID), null);
  const offline = await invoke('analysis:status', LIBRARY_ID);
  assert.equal(offline.running, false);

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the unreachable run to end');
  assert.equal(events.at(-1).event.message, 'The analysis service is not reachable.');
  assertNoToken({ replies, events });
  assert.ok(!JSON.stringify({ replies, events }).includes(String(api.port)), 'the address leaked');
});

test('a stream frame or a body that holds the token never reaches the renderer', async (t) => {
  const { api, invoke, replies, events, config } = await setup(t);
  api.routes.set('GET /photos', (res) => api.send(res, 200, [{ id: PHOTO_ID, fileName: TOKEN }]));
  api.streams.push((res) => {
    res.write(frame({ type: 'log', level: 'info', message: `leak ${TOKEN}` }));
    res.write(frame({ type: 'done', message: 'Finished' }));
    api.status = { ...api.status, step: 'done', running: false };
    res.end();
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');
  assert.deepEqual(eventTypes(events), ['done']);
  assert.deepEqual(await invoke('photos:get', LIBRARY_ID), []);
  assert.ok(api.requests.some(({ url }) => url.endsWith('/photos')), 'the body with the token was served');
  assertNoToken({ replies, events });
});

test('a dropped stream reconnects and does not repeat the replayed events', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  const first = frame({ type: 'progress', step: 'indexing', current: 1, total: 3, message: 'One' });
  const second = frame({ type: 'progress', step: 'indexing', current: 2, total: 3, message: 'Two' });
  api.streams.push((res) => {
    res.write(first);
    res.write(second);
    setTimeout(() => res.destroy(), 5);
  });
  api.streams.push((res) => {
    res.write(first);
    res.write(second);
    res.write(frame({ type: 'done', message: 'Finished' }));
    api.status = { ...api.status, step: 'done', running: false };
    res.end();
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');
  assert.deepEqual(eventTypes(events), ['progress', 'progress', 'done']);
  assert.equal(api.requests.filter(({ url }) => url.endsWith('/events')).length, 2);
});

test('a stream that stays unavailable ends the run with a clear error', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  api.routes.set('GET /events', (res) => res.destroy());

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the run to end');
  assert.equal(events.at(-1).event.type, 'error');
  assert.equal(events.at(-1).event.message, 'Lost contact with the analysis service.');
  assert.equal(api.requests.filter(({ url }) => url.endsWith('/events')).length, 5);
});

test('a run that closes without a terminal event still tells the renderer how it ended', async (t) => {
  const finished = await setup(t);
  finished.api.status = { ...finished.api.status, step: 'done', running: false, message: 'All finished' };
  await finished.invoke('analysis:start', LIBRARY_ID, finished.config);
  await waitFor(() => !isAnalysisRunning(), 'the finished run to end');
  assert.deepEqual(finished.events.at(-1).event, { type: 'done', libraryId: LIBRARY_ID, message: 'All finished' });

  const vanished = await setup(t);
  vanished.api.status = { ...vanished.api.status, step: 'idle', running: false, message: 'Idle' };
  await vanished.invoke('analysis:start', LIBRARY_ID, vanished.config);
  await waitFor(() => !isAnalysisRunning(), 'the vanished run to end');
  assert.equal(vanished.events.at(-1).event.type, 'error');
});

test('after a cancel, progress is dropped and the run ends with the stream', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  let finish;
  api.streams.push((res) => {
    res.write(frame({ type: 'progress', step: 'indexing', current: 1, total: 2, message: 'Before' }));
    finish = () => {
      res.write(frame({ type: 'progress', step: 'indexing', current: 2, total: 2, message: 'After' }));
      res.write(frame({ type: 'log', level: 'info', message: 'Analysis cancelled.' }));
      api.status = { ...api.status, step: 'idle', running: false, message: 'Analysis cancelled.' };
      res.end();
    };
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  // Waits for the client to deliver the first frame: the server having written it is not enough, because a
  // frame that arrives after the cancel is dropped.
  await waitFor(() => finish && events.length > 0, 'the first event');
  await invoke('analysis:cancel', LIBRARY_ID);
  assert.equal(isAnalysisRunning(), true, 'the run stays active until the engine has stopped');
  await assert.rejects(invoke('analysis:start', LIBRARY_ID, config), /already running/);
  finish();
  await waitFor(() => !isAnalysisRunning(), 'the run to end');

  assert.deepEqual(eventTypes(events), ['progress', 'log']);
  assert.equal(events[0].event.message, 'Before');
});

test('a cancel the API refuses leaves the run visible, with its progress and its state', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  api.routes.set('POST /runs/current/cancel', (res) => api.send(res, 500, { code: 'internal', message: 'No' }));
  let finish;
  api.streams.push((res) => {
    res.write(frame({ type: 'progress', step: 'indexing', current: 1, total: 2, message: 'Before' }));
    finish = () => {
      res.write(frame({ type: 'progress', step: 'indexing', current: 2, total: 2, message: 'After' }));
      res.write(frame({ type: 'done', message: 'Finished' }));
      api.status = { ...api.status, step: 'done', running: false, message: 'Finished' };
      res.end();
    };
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => finish && events.length > 0, 'the first event');
  await assert.rejects(invoke('analysis:cancel', LIBRARY_ID), /refused the request/);
  finish();
  await waitFor(() => !isAnalysisRunning(), 'the run to end');

  assert.deepEqual(eventTypes(events), ['progress', 'progress', 'done']);
  assert.equal(events[1].event.message, 'After');
});

test('a cancel made during the start that the API then refuses leaves the progress flowing', async (t) => {
  const { api, invoke, events, config } = await setup(t);
  let answerRun;
  api.routes.set('POST /runs', (res) => {
    answerRun = () => api.send(res, 202, api.status);
  });
  api.routes.set('POST /runs/current/cancel', (res) => api.send(res, 500, { code: 'internal', message: 'No' }));
  let stream;
  api.streams.push((res) => {
    stream = res;
  });

  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => answerRun, 'the run request');
  await invoke('analysis:cancel', LIBRARY_ID);
  answerRun();
  await waitFor(() => stream, 'the event stream');
  await waitFor(() => api.requests.some(({ url }) => url.endsWith('/cancel')), 'the cancel request');

  // Written repeatedly, because the frames sent before the client has seen the refusal are dropped.
  const writer = setInterval(() => {
    stream.write(frame({ type: 'progress', step: 'indexing', current: 1, total: 2, message: 'Still going' }));
  }, 5);
  try {
    await waitFor(() => eventTypes(events).includes('progress'), 'the progress after the refused cancel');
  } finally {
    clearInterval(writer);
  }
});

test('a cancel while the services are starting starts no run', async (t) => {
  let openDocker;
  const dockerGate = new Promise((resolve) => {
    openDocker = resolve;
  });
  const { api, invoke, config } = await setup(t, { dockerGate });

  await invoke('analysis:start', LIBRARY_ID, config);
  assert.equal(isAnalysisRunning(), true);
  const starting = await invoke('analysis:status', LIBRARY_ID);
  assert.equal(starting.running, true);
  await invoke('analysis:cancel', LIBRARY_ID);
  openDocker();
  await waitFor(() => !isAnalysisRunning(), 'the cancelled start to end');

  assert.deepEqual(api.requests, []);
});

test('a pause while the services are starting applies once the run exists', async (t) => {
  let openDocker;
  const dockerGate = new Promise((resolve) => {
    openDocker = resolve;
  });
  const { api, invoke, config } = await setup(t, { dockerGate });
  api.streams.push(() => undefined);

  await invoke('analysis:start', LIBRARY_ID, config);
  await invoke('analysis:pause', LIBRARY_ID);
  assert.equal(api.requests.length, 0);
  openDocker();
  await waitFor(() => api.requests.some(({ url }) => url.endsWith('/pause')), 'the pause request');
});

test('a failed docker or a bad folder ends the start without any API request', async (t) => {
  const failed = await setup(t, { dockerExit: 1 });
  await failed.invoke('analysis:start', LIBRARY_ID, failed.config);
  await waitFor(() => !isAnalysisRunning(), 'the failed start to end');
  assert.equal(failed.events.at(-1).event.type, 'error');
  assert.match(failed.events.at(-1).event.message, /Docker/);
  assert.deepEqual(failed.api.requests, []);

  const bad = await setup(t);
  const missing = path.join(bad.folder, 'missing');
  await bad.invoke('analysis:start', LIBRARY_ID, { ...bad.config, inputFolder: missing });
  assert.deepEqual(bad.events.at(-1).event, {
    type: 'error',
    libraryId: LIBRARY_ID,
    message: 'Invalid or missing photo folder.',
  });
  assert.deepEqual(bad.spawnCalls, []);
  assert.deepEqual(bad.api.requests, []);
  assert.ok(!JSON.stringify(bad.events).includes(missing), 'the path leaked');
});

test('an empty export selection sends nothing, and a clear is refused during a run', async (t) => {
  const { api, invoke, config } = await setup(t);
  await invoke('export:finalSet', LIBRARY_ID, []);
  await invoke('export:finalSet', LIBRARY_ID, 'not a list');
  assert.deepEqual(api.requests, []);

  api.streams.push(() => undefined);
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => api.requests.some(({ url }) => url.endsWith('/events')), 'the event stream');
  await assert.rejects(invoke('data:clear', LIBRARY_ID), /during an analysis/);
  assert.ok(!api.requests.some(({ url }) => url.endsWith('/data')));
});

test('a library unknown to the registry is refused before any request', async (t) => {
  const { api, invoke } = await setup(t);
  const unknown = '22222222-2222-4222-8222-222222222222';
  for (const channel of ['analysis:status', 'photos:get', 'review:get', 'data:clear', 'analysis:pause']) {
    await assert.rejects(invoke(channel, unknown), /Unknown local library/, channel);
  }
  assert.deepEqual(api.requests, []);
});

test('the legacy channels answer as before and contact nothing', async (t) => {
  const { api, invoke } = await setup(t);
  assert.equal((await invoke('ollama:check')).runtimeReachable, false);
  assert.equal(await invoke('openai:estimate', LIBRARY_ID), null);
  assert.equal((await invoke('openai:status', LIBRARY_ID)).reachable, false);
  await assert.rejects(invoke('export:openaiResults', LIBRARY_ID), /not available/);
  assert.deepEqual(api.requests, []);
});

test('the IPC channels are unchanged and match the preload bridge', async (t) => {
  await setup(t);
  const channels = [
    'analysis:start', 'analysis:pause', 'analysis:resume', 'analysis:cancel', 'analysis:status', 'photos:get',
    'export:finalSet', 'ollama:check', 'data:clear', 'review:get', 'review:save', 'openai:estimate', 'openai:status',
    'openai:start', 'export:openaiResults',
  ];
  assert.deepEqual([...handlers.keys()].sort(), [...channels].sort());
  const preload = fs.readFileSync(path.join(REPO_ROOT, 'apps', 'desktop', 'electron', 'preload.ts'), 'utf8');
  for (const channel of channels) {
    assert.ok(preload.includes(`'${channel}'`), channel);
  }
  assert.ok(preload.includes("event: 'analysis:event'"));
  assert.ok(!/authorization|token/i.test(preload), 'the bridge must not mention the token');
});

test('dispose stops only the services this session started, and stops following the run', async (t) => {
  const idle = await setup(t);
  await disposeAnalysisIpc();
  assert.deepEqual(idle.spawnCalls, []);

  const busy = await setup(t);
  busy.api.streams.push(() => undefined);
  await busy.invoke('analysis:start', LIBRARY_ID, busy.config);
  await waitFor(() => busy.api.requests.some(({ url }) => url.endsWith('/events')), 'the event stream');
  await disposeAnalysisIpc();
  assert.equal(isAnalysisRunning(), false);
  assert.deepEqual(busy.spawnCalls.at(-1).args.slice(-1), ['down']);
  const count = busy.spawnCalls.length;
  await disposeAnalysisIpc();
  assert.equal(busy.spawnCalls.length, count, 'a second dispose must not stop the services again');
});

// A stand-in for Electron's app that behaves as the documentation says: `quit` emits `before-quit`, and the quit
// goes on unless a listener prevents it; `exit` emits nothing.
function newFakeApp() {
  const listeners = new Map();
  const app = {
    exits: [],
    quitsLetThrough: 0,
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

const LEAVING_PATHS = {
  'closing the last window': (app) => app.emit('window-all-closed'),
  'File > Exit or app.quit()': (app) => app.quit(),
};

for (const [name, leave] of Object.entries(LEAVING_PATHS)) {
  test(`${name} during a start runs down only after up has ended, then exits`, async (t) => {
    const { gate, open } = newGate();
    const { api, invoke, spawnCalls, config } = await setup(t, { dockerGate: gate });
    const app = newFakeApp();
    installQuitHandling(app, {
      dispose: disposeAnalysisIpc,
      onTimeout: () => assert.fail('the bound must not be reached'),
      platform: 'win32',
    });

    await invoke('analysis:start', LIBRARY_ID, config);
    assert.equal(spawnCalls.length, 1);
    assert.ok(spawnCalls[0].args.includes('up'));

    leave(app);
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.equal(spawnCalls.length, 1, 'down must wait for the up in flight');
    assert.deepEqual(app.exits, [], 'the app must stay alive until Docker has finished');
    await assert.rejects(invoke('analysis:start', LIBRARY_ID, config), /are stopping/);

    open();
    await waitFor(() => app.exits.length > 0, 'the app to exit');
    assert.deepEqual(app.exits, [0]);
    assert.equal(app.quitsLetThrough, 0);
    assert.equal(spawnCalls.length, 2);
    assert.deepEqual(spawnCalls[1].args.slice(-1), ['down']);
    assert.equal(spawnCalls[1].overlapped, false, 'up must have ended before down was spawned');
    assert.ok(spawnCalls[1].closed, 'down must have ended before the exit');

    assert.equal(isAnalysisRunning(), false);
    await assertClosedAnswers(invoke);
    assert.deepEqual(api.requests, [], 'the up that ended behind the stop must not open the gate');
  });
}

test('a start is accepted again once the stop has ended, for the app that outlives its window', async (t) => {
  const { api, invoke, config } = await setup(t);
  api.status = { ...api.status, step: 'done', running: false };
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the first run to end');
  await disposeAnalysisIpc();
  await invoke('analysis:start', LIBRARY_ID, config);
  await waitFor(() => !isAnalysisRunning(), 'the second run to end');
  assert.equal(api.requests.filter(({ url }) => url.endsWith('/runs')).length, 2);
});

test('the client sends nothing while its session is not up, for the stream as well', async (t) => {
  const api = await startFakeApi(t);
  const client = new ApiClient({
    port: api.port,
    session: { authorizationHeader: () => `Bearer ${TOKEN}`, isUp: () => false },
  });
  const unreachable = (error) => error instanceof ApiClientError && error.code === 'unreachable';
  await assert.rejects(client.listPhotos(LIBRARY_ID), unreachable);
  await assert.rejects(client.streamEvents(LIBRARY_ID, () => undefined, new AbortController().signal), unreachable);
  assert.deepEqual(api.requests, []);
});

test('the client never follows a redirect, so the token cannot be sent elsewhere', async (t) => {
  const api = await startFakeApi(t);
  api.routes.set('GET /photos', (res) => {
    res.writeHead(302, { location: 'http://127.0.0.1:1/stolen' });
    res.end();
  });
  const client = new ApiClient({
    port: api.port,
    session: { authorizationHeader: () => `Bearer ${TOKEN}`, isUp: () => true },
  });
  await assert.rejects(client.listPhotos(LIBRARY_ID), (error) => {
    assert.ok(error instanceof ApiClientError);
    assert.equal(error.status, 302);
    assert.ok(!error.message.includes('127.0.0.1'));
    return true;
  });
  assert.equal(api.requests.length, 1);
});

test('the gateway port comes from the environment, and the client refuses an invalid one', () => {
  assert.equal(gatewayPort({}), 8420);
  assert.equal(gatewayPort({ AURAFRAME_PORT: '18420' }), 18420);
  for (const invalid of ['0', '70000', 'abc', '80.5']) {
    assert.equal(gatewayPort({ AURAFRAME_PORT: invalid }), 8420, invalid);
  }
  const session = { authorizationHeader: () => '', isUp: () => false };
  assert.throws(() => new ApiClient({ port: 0, session }), RangeError);
});

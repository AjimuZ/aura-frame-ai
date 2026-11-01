// Tests the `afmedia` proxy against a fake HTTP server: nothing is sent while the services are not up, a thumbnail is
// fetched by photo ID with the token added in main, and every bad request or answer ends as the same 404.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const test = require('node:test');

const { ApiClient } = require('../dist-electron/api-client.js');
const { handleMediaRequest } = require('../dist-electron/media-proxy.js');

const REPO_ROOT = path.resolve(__dirname, '..');
const ANGULAR_APP = path.join(REPO_ROOT, 'apps', 'desktop', 'angular', 'src', 'app');
const LIBRARY_ID = '11111111-1111-4111-8111-111111111111';
const PHOTO_ID = 'IMG_0123456789';
const TOKEN = 'c0ffee'.repeat(10) + 'abcd';
const IMAGE = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0xff, 0xd9]);
const MEDIA_URL = `afmedia://media/?libraryId=${LIBRARY_ID}&photoId=${PHOTO_ID}`;

function sendImage(res) {
  res.writeHead(200, { 'content-type': 'image/jpeg' });
  res.end(IMAGE);
}

// Starts a fake gateway and returns a proxy wired to it. `api.answer` replaces the response of the next requests.
async function setup(t, options = {}) {
  const api = { requests: [], answer: sendImage };
  const server = http.createServer((req, res) => {
    api.requests.push({ method: req.method, url: req.url, headers: req.headers });
    api.answer(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  api.port = server.address().port;
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  const session = {
    up: options.up ?? true,
    tokenReads: 0,
    isUp: () => session.up,
    authorizationHeader: () => {
      session.tokenReads += 1;
      return `Bearer ${TOKEN}`;
    },
  };
  const client = new ApiClient({ port: api.port, session, mediaTimeoutMs: options.mediaTimeoutMs });
  const libraries = { hasLibrary: (id) => id === LIBRARY_ID };
  const get = (url = MEDIA_URL, init = {}) => handleMediaRequest({ api: client, libraries }, new Request(url, init));
  return { api, session, get };
}

async function assertNotFound(response, label) {
  assert.equal(response.status, 404, label);
  assert.equal(await response.text(), 'Not found', label);
  assert.equal(response.headers.get('authorization'), null, label);
}

test('before the services are up the proxy sends no request, reads no token and answers 404', async (t) => {
  const { api, session, get } = await setup(t, { up: false });
  await assertNotFound(await get());
  assert.deepEqual(api.requests, []);
  assert.equal(session.tokenReads, 0);
});

test('a request stops going out when the services stop, and goes out again after the next start', async (t) => {
  const { api, session, get } = await setup(t);
  assert.equal((await get()).status, 200);
  session.up = false;
  await assertNotFound(await get());
  assert.equal(api.requests.length, 1);
  session.up = true;
  assert.equal((await get()).status, 200);
  assert.equal(api.requests.length, 2);
});

test('with the services up it asks for the thumbnail by ID, with the token added in main', async (t) => {
  const { api, get } = await setup(t);
  const response = await get();

  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'image/jpeg');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), IMAGE);
  assert.equal(api.requests.length, 1);
  const [request] = api.requests;
  assert.equal(request.method, 'GET');
  assert.equal(request.url, `/api/libraries/${LIBRARY_ID}/media/${PHOTO_ID}?variant=thumbnail`);
  assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
  assert.equal(request.headers.host, `127.0.0.1:${api.port}`);
});

test('only fixed headers reach the renderer, never one from the API', async (t) => {
  const { api, get } = await setup(t);
  api.answer = (res) => {
    res.writeHead(200, {
      'content-type': 'IMAGE/JPEG; charset=binary',
      'set-cookie': 'session=synthetic',
      'x-echo': `Bearer ${TOKEN}`,
      'access-control-allow-origin': '*',
    });
    res.end(IMAGE);
  };
  const response = await get();
  assert.equal(response.status, 200);
  assert.deepEqual([...response.headers].sort(), [
    ['cache-control', 'private, no-store'],
    ['content-type', 'image/jpeg'],
    ['x-content-type-options', 'nosniff'],
  ]);
});

test('a malformed request is answered with 404 and never reaches the API', async (t) => {
  const { api, session, get } = await setup(t);
  const library = `libraryId=${LIBRARY_ID}`;
  const photo = `photoId=${PHOTO_ID}`;
  const cases = [
    ['no parameters', 'afmedia://media/'],
    ['no photo', `afmedia://media/?${library}`],
    ['no library', `afmedia://media/?${photo}`],
    ['unknown library', `afmedia://media/?libraryId=22222222-2222-4222-8222-222222222222&${photo}`],
    ['library with a traversal', `afmedia://media/?libraryId=..%2F..%2Fsecret&${photo}`],
    ['library with a slash', `afmedia://media/?libraryId=a%2Fb&${photo}`],
    ['lower-case photo', `afmedia://media/?${library}&photoId=img_0123456789`],
    ['short photo', `afmedia://media/?${library}&photoId=IMG_012`],
    ['photo with a traversal', `afmedia://media/?${library}&photoId=..%2F..%2Fconfig`],
    ['photo with a query', `afmedia://media/?${library}&photoId=IMG_0123456789%3Fvariant%3Doriginal`],
    ['photo with a trailing newline', `afmedia://media/?${library}&photoId=IMG_0123456789%0A`],
    ['a variant beside the IDs', `${MEDIA_URL}&variant=original`],
    ['a path beside the IDs', `${MEDIA_URL}&path=%2Fphotos%2Fa.jpg`],
    ['the old path form', `afmedia://media/?${library}&path=%2Fdata%2Fthumb.jpg`],
    ['a repeated photo', `afmedia://media/?${library}&${photo}&${photo}`],
    ['another host', `afmedia://other/?${library}&${photo}`],
    ['another path', `afmedia://media/original?${library}&${photo}`],
  ];
  for (const [label, url] of cases) {
    await assertNotFound(await get(url), label);
  }
  await assertNotFound(await get(MEDIA_URL, { method: 'POST', body: 'x' }), 'a write');
  assert.deepEqual(api.requests, []);
  assert.equal(session.tokenReads, 0);
});

test('an unusable answer from the API becomes a 404 with nothing of it passed on', async (t) => {
  const { api, get } = await setup(t);
  const typed = (status, contentType, body, extra = {}) => (res) => {
    res.writeHead(status, { 'content-type': contentType, ...extra });
    res.end(body);
  };
  const cases = [
    ['missing image', typed(404, 'application/json', '{"code":"media_unavailable"}')],
    ['refused token', typed(401, 'application/json', '{"code":"unauthorized"}')],
    ['server error with an image type', typed(500, 'image/jpeg', IMAGE)],
    ['redirect', typed(302, 'image/jpeg', IMAGE, { location: 'http://example.invalid/x.jpg' })],
    ['partial content', typed(206, 'image/jpeg', IMAGE)],
    ['HTML', typed(200, 'text/html', '<script>alert(1)</script>')],
    ['SVG', typed(200, 'image/svg+xml', '<svg/>')],
    ['type that only starts like an image', typed(200, 'image/jpeg-evil', IMAGE)],
    ['no type', (res) => {
      res.writeHead(200);
      res.end(IMAGE);
    }],
    ['body holding the token', typed(200, 'image/jpeg', Buffer.concat([IMAGE, Buffer.from(TOKEN)]))],
    ['oversize body', typed(200, 'image/jpeg', Buffer.alloc(17 * 1024 * 1024))],
    ['truncated body', (res) => {
      res.writeHead(200, { 'content-type': 'image/jpeg', 'content-length': '1000' });
      res.write(IMAGE, () => res.destroy());
    }],
    ['dropped connection', (res) => res.destroy()],
  ];
  for (const [label, answer] of cases) {
    api.answer = answer;
    const response = await get();
    const text = await response.text();
    assert.equal(response.status, 404, label);
    assert.equal(text, 'Not found', label);
  }
  assert.equal(api.requests.length, cases.length, 'no redirect was followed and no request repeated');
});

test('an answer that outlasts the limit becomes a 404, even while bytes keep arriving', async (t) => {
  const { api, get } = await setup(t, { mediaTimeoutMs: 40 });
  api.answer = () => {};
  await assertNotFound(await get(), 'silent server');

  api.answer = (res) => {
    res.writeHead(200, { 'content-type': 'image/jpeg' });
    const drip = setInterval(() => res.write(IMAGE), 5);
    res.on('close', () => clearInterval(drip));
  };
  await assertNotFound(await get(), 'slow server');
});

test('a refused connection becomes a 404', async (t) => {
  const { api, session } = await setup(t);
  const closed = await new Promise((resolve) => {
    const probe = http.createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
  const client = new ApiClient({ port: closed, session });
  const libraries = { hasLibrary: () => true };
  await assertNotFound(await handleMediaRequest({ api: client, libraries }, new Request(MEDIA_URL)));
  assert.deepEqual(api.requests, []);
});

test('a full grid waits for free slots, and what waited through a stop sends nothing', async (t) => {
  const { api, session, get } = await setup(t);
  const held = [];
  api.answer = (res) => held.push(res);
  const responses = Array.from({ length: 20 }, () => get());
  const deadline = Date.now() + 5000;
  while (held.length < 8 && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(held.length, 8, 'the number of requests in flight is bounded');

  session.up = false;
  for (const res of held.splice(0)) sendImage(res);
  const statuses = (await Promise.all(responses)).map((response) => response.status);
  assert.deepEqual(statuses.slice(0, 8), Array(8).fill(200));
  assert.deepEqual(statuses.slice(8), Array(12).fill(404));
  assert.equal(api.requests.length, 8);
});

test('the renderer asks by photo ID and its code holds no photo path', () => {
  const pipe = fs.readFileSync(path.join(ANGULAR_APP, 'shared', 'pipes', 'file-url.pipe.ts'), 'utf8');
  assert.match(pipe, /afmedia:\/\/media\/\?libraryId=\$\{encodeURIComponent\(libraryId\)\}&photoId=\$\{encodeURIComponent\(photoId\)\}/);

  const sources = fs
    .readdirSync(ANGULAR_APP, { recursive: true, encoding: 'utf8' })
    .filter((file) => file.endsWith('.ts') || file.endsWith('.html'));
  const offenders = sources.filter((file) => {
    const source = fs.readFileSync(path.join(ANGULAR_APP, file), 'utf8');
    return /thumbnailPath|filePath/.test(source);
  });
  assert.deepEqual(offenders, []);
});

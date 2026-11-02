// Contract test for the sidecar's HTTP front (docker/codex/sidecar-server.cjs), run on loopback against a fake CLI.
//
// The fake CLI is a Node script started as a real child process. It records its argv, environment and working folder
// next to itself, because the server hands it no variable it could use, and writes the verdict its plan says. No
// network beyond 127.0.0.1, real CLI or account is used, and the images are a few synthetic JPEG bytes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { createSidecarServer, argsProblem } = require('../docker/codex/sidecar-server.cjs');

const TOKEN = 'k'.repeat(64);
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
const SCHEMA = Buffer.from(JSON.stringify({ type: 'object' }));
const VERDICT = JSON.stringify({ winner: 'A', confidence: 'low', reasons: ['light'], note: 'n' });
const EXEC_ARGS = [
  'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
  '-c', 'model_reasoning_effort="low"', '--image', 'a.jpg', '--image', 'b.jpg',
  '--output-schema', 'schema.json', '--output-last-message', 'verdict.json', 'Which photograph is better?',
];

const FAKE_CLI = `
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
if (args.length === 1 && args[0] === '--version') process.exit(0);
const plan = JSON.parse(fs.readFileSync(path.join(__dirname, 'plan.json'), 'utf8'));
const log = path.join(__dirname, 'calls');
fs.mkdirSync(log, { recursive: true });
const index = fs.readdirSync(log).length;
fs.writeFileSync(path.join(log, index + '.json'), JSON.stringify({
  args, env: process.env, cwd: process.cwd(), listing: fs.readdirSync('.').sort(),
}));
if (plan.kind === 'exit') process.exit(plan.code);
if (plan.kind === 'sleep') { setTimeout(() => {}, 30000); return; }
fs.writeFileSync('verdict.json', plan.text);
`;

// Starts a server on an ephemeral loopback port with a fresh fake CLI and exchange folder. Returns helpers.
async function startSidecar(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-sidecar-'));
  const cliDir = path.join(root, 'cli');
  const exchange = path.join(root, 'exchange');
  fs.mkdirSync(cliDir);
  fs.mkdirSync(exchange);
  const script = path.join(cliDir, 'fake-codex.cjs');
  fs.writeFileSync(script, FAKE_CLI);
  const plan = (value) => fs.writeFileSync(path.join(cliDir, 'plan.json'), JSON.stringify(value));
  plan({ kind: 'write', text: VERDICT });
  const server = createSidecarServer({
    token: TOKEN,
    command: [process.execPath, script],
    exchangeRoot: exchange,
    environment: { ...process.env, AURAFRAME_CODEX_TOKEN: TOKEN, OPENAI_API_KEY: 'sk-not-for-the-cli', HOME: root },
    ...options,
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.close();
    fs.rmSync(root, { recursive: true, force: true });
  });
  const calls = () => {
    const log = path.join(cliDir, 'calls');
    return fs.existsSync(log) ? fs.readdirSync(log).sort().map((name) => JSON.parse(fs.readFileSync(path.join(log, name), 'utf8'))) : [];
  };
  return { port: server.address().port, exchange, plan, calls };
}

// Sends one request and resolves to {status, body}; `token` null sends no Authorization header.
function request(port, { method = 'POST', url = '/v1/run', body, token = TOKEN, raw } = {}) {
  return new Promise((resolve, reject) => {
    const payload = raw ?? (body === undefined ? '' : JSON.stringify(body));
    const headers = { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) };
    if (token !== null) headers.Authorization = `Bearer ${token}`;
    const req = http.request({ host: '127.0.0.1', port, method, path: url, headers }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        resolve({ status: res.statusCode, body: text ? JSON.parse(text) : null });
      });
    });
    req.on('error', reject);
    req.end(payload);
  });
}

// Returns a run body for the comparison call, with the files base64-encoded.
function comparison(overrides = {}) {
  return {
    args: EXEC_ARGS,
    files: { 'a.jpg': JPEG.toString('base64'), 'b.jpg': JPEG.toString('base64'), 'schema.json': SCHEMA.toString('base64') },
    timeout_seconds: 30,
    ...overrides,
  };
}

test('refuses to start without a token of at least 32 characters', () => {
  for (const token of [undefined, '', 'short', 'x'.repeat(31)]) {
    assert.throws(() => createSidecarServer({ token }), /AURAFRAME_CODEX_TOKEN/);
  }
});

test('/health answers without a token and says nothing else', async (t) => {
  const sidecar = await startSidecar(t);
  const health = await request(sidecar.port, { method: 'GET', url: '/health', token: null });
  assert.deepEqual(health, { status: 200, body: { status: 'ok' } });
  const other = await request(sidecar.port, { method: 'GET', url: '/v1/run' });
  assert.equal(other.status, 404);
});

test('a run without the right token is refused and starts nothing', async (t) => {
  const sidecar = await startSidecar(t);
  for (const token of [null, 'w'.repeat(64), `${TOKEN}x`, '']) {
    const answer = await request(sidecar.port, { body: comparison(), token });
    assert.equal(answer.status, 401, String(token));
  }
  assert.deepEqual(sidecar.calls(), []);
  assert.deepEqual(fs.readdirSync(sidecar.exchange), []);
});

test('a comparison runs in a private exchange folder that is removed, and the verdict comes back', async (t) => {
  const sidecar = await startSidecar(t);
  const answer = await request(sidecar.port, { body: comparison() });
  assert.deepEqual(answer, { status: 200, body: { exit_code: 0, timed_out: false, verdict: VERDICT } });
  const [call] = sidecar.calls();
  assert.deepEqual(call.args, EXEC_ARGS);
  assert.deepEqual(call.listing, ['a.jpg', 'b.jpg', 'schema.json']);
  assert.ok(path.basename(call.cwd).startsWith('call-'));
  assert.equal(path.resolve(path.dirname(call.cwd)).toLowerCase(), path.resolve(sidecar.exchange).toLowerCase());
  assert.deepEqual(fs.readdirSync(sidecar.exchange), [], 'the call folder is gone');
});

test('the CLI inherits no token, key or other variable', async (t) => {
  const sidecar = await startSidecar(t);
  await request(sidecar.port, { body: comparison() });
  const [call] = sidecar.calls();
  // On Windows, libuv adds the variables a process needs to every child, whatever env it is given; the sidecar runs
  // on Linux, where nothing is added.
  const allowed = new Set(['PATH', 'HOME', 'CODEX_HOME', 'LANG', 'LC_ALL']);
  const windowsAdded = /^(HOMEDRIVE|HOMEPATH|LOGONSERVER|PATH|SYSTEMDRIVE|SYSTEMROOT|TEMP|USERDOMAIN|USERNAME|USERPROFILE|WINDIR|=.*)$/i;
  const extra = Object.keys(call.env).filter((key) => !allowed.has(key) && !(process.platform === 'win32' && windowsAdded.test(key)));
  assert.deepEqual(extra, []);
  const values = JSON.stringify(call.env);
  assert.ok(!values.includes(TOKEN) && !values.includes('sk-not-for-the-cli'));
});

test('the version check runs with no file', async (t) => {
  const sidecar = await startSidecar(t);
  const answer = await request(sidecar.port, { body: { args: ['--version'], timeout_seconds: 10 } });
  assert.deepEqual(answer.body, { exit_code: 0, timed_out: false, verdict: null });
});

test('a failing CLI returns its exit code and no verdict', async (t) => {
  const sidecar = await startSidecar(t);
  sidecar.plan({ kind: 'exit', code: 3 });
  const answer = await request(sidecar.port, { body: comparison() });
  assert.deepEqual(answer.body, { exit_code: 3, timed_out: false, verdict: null });
  assert.deepEqual(fs.readdirSync(sidecar.exchange), []);
});

test('a CLI over its time is killed and reported as timed out', async (t) => {
  const sidecar = await startSidecar(t);
  sidecar.plan({ kind: 'sleep' });
  const started = Date.now();
  const answer = await request(sidecar.port, { body: comparison({ timeout_seconds: 0.01 }) });
  assert.equal(answer.status, 200);
  assert.equal(answer.body.timed_out, true);
  assert.equal(answer.body.verdict, null);
  assert.ok(Date.now() - started < 15000, 'the timeout is clamped to at least one second, not ignored');
  assert.deepEqual(fs.readdirSync(sidecar.exchange), []);
});

test('a CLI that cannot start is a 502', async (t) => {
  const sidecar = await startSidecar(t, { command: [path.join(os.tmpdir(), 'no-such-codex-binary')] });
  const answer = await request(sidecar.port, { body: comparison() });
  assert.equal(answer.status, 502);
  assert.deepEqual(fs.readdirSync(sidecar.exchange), []);
});

test('only --version and the engine exec shape may run', async (t) => {
  const refused = [
    ['login', '--with-api-key'],
    ['login', '--device-auth'],
    ['exec'],
    ['exec', '--dangerously-bypass-approvals-and-sandbox', 'prompt'],
    ['exec', '--sandbox', 'danger-full-access', 'prompt'],
    ['exec', '--image', '../../etc/passwd', 'prompt'],
    ['exec', '--image', '/photos/a.jpg', 'prompt'],
    ['exec', '-c', 'shell_tool=true', 'prompt'],
    ['exec', '--output-last-message', '/tmp/x', 'prompt'],
    ['exec', '--ephemeral', '--model'],
    ['exec', '--model', '--sandbox', 'prompt'],
    ['exec', '--ephemeral', '-prompt-like-flag'],
    ['exec', `bad\0prompt`],
    ['--version', 'extra'],
    'exec',
    [],
    [1, 2],
  ];
  for (const args of refused) {
    assert.ok(argsProblem(args), JSON.stringify(args));
  }
  assert.equal(argsProblem(['--version']), null);
  assert.equal(argsProblem(EXEC_ARGS), null);
  assert.equal(argsProblem(['exec', '--model', 'gpt-5.5-mini', '--image', 'a.jpg', 'Describe this photograph.']), null);

  const sidecar = await startSidecar(t);
  const answer = await request(sidecar.port, { body: comparison({ args: ['login', '--with-api-key'] }) });
  assert.equal(answer.status, 400);
  assert.deepEqual(sidecar.calls(), []);
});

test('only the neutral files, as JPEG images and a JSON schema, are accepted', async (t) => {
  const sidecar = await startSidecar(t);
  const bad = [
    { '../a.jpg': JPEG.toString('base64') },
    { 'c.jpg': JPEG.toString('base64') },
    { 'verdict.json': SCHEMA.toString('base64') },
    { 'a.jpg': Buffer.from('not a jpeg').toString('base64') },
    { 'a.jpg': 'not base64!' },
    { 'schema.json': Buffer.from('{not json').toString('base64') },
    ['a.jpg'],
  ];
  for (const files of bad) {
    const answer = await request(sidecar.port, { body: comparison({ files }) });
    assert.equal(answer.status, 400, JSON.stringify(files));
  }
  const notJson = await request(sidecar.port, { raw: '{"args":' });
  assert.equal(notJson.status, 400);
  assert.deepEqual(sidecar.calls(), []);
  assert.deepEqual(fs.readdirSync(sidecar.exchange), []);
});

test('a body over the limit is refused', async (t) => {
  const sidecar = await startSidecar(t, { maxBodyBytes: 1024 });
  const answer = await request(sidecar.port, { body: comparison({ args: ['exec', 'x'.repeat(2048)] }) });
  assert.equal(answer.status, 413);
  assert.deepEqual(sidecar.calls(), []);
});

test('one call runs at a time; a second one meanwhile is told the sidecar is busy', async (t) => {
  const sidecar = await startSidecar(t);
  sidecar.plan({ kind: 'sleep' });
  const first = request(sidecar.port, { body: comparison({ timeout_seconds: 2 }) });
  await new Promise((resolve) => setTimeout(resolve, 300));
  const second = await request(sidecar.port, { body: comparison() });
  assert.equal(second.status, 409);
  assert.equal((await first).body.timed_out, true);
});

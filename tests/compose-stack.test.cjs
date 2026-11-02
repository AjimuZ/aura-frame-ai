// Tests the Electron main Compose stack module: folder validation, shell-free docker spawn, the exports folder,
// the GPU overlay and token handling.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  ComposeStack,
  FolderValidationError,
  composeDirectory,
  generateSessionToken,
  takeInheritedSessionToken,
  validateExportsFolder,
  validatePhotoFolder,
} = require('../dist-electron/compose-stack.js');

function temporaryRoot(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-stack-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

// Builds a fake spawn that records each call and closes with the given exit code on the next tick.
// `nvidia-smi` calls are recorded apart in `probes` and answer with `gpu` ('present', 'absent' or 'missing'),
// so `calls` holds the docker commands only.
function fakeSpawn(exitCode = 0, failure = null, gpu = 'absent') {
  const calls = [];
  const probes = [];
  const spawn = (command, args, options) => {
    const probe = command === 'nvidia-smi';
    (probe ? probes : calls).push({ command, args, options });
    const listeners = {};
    setImmediate(() => {
      if (probe) {
        if (gpu === 'missing') listeners.error?.(new Error('spawn nvidia-smi ENOENT'));
        else listeners.close?.(gpu === 'present' ? 0 : 9);
      } else if (failure) listeners.error?.(failure);
      else listeners.close?.(exitCode);
    });
    return {
      on(event, listener) {
        listeners[event] = listener;
        return this;
      },
    };
  };
  return { spawn, calls, probes };
}

// Exports folders made for stacks that were not given one; removed when the test process exits.
const strayExports = [];
process.on('exit', () => {
  for (const folder of strayExports) fs.rmSync(folder, { recursive: true, force: true });
});

// Returns a stack on a fake spawn. Unless `exportsDir` is given, it uses a fresh folder apart from any photo folder.
function newStack(options = {}) {
  const events = [];
  const stub = options.stub ?? fakeSpawn();
  let exportsDir = options.exportsDir;
  if (!('exportsDir' in options)) {
    exportsDir = path.join(os.tmpdir(), `auraframe-exports-${process.pid}-${strayExports.length}`);
    strayExports.push(exportsDir);
  }
  const stack = new ComposeStack({
    composeDir: '/repo',
    exportsDir,
    spawn: stub.spawn,
    createToken: options.createToken,
    inheritedToken: options.inheritedToken,
    onEvent: (event) => events.push(event),
    baseEnv: { PATH: '/usr/bin' },
  });
  return { stack, events, calls: stub.calls, probes: stub.probes };
}

test('an existing absolute directory is accepted', (t) => {
  const root = temporaryRoot(t);
  assert.equal(validatePhotoFolder(root), fs.realpathSync(root));
});

test('a missing path, a file and a relative path are rejected', (t) => {
  const root = temporaryRoot(t);
  const file = path.join(root, 'photo.jpg');
  fs.writeFileSync(file, 'x');
  for (const bad of [path.join(root, 'missing'), file, 'relative/folder', '', '   ', null, 42, `${root}\0x`]) {
    assert.throws(() => validatePhotoFolder(bad), FolderValidationError, String(bad));
  }
});

test('a symlink to a directory resolves to the real folder', (t) => {
  const root = temporaryRoot(t);
  const target = path.join(root, 'real');
  const link = path.join(root, 'link');
  fs.mkdirSync(target);
  try {
    fs.symlinkSync(target, link, 'dir');
  } catch {
    t.skip('symlinks are not permitted here');
    return;
  }
  assert.equal(validatePhotoFolder(link), fs.realpathSync(target));
});

test('start runs docker without a shell and passes the folder only in the environment', async (t) => {
  const root = temporaryRoot(t);
  const { stack, calls } = newStack();
  assert.equal(await stack.start(root), true);

  assert.equal(calls.length, 1);
  const { command, args, options } = calls[0];
  const real = fs.realpathSync(root);
  assert.equal(command, 'docker');
  assert.deepEqual(args, ['compose', '-p', 'auraframe', '-f', 'compose.yaml', 'up', '-d', '--wait']);
  assert.equal(options.shell, false);
  assert.equal(options.cwd, '/repo');
  assert.equal(options.env.AURAFRAME_PHOTOS_DIR, real);
  assert.equal(options.env.PATH, '/usr/bin');
  assert.ok(!args.some((arg) => arg.includes(real) || arg.includes(options.env.AURAFRAME_API_TOKEN)));
});

test('the exports folder reaches compose only through the environment, after validation', async (t) => {
  const root = temporaryRoot(t);
  const photos = path.join(root, 'photos');
  const exportsDir = path.join(root, 'exports');
  fs.mkdirSync(photos);
  const { stack, calls } = newStack({ exportsDir });
  assert.equal(await stack.start(photos), true);

  const { args, options } = calls[0];
  const real = fs.realpathSync(exportsDir);
  assert.equal(options.env.AURAFRAME_EXPORTS_DIR, real);
  assert.equal(options.env.AURAFRAME_PHOTOS_DIR, fs.realpathSync(photos));
  assert.ok(!args.some((arg) => arg.includes(real) || arg.includes('exports')), 'the folder must not be in argv');
  assert.ok(fs.statSync(real).isDirectory(), 'main creates the folder, not Docker');
  assert.equal(options.shell, false);
});

test('an exports folder that cannot be used stops the start before anything is spawned', async (t) => {
  const root = temporaryRoot(t);
  const photos = path.join(root, 'photos');
  fs.mkdirSync(photos);
  const file = path.join(root, 'a-file');
  fs.writeFileSync(file, 'x');
  const bad = [
    'relative/exports',
    '',
    '   ',
    null,
    path.join(root, 'a\0b'),
    file,
    path.join(file, 'child'),
    photos,
    path.join(photos, 'exports'),
    root,
  ];
  for (const exportsDir of bad) {
    const { stack, calls, probes, events } = newStack({ exportsDir });
    await assert.rejects(stack.start(photos), FolderValidationError, String(exportsDir));
    assert.equal(calls.length, 0, String(exportsDir));
    assert.equal(probes.length, 0, String(exportsDir));
    assert.deepEqual(events, [], String(exportsDir));
    assert.equal(stack.isUp(), false);
  }
});

test('the exports folder is checked as a canonical path, so a link into the photos is refused', async (t) => {
  const root = temporaryRoot(t);
  const photos = path.join(root, 'photos');
  fs.mkdirSync(photos);
  const link = path.join(root, 'link-to-photos');
  try {
    fs.symlinkSync(photos, link, 'junction');
  } catch {
    t.skip('links are not permitted here');
    return;
  }
  assert.throws(() => validateExportsFolder(link, fs.realpathSync(photos)), FolderValidationError);
});

test('an existing exports folder is accepted and left as it is', (t) => {
  const root = temporaryRoot(t);
  const photos = path.join(root, 'photos');
  const exportsDir = path.join(root, 'exports');
  fs.mkdirSync(photos);
  fs.mkdirSync(exportsDir);
  fs.writeFileSync(path.join(exportsDir, 'keep.txt'), 'kept');
  assert.equal(validateExportsFolder(exportsDir, fs.realpathSync(photos)), fs.realpathSync(exportsDir));
  assert.equal(fs.readFileSync(path.join(exportsDir, 'keep.txt'), 'utf8'), 'kept');
});

test('the GPU overlay is added when nvidia-smi -L succeeds, and the probe runs without a shell', async (t) => {
  const root = temporaryRoot(t);
  const { stack, calls, probes } = newStack({ stub: fakeSpawn(0, null, 'present') });
  assert.equal(await stack.start(root), true);

  assert.equal(probes.length, 1);
  assert.equal(probes[0].command, 'nvidia-smi');
  assert.deepEqual(probes[0].args, ['-L']);
  assert.equal(probes[0].options.shell, false);
  assert.deepEqual(calls[0].args, [
    'compose', '-p', 'auraframe', '-f', 'compose.yaml', '-f', 'compose.gpu.yaml', 'up', '-d', '--wait',
  ]);
  assert.equal(calls[0].options.shell, false);
});

test('the GPU overlay stays out when nvidia-smi fails or is missing', async (t) => {
  const root = temporaryRoot(t);
  for (const gpu of ['absent', 'missing']) {
    const { stack, calls, probes, events } = newStack({ stub: fakeSpawn(0, null, gpu) });
    assert.equal(await stack.start(root), true, gpu);
    assert.equal(probes.length, 1, gpu);
    assert.deepEqual(calls[0].args, ['compose', '-p', 'auraframe', '-f', 'compose.yaml', 'up', '-d', '--wait'], gpu);
    assert.deepEqual(events.map((e) => e.type), ['starting', 'started'], 'a missing probe is not a failure');
  }
});

test('down names the same compose files as the up before it', async (t) => {
  const root = temporaryRoot(t);
  const { stack, calls } = newStack({ stub: fakeSpawn(0, null, 'present') });
  await stack.start(root);
  await stack.stop();
  assert.deepEqual(calls[1].args, [
    'compose', '-p', 'auraframe', '-f', 'compose.yaml', '-f', 'compose.gpu.yaml', 'down',
  ]);
});

test('a failing docker after a GPU probe is still reported as an outcome without details', async (t) => {
  const root = temporaryRoot(t);
  const { stack, events } = newStack({ stub: fakeSpawn(1, null, 'present') });
  assert.equal(await stack.start(root), false);
  assert.deepEqual(events.at(-1), { type: 'failed', reason: 'docker-failed' });
});

test('start with an invalid folder never spawns docker', async () => {
  const { stack, calls, events } = newStack();
  await assert.rejects(stack.start('relative'), FolderValidationError);
  assert.equal(calls.length, 0);
  assert.deepEqual(events, []);
});

test('stop runs compose down without a shell and without the folder', async () => {
  const { stack, calls, events } = newStack();
  assert.equal(await stack.stop(), true);
  assert.deepEqual(calls[0].args, ['compose', '-p', 'auraframe', '-f', 'compose.yaml', 'down']);
  assert.equal(calls[0].options.shell, false);
  assert.equal(calls[0].options.env.AURAFRAME_PHOTOS_DIR, undefined);
  assert.deepEqual(events.map((e) => e.type), ['stopping', 'stopped']);
});

test('a failing or missing docker is reported as an outcome without details', async (t) => {
  const root = temporaryRoot(t);
  const failed = newStack({ stub: fakeSpawn(1) });
  assert.equal(await failed.stack.start(root), false);
  assert.deepEqual(failed.events.at(-1), { type: 'failed', reason: 'docker-failed' });

  const missing = newStack({ stub: fakeSpawn(0, new Error('spawn docker ENOENT')) });
  assert.equal(await missing.stack.start(root), false);
  assert.deepEqual(missing.events.at(-1), { type: 'failed', reason: 'docker-missing' });
});

test('isUp holds only from a successful up until the next start or stop', async (t) => {
  const root = temporaryRoot(t);
  const { stack } = newStack();
  assert.equal(stack.isUp(), false);
  const starting = stack.start(root);
  assert.equal(stack.isUp(), false, 'not while up is running');
  await starting;
  assert.equal(stack.isUp(), true);

  const restarting = stack.start(root);
  assert.equal(stack.isUp(), false, 'not while a second up may recreate the services');
  await restarting;
  assert.equal(stack.isUp(), true);

  const stopping = stack.stop();
  assert.equal(stack.isUp(), false, 'closed as soon as the stop begins');
  await stopping;
  assert.equal(stack.isUp(), false);

  const failed = newStack({ stub: fakeSpawn(1) });
  await failed.stack.start(root);
  assert.equal(failed.stack.isUp(), false);
});

test('stop waits for an up in flight before it runs down, and that up does not count as started', async (t) => {
  const root = temporaryRoot(t);
  const calls = [];
  const closers = [];
  const spawn = (command, args) => {
    const listeners = {};
    if (command === 'nvidia-smi') {
      // The GPU probe answers "no GPU" at once; only docker commands are held open and counted.
      setImmediate(() => listeners.close?.(1));
      return {
        on(event, listener) {
          listeners[event] = listener;
          return this;
        },
      };
    }
    calls.push(args.at(-1));
    closers.push(() => listeners.close?.(0));
    return {
      on(event, listener) {
        listeners[event] = listener;
        return this;
      },
    };
  };
  const stack = new ComposeStack({ composeDir: '/repo', exportsDir: temporaryRoot(t), spawn, baseEnv: {} });

  const starting = stack.start(root);
  const stopping = stack.stop();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ['--wait'], 'down must not be spawned yet');

  closers[0]();
  assert.equal(await starting, true);
  assert.equal(stack.isUp(), false, 'a stop was requested behind this up');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ['--wait', 'down']);

  closers[1]();
  assert.equal(await stopping, true);
  assert.equal(stack.isUp(), false);
});

test('the session token is 32 or more random bytes and differs per session', () => {
  const first = generateSessionToken();
  const second = generateSessionToken();
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.notEqual(first, second);
});

test('the token reaches docker only through the environment and never leaves main', async (t) => {
  const root = temporaryRoot(t);
  const token = 'f'.repeat(64);
  const { stack, calls, events } = newStack({ createToken: () => token });

  const replies = [await stack.start(root), await stack.stop()];
  assert.equal(calls[0].options.env.AURAFRAME_API_TOKEN, token);
  assert.equal(stack.authorizationHeader(), `Bearer ${token}`);

  assert.ok(!JSON.stringify(replies).includes(token));
  assert.ok(!JSON.stringify(events).includes(token));
  assert.ok(!calls.some((call) => call.args.some((arg) => arg.includes(token))));
});

test('main inherits the token from the environment and removes it there', (t) => {
  const root = temporaryRoot(t);
  const token = 'a1'.repeat(32);
  const env = { AURAFRAME_API_TOKEN: token, PATH: '/usr/bin' };
  const tokenFile = path.join(root, 'session-token');
  fs.writeFileSync(tokenFile, `${'b2'.repeat(32)}\n`);
  assert.equal(takeInheritedSessionToken(env, { development: true, tokenFile }), token, 'the environment comes first');
  assert.deepEqual(env, { PATH: '/usr/bin' }, 'later child processes do not inherit it');
  assert.equal(takeInheritedSessionToken({ AURAFRAME_API_TOKEN: token }, { development: false, tokenFile }), token);
});

test('in development only, main reads the token file that start.sh --backend-only writes', (t) => {
  const root = temporaryRoot(t);
  const token = 'b2'.repeat(32);
  const tokenFile = path.join(root, 'session-token');
  fs.writeFileSync(tokenFile, `${token}\n`);
  assert.equal(takeInheritedSessionToken({}, { development: true, tokenFile }), token);
  assert.equal(takeInheritedSessionToken({}, { development: false, tokenFile }), null, 'a packaged app ignores the file');
  assert.equal(takeInheritedSessionToken({}, { development: true, tokenFile: path.join(root, 'missing') }), null);
});

test('a malformed inherited token is ignored, from the environment and from the file', (t) => {
  const root = temporaryRoot(t);
  const tokenFile = path.join(root, 'session-token');
  const malformed = ['', 'short', 'A1'.repeat(32), 'g'.repeat(64), `${'a'.repeat(64)}0`, `Bearer ${'a'.repeat(64)}`];
  for (const value of malformed) {
    const env = { AURAFRAME_API_TOKEN: value };
    assert.equal(takeInheritedSessionToken(env, { development: false, tokenFile }), null, JSON.stringify(value));
    assert.equal(env.AURAFRAME_API_TOKEN, undefined, 'even a malformed value is removed');
    fs.writeFileSync(tokenFile, value);
    assert.equal(takeInheritedSessionToken({}, { development: true, tokenFile }), null, JSON.stringify(value));
  }
});

test('without an inherited token main generates its own and keeps the gate closed until a start', async (t) => {
  const root = temporaryRoot(t);
  const generated = 'c3'.repeat(32);
  const { stack, calls } = newStack({ createToken: () => generated, inheritedToken: null });
  assert.equal(stack.isUp(), false);
  assert.equal(stack.authorizationHeader(), `Bearer ${generated}`);
  await stack.start(root);
  assert.equal(calls[0].options.env.AURAFRAME_API_TOKEN, generated);
  assert.equal(stack.isUp(), true);
});

test('an inherited token opens the gate from construction and is the token a later start sends', async (t) => {
  const root = temporaryRoot(t);
  const inherited = 'd4'.repeat(32);
  let generated = 0;
  const { stack, calls, events } = newStack({
    inheritedToken: inherited,
    createToken: () => {
      generated += 1;
      return 'e5'.repeat(32);
    },
  });
  assert.equal(generated, 0, 'no token of its own');
  assert.equal(stack.isUp(), true, 'open before any start');
  assert.equal(stack.authorizationHeader(), `Bearer ${inherited}`);
  assert.deepEqual(calls, [], 'nothing is spawned to open it');
  assert.deepEqual(events, []);

  await stack.start(root);
  assert.equal(calls[0].options.env.AURAFRAME_API_TOKEN, inherited, 'compose keeps api for the same token');
  assert.equal(stack.isUp(), true);
});

test('a refused token closes the gate once, with a reason, and the next start opens it again', async (t) => {
  const root = temporaryRoot(t);
  const token = 'f6'.repeat(32);
  const { stack, events } = newStack({ inheritedToken: token });
  stack.tokenRefused();
  stack.tokenRefused();
  assert.equal(stack.isUp(), false);
  assert.deepEqual(events, [{ type: 'failed', reason: 'token-refused' }], 'reported once per opening');
  assert.ok(!JSON.stringify(events).includes(token));

  await stack.start(root);
  assert.equal(stack.isUp(), true);
  const closed = newStack();
  closed.stack.tokenRefused();
  assert.deepEqual(closed.events, [], 'a closed gate has nothing to report');
});

test('compose runs in the project folder that start.sh names, when it holds compose.yaml', (t) => {
  const root = temporaryRoot(t);
  const fallback = path.join(root, 'packaged');
  const project = path.join(root, 'project');
  fs.mkdirSync(project);
  assert.equal(composeDirectory({ AURAFRAME_PROJECT_DIR: project }, fallback), fallback, 'no compose.yaml there');
  fs.writeFileSync(path.join(project, 'compose.yaml'), 'name: synthetic\n');
  assert.equal(composeDirectory({ AURAFRAME_PROJECT_DIR: project }, fallback), project);
  for (const bad of [undefined, '', 'relative/project', `${project}\0`]) {
    assert.equal(composeDirectory({ AURAFRAME_PROJECT_DIR: bad }, fallback), fallback, String(bad));
  }
});

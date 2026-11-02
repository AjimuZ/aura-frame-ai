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
  generateSessionToken,
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

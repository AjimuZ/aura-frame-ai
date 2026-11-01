// Tests the Electron main Compose stack module: folder validation, shell-free docker spawn and token handling.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  ComposeStack,
  FolderValidationError,
  generateSessionToken,
  validatePhotoFolder,
} = require('../dist-electron/compose-stack.js');

function temporaryRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-stack-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

// Builds a fake spawn that records each call and closes with the given exit code on the next tick.
function fakeSpawn(exitCode = 0, failure = null) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    const listeners = {};
    setImmediate(() => {
      if (failure) listeners.error?.(failure);
      else listeners.close?.(exitCode);
    });
    return {
      on(event, listener) {
        listeners[event] = listener;
        return this;
      },
    };
  };
  return { spawn, calls };
}

function newStack(options = {}) {
  const events = [];
  const stub = options.stub ?? fakeSpawn();
  const stack = new ComposeStack({
    composeDir: '/repo',
    spawn: stub.spawn,
    createToken: options.createToken,
    onEvent: (event) => events.push(event),
    baseEnv: { PATH: '/usr/bin' },
  });
  return { stack, events, calls: stub.calls };
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
    calls.push(args.at(-1));
    const listeners = {};
    closers.push(() => listeners.close?.(0));
    return {
      on(event, listener) {
        listeners[event] = listener;
        return this;
      },
    };
  };
  const stack = new ComposeStack({ composeDir: '/repo', spawn, baseEnv: {} });

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

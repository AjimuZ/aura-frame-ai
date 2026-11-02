// Contract test for start.sh: exit codes, token handling, model guidance and Windows behaviour, with every tool stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const START_SH = path.join(REPO_ROOT, 'start.sh');
const GIB = 1024 ** 3;
const MODEL_LISTING = 'Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf\nmmproj-Qwen3.8-27B-BF16.gguf\n';

// Stub tools. Each one records its argv, and docker also records the environment it sees.
// Behaviour is steered through STUB_* variables so one script serves every scenario.
const STUBS = {
  docker: `#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_DIR/docker.argv"
printf 'MSYS_NO_PATHCONV=%s\\n' "\${MSYS_NO_PATHCONV-unset}" >> "$STUB_DIR/docker.env"
if [ -n "\${AURAFRAME_API_TOKEN-}" ]; then printf '%s' "$AURAFRAME_API_TOKEN" > "$STUB_DIR/docker.token"; fi
case " $* " in
  " info ") [ -z "\${STUB_DAEMON_DOWN-}" ]; exit $? ;;
  *" info --format "*) printf '%s\\n' "\${STUB_MEM:-${16 * GIB}}"; exit 0 ;;
  " compose version ") [ -z "\${STUB_NO_COMPOSE-}" ]; exit $? ;;
  *" run "*electron-builder*) exit "\${STUB_BUILD_EXIT:-0}" ;;
  *" run "*) printf '%s' "\${STUB_LISTING-}"; exit 0 ;;
  *" up "*) exit "\${STUB_UP_EXIT:-0}" ;;
esac
exit 0
`,
  nvidia_smi: `#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_DIR/nvidia-smi.argv"
[ -z "\${STUB_NO_GPU-}" ]
`,
  curl: `#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_DIR/curl.argv"
cat > "$STUB_DIR/curl.stdin"
printf '%s' "\${STUB_HEALTH_CODE:-200}"
`,
  uname: `#!/usr/bin/env bash
printf '%s\\n' "\${STUB_UNAME:-Linux}"
`,
};

// Returns the PATH entries of this process that do not contain a docker CLI, so a "missing docker" case is real.
function pathWithoutDocker() {
  const names = ['docker', 'docker.exe', 'docker.cmd', 'docker.bat'];
  return (process.env.PATH || '')
    .split(path.delimiter)
    .filter((dir) => dir && !names.some((name) => fs.existsSync(path.join(dir, name))))
    .join(path.delimiter);
}

// Runs a copy of start.sh in a fresh directory with stub tools first on PATH.
// `without` lists stub names to leave out; `env` sets STUB_* variables; `edit` rewrites the copied script text.
// Returns the result and file readers.
function runStart(args, { env = {}, without = [], edit = (script) => script } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-start-'));
  const stubs = path.join(dir, 'bin');
  fs.mkdirSync(stubs);
  for (const [name, body] of Object.entries(STUBS)) {
    const tool = name === 'nvidia_smi' ? 'nvidia-smi' : name;
    if (without.includes(tool)) {
      continue;
    }
    fs.writeFileSync(path.join(stubs, tool), body, { mode: 0o755 });
  }
  fs.writeFileSync(path.join(dir, 'start.sh'), edit(fs.readFileSync(START_SH, 'utf8')));
  const childEnv = {
    ...process.env,
    PATH: `${stubs}${path.delimiter}${pathWithoutDocker()}`,
    STUB_DIR: dir.replace(/\\/g, '/'),
    STUB_LISTING: MODEL_LISTING,
    ...env,
  };
  delete childEnv.MSYS_NO_PATHCONV;
  delete childEnv.AURAFRAME_API_TOKEN;
  const result = spawnSync('bash', ['./start.sh', ...args], { cwd: dir, env: childEnv, encoding: 'utf8', shell: false });
  const read = (name) => {
    try {
      return fs.readFileSync(path.join(dir, name), 'utf8');
    } catch {
      return '';
    }
  };
  return { status: result.status, stdout: result.stdout, stderr: result.stderr, read, dir };
}

// Returns the docker command lines that start the services (`compose ... up`).
function upLines(run) {
  return run.read('docker.argv').split('\n').filter((line) => / up /.test(` ${line} `));
}

// Returns the docker command lines that run the desktop shell build.
function shellBuildLines(run) {
  return run.read('docker.argv').split('\n').filter((line) => /electron-builder/.test(line));
}

test('exits 2 on an invalid argument, before any prerequisite', () => {
  const cases = [
    ['--bogus'],
    ['positional'],
    ['--provider'],
    ['--provider', 'bogus'],
    ['--stop', '--status'],
    ['--fetch-models', '--stop'],
  ];
  for (const args of cases) {
    const run = runStart(args, { without: ['docker'] });
    assert.equal(run.status, 2, `${args.join(' ')}: ${run.stderr}`);
    assert.match(run.stderr, /Usage:/);
  }
});

test('accepts every flag of the contract', () => {
  const cases = [
    [],
    ['--cpu'],
    ['--rebuild'],
    ['--backend-only'],
    ['--provider', 'local'],
    ['--provider', 'none'],
    ['--status'],
    ['--logs'],
    ['--logs', 'api'],
    ['--stop'],
    ['--help'],
  ];
  for (const args of cases) {
    const run = runStart(args);
    assert.equal(run.status, 0, `${args.join(' ')}: ${run.stderr}`);
  }
});

test('exits 1 when Docker, the daemon, Compose or the GPU is missing', () => {
  const missingDocker = runStart([], { without: ['docker'] });
  assert.equal(missingDocker.status, 1);
  assert.match(missingDocker.stderr, /Docker is not installed/);

  const daemonDown = runStart([], { env: { STUB_DAEMON_DOWN: '1' } });
  assert.equal(daemonDown.status, 1);
  assert.match(daemonDown.stderr, /daemon/);

  const noCompose = runStart([], { env: { STUB_NO_COMPOSE: '1' } });
  assert.equal(noCompose.status, 1);
  assert.match(noCompose.stderr, /Compose v2/);

  const noGpu = runStart([], { env: { STUB_NO_GPU: '1' } });
  assert.equal(noGpu.status, 1);
  assert.match(noGpu.stderr, /--cpu/);
  assert.deepEqual(upLines(noGpu), []);

  const cpu = runStart(['--cpu'], { env: { STUB_NO_GPU: '1' }, without: ['nvidia-smi'] });
  assert.equal(cpu.status, 0, cpu.stderr);
});

test('says on stderr that the window is not launched, and why, without denying that the app uses the services', () => {
  const run = runStart([]);
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stderr, /window is not launched by this script yet: '--rebuild' builds it into release\/, but launching it is not implemented/);
  assert.match(run.stderr, /stop them with '\.\/start\.sh --stop'/);
  assert.doesNotMatch(`${run.stdout}${run.stderr}`, /does not use the services|P02\.4/);
});

test('exits 3 when a service or the gateway fails its health check', () => {
  const serviceDown = runStart([], { env: { STUB_UP_EXIT: '1' } });
  assert.equal(serviceDown.status, 3);
  assert.match(serviceDown.stderr, /health check/);

  const gatewayRefuses = runStart([], { env: { STUB_HEALTH_CODE: '401' } });
  assert.equal(gatewayRefuses.status, 3);
  assert.match(gatewayRefuses.stderr, /\/health/);
});

test('uses the GPU overlay and the local profile only when they apply', () => {
  const full = runStart([]);
  assert.equal(full.status, 0, full.stderr);
  const [fullUp] = upLines(full);
  assert.match(fullUp, /-f compose\.yaml -f compose\.gpu\.yaml/);
  assert.match(fullUp, /--profile local/);

  const cpu = runStart(['--cpu', '--provider', 'none']);
  assert.equal(cpu.status, 0, cpu.stderr);
  const [cpuUp] = upLines(cpu);
  assert.doesNotMatch(cpuUp, /compose\.gpu\.yaml/);
  assert.doesNotMatch(cpuUp, /--profile local/);
});

test('keeps the session token out of every argument', () => {
  const run = runStart(['--backend-only']);
  assert.equal(run.status, 0, run.stderr);

  const token = run.read('docker.token');
  assert.match(token, /^[0-9a-f]{64}$/, 'docker sees a 64 hex character token in its environment');

  assert.ok(run.read('docker.argv').length > 0, 'docker was called');
  assert.ok(run.read('curl.argv').length > 0, 'curl was called');
  for (const log of ['docker.argv', 'curl.argv', 'nvidia-smi.argv']) {
    assert.ok(!run.read(log).includes(token), `${log} must not contain the token`);
  }
  assert.ok(!/AURAFRAME_API_TOKEN/.test(run.read('docker.argv')), 'no variable assignment of the token in argv');
  assert.ok(!run.stdout.includes(token) && !run.stderr.includes(token), 'the token is never printed');

  assert.ok(run.read('curl.stdin').includes(token), 'curl reads the token from stdin');
  assert.equal(run.read('data/session-token').trim(), token, '--backend-only keeps the token in the ignored file');
});

test('generates a different token for every start', () => {
  const first = runStart([]).read('docker.token');
  const second = runStart([]).read('docker.token');
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.notEqual(first, second);
});

test('explains --fetch-models when model files are missing and downloads nothing', () => {
  const run = runStart([], { env: { STUB_LISTING: '' } });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stderr, /--fetch-models/);
  assert.match(run.stderr, /ai\.provider none/);
  assert.deepEqual(upLines(run).filter((line) => /--profile local/.test(line)), [], 'vision stays off');

  const argv = run.read('docker.argv');
  assert.doesNotMatch(argv, /\b(pull|cp|exec)\b/);
  assert.equal(run.read('curl.argv').split('\n').filter((line) => !/127\.0\.0\.1/.test(line) && line).length, 0);

  const explicit = runStart(['--provider', 'local'], { env: { STUB_LISTING: '' } });
  assert.equal(explicit.status, 1);
  assert.match(explicit.stderr, /--fetch-models/);
  assert.deepEqual(upLines(explicit), []);
});

test('says "not available yet" for planned flags and runs nothing', () => {
  const cases = [['--fetch-models'], ['--codex-login'], ['--provider', 'codex']];
  for (const args of cases) {
    const run = runStart(args);
    assert.equal(run.status, 1, args.join(' '));
    assert.match(run.stderr, /not available yet/);
    assert.equal(run.read('docker.argv'), '', 'no docker call');
    assert.equal(run.read('curl.argv'), '', 'no download');
  }
});

test('sets MSYS_NO_PATHCONV=1 on Windows only', () => {
  const windows = runStart([], { env: { STUB_UNAME: 'MINGW64_NT-10.0-26100' } });
  assert.equal(windows.status, 0, windows.stderr);
  const seen = new Set(windows.read('docker.env').split('\n').filter(Boolean));
  assert.deepEqual([...seen], ['MSYS_NO_PATHCONV=1']);

  const linux = runStart([], { env: { STUB_UNAME: 'Linux' } });
  const linuxSeen = new Set(linux.read('docker.env').split('\n').filter(Boolean));
  assert.deepEqual([...linuxSeen], ['MSYS_NO_PATHCONV=unset']);
});

const PINNED_BUILDER = `example/builder@sha256:${'a'.repeat(64)}`;

// Empties both builder pins in the copied script, as if no digest had been recorded.
const withoutPins = (script) => script.replace(/^(BUILDER_IMAGE_(?:WINDOWS|LINUX))=".*"$/gm, '$1=""');

test('--rebuild without a recorded digest builds no desktop shell and says so', () => {
  const run = runStart(['--rebuild'], { edit: withoutPins });
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(shellBuildLines(run), []);
  assert.match(run.stderr, /desktop shell was not built: no builder image digest is recorded/);
});

test('the recorded builder pins are digests of the upstream images, chosen by host platform', () => {
  const script = fs.readFileSync(START_SH, 'utf8');
  const pin = (name) => script.match(new RegExp(`^${name}="([^"]*)"$`, 'm'))[1];
  const windowsPin = pin('BUILDER_IMAGE_WINDOWS');
  const linuxPin = pin('BUILDER_IMAGE_LINUX');
  for (const image of [windowsPin, linuxPin]) {
    assert.match(image, /^electronuserland\/builder@sha256:[0-9a-f]{64}$/);
  }

  const windows = runStart(['--rebuild'], { env: { STUB_UNAME: 'MINGW64_NT-10.0-26100' } });
  assert.equal(windows.status, 0, windows.stderr);
  assert.ok(shellBuildLines(windows)[0].includes(windowsPin));
  const linux = runStart(['--rebuild'], { env: { STUB_UNAME: 'Linux' } });
  assert.equal(linux.status, 0, linux.stderr);
  assert.ok(shellBuildLines(linux)[0].includes(linuxPin));
});

test('--rebuild runs the desktop shell build only in a digest-pinned image', () => {
  for (const image of ['electronuserland/builder:wine', 'example/builder@sha256:abc']) {
    const run = runStart(['--rebuild'], { env: { AURAFRAME_BUILDER_IMAGE: image } });
    assert.equal(run.status, 1, image);
    assert.match(run.stderr, /must be pinned as name@sha256/);
    assert.deepEqual(shellBuildLines(run), []);
    assert.deepEqual(upLines(run), []);
  }
});

test('--rebuild builds the desktop shell for the host platform, with only the build inputs mounted', () => {
  const windows = runStart(['--rebuild'], { env: { AURAFRAME_BUILDER_IMAGE: PINNED_BUILDER, STUB_UNAME: 'MINGW64_NT-10.0-26100' } });
  assert.equal(windows.status, 0, windows.stderr);
  const [windowsBuild] = shellBuildLines(windows);
  assert.match(windowsBuild, /electron-builder --win --publish never/);
  assert.ok(windowsBuild.includes(PINNED_BUILDER));
  assert.ok(fs.statSync(path.join(windows.dir, 'release')).isDirectory());

  const linux = runStart(['--rebuild'], { env: { AURAFRAME_BUILDER_IMAGE: PINNED_BUILDER, STUB_UNAME: 'Linux' } });
  assert.equal(linux.status, 0, linux.stderr);
  const [linuxBuild] = shellBuildLines(linux);
  assert.match(linuxBuild, /electron-builder --linux --publish never/);

  const words = linuxBuild.split(' ');
  const mounts = words.filter((_, index) => words[index - 1] === '-v').map((mount) => mount.match(/:(\/[^:]+)(:ro)?$/).slice(1));
  assert.deepEqual(mounts, [
    ['/src/package.json', ':ro'],
    ['/src/package-lock.json', ':ro'],
    ['/src/angular.json', ':ro'],
    ['/src/tsconfig.json', ':ro'],
    ['/src/tsconfig.app.json', ':ro'],
    ['/src/tsconfig.electron.json', ':ro'],
    ['/src/apps', ':ro'],
    ['/out', undefined],
  ]);
  assert.match(linuxBuild, /--cap-drop ALL/);
  assert.match(linuxBuild, /--user \d+:\d+/);
  assert.doesNotMatch(linuxBuild, /docker\.sock|AURAFRAME_API_TOKEN|--env-file/);
  assert.ok(!linuxBuild.includes(linux.read('docker.token')), 'the session token is not in the build command');
});

test('a failed desktop shell build exits 1 before the services start', () => {
  const run = runStart(['--rebuild'], { env: { AURAFRAME_BUILDER_IMAGE: PINNED_BUILDER, STUB_BUILD_EXIT: '1' } });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /Building the desktop shell failed/);
  assert.deepEqual(upLines(run), []);
});

test('--rebuild with --backend-only leaves the desktop shell alone', () => {
  const run = runStart(['--rebuild', '--backend-only'], { env: { AURAFRAME_BUILDER_IMAGE: PINNED_BUILDER } });
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(shellBuildLines(run), []);
});

test('a start without --rebuild does not build the desktop shell', () => {
  const run = runStart([], { env: { AURAFRAME_BUILDER_IMAGE: PINNED_BUILDER } });
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(shellBuildLines(run), []);
});

test('warns, without failing, when Docker has less than 10 GB', () => {
  const low = runStart([], { env: { STUB_MEM: String(Math.round(8.2 * GIB)) } });
  assert.equal(low.status, 0, low.stderr);
  assert.match(low.stderr, /warning: Docker has 8 GB of memory, less than the recommended 10 GB/);

  const enough = runStart([], { env: { STUB_MEM: String(12 * GIB) } });
  assert.equal(enough.status, 0, enough.stderr);
  assert.doesNotMatch(enough.stderr, /less than the recommended/);
});

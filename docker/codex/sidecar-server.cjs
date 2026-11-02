// HTTP front of the opt-in Codex sidecar: runs one checked `codex` command per authenticated request, in a fresh
// private folder of the exchange tmpfs that is removed afterwards (ADR 0018).
//
// Contract, for the engine's SidecarRunner (engine/src/providers/codex_sidecar.py):
// - GET /health answers 200 {"status":"ok"} without a token and with no other detail, for the compose health check.
// - POST /v1/run needs `Authorization: Bearer <AURAFRAME_CODEX_TOKEN>` and a JSON body
//   {"args": [...], "files": {"a.jpg"|"b.jpg"|"schema.json": base64}, "timeout_seconds": n}. It answers
//   200 {"exit_code": n, "timed_out": bool, "verdict": text|null} once the CLI has run, 400 for a refused request,
//   401 without the token, 409 while another call runs, 413 for a body over the limit, 502 when the CLI cannot start.
// - Only `--version` or the `exec` shape the engine builds is run: shell: false, no stdin, output discarded, and an
//   environment of PATH, HOME, CODEX_HOME and the locale only, so the token never reaches the CLI. Nothing of a
//   request (images, prompt, verdict) is ever logged.
// - Without a token of at least 32 characters the server does not start, so it fails closed.
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');

const PORT = 8090;
const TOKEN_ENV = 'AURAFRAME_CODEX_TOKEN';
const MIN_TOKEN_LENGTH = 32;
const MAX_BODY_BYTES = 24 * 1024 * 1024;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_SCHEMA_BYTES = 64 * 1024;
const MAX_VERDICT_BYTES = 64 * 1024;
const MAX_PROMPT_LENGTH = 16 * 1024;
const MAX_ARGS = 32;
// The CLI timeout in seconds is clamped into this range; 180 applies when the request gives none.
const TIMEOUT_BOUNDS = [1, 600];
const DEFAULT_TIMEOUT_SECONDS = 180;
const ENV_KEYS = ['PATH', 'HOME', 'CODEX_HOME', 'LANG', 'LC_ALL'];
const IMAGE_NAMES = ['a.jpg', 'b.jpg'];
const SCHEMA_NAME = 'schema.json';
const VERDICT_NAME = 'verdict.json';
const BASE64_PATTERN = /^[A-Za-z0-9+/]*={0,2}$/;
// Flags of `codex exec` that the engine sends, each with the pattern its value must match, or null for a bare flag.
// Anything else, such as a sandbox bypass, another sandbox mode or a path, is refused.
const EXEC_FLAGS = new Map([
  ['--ephemeral', null],
  ['--skip-git-repo-check', null],
  ['--sandbox', /^read-only$/],
  ['-c', /^model_reasoning_effort="(minimal|low|medium|high)"$/],
  ['--model', /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/],
  ['--image', /^[ab]\.jpg$/],
  ['--output-schema', /^schema\.json$/],
  ['--output-last-message', /^verdict\.json$/],
]);

// Returns why `args` may not run, or null when it is `--version` or the engine's `exec` shape with the prompt last.
function argsProblem(args) {
  if (!Array.isArray(args) || args.length === 0 || args.length > MAX_ARGS) {
    return 'args must be a short non-empty list';
  }
  if (!args.every((arg) => typeof arg === 'string' && !arg.includes('\0'))) {
    return 'args must be strings';
  }
  if (args.length === 1 && args[0] === '--version') {
    return null;
  }
  if (args[0] !== 'exec' || args.length < 2) {
    return 'only --version and exec may run';
  }
  const prompt = args[args.length - 1];
  if (prompt.length === 0 || prompt.length > MAX_PROMPT_LENGTH || prompt.startsWith('-')) {
    return 'the prompt must be the last argument';
  }
  for (let index = 1; index < args.length - 1; index += 1) {
    if (!EXEC_FLAGS.has(args[index])) {
      return 'a flag is not allowed';
    }
    const pattern = EXEC_FLAGS.get(args[index]);
    if (pattern) {
      index += 1;
      if (index >= args.length - 1 || !pattern.test(args[index])) {
        return 'a flag has a value that is not allowed';
      }
    }
  }
  return null;
}

// Returns {files: [[name, Buffer]]} for the allowed, well-formed files, or {problem} for the first refused one.
// Images must be JPEG data within the size limit; the schema must be JSON.
function decodeFiles(files) {
  if (files === undefined) {
    return { files: [] };
  }
  if (typeof files !== 'object' || files === null || Array.isArray(files)) {
    return { problem: 'files must be an object' };
  }
  const decoded = [];
  for (const [name, value] of Object.entries(files)) {
    if (![...IMAGE_NAMES, SCHEMA_NAME].includes(name)) {
      return { problem: 'a file name is not allowed' };
    }
    if (typeof value !== 'string' || !BASE64_PATTERN.test(value)) {
      return { problem: 'a file is not base64' };
    }
    const data = Buffer.from(value, 'base64');
    if (name === SCHEMA_NAME) {
      if (data.length > MAX_SCHEMA_BYTES || !isJson(data)) {
        return { problem: 'the schema is not JSON within the size limit' };
      }
    } else if (data.length > MAX_IMAGE_BYTES || data.length < 3 || data[0] !== 0xff || data[1] !== 0xd8 || data[2] !== 0xff) {
      return { problem: 'an image is not JPEG within the size limit' };
    }
    decoded.push([name, data]);
  }
  return { files: decoded };
}

// Tells whether a buffer parses as JSON.
function isJson(data) {
  try {
    JSON.parse(data.toString('utf8'));
    return true;
  } catch {
    return false;
  }
}

// Returns the CLI timeout in milliseconds: the requested seconds clamped into TIMEOUT_BOUNDS, else the default.
function timeoutMs(value) {
  const seconds = typeof value === 'number' && Number.isFinite(value) ? value : DEFAULT_TIMEOUT_SECONDS;
  return Math.round(Math.min(Math.max(seconds, TIMEOUT_BOUNDS[0]), TIMEOUT_BOUNDS[1]) * 1000);
}

// Returns the only environment variables the CLI inherits, picked from `source`.
function cliEnvironment(source) {
  const env = {};
  for (const key of ENV_KEYS) {
    if (typeof source[key] === 'string') {
      env[key] = source[key];
    }
  }
  return env;
}

// Starts the CLI and resolves to {started, code, timedOut}; never rejects. On timeout its whole process group is
// killed, because the npm launcher starts the native CLI as a child of its own.
function runCli(command, args, cwd, env, limitMs) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command[0], [...command.slice(1), ...args], {
        cwd,
        env,
        shell: false,
        stdio: 'ignore',
        detached: process.platform !== 'win32',
        windowsHide: true,
      });
    } catch {
      resolve({ started: false, code: -1, timedOut: false });
      return;
    }
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch {
        child.kill('SIGKILL');
      }
    }, limitMs);
    child.once('error', () => {
      clearTimeout(timer);
      resolve({ started: false, code: -1, timedOut: false });
    });
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      resolve({ started: true, code: code === null ? (signal ? 128 : -1) : code, timedOut });
    });
  });
}

// Reads the verdict the CLI wrote, or returns null when there is none or it is over the size limit.
function readVerdict(folder) {
  try {
    const file = path.join(folder, VERDICT_NAME);
    if (fs.statSync(file).size > MAX_VERDICT_BYTES) {
      return null;
    }
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

// Writes the JSON answer and closes the connection; `no-store` keeps a verdict out of any cache.
function send(response, status, body) {
  const text = JSON.stringify(body);
  response.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(text),
    'Cache-Control': 'no-store',
    Connection: 'close',
  });
  response.end(text);
}

// Resolves to the request body as a Buffer, or to null when it is over `limit` bytes.
function readBody(request, limit) {
  return new Promise((resolve, reject) => {
    const declared = Number(request.headers['content-length']);
    if (Number.isFinite(declared) && declared > limit) {
      resolve(null);
      return;
    }
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        request.removeAllListeners('data');
        resolve(null);
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => resolve(Buffer.concat(chunks)));
    request.on('error', reject);
  });
}

// Returns an http.Server that serves the contract above. Options: `token` (required, 32 characters or more),
// `command` (the CLI argv prefix, default ['codex']), `exchangeRoot` (default '/exchange'), `environment` (where the
// CLI's variables are picked from, default process.env) and `maxBodyBytes`. Throws when the token is too short.
function createSidecarServer(options) {
  const token = options.token;
  if (typeof token !== 'string' || token.length < MIN_TOKEN_LENGTH) {
    throw new Error(`${TOKEN_ENV} is missing or shorter than ${MIN_TOKEN_LENGTH} characters; the sidecar does not start.`);
  }
  const expected = crypto.createHash('sha256').update(token).digest();
  const command = options.command || ['codex'];
  const exchangeRoot = options.exchangeRoot || '/exchange';
  const env = cliEnvironment(options.environment || process.env);
  const maxBodyBytes = options.maxBodyBytes || MAX_BODY_BYTES;
  let busy = false;

  // Compares the bearer token in constant time, over digests so the lengths never differ.
  const authorized = (header) => {
    const match = /^Bearer (.+)$/.exec(String(header || ''));
    if (!match) {
      return false;
    }
    return crypto.timingSafeEqual(crypto.createHash('sha256').update(match[1]).digest(), expected);
  };

  // Validates one run request, runs the CLI in a private folder and removes the folder whatever happens.
  const run = async (request, response) => {
    if (!authorized(request.headers.authorization)) {
      send(response, 401, { error: 'unauthorized' });
      return;
    }
    if (busy) {
      send(response, 409, { error: 'busy' });
      return;
    }
    busy = true;
    try {
      const raw = await readBody(request, maxBodyBytes);
      if (raw === null) {
        send(response, 413, { error: 'too_large' });
        return;
      }
      let body;
      try {
        body = JSON.parse(raw.toString('utf8'));
      } catch {
        send(response, 400, { error: 'the body is not JSON' });
        return;
      }
      if (typeof body !== 'object' || body === null) {
        send(response, 400, { error: 'the body is not an object' });
        return;
      }
      const problem = argsProblem(body.args);
      const decoded = decodeFiles(body.files);
      if (problem || decoded.problem) {
        send(response, 400, { error: problem || decoded.problem });
        return;
      }
      const folder = fs.mkdtempSync(path.join(exchangeRoot, 'call-'));
      try {
        fs.chmodSync(folder, 0o700);
        for (const [name, data] of decoded.files) {
          fs.writeFileSync(path.join(folder, name), data, { mode: 0o600 });
        }
        const outcome = await runCli(command, body.args, folder, env, timeoutMs(body.timeout_seconds));
        if (!outcome.started) {
          send(response, 502, { error: 'cli_not_started' });
          return;
        }
        send(response, 200, {
          exit_code: outcome.code,
          timed_out: outcome.timedOut,
          verdict: outcome.timedOut ? null : readVerdict(folder),
        });
      } finally {
        fs.rmSync(folder, { recursive: true, force: true });
      }
    } finally {
      busy = false;
    }
  };

  return http.createServer((request, response) => {
    const url = String(request.url || '');
    let handled;
    if (request.method === 'GET' && url === '/health') {
      send(response, 200, { status: 'ok' });
      return;
    } else if (request.method === 'POST' && url === '/v1/run') {
      handled = run(request, response);
    } else {
      send(response, 404, { error: 'not_found' });
      return;
    }
    handled.catch(() => {
      if (!response.headersSent) {
        send(response, 500, { error: 'internal' });
      }
    });
  });
}

// Exits 0 when the local server answers /health with 200, else 1; the compose health check runs it.
function healthCheck() {
  const request = http.get({ host: '127.0.0.1', port: PORT, path: '/health', timeout: 3000 }, (response) => {
    response.resume();
    process.exit(response.statusCode === 200 ? 0 : 1);
  });
  request.on('timeout', () => request.destroy());
  request.on('error', () => process.exit(1));
}

// Starts the server on PORT for every interface of the container, so `api` reaches it on `backend`. The port is never
// published, and the token gates every run whichever network a request comes from. Exits 1 without a valid token.
// The token is then removed from this process's environment, so nothing spawned later can inherit it.
function main() {
  if (process.argv.includes('--health')) {
    healthCheck();
    return;
  }
  let server;
  try {
    server = createSidecarServer({ token: process.env[TOKEN_ENV] });
  } catch (error) {
    console.error(`codex-sidecar: ${error.message}`);
    process.exit(1);
  }
  delete process.env[TOKEN_ENV];
  server.listen(PORT, '0.0.0.0', () => console.log(`codex-sidecar: listening on port ${PORT}`));
  process.on('SIGTERM', () => server.close(() => process.exit(0)));
}

if (require.main === module) {
  main();
}

module.exports = { createSidecarServer, argsProblem, decodeFiles, MIN_TOKEN_LENGTH };

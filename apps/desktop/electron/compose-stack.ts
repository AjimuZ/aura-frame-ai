// Starts and stops the Docker Compose stack for the Electron main process and holds the per-session API token.
// Docker runs through spawn with shell: false. The chosen photo folder, the exports folder and the token reach
// compose only through the child environment, never through argv, so none shows up in a process listing or a log
// line. The GPU overlay is added when `nvidia-smi -L` succeeds, as start.sh does. A window started by start.sh
// inherits that script's token instead of generating one, and may then talk to the services from launch.

import { randomBytes } from 'node:crypto';
import { spawn as nodeSpawn } from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export const COMPOSE_PROJECT = 'auraframe';
export const PHOTOS_ENV = 'AURAFRAME_PHOTOS_DIR';
export const EXPORTS_ENV = 'AURAFRAME_EXPORTS_DIR';
export const TOKEN_ENV = 'AURAFRAME_API_TOKEN';
// Set by start.sh to the folder that holds compose.yaml, which a packaged window cannot find on its own.
export const PROJECT_DIR_ENV = 'AURAFRAME_PROJECT_DIR';
// Where `./start.sh --backend-only` leaves the token, relative to the repository root. Read in development only.
export const TOKEN_FILE = path.join('data', 'session-token');
const TOKEN_BYTES = 32;
// What start.sh and generateSessionToken produce. Any other inherited value is ignored.
const TOKEN_PATTERN = /^[0-9a-f]{64}$/;
const BASE_FILES = ['compose.yaml'];
const GPU_FILE = 'compose.gpu.yaml';

// Minimal view of a spawned process, so tests can inject a fake without a real Docker.
export interface SpawnedProcess {
  on(event: 'error', listener: (error: Error) => void): unknown;
  on(event: 'close', listener: (code: number | null) => void): unknown;
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => SpawnedProcess;

// Events carry the action and outcome only: never the folder, the token or the process output.
// `token-refused` means the gateway answered 401 or 403, so the services no longer accept this session's token.
export interface StackEvent {
  type: 'starting' | 'started' | 'stopping' | 'stopped' | 'failed';
  reason?: 'docker-missing' | 'docker-failed' | 'token-refused';
}

export interface ComposeStackOptions {
  // Directory that holds compose.yaml; it is the working directory of every docker call.
  composeDir: string;
  // Host folder behind the `/exports` mount. Created and validated at every start; see validateExportsFolder.
  exportsDir: string;
  spawn?: SpawnFn;
  createToken?: () => string;
  // Token of services that another process started and checked with it (see takeInheritedSessionToken).
  // When set it replaces createToken, and the request gate is open from construction.
  inheritedToken?: string | null;
  onEvent?: (event: StackEvent) => void;
  // Environment inherited by docker; defaults to process.env.
  baseEnv?: NodeJS.ProcessEnv;
}

export class FolderValidationError extends Error {}

// Returns the canonical path of an existing directory; throws FolderValidationError otherwise.
// Requires an absolute path, resolves symlinks so the mount names the real folder, and refuses files.
export function validatePhotoFolder(folder: unknown): string {
  if (typeof folder !== 'string' || folder.trim() === '' || folder.includes('\0')) {
    throw new FolderValidationError('The photo folder must be a non-empty path.');
  }
  if (!path.isAbsolute(folder)) {
    throw new FolderValidationError('The photo folder must be an absolute path.');
  }
  let real: string;
  try {
    real = fs.realpathSync(folder);
  } catch {
    throw new FolderValidationError('The photo folder does not exist.');
  }
  if (!fs.statSync(real).isDirectory()) {
    throw new FolderValidationError('The photo folder must be a directory.');
  }
  return real;
}

// True when `inner` is `outer` or lies under it. Both must be canonical; Windows paths compare without case.
function isSameOrInside(outer: string, inner: string): boolean {
  const fold = (value: string): string => (process.platform === 'win32' ? value.toLocaleLowerCase() : value);
  const relative = path.relative(fold(outer), fold(inner));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

// Returns the canonical path of the exports folder, creating it when missing; throws FolderValidationError otherwise.
// Requires an absolute path to a directory (a link is resolved to its real folder), and refuses a folder that holds
// the photo folder or lies inside it, so a copy can never be written next to the originals. The folder is created
// here, by the person's own account, because Docker would create a missing bind source as root on Linux.
export function validateExportsFolder(folder: unknown, photos: string): string {
  if (typeof folder !== 'string' || folder.trim() === '' || folder.includes('\0')) {
    throw new FolderValidationError('The exports folder must be a non-empty path.');
  }
  if (!path.isAbsolute(folder)) {
    throw new FolderValidationError('The exports folder must be an absolute path.');
  }
  let real: string;
  try {
    fs.mkdirSync(folder, { recursive: true });
    real = fs.realpathSync(folder);
    if (!fs.statSync(real).isDirectory()) {
      throw new FolderValidationError('The exports folder must be a directory.');
    }
  } catch (error) {
    if (error instanceof FolderValidationError) throw error;
    throw new FolderValidationError('The exports folder cannot be created or used.');
  }
  if (isSameOrInside(photos, real) || isSameOrInside(real, photos)) {
    throw new FolderValidationError('The exports folder and the photo folder must not contain each other.');
  }
  return real;
}

// Returns a fresh random session token: 32 random bytes as hex, so 64 characters.
export function generateSessionToken(): string {
  return randomBytes(TOKEN_BYTES).toString('hex');
}

// Returns the folder that docker compose runs in: AURAFRAME_PROJECT_DIR when it is an absolute path to a folder
// that holds compose.yaml, and `fallback` otherwise. The packaged app lives under release/, away from compose.yaml,
// so the window that start.sh opens learns the project folder from it.
export function composeDirectory(env: NodeJS.ProcessEnv, fallback: string): string {
  const candidate = env[PROJECT_DIR_ENV];
  if (typeof candidate !== 'string' || !path.isAbsolute(candidate) || candidate.includes('\0')) {
    return fallback;
  }
  try {
    return fs.statSync(path.join(candidate, BASE_FILES[0])).isFile() ? candidate : fallback;
  } catch {
    return fallback;
  }
}

export interface InheritedTokenOptions {
  // True only for the development flow (`npm start`), the one place where the token file is read.
  development: boolean;
  // Absolute path of the token file that `./start.sh --backend-only` writes.
  tokenFile: string;
}

// Returns the token of services that another process started, or null when there is none to inherit.
// The environment comes first: start.sh exports the token it gave compose, and the variable is then removed from
// `env`, so programs that main spawns afterwards do not inherit it. In development only, the ignored file written by
// `./start.sh --backend-only` is read instead. A value that is not 64 lowercase hex characters counts as absent,
// so a malformed one never opens the request gate. Never throws.
export function takeInheritedSessionToken(env: NodeJS.ProcessEnv, options: InheritedTokenOptions): string | null {
  const fromEnv = env[TOKEN_ENV];
  delete env[TOKEN_ENV];
  if (typeof fromEnv === 'string' && TOKEN_PATTERN.test(fromEnv)) {
    return fromEnv;
  }
  if (!options.development) {
    return null;
  }
  try {
    const fromFile = fs.readFileSync(options.tokenFile, 'utf8').trim();
    return TOKEN_PATTERN.test(fromFile) ? fromFile : null;
  } catch {
    return null;
  }
}

export class ComposeStack {
  private readonly spawnFn: SpawnFn;
  private readonly onEvent: (event: StackEvent) => void;
  private readonly baseEnv: NodeJS.ProcessEnv;
  // Held only in this process. There is deliberately no getter that an IPC handler could return.
  private readonly token: string;
  // True from a successful `up`, or from construction with an inherited token, until the next start, stop or
  // refused token.
  private up: boolean;
  private stopRequested = false;
  private inFlightUp: Promise<boolean> | null = null;
  // Compose files of the latest `up`; `down` names the same ones. Only the GPU probe changes them.
  private files: string[] = BASE_FILES;

  constructor(private readonly options: ComposeStackOptions) {
    this.spawnFn = options.spawn ?? (nodeSpawn as unknown as SpawnFn);
    this.onEvent = options.onEvent ?? (() => undefined);
    this.baseEnv = options.baseEnv ?? process.env;
    // An inherited token belongs to services that start.sh brought up and checked with it, so earlier results
    // can be fetched at once. A `start` from this window sends the same token, so compose keeps `api` for it.
    this.token = options.inheritedToken ?? (options.createToken ?? generateSessionToken)();
    this.up = Boolean(options.inheritedToken);
  }

  // Returns the Authorization header value for requests that Electron main sends to the gateway.
  // Callers must keep it inside main: it must never reach an IPC reply, an event or a log.
  authorizationHeader(): string {
    return `Bearer ${this.token}`;
  }

  // Returns true when this session's services accept the token: a successful `up`, or an inherited token, with no
  // start, stop or refusal since. While it is false, whatever listens on the gateway port is not known to be ours,
  // so no request may carry the token.
  isUp(): boolean {
    return this.up;
  }

  // Closes the request gate after the gateway refused the token (401 or 403), and reports it as a `failed` event
  // with the reason `token-refused`, once per opening of the gate. The next successful `start` opens it again.
  tokenRefused(): void {
    if (!this.up) return;
    this.up = false;
    this.onEvent({ type: 'failed', reason: 'token-refused' });
  }

  // Starts the stack with the photo folder mounted read-only and the exports folder mounted writable by compose.
  // The GPU overlay joins the compose files when `nvidia-smi -L` succeeds, and stays out otherwise.
  // Resolves to true when docker exits with code 0; rejects with FolderValidationError for a bad photo or exports
  // folder, before anything is spawned. Requires that no stop is running; the caller refuses a start until its stop
  // has resolved.
  async start(folder: unknown): Promise<boolean> {
    const photos = validatePhotoFolder(folder);
    const exportsDir = validateExportsFolder(this.options.exportsDir, photos);
    this.up = false;
    this.onEvent({ type: 'starting' });
    const env = { ...this.baseEnv, [PHOTOS_ENV]: photos, [EXPORTS_ENV]: exportsDir, [TOKEN_ENV]: this.token };
    const attempt = this.launch(env);
    this.inFlightUp = attempt;
    const started = await attempt;
    this.inFlightUp = null;
    this.up = started && !this.stopRequested;
    return started;
  }

  // Stops the stack and keeps its data volumes. Resolves to true when docker exits with code 0.
  // Waits for an `up` in flight first: run in parallel, that `up` could finish after `down` and leave the
  // services running with the photos mounted. Killing it instead is not reliable, because on Windows the compose
  // plugin outlives the docker process that launched it. The wait has no bound here; quit-handling.ts bounds it.
  async stop(): Promise<boolean> {
    this.up = false;
    this.stopRequested = true;
    this.onEvent({ type: 'stopping' });
    try {
      await this.inFlightUp;
      // "down" mounts nothing, so it needs no folder; compose.yaml has defaults for the other variables.
      return await this.run(['down'], { ...this.baseEnv, [TOKEN_ENV]: this.token }, 'stopped');
    } finally {
      this.stopRequested = false;
    }
  }

  // Settles the compose files from the GPU probe, then runs `up`. The probe sits inside the in-flight attempt, so
  // a stop waits for it too.
  private async launch(env: NodeJS.ProcessEnv): Promise<boolean> {
    this.files = (await this.exec('nvidia-smi', ['-L'], this.baseEnv)) === 'ok' ? [...BASE_FILES, GPU_FILE] : BASE_FILES;
    return this.run(['up', '-d', '--wait'], env, 'started');
  }

  // Runs one docker compose command and reports only its outcome.
  private async run(
    composeArgs: string[],
    env: NodeJS.ProcessEnv,
    successType: 'started' | 'stopped',
  ): Promise<boolean> {
    const files = this.files.flatMap((file) => ['-f', file]);
    const outcome = await this.exec('docker', ['compose', '-p', COMPOSE_PROJECT, ...files, ...composeArgs], env);
    if (outcome === 'ok') {
      this.onEvent({ type: successType });
    } else {
      this.onEvent({ type: 'failed', reason: outcome === 'missing' ? 'docker-missing' : 'docker-failed' });
    }
    return outcome === 'ok';
  }

  // Spawns one command without a shell and resolves to its outcome: 'ok' for exit code 0, 'missing' when the
  // program cannot be started, 'failed' for any other exit. It never rejects and never exposes output.
  private exec(command: string, args: string[], env: NodeJS.ProcessEnv): Promise<'ok' | 'failed' | 'missing'> {
    return new Promise((resolve) => {
      let child: SpawnedProcess;
      try {
        child = this.spawnFn(command, args, {
          cwd: this.options.composeDir,
          env,
          shell: false,
          stdio: 'ignore',
          windowsHide: true,
        });
      } catch {
        resolve('missing');
        return;
      }
      let settled = false;
      const finish = (outcome: 'ok' | 'failed' | 'missing'): void => {
        if (settled) return;
        settled = true;
        resolve(outcome);
      };
      child.on('error', () => finish('missing'));
      child.on('close', (code) => finish(code === 0 ? 'ok' : 'failed'));
    });
  }
}

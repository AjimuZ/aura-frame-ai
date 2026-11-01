// Starts and stops the Docker Compose stack for the Electron main process and holds the per-session API token.
// Docker runs through spawn with shell: false. The chosen photo folder and the token reach compose only
// through the child environment, never through argv, so neither shows up in a process listing or a log line.

import { randomBytes } from 'node:crypto';
import { spawn as nodeSpawn } from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

export const COMPOSE_PROJECT = 'auraframe';
export const PHOTOS_ENV = 'AURAFRAME_PHOTOS_DIR';
export const TOKEN_ENV = 'AURAFRAME_API_TOKEN';
const TOKEN_BYTES = 32;

// Minimal view of a spawned process, so tests can inject a fake without a real Docker.
export interface SpawnedProcess {
  on(event: 'error', listener: (error: Error) => void): unknown;
  on(event: 'close', listener: (code: number | null) => void): unknown;
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptions) => SpawnedProcess;

// Events carry the action and outcome only: never the folder, the token or the process output.
export interface StackEvent {
  type: 'starting' | 'started' | 'stopping' | 'stopped' | 'failed';
  reason?: 'docker-missing' | 'docker-failed';
}

export interface ComposeStackOptions {
  // Directory that holds compose.yaml; it is the working directory of every docker call.
  composeDir: string;
  spawn?: SpawnFn;
  createToken?: () => string;
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

// Returns a fresh random session token: 32 random bytes as hex, so 64 characters.
export function generateSessionToken(): string {
  return randomBytes(TOKEN_BYTES).toString('hex');
}

export class ComposeStack {
  private readonly spawnFn: SpawnFn;
  private readonly onEvent: (event: StackEvent) => void;
  private readonly baseEnv: NodeJS.ProcessEnv;
  // Held only in this process. There is deliberately no getter that an IPC handler could return.
  private readonly token: string;

  constructor(private readonly options: ComposeStackOptions) {
    this.spawnFn = options.spawn ?? (nodeSpawn as unknown as SpawnFn);
    this.onEvent = options.onEvent ?? (() => undefined);
    this.baseEnv = options.baseEnv ?? process.env;
    this.token = (options.createToken ?? generateSessionToken)();
  }

  // Returns the Authorization header value for requests that Electron main sends to the gateway.
  // Callers must keep it inside main: it must never reach an IPC reply, an event or a log.
  authorizationHeader(): string {
    return `Bearer ${this.token}`;
  }

  // Starts the stack with the photo folder mounted read-only by compose.
  // Resolves to true when docker exits with code 0; rejects with FolderValidationError for a bad folder.
  async start(folder: unknown): Promise<boolean> {
    const photos = validatePhotoFolder(folder);
    this.onEvent({ type: 'starting' });
    const env = { ...this.baseEnv, [PHOTOS_ENV]: photos, [TOKEN_ENV]: this.token };
    return this.run(['up', '-d', '--wait'], env, 'started');
  }

  // Stops the stack and keeps its data volumes. Resolves to true when docker exits with code 0.
  async stop(): Promise<boolean> {
    this.onEvent({ type: 'stopping' });
    // "down" mounts nothing, so it needs no folder; compose.yaml has defaults for the other variables.
    return this.run(['down'], { ...this.baseEnv, [TOKEN_ENV]: this.token }, 'stopped');
  }

  // Runs one docker compose command and reports only its outcome.
  private run(composeArgs: string[], env: NodeJS.ProcessEnv, successType: 'started' | 'stopped'): Promise<boolean> {
    const args = ['compose', '-p', COMPOSE_PROJECT, '-f', 'compose.yaml', ...composeArgs];
    return new Promise((resolve) => {
      let child: SpawnedProcess;
      try {
        child = this.spawnFn('docker', args, {
          cwd: this.options.composeDir,
          env,
          shell: false,
          stdio: 'ignore',
          windowsHide: true,
        });
      } catch {
        this.onEvent({ type: 'failed', reason: 'docker-missing' });
        resolve(false);
        return;
      }
      let settled = false;
      const finish = (ok: boolean, reason?: StackEvent['reason']): void => {
        if (settled) return;
        settled = true;
        this.onEvent(ok ? { type: successType } : { type: 'failed', reason });
        resolve(ok);
      };
      child.on('error', () => finish(false, 'docker-missing'));
      child.on('close', (code) => finish(code === 0, 'docker-failed'));
    });
  }
}

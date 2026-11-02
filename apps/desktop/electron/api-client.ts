// HTTP client of the engine API for the Electron main process: the only place that attaches the session token.
// It talks to the gateway on 127.0.0.1 only, sends nothing until this session's services are up, and never follows
// a redirect. Failures carry fixed messages, never server text or socket details, and any answer that contains
// the token is refused before a caller sees it.

import * as http from 'node:http';
import type { ComposeStack } from './compose-stack';
import type { AnalysisStatus, AnalysisStep, AnalysisPreset, ReviewSession } from './ipc/contracts';

export const PORT_ENV = 'AURAFRAME_PORT';
export const DEFAULT_GATEWAY_PORT = 8420;
const LOOPBACK_HOST = '127.0.0.1';

const REQUEST_TIMEOUT_MS = 30_000;
// Exports and data removal answer only when the work is done, so they may stay silent for much longer.
const SLOW_REQUEST_TIMEOUT_MS = 300_000;
// The API sends a keepalive comment every 15 seconds, so three missed ones mean the stream is dead.
const STREAM_IDLE_TIMEOUT_MS = 45_000;
const MAX_RESPONSE_BYTES = 256 * 1024 * 1024;
const MAX_EVENT_CHARS = 1024 * 1024;
// Thumbnails are 512 px on the long side, so a real answer stays far below this.
const MAX_MEDIA_BYTES = 16 * 1024 * 1024;
// A grid asks for all its thumbnails at once, and the gateway has 256 connections, of which each proxied request
// uses two. Past this many, the other requests wait in this client instead of starving the API calls.
const MAX_MEDIA_REQUESTS = 8;
// What the API serves and the renderer can show. SVG is left out because it can carry script.
const MEDIA_CONTENT_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

const UNREACHABLE_MESSAGE = 'The analysis service is not reachable.';
const TIMEOUT_MESSAGE = 'The analysis service did not answer in time.';
const BAD_RESPONSE_MESSAGE = 'The analysis service sent an unusable answer.';
const REFUSED_MESSAGE = 'The analysis service refused the request.';
// Keyed by the API's stable error codes. Server text is deliberately not forwarded.
const REFUSAL_MESSAGES = new Map<string, string>([
  ['run_in_progress', 'An analysis is already running.'],
  ['no_active_run', 'This library has no running analysis.'],
  ['run_cancelling', 'The analysis is being cancelled.'],
  ['photos_unavailable', 'No photo folder is available to analyze.'],
  ['engine_unavailable', 'The analysis engine could not be started.'],
  ['unknown_photo_ids', 'Some photos are not part of this library.'],
  ['photo_unavailable', 'Some photos are no longer available.'],
  ['export_folder_unsafe', 'The export folder cannot be used.'],
  ['export_folder_unwritable', 'The export folder cannot be written by the analysis service. See docs/operations.md.'],
  ['data_folder_unwritable', 'The generated data could not be removed.'],
]);

export const REQUEST_FAILED_MESSAGE = 'The request to the analysis service failed.';

// Returns the text of a failure that may reach the renderer. Anything but a client error gets a fixed message,
// because other errors can carry a path, an address or a token.
export function safeApiMessage(error: unknown): string {
  return error instanceof ApiClientError ? error.message : REQUEST_FAILED_MESSAGE;
}

// `message` is safe to show to the person: it holds no token, path, address or server text.
export class ApiClientError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

export interface ApiClientOptions {
  // Port of the gateway on 127.0.0.1. There is deliberately no host option.
  port: number;
  // Supplies the Authorization header, read per request and never stored or returned, and says whether a
  // request may be sent at all. One object, so the token cannot be wired without its gate.
  session: Pick<ComposeStack, 'authorizationHeader' | 'isUp'>;
  // Limit on one whole media exchange. Injected so a test can use a short one.
  mediaTimeoutMs?: number;
}

// One image from the API. `contentType` is one of MEDIA_CONTENT_TYPES, without parameters.
export interface MediaFile {
  contentType: string;
  bytes: Buffer;
}

// The options of one run. A folder is deliberately absent: the API analyzes its mounted photos root.
export interface RunOptions {
  runGemma: boolean;
  preset: AnalysisPreset;
  includeSubfolders: boolean;
}

// Returns the gateway port from the environment that compose also reads, or the default when unset or invalid.
export function gatewayPort(env: NodeJS.ProcessEnv): number {
  const port = Number(env[PORT_ENV]);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : DEFAULT_GATEWAY_PORT;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function badResponse(): ApiClientError {
  return new ApiClientError('bad_response', BAD_RESPONSE_MESSAGE);
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

// Returns the bridge status for an API RunStatus body; throws ApiClientError when it is not one.
function toRunStatus(libraryId: string, body: unknown): AnalysisStatus {
  if (!isRecord(body) || typeof body['step'] !== 'string') {
    throw badResponse();
  }
  return {
    libraryId,
    step: body['step'] as AnalysisStep,
    running: body['running'] === true,
    paused: body['paused'] === true,
    current: count(body['current']),
    total: count(body['total']),
    message: typeof body['message'] === 'string' ? body['message'] : '',
    ...(typeof body['startedAt'] === 'string' ? { startedAt: body['startedAt'] } : {}),
    ...(typeof body['finishedAt'] === 'string' ? { finishedAt: body['finishedAt'] } : {}),
  };
}

function libraryPath(libraryId: string, suffix: string): string {
  return `/api/libraries/${encodeURIComponent(libraryId)}${suffix}`;
}

// Returns a feeder for SSE text that calls `dispatch` with the data of each complete frame and ignores
// comments and other fields. The feeder returns false when a single frame grows past MAX_EVENT_CHARS.
function createSseParser(dispatch: (data: string) => void): (chunk: string) => boolean {
  let buffer = '';
  let data: string[] = [];
  let pending = 0;
  return (chunk) => {
    buffer += chunk;
    let newline = buffer.indexOf('\n');
    while (newline !== -1) {
      const line = buffer.slice(0, newline).replace(/\r$/, '');
      buffer = buffer.slice(newline + 1);
      if (line === '') {
        if (data.length > 0) dispatch(data.join('\n'));
        data = [];
        pending = 0;
      } else if (line.startsWith('data:')) {
        const value = line.slice(5).replace(/^ /, '');
        data.push(value);
        pending += value.length;
      }
      newline = buffer.indexOf('\n');
    }
    return buffer.length + pending <= MAX_EVENT_CHARS;
  };
}

export class ApiClient {
  private readonly port: number;
  private mediaRequests = 0;
  private readonly mediaQueue: (() => void)[] = [];

  // Throws RangeError when the port is not a valid TCP port.
  constructor(private readonly options: ApiClientOptions) {
    if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65535) {
      throw new RangeError('The gateway port is invalid.');
    }
    this.port = options.port;
  }

  // Starts a run and returns its initial status.
  async startRun(libraryId: string, options: RunOptions): Promise<AnalysisStatus> {
    return toRunStatus(libraryId, await this.call('POST', libraryPath(libraryId, '/runs'), options));
  }

  // Returns the status of the library's latest run, or its idle status.
  async getRunStatus(libraryId: string): Promise<AnalysisStatus> {
    return toRunStatus(libraryId, await this.call('GET', libraryPath(libraryId, '/runs/current')));
  }

  async pauseRun(libraryId: string): Promise<AnalysisStatus> {
    return toRunStatus(libraryId, await this.call('POST', libraryPath(libraryId, '/runs/current/pause')));
  }

  async resumeRun(libraryId: string): Promise<AnalysisStatus> {
    return toRunStatus(libraryId, await this.call('POST', libraryPath(libraryId, '/runs/current/resume')));
  }

  async cancelRun(libraryId: string): Promise<AnalysisStatus> {
    return toRunStatus(libraryId, await this.call('POST', libraryPath(libraryId, '/runs/current/cancel')));
  }

  // Returns the stored photo records of a library; empty when it has none.
  async listPhotos(libraryId: string): Promise<Record<string, unknown>[]> {
    const body = await this.call('GET', libraryPath(libraryId, '/photos'));
    if (!Array.isArray(body) || !body.every(isRecord)) {
      throw badResponse();
    }
    return body;
  }

  // Returns the latest saved review session, or null when none was saved.
  async getReview(libraryId: string): Promise<ReviewSession | null> {
    const body = await this.call('GET', libraryPath(libraryId, '/review'));
    if (body !== null && !isRecord(body)) {
      throw badResponse();
    }
    return body as ReviewSession | null;
  }

  async saveReview(libraryId: string, session: ReviewSession): Promise<void> {
    await this.call('PUT', libraryPath(libraryId, '/review'), session);
  }

  async clearData(libraryId: string): Promise<void> {
    await this.call('DELETE', libraryPath(libraryId, '/data'), undefined, SLOW_REQUEST_TIMEOUT_MS);
  }

  async exportPhotos(libraryId: string, ids: string[]): Promise<void> {
    await this.call('POST', libraryPath(libraryId, '/exports'), { ids }, SLOW_REQUEST_TIMEOUT_MS);
  }

  // Returns the generated thumbnail of one photo. It always asks for that variant, so this client cannot fetch an
  // original. Rejects with ApiClientError while the services are not up, and as fetchMedia describes.
  async getThumbnail(libraryId: string, photoId: string): Promise<MediaFile> {
    const path = `${libraryPath(libraryId, `/media/${encodeURIComponent(photoId)}`)}?variant=thumbnail`;
    await this.acquireMediaSlot();
    try {
      return await this.fetchMedia(path);
    } finally {
      this.releaseMediaSlot();
    }
  }

  // Follows the library's event stream and calls `onEvent` for each well formed JSON object, in order.
  // Resolves when the server ends the stream or `signal` aborts; rejects with ApiClientError on a refusal,
  // a broken connection or an idle stream. The API replays a run's events from the start on every connection.
  streamEvents(
    libraryId: string,
    onEvent: (event: Record<string, unknown>) => void,
    signal: AbortSignal,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) {
        resolve();
        return;
      }
      const request = this.open('GET', libraryPath(libraryId, '/events'), 'text/event-stream');
      const onAbort = (): void => {
        resolve();
        request.destroy();
      };
      const fail = (error: ApiClientError): void => {
        signal.removeEventListener('abort', onAbort);
        reject(error);
        request.destroy();
      };
      signal.addEventListener('abort', onAbort, { once: true });
      request.setTimeout(STREAM_IDLE_TIMEOUT_MS, () => fail(new ApiClientError('timeout', TIMEOUT_MESSAGE)));
      request.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
      request.on('response', (response) => {
        if (response.statusCode !== 200) {
          response.resume();
          fail(new ApiClientError('refused', REFUSED_MESSAGE, response.statusCode));
          return;
        }
        const feed = createSseParser((data) => {
          const event = this.parseFrame(data);
          if (event) onEvent(event);
        });
        response.setEncoding('utf8');
        response.on('data', (chunk: string) => {
          if (!feed(chunk)) fail(badResponse());
        });
        response.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
        response.on('end', () => {
          signal.removeEventListener('abort', onAbort);
          resolve();
        });
      });
      request.end();
    });
  }

  // Returns the parsed frame, or null for a frame that is not a JSON object or that contains the token.
  private parseFrame(data: string): Record<string, unknown> | null {
    if (data.includes(this.secret())) return null;
    try {
      const parsed: unknown = JSON.parse(data);
      return isRecord(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }

  // Returns the token alone, so answers can be checked for it without keeping a copy in this object.
  private secret(): string {
    const header = this.options.session.authorizationHeader();
    return header.slice(header.indexOf(' ') + 1);
  }

  // Builds a request with the token and the Host header. Each request uses its own connection (no agent).
  // Throws ApiClientError while the services are not up, because the token cannot work then and sending it
  // would only hand it to whatever else listens on the port. Every caller runs this inside a Promise executor,
  // so the throw becomes their rejection.
  private open(method: string, path: string, accept: string, payload?: string): http.ClientRequest {
    if (!this.options.session.isUp()) {
      throw new ApiClientError('unreachable', UNREACHABLE_MESSAGE);
    }
    return http.request({
      host: LOOPBACK_HOST,
      port: this.port,
      method,
      path,
      agent: false,
      headers: {
        Host: `${LOOPBACK_HOST}:${this.port}`,
        Authorization: this.options.session.authorizationHeader(),
        Accept: accept,
        ...(payload === undefined
          ? {}
          : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }),
      },
    });
  }

  // Sends one JSON request and resolves to the parsed answer, null when it is empty.
  // Rejects with ApiClientError for a refused connection, a timeout, a refusal or an unusable answer.
  private call(method: string, path: string, body?: unknown, timeoutMs = REQUEST_TIMEOUT_MS): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const payload = body === undefined ? undefined : JSON.stringify(body);
      const request = this.open(method, path, 'application/json', payload);
      const fail = (error: ApiClientError): void => {
        reject(error);
        request.destroy();
      };
      request.setTimeout(timeoutMs, () => fail(new ApiClientError('timeout', TIMEOUT_MESSAGE)));
      request.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
      request.on('response', (response) => {
        const chunks: Buffer[] = [];
        let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > MAX_RESPONSE_BYTES) {
            fail(badResponse());
            return;
          }
          chunks.push(chunk);
        });
        response.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
        response.on('end', () => {
          try {
            resolve(this.interpret(response.statusCode ?? 0, Buffer.concat(chunks).toString('utf8')));
          } catch (error) {
            reject(error as ApiClientError);
          }
        });
      });
      request.end(payload);
    });
  }

  // Resolves when a media request may be sent. The caller must call releaseMediaSlot afterwards.
  private async acquireMediaSlot(): Promise<void> {
    if (this.mediaRequests < MAX_MEDIA_REQUESTS) {
      this.mediaRequests += 1;
      return;
    }
    await new Promise<void>((resolve) => this.mediaQueue.push(resolve));
  }

  // Hands the slot to the oldest waiting request, which keeps the count unchanged, or frees it.
  private releaseMediaSlot(): void {
    const next = this.mediaQueue.shift();
    if (next) next();
    else this.mediaRequests -= 1;
  }

  // Sends one media request and resolves to its image. The gate is read here, when the request is really sent, so
  // one that waited for a slot cannot go out after the services stopped. Rejects with ApiClientError for a refused
  // connection, any status but 200, a type outside MEDIA_CONTENT_TYPES, an answer over MAX_MEDIA_BYTES or holding
  // the token, a broken answer, and an exchange that outlasts its limit, even if bytes keep arriving.
  private fetchMedia(path: string): Promise<MediaFile> {
    return new Promise((resolve, reject) => {
      const request = this.open('GET', path, [...MEDIA_CONTENT_TYPES].join(', '));
      const fail = (error: ApiClientError): void => {
        clearTimeout(deadline);
        reject(error);
        request.destroy();
      };
      const deadline = setTimeout(
        () => fail(new ApiClientError('timeout', TIMEOUT_MESSAGE)),
        this.options.mediaTimeoutMs ?? REQUEST_TIMEOUT_MS,
      );
      request.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
      request.on('response', (response) => {
        const contentType = (response.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase();
        if (response.statusCode !== 200 || !MEDIA_CONTENT_TYPES.has(contentType)) {
          fail(new ApiClientError('refused', REFUSED_MESSAGE, response.statusCode));
          return;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > MAX_MEDIA_BYTES) {
            fail(badResponse());
            return;
          }
          chunks.push(chunk);
        });
        response.on('error', () => fail(new ApiClientError('unreachable', UNREACHABLE_MESSAGE)));
        response.on('end', () => {
          clearTimeout(deadline);
          const bytes = Buffer.concat(chunks);
          if (bytes.includes(this.secret())) {
            reject(badResponse());
            return;
          }
          resolve({ contentType, bytes });
        });
      });
      request.end();
    });
  }

  // Returns the parsed body of a success; throws ApiClientError for a refusal or an unusable body.
  private interpret(status: number, text: string): unknown {
    if (text.includes(this.secret())) {
      throw badResponse();
    }
    let body: unknown = null;
    try {
      body = text === '' ? null : JSON.parse(text);
    } catch {
      if (status >= 200 && status < 300) throw badResponse();
    }
    if (status >= 200 && status < 300) {
      return body;
    }
    const code = isRecord(body) && typeof body['code'] === 'string' ? body['code'] : 'refused';
    throw new ApiClientError(code, REFUSAL_MESSAGES.get(code) ?? REFUSED_MESSAGE, status);
  }
}

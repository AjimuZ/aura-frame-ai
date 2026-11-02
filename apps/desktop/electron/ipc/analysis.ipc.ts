// Library-aware analysis IPC handlers: each one is served by the engine API over HTTP, through the
// main-only API client. The renderer sees the same channels and shapes, and never the session token.

import { BrowserWindow, ipcMain } from 'electron';
import { ApiClient, SERVICES_UNAVAILABLE_MESSAGE, safeApiMessage } from '../api-client';
import type { RunOptions } from '../api-client';
import { validatePhotoFolder } from '../compose-stack';
import type { ComposeStack } from '../compose-stack';
import type {
  AnalysisConfig,
  AnalysisEvent,
  AnalysisPreset,
  AnalysisStatus,
  OpenAiCostEstimate,
  OpenAiStatus,
  PhotoItem,
  ProvidersStatus,
  ReviewSession,
} from './contracts';
import { readOpenAiKey } from './settings.ipc';
import type { LibraryRegistryStore } from './library-store';

type WindowsProvider = () => BrowserWindow[];

// What the handlers need from main. Injected, so a test can use a fake server and a fake Docker.
export interface AnalysisBackend {
  api: ApiClient;
  stack: Pick<ComposeStack, 'start' | 'stop'>;
  reconnectDelayMs?: number;
}

// The run this session owns, from the moment it is requested until its stream is over.
interface ActiveRun {
  libraryId: string;
  // `starting` until the API accepted the run: Docker may need minutes to bring the services up.
  phase: 'starting' | 'running';
  cancelled: boolean;
  pauseOnStart: boolean;
  sawTerminalEvent: boolean;
  abort: AbortController;
}

let activeRun: ActiveRun | null = null;
// Set once this session asked Docker to start the services, so closing the window stops only what it started.
let stackToStop: AnalysisBackend['stack'] | null = null;
// Set while the services are being stopped, so that no new start can bring them back behind that stop.
let disposal: Promise<void> | null = null;
let getWindows: WindowsProvider = () => [];
let getActiveLibraryId: () => string | null = () => null;

const statuses = new Map<string, AnalysisStatus>();

const PRESETS = new Set<AnalysisPreset>(['fast', 'balanced', 'quality']);
// Only what a local run emits. The legacy cloud events are not forwarded: no run can produce them here.
const FORWARDED_EVENTS = new Set(['progress', 'photo_scored', 'log', 'done', 'error', 'local_gate_progress']);
const PROGRESS_EVENTS = new Set(['progress', 'local_gate_progress']);

// A connection that delivers no event this many times in a row counts as lost.
const MAX_STREAM_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 1000;

const ALREADY_RUNNING_MESSAGE = 'An analysis is already running.';
const STACK_FAILED_MESSAGE =
  'The analysis services could not be started. Check that Docker is installed and running.';
const CONTACT_LOST_MESSAGE = 'Lost contact with the analysis service.';
const STOPPED_MESSAGE = 'The analysis stopped without reporting a result.';
const STOPPING_MESSAGE = 'The analysis services are stopping.';
// The legacy cloud flow has no API route, and it must not be extended before package P05 removes it.
const CLOUD_UNAVAILABLE_MESSAGE = 'The cloud review mode is not available in this version.';

function idleStatus(libraryId: string): AnalysisStatus {
  return {
    libraryId,
    step: 'idle',
    running: false,
    paused: false,
    current: 0,
    total: 0,
    message: 'Idle',
  };
}

function statusFor(libraryId: string): AnalysisStatus {
  const current = statuses.get(libraryId) ?? idleStatus(libraryId);
  statuses.set(libraryId, current);
  return current;
}

function broadcast(event: AnalysisEvent): void {
  for (const window of getWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send('analysis:event', event);
    }
  }
}

function applyEvent(event: AnalysisEvent): void {
  const status = statusFor(event.libraryId);
  if (event.type === 'progress') {
    Object.assign(status, {
      step: event.step as AnalysisStatus['step'],
      current: event.current,
      total: event.total,
      message: event.message,
    });
  } else if (event.type === 'done') {
    Object.assign(status, {
      step: 'done',
      running: false,
      paused: false,
      message: event.message,
      finishedAt: new Date().toISOString(),
    });
  } else if (event.type === 'error') {
    Object.assign(status, {
      step: 'error',
      running: false,
      paused: false,
      message: event.message,
      finishedAt: new Date().toISOString(),
    });
  } else if (event.type === 'local_gate_progress') {
    Object.assign(status, {
      step: 'local_gate',
      current: event.current,
      total: event.total,
      message: event.message,
    });
  }
  broadcast(event);
}

function assertLibrary(libraries: LibraryRegistryStore, libraryId: string): void {
  libraries.getLibrary(libraryId);
}

// Returns the run options the API accepts. It refuses unknown fields, so only these three are ever sent.
function toRunOptions(config: AnalysisConfig | undefined): RunOptions {
  const preset = config?.preset;
  return {
    runGemma: config?.runGemma === true,
    preset: preset && PRESETS.has(preset) ? preset : 'balanced',
    includeSubfolders: config?.includeSubfolders !== false,
  };
}

// Refuses a start. The window already shows "Starting...", and a rejection alone would leave it there, so the
// refusal also goes out as an error event. The event is broadcast without touching the stored status: a refusal
// must not rewrite the state of the run that caused it, whose own progress then sets the window right again.
function refuseStart(libraryId: string, message: string): never {
  broadcast({ type: 'error', libraryId, message });
  throw new Error(message);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Ends the run with an error event. Does nothing when the run is no longer the active one.
function failRun(run: ActiveRun, message: string): void {
  if (activeRun !== run) return;
  activeRun = null;
  applyEvent({ type: 'error', libraryId: run.libraryId, message });
}

// Ends a run whose stream is over. A run that closed without a terminal event still has to tell the renderer,
// which otherwise keeps showing it as running: the event is derived from the API status.
function finishRun(run: ActiveRun, remote: AnalysisStatus): void {
  if (activeRun !== run) return;
  activeRun = null;
  statuses.set(run.libraryId, remote);
  if (run.sawTerminalEvent || run.cancelled) return;
  if (remote.step === 'done') {
    applyEvent({ type: 'done', libraryId: run.libraryId, message: remote.message });
  } else {
    applyEvent({ type: 'error', libraryId: run.libraryId, message: STOPPED_MESSAGE });
  }
}

// Forwards one API event in the bridge shape, under the run's library whatever the frame claims.
// After a cancel, progress is dropped: each progress event sets the renderer back to "running".
function forwardEvent(run: ActiveRun, raw: Record<string, unknown>): void {
  const type = raw['type'];
  if (activeRun !== run || typeof type !== 'string' || !FORWARDED_EVENTS.has(type)) return;
  if (run.cancelled && PROGRESS_EVENTS.has(type)) return;
  if (type === 'done' || type === 'error') run.sawTerminalEvent = true;
  applyEvent({ ...raw, libraryId: run.libraryId } as unknown as AnalysisEvent);
}

// Follows the run's events until it is over, reconnecting while the API still reports it as running.
// Each connection replays the run from its start, so the frames already delivered are skipped. If the API's
// buffer dropped old events the skip can lose newer ones; finishRun still reports the outcome from the status.
async function followRun(backend: AnalysisBackend, run: ActiveRun): Promise<void> {
  const { libraryId, abort } = run;
  let delivered = 0;
  let attempts = 0;
  while (!abort.signal.aborted) {
    const before = delivered;
    let skip = delivered;
    try {
      await backend.api.streamEvents(
        libraryId,
        (raw) => {
          if (skip > 0) {
            skip -= 1;
            return;
          }
          delivered += 1;
          forwardEvent(run, raw);
        },
        abort.signal,
      );
    } catch {
      // The status below decides whether to reconnect, so this failure needs no handling of its own.
    }
    if (abort.signal.aborted) return;
    const remote = await backend.api.getRunStatus(libraryId).catch(() => null);
    if (remote && !remote.running) {
      finishRun(run, remote);
      return;
    }
    attempts = delivered > before ? 0 : attempts + 1;
    if (attempts >= MAX_STREAM_ATTEMPTS) {
      failRun(run, CONTACT_LOST_MESSAGE);
      return;
    }
    await delay(backend.reconnectDelayMs ?? RECONNECT_DELAY_MS);
  }
}

// Brings the services up with the folder mounted, starts the run and follows it. Never rejects: every failure
// ends the run with an error event. Compose recreates `api` only when the folder or the token changed.
async function launchRun(
  backend: AnalysisBackend,
  run: ActiveRun,
  folder: string,
  options: RunOptions,
): Promise<void> {
  const { libraryId } = run;
  try {
    stackToStop = backend.stack;
    if (!(await backend.stack.start(folder))) {
      failRun(run, STACK_FAILED_MESSAGE);
      return;
    }
    if (activeRun !== run) return;
    if (run.cancelled) {
      activeRun = null;
      Object.assign(statusFor(libraryId), { running: false, step: 'idle', message: 'Analysis cancelled.' });
      return;
    }
    const started = await backend.api.startRun(libraryId, options);
    if (activeRun !== run) return;
    statuses.set(libraryId, started);
    run.phase = 'running';
    void followRun(backend, run);
  } catch (error) {
    failRun(run, safeApiMessage(error));
    return;
  }
  // The requests made while the run was starting. If they fail the run simply continues, and the stream shows it:
  // a failed cancel clears the flag, because the flag would otherwise drop that run's progress.
  if (run.cancelled) {
    await backend.api.cancelRun(libraryId).catch(() => {
      run.cancelled = false;
    });
  } else if (run.pauseOnStart) {
    await backend.api.pauseRun(libraryId).catch(() => null);
  }
}

// Pauses or resumes this session's run. A run that is still starting applies the request once it exists.
async function setPaused(backend: AnalysisBackend, libraryId: string, paused: boolean): Promise<void> {
  const run = activeRun;
  if (run?.libraryId !== libraryId) return;
  statusFor(libraryId).paused = paused;
  if (run.phase === 'starting') {
    run.pauseOnStart = paused;
    return;
  }
  statuses.set(libraryId, await (paused ? backend.api.pauseRun(libraryId) : backend.api.resumeRun(libraryId)));
}

// Returns a stored photo record in the bridge shape, under the requested library. Its paths stay in main: the renderer
// only learns whether a thumbnail exists, and `afmedia` finds it by photo ID.
function toBridgePhoto(libraryId: string, record: Record<string, unknown>): PhotoItem {
  const thumbnailPath = record['thumbnailPath'];
  const photo: Record<string, unknown> = {
    ...record,
    libraryId,
    hasThumbnail: typeof thumbnailPath === 'string' && thumbnailPath !== '',
  };
  delete photo['filePath'];
  delete photo['thumbnailPath'];
  return photo as unknown as PhotoItem;
}

// Rethrows an API failure as a plain Error with its fixed message, the only part that crosses IPC.
async function guarded<T>(action: () => Promise<T>): Promise<T> {
  try {
    return await action();
  } catch (error) {
    throw new Error(safeApiMessage(error));
  }
}

export function isAnalysisRunning(): boolean {
  return activeRun !== null;
}

// Tells the windows that the services refused the session token, as an error event with a fixed message. A
// followed run can no longer be reached, so it ends with that error. A run still starting is left alone: its launch
// reports its own failures, and its `up` reopens the gate. Otherwise the event goes to the active library without touching its stored status,
// because the refusal says nothing about how its last run ended.
export function reportServicesUnavailable(): void {
  const run = activeRun;
  if (run?.phase === 'running') {
    run.abort.abort();
    failRun(run, SERVICES_UNAVAILABLE_MESSAGE);
    return;
  }
  if (run) return;
  const libraryId = getActiveLibraryId();
  if (libraryId) broadcast({ type: 'error', libraryId, message: SERVICES_UNAVAILABLE_MESSAGE });
}

export function registerAnalysisIpc(
  windowsProvider: WindowsProvider,
  libraries: LibraryRegistryStore,
  backend: AnalysisBackend,
): void {
  getWindows = windowsProvider;
  getActiveLibraryId = () => libraries.getState().activeLibraryId;
  const { api } = backend;

  // Resolves as soon as the run is requested. Its outcome reaches the renderer as events, because bringing
  // the services up can take minutes. The folder goes to the stack only; the API request never names a path.
  ipcMain.handle('analysis:start', (_event, libraryId: string, config: AnalysisConfig): void => {
    assertLibrary(libraries, libraryId);
    if (isAnalysisRunning()) {
      refuseStart(libraryId, ALREADY_RUNNING_MESSAGE);
    }
    if (disposal) {
      refuseStart(libraryId, STOPPING_MESSAGE);
    }
    let folder: string;
    try {
      folder = validatePhotoFolder(config?.inputFolder);
    } catch {
      applyEvent({ type: 'error', libraryId, message: 'Invalid or missing photo folder.' });
      return;
    }
    const run: ActiveRun = {
      libraryId,
      phase: 'starting',
      cancelled: false,
      pauseOnStart: false,
      sawTerminalEvent: false,
      abort: new AbortController(),
    };
    activeRun = run;
    statuses.set(libraryId, {
      ...idleStatus(libraryId),
      step: 'indexing',
      running: true,
      message: 'Starting the analysis services.',
      startedAt: new Date().toISOString(),
    });
    void launchRun(backend, run, folder, toRunOptions(config));
  });

  ipcMain.handle('analysis:pause', (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    return guarded(() => setPaused(backend, libraryId, true));
  });

  ipcMain.handle('analysis:resume', (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    return guarded(() => setPaused(backend, libraryId, false));
  });

  // Asks the run to stop. The run stays active until its stream ends, so a new one cannot race the old one.
  // Rejects when the API call fails. The run then goes on, so its state is restored and its progress flows again.
  ipcMain.handle('analysis:cancel', async (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    const run = activeRun;
    if (run?.libraryId !== libraryId) return;
    run.cancelled = true;
    const status = statusFor(libraryId);
    const before = { paused: status.paused, message: status.message };
    Object.assign(status, { paused: false, message: 'Cancelling the analysis.' });
    if (run.phase !== 'running') return;
    try {
      statuses.set(libraryId, await guarded(() => api.cancelRun(libraryId)));
    } catch (error) {
      run.cancelled = false;
      if (activeRun === run) Object.assign(statusFor(libraryId), before);
      throw error;
    }
  });

  // Reports the API's status. While the services are starting or down, the last known status stands in.
  // That includes the time before this session's first start, when the client sends nothing.
  ipcMain.handle('analysis:status', async (_event, libraryId: string): Promise<AnalysisStatus> => {
    assertLibrary(libraries, libraryId);
    if (activeRun?.libraryId === libraryId && activeRun.phase === 'starting') {
      return { ...statusFor(libraryId) };
    }
    try {
      const remote = await api.getRunStatus(libraryId);
      statuses.set(libraryId, remote);
      return { ...remote };
    } catch {
      return { ...statusFor(libraryId) };
    }
  });

  // Returns no photos when the services are down, as it did when the engine had produced no output.
  ipcMain.handle('photos:get', async (_event, libraryId: string): Promise<PhotoItem[]> => {
    assertLibrary(libraries, libraryId);
    try {
      const records = await api.listPhotos(libraryId);
      return records.map((record) => toBridgePhoto(libraryId, record));
    } catch {
      return [];
    }
  });

  // Nothing is sent for an empty selection: the API requires at least one id, and there is nothing to copy.
  ipcMain.handle('export:finalSet', async (_event, libraryId: string, photoIds: string[]): Promise<void> => {
    assertLibrary(libraries, libraryId);
    const ids = Array.isArray(photoIds) ? photoIds.filter((id) => typeof id === 'string') : [];
    if (ids.length > 0) {
      await guarded(() => api.exportPhotos(libraryId, ids));
    }
  });

  // The one provider check: it asks `GET /api/providers` and never rejects, so Settings can always show an answer.
  // While the services are down, the answer says so with an empty list and sends no request.
  ipcMain.handle('providers:check', async (): Promise<ProvidersStatus> => {
    try {
      const providers = await api.getProviders();
      return { reachable: true, providers, message: 'Providers checked.' };
    } catch (error) {
      return { reachable: false, providers: [], message: safeApiMessage(error) };
    }
  });

  ipcMain.handle('data:clear', async (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    if (isAnalysisRunning()) throw new Error('Cannot clear data during an analysis.');
    await guarded(() => api.clearData(libraryId));
    statuses.delete(libraryId);
  });

  // Returns null when the services are down, the same answer as a library with no saved session.
  ipcMain.handle('review:get', async (_event, libraryId: string): Promise<ReviewSession | null> => {
    assertLibrary(libraries, libraryId);
    return api.getReview(libraryId).catch(() => null);
  });

  // Sends only the known fields, because the API refuses a body with any other.
  ipcMain.handle(
    'review:save',
    async (_event, libraryId: string, session: ReviewSession): Promise<void> => {
      assertLibrary(libraries, libraryId);
      await guarded(() =>
        api.saveReview(libraryId, {
          slotAssignments: session?.slotAssignments ?? {},
          locks: session?.locks ?? {},
          shortlist: session?.shortlist ?? [],
        }),
      );
    },
  );

  // The handlers below belong to the legacy cloud flow. They keep their channels and shapes until package P05
  // removes them, but they contact nothing and start nothing.
  ipcMain.handle('openai:estimate', (_event, libraryId: string): OpenAiCostEstimate | null => {
    assertLibrary(libraries, libraryId);
    return null;
  });

  ipcMain.handle('openai:status', (_event, libraryId: string): OpenAiStatus => {
    assertLibrary(libraries, libraryId);
    return { hasKey: false, reachable: false, models: {}, message: 'Unable to probe OpenAI.' };
  });

  ipcMain.handle(
    'openai:start',
    (_event, libraryId: string, _options: { budgetUsd?: number; stage?: string }): void => {
      assertLibrary(libraries, libraryId);
      if (isAnalysisRunning()) {
        throw new Error('An analysis is already running.');
      }
      const key = readOpenAiKey() || process.env.OPENAI_API_KEY || '';
      if (!key) {
        applyEvent({ type: 'error', libraryId, message: 'No OpenAI API key configured. Add it in Settings.' });
        return;
      }
      applyEvent({ type: 'error', libraryId, message: CLOUD_UNAVAILABLE_MESSAGE });
    },
  );

  ipcMain.handle('export:openaiResults', (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    return Promise.reject(new Error(CLOUD_UNAVAILABLE_MESSAGE));
  });
}

async function stopServices(): Promise<void> {
  activeRun?.abort.abort();
  activeRun = null;
  statuses.clear();
  const stack = stackToStop;
  stackToStop = null;
  if (stack) await stack.stop();
}

// Stops following the run and stops the services if this session started them. Their volumes survive.
// Resolves when Docker has finished, including an `up` still in flight, so the caller can quit without leaving
// the services running. A new start is refused until then, and calls made meanwhile share the same stop.
export function disposeAnalysisIpc(): Promise<void> {
  disposal ??= stopServices().finally(() => {
    disposal = null;
  });
  return disposal;
}

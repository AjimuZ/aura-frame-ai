// Library-aware analysis IPC handlers. Every generated artifact is resolved through the
// selected local library; originals remain read-only and outside the application data tree.

import { BrowserWindow, ipcMain } from 'electron';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type {
  AnalysisConfig,
  AnalysisEvent,
  AnalysisStatus,
  OllamaStatus,
  OpenAiCostEstimate,
  OpenAiStatus,
  PhotoItem,
  ReviewSession,
} from './contracts';
import { engineConfig } from './paths';
import { readOpenAiKey } from './settings.ipc';
import type { LibraryRegistryStore } from './library-store';

type WindowsProvider = () => BrowserWindow[];

let child: ChildProcessWithoutNullStreams | null = null;
let runningLibraryId: string | null = null;
let getWindows: WindowsProvider = () => [];

const statuses = new Map<string, AnalysisStatus>();

// Shown whenever an action needs the engine: Python no longer runs on the host, and the
// containerised engine API is not wired to the desktop app until package P02.4.
const ENGINE_UNAVAILABLE_MESSAGE =
  'The analysis engine is not available yet. Python no longer runs on this computer; ' +
  'the engine will run in a container once the engine service is connected to the app.';

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

function writeControl(libraries: LibraryRegistryStore, libraryId: string, paused: boolean, cancel: boolean): void {
  const working = libraries.workingDir(libraryId);
  fs.mkdirSync(working, { recursive: true });
  fs.writeFileSync(path.join(working, 'control.json'), JSON.stringify({ paused, cancel }), 'utf-8');
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
  } else if (event.type === 'openai_progress') {
    Object.assign(status, {
      step: event.stage as AnalysisStatus['step'],
      current: event.current,
      total: event.total,
      message: event.message,
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

// Rejects for actions that change data and need the engine, so the renderer receives the
// clear ENGINE_UNAVAILABLE_MESSAGE instead of a silent no-op. No process is started.
function runEngineJson(_args: string[]): Promise<Record<string, unknown>[]> {
  return Promise.reject(new Error(ENGINE_UNAVAILABLE_MESSAGE));
}

// Read-only probes resolve to no events when the engine is unavailable, so callers fall back
// to their own "unable to probe" or empty result instead of failing.
function probeEngineJson(_args: string[]): Promise<Record<string, unknown>[]> {
  return Promise.resolve([]);
}

function validateFolder(input: unknown): string | null {
  if (typeof input !== 'string' || !input) {
    return null;
  }
  const resolved = path.resolve(input);
  if (!path.isAbsolute(resolved) || !fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) {
    return null;
  }
  return resolved;
}

function assertLibrary(libraries: LibraryRegistryStore, libraryId: string): void {
  libraries.getLibrary(libraryId);
}

export function isAnalysisRunning(): boolean {
  return child !== null && runningLibraryId !== null;
}

export function registerAnalysisIpc(
  windowsProvider: WindowsProvider,
  libraries: LibraryRegistryStore,
): void {
  getWindows = windowsProvider;

  ipcMain.handle('analysis:start', (_event, libraryId: string, config: AnalysisConfig): void => {
    assertLibrary(libraries, libraryId);
    if (isAnalysisRunning()) {
      throw new Error('An analysis is already running.');
    }
    const folder = validateFolder(config?.inputFolder);
    if (!folder) {
      applyEvent({ type: 'error', libraryId, message: 'Invalid or missing photo folder.' });
      return;
    }
    // Validated input is accepted, but no process starts: the engine only runs in containers.
    // TODO(P02.4): call the containerised engine API here, with the preset and options.
    applyEvent({ type: 'error', libraryId, message: ENGINE_UNAVAILABLE_MESSAGE });
  });

  ipcMain.handle('analysis:pause', (_event, libraryId: string): void => {
    assertLibrary(libraries, libraryId);
    if (runningLibraryId === libraryId) {
      statusFor(libraryId).paused = true;
      writeControl(libraries, libraryId, true, false);
    }
  });

  ipcMain.handle('analysis:resume', (_event, libraryId: string): void => {
    assertLibrary(libraries, libraryId);
    if (runningLibraryId === libraryId) {
      statusFor(libraryId).paused = false;
      writeControl(libraries, libraryId, false, false);
    }
  });

  ipcMain.handle('analysis:cancel', (_event, libraryId: string): void => {
    assertLibrary(libraries, libraryId);
    if (runningLibraryId !== libraryId) return;
    writeControl(libraries, libraryId, false, true);
    const status = statusFor(libraryId);
    Object.assign(status, { running: false, paused: false, step: 'idle', message: 'Analysis cancelled.' });
    if (child) {
      const proc = child;
      setTimeout(() => {
        if (!proc.killed) proc.kill();
      }, 1500);
    }
  });

  ipcMain.handle('analysis:status', (_event, libraryId: string): AnalysisStatus => {
    assertLibrary(libraries, libraryId);
    return { ...statusFor(libraryId) };
  });

  ipcMain.handle('photos:get', (_event, libraryId: string): PhotoItem[] => {
    assertLibrary(libraries, libraryId);
    try {
      const file = path.join(libraries.workingDir(libraryId), 'photos.json');
      const records = JSON.parse(fs.readFileSync(file, 'utf-8')) as Omit<PhotoItem, 'libraryId'>[];
      return records.map((photo) => ({ ...photo, libraryId }));
    } catch {
      return [];
    }
  });

  ipcMain.handle('export:finalSet', async (_event, libraryId: string, photoIds: string[]): Promise<void> => {
    assertLibrary(libraries, libraryId);
    const ids = Array.isArray(photoIds) ? photoIds.filter((id) => typeof id === 'string').join(',') : '';
    await runEngineJson([
      'export-final', '--ids', ids, '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
  });

  ipcMain.handle('ollama:check', async (): Promise<OllamaStatus> => {
    const activeId = libraries.getState().activeLibraryId;
    const events = await probeEngineJson([
      'ollama-check', '--config', engineConfig(), '--data-root', libraries.libraryRoot(activeId),
    ]);
    const report = events.find((event) => event['type'] === 'ollama_check');
    return report
      ? (report as unknown as OllamaStatus)
      : {
          runtimeReachable: false,
          primaryModel: '',
          primaryAvailable: false,
          fallbackModel: null,
          fallbackAvailable: false,
          message: 'Unable to probe Ollama.',
        };
  });

  ipcMain.handle('data:clear', async (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    if (isAnalysisRunning()) throw new Error('Cannot clear data during an analysis.');
    await runEngineJson([
      'clear-data', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
  });

  ipcMain.handle('review:get', async (_event, libraryId: string): Promise<ReviewSession | null> => {
    assertLibrary(libraries, libraryId);
    const events = await probeEngineJson([
      'load-review', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
    const result = events.find((event) => event['type'] === 'review_session');
    return result?.['session'] ? (result['session'] as ReviewSession) : null;
  });

  ipcMain.handle(
    'review:save',
    async (_event, libraryId: string, session: ReviewSession): Promise<void> => {
      assertLibrary(libraries, libraryId);
      const working = libraries.workingDir(libraryId);
      const payloadPath = path.join(working, 'review_session.json');
      fs.mkdirSync(working, { recursive: true });
      fs.writeFileSync(payloadPath, JSON.stringify(session ?? {}), 'utf-8');
      await runEngineJson([
        'save-review', '--file', payloadPath, '--config', engineConfig(),
        '--data-root', libraries.libraryRoot(libraryId),
      ]);
    },
  );

  // Estimates OpenAI Quality Mode cost from the gated set (no API calls are made).
  ipcMain.handle('openai:estimate', async (_event, libraryId: string): Promise<OpenAiCostEstimate | null> => {
    assertLibrary(libraries, libraryId);
    const events = await probeEngineJson([
      'openai-estimate', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
    const r = events.find((event) => event['type'] === 'openai_estimate');
    if (!r) return null;
    return {
      eligible: Number(r['eligible'] ?? 0),
      counts: (r['counts'] as Record<string, number>) ?? {},
      perStageUsd: (r['per_stage_usd'] as Record<string, number>) ?? {},
      estimatedTotalUsd: Number(r['estimated_total_usd'] ?? 0),
      budgetMaxUsd: Number(r['budget_max_usd'] ?? 0),
      withinBudget: Boolean(r['within_budget']),
    };
  });

  // Probes the OpenAI API (key/reachability/models). The key is injected via the environment.
  ipcMain.handle('openai:status', async (_event, libraryId: string): Promise<OpenAiStatus> => {
    assertLibrary(libraries, libraryId);
    const events = await probeEngineJson([
      'openai-check', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
    const r = events.find((event) => event['type'] === 'openai_check');
    if (!r) return { hasKey: false, reachable: false, models: {}, message: 'Unable to probe OpenAI.' };
    return {
      hasKey: Boolean(r['hasKey']),
      reachable: Boolean(r['reachable']),
      models: (r['models'] as Record<string, string | null>) ?? {},
      message: String(r['message'] ?? ''),
      warnings: (r['warnings'] as string[]) ?? undefined,
    };
  });

  // Starts the optional OpenAI Quality Mode run (streams progress like analysis:start). The
  // engine cloud gate is enabled for this run via --enable-cloud (explicit user action), and
  // the API key is injected through the environment, never as an argument.
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
      // No process starts: the engine only runs in containers (see ENGINE_UNAVAILABLE_MESSAGE).
      applyEvent({ type: 'error', libraryId, message: ENGINE_UNAVAILABLE_MESSAGE });
    },
  );

  // Re-exports OpenAI Quality Mode output folders + reports from stored results.
  ipcMain.handle('export:openaiResults', async (_event, libraryId: string): Promise<void> => {
    assertLibrary(libraries, libraryId);
    await runEngineJson([
      'export-openai-results', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId),
    ]);
  });
}

export function disposeAnalysisIpc(): void {
  if (child && !child.killed) child.kill();
  child = null;
  runningLibraryId = null;
}

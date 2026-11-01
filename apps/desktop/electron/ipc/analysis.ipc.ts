// Library-aware analysis IPC handlers. Every generated artifact is resolved through the
// selected local library; originals remain read-only and outside the application data tree.

import { BrowserWindow, ipcMain } from 'electron';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as readline from 'node:readline';
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
import { engineConfig, engineMain, pythonExecutable, repoRoot } from './paths';
import { readOpenAiKey } from './settings.ipc';
import type { LibraryRegistryStore } from './library-store';

type WindowsProvider = () => BrowserWindow[];
type EngineEvent = Omit<AnalysisEvent, 'libraryId'>;

let child: ChildProcessWithoutNullStreams | null = null;
let runningLibraryId: string | null = null;
let getWindows: WindowsProvider = () => [];

const VALID_PRESETS = new Set(['fast', 'balanced', 'quality']);
const statuses = new Map<string, AnalysisStatus>();

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

function maskLog(message: string): string {
  const home = os.homedir();
  return home ? message.split(home).join('~') : message;
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

// Builds the environment overlay carrying the OpenAI API key to the engine child process.
// The key comes from OS-encrypted storage or the ambient environment; it is never logged or
// passed as a command-line argument.
function openAiEnv(): NodeJS.ProcessEnv {
  const key = readOpenAiKey() || process.env.OPENAI_API_KEY || '';
  return key ? { OPENAI_API_KEY: key } : {};
}

function handleLine(libraryId: string, line: string): void {
  const trimmed = line.trim();
  if (!trimmed.startsWith('{')) {
    if (trimmed) {
      broadcast({ type: 'log', libraryId, level: 'info', message: maskLog(trimmed) });
    }
    return;
  }
  try {
    const parsed = JSON.parse(trimmed) as EngineEvent;
    applyEvent({ ...parsed, libraryId } as AnalysisEvent);
  } catch {
    broadcast({ type: 'log', libraryId, level: 'warning', message: maskLog(trimmed) });
  }
}

function spawnAnalysis(
  libraryId: string,
  args: string[],
  extraEnv: NodeJS.ProcessEnv = {},
): ChildProcessWithoutNullStreams {
  const proc = spawn(pythonExecutable(), [engineMain(), ...args], {
    cwd: repoRoot(),
    env: { ...process.env, PYTHONUNBUFFERED: '1', ...extraEnv },
    shell: false,
    windowsHide: true,
  });

  readline.createInterface({ input: proc.stdout }).on('line', (line) => handleLine(libraryId, line));
  readline.createInterface({ input: proc.stderr }).on('line', (line) => {
    if (line.trim()) {
      broadcast({ type: 'log', libraryId, level: 'error', message: maskLog(line.trim()) });
    }
  });

  proc.on('close', (code) => {
    if (child === proc) {
      child = null;
      runningLibraryId = null;
    }
    const status = statusFor(libraryId);
    if (status.running) {
      applyEvent(
        code === 0
          ? { type: 'done', libraryId, message: 'Analysis finished.' }
          : { type: 'error', libraryId, message: `Engine exited with code ${code}.` },
      );
    }
  });

  proc.on('error', (error) => {
    child = null;
    runningLibraryId = null;
    applyEvent({ type: 'error', libraryId, message: `Could not start the engine: ${error.message}` });
  });

  return proc;
}

function runEngineJson(
  args: string[],
  extraEnv: NodeJS.ProcessEnv = {},
): Promise<Record<string, unknown>[]> {
  return new Promise((resolve) => {
    const events: Record<string, unknown>[] = [];
    const proc = spawn(pythonExecutable(), [engineMain(), ...args], {
      cwd: repoRoot(),
      env: { ...process.env, PYTHONUNBUFFERED: '1', ...extraEnv },
      shell: false,
      windowsHide: true,
    });
    readline.createInterface({ input: proc.stdout }).on('line', (line) => {
      const trimmed = line.trim();
      if (trimmed.startsWith('{')) {
        try {
          events.push(JSON.parse(trimmed) as Record<string, unknown>);
        } catch {
          // Ignore non-JSON process noise.
        }
      }
    });
    proc.on('close', () => resolve(events));
    proc.on('error', () => resolve(events));
  });
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
    const preset = VALID_PRESETS.has(config.preset) ? config.preset : 'balanced';
    const status = statusFor(libraryId);
    writeControl(libraries, libraryId, false, false);
    Object.assign(status, {
      libraryId,
      step: 'indexing',
      running: true,
      paused: false,
      current: 0,
      total: 0,
      message: 'Starting analysis...',
      startedAt: new Date().toISOString(),
      finishedAt: undefined,
    });

    const args = [
      'analyze',
      '--input',
      folder,
      '--config',
      engineConfig(),
      '--data-root',
      libraries.libraryRoot(libraryId),
      '--preset',
      preset,
    ];
    if (!config.includeSubfolders) args.push('--no-subfolders');
    if (config.runGemma) args.push('--gemma');
    runningLibraryId = libraryId;
    child = spawnAnalysis(libraryId, args);
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
    const events = await runEngineJson([
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
    const events = await runEngineJson([
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
    const events = await runEngineJson([
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
    const events = await runEngineJson(
      ['openai-check', '--config', engineConfig(), '--data-root', libraries.libraryRoot(libraryId)],
      openAiEnv(),
    );
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
    (_event, libraryId: string, options: { budgetUsd?: number; stage?: string }): void => {
      assertLibrary(libraries, libraryId);
      if (isAnalysisRunning()) {
        throw new Error('An analysis is already running.');
      }
      const key = readOpenAiKey() || process.env.OPENAI_API_KEY || '';
      if (!key) {
        applyEvent({ type: 'error', libraryId, message: 'No OpenAI API key configured. Add it in Settings.' });
        return;
      }
      writeControl(libraries, libraryId, false, false);
      const status = statusFor(libraryId);
      Object.assign(status, {
        libraryId,
        step: 'broad_scan',
        running: true,
        paused: false,
        current: 0,
        total: 0,
        message: 'Starting OpenAI Quality Mode...',
        startedAt: new Date().toISOString(),
        finishedAt: undefined,
      });
      const args = [
        'openai-quality', '--enable-cloud', '--config', engineConfig(),
        '--data-root', libraries.libraryRoot(libraryId),
      ];
      const budget = Number(options?.budgetUsd);
      if (Number.isFinite(budget) && budget > 0) args.push('--budget', String(budget));
      if (options?.stage) args.push('--stage', String(options.stage));
      runningLibraryId = libraryId;
      child = spawnAnalysis(libraryId, args, { OPENAI_API_KEY: key });
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

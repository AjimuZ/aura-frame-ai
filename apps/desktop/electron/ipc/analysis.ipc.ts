// Profile-aware analysis IPC handlers. Every generated artifact is resolved through the
// selected local profile; originals remain read-only and outside the application data tree.

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
import type { ProfileRegistryStore } from './profile-store';

type WindowsProvider = () => BrowserWindow[];
type EngineEvent = Omit<AnalysisEvent, 'profileId'>;

let child: ChildProcessWithoutNullStreams | null = null;
let runningProfileId: string | null = null;
let getWindows: WindowsProvider = () => [];

const VALID_PRESETS = new Set(['fast', 'balanced', 'quality']);
const statuses = new Map<string, AnalysisStatus>();

function idleStatus(profileId: string): AnalysisStatus {
  return {
    profileId,
    step: 'idle',
    running: false,
    paused: false,
    current: 0,
    total: 0,
    message: 'Idle',
  };
}

function statusFor(profileId: string): AnalysisStatus {
  const current = statuses.get(profileId) ?? idleStatus(profileId);
  statuses.set(profileId, current);
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

function writeControl(profiles: ProfileRegistryStore, profileId: string, paused: boolean, cancel: boolean): void {
  const working = profiles.workingDir(profileId);
  fs.mkdirSync(working, { recursive: true });
  fs.writeFileSync(path.join(working, 'control.json'), JSON.stringify({ paused, cancel }), 'utf-8');
}

function applyEvent(event: AnalysisEvent): void {
  const status = statusFor(event.profileId);
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

function handleLine(profileId: string, line: string): void {
  const trimmed = line.trim();
  if (!trimmed.startsWith('{')) {
    if (trimmed) {
      broadcast({ type: 'log', profileId, level: 'info', message: maskLog(trimmed) });
    }
    return;
  }
  try {
    const parsed = JSON.parse(trimmed) as EngineEvent;
    applyEvent({ ...parsed, profileId } as AnalysisEvent);
  } catch {
    broadcast({ type: 'log', profileId, level: 'warning', message: maskLog(trimmed) });
  }
}

function spawnAnalysis(
  profileId: string,
  args: string[],
  extraEnv: NodeJS.ProcessEnv = {},
): ChildProcessWithoutNullStreams {
  const proc = spawn(pythonExecutable(), [engineMain(), ...args], {
    cwd: repoRoot(),
    env: { ...process.env, PYTHONUNBUFFERED: '1', ...extraEnv },
    shell: false,
    windowsHide: true,
  });

  readline.createInterface({ input: proc.stdout }).on('line', (line) => handleLine(profileId, line));
  readline.createInterface({ input: proc.stderr }).on('line', (line) => {
    if (line.trim()) {
      broadcast({ type: 'log', profileId, level: 'error', message: maskLog(line.trim()) });
    }
  });

  proc.on('close', (code) => {
    if (child === proc) {
      child = null;
      runningProfileId = null;
    }
    const status = statusFor(profileId);
    if (status.running) {
      applyEvent(
        code === 0
          ? { type: 'done', profileId, message: 'Analysis finished.' }
          : { type: 'error', profileId, message: `Engine exited with code ${code}.` },
      );
    }
  });

  proc.on('error', (error) => {
    child = null;
    runningProfileId = null;
    applyEvent({ type: 'error', profileId, message: `Could not start the engine: ${error.message}` });
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

function assertProfile(profiles: ProfileRegistryStore, profileId: string): void {
  profiles.getProfile(profileId);
}

export function isAnalysisRunning(): boolean {
  return child !== null && runningProfileId !== null;
}

export function registerAnalysisIpc(
  windowsProvider: WindowsProvider,
  profiles: ProfileRegistryStore,
): void {
  getWindows = windowsProvider;

  ipcMain.handle('analysis:start', (_event, profileId: string, config: AnalysisConfig): void => {
    assertProfile(profiles, profileId);
    if (isAnalysisRunning()) {
      throw new Error('An analysis is already running.');
    }
    const folder = validateFolder(config?.inputFolder);
    if (!folder) {
      applyEvent({ type: 'error', profileId, message: 'Invalid or missing photo folder.' });
      return;
    }
    const preset = VALID_PRESETS.has(config.preset) ? config.preset : 'balanced';
    const status = statusFor(profileId);
    writeControl(profiles, profileId, false, false);
    Object.assign(status, {
      profileId,
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
      profiles.profileRoot(profileId),
      '--preset',
      preset,
    ];
    if (!config.includeSubfolders) args.push('--no-subfolders');
    if (config.runGemma) args.push('--gemma');
    runningProfileId = profileId;
    child = spawnAnalysis(profileId, args);
  });

  ipcMain.handle('analysis:pause', (_event, profileId: string): void => {
    assertProfile(profiles, profileId);
    if (runningProfileId === profileId) {
      statusFor(profileId).paused = true;
      writeControl(profiles, profileId, true, false);
    }
  });

  ipcMain.handle('analysis:resume', (_event, profileId: string): void => {
    assertProfile(profiles, profileId);
    if (runningProfileId === profileId) {
      statusFor(profileId).paused = false;
      writeControl(profiles, profileId, false, false);
    }
  });

  ipcMain.handle('analysis:cancel', (_event, profileId: string): void => {
    assertProfile(profiles, profileId);
    if (runningProfileId !== profileId) return;
    writeControl(profiles, profileId, false, true);
    const status = statusFor(profileId);
    Object.assign(status, { running: false, paused: false, step: 'idle', message: 'Analysis cancelled.' });
    if (child) {
      const proc = child;
      setTimeout(() => {
        if (!proc.killed) proc.kill();
      }, 1500);
    }
  });

  ipcMain.handle('analysis:status', (_event, profileId: string): AnalysisStatus => {
    assertProfile(profiles, profileId);
    return { ...statusFor(profileId) };
  });

  ipcMain.handle('photos:get', (_event, profileId: string): PhotoItem[] => {
    assertProfile(profiles, profileId);
    try {
      const file = path.join(profiles.workingDir(profileId), 'photos.json');
      const records = JSON.parse(fs.readFileSync(file, 'utf-8')) as Omit<PhotoItem, 'profileId'>[];
      return records.map((photo) => ({ ...photo, profileId }));
    } catch {
      return [];
    }
  });

  ipcMain.handle('export:finalSet', async (_event, profileId: string, photoIds: string[]): Promise<void> => {
    assertProfile(profiles, profileId);
    const ids = Array.isArray(photoIds) ? photoIds.filter((id) => typeof id === 'string').join(',') : '';
    await runEngineJson([
      'export-final', '--ids', ids, '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId),
    ]);
  });

  ipcMain.handle('ollama:check', async (): Promise<OllamaStatus> => {
    const activeId = profiles.getState().activeProfileId;
    const events = await runEngineJson([
      'ollama-check', '--config', engineConfig(), '--data-root', profiles.profileRoot(activeId),
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

  ipcMain.handle('data:clear', async (_event, profileId: string): Promise<void> => {
    assertProfile(profiles, profileId);
    if (isAnalysisRunning()) throw new Error('Cannot clear data during an analysis.');
    await runEngineJson([
      'clear-data', '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId),
    ]);
  });

  ipcMain.handle('review:get', async (_event, profileId: string): Promise<ReviewSession | null> => {
    assertProfile(profiles, profileId);
    const events = await runEngineJson([
      'load-review', '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId),
    ]);
    const result = events.find((event) => event['type'] === 'review_session');
    return result?.['session'] ? (result['session'] as ReviewSession) : null;
  });

  ipcMain.handle(
    'review:save',
    async (_event, profileId: string, session: ReviewSession): Promise<void> => {
      assertProfile(profiles, profileId);
      const working = profiles.workingDir(profileId);
      const payloadPath = path.join(working, 'review_session.json');
      fs.mkdirSync(working, { recursive: true });
      fs.writeFileSync(payloadPath, JSON.stringify(session ?? {}), 'utf-8');
      await runEngineJson([
        'save-review', '--file', payloadPath, '--config', engineConfig(),
        '--data-root', profiles.profileRoot(profileId),
      ]);
    },
  );

  // Estimates OpenAI Quality Mode cost from the gated set (no API calls are made).
  ipcMain.handle('openai:estimate', async (_event, profileId: string): Promise<OpenAiCostEstimate | null> => {
    assertProfile(profiles, profileId);
    const events = await runEngineJson([
      'openai-estimate', '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId),
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
  ipcMain.handle('openai:status', async (_event, profileId: string): Promise<OpenAiStatus> => {
    assertProfile(profiles, profileId);
    const events = await runEngineJson(
      ['openai-check', '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId)],
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
    (_event, profileId: string, options: { budgetUsd?: number; stage?: string }): void => {
      assertProfile(profiles, profileId);
      if (isAnalysisRunning()) {
        throw new Error('An analysis is already running.');
      }
      const key = readOpenAiKey() || process.env.OPENAI_API_KEY || '';
      if (!key) {
        applyEvent({ type: 'error', profileId, message: 'No OpenAI API key configured. Add it in Settings.' });
        return;
      }
      writeControl(profiles, profileId, false, false);
      const status = statusFor(profileId);
      Object.assign(status, {
        profileId,
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
        '--data-root', profiles.profileRoot(profileId),
      ];
      const budget = Number(options?.budgetUsd);
      if (Number.isFinite(budget) && budget > 0) args.push('--budget', String(budget));
      if (options?.stage) args.push('--stage', String(options.stage));
      runningProfileId = profileId;
      child = spawnAnalysis(profileId, args, { OPENAI_API_KEY: key });
    },
  );

  // Re-exports OpenAI Quality Mode output folders + reports from stored results.
  ipcMain.handle('export:openaiResults', async (_event, profileId: string): Promise<void> => {
    assertProfile(profiles, profileId);
    await runEngineJson([
      'export-openai-results', '--config', engineConfig(), '--data-root', profiles.profileRoot(profileId),
    ]);
  });
}

export function disposeAnalysisIpc(): void {
  if (child && !child.killed) child.kill();
  child = null;
  runningProfileId = null;
}

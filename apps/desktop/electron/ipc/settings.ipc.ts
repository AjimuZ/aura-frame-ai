// Settings IPC handlers: read and persist user settings to local app-data only.
// Settings never leave the machine and are stored outside the repository so they are
// not committed. Defaults mirror engine/config.yaml.

import { app, ipcMain, safeStorage } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { AppSettings } from './contracts';

const VALID_PRESETS = new Set<AppSettings['preset']>(['fast', 'balanced', 'quality']);

const DEFAULT_SETTINGS: AppSettings = {
  preset: 'balanced',
  vlmCandidateCount: 70,
  blurThreshold: 120,
  minBrightness: 35,
  debugMode: false,
  modifyOriginals: false,
  storeAnalysisLocally: true,
  // OpenAI Quality Mode is OFF by default (privacy-first). The API key is never stored here.
  openai: { enabled: false, budgetUsd: 16 },
};

// Returns the path to the OS-encrypted OpenAI API key blob inside userData.
function openAiKeyPath(): string {
  return path.join(app.getPath('userData'), 'openai-key.enc');
}

// True when an encrypted API key blob is present (its value is never read back to the UI).
function hasOpenAiKey(): boolean {
  try {
    return fs.existsSync(openAiKeyPath()) && fs.statSync(openAiKeyPath()).size > 0;
  } catch {
    return false;
  }
}

// Decrypts and returns the stored OpenAI API key for injection into the engine's env, or ''.
// Exported for the analysis IPC; the renderer can never read this value.
export function readOpenAiKey(): string {
  try {
    if (!safeStorage.isEncryptionAvailable()) return '';
    return safeStorage.decryptString(fs.readFileSync(openAiKeyPath()));
  } catch {
    return '';
  }
}

// Returns the path to the local settings file inside Electron's userData directory.
function settingsPath(): string {
  return path.join(app.getPath('userData'), 'aura-frame-settings.json');
}

// Reads settings from disk, falling back to defaults for any missing keys. Keys that settings no longer
// have (written by an earlier version) are dropped, because the values pass through sanitize.
function readSettings(): AppSettings {
  try {
    const raw = fs.readFileSync(settingsPath(), 'utf-8');
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    return sanitize(parsed);
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

// Validates and coerces an incoming settings payload into a safe AppSettings object.
function sanitize(input: Partial<AppSettings>): AppSettings {
  const merged = { ...DEFAULT_SETTINGS, ...input };
  const preset = VALID_PRESETS.has(merged.preset as AppSettings['preset'])
    ? (merged.preset as AppSettings['preset'])
    : DEFAULT_SETTINGS.preset;
  return {
    preset,
    vlmCandidateCount: Math.max(1, Math.min(2000, Number(merged.vlmCandidateCount) || 70)),
    blurThreshold: Math.max(0, Number(merged.blurThreshold) || 120),
    minBrightness: Math.max(0, Math.min(255, Number(merged.minBrightness) || 35)),
    debugMode: Boolean(merged.debugMode),
    // Originals are immutable by product policy; never allow this to be enabled.
    modifyOriginals: false,
    storeAnalysisLocally: Boolean(merged.storeAnalysisLocally),
    openai: {
      enabled: Boolean(merged.openai?.enabled),
      budgetUsd: Math.max(0, Math.min(1000, Number(merged.openai?.budgetUsd) || 16)),
    },
  };
}

// Registers the settings IPC channels exactly once.
export function registerSettingsIpc(): void {
  ipcMain.handle('settings:get', (): AppSettings => readSettings());

  ipcMain.handle('settings:set', (_event, input: Partial<AppSettings>): AppSettings => {
    const safe = sanitize(input);
    fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
    fs.writeFileSync(settingsPath(), JSON.stringify(safe, null, 2), 'utf-8');
    return safe;
  });

  // --- OpenAI API key (OS-encrypted; write-only from the renderer's perspective) ---
  ipcMain.handle('openai:getKeyStatus', (): boolean => hasOpenAiKey());

  ipcMain.handle('openai:setKey', (_event, key: unknown): boolean => {
    const value = typeof key === 'string' ? key.trim() : '';
    if (!value) throw new Error('Empty API key.');
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error('Secure OS key storage is unavailable; set OPENAI_API_KEY in the environment instead.');
    }
    fs.mkdirSync(path.dirname(openAiKeyPath()), { recursive: true });
    fs.writeFileSync(openAiKeyPath(), safeStorage.encryptString(value));
    return true;
  });

  ipcMain.handle('openai:clearKey', (): boolean => {
    try {
      fs.rmSync(openAiKeyPath(), { force: true });
    } catch {
      // already absent
    }
    return true;
  });
}

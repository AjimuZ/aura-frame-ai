// Local application settings model. Mirrors the engine config defaults.

import type { AnalysisPreset } from './analysis.model';

// OpenAI Quality Mode settings. The API key is NEVER stored here; it lives in OS-encrypted
// storage and is injected into the engine as an environment variable.
export interface OpenAiSettings {
  enabled: boolean;
  budgetUsd: number;
}

export interface AppSettings {
  ollamaUrl: string;
  modelName: string;
  fallbackModelName: string;
  preset: AnalysisPreset;
  vlmCandidateCount: number;
  blurThreshold: number;
  minBrightness: number;
  debugMode: boolean;
  modifyOriginals: boolean;
  storeAnalysisLocally: boolean;
  openai: OpenAiSettings;
}

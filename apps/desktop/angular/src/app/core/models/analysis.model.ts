// Analysis pipeline models shared between the renderer and the Electron bridge.

import type { PhotoCategory } from './photo.model';

export type AnalysisPreset = 'fast' | 'balanced' | 'quality';

export interface AnalysisConfig {
  inputFolder: string;
  includeSubfolders: boolean;
  ignoreDuplicates: boolean;
  preserveOriginals: boolean;
  generateThumbnails: boolean;
  runGemma: boolean;
  preset: AnalysisPreset;
}

export type AnalysisStep =
  | 'idle'
  | 'indexing'
  | 'duplicates'
  | 'quality'
  | 'faces'
  | 'local_gate'
  | 'embeddings'
  | 'aesthetic'
  | 'preranking'
  | 'gemma'
  | 'final_set'
  | 'broad_scan'
  | 'quality_review'
  | 'premium_review'
  | 'final_judge'
  | 'consensus'
  | 'export'
  | 'done'
  | 'error';

// Result of probing the local Ollama runtime and the configured models.
export interface OllamaStatus {
  runtimeReachable: boolean;
  primaryModel: string;
  primaryAvailable: boolean;
  fallbackModel: string | null;
  fallbackAvailable: boolean;
  message: string;
}

// Result of probing the OpenAI API (key presence, reachability, per-stage resolved model).
export interface OpenAiStatus {
  hasKey: boolean;
  reachable: boolean;
  models: Record<string, string | null>;
  message: string;
  warnings?: string[];
}

// Pre-run OpenAI Quality Mode cost estimate (no API calls were made to produce it).
export interface OpenAiCostEstimate {
  eligible: number;
  counts: Record<string, number>;
  perStageUsd: Record<string, number>;
  estimatedTotalUsd: number;
  budgetMaxUsd: number;
  withinBudget: boolean;
}

// A persisted manual review session (slot assignments, per-slot locks, shortlist).
export interface ReviewSession {
  slotAssignments: Record<string, string>;
  locks: Record<string, boolean>;
  shortlist: string[];
}

export interface AnalysisStatus {
  profileId: string | null;
  step: AnalysisStep;
  running: boolean;
  paused: boolean;
  current: number;
  total: number;
  message: string;
  startedAt?: string;
  finishedAt?: string;
}

export type AnalysisEvent =
  | {
      type: 'progress';
      profileId: string;
      step: AnalysisStep | string;
      current: number;
      total: number;
      message: string;
    }
  | {
      type: 'photo_scored';
      profileId: string;
      photoId: string;
      finalScore: number;
      category: PhotoCategory | string;
    }
  | { type: 'log'; profileId: string; level: 'info' | 'warning' | 'error'; message: string }
  | { type: 'done'; profileId: string; message: string }
  | { type: 'error'; profileId: string; message: string }
  | {
      type: 'local_gate_progress';
      profileId: string;
      current: number;
      total: number;
      hard_rejected: number;
      openai_candidates: number;
      message: string;
    }
  | {
      type: 'openai_progress';
      profileId: string;
      stage: string;
      current: number;
      total: number;
      estimated_cost_usd: number;
      budget_remaining_usd: number;
      message: string;
    }
  | {
      type: 'openai_photo_scored';
      profileId: string;
      photo_id: string;
      stage: string;
      score: number;
      best_slot: string;
    }
  | { type: 'budget_warning'; profileId: string; spent_estimate_usd: number; message: string }
  | { type: 'budget_stopped'; profileId: string; spent_estimate_usd: number; message: string };

// Ordered steps shown in the Analysis screen stepper, with display labels.
export const ANALYSIS_STEPS: { step: AnalysisStep; label: string }[] = [
  { step: 'indexing', label: 'Indexing photos' },
  { step: 'duplicates', label: 'Detecting duplicates' },
  { step: 'quality', label: 'Checking image quality' },
  { step: 'faces', label: 'Analyzing face quality' },
  { step: 'local_gate', label: 'Applying the Local Minimal Gate' },
  { step: 'embeddings', label: 'Grouping similar photos' },
  { step: 'aesthetic', label: 'Scoring aesthetics' },
  { step: 'preranking', label: 'Pre-ranking candidates' },
  { step: 'gemma', label: 'Reviewing top candidates with the local model' },
  { step: 'final_set', label: 'Building final profile set' },
];

// Ordered steps shown for the optional OpenAI Quality Mode run.
export const OPENAI_STEPS: { step: AnalysisStep; label: string }[] = [
  { step: 'broad_scan', label: 'OpenAI broad scan' },
  { step: 'quality_review', label: 'OpenAI quality review' },
  { step: 'premium_review', label: 'OpenAI premium review' },
  { step: 'final_judge', label: 'OpenAI final judge (comparative)' },
  { step: 'consensus', label: 'Consensus ranking' },
  { step: 'export', label: 'Exporting results' },
];

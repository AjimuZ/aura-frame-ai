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

// One provider as the engine API reports it. The text is fixed by the API and holds no path or address.
export interface ProviderStatus {
  id: string;
  label: string;
  cloud: boolean;
  state: 'available' | 'not_configured' | 'disabled';
  detail: string;
}

// Result of the one provider check. `reachable` is false, with an empty list, when the API could not be asked.
export interface ProvidersStatus {
  reachable: boolean;
  providers: ProviderStatus[];
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
  libraryId: string | null;
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
      libraryId: string;
      step: AnalysisStep | string;
      current: number;
      total: number;
      message: string;
    }
  | {
      type: 'photo_scored';
      libraryId: string;
      photoId: string;
      finalScore: number;
      category: PhotoCategory | string;
    }
  | { type: 'log'; libraryId: string; level: 'info' | 'warning' | 'error'; message: string }
  | { type: 'done'; libraryId: string; message: string }
  | { type: 'error'; libraryId: string; message: string }
  | {
      type: 'local_gate_progress';
      libraryId: string;
      current: number;
      total: number;
      hard_rejected: number;
      openai_candidates: number;
      message: string;
    }
  | {
      type: 'openai_progress';
      libraryId: string;
      stage: string;
      current: number;
      total: number;
      estimated_cost_usd: number;
      budget_remaining_usd: number;
      message: string;
    }
  | {
      type: 'openai_photo_scored';
      libraryId: string;
      photo_id: string;
      stage: string;
      score: number;
      best_slot: string;
    }
  | { type: 'budget_warning'; libraryId: string; spent_estimate_usd: number; message: string }
  | { type: 'budget_stopped'; libraryId: string; spent_estimate_usd: number; message: string };

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
  { step: 'final_set', label: 'Building final set' },
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

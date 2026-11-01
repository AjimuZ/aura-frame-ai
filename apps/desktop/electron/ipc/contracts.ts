// Shared IPC contract types used by the Electron main process and the preload bridge.
// These mirror the Angular core models so the typed boundary stays consistent.

export type PhotoCategory =
  | 'HEADSHOT'
  | 'FULL_BODY'
  | 'ACTIVITY'
  | 'SOCIAL'
  | 'CANDID'
  | 'ELEGANT'
  | 'BAD'
  | 'REJECT';

export type ProfileSlot =
  | 'main_photo'
  | 'second_photo'
  | 'full_body'
  | 'activity_photo'
  | 'social_photo'
  | 'elegant_photo'
  | 'backup'
  | 'reject';

export type PhotoStatus = 'pending' | 'accepted' | 'rejected' | 'selected';

// Local Minimal Gate classification (parallel to PhotoStatus; only hard_reject/failed_read
// are excluded from the optional OpenAI review).
export type GateStatus =
  | 'openai_candidate'
  | 'low_priority_openai_candidate'
  | 'needs_manual_review'
  | 'duplicate_candidate'
  | 'hard_reject'
  | 'failed_read';

export type AnalysisPreset = 'fast' | 'balanced' | 'quality';

export interface GemmaPhotoReview {
  datingScore: number;
  technicalQuality: number;
  faceClarity: number;
  expressionScore: number;
  naturalnessScore: number;
  outfitScore: number;
  contextScore: number;
  category: PhotoCategory;
  bestSlot: ProfileSlot;
  strengths: string[];
  weaknesses: string[];
  reason: string;
}

export interface PhotoItem {
  profileId: string;
  id: string;
  filePath: string;
  thumbnailPath: string;
  fileName: string;
  width: number;
  height: number;
  createdAt?: string;
  modifiedAt?: string;
  technicalScore: number;
  blurScore: number;
  brightnessScore: number;
  contrastScore: number;
  likelyScreenshot: boolean;
  faceDetected: boolean;
  faceClarityScore: number;
  faceSizeScore: number;
  faceCenterScore: number;
  eyesVisibleScore: number;
  headYaw: number;
  headPitch: number;
  occlusionScore: number;
  groupPhoto: boolean;
  faceQualityScore: number;
  aestheticScore: number;
  clusterId: number;
  numberOfFaces: number;
  gemmaScore?: GemmaPhotoReview;
  finalScore: number;
  category: PhotoCategory;
  bestSlot: ProfileSlot;
  status: PhotoStatus;
  rejectReason?: string;
  reasonTags: string[];

  // Local Minimal Gate + OpenAI Quality Mode (optional cloud flow). All optional so the
  // local-only pipeline and the browser mock remain valid without them.
  softFlags?: string[];
  gateStatus?: GateStatus | '';
  hardRejectReason?: string;
  eligibleForOpenai?: boolean;
  openaiPriority?: number;
  isNearDuplicate?: boolean;
  openaiBroadScore?: number;
  openaiQualityScore?: number;
  openaiPremiumScore?: number;
  finalJudgeScore?: number;
  consensusScore?: number;
  openaiReason?: string;
  openaiRisk?: string;
  needsManualReview?: boolean;
  failedOpenaiReview?: boolean;
  inTop6?: boolean;
  inTop20?: boolean;
  inTop50?: boolean;
  inTop100?: boolean;
  finalSelectedSlot?: ProfileSlot | '';
  estimatedCostUsd?: number;
}

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

// OpenAI Quality Mode settings stored in app settings (the API key is NOT stored here; it is
// held in OS-encrypted storage and injected as an environment variable into the engine).
export interface OpenAiSettings {
  enabled: boolean;
  budgetUsd: number;
}

// Result of probing the OpenAI API (key presence, reachability, per-stage resolved model).
export interface OpenAiStatus {
  hasKey: boolean;
  reachable: boolean;
  models: Record<string, string | null>;
  message: string;
  warnings?: string[];
}

// Pre-run cost estimate for OpenAI Quality Mode (no API calls were made to produce it).
export interface OpenAiCostEstimate {
  eligible: number;
  counts: Record<string, number>;
  perStageUsd: Record<string, number>;
  estimatedTotalUsd: number;
  budgetMaxUsd: number;
  withinBudget: boolean;
}

// Result of probing the local Ollama runtime and the configured primary/fallback models.
export interface OllamaStatus {
  runtimeReachable: boolean;
  primaryModel: string;
  primaryAvailable: boolean;
  fallbackModel: string | null;
  fallbackAvailable: boolean;
  message: string;
}

// A persisted manual review session (slot assignments, per-slot locks, shortlist).
export interface ReviewSession {
  slotAssignments: Record<string, string>;
  locks: Record<string, boolean>;
  shortlist: string[];
}

export interface LocalProfile {
  id: string;
  name: string;
  inputFolder: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProfileState {
  profiles: LocalProfile[];
  activeProfileId: string;
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

// The typed bridge surface exposed to the Angular renderer via contextBridge.
export interface AuraFrameApi {
  getProfileState(): Promise<ProfileState>;
  createProfile(name: string): Promise<ProfileState>;
  renameProfile(profileId: string, name: string): Promise<ProfileState>;
  activateProfile(profileId: string): Promise<ProfileState>;
  deleteProfile(profileId: string): Promise<ProfileState>;
  selectPhotoFolder(profileId: string): Promise<string | null>;
  startAnalysis(profileId: string, config: AnalysisConfig): Promise<void>;
  pauseAnalysis(profileId: string): Promise<void>;
  resumeAnalysis(profileId: string): Promise<void>;
  cancelAnalysis(profileId: string): Promise<void>;
  getAnalysisStatus(profileId: string): Promise<AnalysisStatus>;
  getPhotos(profileId: string): Promise<PhotoItem[]>;
  exportPhoto(profileId: string, photoId: string): Promise<boolean>;
  exportFinalSet(profileId: string, photoIds: string[]): Promise<void>;
  openOutputFolder(profileId: string): Promise<void>;
  openReportsFolder(profileId: string): Promise<void>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;
  checkOllama(): Promise<OllamaStatus>;
  clearGeneratedData(profileId: string): Promise<void>;
  getReviewSession(profileId: string): Promise<ReviewSession | null>;
  saveReviewSession(profileId: string, session: ReviewSession): Promise<void>;
  // OpenAI Quality Mode (optional cloud flow; all guarded + off by default).
  getOpenAiKeyStatus(): Promise<boolean>;
  setOpenAiKey(key: string): Promise<boolean>;
  clearOpenAiKey(): Promise<boolean>;
  getOpenAiStatus(profileId: string): Promise<OpenAiStatus>;
  estimateOpenAiCost(profileId: string): Promise<OpenAiCostEstimate | null>;
  startOpenAiQuality(
    profileId: string,
    options: { budgetUsd?: number; stage?: string },
  ): Promise<void>;
  exportOpenAiResults(profileId: string): Promise<void>;
  onProgress(callback: (event: AnalysisEvent) => void): () => void;
}

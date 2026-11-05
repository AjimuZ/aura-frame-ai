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

export type PhotoSlot =
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
  overallScore: number;
  technicalQuality: number;
  faceClarity: number;
  expressionScore: number;
  naturalnessScore: number;
  styleScore: number;
  contextScore: number;
  category: PhotoCategory;
  bestSlot: PhotoSlot;
  strengths: string[];
  weaknesses: string[];
  reason: string;
}

export interface PhotoItem {
  libraryId: string;
  id: string;
  // Whether a thumbnail was generated. The renderer asks for it by ID and never holds a path.
  hasThumbnail: boolean;
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
  bestSlot: PhotoSlot;
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
  finalSelectedSlot?: PhotoSlot | '';
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

// One provider as `GET /api/providers` reports it. The text is fixed by the API and holds no path or address.
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

// A persisted manual review session (slot assignments, per-slot locks, shortlist).
export interface ReviewSession {
  slotAssignments: Record<string, string>;
  locks: Record<string, boolean>;
  shortlist: string[];
}

export interface LocalLibrary {
  id: string;
  name: string;
  inputFolder: string | null;
  // Whether this library's anonymous copies may go to the optional cloud provider. Off until the person turns it on,
  // and changed only through `setCodexOptIn`, so a renderer cannot slip it into another request.
  codexOptIn: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface LibraryState {
  libraries: LocalLibrary[];
  activeLibraryId: string;
}

export interface AppSettings {
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
  getLibraryState(): Promise<LibraryState>;
  createLibrary(name: string): Promise<LibraryState>;
  renameLibrary(libraryId: string, name: string): Promise<LibraryState>;
  activateLibrary(libraryId: string): Promise<LibraryState>;
  deleteLibrary(libraryId: string): Promise<LibraryState>;
  selectPhotoFolder(libraryId: string): Promise<string | null>;
  // The per-library opt-in for the optional cloud provider. Both reject for an unknown library; the setter also
  // rejects a value that is not a boolean, and resolves to the value now stored.
  getCodexOptIn(libraryId: string): Promise<boolean>;
  setCodexOptIn(libraryId: string, enabled: boolean): Promise<boolean>;
  startAnalysis(libraryId: string, config: AnalysisConfig): Promise<void>;
  pauseAnalysis(libraryId: string): Promise<void>;
  resumeAnalysis(libraryId: string): Promise<void>;
  cancelAnalysis(libraryId: string): Promise<void>;
  getAnalysisStatus(libraryId: string): Promise<AnalysisStatus>;
  getPhotos(libraryId: string): Promise<PhotoItem[]>;
  exportPhoto(libraryId: string, photoId: string): Promise<boolean>;
  exportFinalSet(libraryId: string, photoIds: string[]): Promise<void>;
  openOutputFolder(libraryId: string): Promise<void>;
  openReportsFolder(libraryId: string): Promise<void>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;
  checkProviders(): Promise<ProvidersStatus>;
  clearGeneratedData(libraryId: string): Promise<void>;
  getReviewSession(libraryId: string): Promise<ReviewSession | null>;
  saveReviewSession(libraryId: string, session: ReviewSession): Promise<void>;
  // OpenAI Quality Mode (optional cloud flow; all guarded + off by default).
  getOpenAiKeyStatus(): Promise<boolean>;
  setOpenAiKey(key: string): Promise<boolean>;
  clearOpenAiKey(): Promise<boolean>;
  getOpenAiStatus(libraryId: string): Promise<OpenAiStatus>;
  estimateOpenAiCost(libraryId: string): Promise<OpenAiCostEstimate | null>;
  startOpenAiQuality(
    libraryId: string,
    options: { budgetUsd?: number; stage?: string },
  ): Promise<void>;
  exportOpenAiResults(libraryId: string): Promise<void>;
  onProgress(callback: (event: AnalysisEvent) => void): () => void;
}

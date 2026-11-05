// Type definition for the secure bridge exposed by the Electron preload script.

import type {
  AnalysisConfig,
  AnalysisEvent,
  AnalysisStatus,
  OpenAiCostEstimate,
  OpenAiStatus,
  ProvidersStatus,
  ReviewSession,
} from './analysis.model';
import type { PhotoItem } from './photo.model';
import type { LibraryState } from './library.model';
import type { AppSettings } from './settings.model';

export interface AuraFrameApi {
  getLibraryState(): Promise<LibraryState>;
  createLibrary(name: string): Promise<LibraryState>;
  renameLibrary(libraryId: string, name: string): Promise<LibraryState>;
  activateLibrary(libraryId: string): Promise<LibraryState>;
  deleteLibrary(libraryId: string): Promise<LibraryState>;
  selectPhotoFolder(libraryId: string): Promise<string | null>;
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

declare global {
  interface Window {
    auraFrame?: AuraFrameApi;
  }
}

// Type definition for the secure bridge exposed by the Electron preload script.

import type {
  AnalysisConfig,
  AnalysisEvent,
  AnalysisStatus,
  OllamaStatus,
  OpenAiCostEstimate,
  OpenAiStatus,
  ReviewSession,
} from './analysis.model';
import type { PhotoItem } from './photo.model';
import type { ProfileState } from './profile.model';
import type { AppSettings } from './settings.model';

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

declare global {
  interface Window {
    auraFrame?: AuraFrameApi;
  }
}

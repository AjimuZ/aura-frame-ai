// AuraFrame AI - secure preload bridge.
// Exposes a single, narrow, typed `window.auraFrame` API to the renderer.
// No Node primitives (fs, child_process, ipcRenderer) are leaked to the page.

import { contextBridge, ipcRenderer } from 'electron';
import type {
  AnalysisConfig,
  AnalysisEvent,
  AnalysisStatus,
  AppSettings,
  AuraFrameApi,
  OllamaStatus,
  OpenAiCostEstimate,
  OpenAiStatus,
  PhotoItem,
  ReviewSession,
  ProfileState,
} from './ipc/contracts';

const CHANNELS = {
  selectFolder: 'fs:selectPhotoFolder',
  openOutput: 'fs:openOutputFolder',
  openReports: 'fs:openReportsFolder',
  getProfiles: 'profiles:get',
  createProfile: 'profiles:create',
  renameProfile: 'profiles:rename',
  activateProfile: 'profiles:activate',
  deleteProfile: 'profiles:delete',
  start: 'analysis:start',
  pause: 'analysis:pause',
  resume: 'analysis:resume',
  cancel: 'analysis:cancel',
  status: 'analysis:status',
  getPhotos: 'photos:get',
  exportPhoto: 'export:photo',
  exportSet: 'export:finalSet',
  getSettings: 'settings:get',
  setSettings: 'settings:set',
  checkOllama: 'ollama:check',
  clearData: 'data:clear',
  getReview: 'review:get',
  saveReview: 'review:save',
  openaiKeyStatus: 'openai:getKeyStatus',
  openaiSetKey: 'openai:setKey',
  openaiClearKey: 'openai:clearKey',
  openaiStatus: 'openai:status',
  openaiEstimate: 'openai:estimate',
  openaiStart: 'openai:start',
  exportOpenai: 'export:openaiResults',
  event: 'analysis:event',
} as const;

const api: AuraFrameApi = {
  getProfileState: () => ipcRenderer.invoke(CHANNELS.getProfiles) as Promise<ProfileState>,
  createProfile: (name: string) =>
    ipcRenderer.invoke(CHANNELS.createProfile, name) as Promise<ProfileState>,
  renameProfile: (profileId: string, name: string) =>
    ipcRenderer.invoke(CHANNELS.renameProfile, profileId, name) as Promise<ProfileState>,
  activateProfile: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.activateProfile, profileId) as Promise<ProfileState>,
  deleteProfile: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.deleteProfile, profileId) as Promise<ProfileState>,
  selectPhotoFolder: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.selectFolder, profileId) as Promise<string | null>,
  startAnalysis: (profileId: string, config: AnalysisConfig) =>
    ipcRenderer.invoke(CHANNELS.start, profileId, config) as Promise<void>,
  pauseAnalysis: (profileId: string) => ipcRenderer.invoke(CHANNELS.pause, profileId) as Promise<void>,
  resumeAnalysis: (profileId: string) => ipcRenderer.invoke(CHANNELS.resume, profileId) as Promise<void>,
  cancelAnalysis: (profileId: string) => ipcRenderer.invoke(CHANNELS.cancel, profileId) as Promise<void>,
  getAnalysisStatus: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.status, profileId) as Promise<AnalysisStatus>,
  getPhotos: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.getPhotos, profileId) as Promise<PhotoItem[]>,
  exportPhoto: (profileId: string, photoId: string) =>
    ipcRenderer.invoke(CHANNELS.exportPhoto, profileId, photoId) as Promise<boolean>,
  exportFinalSet: (profileId: string, photoIds: string[]) =>
    ipcRenderer.invoke(CHANNELS.exportSet, profileId, photoIds) as Promise<void>,
  openOutputFolder: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.openOutput, profileId) as Promise<void>,
  openReportsFolder: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.openReports, profileId) as Promise<void>,
  getSettings: () => ipcRenderer.invoke(CHANNELS.getSettings) as Promise<AppSettings>,
  setSettings: (settings: AppSettings) =>
    ipcRenderer.invoke(CHANNELS.setSettings, settings) as Promise<AppSettings>,
  checkOllama: () => ipcRenderer.invoke(CHANNELS.checkOllama) as Promise<OllamaStatus>,
  clearGeneratedData: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.clearData, profileId) as Promise<void>,
  getReviewSession: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.getReview, profileId) as Promise<ReviewSession | null>,
  saveReviewSession: (profileId: string, session: ReviewSession) =>
    ipcRenderer.invoke(CHANNELS.saveReview, profileId, session) as Promise<void>,
  getOpenAiKeyStatus: () => ipcRenderer.invoke(CHANNELS.openaiKeyStatus) as Promise<boolean>,
  setOpenAiKey: (key: string) => ipcRenderer.invoke(CHANNELS.openaiSetKey, key) as Promise<boolean>,
  clearOpenAiKey: () => ipcRenderer.invoke(CHANNELS.openaiClearKey) as Promise<boolean>,
  getOpenAiStatus: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.openaiStatus, profileId) as Promise<OpenAiStatus>,
  estimateOpenAiCost: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.openaiEstimate, profileId) as Promise<OpenAiCostEstimate | null>,
  startOpenAiQuality: (profileId: string, options: { budgetUsd?: number; stage?: string }) =>
    ipcRenderer.invoke(CHANNELS.openaiStart, profileId, options) as Promise<void>,
  exportOpenAiResults: (profileId: string) =>
    ipcRenderer.invoke(CHANNELS.exportOpenai, profileId) as Promise<void>,
  onProgress: (callback: (event: AnalysisEvent) => void) => {
    const listener = (_event: unknown, payload: AnalysisEvent): void => callback(payload);
    ipcRenderer.on(CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(CHANNELS.event, listener);
  },
};

contextBridge.exposeInMainWorld('auraFrame', api);

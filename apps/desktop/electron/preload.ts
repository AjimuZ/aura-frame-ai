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
  OpenAiCostEstimate,
  OpenAiStatus,
  PhotoItem,
  ProvidersStatus,
  ReviewSession,
  LibraryState,
} from './ipc/contracts';

const CHANNELS = {
  selectFolder: 'fs:selectPhotoFolder',
  openOutput: 'fs:openOutputFolder',
  openReports: 'fs:openReportsFolder',
  getLibraries: 'libraries:get',
  createLibrary: 'libraries:create',
  renameLibrary: 'libraries:rename',
  activateLibrary: 'libraries:activate',
  deleteLibrary: 'libraries:delete',
  getCodexOptIn: 'libraries:getCodexOptIn',
  setCodexOptIn: 'libraries:setCodexOptIn',
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
  checkProviders: 'providers:check',
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
  getLibraryState: () => ipcRenderer.invoke(CHANNELS.getLibraries) as Promise<LibraryState>,
  createLibrary: (name: string) =>
    ipcRenderer.invoke(CHANNELS.createLibrary, name) as Promise<LibraryState>,
  renameLibrary: (libraryId: string, name: string) =>
    ipcRenderer.invoke(CHANNELS.renameLibrary, libraryId, name) as Promise<LibraryState>,
  activateLibrary: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.activateLibrary, libraryId) as Promise<LibraryState>,
  deleteLibrary: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.deleteLibrary, libraryId) as Promise<LibraryState>,
  selectPhotoFolder: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.selectFolder, libraryId) as Promise<string | null>,
  getCodexOptIn: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.getCodexOptIn, libraryId) as Promise<boolean>,
  setCodexOptIn: (libraryId: string, enabled: boolean) =>
    ipcRenderer.invoke(CHANNELS.setCodexOptIn, libraryId, enabled) as Promise<boolean>,
  startAnalysis: (libraryId: string, config: AnalysisConfig) =>
    ipcRenderer.invoke(CHANNELS.start, libraryId, config) as Promise<void>,
  pauseAnalysis: (libraryId: string) => ipcRenderer.invoke(CHANNELS.pause, libraryId) as Promise<void>,
  resumeAnalysis: (libraryId: string) => ipcRenderer.invoke(CHANNELS.resume, libraryId) as Promise<void>,
  cancelAnalysis: (libraryId: string) => ipcRenderer.invoke(CHANNELS.cancel, libraryId) as Promise<void>,
  getAnalysisStatus: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.status, libraryId) as Promise<AnalysisStatus>,
  getPhotos: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.getPhotos, libraryId) as Promise<PhotoItem[]>,
  exportPhoto: (libraryId: string, photoId: string) =>
    ipcRenderer.invoke(CHANNELS.exportPhoto, libraryId, photoId) as Promise<boolean>,
  exportFinalSet: (libraryId: string, photoIds: string[]) =>
    ipcRenderer.invoke(CHANNELS.exportSet, libraryId, photoIds) as Promise<void>,
  openOutputFolder: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.openOutput, libraryId) as Promise<void>,
  openReportsFolder: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.openReports, libraryId) as Promise<void>,
  getSettings: () => ipcRenderer.invoke(CHANNELS.getSettings) as Promise<AppSettings>,
  setSettings: (settings: AppSettings) =>
    ipcRenderer.invoke(CHANNELS.setSettings, settings) as Promise<AppSettings>,
  checkProviders: () => ipcRenderer.invoke(CHANNELS.checkProviders) as Promise<ProvidersStatus>,
  clearGeneratedData: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.clearData, libraryId) as Promise<void>,
  getReviewSession: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.getReview, libraryId) as Promise<ReviewSession | null>,
  saveReviewSession: (libraryId: string, session: ReviewSession) =>
    ipcRenderer.invoke(CHANNELS.saveReview, libraryId, session) as Promise<void>,
  getOpenAiKeyStatus: () => ipcRenderer.invoke(CHANNELS.openaiKeyStatus) as Promise<boolean>,
  setOpenAiKey: (key: string) => ipcRenderer.invoke(CHANNELS.openaiSetKey, key) as Promise<boolean>,
  clearOpenAiKey: () => ipcRenderer.invoke(CHANNELS.openaiClearKey) as Promise<boolean>,
  getOpenAiStatus: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.openaiStatus, libraryId) as Promise<OpenAiStatus>,
  estimateOpenAiCost: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.openaiEstimate, libraryId) as Promise<OpenAiCostEstimate | null>,
  startOpenAiQuality: (libraryId: string, options: { budgetUsd?: number; stage?: string }) =>
    ipcRenderer.invoke(CHANNELS.openaiStart, libraryId, options) as Promise<void>,
  exportOpenAiResults: (libraryId: string) =>
    ipcRenderer.invoke(CHANNELS.exportOpenai, libraryId) as Promise<void>,
  onProgress: (callback: (event: AnalysisEvent) => void) => {
    const listener = (_event: unknown, payload: AnalysisEvent): void => callback(payload);
    ipcRenderer.on(CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(CHANNELS.event, listener);
  },
};

contextBridge.exposeInMainWorld('auraFrame', api);

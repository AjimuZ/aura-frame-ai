// Central signal store for photos and analysis state.
// Wraps the AuraFrame bridge and exposes reactive signals to all feature screens.

import { computed, inject, Injectable, signal } from '@angular/core';
import { AURA_FRAME_API } from '../tokens/aura-frame-api.token';
import { LibraryService } from './library.service';
import {
  type AnalysisConfig,
  type AnalysisStatus,
  type PhotoItem,
  type PhotoSlot,
  type ReviewSession,
  FINAL_SET_SLOTS,
} from '../models';

export interface LogLine {
  level: 'info' | 'warning' | 'error';
  message: string;
}

const IDLE_STATUS: AnalysisStatus = {
  libraryId: null,
  step: 'idle',
  running: false,
  paused: false,
  current: 0,
  total: 0,
  message: 'Idle',
};

@Injectable({ providedIn: 'root' })
export class PhotoStore {
  private readonly api = inject(AURA_FRAME_API);
  readonly libraryService = inject(LibraryService);

  readonly photos = signal<PhotoItem[]>([]);
  readonly status = signal<AnalysisStatus>({ ...IDLE_STATUS });
  readonly logs = signal<LogLine[]>([]);
  readonly selectedFolder = signal<string | null>(null);

  // Manual curation state for the Review and Library Builder screens.
  readonly shortlist = signal<string[]>([]);
  readonly compareSelection = signal<string[]>([]);
  // Per-slot lock: maps a photo slot to the photo id pinned into it.
  readonly lockedSlots = signal<Record<string, string>>({});

  // Aggregate dashboard statistics derived from the current photo set.
  readonly stats = computed(() => {
    const photos = this.photos();
    const valid = photos.filter((p) => p.status !== 'rejected');
    const rejected = photos.filter((p) => p.status === 'rejected');
    const gemmaAnalyzed = photos.filter((p) => !!p.gemmaScore);
    const bestScore = photos.reduce((max, p) => Math.max(max, p.finalScore), 0);
    const selected = photos.filter((p) => p.status === 'selected');
    return {
      imported: photos.length,
      valid: valid.length,
      rejected: rejected.length,
      gemmaAnalyzed: gemmaAnalyzed.length,
      bestScore: Math.round(bestScore * 10) / 10,
      selected: selected.length,
    };
  });

  // The balanced final set of six photos keyed by photo slot. Locked slots keep their
  // pinned photo; the rest are filled with the best remaining candidates.
  readonly finalSet = computed(() => {
    const photos = [...this.photos()].sort((a, b) => b.finalScore - a.finalScore);
    const byId = new Map(photos.map((p) => [p.id, p] as const));
    const locks = this.lockedSlots();
    const used = new Set<string>(Object.values(locks));
    const set: { slot: PhotoSlot; photo: PhotoItem | null; locked: boolean }[] = [];
    for (const slot of FINAL_SET_SLOTS) {
      const lockedId = locks[slot];
      if (lockedId && byId.has(lockedId)) {
        set.push({ slot, photo: byId.get(lockedId) ?? null, locked: true });
        continue;
      }
      const match = photos.find((p) => p.bestSlot === slot && !used.has(p.id));
      if (match) {
        used.add(match.id);
      }
      set.push({ slot, photo: match ?? null, locked: false });
    }
    return set;
  });

  constructor() {
    // Stream live analysis events into the status, logs, and scored photos.
    this.api.onProgress((event) => {
      if (event.libraryId !== this.libraryService.activeLibraryId()) {
        return;
      }
      if (event.type === 'progress') {
        this.status.update((s) => ({
          ...s,
          step: event.step as AnalysisStatus['step'],
          current: event.current,
          total: event.total,
          message: event.message,
          running: true,
        }));
      } else if (event.type === 'photo_scored') {
        this.appendLog('info', `Scored ${event.photoId}: ${event.finalScore} (${event.category})`);
      } else if (event.type === 'log') {
        this.appendLog(event.level, event.message);
      } else if (event.type === 'done') {
        this.status.update((s) => ({ ...s, running: false, step: 'done', message: event.message }));
        void this.refreshPhotos();
      } else if (event.type === 'error') {
        this.status.update((s) => ({ ...s, running: false, step: 'error', message: event.message }));
        this.appendLog('error', event.message);
      } else if (event.type === 'local_gate_progress') {
        this.status.update((s) => ({
          ...s,
          running: true,
          step: 'local_gate',
          current: event.current,
          total: event.total,
          message: event.message,
        }));
      } else if (event.type === 'openai_progress') {
        this.status.update((s) => ({
          ...s,
          running: true,
          step: event.stage as AnalysisStatus['step'],
          current: event.current,
          total: event.total,
          message: `${event.message} — est. $${event.estimated_cost_usd.toFixed(2)}, budget left $${event.budget_remaining_usd.toFixed(2)}`,
        }));
      } else if (event.type === 'openai_photo_scored') {
        this.appendLog('info', `OpenAI ${event.stage}: ${event.photo_id} → ${event.score} (${event.best_slot})`);
      } else if (event.type === 'budget_warning' || event.type === 'budget_stopped') {
        this.appendLog('warning', `${event.message} (~$${event.spent_estimate_usd.toFixed(2)})`);
      }
    });
  }

  async initialize(): Promise<void> {
    await this.libraryService.load();
    await this.loadActiveLibrary();
  }

  private activeLibraryId(): string {
    const libraryId = this.libraryService.activeLibraryId();
    if (!libraryId) {
      throw new Error('No local library is active.');
    }
    return libraryId;
  }

  private resetLibraryState(): void {
    const library = this.libraryService.activeLibrary();
    this.photos.set([]);
    this.status.set({ ...IDLE_STATUS, libraryId: library?.id ?? null });
    this.logs.set([]);
    this.selectedFolder.set(library?.inputFolder ?? null);
    this.shortlist.set([]);
    this.compareSelection.set([]);
    this.lockedSlots.set({});
  }

  private async loadActiveLibrary(): Promise<void> {
    const libraryId = this.activeLibraryId();
    this.resetLibraryState();
    const [photos, status, session] = await Promise.all([
      this.api.getPhotos(libraryId),
      this.api.getAnalysisStatus(libraryId),
      this.api.getReviewSession(libraryId),
    ]);
    if (libraryId !== this.libraryService.activeLibraryId()) {
      return;
    }
    this.photos.set(photos);
    this.status.set(status);
    if (session) {
      this.shortlist.set(session.shortlist);
      const locked: Record<string, string> = {};
      for (const [slot, isLocked] of Object.entries(session.locks)) {
        const photoId = session.slotAssignments[slot];
        if (isLocked && photoId) {
          locked[slot] = photoId;
        }
      }
      this.lockedSlots.set(locked);
    }
  }

  // Appends a line to the rolling analysis log (capped to the last 500 lines).
  private appendLog(level: LogLine['level'], message: string): void {
    this.logs.update((lines) => [...lines.slice(-499), { level, message }]);
  }

  // Loads the analyzed photo list from the engine output.
  async refreshPhotos(): Promise<void> {
    const libraryId = this.activeLibraryId();
    const photos = await this.api.getPhotos(libraryId);
    if (libraryId === this.libraryService.activeLibraryId()) {
      this.photos.set(photos);
    }
  }

  // Refreshes the cached analysis status from the bridge.
  async refreshStatus(): Promise<void> {
    const libraryId = this.activeLibraryId();
    const status = await this.api.getAnalysisStatus(libraryId);
    if (libraryId === this.libraryService.activeLibraryId()) {
      this.status.set(status);
    }
  }

  // Opens the native folder picker and stores the chosen folder.
  async selectFolder(): Promise<string | null> {
    const libraryId = this.activeLibraryId();
    const folder = await this.api.selectPhotoFolder(libraryId);
    if (folder) {
      this.selectedFolder.set(folder);
      this.libraryService.setInputFolder(libraryId, folder);
    }
    return folder;
  }

  // Starts the analysis pipeline for the given configuration.
  async startAnalysis(config: AnalysisConfig): Promise<void> {
    this.logs.set([]);
    const libraryId = this.activeLibraryId();
    this.status.set({
      ...IDLE_STATUS,
      libraryId,
      running: true,
      step: 'indexing',
      message: 'Starting...',
    });
    await this.api.startAnalysis(libraryId, config);
  }

  pause(): Promise<void> {
    this.status.update((s) => ({ ...s, paused: true }));
    return this.api.pauseAnalysis(this.activeLibraryId());
  }

  resume(): Promise<void> {
    this.status.update((s) => ({ ...s, paused: false }));
    return this.api.resumeAnalysis(this.activeLibraryId());
  }

  cancel(): Promise<void> {
    this.status.update((s) => ({ ...s, running: false, step: 'idle', message: 'Cancelled' }));
    return this.api.cancelAnalysis(this.activeLibraryId());
  }

  openOutputFolder(): Promise<void> {
    return this.api.openOutputFolder(this.activeLibraryId());
  }

  openReportsFolder(): Promise<void> {
    return this.api.openReportsFolder(this.activeLibraryId());
  }

  // --- OpenAI Quality Mode (optional cloud flow) ---

  // Estimates the OpenAI cost for the gated set (no API calls).
  estimateOpenAiCost() {
    return this.api.estimateOpenAiCost(this.activeLibraryId());
  }

  // Probes OpenAI key/reachability/models for the active library.
  getOpenAiStatus() {
    return this.api.getOpenAiStatus(this.activeLibraryId());
  }

  // Starts the optional OpenAI Quality Mode run (streams progress like the local analysis).
  async startOpenAiQuality(options: { budgetUsd?: number; stage?: string }): Promise<void> {
    this.logs.set([]);
    const libraryId = this.activeLibraryId();
    this.status.set({
      ...IDLE_STATUS,
      libraryId,
      running: true,
      step: 'broad_scan',
      message: 'Starting OpenAI Quality Mode...',
    });
    await this.api.startOpenAiQuality(libraryId, options);
  }

  // Re-exports the OpenAI Quality Mode output folders + reports from stored results.
  exportOpenAiResults(): Promise<void> {
    return this.api.exportOpenAiResults(this.activeLibraryId());
  }

  // Exports one original photo through a native Save As dialog, regardless of its status.
  exportPhoto(photoId: string): Promise<boolean> {
    return this.api.exportPhoto(this.activeLibraryId(), photoId);
  }

  // Exports the current final set (or an explicit selection) to ordered folders.
  exportFinalSet(photoIds?: string[]): Promise<void> {
    const ids =
      photoIds ??
      this.finalSet()
        .map((slot) => slot.photo?.id)
        .filter((id): id is string => !!id);
    return this.api.exportFinalSet(this.activeLibraryId(), ids);
  }

  // Applies a manual status or slot change to a single photo in the store.
  updatePhoto(id: string, patch: Partial<PhotoItem>): void {
    this.photos.update((photos) => photos.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  }

  // Adds or removes a photo from the manual shortlist.
  toggleShortlist(id: string): void {
    this.shortlist.update((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  isShortlisted(id: string): boolean {
    return this.shortlist().includes(id);
  }

  // Toggles a photo in the A/B compare selection (capped at two photos).
  toggleCompare(id: string): void {
    this.compareSelection.update((ids) => {
      if (ids.includes(id)) {
        return ids.filter((x) => x !== id);
      }
      return ids.length >= 2 ? [ids[1], id] : [...ids, id];
    });
  }

  // Pins or releases the photo currently shown in a photo slot.
  toggleLock(slot: PhotoSlot, photoId: string | null): void {
    this.lockedSlots.update((locks) => {
      const next = { ...locks };
      if (next[slot]) {
        delete next[slot];
      } else if (photoId) {
        next[slot] = photoId;
      }
      return next;
    });
  }

  isLocked(slot: PhotoSlot): boolean {
    return !!this.lockedSlots()[slot];
  }

  // Persists the current review session (slot assignments, locks, shortlist) locally.
  saveReviewSession(): Promise<void> {
    const slotAssignments: Record<string, string> = {};
    const locks: Record<string, boolean> = {};
    for (const entry of this.finalSet()) {
      if (entry.photo) {
        slotAssignments[entry.slot] = entry.photo.id;
      }
      locks[entry.slot] = entry.locked;
    }
    const session: ReviewSession = { slotAssignments, locks, shortlist: this.shortlist() };
    return this.api.saveReviewSession(this.activeLibraryId(), session);
  }

  async switchLibrary(libraryId: string): Promise<void> {
    if (libraryId === this.libraryService.activeLibraryId()) {
      return;
    }
    if (this.photos().length > 0) {
      await this.saveReviewSession();
    }
    await this.libraryService.activate(libraryId);
    await this.loadActiveLibrary();
  }

  async createLibrary(name: string): Promise<void> {
    if (this.photos().length > 0) {
      await this.saveReviewSession();
    }
    await this.libraryService.create(name);
    await this.loadActiveLibrary();
  }

  async renameLibrary(libraryId: string, name: string): Promise<void> {
    await this.libraryService.rename(libraryId, name);
  }

  async deleteLibrary(libraryId: string): Promise<void> {
    const wasActive = libraryId === this.libraryService.activeLibraryId();
    await this.libraryService.delete(libraryId);
    if (wasActive) {
      await this.loadActiveLibrary();
    }
  }

  async clearGeneratedData(): Promise<void> {
    await this.api.clearGeneratedData(this.activeLibraryId());
    this.resetLibraryState();
  }
}

// Settings service: loads and persists local application settings via the bridge.

import { inject, Injectable, signal } from '@angular/core';
import { AURA_FRAME_API } from '../tokens/aura-frame-api.token';
import type { AppSettings } from '../models';

const DEFAULT_SETTINGS: AppSettings = {
  preset: 'balanced',
  vlmCandidateCount: 70,
  blurThreshold: 120,
  minBrightness: 35,
  debugMode: false,
  modifyOriginals: false,
  storeAnalysisLocally: true,
  openai: { enabled: false, budgetUsd: 16 },
};

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private readonly api = inject(AURA_FRAME_API);
  readonly settings = signal<AppSettings>({ ...DEFAULT_SETTINGS });

  // Loads persisted settings from local storage via the bridge.
  async load(): Promise<void> {
    this.settings.set(await this.api.getSettings());
  }

  // Persists the given settings and updates the local signal.
  async save(next: AppSettings): Promise<void> {
    const saved = await this.api.setSettings({ ...next, modifyOriginals: false });
    this.settings.set(saved);
  }
}

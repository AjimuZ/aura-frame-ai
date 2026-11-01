// Injection token for the AuraFrame bridge.
// Resolves to the real Electron-exposed `window.auraFrame` when present, otherwise to
// a browser mock so the UI is fully usable under `ng serve`.

import { InjectionToken } from '@angular/core';
import type { AuraFrameApi } from '../models';
import { createMockAuraFrameApi } from '../services/mock-aura-frame-api';

export const AURA_FRAME_API = new InjectionToken<AuraFrameApi>('AURA_FRAME_API', {
  providedIn: 'root',
  factory: (): AuraFrameApi => {
    if (typeof window !== 'undefined' && window.auraFrame) {
      return window.auraFrame;
    }
    return createMockAuraFrameApi();
  },
});

// True when running inside Electron with the secure bridge available.
export function isElectron(): boolean {
  return typeof window !== 'undefined' && !!window.auraFrame;
}

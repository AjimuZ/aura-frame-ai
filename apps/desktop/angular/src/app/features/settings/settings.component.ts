// Settings: check the model providers, and configure thresholds and privacy options.
import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { SettingsService } from '../../core/services/settings.service';
import { AURA_FRAME_API } from '../../core/tokens/aura-frame-api.token';
import type { AppSettings, OpenAiStatus, ProviderStatus, ProvidersStatus } from '../../core/models';
import { PhotoStore } from '../../core/services/photo-store.service';

@Component({
  selector: 'af-settings',
  standalone: true,
  imports: [PageHeaderComponent, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Settings" subtitle="Local-only configuration. Nothing is sent to the cloud." />

    <section class="af-card" style="max-width:640px;">
      <div class="af-field">
        <label class="af-field-label">Quality preset</label>
        <select class="af-input" [(ngModel)]="model().preset" name="preset">
          <option value="fast">Fast</option>
          <option value="balanced">Balanced</option>
          <option value="quality">Quality</option>
        </select>
      </div>

      <div class="af-field">
        <label class="af-field-label">Candidate photos sent to the model</label>
        <input class="af-input" type="number" [(ngModel)]="model().vlmCandidateCount" name="vlmCandidateCount" />
        <div class="af-field-hint">Only the best candidates after dedup/quality/face/diversity are sent.</div>
      </div>

      <div class="af-field">
        <label class="af-field-label">Model providers</label>
        <div class="af-actions">
          <button class="af-button-secondary" (click)="checkProviders()">Check providers</button>
          @if (providers(); as p) {
            @if (!p.reachable) {
              <span class="af-badge af-badge--warning">{{ p.message }}</span>
            }
          }
        </div>
        @if (providers(); as p) {
          @for (provider of p.providers; track provider.id) {
            <div class="af-field-hint" style="margin-top:8px;">
              <span
                class="af-badge"
                [class.af-badge--success]="provider.state === 'available'"
                [class.af-badge--warning]="provider.state !== 'available'"
              >{{ provider.label }}: {{ stateLabel(provider.state) }}</span>
              {{ provider.detail }}
            </div>
          }
        }
      </div>

      <div class="af-field">
        <label class="af-field-label">Blur threshold</label>
        <input class="af-input" type="number" [(ngModel)]="model().blurThreshold" name="blurThreshold" />
      </div>

      <div class="af-field">
        <label class="af-field-label">Minimum brightness</label>
        <input class="af-input" type="number" [(ngModel)]="model().minBrightness" name="minBrightness" />
      </div>

      <div class="af-field">
        <label class="af-field-label">Library output</label>
        <div class="af-field-hint">
          Output and reports are isolated in AuraFrame's private data area for
          {{ activeLibrary()?.name ?? 'the active library' }}.
        </div>
        <div class="af-actions" style="margin-top:8px;">
          <button class="af-button-secondary" (click)="store.openOutputFolder()">Open output folder</button>
        </div>
      </div>

      <div class="af-field">
        <label class="af-checkbox">
          <input type="checkbox" [(ngModel)]="model().debugMode" name="debugMode" />
          Debug mode (store raw AI responses locally)
        </label>
      </div>

      <div class="af-field">
        <label class="af-checkbox">
          <input type="checkbox" checked disabled />
          Never modify original photos (enforced)
        </label>
      </div>

      <div class="af-field">
        <label class="af-checkbox">
          <input type="checkbox" [(ngModel)]="model().storeAnalysisLocally" name="storeAnalysisLocally" />
          Store analysis locally
        </label>
      </div>

      <div class="af-actions">
        <button class="af-button-primary" (click)="save()">Save settings</button>
        @if (saved()) { <span class="af-badge af-badge--success">Saved</span> }
      </div>
    </section>

    <section class="af-card" style="max-width:640px; margin-top:24px;">
      <h3>OpenAI Quality Mode (optional cloud review)</h3>
      <p class="af-field-hint">
        Off by default and privacy-first. When enabled, only resized, EXIF-stripped copies of
        photos are sent to OpenAI — never originals, file names, paths, or location. The local
        pipeline keeps working without it.
      </p>

      <div class="af-field">
        <label class="af-checkbox">
          <input type="checkbox" [(ngModel)]="model().openai.enabled" name="openaiEnabled" />
          Enable OpenAI Quality Mode
        </label>
      </div>

      <div class="af-field">
        <label class="af-field-label">Budget cap (USD)</label>
        <input class="af-input" type="number" [(ngModel)]="model().openai.budgetUsd" name="openaiBudget" />
        <div class="af-field-hint">Hard spending limit; the run stops before exceeding it.</div>
      </div>

      <div class="af-field">
        <label class="af-field-label">OpenAI API key</label>
        <div class="af-actions">
          <span
            class="af-badge"
            [class.af-badge--success]="keyConfigured()"
            [class.af-badge--warning]="!keyConfigured()"
          >{{ keyConfigured() ? 'Configured' : 'Missing' }}</span>
        </div>
        <input
          class="af-input"
          type="password"
          [(ngModel)]="apiKeyDraft"
          name="openaiKey"
          placeholder="sk-..."
          autocomplete="off"
          style="margin-top:8px;"
        />
        <div class="af-actions" style="margin-top:8px;">
          <button class="af-button-secondary" (click)="saveKey()">Save key</button>
          <button class="af-button-secondary" (click)="clearKey()">Remove key</button>
          <button class="af-button-secondary" (click)="checkOpenAi()">Check OpenAI</button>
          @if (keyMessage()) { <span class="af-badge">{{ keyMessage() }}</span> }
        </div>
        <div class="af-field-hint">
          Stored in your operating system's encrypted credential store — never in the project,
          config, or logs. You can also set the OPENAI_API_KEY environment variable instead.
        </div>
        @if (openaiProbe(); as p) {
          <div class="af-field-hint" style="margin-top:8px;">
            {{ p.message }}
            @if (p.reachable) {
              — broad: {{ p.models['broad_scan'] }} · premium: {{ p.models['premium_review'] }}
              · judge: {{ p.models['final_judge'] }}
            }
          </div>
        }
      </div>
      <div class="af-field-hint">
        Models, per-stage photo limits, and image detail are configured in
        engine/config.yaml.
      </div>
    </section>

    <section class="af-card" style="max-width:640px; margin-top:24px;">
      <h3>Generated data</h3>
      <p class="af-field-hint">
        Deletes the local database, thumbnails, reports, and exported sets for
        {{ activeLibrary()?.name ?? 'the active library' }}. Your original photos and other
        libraries are never touched.
      </p>
      <div class="af-actions">
        @if (!confirmingClear()) {
          <button class="af-button-secondary" (click)="confirmingClear.set(true)">Clear generated data</button>
        } @else {
          <button class="af-button-primary" (click)="clearData()">Confirm delete</button>
          <button class="af-button-secondary" (click)="confirmingClear.set(false)">Cancel</button>
        }
        @if (cleared()) { <span class="af-badge af-badge--success">Cleared</span> }
      </div>
    </section>
  `,
})
export class SettingsComponent {
  private readonly settingsService = inject(SettingsService);
  private readonly api = inject(AURA_FRAME_API);
  readonly store = inject(PhotoStore);
  readonly activeLibrary = this.store.libraryService.activeLibrary;

  // Local editable copy of the persisted settings, synced from the service.
  readonly model = signal<AppSettings>({ ...this.settingsService.settings() });
  readonly saved = signal(false);
  readonly providers = signal<ProvidersStatus | null>(null);
  readonly confirmingClear = signal(false);
  readonly cleared = signal(false);

  // OpenAI Quality Mode key/status state. apiKeyDraft is a plain field bound to the input;
  // the key itself is write-only and never read back from storage.
  readonly keyConfigured = signal(false);
  readonly keyMessage = signal('');
  readonly openaiProbe = signal<OpenAiStatus | null>(null);
  apiKeyDraft = '';

  constructor() {
    // Keep the editable copy in sync when persisted settings load asynchronously.
    effect(() => {
      this.model.set({ ...this.settingsService.settings() });
    });
    void this.refreshKeyStatus();
  }

  // Refreshes whether an encrypted OpenAI key is configured (its value is never read back).
  async refreshKeyStatus(): Promise<void> {
    this.keyConfigured.set(await this.api.getOpenAiKeyStatus());
  }

  // Stores the entered API key in the OS-encrypted credential store.
  async saveKey(): Promise<void> {
    const key = this.apiKeyDraft.trim();
    if (!key) {
      this.keyMessage.set('Enter an API key first.');
      return;
    }
    try {
      await this.api.setOpenAiKey(key);
      this.apiKeyDraft = '';
      this.keyMessage.set('API key saved.');
      await this.refreshKeyStatus();
    } catch (error) {
      this.keyMessage.set((error as Error).message ?? 'Could not save the API key.');
    }
  }

  // Removes the stored API key.
  async clearKey(): Promise<void> {
    await this.api.clearOpenAiKey();
    this.keyMessage.set('API key removed.');
    await this.refreshKeyStatus();
  }

  // Probes the OpenAI API (key/reachability/resolved models) for the active library.
  async checkOpenAi(): Promise<void> {
    this.openaiProbe.set(await this.store.getOpenAiStatus());
  }

  async save(): Promise<void> {
    await this.settingsService.save(this.model());
    this.saved.set(true);
    setTimeout(() => this.saved.set(false), 2000);
  }

  // Runs the one provider check (`GET /api/providers`, through the bridge) and shows each provider's state.
  async checkProviders(): Promise<void> {
    this.providers.set(await this.api.checkProviders());
  }

  // Returns the words shown for a provider state.
  stateLabel(state: ProviderStatus['state']): string {
    switch (state) {
      case 'available':
        return 'available';
      case 'not_configured':
        return 'not configured';
      case 'disabled':
        return 'off';
    }
  }

  async clearData(): Promise<void> {
    await this.store.clearGeneratedData();
    this.confirmingClear.set(false);
    this.cleared.set(true);
    setTimeout(() => this.cleared.set(false), 2000);
  }
}

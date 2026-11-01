// Analysis: drives the pipeline and shows step progress, a progress bar, and live logs.
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { ProgressStepperComponent } from '../../shared/components/progress-stepper/progress-stepper.component';
import { PhotoStore } from '../../core/services/photo-store.service';
import { SettingsService } from '../../core/services/settings.service';
import type { AnalysisPreset, OpenAiCostEstimate } from '../../core/models';

@Component({
  selector: 'af-analysis',
  standalone: true,
  imports: [PageHeaderComponent, ProgressStepperComponent, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Analysis" [subtitle]="'Local computer-vision and AI pipeline for ' + (activeProfile()?.name ?? 'the active profile')">
      <button class="af-button-secondary" (click)="store.openOutputFolder()">Open output folder</button>
    </af-page-header>

    <div style="display:grid; grid-template-columns:1fr 1.4fr; gap:24px; align-items:start;">
      <section class="af-card">
        <h3>Pipeline</h3>
        <af-progress-stepper [current]="status().step" />

        <div style="margin-top:20px;">
          <div class="af-progress">
            <div class="af-progress__bar" [style.width.%]="percent()"></div>
          </div>
          <p class="af-field-hint">{{ status().message }} ({{ status().current }}/{{ status().total }})</p>
        </div>

        <div class="af-field" style="margin-top:20px;">
          <label class="af-field-label">Quality preset</label>
          <select class="af-input" [(ngModel)]="preset" name="preset" [disabled]="status().running">
            <option value="fast">Fast — quick triage, no AI review</option>
            <option value="balanced">Balanced — recommended</option>
            <option value="quality">Quality — deepest analysis</option>
          </select>
          <div class="af-field-hint">Presets adjust embeddings, aesthetics, and AI candidate budget.</div>
        </div>

        <div class="af-actions" style="margin-top:20px;">
          @if (!status().running) {
            <button class="af-button-primary" [disabled]="!folder()" (click)="start()">Start analysis</button>
          } @else if (status().paused) {
            <button class="af-button-primary" (click)="store.resume()">Resume</button>
          } @else {
            <button class="af-button-secondary" (click)="store.pause()">Pause</button>
          }
          <button class="af-button-secondary" [disabled]="!status().running" (click)="store.cancel()">Cancel</button>
        </div>

        @if (!folder()) {
          <p class="af-field-hint" style="margin-top:12px;">Select a folder in Import Photos first.</p>
        }
      </section>

      <section class="af-card">
        <h3>Activity log</h3>
        <div class="af-log">
          @for (line of logs(); track $index) {
            <div [class.af-log__line--warning]="line.level === 'warning'" [class.af-log__line--error]="line.level === 'error'">
              {{ line.message }}
            </div>
          }
          @if (logs().length === 0) {
            <div>Waiting to start…</div>
          }
        </div>
      </section>
    </div>

    <section class="af-card" style="margin-top:24px;">
      <h3>OpenAI Quality Mode (optional cloud review)</h3>
      <p class="af-field-hint">
        Run the local analysis first. Then optionally send resized, EXIF-stripped copies to
        OpenAI to build Top 6 / 20 / 50 / 100 + per-slot candidates. Configure it in Settings.
      </p>
      <div class="af-actions">
        <button class="af-button-secondary" [disabled]="status().running" (click)="estimateCost()">
          Estimate cost
        </button>
        <button
          class="af-button-primary"
          [disabled]="status().running || !openaiEnabled()"
          (click)="runOpenAi()"
        >Run OpenAI Quality Mode</button>
        <button class="af-button-secondary" [disabled]="status().running" (click)="exportOpenAi()">
          Export OpenAI results
        </button>
      </div>
      @if (!openaiEnabled()) {
        <p class="af-field-hint" style="margin-top:8px;">
          Enable OpenAI Quality Mode in Settings to run it (budget cap {{ budget() }} USD).
        </p>
      }
      @if (estimate(); as e) {
        <p class="af-field-hint" style="margin-top:8px;">
          Estimated cost: <strong>{{ e.estimatedTotalUsd }} USD</strong> for {{ e.eligible }}
          eligible photos (budget {{ e.budgetMaxUsd }} USD —
          {{ e.withinBudget ? 'within budget' : 'over budget' }}).
        </p>
      }
    </section>
  `,
})
export class AnalysisComponent {
  readonly store = inject(PhotoStore);
  private readonly settingsService = inject(SettingsService);

  readonly status = this.store.status;
  readonly logs = this.store.logs;
  readonly folder = this.store.selectedFolder;
  readonly activeProfile = this.store.profileService.activeProfile;
  preset: AnalysisPreset = 'balanced';

  readonly openaiEnabled = computed(() => this.settingsService.settings().openai?.enabled ?? false);
  readonly budget = computed(() => this.settingsService.settings().openai?.budgetUsd ?? 16);
  readonly estimate = signal<OpenAiCostEstimate | null>(null);

  readonly percent = computed(() => {
    const s = this.status();
    return s.total > 0 ? Math.round((s.current / s.total) * 100) : 0;
  });

  // Estimates the OpenAI cost from the gated set without calling the API.
  async estimateCost(): Promise<void> {
    this.estimate.set(await this.store.estimateOpenAiCost());
  }

  // Starts the optional OpenAI Quality Mode run using the configured budget cap.
  async runOpenAi(): Promise<void> {
    await this.store.startOpenAiQuality({ budgetUsd: this.budget() });
  }

  // Re-exports the OpenAI Quality Mode output folders + reports.
  async exportOpenAi(): Promise<void> {
    await this.store.exportOpenAiResults();
  }

  async start(): Promise<void> {
    const folder = this.folder();
    if (!folder) {
      return;
    }
    await this.store.startAnalysis({
      inputFolder: folder,
      includeSubfolders: true,
      ignoreDuplicates: true,
      preserveOriginals: true,
      generateThumbnails: true,
      runGemma: this.preset !== 'fast',
      preset: this.preset,
    });
  }
}

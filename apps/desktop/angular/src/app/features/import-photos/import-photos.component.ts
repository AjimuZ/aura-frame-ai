// Import Photos: choose a local folder and configure import options.
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { PhotoStore } from '../../core/services/photo-store.service';

@Component({
  selector: 'af-import-photos',
  standalone: true,
  imports: [PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Import Photos" [subtitle]="'Select a local folder for ' + (activeProfile()?.name ?? 'the active profile') + '. Photos never leave your computer.'" />

    <section class="af-card">
      <button
        type="button"
        class="af-empty-state"
        style="width:100%; cursor:pointer; background:transparent;"
        (click)="pickFolder()"
      >
        <div style="font-size:32px;" aria-hidden="true">⤓</div>
        <h3>{{ folder() ?? 'Choose a photo folder' }}</h3>
        <p>Click to select a local folder of photos to analyze.</p>
      </button>

      <div style="margin-top:24px; display:flex; flex-direction:column; gap:12px;">
        <label class="af-checkbox">
          <input type="checkbox" [checked]="includeSubfolders()" (change)="includeSubfolders.set($any($event.target).checked)" />
          Include subfolders
        </label>
        <label class="af-checkbox">
          <input type="checkbox" [checked]="ignoreDuplicates()" (change)="ignoreDuplicates.set($any($event.target).checked)" />
          Ignore duplicates
        </label>
        <label class="af-checkbox">
          <input type="checkbox" checked disabled />
          Preserve original files (always on)
        </label>
        <label class="af-checkbox">
          <input type="checkbox" [checked]="generateThumbnails()" (change)="generateThumbnails.set($any($event.target).checked)" />
          Generate thumbnails
        </label>
      </div>

      <div class="af-actions" style="margin-top:24px;">
        <button class="af-button-secondary" (click)="pickFolder()">Select folder</button>
        <button class="af-button-primary" [disabled]="!folder()" (click)="continueToAnalysis()">
          Continue to analysis
        </button>
      </div>

      <p class="af-field-hint" style="margin-top:16px;">
        AuraFrame AI never uploads your photos and never modifies the originals. All
        processing happens on your machine.
      </p>
    </section>
  `,
})
export class ImportPhotosComponent {
  private readonly store = inject(PhotoStore);
  private readonly router = inject(Router);

  readonly folder = this.store.selectedFolder;
  readonly activeProfile = this.store.profileService.activeProfile;
  readonly includeSubfolders = signal(true);
  readonly ignoreDuplicates = signal(true);
  readonly generateThumbnails = signal(true);

  async pickFolder(): Promise<void> {
    await this.store.selectFolder();
  }

  continueToAnalysis(): void {
    void this.router.navigate(['/analysis']);
  }
}

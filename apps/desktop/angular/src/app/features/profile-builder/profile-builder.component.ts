// Profile Builder: shows the balanced final set of six photos by slot.
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { ScoreBadgeComponent } from '../../shared/components/score-badge/score-badge.component';
import { FileUrlPipe } from '../../shared/pipes/file-url.pipe';
import { PhotoStore } from '../../core/services/photo-store.service';
import { PROFILE_SLOT_LABELS, type ProfileSlot } from '../../core/models';

@Component({
  selector: 'af-profile-builder',
  standalone: true,
  imports: [PageHeaderComponent, ScoreBadgeComponent, FileUrlPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Profile Builder" [subtitle]="'A balanced set of six photos for ' + (activeProfile()?.name ?? 'the active profile')">
      <button class="af-button-secondary" (click)="store.refreshPhotos()">Generate new set</button>
      <button class="af-button-secondary" (click)="save()">Save session</button>
      <button class="af-button-primary" (click)="export()">Export final set</button>
      @if (saved()) { <span class="af-badge af-badge--success">Saved</span> }
      @if (exportError()) { <span class="af-badge af-badge--warning">{{ exportError() }}</span> }
    </af-page-header>

    <section class="af-grid" style="grid-template-columns: repeat(3, 1fr);">
      @for (slot of finalSet(); track slot.slot) {
        <div class="af-card" style="padding:0; overflow:hidden;">
          <div style="padding:12px 16px; display:flex; justify-content:space-between; align-items:center;">
            <strong>{{ label(slot.slot) }} @if (slot.locked) { <span title="Locked">🔒</span> }</strong>
            @if (slot.photo) { <af-score-badge [score]="slot.photo.finalScore" /> }
          </div>
          @if (slot.photo; as photo) {
            @if (photo.thumbnailPath) {
              <img [src]="photo.thumbnailPath | fileUrl: photo.profileId" [alt]="photo.fileName" style="width:100%; aspect-ratio:4/5; object-fit:cover;" />
            } @else {
              <div style="aspect-ratio:4/5; background:var(--af-bg-deep); display:flex; align-items:center; justify-content:center; color:var(--af-text-muted);">No preview</div>
            }
            <div style="padding:12px 16px;">
              <p class="af-field-hint" style="margin:0 0 12px;">{{ photo.gemmaScore?.reason ?? photo.fileName }}</p>
              <div class="af-actions">
                <button
                  class="af-button-secondary"
                  [disabled]="exportingPhotoId() === photo.id"
                  (click)="exportPhoto(photo.id)"
                >
                  @if (exportingPhotoId() === photo.id) {
                    Exporting…
                  } @else if (exportedPhotoId() === photo.id) {
                    Exported ✓
                  } @else {
                    Export photo
                  }
                </button>
                <button class="af-button-secondary" [disabled]="slot.locked" (click)="store.refreshPhotos()">Replace</button>
                <button class="af-button-secondary" (click)="store.toggleLock(slot.slot, photo.id)">
                  {{ slot.locked ? 'Unlock' : 'Lock' }}
                </button>
              </div>
            </div>
          } @else {
            <div style="aspect-ratio:4/5; display:flex; align-items:center; justify-content:center; color:var(--af-text-muted); border-top:1px solid var(--af-border-soft);">
              No photo assigned
            </div>
          }
        </div>
      }
    </section>

    <div class="af-actions" style="margin-top:24px;">
      <button class="af-button-secondary" (click)="store.openOutputFolder()">Open output folder</button>
    </div>
  `,
})
export class ProfileBuilderComponent {
  readonly store = inject(PhotoStore);
  readonly finalSet = this.store.finalSet;
  readonly activeProfile = this.store.profileService.activeProfile;
  readonly saved = signal(false);
  readonly exportingPhotoId = signal<string | null>(null);
  readonly exportedPhotoId = signal<string | null>(null);
  readonly exportError = signal<string | null>(null);

  label(slot: ProfileSlot): string {
    return PROFILE_SLOT_LABELS[slot];
  }

  export(): void {
    void this.store.exportFinalSet();
  }

  async exportPhoto(photoId: string): Promise<void> {
    this.exportingPhotoId.set(photoId);
    this.exportedPhotoId.set(null);
    this.exportError.set(null);
    try {
      if (await this.store.exportPhoto(photoId)) {
        this.exportedPhotoId.set(photoId);
        setTimeout(() => {
          if (this.exportedPhotoId() === photoId) this.exportedPhotoId.set(null);
        }, 2500);
      }
    } catch (error) {
      this.exportError.set(error instanceof Error ? error.message : 'Could not export this photo.');
    } finally {
      this.exportingPhotoId.set(null);
    }
  }

  async save(): Promise<void> {
    await this.store.saveReviewSession();
    this.saved.set(true);
    setTimeout(() => this.saved.set(false), 2000);
  }
}

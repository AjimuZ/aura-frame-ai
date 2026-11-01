// Review: filterable photo grid with a detail drawer, A/B compare, shortlist, and
// explainability (reason tags + face/aesthetic metrics) plus manual override actions.
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { PhotoCardComponent } from '../../shared/components/photo-card/photo-card.component';
import { EmptyStateComponent } from '../../shared/components/empty-state/empty-state.component';
import { ScoreBadgeComponent } from '../../shared/components/score-badge/score-badge.component';
import { CategoryChipComponent } from '../../shared/components/category-chip/category-chip.component';
import { ReasonTagComponent } from '../../shared/components/reason-tag/reason-tag.component';
import { FileUrlPipe } from '../../shared/pipes/file-url.pipe';
import { PhotoStore } from '../../core/services/photo-store.service';
import {
  PHOTO_SLOT_LABELS,
  type PhotoCategory,
  type PhotoItem,
  type PhotoSlot,
} from '../../core/models';

type Filter =
  | { kind: 'all' }
  | { kind: 'category'; value: PhotoCategory }
  | { kind: 'minScore'; value: number }
  | { kind: 'group' }
  | { kind: 'similar' }
  | { kind: 'screenshot' }
  | { kind: 'shortlist' }
  | { kind: 'rejected' }
  | { kind: 'tier'; value: 'top6' | 'top20' | 'top50' | 'top100' }
  | { kind: 'manualReview' }
  | { kind: 'hardReject' }
  | { kind: 'softFlagged' };

@Component({
  selector: 'af-review',
  standalone: true,
  imports: [
    PageHeaderComponent,
    PhotoCardComponent,
    EmptyStateComponent,
    ScoreBadgeComponent,
    CategoryChipComponent,
    ReasonTagComponent,
    FileUrlPipe,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Review" [subtitle]="'Browse, filter, and curate photos for ' + (activeLibrary()?.name ?? 'the active library')">
      <button class="af-button-secondary" (click)="store.refreshPhotos()">Refresh</button>
    </af-page-header>

    <div class="af-actions" style="margin-bottom:20px; flex-wrap:wrap;">
      @for (chip of filterChips; track chip.label) {
        <button
          class="af-chip af-chip--active"
          [style.opacity]="isActive(chip.filter) ? 1 : 0.6"
          (click)="setFilter(chip.filter)"
        >
          {{ chip.label }}
        </button>
      }
    </div>

    @if (compared().length === 2) {
      <section class="af-card" style="margin-bottom:20px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3 style="margin:0;">Compare A / B</h3>
          <button class="af-button-secondary" (click)="store.compareSelection.set([])">Clear</button>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:12px;">
          @for (photo of compared(); track photo.id) {
            <div>
              @if (photo.hasThumbnail) {
                <img [src]="photo.id | fileUrl: photo.libraryId" [alt]="photo.fileName" style="width:100%; border-radius:12px;" />
              }
              <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">
                <af-score-badge [score]="photo.finalScore" />
                <af-category-chip [category]="photo.category" />
              </div>
              <p class="af-field-hint" style="margin:6px 0 0;">
                Face {{ photo.faceQualityScore }} · Aesthetic {{ photo.aestheticScore }} · Cluster {{ photo.clusterId }}
              </p>
            </div>
          }
        </div>
      </section>
    }

    @if (photos().length === 0) {
      <af-empty-state title="No photos yet" message="Run an analysis to populate the review grid." />
    } @else {
      <div style="display:grid; grid-template-columns: 1fr 360px; gap:24px; align-items:start;">
        <section class="af-grid af-grid--photos">
          @for (photo of filtered(); track photo.id) {
            <af-photo-card [photo]="photo" (cardClick)="select($event)" />
          }
        </section>

        <aside class="af-card" style="position:sticky; top:0;">
          @if (selected(); as photo) {
            @if (photo.hasThumbnail) {
              <img [src]="photo.id | fileUrl: photo.libraryId" [alt]="photo.fileName" style="width:100%; border-radius:12px;" />
            }
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px;">
              <af-score-badge [score]="photo.finalScore" />
              <af-category-chip [category]="photo.category" />
            </div>
            <h3 style="margin-top:12px;">{{ photo.fileName }}</h3>
            <p class="af-field-hint">Best slot: {{ slotLabel(photo.bestSlot) }}</p>

            @if (photo.reasonTags.length) {
              <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;">
                @for (tag of photo.reasonTags; track tag) { <af-reason-tag [tag]="tag" /> }
              </div>
            }

            <div class="af-field-label" style="margin-top:12px;">Metrics</div>
            <p class="af-field-hint" style="margin:4px 0 0;">
              Technical {{ photo.technicalScore }} · Face quality {{ photo.faceQualityScore }} ·
              Aesthetic {{ photo.aestheticScore }} · Eyes {{ photo.eyesVisibleScore }} ·
              Faces {{ photo.numberOfFaces }} · Cluster {{ photo.clusterId }}
            </p>

            @if (photo.gemmaScore; as g) {
              <p style="margin-top:8px;">{{ g.reason }}</p>
              @if (g.strengths.length) {
                <div class="af-field-label" style="margin-top:8px;">Strengths</div>
                <ul style="margin:0; padding-left:18px; color:var(--af-success);">
                  @for (s of g.strengths; track s) { <li>{{ s }}</li> }
                </ul>
              }
              @if (g.weaknesses.length) {
                <div class="af-field-label" style="margin-top:8px;">Weaknesses</div>
                <ul style="margin:0; padding-left:18px; color:var(--af-warning);">
                  @for (w of g.weaknesses; track w) { <li>{{ w }}</li> }
                </ul>
              }
            }

            @if (photo.eligibleForOpenai || photo.consensusScore) {
              <div class="af-field-label" style="margin-top:12px;">OpenAI scores</div>
              <p class="af-field-hint" style="margin:4px 0 0;">
                Consensus {{ photo.consensusScore }} · Broad {{ photo.openaiBroadScore }} ·
                Quality {{ photo.openaiQualityScore }} · Premium {{ photo.openaiPremiumScore }} ·
                Judge {{ photo.finalJudgeScore }}
              </p>
              @if (photo.finalSelectedSlot) {
                <p class="af-field-hint">Final slot: {{ photo.finalSelectedSlot }}</p>
              }
              @if (photo.openaiReason) { <p style="margin-top:6px;">{{ photo.openaiReason }}</p> }
              @if (photo.openaiRisk) {
                <p class="af-field-hint" style="color:var(--af-warning);">Risk: {{ photo.openaiRisk }}</p>
              }
              @if (photo.needsManualReview) {
                <span class="af-badge af-badge--warning">Needs manual review</span>
              }
            }

            @if (photo.softFlags?.length) {
              <div class="af-field-label" style="margin-top:12px;">Soft flags</div>
              <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:4px;">
                @for (flag of photo.softFlags; track flag) { <span class="af-badge">{{ flag }}</span> }
              </div>
            }

            @if (photo.gateStatus === 'hard_reject' || photo.gateStatus === 'failed_read') {
              <p class="af-field-hint" style="margin-top:8px; color:var(--af-warning);">
                Hard rejected: {{ photo.hardRejectReason }}
              </p>
            }

            <div class="af-actions" style="margin-top:16px; flex-wrap:wrap;">
              <button
                class="af-button-secondary"
                [disabled]="exportingPhotoId() === photo.id"
                (click)="exportPhoto(photo)"
              >
                @if (exportingPhotoId() === photo.id) {
                  Exporting…
                } @else if (exportedPhotoId() === photo.id) {
                  Exported ✓
                } @else {
                  Export photo
                }
              </button>
              <button class="af-button-primary" (click)="promote(photo)">Promote</button>
              <button class="af-button-secondary" (click)="reject(photo)">Reject</button>
              <button class="af-button-secondary" (click)="store.toggleShortlist(photo.id)">
                {{ store.isShortlisted(photo.id) ? 'Shortlisted ✓' : 'Shortlist' }}
              </button>
              <button class="af-button-secondary" (click)="store.toggleCompare(photo.id)">Compare</button>
              <button class="af-button-secondary" (click)="assign(photo, 'main_photo')">Use as main</button>
              <button class="af-button-secondary" (click)="assign(photo, 'full_body')">Use as full body</button>
              <button class="af-button-secondary" (click)="assign(photo, 'activity_photo')">Use as activity</button>
              <button class="af-button-secondary" (click)="assign(photo, 'second_photo')">Use as candid</button>
              <button class="af-button-secondary" (click)="assign(photo, 'social_photo')">Use as social</button>
              <button class="af-button-secondary" (click)="assign(photo, 'elegant_photo')">Use as elegant</button>
              @if (exportError()) {
                <span class="af-badge af-badge--warning">{{ exportError() }}</span>
              }
            </div>
          } @else {
            <af-empty-state title="Select a photo" message="Click any photo to see its details and actions." />
          }
        </aside>
      </div>
    }
  `,
})
export class ReviewComponent {
  readonly store = inject(PhotoStore);
  readonly activeLibrary = this.store.libraryService.activeLibrary;
  readonly photos = this.store.photos;

  private readonly filter = signal<Filter>({ kind: 'all' });
  readonly selected = signal<PhotoItem | null>(null);
  readonly exportingPhotoId = signal<string | null>(null);
  readonly exportedPhotoId = signal<string | null>(null);
  readonly exportError = signal<string | null>(null);

  // The (up to two) photos currently selected for A/B comparison.
  readonly compared = computed(() => {
    const ids = this.store.compareSelection();
    return this.photos().filter((p) => ids.includes(p.id));
  });

  readonly filterChips: { label: string; filter: Filter }[] = [
    { label: 'All', filter: { kind: 'all' } },
    { label: 'Headshot', filter: { kind: 'category', value: 'HEADSHOT' } },
    { label: 'Full body', filter: { kind: 'category', value: 'FULL_BODY' } },
    { label: 'Activity', filter: { kind: 'category', value: 'ACTIVITY' } },
    { label: 'Social', filter: { kind: 'category', value: 'SOCIAL' } },
    { label: 'Candid', filter: { kind: 'category', value: 'CANDID' } },
    { label: 'Elegant', filter: { kind: 'category', value: 'ELEGANT' } },
    { label: '9+', filter: { kind: 'minScore', value: 9 } },
    { label: '8+', filter: { kind: 'minScore', value: 8 } },
    { label: '7+', filter: { kind: 'minScore', value: 7 } },
    { label: 'Group', filter: { kind: 'group' } },
    { label: 'Similar', filter: { kind: 'similar' } },
    { label: 'Screenshots', filter: { kind: 'screenshot' } },
    { label: 'Shortlist', filter: { kind: 'shortlist' } },
    { label: 'Rejected', filter: { kind: 'rejected' } },
    { label: 'Top 6', filter: { kind: 'tier', value: 'top6' } },
    { label: 'Top 20', filter: { kind: 'tier', value: 'top20' } },
    { label: 'Top 50', filter: { kind: 'tier', value: 'top50' } },
    { label: 'Top 100', filter: { kind: 'tier', value: 'top100' } },
    { label: 'Needs review', filter: { kind: 'manualReview' } },
    { label: 'Hard rejected', filter: { kind: 'hardReject' } },
    { label: 'Soft flagged', filter: { kind: 'softFlagged' } },
  ];

  readonly filtered = computed(() => {
    const f = this.filter();
    const photos = [...this.photos()].sort((a, b) => b.finalScore - a.finalScore);
    switch (f.kind) {
      case 'category':
        return photos.filter((p) => p.category === f.value);
      case 'minScore':
        return photos.filter((p) => p.finalScore >= f.value);
      case 'group':
        return photos.filter((p) => p.groupPhoto);
      case 'similar': {
        const counts = new Map<number, number>();
        for (const p of photos) {
          if (p.clusterId >= 0) counts.set(p.clusterId, (counts.get(p.clusterId) ?? 0) + 1);
        }
        return photos.filter((p) => p.clusterId >= 0 && (counts.get(p.clusterId) ?? 0) > 1);
      }
      case 'screenshot':
        return photos.filter((p) => p.likelyScreenshot);
      case 'shortlist':
        return photos.filter((p) => this.store.isShortlisted(p.id));
      case 'rejected':
        return photos.filter((p) => p.status === 'rejected');
      case 'tier': {
        const inTier = (p: PhotoItem): boolean =>
          f.value === 'top6'
            ? !!p.inTop6
            : f.value === 'top20'
              ? !!p.inTop20
              : f.value === 'top50'
                ? !!p.inTop50
                : !!p.inTop100;
        return photos
          .filter(inTier)
          .sort((a, b) => (b.consensusScore ?? 0) - (a.consensusScore ?? 0));
      }
      case 'manualReview':
        return photos.filter((p) => p.needsManualReview);
      case 'hardReject':
        return photos.filter((p) => p.gateStatus === 'hard_reject' || p.gateStatus === 'failed_read');
      case 'softFlagged':
        return photos.filter((p) => (p.softFlags?.length ?? 0) > 0);
      default:
        return photos;
    }
  });

  setFilter(filter: Filter): void {
    this.filter.set(filter);
  }

  isActive(filter: Filter): boolean {
    return JSON.stringify(filter) === JSON.stringify(this.filter());
  }

  select(photo: PhotoItem): void {
    this.selected.set(photo);
  }

  slotLabel(slot: PhotoSlot): string {
    return PHOTO_SLOT_LABELS[slot];
  }

  promote(photo: PhotoItem): void {
    this.store.updatePhoto(photo.id, { status: 'selected' });
    this.refreshSelected(photo.id);
  }

  reject(photo: PhotoItem): void {
    this.store.updatePhoto(photo.id, { status: 'rejected' });
    this.refreshSelected(photo.id);
  }

  assign(photo: PhotoItem, slot: PhotoSlot): void {
    this.store.updatePhoto(photo.id, { bestSlot: slot, status: 'selected' });
    this.refreshSelected(photo.id);
  }

  async exportPhoto(photo: PhotoItem): Promise<void> {
    this.exportingPhotoId.set(photo.id);
    this.exportedPhotoId.set(null);
    this.exportError.set(null);
    try {
      if (await this.store.exportPhoto(photo.id)) {
        this.exportedPhotoId.set(photo.id);
        setTimeout(() => {
          if (this.exportedPhotoId() === photo.id) this.exportedPhotoId.set(null);
        }, 2500);
      }
    } catch (error) {
      this.exportError.set(error instanceof Error ? error.message : 'Could not export this photo.');
    } finally {
      this.exportingPhotoId.set(null);
    }
  }

  private refreshSelected(id: string): void {
    const updated = this.photos().find((p) => p.id === id) ?? null;
    this.selected.set(updated);
  }
}

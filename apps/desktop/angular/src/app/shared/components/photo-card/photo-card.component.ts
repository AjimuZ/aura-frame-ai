// Photo card: thumbnail with score badge, category chip, and best-slot label.
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { FileUrlPipe } from '../../pipes/file-url.pipe';
import { ScoreBadgeComponent } from '../score-badge/score-badge.component';
import { CategoryChipComponent } from '../category-chip/category-chip.component';
import { PHOTO_SLOT_LABELS, type PhotoItem } from '../../../core/models';

@Component({
  selector: 'af-photo-card',
  standalone: true,
  imports: [FileUrlPipe, ScoreBadgeComponent, CategoryChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="af-photo-card"
      [class.af-photo-card--selected]="photo().status === 'selected'"
      [class.af-photo-card--rejected]="photo().status === 'rejected'"
      style="text-align:left; padding:0; width:100%; border-width:1px;"
      (click)="cardClick.emit(photo())"
    >
      <div style="position:relative;">
        @if (photo().hasThumbnail) {
          <img class="af-photo-card__thumb" [src]="photo().id | fileUrl: photo().libraryId" [alt]="photo().fileName" />
        } @else {
          <div class="af-photo-card__thumb" style="display:flex; align-items:center; justify-content:center; color:var(--af-text-muted);">No preview</div>
        }
        <div style="position:absolute; top:8px; right:8px;">
          <af-score-badge [score]="photo().finalScore" />
        </div>
      </div>
      <div class="af-photo-card__body">
        <div class="af-photo-card__name">{{ photo().fileName }}</div>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <af-category-chip [category]="photo().category" />
          <span class="af-photo-card__name" style="font-size:11px;">{{ slotLabel() }}</span>
        </div>
      </div>
    </button>
  `,
})
export class PhotoCardComponent {
  readonly photo = input.required<PhotoItem>();
  readonly cardClick = output<PhotoItem>();

  readonly slotLabel = computed(() => PHOTO_SLOT_LABELS[this.photo().bestSlot] ?? '');
}

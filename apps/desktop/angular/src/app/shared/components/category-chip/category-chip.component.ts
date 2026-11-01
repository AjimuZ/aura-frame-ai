// Renders a photo category as a labeled chip.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { PhotoCategory } from '../../../core/models';

const LABELS: Record<PhotoCategory, string> = {
  HEADSHOT: 'Headshot',
  FULL_BODY: 'Full Body',
  ACTIVITY: 'Activity',
  SOCIAL: 'Social',
  CANDID: 'Candid',
  ELEGANT: 'Elegant',
  BAD: 'Bad',
  REJECT: 'Reject',
};

@Component({
  selector: 'af-category-chip',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="af-chip">{{ label() }}</span>`,
})
export class CategoryChipComponent {
  readonly category = input.required<PhotoCategory>();
  readonly label = computed(() => LABELS[this.category()] ?? this.category());
}

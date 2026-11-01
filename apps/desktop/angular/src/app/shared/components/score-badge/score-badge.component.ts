// Displays a final score as a colored badge. Tone reflects the score band so state is
// not conveyed by color alone (the numeric value is always shown).
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

@Component({
  selector: 'af-score-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="af-score-badge" [style.background]="background()">{{ display() }}</span>`,
})
export class ScoreBadgeComponent {
  readonly score = input.required<number>();

  readonly display = computed(() => this.score().toFixed(1));

  readonly background = computed(() => {
    const value = this.score();
    if (value >= 8) {
      return 'var(--af-primary)';
    }
    if (value >= 6) {
      return 'var(--af-primary-muted)';
    }
    return 'var(--af-bg-elevated)';
  });
}

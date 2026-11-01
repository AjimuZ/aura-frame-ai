// Renders a short explainability tag (e.g. "sharp face", "too similar") as a chip.
// Positive and cautionary tags are tinted differently for quick scanning.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

const CAUTION = new Set(['too similar', 'group photo', 'screenshot', 'low light']);

@Component({
  selector: 'af-reason-tag',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="af-tag" [class.af-tag--caution]="caution()">{{ tag() }}</span>`,
  styles: [
    `
      .af-tag {
        display: inline-block;
        font-size: 11px;
        line-height: 1;
        padding: 4px 8px;
        border-radius: 999px;
        background: var(--af-bg-deep);
        color: var(--af-text-soft);
        border: 1px solid rgba(243, 216, 221, 0.18);
      }
      .af-tag--caution {
        color: #f0b86e;
        border-color: rgba(240, 184, 110, 0.4);
      }
    `,
  ],
})
export class ReasonTagComponent {
  readonly tag = input.required<string>();
  readonly caution = computed(() => CAUTION.has(this.tag()));
}

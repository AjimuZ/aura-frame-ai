// Vertical stepper showing analysis pipeline progress.
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ANALYSIS_STEPS, type AnalysisStep } from '../../../core/models';

@Component({
  selector: 'af-progress-stepper',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="af-stepper">
      @for (item of steps(); track item.step) {
        <div
          class="af-step"
          [class.af-step--active]="item.state === 'active'"
          [class.af-step--done]="item.state === 'done'"
        >
          <span class="af-step__dot"></span>
          <span>{{ item.label }}</span>
          @if (item.state === 'done') {
            <span style="margin-left:auto;">✓</span>
          }
        </div>
      }
    </div>
  `,
})
export class ProgressStepperComponent {
  readonly current = input.required<AnalysisStep>();

  readonly steps = computed(() => {
    const order = ANALYSIS_STEPS.map((s) => s.step);
    const currentIndex = order.indexOf(this.current());
    return ANALYSIS_STEPS.map((item, index) => {
      let state: 'pending' | 'active' | 'done' = 'pending';
      if (this.current() === 'done') {
        state = 'done';
      } else if (currentIndex === -1) {
        state = 'pending';
      } else if (index < currentIndex) {
        state = 'done';
      } else if (index === currentIndex) {
        state = 'active';
      }
      return { ...item, state };
    });
  });
}

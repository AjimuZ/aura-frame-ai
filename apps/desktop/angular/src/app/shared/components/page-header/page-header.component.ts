// Reusable page header with title, subtitle, and an optional actions slot.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'af-page-header',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="af-page-header">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px;">
        <div>
          <h1 class="af-page-title">{{ title() }}</h1>
          @if (subtitle()) {
            <p class="af-page-subtitle">{{ subtitle() }}</p>
          }
        </div>
        <div class="af-actions">
          <ng-content></ng-content>
        </div>
      </div>
    </header>
  `,
})
export class PageHeaderComponent {
  readonly title = input.required<string>();
  readonly subtitle = input<string>('');
}

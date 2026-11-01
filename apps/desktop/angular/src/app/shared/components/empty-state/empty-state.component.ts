// Generic empty-state placeholder with an icon, title, and message.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'af-empty-state',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="af-empty-state">
      <div style="font-size:32px;" aria-hidden="true">{{ icon() }}</div>
      <h3>{{ title() }}</h3>
      <p>{{ message() }}</p>
      <ng-content></ng-content>
    </div>
  `,
})
export class EmptyStateComponent {
  readonly icon = input<string>('◍');
  readonly title = input.required<string>();
  readonly message = input<string>('');
}

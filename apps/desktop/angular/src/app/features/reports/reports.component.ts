// Reports: score distribution, per-category counts, and reject reasons.
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { EmptyStateComponent } from '../../shared/components/empty-state/empty-state.component';
import { PhotoStore } from '../../core/services/photo-store.service';
import type { PhotoCategory } from '../../core/models';

@Component({
  selector: 'af-reports',
  standalone: true,
  imports: [PageHeaderComponent, EmptyStateComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Reports" [subtitle]="'Distribution and curation summary for ' + (activeProfile()?.name ?? 'the active profile')">
      <button class="af-button-secondary" (click)="store.openReportsFolder()">Open reports folder</button>
    </af-page-header>

    @if (photos().length === 0) {
      <af-empty-state title="No data yet" message="Run an analysis to generate reports." />
    } @else {
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:24px; align-items:start;">
        <section class="af-card">
          <h3>Score distribution</h3>
          @for (band of distribution(); track band.label) {
            <div style="margin-bottom:10px;">
              <div style="display:flex; justify-content:space-between;">
                <span class="af-field-hint">{{ band.label }}</span>
                <span class="af-field-hint">{{ band.count }}</span>
              </div>
              <div class="af-progress"><div class="af-progress__bar" [style.width.%]="band.percent"></div></div>
            </div>
          }
        </section>

        <section class="af-card">
          <h3>Photos per category</h3>
          @for (row of categories(); track row.category) {
            <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--af-border-soft);">
              <span>{{ row.category }}</span>
              <strong>{{ row.count }}</strong>
            </div>
          }
        </section>

        <section class="af-card" style="grid-column:1 / -1;">
          <h3>Top reject reasons</h3>
          @if (rejectReasons().length === 0) {
            <p class="af-field-hint">No rejected photos.</p>
          } @else {
            @for (row of rejectReasons(); track row.reason) {
              <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--af-border-soft);">
                <span>{{ row.reason }}</span>
                <strong>{{ row.count }}</strong>
              </div>
            }
          }
        </section>
      </div>
    }
  `,
})
export class ReportsComponent {
  readonly store = inject(PhotoStore);
  readonly photos = this.store.photos;
  readonly activeProfile = this.store.profileService.activeProfile;

  readonly distribution = computed(() => {
    const photos = this.photos();
    const bands = [
      { label: '9 - 10', min: 9, max: 11 },
      { label: '8 - 9', min: 8, max: 9 },
      { label: '7 - 8', min: 7, max: 8 },
      { label: '5 - 7', min: 5, max: 7 },
      { label: '0 - 5', min: 0, max: 5 },
    ];
    const total = photos.length || 1;
    return bands.map((b) => {
      const count = photos.filter((p) => p.finalScore >= b.min && p.finalScore < b.max).length;
      return { label: b.label, count, percent: Math.round((count / total) * 100) };
    });
  });

  readonly categories = computed(() => {
    const map = new Map<PhotoCategory, number>();
    for (const p of this.photos()) {
      map.set(p.category, (map.get(p.category) ?? 0) + 1);
    }
    return [...map.entries()]
      .map(([category, count]) => ({ category, count }))
      .sort((a, b) => b.count - a.count);
  });

  readonly rejectReasons = computed(() => {
    const map = new Map<string, number>();
    for (const p of this.photos()) {
      if (p.status === 'rejected') {
        const reason = p.rejectReason ?? 'Unspecified';
        map.set(reason, (map.get(reason) ?? 0) + 1);
      }
    }
    return [...map.entries()]
      .map(([reason, count]) => ({ reason, count }))
      .sort((a, b) => b.count - a.count);
  });
}

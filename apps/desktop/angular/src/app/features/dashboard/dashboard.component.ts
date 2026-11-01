// Dashboard: overview statistics and quick actions.
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router } from '@angular/router';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header.component';
import { PhotoStore } from '../../core/services/photo-store.service';

@Component({
  selector: 'af-dashboard',
  standalone: true,
  imports: [PageHeaderComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <af-page-header title="Dashboard" [subtitle]="'Local photo curation for ' + (activeProfile()?.name ?? 'the active profile')">
      <button class="af-button-secondary" (click)="go('/import')">Import photos</button>
      <button class="af-button-primary" (click)="go('/analysis')">Start analysis</button>
    </af-page-header>

    <section class="af-grid af-grid--stats">
      <div class="af-card">
        <div class="af-stat-card__label">Photos imported</div>
        <div class="af-stat-card__value">{{ stats().imported }}</div>
      </div>
      <div class="af-card">
        <div class="af-stat-card__label">Valid photos</div>
        <div class="af-stat-card__value">{{ stats().valid }}</div>
      </div>
      <div class="af-card">
        <div class="af-stat-card__label">Rejected</div>
        <div class="af-stat-card__value">{{ stats().rejected }}</div>
      </div>
      <div class="af-card">
        <div class="af-stat-card__label">Analyzed by Gemma</div>
        <div class="af-stat-card__value">{{ stats().gemmaAnalyzed }}</div>
      </div>
      <div class="af-card">
        <div class="af-stat-card__label">Best score</div>
        <div class="af-stat-card__value">{{ stats().bestScore }}</div>
      </div>
      <div class="af-card">
        <div class="af-stat-card__label">Final set</div>
        <div class="af-stat-card__value">{{ stats().selected }} / 6</div>
      </div>
    </section>

    <section class="af-card" style="margin-top:24px;">
      <h3>Quick actions</h3>
      <p class="af-page-subtitle">Everything runs locally. Your photos never leave your computer.</p>
      <div class="af-actions" style="margin-top:16px;">
        <button class="af-button-secondary" (click)="go('/import')">Import photos</button>
        <button class="af-button-secondary" (click)="go('/analysis')">Run analysis</button>
        <button class="af-button-secondary" (click)="go('/review')">Open Top 20</button>
        <button class="af-button-primary" (click)="go('/profile-builder')">Generate curated set</button>
      </div>
    </section>
  `,
})
export class DashboardComponent {
  private readonly store = inject(PhotoStore);
  private readonly router = inject(Router);

  readonly stats = this.store.stats;
  readonly activeProfile = this.store.profileService.activeProfile;

  go(path: string): void {
    void this.router.navigate([path]);
  }
}

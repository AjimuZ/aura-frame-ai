// Root component: the application shell (sidebar + routed main content).
import { ChangeDetectionStrategy, Component, inject, OnInit } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { SidebarComponent } from './shared/components/sidebar/sidebar.component';
import { SettingsService } from './core/services/settings.service';
import { PhotoStore } from './core/services/photo-store.service';

@Component({
  selector: 'af-root',
  standalone: true,
  imports: [RouterOutlet, SidebarComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="af-app-shell">
      <af-sidebar />
      <main class="af-main-content">
        <router-outlet />
      </main>
    </div>
  `,
})
export class AppComponent implements OnInit {
  private readonly settings = inject(SettingsService);
  private readonly store = inject(PhotoStore);

  async ngOnInit(): Promise<void> {
    void this.settings.load();
    await this.store.initialize();
  }
}

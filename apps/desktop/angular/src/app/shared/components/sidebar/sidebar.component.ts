// Fixed application sidebar with local-profile management and primary navigation.
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { PhotoStore } from '../../../core/services/photo-store.service';

interface NavItem {
  label: string;
  path: string;
  icon: string;
}

type EditorMode = 'create' | 'rename' | 'delete' | null;

@Component({
  selector: 'af-sidebar',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="af-sidebar">
      <div class="af-sidebar-brand">
        <img class="af-sidebar-brand__logo" src="assets/logo.svg" alt="AuraFrame AI" />
        <div>
          <div class="af-sidebar-brand__title">AuraFrame AI</div>
          <div class="af-sidebar-brand__subtitle">Local Photo Curator</div>
        </div>
      </div>

      <section class="af-profile-switcher" aria-label="Local profile">
        <div class="af-profile-switcher__identity">
          <span class="af-profile-switcher__avatar" aria-hidden="true">{{ initial() }}</span>
          <div>
            <div class="af-profile-switcher__eyebrow">Active profile</div>
            <strong>{{ activeProfile()?.name ?? 'Loading…' }}</strong>
          </div>
        </div>

        <label class="af-sr-only" for="active-profile">Select active profile</label>
        <select
          id="active-profile"
          class="af-input af-profile-switcher__select"
          [value]="activeProfileId()"
          [disabled]="managementDisabled()"
          (change)="selectProfile($event)"
        >
          @for (profile of profiles(); track profile.id) {
            <option [value]="profile.id">{{ profile.name }}</option>
          }
        </select>

        <div class="af-profile-switcher__actions">
          <button class="af-icon-button" title="New profile" aria-label="New profile" [disabled]="managementDisabled()" (click)="openCreate()">＋</button>
          <button class="af-icon-button" title="Rename profile" aria-label="Rename profile" [disabled]="managementDisabled() || !activeProfile()" (click)="openRename()">✎</button>
          <button class="af-icon-button" title="Delete profile" aria-label="Delete profile" [disabled]="managementDisabled() || profiles().length <= 1" (click)="openDelete()">⌫</button>
        </div>

        @if (mode() === 'create' || mode() === 'rename') {
          <div class="af-profile-editor">
            <label class="af-field-label" for="profile-name">
              {{ mode() === 'create' ? 'New profile name' : 'Rename profile' }}
            </label>
            <input
              id="profile-name"
              class="af-input"
              maxlength="60"
              [value]="nameInput()"
              (input)="nameInput.set($any($event.target).value)"
              (keydown.enter)="submitName()"
            />
            <div class="af-actions">
              <button class="af-button-primary" [disabled]="!nameInput().trim() || busy()" (click)="submitName()">Save</button>
              <button class="af-button-secondary" [disabled]="busy()" (click)="closeEditor()">Cancel</button>
            </div>
          </div>
        } @else if (mode() === 'delete') {
          <div class="af-profile-editor">
            <p>Delete <strong>{{ activeProfile()?.name }}</strong> and all generated data?</p>
            <p class="af-field-hint">Original photos will never be deleted.</p>
            <div class="af-actions">
              <button class="af-button-danger" [disabled]="busy()" (click)="confirmDelete()">Confirm delete</button>
              <button class="af-button-secondary" [disabled]="busy()" (click)="closeEditor()">Cancel</button>
            </div>
          </div>
        }

        @if (error()) {
          <div class="af-profile-switcher__error" role="alert">{{ error() }}</div>
        }
        @if (status().running) {
          <div class="af-field-hint">Profile management is locked during analysis.</div>
        }
      </section>

      <nav class="af-sidebar-nav" aria-label="Primary navigation">
        @for (item of items; track item.path) {
          <a
            class="af-sidebar-item"
            [routerLink]="item.path"
            routerLinkActive="af-sidebar-item--active"
          >
            <span aria-hidden="true">{{ item.icon }}</span>
            <span>{{ item.label }}</span>
          </a>
        }
      </nav>
    </aside>
  `,
})
export class SidebarComponent {
  private readonly store = inject(PhotoStore);
  readonly profiles = this.store.profileService.profiles;
  readonly activeProfile = this.store.profileService.activeProfile;
  readonly activeProfileId = this.store.profileService.activeProfileId;
  readonly status = this.store.status;
  readonly mode = signal<EditorMode>(null);
  readonly nameInput = signal('');
  readonly error = signal('');
  readonly busy = signal(false);

  readonly items: NavItem[] = [
    { label: 'Dashboard', path: '/dashboard', icon: '◫' },
    { label: 'Import Photos', path: '/import', icon: '⤓' },
    { label: 'Analysis', path: '/analysis', icon: '◐' },
    { label: 'Review', path: '/review', icon: '▦' },
    { label: 'Profile Builder', path: '/profile-builder', icon: '★' },
    { label: 'Reports', path: '/reports', icon: '▤' },
    { label: 'Settings', path: '/settings', icon: '⚙' },
  ];

  managementDisabled(): boolean {
    return this.status().running || this.busy();
  }

  initial(): string {
    return this.activeProfile()?.name.trim().charAt(0).toLocaleUpperCase() || '•';
  }

  async selectProfile(event: Event): Promise<void> {
    const profileId = (event.target as HTMLSelectElement).value;
    await this.run(() => this.store.switchProfile(profileId));
  }

  openCreate(): void {
    this.error.set('');
    this.nameInput.set('');
    this.mode.set('create');
  }

  openRename(): void {
    this.error.set('');
    this.nameInput.set(this.activeProfile()?.name ?? '');
    this.mode.set('rename');
  }

  openDelete(): void {
    this.error.set('');
    this.mode.set('delete');
  }

  closeEditor(): void {
    this.mode.set(null);
    this.error.set('');
  }

  async submitName(): Promise<void> {
    const name = this.nameInput();
    const profileId = this.activeProfileId();
    const operation =
      this.mode() === 'create'
        ? () => this.store.createProfile(name)
        : () => this.store.renameProfile(profileId, name);
    const succeeded = await this.run(operation);
    if (succeeded) this.closeEditor();
  }

  async confirmDelete(): Promise<void> {
    const profileId = this.activeProfileId();
    const succeeded = await this.run(() => this.store.deleteProfile(profileId));
    if (succeeded) this.closeEditor();
  }

  private async run(operation: () => Promise<void>): Promise<boolean> {
    this.busy.set(true);
    this.error.set('');
    try {
      await operation();
      return true;
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      this.busy.set(false);
    }
  }
}

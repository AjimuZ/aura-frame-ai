// Fixed application sidebar with local-library management and primary navigation.
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

      <section class="af-library-switcher" aria-label="Local library">
        <div class="af-library-switcher__identity">
          <span class="af-library-switcher__avatar" aria-hidden="true">{{ initial() }}</span>
          <div>
            <div class="af-library-switcher__eyebrow">Active library</div>
            <strong>{{ activeLibrary()?.name ?? 'Loading…' }}</strong>
          </div>
        </div>

        <label class="af-sr-only" for="active-library">Select active library</label>
        <select
          id="active-library"
          class="af-input af-library-switcher__select"
          [value]="activeLibraryId()"
          [disabled]="managementDisabled()"
          (change)="selectLibrary($event)"
        >
          @for (library of libraries(); track library.id) {
            <option [value]="library.id">{{ library.name }}</option>
          }
        </select>

        <div class="af-library-switcher__actions">
          <button class="af-icon-button" title="New library" aria-label="New library" [disabled]="managementDisabled()" (click)="openCreate()">＋</button>
          <button class="af-icon-button" title="Rename library" aria-label="Rename library" [disabled]="managementDisabled() || !activeLibrary()" (click)="openRename()">✎</button>
          <button class="af-icon-button" title="Delete library" aria-label="Delete library" [disabled]="managementDisabled() || libraries().length <= 1" (click)="openDelete()">⌫</button>
        </div>

        @if (mode() === 'create' || mode() === 'rename') {
          <div class="af-library-editor">
            <label class="af-field-label" for="library-name">
              {{ mode() === 'create' ? 'New library name' : 'Rename library' }}
            </label>
            <input
              id="library-name"
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
          <div class="af-library-editor">
            <p>Delete <strong>{{ activeLibrary()?.name }}</strong> and all generated data?</p>
            <p class="af-field-hint">Original photos will never be deleted.</p>
            <div class="af-actions">
              <button class="af-button-danger" [disabled]="busy()" (click)="confirmDelete()">Confirm delete</button>
              <button class="af-button-secondary" [disabled]="busy()" (click)="closeEditor()">Cancel</button>
            </div>
          </div>
        }

        @if (error()) {
          <div class="af-library-switcher__error" role="alert">{{ error() }}</div>
        }
        @if (status().running) {
          <div class="af-field-hint">Library management is locked during analysis.</div>
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
  readonly libraries = this.store.libraryService.libraries;
  readonly activeLibrary = this.store.libraryService.activeLibrary;
  readonly activeLibraryId = this.store.libraryService.activeLibraryId;
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
    { label: 'Library Builder', path: '/library-builder', icon: '★' },
    { label: 'Reports', path: '/reports', icon: '▤' },
    { label: 'Settings', path: '/settings', icon: '⚙' },
  ];

  managementDisabled(): boolean {
    return this.status().running || this.busy();
  }

  initial(): string {
    return this.activeLibrary()?.name.trim().charAt(0).toLocaleUpperCase() || '•';
  }

  async selectLibrary(event: Event): Promise<void> {
    const libraryId = (event.target as HTMLSelectElement).value;
    await this.run(() => this.store.switchLibrary(libraryId));
  }

  openCreate(): void {
    this.error.set('');
    this.nameInput.set('');
    this.mode.set('create');
  }

  openRename(): void {
    this.error.set('');
    this.nameInput.set(this.activeLibrary()?.name ?? '');
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
    const libraryId = this.activeLibraryId();
    const operation =
      this.mode() === 'create'
        ? () => this.store.createLibrary(name)
        : () => this.store.renameLibrary(libraryId, name);
    const succeeded = await this.run(operation);
    if (succeeded) this.closeEditor();
  }

  async confirmDelete(): Promise<void> {
    const libraryId = this.activeLibraryId();
    const succeeded = await this.run(() => this.store.deleteLibrary(libraryId));
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

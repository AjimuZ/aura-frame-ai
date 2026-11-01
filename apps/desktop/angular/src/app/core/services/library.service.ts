import { computed, inject, Injectable, signal } from '@angular/core';
import { AURA_FRAME_API } from '../tokens/aura-frame-api.token';
import type { LibraryState, LocalLibrary } from '../models';

const EMPTY_STATE: LibraryState = { libraries: [], activeLibraryId: '' };

@Injectable({ providedIn: 'root' })
export class LibraryService {
  private readonly api = inject(AURA_FRAME_API);

  readonly state = signal<LibraryState>({ ...EMPTY_STATE });
  readonly libraries = computed(() => this.state().libraries);
  readonly activeLibraryId = computed(() => this.state().activeLibraryId);
  readonly activeLibrary = computed<LocalLibrary | null>(() => {
    const current = this.state();
    return current.libraries.find((library) => library.id === current.activeLibraryId) ?? null;
  });

  async load(): Promise<LibraryState> {
    return this.apply(await this.api.getLibraryState());
  }

  async create(name: string): Promise<LibraryState> {
    return this.apply(await this.api.createLibrary(name));
  }

  async rename(libraryId: string, name: string): Promise<LibraryState> {
    return this.apply(await this.api.renameLibrary(libraryId, name));
  }

  async activate(libraryId: string): Promise<LibraryState> {
    return this.apply(await this.api.activateLibrary(libraryId));
  }

  async delete(libraryId: string): Promise<LibraryState> {
    return this.apply(await this.api.deleteLibrary(libraryId));
  }

  setInputFolder(libraryId: string, inputFolder: string): void {
    this.state.update((state) => ({
      ...state,
      libraries: state.libraries.map((library) =>
        library.id === libraryId ? { ...library, inputFolder } : library,
      ),
    }));
  }

  private apply(state: LibraryState): LibraryState {
    const copy = {
      activeLibraryId: state.activeLibraryId,
      libraries: state.libraries.map((library) => ({ ...library })),
    };
    this.state.set(copy);
    return copy;
  }
}

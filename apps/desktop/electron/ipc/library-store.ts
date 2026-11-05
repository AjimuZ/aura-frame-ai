import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { LibraryState, LocalLibrary } from './contracts';

interface LibraryRegistry extends LibraryState {
  version: 1;
}

interface LibraryStoreOptions {
  now?: () => string;
  createId?: () => string;
  // Host folder behind the stack's `/exports` mount. Defaults to `exports` next to the registry.
  exportsPath?: string;
}

const DEFAULT_LIBRARY_NAME = 'My library';
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class LibraryRegistryStore {
  private readonly registryPath: string;
  private readonly librariesPath: string;
  private readonly exportsPath: string;
  private readonly now: () => string;
  private readonly createId: () => string;
  private registry: LibraryRegistry | null = null;

  constructor(
    private readonly rootPath: string,
    options: LibraryStoreOptions = {},
  ) {
    this.registryPath = path.join(rootPath, 'libraries.json');
    this.librariesPath = path.join(rootPath, 'libraries');
    this.exportsPath = path.resolve(options.exportsPath ?? path.join(rootPath, 'exports'));
    this.now = options.now ?? (() => new Date().toISOString());
    this.createId = options.createId ?? randomUUID;
  }

  initialize(): LibraryState {
    fs.mkdirSync(this.rootPath, { recursive: true });
    fs.mkdirSync(this.librariesPath, { recursive: true });

    if (fs.existsSync(this.registryPath)) {
      this.registry = this.readRegistry();
    } else {
      const timestamp = this.now();
      const first: LocalLibrary = {
        id: this.nextId(),
        name: DEFAULT_LIBRARY_NAME,
        inputFolder: null,
        codexOptIn: false,
        createdAt: timestamp,
        updatedAt: timestamp,
      };
      this.registry = {
        version: 1,
        libraries: [first],
        activeLibraryId: first.id,
      };
      this.writeRegistry(this.registry);
    }

    for (const library of this.registry.libraries) {
      fs.mkdirSync(this.libraryRoot(library.id), { recursive: true });
    }
    return this.getState();
  }

  getState(): LibraryState {
    const registry = this.requireRegistry();
    return {
      libraries: registry.libraries.map((library) => ({ ...library })),
      activeLibraryId: registry.activeLibraryId,
    };
  }

  getLibrary(libraryId: string): LocalLibrary {
    const library = this.requireRegistry().libraries.find((item) => item.id === libraryId);
    if (!library) {
      throw new Error('Unknown local library.');
    }
    return { ...library };
  }

  hasLibrary(libraryId: string): boolean {
    return this.requireRegistry().libraries.some((library) => library.id === libraryId);
  }

  createLibrary(name: string): LibraryState {
    const registry = this.requireRegistry();
    const safeName = this.validateName(name);
    const timestamp = this.now();
    const library: LocalLibrary = {
      id: this.nextId(),
      name: safeName,
      inputFolder: null,
      codexOptIn: false,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    const root = this.libraryRoot(library.id);
    fs.mkdirSync(root, { recursive: false });
    const next: LibraryRegistry = {
      ...registry,
      libraries: [...registry.libraries, library],
      activeLibraryId: library.id,
    };
    try {
      this.writeRegistry(next);
      this.registry = next;
    } catch (error) {
      fs.rmSync(root, { recursive: true, force: true });
      throw error;
    }
    return this.getState();
  }

  renameLibrary(libraryId: string, name: string): LibraryState {
    this.getLibrary(libraryId);
    const safeName = this.validateName(name, libraryId);
    const timestamp = this.now();
    return this.updateRegistry((registry) => ({
      ...registry,
      libraries: registry.libraries.map((library) =>
        library.id === libraryId ? { ...library, name: safeName, updatedAt: timestamp } : library,
      ),
    }));
  }

  activateLibrary(libraryId: string): LibraryState {
    this.getLibrary(libraryId);
    return this.updateRegistry((registry) => ({ ...registry, activeLibraryId: libraryId }));
  }

  setInputFolder(libraryId: string, inputFolder: string): LibraryState {
    this.getLibrary(libraryId);
    const timestamp = this.now();
    return this.updateRegistry((registry) => ({
      ...registry,
      libraries: registry.libraries.map((library) =>
        library.id === libraryId
          ? { ...library, inputFolder, updatedAt: timestamp }
          : library,
      ),
    }));
  }

  // Returns whether the library's anonymous copies may go to the optional cloud provider. Throws for an unknown
  // library. A library with no stored choice reads as off.
  getCodexOptIn(libraryId: string): boolean {
    return this.getLibrary(libraryId).codexOptIn;
  }

  // Stores the library's opt-in and returns the stored value. Only a real boolean is accepted: any other value
  // (a string, a number, null) throws and leaves the registry unchanged, so a truthy value can never turn it on.
  // Throws for an unknown library.
  setCodexOptIn(libraryId: string, enabled: boolean): boolean {
    this.getLibrary(libraryId);
    if (typeof enabled !== 'boolean') {
      throw new Error('The cloud provider opt-in must be true or false.');
    }
    const timestamp = this.now();
    this.updateRegistry((registry) => ({
      ...registry,
      libraries: registry.libraries.map((library) =>
        library.id === libraryId ? { ...library, codexOptIn: enabled, updatedAt: timestamp } : library,
      ),
    }));
    return enabled;
  }

  deleteLibrary(libraryId: string): LibraryState {
    const registry = this.requireRegistry();
    this.getLibrary(libraryId);
    if (registry.libraries.length <= 1) {
      throw new Error('The last local library cannot be deleted.');
    }

    const remaining = registry.libraries
      .filter((library) => library.id !== libraryId)
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const next: LibraryRegistry = {
      ...registry,
      libraries: remaining,
      activeLibraryId:
        registry.activeLibraryId === libraryId ? remaining[0].id : registry.activeLibraryId,
    };

    const root = this.libraryRoot(libraryId);
    const staged = path.join(this.librariesPath, `.deleting-${libraryId}`);
    if (fs.existsSync(staged)) {
      fs.rmSync(staged, { recursive: true, force: true });
    }
    if (fs.existsSync(root)) {
      fs.renameSync(root, staged);
    }
    try {
      this.writeRegistry(next);
      this.registry = next;
    } catch (error) {
      if (fs.existsSync(staged)) {
        fs.renameSync(staged, root);
      }
      throw error;
    }
    fs.rmSync(staged, { recursive: true, force: true });
    this.removeExports(libraryId);
    return this.getState();
  }

  libraryRoot(libraryId: string): string {
    if (!UUID_PATTERN.test(libraryId)) {
      throw new Error('Invalid local library id.');
    }
    const candidate = path.resolve(this.librariesPath, libraryId);
    if (!this.isInside(this.librariesPath, candidate)) {
      throw new Error('Invalid local library path.');
    }
    return candidate;
  }

  workingDir(libraryId: string): string {
    this.getLibrary(libraryId);
    return path.join(this.libraryRoot(libraryId), 'working');
  }

  // Returns the host folder behind the stack's `/exports` mount. It is the value main gives to ComposeStack.
  exportsRoot(): string {
    return this.exportsPath;
  }

  // Returns the host folder that holds this library's copies: the API writes `<exports>/<library id>/output`.
  outputDir(libraryId: string): string {
    return path.join(this.libraryExportsDir(libraryId), 'output');
  }

  // Returns the host folder that holds this library's reports: the engine writes `<exports>/<library id>/reports`.
  reportsDir(libraryId: string): string {
    return path.join(this.libraryExportsDir(libraryId), 'reports');
  }

  containsWorkingFile(libraryId: string, requestedPath: string): boolean {
    const root = path.resolve(this.workingDir(libraryId));
    const candidate = path.resolve(requestedPath);
    return this.isInside(root, candidate);
  }

  // Returns the resolved path when it lies inside the library's working tree, otherwise null.
  // Paths from any other location or library are rejected.
  resolveWorkingFile(libraryId: string, requestedPath: string): string | null {
    this.getLibrary(libraryId);
    const root = path.resolve(this.workingDir(libraryId));
    const candidate = path.resolve(requestedPath);
    return this.isInside(root, candidate) ? candidate : null;
  }

  // Returns `<exports>/<library id>`, the same layout as `library_exports_dir` in engine/api/storage.py.
  // Nothing is created: on Linux a folder made here by the person's account could not be written by the API user.
  private libraryExportsDir(libraryId: string): string {
    this.getLibrary(libraryId);
    this.libraryRoot(libraryId);
    return path.join(this.exportsPath, libraryId);
  }

  // Removes this library's copies and reports from the host once its registry entry is gone. A folder the
  // person's account may not remove is left in place: the library is already deleted from the registry.
  private removeExports(libraryId: string): void {
    try {
      fs.rmSync(path.join(this.exportsPath, libraryId), { recursive: true, force: true });
    } catch {
      // Nothing to report: the API's data removal has already cleared what it could.
    }
  }

  private validateName(name: string, currentId?: string): string {
    const safe = String(name ?? '').normalize('NFC').trim().replace(/\s+/gu, ' ');
    if (safe.length < 1 || safe.length > 60) {
      throw new Error('Library names must contain between 1 and 60 characters.');
    }
    const key = safe.normalize('NFKC').toLocaleLowerCase();
    const duplicate = this.requireRegistry().libraries.some(
      (library) =>
        library.id !== currentId && library.name.normalize('NFKC').toLocaleLowerCase() === key,
    );
    if (duplicate) {
      throw new Error('A local library with this name already exists.');
    }
    return safe;
  }

  private nextId(): string {
    const id = this.createId();
    if (!UUID_PATTERN.test(id)) {
      throw new Error('The library id generator returned an invalid UUID.');
    }
    return id;
  }

  private updateRegistry(transform: (registry: LibraryRegistry) => LibraryRegistry): LibraryState {
    const next = transform(this.requireRegistry());
    this.writeRegistry(next);
    this.registry = next;
    return this.getState();
  }

  private readRegistry(): LibraryRegistry {
    const parsed = JSON.parse(fs.readFileSync(this.registryPath, 'utf-8')) as LibraryRegistry;
    if (
      parsed.version !== 1 ||
      !Array.isArray(parsed.libraries) ||
      parsed.libraries.length === 0 ||
      !parsed.libraries.some((library) => library.id === parsed.activeLibraryId)
    ) {
      throw new Error('The local library registry is invalid.');
    }
    const ids = new Set<string>();
    const names = new Set<string>();
    for (const library of parsed.libraries) {
      if (!UUID_PATTERN.test(library.id) || ids.has(library.id)) {
        throw new Error('The local library registry contains an invalid library id.');
      }
      ids.add(library.id);
      const key = String(library.name).normalize('NFKC').toLocaleLowerCase();
      if (!library.name || names.has(key)) {
        throw new Error('The local library registry contains an invalid library name.');
      }
      names.add(key);
      // A registry written before the opt-in existed has no field, which reads as off. A field of any other type
      // is corruption, and it is refused rather than guessed at.
      if (library.codexOptIn === undefined) {
        library.codexOptIn = false;
      } else if (typeof library.codexOptIn !== 'boolean') {
        throw new Error('The local library registry contains an invalid cloud provider opt-in.');
      }
    }
    return parsed;
  }

  private writeRegistry(registry: LibraryRegistry): void {
    const temporary = `${this.registryPath}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(registry, null, 2), 'utf-8');
    fs.renameSync(temporary, this.registryPath);
  }

  private requireRegistry(): LibraryRegistry {
    if (!this.registry) {
      throw new Error('The local library store has not been initialized.');
    }
    return this.registry;
  }

  private isInside(root: string, candidate: string): boolean {
    const relative = path.relative(path.resolve(root), path.resolve(candidate));
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
  }
}

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
}

const DEFAULT_LIBRARY_NAME = 'My library';
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class LibraryRegistryStore {
  private readonly registryPath: string;
  private readonly librariesPath: string;
  private readonly now: () => string;
  private readonly createId: () => string;
  private registry: LibraryRegistry | null = null;

  constructor(
    private readonly rootPath: string,
    options: LibraryStoreOptions = {},
  ) {
    this.registryPath = path.join(rootPath, 'libraries.json');
    this.librariesPath = path.join(rootPath, 'libraries');
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

  outputDir(libraryId: string): string {
    this.getLibrary(libraryId);
    return path.join(this.libraryRoot(libraryId), 'output');
  }

  reportsDir(libraryId: string): string {
    this.getLibrary(libraryId);
    return path.join(this.libraryRoot(libraryId), 'reports');
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

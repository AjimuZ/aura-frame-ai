import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { LocalProfile, ProfileState } from './contracts';

interface ProfileRegistry extends ProfileState {
  version: 1;
  legacyMigration: 'pending' | 'complete';
  legacyProfileId: string;
}

interface ProfileStoreOptions {
  now?: () => string;
  createId?: () => string;
}

const LEGACY_DIRECTORIES = ['working', 'output', 'reports'] as const;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class ProfileRegistryStore {
  private readonly registryPath: string;
  private readonly profilesPath: string;
  private readonly now: () => string;
  private readonly createId: () => string;
  private registry: ProfileRegistry | null = null;

  constructor(
    private readonly rootPath: string,
    options: ProfileStoreOptions = {},
  ) {
    this.registryPath = path.join(rootPath, 'profiles.json');
    this.profilesPath = path.join(rootPath, 'profiles');
    this.now = options.now ?? (() => new Date().toISOString());
    this.createId = options.createId ?? randomUUID;
  }

  initialize(): ProfileState {
    fs.mkdirSync(this.rootPath, { recursive: true });
    fs.mkdirSync(this.profilesPath, { recursive: true });

    if (fs.existsSync(this.registryPath)) {
      this.registry = this.readRegistry();
      if (!this.registry.legacyProfileId) {
        this.registry = { ...this.registry, legacyProfileId: this.registry.profiles[0].id };
        this.writeRegistry(this.registry);
      }
    } else {
      const timestamp = this.now();
      const azim: LocalProfile = {
        id: this.nextId(),
        name: 'Azim',
        inputFolder: null,
        createdAt: timestamp,
        updatedAt: timestamp,
      };
      this.registry = {
        version: 1,
        legacyMigration: 'pending',
        legacyProfileId: azim.id,
        profiles: [azim],
        activeProfileId: azim.id,
      };
      this.writeRegistry(this.registry);
    }

    if (this.registry.legacyMigration === 'pending') {
      this.resumeLegacyMigration();
    }
    for (const profile of this.registry.profiles) {
      fs.mkdirSync(this.profileRoot(profile.id), { recursive: true });
    }
    return this.getState();
  }

  getState(): ProfileState {
    const registry = this.requireRegistry();
    return {
      profiles: registry.profiles.map((profile) => ({ ...profile })),
      activeProfileId: registry.activeProfileId,
    };
  }

  getProfile(profileId: string): LocalProfile {
    const profile = this.requireRegistry().profiles.find((item) => item.id === profileId);
    if (!profile) {
      throw new Error('Unknown local profile.');
    }
    return { ...profile };
  }

  hasProfile(profileId: string): boolean {
    return this.requireRegistry().profiles.some((profile) => profile.id === profileId);
  }

  createProfile(name: string): ProfileState {
    const registry = this.requireRegistry();
    const safeName = this.validateName(name);
    const timestamp = this.now();
    const profile: LocalProfile = {
      id: this.nextId(),
      name: safeName,
      inputFolder: null,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    const root = this.profileRoot(profile.id);
    fs.mkdirSync(root, { recursive: false });
    const next: ProfileRegistry = {
      ...registry,
      profiles: [...registry.profiles, profile],
      activeProfileId: profile.id,
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

  renameProfile(profileId: string, name: string): ProfileState {
    this.getProfile(profileId);
    const safeName = this.validateName(name, profileId);
    const timestamp = this.now();
    return this.updateRegistry((registry) => ({
      ...registry,
      profiles: registry.profiles.map((profile) =>
        profile.id === profileId ? { ...profile, name: safeName, updatedAt: timestamp } : profile,
      ),
    }));
  }

  activateProfile(profileId: string): ProfileState {
    this.getProfile(profileId);
    return this.updateRegistry((registry) => ({ ...registry, activeProfileId: profileId }));
  }

  setInputFolder(profileId: string, inputFolder: string): ProfileState {
    this.getProfile(profileId);
    const timestamp = this.now();
    return this.updateRegistry((registry) => ({
      ...registry,
      profiles: registry.profiles.map((profile) =>
        profile.id === profileId
          ? { ...profile, inputFolder, updatedAt: timestamp }
          : profile,
      ),
    }));
  }

  deleteProfile(profileId: string): ProfileState {
    const registry = this.requireRegistry();
    this.getProfile(profileId);
    if (registry.profiles.length <= 1) {
      throw new Error('The last local profile cannot be deleted.');
    }

    const remaining = registry.profiles
      .filter((profile) => profile.id !== profileId)
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const next: ProfileRegistry = {
      ...registry,
      profiles: remaining,
      activeProfileId:
        registry.activeProfileId === profileId ? remaining[0].id : registry.activeProfileId,
    };

    const root = this.profileRoot(profileId);
    const staged = path.join(this.profilesPath, `.deleting-${profileId}`);
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

  profileRoot(profileId: string): string {
    if (!UUID_PATTERN.test(profileId)) {
      throw new Error('Invalid local profile id.');
    }
    const candidate = path.resolve(this.profilesPath, profileId);
    if (!this.isInside(this.profilesPath, candidate)) {
      throw new Error('Invalid local profile path.');
    }
    return candidate;
  }

  workingDir(profileId: string): string {
    this.getProfile(profileId);
    return path.join(this.profileRoot(profileId), 'working');
  }

  outputDir(profileId: string): string {
    this.getProfile(profileId);
    return path.join(this.profileRoot(profileId), 'output');
  }

  reportsDir(profileId: string): string {
    this.getProfile(profileId);
    return path.join(this.profileRoot(profileId), 'reports');
  }

  containsWorkingFile(profileId: string, requestedPath: string): boolean {
    const root = path.resolve(this.workingDir(profileId));
    const candidate = path.resolve(requestedPath);
    return this.isInside(root, candidate);
  }

  // Maps thumbnail paths persisted before profile migration into Azim's new working tree.
  // Paths from any other location or profile remain rejected.
  resolveWorkingFile(profileId: string, requestedPath: string): string | null {
    this.getProfile(profileId);
    const currentRoot = path.resolve(this.workingDir(profileId));
    const candidate = path.resolve(requestedPath);
    if (this.isInside(currentRoot, candidate)) {
      return candidate;
    }

    const registry = this.requireRegistry();
    if (profileId !== registry.legacyProfileId) {
      return null;
    }
    const legacyRoot = path.resolve(this.rootPath, 'working');
    if (!this.isInside(legacyRoot, candidate)) {
      return null;
    }
    const remapped = path.resolve(currentRoot, path.relative(legacyRoot, candidate));
    return this.isInside(currentRoot, remapped) ? remapped : null;
  }

  private resumeLegacyMigration(): void {
    const registry = this.requireRegistry();
    const legacyProfile = registry.profiles[0];
    const profileRoot = this.profileRoot(legacyProfile.id);
    fs.mkdirSync(profileRoot, { recursive: true });

    for (const directory of LEGACY_DIRECTORIES) {
      const source = path.join(this.rootPath, directory);
      const destination = path.join(profileRoot, directory);
      if (!fs.existsSync(source)) {
        continue;
      }
      if (fs.existsSync(destination)) {
        const entries = fs.readdirSync(destination);
        if (entries.length > 0) {
          throw new Error(`Legacy migration conflict in ${directory}.`);
        }
        fs.rmdirSync(destination);
      }
      fs.renameSync(source, destination);
    }

    const complete: ProfileRegistry = { ...registry, legacyMigration: 'complete' };
    this.writeRegistry(complete);
    this.registry = complete;
  }

  private validateName(name: string, currentId?: string): string {
    const safe = String(name ?? '').normalize('NFC').trim().replace(/\s+/gu, ' ');
    if (safe.length < 1 || safe.length > 60) {
      throw new Error('Profile names must contain between 1 and 60 characters.');
    }
    const key = safe.normalize('NFKC').toLocaleLowerCase();
    const duplicate = this.requireRegistry().profiles.some(
      (profile) =>
        profile.id !== currentId && profile.name.normalize('NFKC').toLocaleLowerCase() === key,
    );
    if (duplicate) {
      throw new Error('A local profile with this name already exists.');
    }
    return safe;
  }

  private nextId(): string {
    const id = this.createId();
    if (!UUID_PATTERN.test(id)) {
      throw new Error('The profile id generator returned an invalid UUID.');
    }
    return id;
  }

  private updateRegistry(transform: (registry: ProfileRegistry) => ProfileRegistry): ProfileState {
    const next = transform(this.requireRegistry());
    this.writeRegistry(next);
    this.registry = next;
    return this.getState();
  }

  private readRegistry(): ProfileRegistry {
    const parsed = JSON.parse(fs.readFileSync(this.registryPath, 'utf-8')) as ProfileRegistry;
    if (
      parsed.version !== 1 ||
      !['pending', 'complete'].includes(parsed.legacyMigration) ||
      !Array.isArray(parsed.profiles) ||
      parsed.profiles.length === 0 ||
      !parsed.profiles.some((profile) => profile.id === parsed.activeProfileId)
    ) {
      throw new Error('The local profile registry is invalid.');
    }
    const ids = new Set<string>();
    const names = new Set<string>();
    for (const profile of parsed.profiles) {
      if (!UUID_PATTERN.test(profile.id) || ids.has(profile.id)) {
        throw new Error('The local profile registry contains an invalid profile id.');
      }
      ids.add(profile.id);
      const key = String(profile.name).normalize('NFKC').toLocaleLowerCase();
      if (!profile.name || names.has(key)) {
        throw new Error('The local profile registry contains an invalid profile name.');
      }
      names.add(key);
    }
    return parsed;
  }

  private writeRegistry(registry: ProfileRegistry): void {
    const temporary = `${this.registryPath}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(registry, null, 2), 'utf-8');
    fs.renameSync(temporary, this.registryPath);
  }

  private requireRegistry(): ProfileRegistry {
    if (!this.registry) {
      throw new Error('The local profile store has not been initialized.');
    }
    return this.registry;
  }

  private isInside(root: string, candidate: string): boolean {
    const relative = path.relative(path.resolve(root), path.resolve(candidate));
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
  }
}

import { computed, inject, Injectable, signal } from '@angular/core';
import { AURA_FRAME_API } from '../tokens/aura-frame-api.token';
import type { LocalProfile, ProfileState } from '../models';

const EMPTY_STATE: ProfileState = { profiles: [], activeProfileId: '' };

@Injectable({ providedIn: 'root' })
export class ProfileService {
  private readonly api = inject(AURA_FRAME_API);

  readonly state = signal<ProfileState>({ ...EMPTY_STATE });
  readonly profiles = computed(() => this.state().profiles);
  readonly activeProfileId = computed(() => this.state().activeProfileId);
  readonly activeProfile = computed<LocalProfile | null>(() => {
    const current = this.state();
    return current.profiles.find((profile) => profile.id === current.activeProfileId) ?? null;
  });

  async load(): Promise<ProfileState> {
    return this.apply(await this.api.getProfileState());
  }

  async create(name: string): Promise<ProfileState> {
    return this.apply(await this.api.createProfile(name));
  }

  async rename(profileId: string, name: string): Promise<ProfileState> {
    return this.apply(await this.api.renameProfile(profileId, name));
  }

  async activate(profileId: string): Promise<ProfileState> {
    return this.apply(await this.api.activateProfile(profileId));
  }

  async delete(profileId: string): Promise<ProfileState> {
    return this.apply(await this.api.deleteProfile(profileId));
  }

  setInputFolder(profileId: string, inputFolder: string): void {
    this.state.update((state) => ({
      ...state,
      profiles: state.profiles.map((profile) =>
        profile.id === profileId ? { ...profile, inputFolder } : profile,
      ),
    }));
  }

  private apply(state: ProfileState): ProfileState {
    const copy = {
      activeProfileId: state.activeProfileId,
      profiles: state.profiles.map((profile) => ({ ...profile })),
    };
    this.state.set(copy);
    return copy;
  }
}

export interface LocalProfile {
  id: string;
  name: string;
  inputFolder: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProfileState {
  profiles: LocalProfile[];
  activeProfileId: string;
}

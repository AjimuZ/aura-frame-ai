export interface LocalLibrary {
  id: string;
  name: string;
  inputFolder: string | null;
  // Per-library opt-in for the optional cloud provider; off by default and set only through `setCodexOptIn`.
  codexOptIn: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface LibraryState {
  libraries: LocalLibrary[];
  activeLibraryId: string;
}

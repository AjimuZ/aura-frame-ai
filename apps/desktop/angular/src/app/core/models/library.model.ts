export interface LocalLibrary {
  id: string;
  name: string;
  inputFolder: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LibraryState {
  libraries: LocalLibrary[];
  activeLibraryId: string;
}

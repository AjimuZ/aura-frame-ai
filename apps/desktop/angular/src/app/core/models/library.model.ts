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

// Most Codex calls one run may make, retries included, as the opt-in dialog states it. Mirrors the engine default
// (`ai.codex.max_calls` in engine/config.yaml, `CodexSettings.max_calls`); tests/codex-opt-in-dialog.test.cjs fails
// when they differ. The window cannot read the engine's configuration, so a changed value there is not shown here.
export const CODEX_MAX_CALLS_PER_RUN = 200;

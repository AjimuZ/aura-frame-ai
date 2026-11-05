import { ipcMain } from 'electron';
import { safeApiMessage } from '../api-client';
import type { ApiClient } from '../api-client';
import type { LibraryState } from './contracts';
import type { LibraryRegistryStore } from './library-store';

// What the handlers need from main. Injected, so a test can use a fake server.
export interface LibrariesBackend {
  api: Pick<ApiClient, 'clearData'>;
  // True only while this session's services are up. While false, nothing is sent to the API.
  servicesUp: () => boolean;
}

// Shown when a deletion is refused because the services that hold the library's stored data are not running.
const SERVICES_DOWN_MESSAGE =
  'The library was not deleted, because its stored data can only be removed while the analysis services run. ' +
  'Start an analysis, then delete the library again.';
const LAST_LIBRARY_MESSAGE = 'The last local library cannot be deleted.';

function assertIdle(isAnalysisRunning: () => boolean): void {
  if (isAnalysisRunning()) {
    throw new Error('Library management is unavailable while an analysis is running.');
  }
}

export function registerLibrariesIpc(
  libraries: LibraryRegistryStore,
  isAnalysisRunning: () => boolean,
  backend: LibrariesBackend,
): void {
  ipcMain.handle('libraries:get', (): LibraryState => libraries.getState());

  ipcMain.handle('libraries:create', (_event, name: string): LibraryState => {
    assertIdle(isAnalysisRunning);
    return libraries.createLibrary(name);
  });

  ipcMain.handle('libraries:rename', (_event, libraryId: string, name: string): LibraryState => {
    assertIdle(isAnalysisRunning);
    return libraries.renameLibrary(libraryId, name);
  });

  ipcMain.handle('libraries:activate', (_event, libraryId: string): LibraryState => {
    assertIdle(isAnalysisRunning);
    return libraries.activateLibrary(libraryId);
  });

  // Reads the library's opt-in for the optional cloud provider: false until the person turned it on.
  ipcMain.handle('libraries:getCodexOptIn', (_event, libraryId: string): boolean =>
    libraries.getCodexOptIn(libraryId),
  );

  // Stores the opt-in and answers with the stored value. The store refuses an unknown library and any value that
  // is not a boolean, so nothing but an explicit true or false reaches the registry. Refused during a run, because
  // that run already holds the value it started with.
  ipcMain.handle('libraries:setCodexOptIn', (_event, libraryId: string, enabled: boolean): boolean => {
    assertIdle(isAnalysisRunning);
    return libraries.setCodexOptIn(libraryId, enabled);
  });

  // Removes the library's data from the volume, then the library from the registry, so the data is never left
  // behind without an owner. While the services are down the deletion is refused with a fixed message and
  // nothing is sent or removed. If the API fails, the library stays registered.
  ipcMain.handle('libraries:delete', async (_event, libraryId: string): Promise<LibraryState> => {
    assertIdle(isAnalysisRunning);
    libraries.getLibrary(libraryId);
    if (libraries.getState().libraries.length <= 1) {
      throw new Error(LAST_LIBRARY_MESSAGE);
    }
    if (!backend.servicesUp()) {
      throw new Error(SERVICES_DOWN_MESSAGE);
    }
    try {
      await backend.api.clearData(libraryId);
    } catch (error) {
      throw new Error(safeApiMessage(error));
    }
    return libraries.deleteLibrary(libraryId);
  });
}

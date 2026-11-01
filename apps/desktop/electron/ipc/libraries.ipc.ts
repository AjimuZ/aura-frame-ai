import { ipcMain } from 'electron';
import type { LibraryState } from './contracts';
import type { LibraryRegistryStore } from './library-store';

function assertIdle(isAnalysisRunning: () => boolean): void {
  if (isAnalysisRunning()) {
    throw new Error('Library management is unavailable while an analysis is running.');
  }
}

export function registerLibrariesIpc(
  libraries: LibraryRegistryStore,
  isAnalysisRunning: () => boolean,
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

  ipcMain.handle('libraries:delete', (_event, libraryId: string): LibraryState => {
    assertIdle(isAnalysisRunning);
    return libraries.deleteLibrary(libraryId);
  });
}

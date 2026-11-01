// Filesystem IPC handlers: folder selection and opening the output folder.
// The renderer never touches the filesystem directly; these handlers validate and
// mediate every access. No personal data paths are read or enumerated here.

import { dialog, ipcMain, shell } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { LibraryRegistryStore } from './library-store';

interface IndexedPhoto {
  id: string;
  filePath: string;
  fileName: string;
}

function isIndexedPhoto(value: unknown): value is IndexedPhoto {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record['id'] === 'string' &&
    typeof record['filePath'] === 'string' &&
    typeof record['fileName'] === 'string'
  );
}

// Reads the trusted engine mirror and resolves a renderer-provided id to an indexed original.
function findPhoto(libraries: LibraryRegistryStore, libraryId: string, photoId: unknown): IndexedPhoto {
  if (typeof photoId !== 'string' || photoId.length === 0 || photoId.length > 200) {
    throw new Error('Invalid photo id.');
  }
  const mirrorPath = fs.realpathSync(path.join(libraries.workingDir(libraryId), 'photos.json'));
  const records: unknown = JSON.parse(fs.readFileSync(mirrorPath, 'utf-8'));
  const photo = Array.isArray(records)
    ? records.find((record) => isIndexedPhoto(record) && record.id === photoId)
    : undefined;
  if (!isIndexedPhoto(photo)) {
    throw new Error('Photo not found in the active library.');
  }
  const source = path.resolve(photo.filePath);
  if (!fs.existsSync(source) || !fs.statSync(source).isFile()) {
    throw new Error('The original photo is no longer available.');
  }
  return { ...photo, filePath: source };
}

function isSameFile(source: string, destination: string): boolean {
  const normalize = (value: string): string => {
    const resolved = path.resolve(value);
    return process.platform === 'win32' ? resolved.toLocaleLowerCase() : resolved;
  };
  if (normalize(source) === normalize(destination)) return true;
  if (!fs.existsSync(destination)) return false;
  const sourceStat = fs.statSync(source);
  const destinationStat = fs.statSync(destination);
  return sourceStat.dev === destinationStat.dev && sourceStat.ino === destinationStat.ino;
}

// Registers the filesystem-related IPC channels exactly once.
export function registerFilesystemIpc(libraries: LibraryRegistryStore): void {
  // Opens a native directory picker and returns the chosen folder, or null if cancelled.
  ipcMain.handle('fs:selectPhotoFolder', async (_event, libraryId: string): Promise<string | null> => {
    libraries.getLibrary(libraryId);
    const result = await dialog.showOpenDialog({
      title: 'Select a local photo folder',
      properties: ['openDirectory'],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    const folder = result.filePaths[0];
    libraries.setInputFolder(libraryId, folder);
    return folder;
  });

  // Copies any indexed photo (including review/rejected items) to a user-selected path.
  ipcMain.handle('export:photo', async (_event, libraryId: string, photoId: string): Promise<boolean> => {
    libraries.getLibrary(libraryId);
    const photo = findPhoto(libraries,libraryId, photoId);
    const exportDir = path.join(libraries.outputDir(libraryId), 'exported_photos');
    fs.mkdirSync(exportDir, { recursive: true });
    const safeName = path.basename(photo.fileName) || `photo${path.extname(photo.filePath)}`;
    const result = await dialog.showSaveDialog({
      title: 'Export photo',
      defaultPath: path.join(exportDir, safeName),
      buttonLabel: 'Export',
    });
    if (result.canceled || !result.filePath) {
      return false;
    }
    if (isSameFile(photo.filePath, result.filePath)) {
      throw new Error('Choose a destination different from the original photo.');
    }
    await fs.promises.copyFile(photo.filePath, result.filePath);
    return true;
  });

  // Opens the local output folder in the OS file explorer, creating it if needed.
  ipcMain.handle('fs:openOutputFolder', async (_event, libraryId: string): Promise<void> => {
    const dir = libraries.outputDir(libraryId);
    fs.mkdirSync(dir, { recursive: true });
    await shell.openPath(dir);
  });

  ipcMain.handle('fs:openReportsFolder', async (_event, libraryId: string): Promise<void> => {
    const dir = libraries.reportsDir(libraryId);
    fs.mkdirSync(dir, { recursive: true });
    await shell.openPath(dir);
  });
}

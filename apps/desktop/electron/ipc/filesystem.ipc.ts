// Filesystem IPC handlers: folder selection, single-photo export and opening the output folder.
// The renderer never touches the filesystem directly; these handlers validate and
// mediate every access. No personal data paths are read or enumerated here.

import { dialog, ipcMain, shell } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { safeApiMessage } from '../api-client';
import type { ApiClient } from '../api-client';
import type { LibraryRegistryStore } from './library-store';

// What the handlers need from main. Injected, so a test can use a fake server.
export interface FilesystemBackend {
  api: Pick<ApiClient, 'listPhotos'>;
  // True only while this session's services are up. While false, nothing is sent to the API.
  servicesUp: () => boolean;
}

interface IndexedPhoto {
  // The original on the host, resolved and checked to lie inside the library's photo folder.
  source: string;
  fileName: string;
}

// Where the API container mounts the library's photo folder. Stored paths start with it.
const CONTAINER_PHOTOS_PREFIX = '/photos/';

// Fixed messages. None of them may name a path, a folder or a token.
const SERVICES_DOWN_MESSAGE = 'The analysis services are not running. Start an analysis to export a photo.';
const NO_FOLDER_MESSAGE = 'This library has no photo folder.';
const NOT_FOUND_MESSAGE = 'Photo not found in the active library.';
const UNAVAILABLE_MESSAGE = 'The original photo is no longer available.';
const COPY_FAILED_MESSAGE = 'The photo could not be copied to the chosen destination.';
const OUTSIDE_MESSAGE ='The original photo is outside the library photo folder.';

// Returns true when `candidate` is strictly inside `root`. Both must already be resolved.
function isInside(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return (
    relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
  );
}

// Maps a stored container path to a file inside the library's host folder and returns its real path.
// Throws a fixed-message Error when the path is not under `/photos`, holds a dot or empty segment, a backslash or
// a NUL, does not exist as a file, or resolves outside the folder (a link or a case trick included).
function resolveOriginal(inputFolder: string, storedPath: string): string {
  if (!storedPath.startsWith(CONTAINER_PHOTOS_PREFIX)) throw new Error(OUTSIDE_MESSAGE);
  const segments = storedPath.slice(CONTAINER_PHOTOS_PREFIX.length).split('/');
  if (segments.some((part) => part === '' || part === '.' || part === '..' || /[\\\0]/.test(part))) {
    throw new Error(OUTSIDE_MESSAGE);
  }
  let root: string;
  let real: string;
  try {
    root = fs.realpathSync.native(inputFolder);
    real = fs.realpathSync.native(path.join(root, ...segments));
  } catch {
    throw new Error(UNAVAILABLE_MESSAGE);
  }
  if (!isInside(root, real)) throw new Error(OUTSIDE_MESSAGE);
  if (!fs.statSync(real).isFile()) throw new Error(UNAVAILABLE_MESSAGE);
  return real;
}

// Looks the photo up by ID in the API and maps its stored path to the original on the host.
// The stored path is read here and goes no further: no reply and no error message carries it.
async function findPhoto(
  backend: FilesystemBackend,
  libraries: LibraryRegistryStore,
  libraryId: string,
  photoId: unknown,
): Promise<IndexedPhoto> {
  if (typeof photoId !== 'string' || photoId.length === 0 || photoId.length > 200) {
    throw new Error('Invalid photo id.');
  }
  const { inputFolder } = libraries.getLibrary(libraryId);
  if (!inputFolder) throw new Error(NO_FOLDER_MESSAGE);
  if (!backend.servicesUp()) throw new Error(SERVICES_DOWN_MESSAGE);
  let records: Record<string, unknown>[];
  try {
    records = await backend.api.listPhotos(libraryId);
  } catch (error) {
    throw new Error(safeApiMessage(error));
  }
  const record = records.find((item) => item['id'] === photoId);
  const storedPath = record?.['filePath'];
  if (!record || typeof storedPath !== 'string') throw new Error(NOT_FOUND_MESSAGE);
  const source = resolveOriginal(inputFolder, storedPath);
  const storedName = record['fileName'];
  return { source, fileName: typeof storedName === 'string' ? storedName : path.basename(source) };
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
export function registerFilesystemIpc(libraries: LibraryRegistryStore, backend: FilesystemBackend): void {
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

  // Copies any indexed photo (including review/rejected items) to a user-selected path. Resolves to false when the
  // dialog is cancelled. Sends nothing while the services are down, and refuses with a fixed message then.
  ipcMain.handle('export:photo', async (_event, libraryId: string, photoId: string): Promise<boolean> => {
    const photo = await findPhoto(backend, libraries, libraryId, photoId);
    const exportDir = path.join(libraries.outputDir(libraryId), 'exported_photos');
    fs.mkdirSync(exportDir, { recursive: true });
    const safeName = path.basename(photo.fileName) || `photo${path.extname(photo.source)}`;
    const result = await dialog.showSaveDialog({
      title: 'Export photo',
      defaultPath: path.join(exportDir, safeName),
      buttonLabel: 'Export',
    });
    if (result.canceled || !result.filePath) {
      return false;
    }
    if (isSameFile(photo.source, result.filePath)) {
      throw new Error('Choose a destination different from the original photo.');
    }
    try {
      // COPYFILE_EXCL is not used: the save dialog already asked the person to confirm an overwrite.
      await fs.promises.copyFile(photo.source, result.filePath);
    } catch {
      // The system error names both paths, so it is replaced.
      throw new Error(COPY_FAILED_MESSAGE);
    }
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

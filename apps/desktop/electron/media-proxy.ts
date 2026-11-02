// Answers the `afmedia` scheme: the renderer names a library and a photo by ID, and main fetches that thumbnail
// from the API. No path and no token cross to the renderer, and no original can be asked for.

import type { ApiClient } from './api-client';
import type { LibraryRegistryStore } from './ipc/library-store';

// The same patterns as the API (engine/api/storage.py), so a bad ID never becomes a request.
const LIBRARY_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const PHOTO_ID_PATTERN = /^(?:[0-9a-f]{64}|IMG_[0-9A-F]{10})$/;
// A URL holds exactly these two parameters, so no variant or path can be smuggled in beside them.
const PARAMETER_COUNT = 2;

// What the handler needs from main. Injected, so a test can run it without Electron.
export interface MediaProxyBackend {
  api: Pick<ApiClient, 'getThumbnail'>;
  libraries: Pick<LibraryRegistryStore, 'hasLibrary'>;
}

function notFound(): Response {
  return new Response('Not found', { status: 404 });
}

// Returns the IDs of a well formed thumbnail request for a known library, or null for anything else.
function parseMediaRequest(
  libraries: MediaProxyBackend['libraries'],
  request: Request,
): { libraryId: string; photoId: string } | null {
  const url = new URL(request.url);
  const libraryId = url.searchParams.get('libraryId') ?? '';
  const photoId = url.searchParams.get('photoId') ?? '';
  const valid =
    request.method === 'GET' &&
    url.hostname === 'media' &&
    url.pathname === '/' &&
    [...url.searchParams].length === PARAMETER_COUNT &&
    LIBRARY_ID_PATTERN.test(libraryId) &&
    PHOTO_ID_PATTERN.test(photoId) &&
    libraries.hasLibrary(libraryId);
  return valid ? { libraryId, photoId } : null;
}

// Never rejects. Every failure, including services that are not up in this session, gives the same 404 as a missing
// thumbnail, because the renderer has nothing else to do with it. Only the image bytes and fixed headers are passed
// on, never an API header, and nothing is sent to the API when the client's gate is closed.
export async function handleMediaRequest(backend: MediaProxyBackend, request: Request): Promise<Response> {
  try {
    const ids = parseMediaRequest(backend.libraries, request);
    if (!ids) {
      return notFound();
    }
    const media = await backend.api.getThumbnail(ids.libraryId, ids.photoId);
    return new Response(new Uint8Array(media.bytes), {
      headers: {
        'content-type': media.contentType,
        'cache-control': 'private, no-store',
        'x-content-type-options': 'nosniff',
      },
    });
  } catch {
    return notFound();
  }
}

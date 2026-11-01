// Converts a photo ID into the URL that displays its thumbnail.
// In Electron, main answers the privileged `afmedia` scheme by asking the API for that photo,
// so the renderer holds no path and no token. Returns '' when either ID is missing.
import { Pipe, PipeTransform } from '@angular/core';

@Pipe({ name: 'fileUrl', standalone: true })
export class FileUrlPipe implements PipeTransform {
  transform(photoId: string | null | undefined, libraryId: string | null | undefined): string {
    if (!photoId || !libraryId) {
      return '';
    }
    return `afmedia://media/?libraryId=${encodeURIComponent(libraryId)}&photoId=${encodeURIComponent(photoId)}`;
  }
}

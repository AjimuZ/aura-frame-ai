// Converts a local thumbnail path into a URL the renderer can display.
// In Electron, local files are served via the privileged `afmedia` scheme (Chromium
// blocks raw file:// images on http(s) pages). Returns '' for missing paths.
import { Pipe, PipeTransform } from '@angular/core';

@Pipe({ name: 'fileUrl', standalone: true })
export class FileUrlPipe implements PipeTransform {
  transform(path: string | null | undefined, profileId: string | null | undefined): string {
    if (!path || !profileId) {
      return '';
    }
    if (/^(afmedia|https?|data|blob):/.test(path)) {
      return path;
    }
    const normalized = path.replace(/\\/g, '/');
    return `afmedia://media/?profileId=${encodeURIComponent(profileId)}&path=${encodeURIComponent(normalized)}`;
  }
}

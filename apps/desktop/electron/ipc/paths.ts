// Resolves the repository and generated-data paths for the Electron main process.
// Python never runs on the host: the engine runs in containers only (see docs/operations.md).
// compose.yaml lives in the repository root (repoRoot); host-side data lives under
// userData/AuraFrameAI so nothing is written into the repository or any protected
// personal-data location.

import { app } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

// Returns the repository root (parent of the compiled dist-electron directory).
export function repoRoot(): string {
  return path.join(__dirname, '..', '..');
}

// Returns the per-user generated-data root under Electron's userData directory.
export function dataRoot(): string {
  const root = path.join(app.getPath('userData'), 'AuraFrameAI');
  fs.mkdirSync(root, { recursive: true });
  return root;
}

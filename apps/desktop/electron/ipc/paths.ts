// Resolves repository, engine config, and generated-data paths for the Electron main process.
// Python never runs on the host: the engine runs in containers only (see docs/operations.md).
// The engine code lives next to the app (repoRoot); ALL generated data (DB, thumbnails,
// working files, output, reports) lives under userData/AuraFrameAI so nothing is written
// into the repository or any protected personal-data location.

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

// Returns the absolute path to the engine config file.
export function engineConfig(): string {
  return path.join(repoRoot(), 'engine', 'config.yaml');
}

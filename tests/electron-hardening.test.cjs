// Guard test: keeps the Electron window hardening, CSP and media scheme in the source, and the lockfile on the declared Electron.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const MAIN_PATH = path.join(REPO_ROOT, 'apps', 'desktop', 'electron', 'main.ts');
const INDEX_PATH = path.join(REPO_ROOT, 'apps', 'desktop', 'angular', 'src', 'index.html');

// Each protection, with the source text that proves it. These are read from source, not observed at run time.
const MAIN_REQUIREMENTS = [
  ['contextIsolation', /contextIsolation: true/],
  ['nodeIntegration disabled', /nodeIntegration: false/],
  ['sandbox', /sandbox: true/],
  ['new windows denied', /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/],
  ['navigation lock', /on\('will-navigate'[\s\S]*?event\.preventDefault\(\)/],
  ['afmedia scheme declared', /scheme: 'afmedia'/],
  ['afmedia cannot bypass the CSP', /bypassCSP: false/],
  ['afmedia handler', /protocol\.handle\('afmedia'/],
];
const INDEX_REQUIREMENTS = [
  ['CSP present', /http-equiv="Content-Security-Policy"/],
  ['CSP default-src', /default-src 'self'/],
  ['CSP script-src', /script-src 'self'/],
  ['CSP allows afmedia images', /img-src [^;]*afmedia:/],
];

// Returns the labels of the requirements the given source text does not satisfy; empty when all hold.
function missing(requirements, source) {
  return requirements.filter(([, pattern]) => !pattern.test(source)).map(([label]) => label);
}

test('the Electron window, CSP and media scheme stay hardened', () => {
  assert.deepEqual(missing(MAIN_REQUIREMENTS, fs.readFileSync(MAIN_PATH, 'utf8')), []);
  assert.deepEqual(missing(INDEX_REQUIREMENTS, fs.readFileSync(INDEX_PATH, 'utf8')), []);
});

test('a removed protection is reported by name', () => {
  const weakened = fs.readFileSync(MAIN_PATH, 'utf8').replace('sandbox: true', 'sandbox: false');
  assert.deepEqual(missing(MAIN_REQUIREMENTS, weakened), ['sandbox']);
});

test('the lockfile matches the declared Electron and electron-builder', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package-lock.json'), 'utf8'));
  for (const name of ['electron', 'electron-builder']) {
    const declared = manifest.devDependencies[name];
    assert.equal(lock.packages[''].devDependencies[name], declared);
    assert.equal(`^${lock.packages[`node_modules/${name}`].version}`.split('.')[0], declared.split('.')[0]);
  }
});

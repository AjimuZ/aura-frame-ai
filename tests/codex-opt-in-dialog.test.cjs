// Pins the Codex opt-in dialog's disclosure and its one call-cap source by reading the sources: the dialog states what
// is sent and what never is, states the cap it is given and both opt-ins a run needs, stores nothing itself, and the
// window's cap equals the engine default. The repository has no Angular component test runner, so what the dialog
// does in a window (opening, focus, Escape, saving) is not exercised here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const APP = path.join(REPO_ROOT, 'apps', 'desktop', 'angular', 'src', 'app');
const DIALOG = path.join(APP, 'shared', 'components', 'codex-opt-in-dialog', 'codex-opt-in-dialog.component.ts');
const LIBRARY_MODEL = path.join(APP, 'core', 'models', 'library.model.ts');
const LIBRARY_SERVICE = path.join(APP, 'core', 'services', 'library.service.ts');
const SETTINGS = path.join(APP, 'features', 'settings', 'settings.component.ts');
const ENGINE_CONFIG = path.join(REPO_ROOT, 'engine', 'config.yaml');
const CODEX_PROVIDER = path.join(REPO_ROOT, 'engine', 'src', 'providers', 'codex.py');

// Returns a file's text with LF endings, whatever the checkout converted them to.
function read(file) {
  return fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
}

// Returns the text a person reads in the dialog: its template without tags, whitespace collapsed to single spaces.
function dialogText() {
  const template = read(DIALOG).match(/template: `([\s\S]*?)`/);
  assert.ok(template, 'the dialog has an inline template');
  return template[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
}

// Returns the number a regular expression's first group captures in `text`, failing with `what` when it is absent.
function number(text, pattern, what) {
  const match = text.match(pattern);
  assert.ok(match, `${what} was not found`);
  return Number(match[1]);
}

test('the dialog states that only resized, EXIF-stripped anonymous copies are sent', () => {
  assert.match(dialogText(), /Only resized, EXIF-stripped, anonymous copies of the photos being judged\./);
});

test('the dialog states that originals, file names, paths and location are never sent', () => {
  assert.match(dialogText(), /Never the originals, file names, folder paths or location\./);
});

test('the dialog states the call cap it is given and both opt-ins a run needs', () => {
  const text = dialogText();
  assert.match(text, /At most \{\{ maxCalls\(\) \}\} Codex calls per run, retries included\./);
  assert.match(text, /this library allows Codex, and the app was started with \.\/start\.sh --provider codex --codex-opt-in/);
});

test('the call cap shown in the window equals the engine default', () => {
  const windowCap = number(read(LIBRARY_MODEL), /export const CODEX_MAX_CALLS_PER_RUN = (\d+);/, 'the window cap');
  const codexBlock = read(ENGINE_CONFIG).match(/\n {2}codex:\n((?: {4}.*\n|\s*#.*\n|\n)*)/);
  assert.ok(codexBlock, 'engine/config.yaml has an ai.codex block');
  const configCap = number(codexBlock[1], /^ {4}max_calls: (\d+)$/m, 'ai.codex.max_calls');
  const providerDefault = number(
    read(CODEX_PROVIDER),
    /class CodexSettings:[\s\S]*?\n {4}max_calls: int = (\d+)\n/,
    'CodexSettings.max_calls',
  );
  assert.equal(windowCap, configCap);
  assert.equal(windowCap, providerDefault);
});

test('the choice is stored only through the typed bridge, and the dialog stores nothing itself', () => {
  const dialog = read(DIALOG);
  assert.doesNotMatch(dialog, /AURA_FRAME_API|setCodexOptIn|localStorage|sessionStorage/);
  assert.match(read(LIBRARY_SERVICE), /await this\.api\.setCodexOptIn\(libraryId, enabled\)/);
  const settings = read(SETTINGS);
  assert.match(settings, /this\.store\.libraryService\.setCodexOptIn\(libraryId, enabled\)/);
  assert.match(settings, /readonly codexMaxCalls = CODEX_MAX_CALLS_PER_RUN;/);
  assert.match(settings, /\[maxCalls\]="codexMaxCalls"/);
});

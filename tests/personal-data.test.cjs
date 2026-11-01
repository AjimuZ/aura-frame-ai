// Guard test: keeps user-home paths and the owner's name out of every file the repository tracks or could track.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const SELF = path.relative(REPO_ROOT, __filename).split(path.sep).join('/');
const MIN_WORD_LENGTH = 4;

// Patterns for absolute user-home locations on Windows, macOS and Linux.
const HOME_PATH_PATTERNS = [
  /[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s*<>{}$%]/i,
  /(^|[^A-Za-z0-9_.-])\/Users\/[^/\s*<>{}$]/,
  /(^|[^A-Za-z0-9_.-])\/home\/[a-z][^/\s]*\//i,
];

// Returns the owner's name words of at least four letters from `git config user.name`, read at call time.
// An unset or unreadable name yields an empty list, so the name check is then a no-op rather than a failure.
function ownerNameWords() {
  let name = '';
  try {
    name = execFileSync('git', ['config', 'user.name'], { cwd: REPO_ROOT, encoding: 'utf8', shell: false });
  } catch {
    return [];
  }
  const words = name.split(/[^\p{L}]+/u).filter((word) => word.length >= MIN_WORD_LENGTH);
  return [...new Set(words.map((word) => word.toLowerCase()))];
}

// Splits text into lowercase word tokens on non-letters and camelCase boundaries.
function tokens(text) {
  return text
    .replace(/(\p{Ll})(\p{Lu})/gu, '$1 $2')
    .split(/[^\p{L}]+/u)
    .filter(Boolean)
    .map((token) => token.toLowerCase());
}

// Returns the 1-based numbers of lines holding a user-home path or one of the given name words.
// The copyright line of LICENSE is the only line allowed to carry a name.
function matchingLines(text, words, file = '') {
  const hits = [];
  const isLicense = path.basename(file) === 'LICENSE';
  text.split(/\r?\n/).forEach((line, index) => {
    const homePath = HOME_PATH_PATTERNS.some((pattern) => pattern.test(line));
    const allowedName = isLicense && /^\s*copyright\b/i.test(line);
    const found = allowedName ? [] : tokens(line);
    if (homePath || words.some((word) => found.includes(word))) {
      hits.push(index + 1);
    }
  });
  return hits;
}

// Returns "file:line" for each match in the given files; unreadable and binary files are skipped.
function scanFiles(root, files, words) {
  const violations = [];
  for (const file of files) {
    let buffer;
    try {
      buffer = fs.readFileSync(path.join(root, file));
    } catch {
      continue;
    }
    if (buffer.includes(0)) {
      continue;
    }
    for (const line of matchingLines(buffer.toString('utf8'), words, file)) {
      violations.push(`${file}:${line}`);
    }
  }
  return violations;
}

// Lists tracked and untracked-but-not-ignored files, relative to the repository root.
function repositoryFiles() {
  const output = execFileSync('git', ['ls-files', '-co', '--exclude-standard'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    maxBuffer: 256 * 1024 * 1024,
    shell: false,
  });
  return output.split('\n').filter(Boolean).filter((file) => file !== SELF);
}

test('the repository contains no user-home path and no owner name', () => {
  const violations = scanFiles(REPO_ROOT, repositoryFiles(), ownerNameWords());
  assert.deepEqual(violations, [], `Personal data found at:\n${violations.join('\n')}`);
});

test('the scan reports a planted home path and a planted name word', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-personal-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const sep = '\\';
  fs.writeFileSync(path.join(root, 'path.txt'), `ok\nroot = C:${sep}Users${sep}someone${sep}photos\n`);
  fs.writeFileSync(path.join(root, 'unix.txt'), 'dir = /home/someone/photos/\n');
  fs.writeFileSync(path.join(root, 'name.txt'), 'ok\nwritten by Samplename Q.\n');
  assert.deepEqual(scanFiles(root, ['path.txt'], []), ['path.txt:2']);
  assert.deepEqual(scanFiles(root, ['unix.txt'], []), ['unix.txt:1']);
  assert.deepEqual(scanFiles(root, ['name.txt'], ['samplename']), ['name.txt:2']);
});

test('only the LICENSE copyright line may carry a name', () => {
  const text = 'Copyright (c) 2026 Samplename\nAuthor: Samplename\n';
  assert.deepEqual(matchingLines(text, ['samplename'], 'LICENSE'), [2]);
  assert.deepEqual(matchingLines(text, ['samplename'], 'README.md'), [1, 2]);
});

test('short words and an empty name never match', () => {
  assert.deepEqual(matchingLines('Q. Doe is here', ['samplename']), []);
  assert.ok(ownerNameWords().every((word) => word.length >= MIN_WORD_LENGTH));
});

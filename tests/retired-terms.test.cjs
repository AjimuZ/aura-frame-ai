// Guard test: keeps the retired product vocabulary out of every file the repository tracks or could track.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

// The terms are stored encoded so that this file never contains them in plain text.
const ENCODED_TERMS = ['ZGF0aW5n'];
const TERMS = ENCODED_TERMS.map((term) => Buffer.from(term, 'base64').toString('utf8').toLowerCase());
const REPO_ROOT = path.resolve(__dirname, '..');
const SELF = path.relative(REPO_ROOT, __filename).split(path.sep).join('/');

// Splits text into lowercase word tokens on non-alphanumerics, underscores and camelCase boundaries,
// so identifiers match by whole word and words that merely contain the letters do not.
function tokens(text) {
  return text
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((token) => token.toLowerCase());
}

// Returns the 1-based numbers of the lines of `text` that contain a retired term as a whole token.
function matchingLines(text, terms = TERMS) {
  const hits = [];
  text.split(/\r?\n/).forEach((line, index) => {
    const found = tokens(line);
    if (terms.some((term) => found.includes(term))) {
      hits.push(index + 1);
    }
  });
  return hits;
}

// Returns "file:line" for each match in the given files; unreadable and binary files are skipped.
function scanFiles(root, files, terms = TERMS) {
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
    for (const line of matchingLines(buffer.toString('utf8'), terms)) {
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

test('the repository contains no retired vocabulary', () => {
  const violations = scanFiles(REPO_ROOT, repositoryFiles());
  assert.deepEqual(violations, [], `Retired terms found at:\n${violations.join('\n')}`);
});

test('the matcher catches a planted term and reports file and line', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-terms-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const term = TERMS[0];
  fs.writeFileSync(path.join(root, 'planted.txt'), `first line\nvalue = ${term}\n`);
  assert.deepEqual(scanFiles(root, ['planted.txt']), ['planted.txt:2']);
});

test('the matcher is identifier-aware', () => {
  const term = TERMS[0];
  const capital = term[0].toUpperCase() + term.slice(1);
  for (const sample of [`my_${term}_score`, `my${capital}Score`, term.toUpperCase(), `${capital}`, `a-${term}`]) {
    assert.deepEqual(matchingLines(sample), [1], sample);
  }
  for (const sample of ['updating the record', 'validating input', 'updatingState', 'validating_input']) {
    assert.deepEqual(matchingLines(sample), [], sample);
  }
});

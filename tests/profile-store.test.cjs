const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { LibraryRegistryStore } = require('../dist-electron/ipc/library-store.js');

const MAIN_ID = '11111111-1111-4111-8111-111111111111';
const SECOND_ID = '22222222-2222-4222-8222-222222222222';

function temporaryRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-libraries-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function deterministicStore(root, ids = [MAIN_ID, SECOND_ID]) {
  let index = 0;
  return new LibraryRegistryStore(root, {
    now: () => '2026-06-21T12:00:00.000Z',
    createId: () => ids[index++],
  });
}

test('initialization creates one default library and is idempotent', (t) => {
  const root = temporaryRoot(t);
  const first = deterministicStore(root, [MAIN_ID]);
  const state = first.initialize();
  assert.equal(state.libraries.length, 1);
  assert.equal(state.libraries[0].name, 'My library');
  assert.equal(state.activeLibraryId, MAIN_ID);
  for (const directory of ['working', 'output', 'reports']) {
    assert.equal(fs.existsSync(path.join(root, directory)), false);
  }
  assert.equal(fs.existsSync(path.join(root, 'libraries', MAIN_ID)), true);

  const second = deterministicStore(root);
  assert.deepEqual(second.initialize(), state);
  const registry = JSON.parse(fs.readFileSync(path.join(root, 'libraries.json'), 'utf8'));
  assert.deepEqual(Object.keys(registry).sort(), ['activeLibraryId', 'libraries', 'version']);
});

test('an invalid registry is rejected instead of being repaired', (t) => {
  const root = temporaryRoot(t);
  fs.writeFileSync(
    path.join(root, 'libraries.json'),
    JSON.stringify({ version: 1, libraries: [], activeLibraryId: MAIN_ID }),
  );
  assert.throws(() => deterministicStore(root).initialize(), /registry is invalid/i);
});

test('library lifecycle validates names, persists folders, and deletes only its own root', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  store.initialize();
  let state = store.createLibrary('  Summer   Trip  ');
  assert.equal(state.libraries[1].name, 'Summer Trip');
  assert.equal(state.activeLibraryId, SECOND_ID);
  assert.throws(() => store.renameLibrary(SECOND_ID, 'my LIBRARY'), /already exists/i);
  assert.throws(() => store.createLibrary(' '.repeat(3)), /between 1 and 60/i);

  store.setInputFolder(SECOND_ID, 'C:/Pictures/Trip');
  assert.equal(store.getLibrary(SECOND_ID).inputFolder, 'C:/Pictures/Trip');
  fs.writeFileSync(path.join(store.libraryRoot(MAIN_ID), 'main.txt'), 'safe');
  fs.writeFileSync(path.join(store.libraryRoot(SECOND_ID), 'second.txt'), 'delete');

  store.activateLibrary(MAIN_ID);
  state = store.deleteLibrary(MAIN_ID);
  assert.equal(state.activeLibraryId, SECOND_ID);
  assert.equal(fs.existsSync(path.join(root, 'libraries', MAIN_ID)), false);
  assert.equal(fs.readFileSync(path.join(root, 'libraries', SECOND_ID, 'second.txt'), 'utf8'), 'delete');
  assert.throws(() => store.deleteLibrary(SECOND_ID), /last local library/i);
});

test('the cloud provider opt-in is off for new libraries and persists across a reload of libraries.json', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  const initial = store.initialize();
  assert.equal(initial.libraries[0].codexOptIn, false);
  assert.equal(store.createLibrary('Second').libraries[1].codexOptIn, false);
  assert.equal(store.getCodexOptIn(MAIN_ID), false);

  assert.equal(store.setCodexOptIn(SECOND_ID, true), true);
  assert.equal(store.getCodexOptIn(SECOND_ID), true);
  assert.equal(store.getCodexOptIn(MAIN_ID), false);

  const stored = JSON.parse(fs.readFileSync(path.join(root, 'libraries.json'), 'utf8'));
  assert.deepEqual(
    stored.libraries.map((library) => [library.id, library.codexOptIn]),
    [[MAIN_ID, false], [SECOND_ID, true]],
  );

  const reloaded = deterministicStore(root);
  reloaded.initialize();
  assert.equal(reloaded.getCodexOptIn(SECOND_ID), true);
  assert.equal(reloaded.getCodexOptIn(MAIN_ID), false);

  assert.equal(reloaded.setCodexOptIn(SECOND_ID, false), false);
  const again = deterministicStore(root);
  again.initialize();
  assert.equal(again.getCodexOptIn(SECOND_ID), false);
});

test('a registry written before the opt-in existed reads as off and is not rejected', (t) => {
  const root = temporaryRoot(t);
  const legacy = {
    version: 1,
    activeLibraryId: MAIN_ID,
    libraries: [
      { id: MAIN_ID, name: 'Old', inputFolder: null, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z' },
    ],
  };
  fs.writeFileSync(path.join(root, 'libraries.json'), JSON.stringify(legacy));
  const store = deterministicStore(root);
  const state = store.initialize();
  assert.equal(state.libraries[0].codexOptIn, false);
  assert.equal(store.getCodexOptIn(MAIN_ID), false);
  store.setCodexOptIn(MAIN_ID, true);
  const reloaded = deterministicStore(root);
  reloaded.initialize();
  assert.equal(reloaded.getCodexOptIn(MAIN_ID), true);
});

test('the opt-in setter accepts only booleans for known libraries and leaves the registry unchanged otherwise', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  store.initialize();
  const before = fs.readFileSync(path.join(root, 'libraries.json'), 'utf8');
  for (const value of ['true', 'false', 1, 0, null, undefined, {}, []]) {
    assert.throws(() => store.setCodexOptIn(MAIN_ID, value), /must be true or false/i, String(value));
  }
  assert.throws(() => store.setCodexOptIn('33333333-3333-4333-8333-333333333333', true), /unknown local library/i);
  assert.throws(() => store.getCodexOptIn('../escape'), /unknown local library/i);
  assert.equal(fs.readFileSync(path.join(root, 'libraries.json'), 'utf8'), before);
  assert.equal(store.getCodexOptIn(MAIN_ID), false);
});

test('a registry whose stored opt-in is not a boolean is rejected instead of being read as on or off', (t) => {
  for (const value of ['true', 1, null, {}]) {
    const root = temporaryRoot(t);
    const registry = {
      version: 1,
      activeLibraryId: MAIN_ID,
      libraries: [
        {
          id: MAIN_ID,
          name: 'Tampered',
          inputFolder: null,
          codexOptIn: value,
          createdAt: '2026-01-01T00:00:00.000Z',
          updatedAt: '2026-01-01T00:00:00.000Z',
        },
      ],
    };
    fs.writeFileSync(path.join(root, 'libraries.json'), JSON.stringify(registry));
    assert.throws(() => deterministicStore(root).initialize(), /invalid cloud provider opt-in/i, String(value));
  }
});

test('workspace resolution rejects unknown ids and cross-library paths', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  store.initialize();
  store.createLibrary('Second');
  const mainThumb = path.join(store.workingDir(MAIN_ID), 'thumbs', 'a.jpg');
  const secondThumb = path.join(store.workingDir(SECOND_ID), 'thumbs', 'b.jpg');
  assert.equal(store.containsWorkingFile(MAIN_ID, mainThumb), true);
  assert.equal(store.containsWorkingFile(MAIN_ID, secondThumb), false);
  assert.equal(store.resolveWorkingFile(MAIN_ID, mainThumb), mainThumb);
  assert.equal(store.resolveWorkingFile(MAIN_ID, secondThumb), null);
  const outsideThumb = path.join(root, 'working', 'thumbs', 'a.jpg');
  assert.equal(store.resolveWorkingFile(MAIN_ID, outsideThumb), null);
  assert.equal(store.containsWorkingFile(MAIN_ID, path.join(store.workingDir(MAIN_ID), '..', 'output', 'x.jpg')), false);
  assert.throws(() => store.libraryRoot('../../escape'), /invalid local library id/i);
  assert.throws(() => store.workingDir('33333333-3333-4333-8333-333333333333'), /unknown local library/i);
});

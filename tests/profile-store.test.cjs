const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { ProfileRegistryStore } = require('../dist-electron/ipc/profile-store.js');

const AZIM_ID = '11111111-1111-4111-8111-111111111111';
const FRIEND_ID = '22222222-2222-4222-8222-222222222222';

function temporaryRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'auraframe-profiles-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function deterministicStore(root, ids = [AZIM_ID, FRIEND_ID]) {
  let index = 0;
  return new ProfileRegistryStore(root, {
    now: () => '2026-06-21T12:00:00.000Z',
    createId: () => ids[index++],
  });
}

test('initialization migrates legacy data into Azim and is idempotent', (t) => {
  const root = temporaryRoot(t);
  for (const directory of ['working', 'output', 'reports']) {
    fs.mkdirSync(path.join(root, directory), { recursive: true });
    fs.writeFileSync(path.join(root, directory, `${directory}.txt`), directory);
  }

  const first = deterministicStore(root, [AZIM_ID]);
  const state = first.initialize();
  assert.equal(state.profiles.length, 1);
  assert.equal(state.profiles[0].name, 'Azim');
  assert.equal(state.activeProfileId, AZIM_ID);
  for (const directory of ['working', 'output', 'reports']) {
    assert.equal(fs.existsSync(path.join(root, directory)), false);
    assert.equal(
      fs.readFileSync(path.join(root, 'profiles', AZIM_ID, directory, `${directory}.txt`), 'utf8'),
      directory,
    );
  }
  const legacyThumbPath = path.join(root, 'working', 'thumbs', 'photo.jpg');
  const migratedThumbPath = path.join(root, 'profiles', AZIM_ID, 'working', 'thumbs', 'photo.jpg');
  assert.equal(first.resolveWorkingFile(AZIM_ID, legacyThumbPath), migratedThumbPath);

  const second = deterministicStore(root);
  assert.deepEqual(second.initialize(), state);
  const registry = JSON.parse(fs.readFileSync(path.join(root, 'profiles.json'), 'utf8'));
  assert.equal(registry.legacyMigration, 'complete');
});

test('a pending partial migration resumes without overwriting migrated data', (t) => {
  const root = temporaryRoot(t);
  const profileRoot = path.join(root, 'profiles', AZIM_ID);
  fs.mkdirSync(path.join(profileRoot, 'working'), { recursive: true });
  fs.writeFileSync(path.join(profileRoot, 'working', 'photos.json'), '[]');
  fs.mkdirSync(path.join(root, 'output'), { recursive: true });
  fs.writeFileSync(path.join(root, 'output', 'result.txt'), 'kept');
  const timestamp = '2026-06-21T12:00:00.000Z';
  fs.writeFileSync(
    path.join(root, 'profiles.json'),
    JSON.stringify({
      version: 1,
      legacyMigration: 'pending',
      legacyProfileId: AZIM_ID,
      activeProfileId: AZIM_ID,
      profiles: [{ id: AZIM_ID, name: 'Azim', inputFolder: null, createdAt: timestamp, updatedAt: timestamp }],
    }),
  );

  const store = deterministicStore(root);
  store.initialize();
  assert.equal(fs.readFileSync(path.join(profileRoot, 'working', 'photos.json'), 'utf8'), '[]');
  assert.equal(fs.readFileSync(path.join(profileRoot, 'output', 'result.txt'), 'utf8'), 'kept');
});

test('profile lifecycle validates names, persists folders, and deletes only its own root', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  store.initialize();
  let state = store.createProfile('  Mario   Rossi  ');
  assert.equal(state.profiles[1].name, 'Mario Rossi');
  assert.equal(state.activeProfileId, FRIEND_ID);
  assert.throws(() => store.renameProfile(FRIEND_ID, 'azim'), /already exists/i);
  assert.throws(() => store.createProfile(' '.repeat(3)), /between 1 and 60/i);

  store.setInputFolder(FRIEND_ID, 'C:/Pictures/Mario');
  assert.equal(store.getProfile(FRIEND_ID).inputFolder, 'C:/Pictures/Mario');
  fs.writeFileSync(path.join(store.profileRoot(AZIM_ID), 'azim.txt'), 'safe');
  fs.writeFileSync(path.join(store.profileRoot(FRIEND_ID), 'friend.txt'), 'delete');

  store.activateProfile(AZIM_ID);
  state = store.deleteProfile(AZIM_ID);
  assert.equal(state.activeProfileId, FRIEND_ID);
  assert.equal(fs.existsSync(path.join(root, 'profiles', AZIM_ID)), false);
  assert.equal(fs.readFileSync(path.join(root, 'profiles', FRIEND_ID, 'friend.txt'), 'utf8'), 'delete');
  assert.throws(() => store.deleteProfile(FRIEND_ID), /last local profile/i);
});

test('workspace resolution rejects unknown ids and cross-profile paths', (t) => {
  const root = temporaryRoot(t);
  const store = deterministicStore(root);
  store.initialize();
  store.createProfile('Friend');
  const azimThumb = path.join(store.workingDir(AZIM_ID), 'thumbs', 'a.jpg');
  const friendThumb = path.join(store.workingDir(FRIEND_ID), 'thumbs', 'b.jpg');
  assert.equal(store.containsWorkingFile(AZIM_ID, azimThumb), true);
  assert.equal(store.containsWorkingFile(AZIM_ID, friendThumb), false);
  const oldAzimThumb = path.join(root, 'working', 'thumbs', 'a.jpg');
  assert.equal(store.resolveWorkingFile(AZIM_ID, oldAzimThumb), azimThumb);
  assert.equal(store.resolveWorkingFile(FRIEND_ID, oldAzimThumb), null);
  assert.equal(store.containsWorkingFile(AZIM_ID, path.join(store.workingDir(AZIM_ID), '..', 'output', 'x.jpg')), false);
  assert.throws(() => store.profileRoot('../../escape'), /invalid local profile id/i);
  assert.throws(() => store.workingDir('33333333-3333-4333-8333-333333333333'), /unknown local profile/i);
});

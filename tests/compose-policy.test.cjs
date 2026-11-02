// Guard test: the compose policy names each weakened protection, and the repository's Dockerfiles and build context comply.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const { findViolations, unpinnedBaseImages } = require('../scripts/compose-policy.cjs');

const REPO_ROOT = path.resolve(__dirname, '..');
const PINNED = `@sha256:${'a'.repeat(64)}`;
const PINNED_DOCKERFILE = `FROM base:1${PINNED} AS core\nFROM core AS full\n`;

// Returns the hardening fields every service must carry, for the given user.
function hardened(user) {
  return { user, read_only: true, cap_drop: ['ALL'], security_opt: ['no-new-privileges:true'] };
}

// Returns a complying configuration in the shape of `docker compose config --format json`.
function compliantConfig() {
  const models = { type: 'volume', source: 'models', target: '/models', read_only: true };
  return {
    networks: { frontend: {}, backend: { internal: true }, egress: {} },
    services: {
      gateway: {
        ...hardened('101:101'),
        build: { context: 'ctx' },
        networks: { frontend: null, backend: null },
        ports: [{ host_ip: '127.0.0.1', published: '8420', target: 8080 }],
      },
      api: {
        ...hardened('10001:10001'),
        build: { context: 'ctx', dockerfile: 'api.Dockerfile' },
        networks: { backend: null },
        volumes: [
          { type: 'bind', source: 'chosen', target: '/photos', read_only: true },
          { type: 'volume', source: 'data', target: '/data' },
          models,
        ],
      },
      vision: {
        ...hardened('10001:10001'),
        image: `server${PINNED}`,
        networks: { backend: null },
        volumes: [models],
      },
      'fetch-models': {
        ...hardened('10001:10001'),
        build: { context: 'ctx', dockerfile: 'fetch.Dockerfile' },
        profiles: ['fetch'],
        networks: { egress: null },
        volumes: [{ type: 'volume', source: 'models', target: '/models' }],
      },
    },
  };
}

// Applies one weakening to a fresh complying configuration and returns its violations.
function violationsAfter(weaken) {
  const config = compliantConfig();
  weaken(config);
  return findViolations(config, () => PINNED_DOCKERFILE);
}

test('a complying configuration has no violation', () => {
  assert.deepEqual(violationsAfter(() => {}), []);
});

const WEAKENINGS = [
  ['a missing service', (c) => delete c.services.vision, 'vision: service is missing'],
  ['a backend with an internet route', (c) => (c.networks.backend.internal = false), 'network backend: must be internal'],
  [
    'a port on a service other than gateway',
    (c) => (c.services.api.ports = [{ host_ip: '127.0.0.1', published: '8000', target: 8000 }]),
    'api: publishes a port; only gateway may',
  ],
  [
    'a port on every interface',
    (c) => (c.services.gateway.ports[0].host_ip = '0.0.0.0'),
    'gateway: port 8420 is not bound to 127.0.0.1',
  ],
  [
    'an isolated service on a routed network',
    (c) => (c.services.vision.networks.frontend = null),
    'vision: joins network frontend, which is not internal',
  ],
  ['a tag without digest', (c) => (c.services.vision.image = 'server:latest'), 'vision: image not pinned by digest: server:latest'],
  ['a root user', (c) => (c.services.api.user = '0:0'), 'api: must run as a numeric non-root user'],
  ['no user', (c) => delete c.services.vision.user, 'vision: must run as a numeric non-root user'],
  ['privilege escalation', (c) => (c.services.api.security_opt = []), 'api: no-new-privileges is missing'],
  ['kept capabilities', (c) => (c.services.gateway.cap_drop = ['NET_RAW']), 'gateway: capabilities are not all dropped'],
  ['added capabilities', (c) => (c.services.vision.cap_add = ['SYS_ADMIN']), 'vision: adds capabilities'],
  ['privileged mode', (c) => (c.services.api.privileged = true), 'api: is privileged'],
  ['a writable root filesystem', (c) => (c.services.api.read_only = false), 'api: root filesystem is writable'],
  ['the host network', (c) => (c.services.api.network_mode = 'host'), 'api: uses the host network_mode'],
  [
    'the Docker socket',
    (c) => c.services.api.volumes.push({ type: 'bind', source: '/var/run/docker.sock', target: '/run/d' }),
    'api: mounts the Docker socket',
  ],
  ['writable photos', (c) => delete c.services.api.volumes[0].read_only, 'api: /photos must be a read-only bind mount'],
  [
    'data in a bind mount',
    (c) => (c.services.api.volumes[1].type = 'bind'),
    'api: /data must be the writable named volume data',
  ],
  [
    'writable models',
    (c) => (c.services.vision.volumes = [{ type: 'volume', source: 'models', target: '/models' }]),
    'vision: /models must be the named volume models, read-only',
  ],
  [
    'photos reaching vision',
    (c) => c.services.vision.volumes.push({ type: 'bind', source: 'chosen', target: '/photos', read_only: true }),
    'vision: must not mount the photos',
  ],
  ['a missing download service', (c) => delete c.services['fetch-models'], 'fetch-models: service is missing'],
  [
    'the download service on the backend',
    (c) => (c.services['fetch-models'].networks.backend = null),
    'fetch-models: must join the egress network only',
  ],
  [
    'the download service without a profile',
    (c) => delete c.services['fetch-models'].profiles,
    'fetch-models: must sit behind a profile, so that no plain up starts it',
  ],
  [
    'photos reaching the download service',
    (c) => c.services['fetch-models'].volumes.push({ type: 'bind', source: 'chosen', target: '/photos', read_only: true }),
    'fetch-models: may mount the models volume only',
  ],
  [
    'the data volume in the download service',
    (c) => c.services['fetch-models'].volumes.push({ type: 'volume', source: 'data', target: '/data' }),
    'fetch-models: may mount the models volume only',
  ],
  [
    'read-only models in the download service',
    (c) => (c.services['fetch-models'].volumes[0].read_only = true),
    'fetch-models: /models must be the named volume models, writable',
  ],
  [
    'the download service as root',
    (c) => (c.services['fetch-models'].user = '0'),
    'fetch-models: must run as a numeric non-root user',
  ],
  [
    'an isolated service on the egress network',
    (c) => (c.services.api.networks.egress = null),
    'api: joins network egress, which is not internal',
  ],
  [
    'another service on the egress network',
    (c) => (c.services.gateway.networks.egress = null),
    'gateway: joins network egress, which has an internet route',
  ],
  [
    'the download service on the frontend',
    (c) => (c.services['fetch-models'].networks = { frontend: null }),
    'fetch-models: joins network frontend, which has an internet route',
  ],
  [
    'a service on the default network',
    (c) => (c.services['fetch-models'].networks = { default: null }),
    'fetch-models: joins network default, which has an internet route',
  ],
];

for (const [label, weaken, expected] of WEAKENINGS) {
  test(`the policy reports ${label}`, () => {
    assert.ok(violationsAfter(weaken).includes(expected), `expected: ${expected}`);
  });
}

test('an unpinned base image in a built service is reported', () => {
  const violations = findViolations(compliantConfig(), () => 'FROM python:3.12-slim\n');
  assert.ok(violations.includes('api: base image not pinned by digest: python:3.12-slim'));
});

test('a later stage may build on an earlier stage, but an unknown name must be pinned', () => {
  assert.deepEqual(unpinnedBaseImages(PINNED_DOCKERFILE), []);
  assert.deepEqual(unpinnedBaseImages(`FROM base${PINNED} AS one\nFROM two AS three\n`), ['two']);
});

test('every Dockerfile in the repository pins its base images', () => {
  for (const service of ['api', 'gateway', 'fetch-models']) {
    const text = fs.readFileSync(path.join(REPO_ROOT, 'docker', service, 'Dockerfile'), 'utf8');
    assert.match(text, /^FROM /m);
    assert.deepEqual(unpinnedBaseImages(text), [], service);
  }
});

test('the api build context starts by excluding everything, so personal folders never reach the daemon', () => {
  const rules = fs
    .readFileSync(path.join(REPO_ROOT, '.dockerignore'), 'utf8')
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith('#'));
  assert.equal(rules[0], '*');
  assert.deepEqual(rules.filter((rule) => /img|data/.test(rule)), []);
});

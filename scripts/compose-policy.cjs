// Static hardening policy for the container stack: reads `docker compose config --format json` and names every violation.
//
// Usage: docker compose --profile local config --format json | node scripts/compose-policy.cjs
// Exits 0 when the configuration complies, 1 with one line per violation otherwise, 2 for unreadable input.
const fs = require('node:fs');
const path = require('node:path');

const REQUIRED_SERVICES = ['gateway', 'api', 'vision'];
// Services that handle photos or their copies: they may join internal networks only.
const ISOLATED_SERVICES = ['api', 'vision'];
const PUBLISHING_SERVICE = 'gateway';
const LOOPBACK = '127.0.0.1';
const DIGEST_PATTERN = /@sha256:[0-9a-f]{64}$/;
const NON_ROOT_USER_PATTERN = /^[1-9][0-9]*(:[1-9][0-9]*)?$/;
const DOCKER_SOCKET_PATTERN = /docker(\.sock|_engine)/i;

// Returns the base images of a Dockerfile that are not pinned by digest; a stage built from an earlier stage is fine.
function unpinnedBaseImages(dockerfileText) {
  const stages = new Set();
  const unpinned = [];
  for (const line of dockerfileText.split(/\r?\n/)) {
    const match = /^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?/i.exec(line);
    if (!match) {
      continue;
    }
    const [, image, stage] = match;
    if (!stages.has(image.toLowerCase()) && !DIGEST_PATTERN.test(image)) {
      unpinned.push(image);
    }
    if (stage) {
      stages.add(stage.toLowerCase());
    }
  }
  return unpinned;
}

// Returns the violations of the image rule for one service. `readFile` reads the Dockerfile of a built service.
function imageViolations(name, service, readFile) {
  if (service.build) {
    const dockerfile = path.resolve(service.build.context, service.build.dockerfile || 'Dockerfile');
    return unpinnedBaseImages(readFile(dockerfile)).map((image) => `${name}: base image not pinned by digest: ${image}`);
  }
  return DIGEST_PATTERN.test(service.image || '') ? [] : [`${name}: image not pinned by digest: ${service.image}`];
}

// Returns the violations of the privilege rules for one service.
function privilegeViolations(name, service) {
  const violations = [];
  if (!NON_ROOT_USER_PATTERN.test(String(service.user || ''))) {
    violations.push(`${name}: must run as a numeric non-root user`);
  }
  if (!(service.security_opt || []).includes('no-new-privileges:true')) {
    violations.push(`${name}: no-new-privileges is missing`);
  }
  if (!(service.cap_drop || []).includes('ALL')) {
    violations.push(`${name}: capabilities are not all dropped`);
  }
  if ((service.cap_add || []).length > 0) {
    violations.push(`${name}: adds capabilities`);
  }
  if (service.privileged) {
    violations.push(`${name}: is privileged`);
  }
  if (!service.read_only) {
    violations.push(`${name}: root filesystem is writable`);
  }
  for (const key of ['network_mode', 'pid', 'ipc']) {
    if (service[key] === 'host') {
      violations.push(`${name}: uses the host ${key}`);
    }
  }
  for (const volume of service.volumes || []) {
    if (DOCKER_SOCKET_PATTERN.test(String(volume.source)) || DOCKER_SOCKET_PATTERN.test(String(volume.target))) {
      violations.push(`${name}: mounts the Docker socket`);
    }
  }
  return violations;
}

// Returns the violations of the network and published-port rules across all services.
function networkViolations(config) {
  const violations = [];
  const networks = config.networks || {};
  if (networks.backend?.internal !== true) {
    violations.push('network backend: must be internal');
  }
  for (const [name, service] of Object.entries(config.services || {})) {
    if (name !== PUBLISHING_SERVICE && (service.ports || []).length > 0) {
      violations.push(`${name}: publishes a port; only ${PUBLISHING_SERVICE} may`);
    }
  }
  const published = config.services?.[PUBLISHING_SERVICE]?.ports || [];
  if (published.length === 0) {
    violations.push(`${PUBLISHING_SERVICE}: publishes no port`);
  }
  for (const port of published) {
    if (port.host_ip !== LOOPBACK) {
      violations.push(`${PUBLISHING_SERVICE}: port ${port.published} is not bound to ${LOOPBACK}`);
    }
  }
  for (const name of ISOLATED_SERVICES) {
    for (const network of Object.keys(config.services?.[name]?.networks || {})) {
      if (networks[network]?.internal !== true) {
        violations.push(`${name}: joins network ${network}, which is not internal`);
      }
    }
  }
  return violations;
}

// Returns the mount with the given target in a service, or undefined.
function mountAt(service, target) {
  return (service?.volumes || []).find((volume) => volume.target === target);
}

// Returns the violations of the storage rules: read-only photos, data and models in named volumes.
function storageViolations(config) {
  const violations = [];
  const api = config.services?.api;
  const photos = mountAt(api, '/photos');
  if (!photos || photos.type !== 'bind' || photos.read_only !== true) {
    violations.push('api: /photos must be a read-only bind mount');
  }
  const data = mountAt(api, '/data');
  if (data?.type !== 'volume' || data.source !== 'data' || data.read_only) {
    violations.push('api: /data must be the writable named volume data');
  }
  for (const name of ['api', 'vision']) {
    const models = mountAt(config.services?.[name], '/models');
    if (models?.type !== 'volume' || models.source !== 'models' || models.read_only !== true) {
      violations.push(`${name}: /models must be the named volume models, read-only`);
    }
  }
  if ((config.services?.vision?.volumes || []).some((volume) => volume.target === '/photos')) {
    violations.push('vision: must not mount the photos');
  }
  return violations;
}

// Returns every violation of a parsed Compose configuration; an empty list means it complies.
function findViolations(config, readFile = (file) => fs.readFileSync(file, 'utf8')) {
  const services = config.services || {};
  const violations = REQUIRED_SERVICES.filter((name) => !services[name]).map((name) => `${name}: service is missing`);
  for (const [name, service] of Object.entries(services)) {
    violations.push(...imageViolations(name, service, readFile), ...privilegeViolations(name, service));
  }
  return [...violations, ...networkViolations(config), ...storageViolations(config)];
}

// Reads the configuration from stdin and reports. Sets the process exit code described in the file header.
function main() {
  let config;
  try {
    config = JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch (error) {
    console.error(`compose-policy: cannot read the configuration: ${error.message}`);
    process.exitCode = 2;
    return;
  }
  const violations = findViolations(config);
  for (const violation of violations) {
    console.error(`compose-policy: ${violation}`);
  }
  process.exitCode = violations.length > 0 ? 1 : 0;
}

if (require.main === module) {
  main();
}

module.exports = { findViolations, unpinnedBaseImages };

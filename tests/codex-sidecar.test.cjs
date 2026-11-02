// Guard test: the Codex sidecar image pins its base and CLI, installs the documented config, keeps it current, and is
// wired to `api` through its HTTP front and the per-start token.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const SIDECAR_DIR = path.join(REPO_ROOT, 'docker', 'codex');
const BASE_IMAGE = 'node:22-slim@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2';
const CLI_PACKAGE = '@openai/codex@0.166.0';

// Returns the text of a file under the repository root.
function read(...parts) {
  return fs.readFileSync(path.join(REPO_ROOT, ...parts), 'utf8');
}

// Returns the lines of the TOML block that follows the sidecar-config sentence in docs/ai-providers.md.
function documentedConfig() {
  const docs = read('docs', 'ai-providers.md');
  const match = /sidecar config turns off[^\n]*\n+```toml\r?\n([\s\S]*?)```/.exec(docs);
  assert.ok(match, 'docs/ai-providers.md has no sidecar TOML block');
  return match[1].split(/\r?\n/).filter((line) => line.trim() !== '');
}

// Returns the non-empty lines of the sidecar config, without its leading comment header.
function shippedConfig() {
  const lines = read('docker', 'codex', 'config.toml').split(/\r?\n/).filter((line) => line.trim() !== '');
  const first = lines.findIndex((line) => !line.startsWith('#'));
  return lines.slice(first);
}

test('the sidecar config is exactly the TOML documented in docs/ai-providers.md', () => {
  assert.deepEqual(shippedConfig(), documentedConfig());
});

test('the sidecar config turns off web search, history, analytics, feedback, telemetry and the shell tool', () => {
  const config = shippedConfig().join('\n');
  for (const line of [
    'web_search = "disabled"',
    'history.persistence = "none"',
    'analytics.enabled = false',
    'feedback.enabled = false',
    'otel.exporter = "none"',
    'otel.trace_exporter = "none"',
    'otel.metrics_exporter = "none"',
  ]) {
    assert.ok(config.includes(line), line);
  }
  assert.match(config, /\[features\]\nshell_tool = false/);
});

test('the image pins the base by digest and the CLI by exact version, and runs as uid 10001', () => {
  const dockerfile = read('docker', 'codex', 'Dockerfile');
  const froms = dockerfile.split(/\r?\n/).filter((line) => /^FROM /.test(line));
  assert.deepEqual(froms, [`FROM ${BASE_IMAGE}`]);
  assert.match(dockerfile, new RegExp(`npm install -g ${CLI_PACKAGE.replace(/[.@/]/g, '\\$&')} `));
  const installs = dockerfile.match(/npm install[^\n]*/g);
  assert.equal(installs.length, 1, 'one install, of the pinned CLI only');
  assert.match(dockerfile, /^USER 10001:10001$/m);
  assert.match(dockerfile, /^ENV [\s\S]*CODEX_HOME=\/codex-home/m);
});

test('the image copies only its config, entrypoint and HTTP front from a context that holds nothing else', () => {
  const dockerfile = read('docker', 'codex', 'Dockerfile');
  const copies = dockerfile.split(/\r?\n/).filter((line) => /^(COPY|ADD) /.test(line));
  assert.deepEqual(copies, [
    'COPY config.toml /etc/auraframe/codex/config.toml',
    'COPY entrypoint.sh /usr/local/lib/auraframe/codex-entrypoint.sh',
    'COPY sidecar-server.cjs /usr/local/lib/auraframe/codex-sidecar.cjs',
  ]);
  assert.deepEqual(fs.readdirSync(SIDECAR_DIR).sort(), ['Dockerfile', 'config.toml', 'entrypoint.sh', 'sidecar-server.cjs']);
  assert.match(dockerfile, /^CMD \["node", "\/usr\/local\/lib\/auraframe\/codex-sidecar\.cjs"\]$/m);
});

test('the HTTP front needs no package beyond Node itself', () => {
  const server = read('docker', 'codex', 'sidecar-server.cjs');
  const modules = [...server.matchAll(/require\('([^']+)'\)/g)].map((match) => match[1]);
  assert.ok(modules.length > 0);
  for (const name of modules) {
    assert.match(name, /^node:/, `${name} would need an install in the image`);
  }
});

test('the entrypoint rewrites the config in the Codex home on every start, before the command runs', () => {
  const script = read('docker', 'codex', 'entrypoint.sh');
  const steps = script.split(/\r?\n/).filter((line) => line && !line.startsWith('#'));
  assert.deepEqual(steps, [
    'set -eu',
    'umask 077',
    'cp /etc/auraframe/codex/config.toml "$CODEX_HOME/config.toml"',
    'exec "$@"',
  ]);
  assert.ok(!script.includes('\r'), 'LF line endings, or /bin/sh in the image cannot run it');
});

test('git keeps LF endings for the image scripts on every checkout', () => {
  const rules = read('.gitattributes').split(/\r?\n/).filter((line) => line && !line.startsWith('#'));
  assert.ok(rules.includes('docker/**/*.sh text eol=lf'), '.gitattributes has no LF rule for docker/**/*.sh');
});

test('compose builds the sidecar from its own folder, behind the codex profile, with the codex-home volume', () => {
  const compose = read('compose.yaml');
  const block = /\n {2}codex:\n([\s\S]*?)(?=\n {2}\S|\nnetworks:)/.exec(compose);
  assert.ok(block, 'compose.yaml has no codex service');
  const service = block[1];
  assert.match(service, /\n {6}context: \.\/docker\/codex\n/);
  assert.match(service, /\n {4}profiles: \[codex\]\n/);
  assert.match(service, /\n {4}user: "10001:10001"\n/);
  assert.match(compose, /\nvolumes:\n(?: {2}\S+: \{\}\n)* {2}codex-home: \{\}/);
});

test('compose gives the sidecar and api the per-start token, and exposes the HTTP front on the backend only', () => {
  const compose = read('compose.yaml');
  const block = (name) => new RegExp(`\\n {2}${name}:\\n([\\s\\S]*?)(?=\\n {2}\\S|\\nnetworks:)`).exec(compose)[1];
  const codex = block('codex');
  const api = block('api');
  for (const service of [codex, api]) {
    assert.match(service, /\n {6}AURAFRAME_CODEX_TOKEN: \$\{AURAFRAME_CODEX_TOKEN:-\}\n/);
  }
  assert.match(codex, /\n {4}expose:\n {6}- "8090"\n/);
  assert.doesNotMatch(codex, /\n {4}ports:/);
  assert.match(codex, /test: \["CMD", "node", "\/usr\/local\/lib\/auraframe\/codex-sidecar\.cjs", "--health"\]/);
  assert.ok(/\n {4}endpoint: "http:\/\/codex:8090"\r?\n/.test(read('engine', 'config.yaml')), 'engine/config.yaml points ai.codex at the sidecar');
});

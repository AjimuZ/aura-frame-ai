# ADR 0016: Codex sidecar image, config and volumes

Status: accepted (2026-11-02). Refines [ADR 0005](0005-codex-cli-provider.md) for P05.1. Not observed yet: the image
building, and the CLI running in it.

## Context

ADR 0005 puts the optional Codex CLI judge in its own container, on the only routed network that also reaches the
engine. P05.1 has to build that container before the provider exists (P05.2). The CLI reads its config from
`CODEX_HOME`, which must be a named volume because sign-in writes `auth.json` there and it must survive restarts. A
config seeded into the image would reach a named volume only while the volume is still empty; a volume made by an
older image, or a config edited inside it, would then stay in force without anyone noticing.

## Decision

- `docker/codex/Dockerfile` builds from `node:22-slim` pinned by digest and installs `@openai/codex@0.166.0`, the
  exact version, globally. Its build context is `docker/codex/` alone, so nothing else of the repository reaches the
  daemon. It runs as uid 10001 on a read-only root filesystem with all capabilities dropped.
- `docker/codex/config.toml` is the TOML in [AI providers](../ai-providers.md#how-it-runs), byte for byte below its
  header. It is copied to `/etc/auraframe/codex/` in the image, and the entrypoint copies it into `$CODEX_HOME` on
  every start, before it runs the command. A failed copy stops the container, so the CLI never runs without it.
  `.gitattributes` keeps `docker/**/*.sh` at LF endings, so a Windows checkout cannot turn the entrypoint into a
  script `/bin/sh` refuses.
- The `codex` service in `compose.yaml` sits behind the profile `codex`, joins exactly `backend` and `egress`, and
  mounts only a tmpfs at `/exchange` and the named volume `codex-home` at `/codex-home`. `/tmp` is a tmpfs as in every
  service. Until P05.2 the default command only prints the CLI version and exits.
- `scripts/compose-policy.cjs` enforces the profile, the two networks and the two mounts, and refuses `codex-home` in
  any other service. `bash scripts/check.sh --compose` also proves that the service list of a plain `up` and of
  `--profile local up` holds neither `codex` nor `fetch-models`.

## Alternatives

- **Seed the config only into an empty volume.** Simple, but an older or edited config would silently survive an
  image update.
- **Mount the config read-only over `$CODEX_HOME/config.toml`.** Needs a file bind mount from the host or a second
  volume, which widens the mount list the policy must allow.
- **Point `CODEX_HOME` at a tmpfs and keep only `auth.json` in the volume.** The CLI expects one home directory; a
  split would rely on undocumented file locations.
- **Leave out `backend`.** The sidecar could then not receive copies from `api` without a shared volume, which would
  put the exchange on disk.

## Consequences

- The config cannot be changed at runtime by editing the volume; a change goes through `docker/codex/config.toml`,
  its documentation and its test (`tests/codex-sidecar.test.cjs`).
- The sidecar is on `backend` and has an internet route, so it must never run a server that forwards requests; the
  provider in P05.2 must keep its runner limited to comparisons.
- The `npm install` at build time and the CLI starting on the read-only filesystem have not been observed. The first
  `docker compose --profile codex build codex` is that check.
- Evidence: `tests/compose-policy.test.cjs`, `tests/codex-sidecar.test.cjs`, `bash scripts/check.sh --compose`, and
  the "codex is not started by up" assertion of `bash scripts/check.sh --integration` (not run in P05.1).

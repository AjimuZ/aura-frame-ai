# ADR 0011: Pinned model download in a one-shot egress service

Status: accepted (2026-11-02).

## Context

`vision` needs GGUF files of several gigabytes in the `models` volume. The services that handle photos have no
internet route, and the project rules forbid downloading a model unless the person asks for it. A model file is
code-adjacent input: a tampered file, or a pickle-based format, can do harm when it is loaded. The bake-off (P03.3)
needs three candidates besides the default, and two of their projectors share the upstream name `mmproj-BF16.gguf`.

## Decision

- A one-shot `fetch-models` service downloads the files. It is the only service on the routed `egress` network,
  sits behind the profile `fetch` so that no `up` starts it, and mounts only the `models` volume, writable.
  `./start.sh --fetch-models [model|all]` is the only path that runs it, through `compose run --rm`.
- A manifest (`engine/src/model_fetch/manifest.json`) pins each file to a repository commit, a size and a SHA-256,
  for the default model and every bake-off candidate. Each file has its own name in the volume, so the two
  projectors named `mmproj-BF16.gguf` upstream do not collide.
- A download goes to a hidden partial file. It is renamed into place only after the size, the SHA-256 and the
  format's first bytes match; otherwise it is deleted. Only `.gguf` and `.safetensors` are accepted, by name and by
  content.
- The fetcher uses the Python standard library only, in its own image on the pinned `python:3.12-slim` base, so the
  service installs nothing from a package index.

## Alternatives

- **`huggingface_hub` or `huggingface-cli`.** Resumable downloads and a cache, but a new dependency with its own
  telemetry setting and network behaviour, and its cache layout would hide the plain file names `vision` loads.
- **Download from `start.sh` with `curl` on the host.** No container boundary, and the host would need the checks
  the container image already has.
- **Give `api` a route out for the download.** Breaks the rule that the services handling photos have no route to
  the internet.
- **Pin by branch or tag.** A branch can move; a commit cannot. The SHA-256 would still catch a change, but only
  after the download.

## Consequences

- A changed upstream file fails its check and is not kept; the manifest has to be reviewed and updated on purpose.
- An interrupted download starts again from zero on the next run; there is no resume.
- A `models` volume that already holds files owned by another user is reported as not writable, and needs its
  owner changed by hand.
- Evidence: `engine/tests/test_model_fetch.py` (local fake files), `tests/start-sh.test.cjs`,
  `tests/compose-policy.test.cjs` and the `fetch-models` assertions of `bash scripts/check.sh --integration`.

# ADR 0018: Codex sidecar transport, command-line opt-in and sign-in through stdin

Status: accepted (2026-11-02) for P05.2.2. Refines [ADR 0016](0016-codex-sidecar-image.md) and
[ADR 0017](0017-codex-provider-gate-and-cap.md). Tested with stubs, a fake CLI and an in-memory sidecar; the image
build, a real sign-in and a real call are not observed.

## Context

`CodexProvider` hands each CLI start to an injectable runner (ADR 0017), but `api` has no CLI, no Docker socket and no
internet route, and the `exchange` tmpfs is mounted only in the `codex` sidecar. `start.sh` still refused
`--provider codex` and `--codex-login`. The task also requires that an API key reaches the sidecar only through stdin,
and that the `codex` profile never starts without an explicit opt-in on the command line.

## Decision

- **Transport: a small HTTP service in the sidecar, on `backend`.** `docker/codex/sidecar-server.cjs` (Node built-ins
  only) is the sidecar's default command and listens on port `8090`, exposed and never published.
  `GET /health` answers without a token, for the compose health check. `POST /v1/run` needs
  `Authorization: Bearer <AURAFRAME_CODEX_TOKEN>` (compared in constant time) and carries the CLI arguments without
  the command name, the files `a.jpg`, `b.jpg` and `schema.json` in base64, and the timeout. It runs one call at a
  time (`409` meanwhile), refuses bodies over 24 MiB (`413`), and answers the exit code, whether it timed out, and the
  verdict text.
- **What may run is an allow-list.** Only `codex --version`, or `codex exec` with the flags the engine builds
  (`--ephemeral`, `--skip-git-repo-check`, `--sandbox read-only`, `-c model_reasoning_effort="<effort>"`,
  `--model <name>`, `--image a.jpg|b.jpg`, `--output-schema schema.json`, `--output-last-message verdict.json`) and
  the prompt last. Files must be those three names, the images JPEG data of at most 8 MiB, the schema JSON. Anything
  else is `400` and starts nothing, so a caller holding the token still cannot run `codex login`, bypass the sandbox
  or name a path.
- **Each call runs in a fresh `0700` folder under `/exchange`,** removed afterwards whatever happens, with
  `shell: false`, no stdin, output discarded, and an environment of `PATH`, `HOME`, `CODEX_HOME` and the locale only.
  A timeout (1 to 600 s) kills the CLI's whole process group, because the npm launcher starts the native CLI as its
  child. Nothing of a request is logged.
- **The token.** `start.sh --provider codex --codex-opt-in` exports a second 64-hex token, independent of the session
  token. Compose passes it to `api` and `codex` only. The sidecar refuses to start without at least 32 characters and
  removes it from its own environment after reading it. In `api`, `SidecarRunner` (`engine/src/providers/codex_sidecar.py`)
  sends it only in the Authorization header; `sidecar_provider(settings, opted_in=...)` builds the provider with it.
  The endpoint is `ai.codex.endpoint` (`http://codex:8090`).
- **Opt-in on the command line.** `--provider codex` without `--codex-opt-in` exits `2` before any Docker call, with a
  message that copies go to OpenAI; `--codex-opt-in` with any other mode or provider also exits `2`. Only that pair
  adds `--profile codex` to `up`. The per-library confirmation in the app is still required before the engine sends a
  copy (P05.2.3).
- **Sign-in.** `--codex-login` runs `codex login --device-auth` in a throwaway container of the same service
  (`compose run --rm --build`), which keeps the credentials in `codex-home`. `--codex-login api-key` reads one line
  from stdin (a hidden `read -s` prompt on a terminal) into an unexported shell variable and writes it with the
  `printf` builtin into the stdin of `compose run -T ... codex login --with-api-key`. The key is never an argument,
  an environment variable or a file.

## Alternatives

- **A shared volume between `api` and `codex`, polled by the sidecar.** Rejected: it would put copies of photos on a
  second mount of `api`, needs a file protocol with atomic renames and polling, and the compose policy would have to
  allow a shared mount in the sidecar.
- **`docker exec` from `api`.** Rejected: it needs the Docker socket, which the project forbids.
- **Putting the CLI in the `api` image.** Rejected: `api` would need an internet route.
- **Reusing the session token.** Rejected: the session token opens the whole API through the gateway; a separate
  secret limits what a leak in the sidecar, which has an internet route, could reach.
- **Passing the API key through `-e` or an argument.** Rejected by the task and by the process list.

## Consequences

- ADR 0016 required that the sidecar never run a server that forwards requests. The front forwards nothing: it starts
  only the allow-listed CLI shapes and answers with an exit code and a verdict.
- `api` gains no network, volume or capability; the sidecar gains a listener on `backend`. Anything on `backend`
  (`gateway`, `vision`, `api`) can connect to port `8090`, but only a holder of the token can run the allow-listed
  CLI shapes. The sidecar can in turn reach `api:8000`, which still requires the session token it does not have.
- The token is still readable in `/proc/1/environ` inside the sidecar by its own user. The CLI's shell tool is off,
  so the model cannot read it; P05.3 checks the upstream sandbox.
- P05.3 may need `--sandbox danger-full-access` if the upstream sandbox fails in Docker; the allow-list must change
  with it.
- Electron's own `docker compose down` does not name the `codex` profile; `start.sh` stops the sidecar when the
  window closes. P05.2.3 decides how the app names the profile.
- Evidence: `tests/codex-sidecar-server.test.cjs` (the server against a fake CLI), `engine/tests/test_codex_sidecar.py`
  (the runner and the provider against an in-memory sidecar), `tests/start-sh.test.cjs` (flags, tokens and stdin,
  with stubs), and the integration check, which now starts the sidecar and runs `codex --version` from `api` through
  the transport (not run in the session that wrote this).

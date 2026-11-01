# ADR 0009: Keep Electron as the desktop shell, with the services in Docker

Status: accepted (2026-11-01), at the owner's request. Supersedes: [ADR 0002](0002-docker-and-web-interface.md).

## Context

The owner wants the application dockerized and started with `start.sh`. ADR 0002 concluded from this that
Electron should go, in favor of a browser interface. The owner rejected that conclusion and wants Electron.

The two wishes meet in one practical constraint. A desktop window inside a container on Windows needs a
display server inside WSL, and it loses what makes a desktop shell useful: native dialogs, the OS key store,
and opening folders in the file manager.

The current Electron layer is already well hardened. Its main process, preload bridge, native dialogs and
`afmedia` scheme can stay almost unchanged ([assessment](../assessment.md#worth-keeping)).

## Decision

- **The Electron window runs on the host. It is the only part outside the containers.**
- All services (`gateway`, `api`, `vision`, `codex`, `fetch-models`) run in Docker Compose.
- `start.sh` remains the only entry point: it starts the services, opens the window, and stops the services
  when the window closes.
- The renderer stays behind the existing bridge, `window.auraFrame`, and has no network access. Only the
  Electron main process talks to the API, with a per-session token the renderer never sees.
- The main process keeps the library registry, the host folder paths, native dialogs and the OS key store.
  Containers see only `/photos`.
- A folder chosen in the native dialog is mounted read-only by recreating `api` through the Docker CLI.
- The desktop shell is built in a container, so the host needs only Docker and a POSIX shell. Host Node.js
  stays as the fallback and for development.
- `start.py` and the Python paths in Electron are removed.

## Alternatives

- **Electron inside a container, through WSLg or X11.** Literally everything in Docker, but fragile on
  Windows, different on each platform, and it loses native dialogs and the key store.
- **Browser interface (ADR 0002).** Rejected by the owner.
- **Electron starting a local Python engine, as today.** Rejected: it does not meet the Docker requirement,
  and that local environment is what broke on this PC.
- **Mount a whole disk read-only, to avoid recreating `api`.** Rejected: it exposes everything on the disk to
  the service that handles photos.
- **Tauri or another shell.** Rejected: a rewrite with no benefit for this project.

## Consequences

- "Everything in Docker" has one explicit exception, the window.
- Fewer changes than ADR 0002: Angular and the bridge stay, and the IPC handlers change their implementation.
- Security improves over a browser interface: websites cannot read the token, and the renderer has no network.
- The main process must be able to run the Docker CLI on the host.
- **Electron must stay on a supported version.** The installed 33.4.11 is out of support, so upgrading becomes
  required work (P01.6).
- Building Windows binaries in a container is documented upstream, but not yet tried on this PC. It rules out
  native runtime dependencies without prebuilds, and the app's runtime dependencies have none.
- Evidence: the exit checks of P02, including the token, hardening, read-only and network isolation tests.

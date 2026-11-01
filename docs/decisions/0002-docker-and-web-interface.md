# ADR 0002: Run everything in Docker with a local web interface

Status: superseded by [ADR 0009](0009-electron-desktop-shell.md) (2026-11-01). The owner wants Electron. Docker
and `start.sh` remain, as described in ADR 0009. This record is kept for history, and nothing below applies.

## Context

The owner wants the whole application dockerized and started with `start.sh`, and old parts removed.
Today it is an Electron desktop application with a Python child process and a local environment that
no longer works on this PC ([assessment](../assessment.md), D2 to D4).

A desktop window does not suit a container. On Windows it needs a display server inside WSL, GPU and
file dialogs behave differently, and the security model of Electron stops being useful.

## Decision

- All services run with Docker Compose, and `start.sh` is the only entry point.
- The interface is the existing Angular application, served on `127.0.0.1` and opened in a browser.
- FastAPI replaces the Electron main process.
- Electron, `start.py` and the preload bridge are removed.
- Services handling photos sit on an internal network with no route to the internet.

## Alternatives

- **Electron in a container through WSLg or X11.** Rejected: fragile, not portable, and gains nothing.
- **Containers for the engine with Electron on the host.** Rejected: not "everything in Docker", and
  it keeps two runtimes.
- **Single container for everything.** Rejected: GPU, network and credential isolation all need
  separate services.
- **`start.ps1` for Windows.** Rejected: the owner asked for `start.sh`, and Git Bash and WSL are
  present on this PC.

## Consequences

- A local web service brings new risks: DNS rebinding and cross-site requests. They are handled in
  [Security and privacy](../security-privacy.md).
- A native folder dialog is lost. The user names a host folder at start, and browses inside it.
- Exports move to a visible host folder. The database lives on a Linux volume, because SQLite with WAL
  is unreliable on Windows bind mounts.
- Docker Desktop needs a memory setting on this PC ([Operations](../operations.md#memory-on-windows)).
- Evidence: the exit checks of P02, including the network isolation and read-only tests.

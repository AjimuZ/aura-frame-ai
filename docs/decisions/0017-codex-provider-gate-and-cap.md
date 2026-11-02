# ADR 0017: Codex provider in the engine: opt-in gate, call cap and an injectable CLI runner

Status: accepted (2026-11-02) for P05.2.1. Refines [ADR 0005](0005-codex-cli-provider.md). Tested only against a
fake CLI; the real CLI, the sidecar transport and a real account are not observed.

## Context

The provider must judge through `codex exec` ([AI providers](../ai-providers.md#how-it-runs)), never act without the
owner's opt-in, stop at a per-run cap (`codex.max_calls`, default 200) and fall back to the local prescore. The
`api` container has no Docker socket, the `codex` sidecar has no listener, and the `exchange` tmpfs is mounted only in
`codex` (ADR 0016). How `api` hands a call to the sidecar is therefore still open.

## Decision

- `engine/src/providers/codex.py` adds `CodexProvider` and `CodexSettings` (`ai.codex` in `engine/config.yaml`).
- **Opt-in is a constructor argument, not a setting.** Only `opted_in=True` (the boolean, nothing truthy) turns the
  provider on. Without it, `compare` and `critique` return `opt_in_required` before any copy is checked, any file is
  written or any process starts, and `health()` reports opt-in as required without starting anything. No
  configuration file can switch it on; the caller passes it after `--provider codex` and the per-library
  confirmation (both still planned).
- **The cap counts CLI starts, retries included,** because each start is a billed agent turn. At the cap the
  provider refuses further starts with `call_limit` and marks its own `status` degraded. `GuardedProvider` now adopts
  the degraded state of an inner provider that keeps a `status`, so the tournament, which reads the outer status,
  stops too. `CodexSettings.guard_settings()` gives the guard the same cap and the failure breaker; the guard counts
  verdicts, never more than starts, so nothing is counted twice against the budget.
- **The CLI is started by an injectable runner** (`run_cli` by default: `shell=False`, an argv list, a minimal
  environment of `PATH`, `HOME`, `CODEX_HOME` and the locale, no stdin, output discarded, a timeout that kills the
  child). Each start uses a fresh private folder under `exchange_root` holding only `a.jpg`, `b.jpg` (or `a.jpg`)
  and `schema.json`; the CLI runs in that folder with relative names, so no engine path reaches its argv. The folder
  is removed whatever happens.
- Copies must pass `check_copy` first, so an original, a PNG or a JPEG with EXIF, ICC or a comment never reaches it.
- A model name and a reasoning effort are validated before they reach argv (`[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`, and
  one of `minimal`, `low`, `medium`, `high`), so a configured value cannot be read as a flag.
- Error codes: `opt_in_required`, `invalid_input`, `unreachable` (the CLI cannot start; not retried), `timeout`,
  `cli_failed` (non-zero exit), `no_output` (no verdict file), `invalid_output` (bad JSON or schema),
  `exchange_failed`, `call_limit`, `degraded`. None holds model text, image data or a path.

## Alternatives

- **An opt-in key in `engine/config.yaml`.** Rejected: an edited file could send photos out without the dialog.
- **Cap verdicts, as the guard does.** Rejected: with retries, the real cost could exceed the cap.
- **A Docker exec from `api` into the sidecar.** Rejected: it needs the Docker socket in `api`, which the project
  forbids.
- **A small HTTP runner in the sidecar now.** Deferred: it would add a listener on `backend` next to an internet
  route, and its design belongs with the stack wiring.

## Consequences

- The provider is complete as an engine component but has no real transport: with the default `run_cli` it needs
  the `codex` command in the same container, which `api` does not have. P05.2.2 and P05.3 must decide how the copies
  and the argv reach the sidecar, keep the exchange folder in the `exchange` tmpfs, and keep the opt-in gate in
  front of it.
- The `codex exec` flags come from the documentation and have not been run against CLI 0.166.0; the verdict schemas
  use `maxLength` and `maxItems`, whose support by the CLI's structured output is unverified.
- Evidence: `engine/tests/test_codex_provider.py` (27 cases, fake CLI started as a real subprocess).

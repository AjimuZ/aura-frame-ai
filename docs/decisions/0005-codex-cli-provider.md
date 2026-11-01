# ADR 0005: Codex CLI as an optional provider in a sidecar

Status: option accepted at the owner's request (2026-11-01). Sidecar design proposed until P05 exit
checks pass. Supersedes: none.

## Context

The owner wants the Codex CLI available as a way to analyze photos. Using it sends image content to
OpenAI, which is a clear exception to the local-first model. `codex exec` is also an agent: by default
it can run commands and search the web, and upstream warns that its sandbox can fail inside Docker.

## Decision

- Codex is an optional provider, off by default. It needs `--provider codex` and an opt-in per library.
- It runs in its own container, on the only network with internet access that also reaches the engine.
  It holds no originals, database, names or paths, only anonymous copies in a tmpfs.
- Each comparison is one `codex exec` call with `--ephemeral`, `--output-schema` and `--image`.
- Web search, the shell tool, history, analytics, feedback and metrics are all turned off by config.
- Calls per run are capped by `codex.max_calls` (default 200).
- No model is hard-coded, since names change often. `codex.model` is optional.

## Alternatives

- **Run Codex in the engine container.** Rejected: it would give an internet route to a service that
  reads originals.
- **Use the host's Codex through the Docker socket.** Rejected: socket access is close to host control.
- **Attach the images with their real names.** Rejected: names leak information.
- **Send all candidates in one request.** Rejected: published evidence shows reliability collapses as
  sets grow.

## Consequences

- A full agent turn per comparison uses more of the plan or API budget than a plain image request. P05
  measures this and may lower the default comparison count for this provider.
- The user is responsible for account data settings, which the opt-in dialog points out.
- Credentials live in a volume visible only to the sidecar.
- Evidence: contract and data-shape tests, and proof that without opt-in the sidecar does not run.

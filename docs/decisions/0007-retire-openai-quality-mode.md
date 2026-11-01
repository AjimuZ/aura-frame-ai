# ADR 0007: Retire the direct OpenAI API flow

Status: proposed (2026-11-01). Needs the owner's confirmation before P05.5 removes the code. Supersedes:
none.

## Context

The engine has an optional cloud flow in `engine/src/cloud/`, with four single-purpose stages, its own
prompts, slot logic, cost table and output folders, about 1,300 lines in total. The owner asked to add the
Codex CLI and to remove what is no longer needed. Keeping both would mean two cloud paths to the same
vendor, two sets of prompts and two privacy surfaces.

## Decision

Remove the direct API flow once the Codex provider works. Move what stays useful into `providers/`:

- image preparation: orientation applied, proportions kept, EXIF stripped, anonymous names;
- the idea of a result cache keyed by image hash, model and prompt version;
- bounded retries.

API-key users stay covered: the Codex CLI signs in with an API key too.

## Alternatives

- **Keep it next to Codex.** Rejected: duplicated surface, and its stages encode the old purpose. Keeping
  it would need a full rewrite anyway.
- **Rewrite it as a third provider, calling the API directly.** A possible later addition. It would avoid
  the agent overhead per call, and it is cheap to add behind the provider contract. Not needed now.

## Consequences

- The budget guard and cost table go away. Codex uses a call cap instead, since plan usage has no price per
  token.
- The API-key field in Settings stays, now for Codex: Electron keeps the key encrypted and passes it to the
  sidecar through stdin. ChatGPT sign-in uses `./start.sh --codex-login`.
- If P05 shows the agent overhead is too high, the direct provider above becomes the next step.

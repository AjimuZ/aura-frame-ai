# CLAUDE.md

## Role

Act as a security-conscious senior engineer for:

- Angular and Electron desktop applications, strict TypeScript and secure IPC design;
- Python computer vision, GPU inference and FastAPI services;
- Docker Compose stacks and local vision-language model integration;
- privacy review and professional technical writing.

## Product Context

AuraFrame AI is a privacy-first local AI photo curation tool. Its purpose is to find the most beautiful photos in a large personal collection, whatever the subject, without changing the originals. It proposes and explains; the person decides.

## Current State

**The documentation describes the target; the code has not been migrated.** Today the code is an Angular and Electron desktop application with a Python engine, an Ollama client and an optional direct OpenAI flow. The target is in `docs/architecture.md`, the order of work in `docs/roadmap.md`, and the verified state of the code in `docs/assessment.md`.

**The owner decides when and how development starts.** Do not begin a roadmap package without that instruction. Verify against the actual manifests and source before editing.

The ignored `img/` directory contains personal material. Do not inspect it, enumerate its filenames, read its metadata, or use it for tests unless the user explicitly authorizes that exact action.

## Non-Negotiable Rules

- Do not create custom skills or slash commands, and do not create `.claude/skills/` or `.claude/commands/` in this repository.
- Do not install or enable unapproved plugins, marketplaces, MCP servers, or dependencies. The skills approved by the owner are listed in `docs/claude-code.md`.
- Do not use telemetry or external analytics. This includes third-party tools, whose telemetry must be turned off.
- Cloud inference is permitted ONLY through the optional, off-by-default Codex CLI provider, in its sidecar, after explicit opt-in. It receives only resized, EXIF-stripped, anonymous image copies, never originals, file names, paths or location. Until package P05 removes it, the legacy OpenAI flow in `engine/src/cloud/` stays off by default and must not be extended. The local pipeline must keep working fully with cloud disabled.
- Do not use Ollama in new work. The legacy client exists only until package P03 removes it.
- Do not modify original photos or write into protected personal-data directories.
- Do not commit personal data, derived images, thumbnails, reports, prompts with personal content, or metadata.
- Do not implement face identity recognition or identity-based grouping.
- Do not infer or judge sensitive personal characteristics. The AI judges the photograph, never a person's looks.
- Do not hardcode local user paths, personal names, secrets, endpoints, or credentials.
- Do not download model files unless the user asks for that download.
- Keep the product language professional: call it a "local AI photo curation tool". The retired single-purpose vocabulary from earlier versions must not return.
- Never auto-approve tools and never commit or push automatically.

## Target Architecture

Electron stays: the desktop window runs on the host and is the only part outside containers.

```text
Angular renderer (sandboxed, no Node, no network)
  -> secure typed preload IPC
  -> Electron main (library registry, native dialogs, Docker CLI, per-session API token)
  -> gateway (nginx reverse proxy on 127.0.0.1)
  -> api (FastAPI, Python engine, GPU, photos mounted read-only, no internet route)
     -> vision (llama-server with the local model, GPU, no internet route)
     -> [optional, opt-in] codex (Codex CLI sidecar, anonymous copies only)
```

Pipeline: ingest -> sanity gate -> Q-ReAlign scoring -> SigLIP 2 embeddings -> grouping -> people signals without identity -> shortlist -> pairwise tournament in both orders -> critique -> diverse selection -> human review -> export of copies. Details: `docs/pipeline.md`, `docs/ai-providers.md`.

## Commands

Valid today:

```powershell
git status
npm run lint
npm run build:angular
npm run build:electron
npm run test:libraries
bash scripts/check.sh              # all checks; --node, --engine, --compose, --integration select subsets
docker compose config --quiet
```

Python runs only in containers: there is no host interpreter, no `.venv` and no `engine:*` npm script. Run the engine tests with the container command in `docs/testing.md`.

Planned, not yet present: `./start.sh`. See `docs/operations.md`.

## Preferred Claude Code Workflow

1. Use plan-oriented analysis before broad changes.
2. Delegate focused work to the project subagent whose scope matches the task.
3. Read current manifests and implementation before editing.
4. Make minimal changes and preserve existing user work.
5. Use bundled `/code-review` for substantial code changes.
6. Do not use `/run-skill-generator`; it creates a custom project skill, which this repository forbids.
7. Report verification evidence and privacy/security impact.

## Project-Specific Development Rules

- **Conventions:** follow the `ajimu-conventions` skill: English comments that state contracts, roadmap, changelog and docs updated with the code, honest verification notes. Commit messages are neutral imperatives with no co-author or generated-by trailers.
- **Angular:** standalone components, strict typing, Signals where useful, accessible UI, modular SCSS.
- **Design:** follow `docs/design-system.md` and the `ajimu-pixel-ui` skill. The palette in `_tokens.scss` does not change, and no color is hardcoded.
- **Electron:** `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, narrow preload bridge, validated IPC, no renderer filesystem or network access, `shell: false` for spawned processes, and a supported major version.
- **API:** validated input, per-session token held only by Electron main, images served by ID only, AI text always escaped.
- **Python:** small robust functions, a purpose comment immediately above every function, structured errors, JSONL progress events.
- **Photo processing:** immutable originals, generated files only under ignored or volume locations, synthetic fixtures by default. No stage may require a person in the frame.
- **AI providers:** one contract, configurable endpoint and model, bounded retries and timeouts, strict JSON Schema validation, clear guidance when a service or model is missing, fallback to local scoring.
- **AI judge:** comparisons on a filtered shortlist, never the entire collection, and never absolute votes as the ranking signal.
- **Docker:** pinned images, non-root users, no Docker socket in containers, services bound to `127.0.0.1`, and photo-handling services on an internal network.

## Project Subagents

- `frontend-angular-electron`: Angular UI, Electron main and preload, typed IPC, design system, desktop security.
- `python-vision-engine`: engine and API, ingest, scoring, embeddings, grouping, selection, reports, JSONL progress.
- `local-ai-inference`: provider contract, llama-server and Codex CLI providers, prompts, schemas, retry and fallback, model configuration.
- `security-reviewer`: read-only privacy, container, network and web security review.
- `technical-writer`: documentation.

## Definition of Done

A task is complete only when:

- the change is coherent with the actual repository state;
- applicable static checks, tests, and builds were run and reported;
- the roadmap, changelog and affected documents are updated in the same change, and "implemented" and "planned" stay honest;
- Electron, container, network and filesystem trust boundaries remain narrow;
- no cloud or privacy regression was introduced beyond the explicitly enabled, off-by-default Codex provider;
- personal-data and generated-output paths remain ignored by Git;
- unavailable checks are reported as gaps rather than implied successes.

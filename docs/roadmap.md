# Roadmap

The order below is a proposal. **The owner decides when and how each package starts.** No package
after P00 has started. An item is checked only after its exit checks pass.

```text
P00 -> P01 -> P02 -> P03 -> P04 -> P05
                 \            \--> P06
                  \----------------^
P04, P05, P06 -> P07
```

## 1. P00 — Baseline and documentation

**Depends on:** nothing. **Areas:** documentation, tooling.

- [x] P00.1 Assess the repository, with evidence.
- [x] P00.2 Research the current state of the art for the pipeline, local models and Codex CLI.
- [x] P00.3 Replace all documentation with the new set.
- [x] P00.4 Review and install 10 Claude Code skills at user scope.
- [x] P00.5 Make the first commit. The owner must authorize this.

Exit: documentation describes the target and marks what is planned; no retired term remains in the
documentation; the first commit exists.

P00.1–P00.4 evidence (2026-11-01): see [assessment](assessment.md), [testing](testing.md) and
[Claude Code](claude-code.md). P00.5 remains open.

## 2. P01 — Foundation

**Depends on:** P00. **Areas:** repository, naming, checks.

- [x] P01.1 Add `scripts/check.sh` as the single entry point for all checks.
- [x] P01.2 Remove the retired product vocabulary from code, prompts, schemas, names, comments,
      tests and `package.json`. Rename `profile` to `library` and the AI fields to neutral names.
- [ ] P01.3 Remove the hard-coded personal name from code, mock data and tests.
- [ ] P01.4 Add the `LICENSE` file for the declared license, or change the declaration.
- [ ] P01.5 Remove the broken `.venv`, and run Python only in containers.
- [ ] P01.6 Upgrade Electron and `electron-builder` to supported versions.

Exit: `scripts/check.sh` passes; the retired-terms test passes; no user path or personal name in
tracked files; the desktop app starts on a supported Electron version.

## 3. P02 — Container stack

**Depends on:** P01. **Areas:** Docker, API, Electron main.

- [ ] P02.1 Add `compose.yaml`, Dockerfiles, networks, volumes and hardening.
- [ ] P02.2 Add `start.sh` with the contract in [Operations](operations.md#startsh).
- [ ] P02.3 Add the FastAPI application: runs, events, photos, media, exports, data.
- [ ] P02.4 Make Electron main a client of the API: same bridge and IPC channels, HTTP in place of
      the child process, `afmedia` proxied, chosen folder mounted read-only.
- [ ] P02.5 Build the desktop shell in a container, or record why the host fallback is needed.
- [ ] P02.6 Remove `start.py` and the Python paths from Electron.

Exit: `./start.sh` opens the Electron window; a synthetic folder chosen in the native dialog completes
a `fast` run end to end; closing the window stops the services; the network, read-only, token and
hardening checks in [Testing](testing.md) pass.

## 4. P03 — Local AI runtime

**Depends on:** P02. **Areas:** providers, models.

- [ ] P03.1 Define the `VisionProvider` contract and implement the llama-server provider.
- [ ] P03.2 Add `fetch-models` with pinned revisions and SHA-256 checks.
- [ ] P03.3 Run the [bake-off](ai-providers.md#bake-off) on the reference PC, and record the results.
- [ ] P03.4 Confirm or replace the default in [ADR 0004](decisions/0004-default-local-model.md).
- [ ] P03.5 Remove the Ollama client, its settings, UI texts and tests.

Exit: the default model completes the benchmark on the reference PC with at least 0.5 GiB of GPU
memory free at peak; no Ollama reference remains in the code; provider contract tests pass.

## 5. P04 — Curation pipeline

**Depends on:** P03. **Areas:** engine, database, reports.

- [ ] P04.1 New ingest: content-hash IDs, working copies, one decode, orientation applied once.
- [ ] P04.2 Sanity gate that rejects only objective failures.
- [ ] P04.3 Q-ReAlign scoring and SigLIP 2 embeddings, batched on the GPU.
- [ ] P04.4 Order-independent grouping, content tags and people signals with no identity.
- [ ] P04.5 Shortlist, Swiss tournament in both orders, Bradley-Terry rank, verdict cache.
- [ ] P04.6 Critique, diversity-aware selection, tiers, reports and exports.
- [ ] P04.7 Persist per-stage progress so resume really works.
- [ ] P04.8 Remove the old gate, ranker, slots, OpenCLIP and LAION paths.

Exit: the regression corpus in [Testing](testing.md#regression-corpus-p04) passes; a run on 2,000
synthetic images completes on the reference PC with recorded time and peak memory; the benchmark
score of the pipeline is recorded and is not below the scorer alone.

## 6. P05 — Codex CLI provider

**Depends on:** P03. **Areas:** providers, security.

- [ ] P05.1 Build the sidecar image, its config with tools and telemetry off, and its volumes.
- [ ] P05.2 Implement the provider, opt-in, call cap and `--codex-login`.
- [ ] P05.3 Check the upstream sandbox in Docker, and record the outcome.
- [ ] P05.4 Measure the usage cost per comparison, and record it.
- [ ] P05.5 Remove `engine/src/cloud/`, after moving the image preparer into `providers/`
      ([ADR 0007](decisions/0007-retire-openai-quality-mode.md)).

Exit: the contract and data-shape tests pass; without opt-in the sidecar is not running and no call
can happen; a real run with the owner's account completes on synthetic images.

## 7. P06 — Interface

**Depends on:** P02; screens for new data depend on P04. **Areas:** web.

- [ ] P06.1 Add non-color tokens and shared primitives on the existing palette.
- [ ] P06.2 Apply them to layout, navigation and every screen.
- [ ] P06.3 Add the pixel icon sprite and, if approved, a pixel title font.
- [ ] P06.4 Build the review flow: alternates, A/B compare, stored decisions.

Exit: all checks in [Design system](design-system.md#verification) pass; the end-to-end test passes.

## 8. P07 — Release

**Depends on:** P04, P05, P06. **Areas:** documentation, release.

- [ ] P07.1 Remove the "planned" marks from what was delivered, and fix what changed.
- [ ] P07.2 Add a project summary for a portfolio, written only from delivered behavior.
- [ ] P07.3 Add real screenshots made from synthetic photos.
- [ ] P07.4 Tag version 2.0.0, after authorization by the owner.

Exit: a clean clone runs on the reference PC by following the documentation alone.

## Later

- Taste model trained on the user's own A/B choices.
- Heavier scorers (Q-ReAlign Lite or Pro) on larger GPUs.
- AMD and Intel GPUs through the llama.cpp Vulkan image.

# Repository assessment

Date: 2026-11-01. This records the code as it stood before the target work began. Evidence is
either "run", meaning a command or script was executed, or "read", meaning the source was inspected.
No personal photo was opened. All run checks used synthetic images.

## What the code does today

A desktop application with an Angular UI inside Electron, which starts a Python engine as a child
process. The engine indexes a folder, measures each photo with classic metrics and a face detector,
pre-scores it with an aesthetic model, asks a local vision model to rate the best candidates, then
fills a fixed six-slot set and exports copies and reports. An optional OpenAI flow does a four-stage
cloud review. Size: about 11,500 lines across engine, Electron and Angular sources, tests and old
documents.

The product was built for one narrow purpose: choosing photos for a person's social profile. That
assumption is built into the prompts, score fields, slots, face requirement and UI. The new goal, to
find beautiful photos of any subject, conflicts with it directly.

## Worth keeping

- **Boundaries.** The UI reaches the backend through one interface, `AURA_FRAME_API`, so it can
  change transport without touching the screens.
- **Electron layer.** A sandboxed renderer, a narrow typed bridge, validated IPC, native dialogs, the
  `afmedia` scheme and the OS key store can all stay ([ADR 0009](decisions/0009-electron-desktop-shell.md)).
- **Engine protocol.** JSONL events and per-library SQLite carry over well to a local API.
- **Privacy habits.** Read-only originals, no identity recognition, synthetic test images, and guard
  hooks that protect personal paths.
- **Report escaping.** HTML reports escape every value, including AI text.
- **Image preparation for the cloud.** It applies orientation, keeps proportions and strips EXIF.
- **Validation.** Strict parsing of model output, with bounded retries.

## Verified defects

Run evidence comes from a throwaway `python:3.12-slim` container with `engine/` mounted read-only.

### Blocks the new goal

| ID | Defect | Location | Evidence |
| --- | --- | --- | --- |
| A1 | A photo without a face never reaches the AI, and is categorized `REJECT` | `engine/src/final_ranker.py:176`, `:36` | Run: a technically perfect faceless photo gives 0 candidates, category `REJECT` |
| A2 | ~~The gate discards valid photographs: global blur measure and mean brightness~~ **Resolved in P04.2:** the gate rejects only unreadable files, unsupported formats, byte-identical copies and images under 256 px on the long side (`engine/src/local_gate.py`) | Run: `engine/tests/test_sanity_gate.py` (a smooth gradient and a black image with bright points survive; dark, bright and flat images are kept) |
| A3 | Prompts, score fields and slots assume one narrow purpose | `engine/src/vlm/schema.py`, `engine/src/cloud/openai_prompts.py`, `engine/src/final_ranker.py` | Read: see [retired vocabulary](#retired-vocabulary) |
| A4 | The final set is six fixed slots, tied to the old purpose | `engine/src/final_ranker.py:189` | Read |

### Wrong results

| ID | Defect | Location | Evidence |
| --- | --- | --- | --- |
| B1 | **Resume never works.** Hashes are not saved in the JSON record, so the comparison always fails, and every photo is redone | `engine/src/utils.py:296`, `engine/src/db.py:253`, `engine/main.py:103` | Run: after a round trip `file_hash` is `''`, and the resume condition is false |
| B2 | ~~The local model receives photos sideways. Orientation is not applied, and the EXIF tag is dropped~~ **Resolved in P03.5:** the legacy client that sent the photos is removed. The provider copy applies the EXIF orientation before the metadata is dropped (`engine/src/providers/images.py`), and the model stage no longer sends photos to a model at all until P04 | Read: `engine/src/providers/images.py:21`–`26`; the legacy client file is gone |
| B3 | Reranking is not idempotent, so penalties compound on each AI re-review | `engine/src/final_ranker.py:130`, `engine/main.py:218` | Run: 8.0 becomes 7.2, then 6.48 |
| B4 | AI re-review breaks the final set. `rank_photo` resets `selected`, and the command never selects again | `engine/src/final_ranker.py:99`, `engine/main.py:206`–`224` | Run: status goes from `selected` to `accepted`. The command path is read |
| B5 | ~~Embeddings ignore EXIF orientation~~ **Resolved in P04.1:** ingest applies the orientation once, and embeddings read the upright working copy (`engine/src/embeddings.py`) | Run: `engine/tests/test_ingest.py` (orientation 6 gives an upright working copy and thumbnail) |
| B6 | ~~Absolute 1 to 10 votes from the model are the main AI signal, the protocol that published evidence finds least reliable~~ **Resolved in P04.5 for the new path:** the ranking signal is the pairwise outcome in both orders (a disagreement is a tie), fitted with Bradley-Terry; the prescore is only a weak prior and no absolute vote enters the rank (`engine/src/tournament.py`, `engine/src/bradley_terry.py`, [ADR 0013](decisions/0013-tournament-rank-and-verdict-cache.md)). The old vote schema in `engine/src/vlm/schema.py` still feeds the legacy ranker and goes with it in P04.8 | `engine/src/vlm/schema.py:12`, `engine/src/tournament.py` | Run: `engine/tests/test_tournament.py` (fake provider; the real model was not run); the legacy schema is read |
| B7 | Without AI, the suitability term falls back to the technical score, so it counts twice. Diversity is a constant 60 | `engine/src/final_ranker.py:67`, `:74` | Read |
| B8 | A clean install has no aesthetic weights, and the fallback derives a score from vector spread and shows it as a real score. **Resolved in P04.3 for the new scoring path:** a missing Q-ReAlign or SigLIP 2 model stores an unknown value (NULL, or no embedding row) and a structured error, never a neutral number or a colour histogram. The old fallback in `engine/src/aesthetic.py` still feeds the legacy ranker and goes with it in P04.8 | `engine/src/neural_stages.py`, `engine/src/aesthetic.py:34`, `:68` | Run: `engine/tests/test_neural_stages.py` (missing model, unusable answers, failed batch) with fake models; the legacy fallback is read |
| B9 | Penalties match free model text, in two languages | `engine/src/final_ranker.py:113`–`116` | Read |
| B10 | Clustering depended on input order (first-fit). **Resolved in P04.4a:** `assign_clusters` is now complete-linkage over id-sorted photos with a capture-time gate | `engine/src/embeddings.py` | Run: `engine/tests/test_clustering.py` (permuted input, time gate, vector sizes) |

### Performance

| ID | Defect | Location | Evidence |
| --- | --- | --- | --- |
| C1 | ~~Each original is fully decoded three times, by quality, faces and embeddings~~ **Resolved in P04.1:** ingest decodes each original once and the three stages read the working copy | Run: `engine/tests/test_ingest.py` counts one decode per original |
| C2 | ~~Models run on CPU, one image at a time, on a PC with a 12 GB GPU~~ **Resolved in P04.3 in code, not yet measured:** the engine sends Q-ReAlign and SigLIP 2 batches of the configured size to the GPU (`engine/src/neural_stages.py`, `engine/src/gpu_models.py`), and `analyze` no longer calls OpenCLIP. Speed and memory on the reference PC stay unmeasured until P04.MEASURE | Run: `engine/tests/test_neural_stages.py` proves batching and device selection with fake models; real GPU behavior is unverified |
| C3 | ~~Photo IDs hash the absolute path. Moving a folder loses all results~~ **Resolved in P04.1:** the ID is the SHA-256 of the content (`photo_id_for` in `engine/src/utils.py`) | Run: `engine/tests/test_ingest.py` (a moved file keeps its ID and results) |

### Environment and repository

| ID | Defect | Evidence |
| --- | --- | --- |
| D1 | **No commits exist.** Everything is untracked, so no change can be undone with Git | Run: `git log` reports no commits |
| D2 | ~~`.venv` points to an interpreter under another user account, so it does not start~~ **Resolved in P01.5:** the `.venv` directory is gone from the repository root, and the Electron main process no longer looks for it. Python runs only in containers | Run: root listing shows no `.venv` |
| D3 | ~~`npm run engine:test` needs Python 3.12, and this PC has only 3.15~~ **Resolved in P01.5:** the `engine:*` npm scripts that called Python on the host are removed. Engine tests run in the container command in [Testing](testing.md#engine-tests-in-a-throwaway-container) | Read: `package.json` |
| D4 | ~~`start.py` accepts the broken `.venv` as valid, because it checks only that the file exists~~ **Resolved in P01.5:** the host Python lookup is removed, and `start.py` is absent from the working tree. Since P02.4.2 `analysis:start` calls the containerised engine over HTTP, and `openai:start` fails with a clear message | Read: `apps/desktop/electron/ipc/paths.ts`, `analysis.ipc.ts` |
| D5 | ~~A personal first name is hard-coded as the default library, in code, mock data and tests~~ **Fixed in P01.2.2:** the default is now "My library" and the mock data and tests use neutral names. **Guarded in P01.3:** the `package.json` author field is removed and `tests/personal-data.test.cjs` fails on user-home paths and on the owner name | Read: `apps/desktop/electron/ipc/library-store.ts` |
| D6 | `package.json` declares MIT, but no `LICENSE` file exists | Run |
| D7 | Face detection used MediaPipe's legacy `solutions` API, with upstream support ended in 2023. **Resolved in P04.4c:** `engine/src/face_quality.py` uses the Face Landmarker tasks API, with the model pinned in the fetch manifest ([ADR 0012](decisions/0012-people-signals-face-landmarker.md)) | Run: `engine/tests/test_face_quality.py` (fake landmarker; the real model and API were not run) |
| D8 | `tqdm` is required but never imported | Read |
| D9 | Primary button labels have 3.36:1 contrast, under the AA threshold | Run, see [Design system](design-system.md#measured-contrast) |
| D10 | Angular has no unit tests, and no end-to-end test exists | Read |
| D11 | 13 color literals sit outside the token file, in shared styles and one component | Run: the design system color check |
| D12 | ~~Electron 33.4.11 is out of support. Its last release is from April 2025, and upstream supports only the latest three majors~~ **Resolved in P01.6:** Electron is 45.2.1, the latest release on 2026-11-01, and `electron-builder` is 26.18.0, its current major. Build, lint and Node tests pass. The upgraded app has not yet been started | Run: `npm view electron version`. Read: `package.json`, `package-lock.json` |

## Retired vocabulary

On 2026-11-01, the search for the retired product terms found 64 occurrences in 26 files, including
the documents since replaced. The engine, the Electron contracts, the Angular models and `package.json`
have since been cleaned. `tests/retired-terms.test.cjs` now scans every file listed by
`git ls-files -co --exclude-standard`, matches whole identifier words in any letter case, and fails
naming file and line. The per-person collections are now called libraries in the apps, the engine
comments and `package.json` (P01.2.2).

## How to improve the project

In order of value, all planned in the [roadmap](roadmap.md):

1. **Make history possible.** A first commit before any change (P00.5).
2. **Patch the shell.** Move Electron to a supported version (P01).
3. **Change the question.** Stop rating one photo at a time, and compare pairs (P04).
4. **Remove the person assumption.** Any subject can win (P01, P04).
5. **Use the GPU.** Modern scorers in batches replace CPU work and hand-tuned metrics (P04).
6. **Make it reproducible.** Containers replace a broken local environment (P02).
7. **Make privacy structural.** Network design, not only code, keeps photos local (P02).
8. **Fix what stays.** Resume, orientation and idempotence become regression tests (P04).

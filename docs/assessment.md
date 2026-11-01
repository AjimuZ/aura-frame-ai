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
| A2 | The gate discards valid photographs: global blur measure and mean brightness | `engine/src/local_gate.py:72`–`77` | Run: a smooth scene (variance 0.1) is rejected as `extreme_blur`; a starry sky (mean 0.14) as `fully_black_image` |
| A3 | Prompts, score fields and slots assume one narrow purpose | `engine/src/vlm/schema.py`, `engine/src/cloud/openai_prompts.py`, `engine/src/final_ranker.py` | Read: see [retired vocabulary](#retired-vocabulary) |
| A4 | The final set is six fixed slots, tied to the old purpose | `engine/src/final_ranker.py:189` | Read |

### Wrong results

| ID | Defect | Location | Evidence |
| --- | --- | --- | --- |
| B1 | **Resume never works.** Hashes are not saved in the JSON record, so the comparison always fails, and every photo is redone | `engine/src/utils.py:296`, `engine/src/db.py:253`, `engine/main.py:103` | Run: after a round trip `file_hash` is `''`, and the resume condition is false |
| B2 | The local model receives photos sideways. Orientation is not applied, and the EXIF tag is dropped | `engine/src/vlm/ollama_client.py:109` | Run: a 400x800 photo is sent as 800x400 with no orientation tag |
| B3 | Reranking is not idempotent, so penalties compound on each AI re-review | `engine/src/final_ranker.py:130`, `engine/main.py:218` | Run: 8.0 becomes 7.2, then 6.48 |
| B4 | AI re-review breaks the final set. `rank_photo` resets `selected`, and the command never selects again | `engine/src/final_ranker.py:99`, `engine/main.py:206`–`224` | Run: status goes from `selected` to `accepted`. The command path is read |
| B5 | Embeddings ignore EXIF orientation | `engine/src/embeddings.py:62` | Read |
| B6 | Absolute 1 to 10 votes from the model are the main AI signal, the protocol that published evidence finds least reliable | `engine/src/vlm/schema.py:12` | Read, and [ADR 0006](decisions/0006-comparative-curation-pipeline.md) |
| B7 | Without AI, the suitability term falls back to the technical score, so it counts twice. Diversity is a constant 60 | `engine/src/final_ranker.py:67`, `:74` | Read |
| B8 | A clean install has no aesthetic weights, and the fallback derives a score from vector spread and shows it as a real score | `engine/src/aesthetic.py:34`, `:68` | Read |
| B9 | Penalties match free model text, in two languages | `engine/src/final_ranker.py:113`–`116` | Read |
| B10 | Clustering depends on input order: first-fit, with the first member as centroid | `engine/src/embeddings.py:91` | Read |

### Performance

| ID | Defect | Location | Evidence |
| --- | --- | --- | --- |
| C1 | Each original is fully decoded three times, by quality, faces and embeddings | `engine/src/quality_score.py:60`, `engine/src/face_quality.py:64`, `engine/src/embeddings.py:62` | Read |
| C2 | Models run on CPU, one image at a time, on a PC with a 12 GB GPU | `engine/src/embeddings.py:37`–`41` | Read |
| C3 | Photo IDs hash the absolute path. Moving a folder loses all results | `engine/src/utils.py:190` | Read |

### Environment and repository

| ID | Defect | Evidence |
| --- | --- | --- |
| D1 | **No commits exist.** Everything is untracked, so no change can be undone with Git | Run: `git log` reports no commits |
| D2 | `.venv` points to an interpreter under another user account, so it does not start | Run: "No Python at ..." |
| D3 | `npm run engine:test` needs Python 3.12, and this PC has only 3.15 | Run: exit 6 |
| D4 | `start.py` accepts the broken `.venv` as valid, because it checks only that the file exists | Read: `start.py:75` |
| D5 | ~~A personal first name is hard-coded as the default library, in code, mock data and tests~~ **Fixed in P01.2.2:** the default is now "My library" and the mock data and tests use neutral names. **Guarded in P01.3:** the `package.json` author field is removed and `tests/personal-data.test.cjs` fails on user-home paths and on the owner name | Read: `apps/desktop/electron/ipc/library-store.ts` |
| D6 | `package.json` declares MIT, but no `LICENSE` file exists | Run |
| D7 | Face detection uses MediaPipe's legacy `solutions` API, with upstream support ended in 2023 | Read: `engine/src/face_quality.py:24` |
| D8 | `tqdm` is required but never imported | Read |
| D9 | Primary button labels have 3.36:1 contrast, under the AA threshold | Run, see [Design system](design-system.md#measured-contrast) |
| D10 | Angular has no unit tests, and no end-to-end test exists | Read |
| D11 | 13 color literals sit outside the token file, in shared styles and one component | Run: the design system color check |
| D12 | **Electron 33.4.11 is out of support.** Its last release is from April 2025, the latest is 45.2.1, and upstream supports only the latest three majors | Run: installed version against the official release list |

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

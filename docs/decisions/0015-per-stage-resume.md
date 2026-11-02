# ADR 0015: Per-photo, per-stage progress in SQLite; collection stages keyed by their inputs

Status: accepted (2026-11-02).

## Context

`analyze` marked a photo finished only once the whole run had ranked it, so a run killed or cancelled in any stage
redid every stage for every photo that had not reached the end (assessment item B1). The `jobs` table existed but
was written in bulk after each stage, or not at all for some stages. Three more defects made a resumed photo differ
from a fresh one: `load_photos` dropped the file hash, the perceptual hash and the EXIF subset; the camelCase record
rounds scores and leaves out the gate's raw brightness and sharpness; and `upsert_photo` used `INSERT OR REPLACE`,
which deletes the row first, so with foreign keys on it cascaded to the photo's Q-ReAlign values, embedding and
content tag. P04.7 had to make resume real without changing any score.

## Decision

- **One state per photo and stage.** Each per-photo stage (ingest, quality, faces, gate, Q-ReAlign, SigLIP 2, content
  tags, the legacy aesthetic score) stores its results first and then marks the photo `done` in `jobs`: per photo for
  the CPU stages, per batch (one transaction) for the GPU stages through a `checkpoint` callback, and once for the
  content-tag stage. A kill between the two writes leaves the photo pending, so it is redone, never skipped without
  data. The next run gives each stage only the photos it has not finished.
- **Ingest is recorded as it goes.** Each record is stored as soon as it is made, so a kill during indexing keeps the
  photos already decoded. A photo whose derived files are gone is decoded again, and its stage states are cleared, so
  every stage runs on it again.
- **A missing model leaves its stage pending.** The stage records nothing when its model cannot load, so a later run
  with the model does the work. A batch that fails or a copy that cannot be read is recorded as done with an unknown
  value, as before; it is retried only when the photo is ingested again.
- **Collection stages are keyed by their inputs.** Grouping stores its result in `stage_runs` under a SHA-256 of the
  settings and of each member's ID, vector and capture time, and reuses it while the key is unchanged. The other
  collection steps (duplicate marks, gate report, legacy ranking, reports, mirror) only read stored values, take
  milliseconds, and are recomputed on every run.
- **Stored photos load back exactly.** `load_photos` restores the hashes, the EXIF subset and the exact scores from
  their columns, and a new `photo_measures` table keeps the gate's raw brightness and sharpness and the unrounded
  people penalty. `upsert_photo` updates the row in place (`ON CONFLICT DO UPDATE`), so the model results survive.
- **Pause and cancel between units.** The checkpoints read the control file after each photo and each batch, so a
  pause holds a long stage and a cancel stops it with the finished work recorded.
- **Wiring the curation stages waits for P04.8.** ADR 0014 expected P04.7 to call shortlist, tournament, selection
  and critique from `analyze`. That would change what the review screen reads while the old ranker still writes it.
  The wiring moves to P04.8, where the old ranker, slots and exports are removed, so the output changes once.

## Alternatives

- Mark a stage done for all photos after it ends. Rejected: a kill in a long GPU stage would lose all of it.
- Keep a single "last finished stage" per photo. Rejected: stages skip rejected photos and a missing model must leave
  only its own stage pending.
- Recompute grouping on every run. Rejected: it reads every embedding and compares every pair, and the criterion is
  that a rerun repeats no finished work.
- Wire the curation stages now, next to the old ranker. Rejected: two rankings would be stored for the same run and
  the review screen would show only the old one.

## Consequences

- A rerun of a finished, unchanged library decodes, measures, scores, embeds and groups nothing.
- The tables `photo_measures` and `stage_runs` are created on first open; older libraries keep working, and a photo
  stored before them keeps the rounded values of its record until it is analyzed again.
- A library analyzed before this change counts its ranked photos as ingested, and its other stages run once more.
- Tests cover the behavior with fake models and simulated kills only; no real model, GPU or long collection has run.

# ADR 0014: Rank first, then select, then critique; nested tiers from one MMR sequence; escaped reports and copy-only exports

Status: accepted (2026-11-02).

## Context

[ADR 0006](0006-comparative-curation-pipeline.md) chose a written critique for finalists only that never affects the
rank, diversity-aware selection with maximal marginal relevance (`lambda = 0.75`), and copy-only exports. P04.6 had to
fix how those pieces fit together: what the relevance is, how the tiers relate, what the critique may touch, how a
report treats text from a model, and what an export may write. The legacy ranker it replaces on the new path had a
rerank that compounded penalties on each run and matched free model text (assessment items B3, B4, B9).

## Decision

- **Order of work.** The rank is fixed first (tournament order for the shortlist, then the other scored photos by
  prescore, then unscored photos by ID). The selection is cut from it. Only then are the finalists critiqued. The
  critique stage receives photo IDs and returns text, so it has no input to reorder with.
- **Relevance is the place in the ranking**, scaled from 1 to 0 over the pool, not a score. The scale of Bradley-Terry
  strengths or prescores therefore cannot tilt the trade against similarity. The pool is the first
  `selection.pool_size` photos (200) so that a look-alike far down the ranking cannot be pulled up by novelty alone.
- **One sequence, nested tiers.** MMR is greedy, so the sequence is built once and every tier (10, 25, 50, 100) and
  the final set (default 25, configurable) is a prefix. Tiers are nested by construction and clamp to the number of
  photos. Similarity is the cosine of the SigLIP 2 vectors, clamped to 0 to 1; a missing, zero or differently sized
  vector counts as similar to nothing.
- **No state, so no compounding.** Ranking, selection and tiers are pure functions of the inputs and the verdict
  cache. Ties break by rank and then by photo ID, and inputs are sorted by ID. Running again gives the same result.
- **Missing scores are not invented.** The prescore leaves out an unknown value and renormalizes the weights; a photo
  with no score is never shortlisted and ranks after the scored ones, marked `unscored`.
- **Model text is stored raw and escaped on output.** The critique is trimmed of control characters and bounded in
  count and length when received. HTML output escapes every value, has no script or external resource and carries a
  content security policy. CSV output prefixes cells that start with a formula character with an apostrophe.
- **Exports are exclusive copies.** `001_<original name>` with the number as the place in the final set. A copy is
  created only if no file has that name, the folder may not be a link or hold an original, and only earlier numbered
  files are removed on a new export. Reports carry file names and no path or image.

## Alternatives

- Use the Bradley-Terry strength as the MMR relevance. Rejected: its scale depends on the number of outcomes, and the
  diversity trade would shift with it.
- Compute each tier separately. Rejected: tiers would not nest, so Top 10 might hold a photo missing from Top 25.
- Critique before the selection, to show reasons for more photos. Rejected: the critique could then be tempted to feed
  the rank, and it costs a call per photo.
- Escape model text when it is stored. Rejected: the right escaping depends on the output (HTML, CSV, the UI), and
  stored text would be wrong for the others.

## Consequences

- A rerun on an unchanged collection makes no comparison call; critiques are not cached, so it asks again for each
  finalist (25 by default).
- `mmr_lambda`, `final_size` and `pool_size` are untuned starting values (`selection` in `engine/config.yaml`), to be
  reviewed in P04.MEASURE.
- The new modules are not called by `engine/main.py`, the API or the review screen yet. P04.7 wires them with persisted
  progress and P04.8 removes the old ranker and exports.

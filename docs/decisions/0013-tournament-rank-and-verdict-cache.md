# ADR 0013: Pairwise tournament outcomes, Bradley-Terry rank with a weak prior, and an order-aware verdict cache

Status: accepted (2026-11-02).

## Context

[ADR 0006](0006-comparative-curation-pipeline.md) chose pairwise comparison in both orders on a shortlist, ranked with
Bradley-Terry. P04.5 had to fix the details: what counts as a win, how the prescore enters the rank, how many
comparisons a pair costs, and what the verdict cache is keyed on. The old absolute 1 to 10 votes (assessment item B6)
must not return as a ranking signal.

## Decision

- **Outcome of a pair.** A pair is judged twice, as (A, B) and (B, A). It is a win only when both orders pick the same
  photo. Any disagreement, including a tie in either order, is a tie, because a disagreement is the signature of a
  position bias, not of a preference. A pair gives one outcome (1, 0.5 or 0) to the rank, not two.
- **Rank.** Bradley-Terry strengths are a maximum a posteriori fit with a Gaussian prior centred on the standardized
  prescore. `prior_scale` (0.5 log-odds per standard deviation of prescore) and `prior_precision` (0.5) are weak: one
  decisive outcome overrides the whole prescore range, and the prescore only breaks ties and orders photos that no
  outcome touched. The prior also keeps unbeaten and winless photos finite. The uncertainty is the standard error from
  the inverse Hessian at the optimum (a Laplace approximation); it is reported in log-odds units. Standardizing makes
  the result independent of the prescore scale. Only pairwise outcomes rank; the prescore is a prior, never a vote.
- **Pairing.** Swiss rounds, `ceil(log2(K)) + 1`, on photos sorted by running score then prescore, never repeating a
  pair, with a bye (lowest photo without one) for an odd count. Pairing is a bounded backtracking search that prefers
  neighbours, so it is deterministic. When no unmet pairing exists (two photos, for instance) fewer rounds are played.
- **Verdict cache.** The `verdicts` table is keyed by (first photo ID, second photo ID, provider, model, prompt
  version). The photo ID is the content hash, so the cache survives a re-ingest and a move. The key includes the
  order, because each order is a separate judgment. A changed model, provider or prompt version misses the cache. Only
  successful verdicts are stored; a failed call, an unreadable copy and a prescore stand-in are not, so a rerun
  completes the missing pairs. The table holds the winner, confidence, reason tags and the short note, and no path, file
  name or image data.
- **Failure.** The tournament stops early after `max_failed_in_a_row` pairs without a verdict or when the guard
  reports itself degraded, and ranks with the outcomes it has. With no usable outcome, no provider, or a provider that
  is not ready, the stage is skipped and the prescore decides.

## Alternatives

- Count each order as its own outcome. Rejected: a biased judge would then produce two contradictory outcomes per
  pair instead of one tie.
- A flat prior or no prior. Rejected: it leaves unbeaten photos at infinity and gives no order to photos with no data.
- A strong prior. Rejected: the prescore is a rough local estimate, and the judge exists to correct it.
- Key the cache by file path. Rejected: it breaks on a move and stores personal data.

## Consequences

- A rerun on an unchanged collection, model and prompt costs no provider call and loads no image.
- The shortlist, the tournament and the rank are plain functions that take their inputs explicitly. They are not called
  by `engine/main.py` yet, because the prescore they need is built in P04.6 together with the selection. P04.6 added
  the prescore and `run_curation`, which calls them ([ADR 0014](0014-selection-critique-reports-and-exports.md)); the
  call from `engine/main.py` is still open.
- The prior settings, the per-tag share and the failure limit are untuned starting values (`tournament` in
  `engine/config.yaml`), to be reviewed in P04.MEASURE.
- The cache has no expiry; the table grows with the number of distinct pairs judged.

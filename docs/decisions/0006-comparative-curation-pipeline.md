# ADR 0006: Comparative curation pipeline

Status: proposed (2026-11-01). Becomes accepted when the P04 exit checks pass. Supersedes: none.

## Context

The owner asked for the best pipeline available today in place of the current one. The current one
measures photos with hand-tuned metrics, then asks a model for a 1 to 10 vote per photo. It rejects valid
photographs, requires faces, and runs on CPU ([assessment](../assessment.md)).

The most relevant evidence is the Visual Aesthetic Benchmark (arXiv 2605.12684, ICML 2026), which tested
20 multimodal models and six image-quality models against 10 expert judges per task:

- Expert labels are far more reproducible when experts compare than when they score: from 39.5% to 90.8%
  for best and worst together, on homogeneous tasks.
- Scoring and comparing disagree (Kendall's tau of 0.188 and 0.351), so scores do not stand in for
  comparisons.
- Verdicts are unstable when candidate order changes: models keep only 34% to 65% of their accuracy when
  they must agree across three orders.
- Reliability collapses with set size: the best model falls from 47.3% on two images to 6.7% on four.
- On photography, the single-image scorer Q-Align reaches 67.6% top-1, next to the best frontier model at
  68.8%. The authors note the two are not strictly comparable, because scorers rank single-image scores
  within each set. Even so, scorers look like a strong and cheap first filter.
- Human experts remain well ahead: 68.9% against 26.5% on the strict task.

## Decision

Use the pipeline in [Pipeline](../pipeline.md):

1. A gate that rejects only objective failures.
2. Q-ReAlign scoring for quality and aesthetics on the GPU, replacing classic metrics and LAION.
3. SigLIP 2 for grouping, diversity and content tags, replacing OpenCLIP.
4. Group bursts first, and keep only the best of each group as a candidate.
5. Pairwise Swiss tournament in both orders on a shortlist, ranked with Bradley-Terry.
6. Written critique for finalists only. It does not affect rank.
7. Selection by maximal marginal relevance, with a human final decision.

## Alternatives

- **Keep votes and improve the prompt.** Rejected: the protocol itself is the weak point.
- **Larger comparison sets to save calls.** Rejected: reliability collapses.
- **One order only.** Rejected for the default, since instability is well documented. It may become a
  budget option for Codex.
- **Scorer only, no AI judge.** Kept as the `fast` preset, and as the fallback in every failure.
- **`pyiqa` as the scoring library.** Rejected: noncommercial license. Q-ReAlign runs directly through
  `transformers` under Apache-2.0.

## Consequences

- Hundreds of comparisons per run mean long unattended runs. Resume and a verdict cache become essential.
- The benchmark tested Q-Align, not Q-ReAlign, whose improvement is vendor-reported. P04 evaluates it.
- The benchmark's model results do not cover models of the size used here. P03 measures that.
- The interface must say "proposed": no model reaches expert reliability.
- Evidence: the regression corpus, and the benchmark results recorded in P04.

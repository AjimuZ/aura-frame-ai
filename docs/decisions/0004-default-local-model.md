# ADR 0004: Default local vision model

Status: proposed (2026-11-01). Becomes accepted, or changes, when the bake-off in P03 finishes.
Supersedes: none.

## Context

The owner asked for a recommended local model as the default: the best that can run on this PC. The PC
has an RTX 4070 with about 10.77 GiB free at idle, and 15.8 GB of system memory.

Evidence is incomplete. Vendor benchmarks do not measure aesthetic judgment, and the only independent
aesthetic benchmark did not test models this small. The choice therefore rests on published capability
and memory fit, and must be confirmed by a local test.

## Decision

Default: **Qwen3.8-27B, ISTA-DASLab GSQ-RCO build, `IQ2_XS`**, with `mmproj-Qwen3.8-27B-BF16.gguf`,
context 8,192, reasoning off. Details are in [AI providers](../ai-providers.md).

Reasons:

- Newest family that fits, Apache-2.0.
- Vendor RealWorldQA 85.9, against 80.3 for Qwen3.5-9B.
- Zero-shot benchmarks stay at 100.3% of the full model at 2.5 bits per weight.
- Estimated at 9.21 GiB before compute buffers, the safest of the Qwen3.8 builds on this card.

## Alternatives

| Option | Why not the default |
| --- | --- |
| Qwen3.8-27B `IQ2_S` | Better scores, but only 0.78 GiB of estimated margin. In the bake-off |
| Qwen3.8-27B `IQ3_XXS` or above | No margin once the Windows desktop's GPU use is counted |
| Qwen3.5-9B `Q5_K_M` | The usual 12 GB pick and likely faster, but an older family. In the bake-off as fallback |
| Gemma 4 12B `Q4_K_M` | No comparable published result. In the bake-off, as the family used before |
| Qwen3.8-Flash-Next | Smallest file is 66 GB |
| Qwen3.6-27B and 35B-A3B | Do not fit on 12 GB at usable quantization |

## Consequences

- About 9.3 GB of download, on explicit request only.
- The first development task for this area may overturn the choice. The bake-off rule decides: the
  accurate model that fits with at least 0.5 GiB free wins, and speed breaks near ties.
- Reasoning benchmarks fall at this quantization, so reasoning stays off.
- Evidence to collect: accuracy, order stability, seconds per comparison, peak GPU and system memory.

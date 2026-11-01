# ADR 0003: Replace Ollama with llama-server

Status: removal of Ollama accepted at the owner's request (2026-11-01). Choice of runtime proposed
until the P03 exit check passes. Supersedes: none.

## Context

The owner does not want to use Ollama. The local provider needs a runtime that works in a container on
12 GB of GPU memory, accepts images, constrains output with a JSON Schema, and frees memory when idle,
because the scoring models share the GPU.

## Decision

Use `llama-server` from the official `ghcr.io/ggml-org/llama.cpp:server-cuda` image, pinned by digest,
through its OpenAI-compatible endpoint.

Checked on this PC on 2026-11-01, with the local image at build 11960 (`sha256:4ccae195bf54...`):
the container sees the RTX 4070, and `--mmproj`, `--image-max-tokens`, `--reasoning`,
`--sleep-idle-seconds` are all present.

## Alternatives

- **Docker Model Runner.** Convenient, but on Docker Desktop its engine runs outside the Compose stack, so
  `compose.yaml` would not fully describe the runtime or its network limits. Not re-tested on this PC.
- **vLLM.** Very fast with many users, but built for full GPU loading and not aimed at this 12 GB, one
  user case. Its quantized builds are much larger than the GGUF ones.
- **SGLang.** Same limits as vLLM for this case.
- **`transformers` inside the engine.** Would tie the model to the engine process and its memory.
  Rejected for the judge model. It stays in use for the small scoring models.

## Consequences

- Upstream calls multimodal support experimental, so the image is pinned and upgraded on purpose.
- `llama-server` serves any GGUF, so the model can change without code changes.
- The Ollama client, its settings, UI texts and tests are removed in P03.
- Evidence: P03 provider contract tests, and the bake-off running on this runtime.

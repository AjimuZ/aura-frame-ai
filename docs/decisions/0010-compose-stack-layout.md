# ADR 0010: GPU overlay and a light api build stage for the Compose stack

Status: accepted (2026-11-01).

## Context

The architecture gives `api` and `vision` a GPU, and promises that the app can run without one
(`./start.sh --cpu`). A service with `gpus` fails to start on a host without a GPU runtime, so one Compose
file cannot serve both.

The integration check must prove network and mount isolation from inside the real services, on any host
and on every run. The `api` image with PyTorch and CUDA is several gigabytes, and those packages have no
effect on what the check proves.

The check also needs `vision` running, but it has no model file and must never download one.

## Decision

- `compose.yaml` holds everything except GPU access, and `compose.gpu.yaml` adds `gpus: all` to `api` and
  `vision`. `start.sh` (P02.2) selects the overlay unless given `--cpu`.
- `docker/api/Dockerfile` has two stages. `core` has the same package set as the engine tests. `full` adds
  the torch stack and is the default, through `AURAFRAME_API_TARGET`.
- The integration check uses `core`, plus the overlay `tests/integration/compose.override.yaml`, which starts
  `sleep` in the real `vision` image instead of `llama-server`. User, networks, mounts and hardening stay those
  of the real services.
- The top-level `name: auraframe` names the volumes `auraframe_data` and `auraframe_models`. They carry no
  explicit `name:`, so the integration project gets its own volumes and its `down -v` cannot reach real data.

## Alternatives

- **`deploy.resources.reservations.devices` in the base file.** Fails on a host without a GPU, like `gpus`.
- **Two complete Compose files.** The hardening rules would be duplicated, and could drift apart.
- **The `full` stage in the integration check.** Closest to production, but it costs a download of several
  gigabytes on first use and proves nothing more about isolation.
- **A generic test image for `vision`.** It would not show that the real image runs under the hardening.

## Consequences

- `docker compose up` alone runs without a GPU. Forgetting the overlay gives a slow run, not a failure.
- The `full` stage and the GPU overlay are outside the integration check. `--compose` validates the overlay
  statically, and the first real run has to prove the rest.
- A run of the integration check does not need the torch stack, but it does need internet access, for its
  control and for a first build.

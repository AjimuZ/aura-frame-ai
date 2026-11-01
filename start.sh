#!/usr/bin/env bash
# Starts, inspects and stops the AuraFrame container stack; the contract is docs/operations.md#startsh.
#
# Usage: ./start.sh [--fetch-models] [--provider local|codex|none] [--codex-login] [--cpu]
#                   [--rebuild] [--backend-only] [--status] [--logs [service]] [--stop] [--help]
#
# Exit codes: 0 success, 1 failed prerequisite, 2 invalid arguments, 3 a service failed its health check.
#
# Invariants:
# - Arguments are validated before anything else runs, so exit 2 never depends on the host.
# - The session token is generated per start and travels only through the environment (and, with
#   --backend-only, an ignored local file). It is never part of a command line.
# - Nothing here downloads a model. Missing model files are explained, never fetched.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

# Git Bash would otherwise rewrite container paths such as /photos in arguments to docker.
case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) export MSYS_NO_PATHCONV=1 ;;
esac

MIN_DOCKER_MEMORY_GB=10
HEALTH_TIMEOUT_SECONDS=420
TOKEN_FILE="data/session-token"

mode="start"
provider=""
use_gpu=1
rebuild=0
backend_only=0
logs_service=""

# usage: prints the flag list; used for --help and after an invalid argument.
usage() {
  cat <<'EOF'
Usage: ./start.sh [options]
  (no option)                start the services and open the window
  --fetch-models             download model files, then exit (not available yet)
  --provider local|codex|none  choose the provider (codex is not available yet)
  --codex-login              sign in to Codex with a ChatGPT account (not available yet)
  --cpu                      run without a GPU
  --rebuild                  rebuild the images and the desktop shell
  --backend-only             services only, for development
  --status                   show service health
  --logs [service]           follow logs
  --stop                     stop everything, keep the data
Exit codes: 0 success, 1 failed prerequisite, 2 invalid arguments, 3 a service failed its health check.
EOF
}

# invalid_argument MESSAGE: reports a usage error on stderr and exits 2.
invalid_argument() {
  echo "start.sh: $1" >&2
  usage >&2
  exit 2
}

# not_available_yet FEATURE PACKAGE: explains a flag whose feature is planned and exits 1.
# The flag is valid, so this is not a usage error; it counts as a missing prerequisite.
not_available_yet() {
  echo "start.sh: $1 is not available yet (planned in $2, see docs/roadmap.md). Nothing was downloaded or changed." >&2
  exit 1
}

# set_mode MODE: records the single action flag and rejects a second one.
set_mode() {
  if [ "$mode" != "start" ]; then
    invalid_argument "only one of --fetch-models, --codex-login, --status, --logs and --stop can be used at a time"
  fi
  mode="$1"
}

# parse_arguments ARGS...: fills the settings above and exits 2 on any invalid argument.
parse_arguments() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --fetch-models) set_mode fetch-models ;;
      --codex-login) set_mode codex-login ;;
      --status) set_mode status ;;
      --stop) set_mode stop ;;
      --logs)
        set_mode logs
        # The optional service name is the next word, unless it is another flag.
        if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then
          logs_service="$2"
          shift
        fi
        ;;
      --provider)
        if [ $# -lt 2 ]; then
          invalid_argument "--provider needs a value: local, codex or none"
        fi
        case "$2" in
          local | codex | none) provider="$2" ;;
          *) invalid_argument "unknown provider '$2' (use local, codex or none)" ;;
        esac
        shift
        ;;
      --cpu) use_gpu=0 ;;
      --rebuild) rebuild=1 ;;
      --backend-only) backend_only=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) invalid_argument "unknown argument '$1'" ;;
    esac
    shift
  done
}

# fail_prerequisite MESSAGE: reports a missing prerequisite on stderr and exits 1.
fail_prerequisite() {
  echo "start.sh: $1" >&2
  exit 1
}

# check_docker: requires the Docker CLI, a reachable daemon and Compose v2.
check_docker() {
  command -v docker >/dev/null 2>&1 ||
    fail_prerequisite "Docker is not installed or not on PATH. Install Docker Desktop (Windows, WSL2 backend) or Docker Engine (Linux)."
  docker info >/dev/null 2>&1 ||
    fail_prerequisite "The Docker daemon does not answer. Start Docker Desktop or the Docker service, then run this again."
  docker compose version >/dev/null 2>&1 ||
    fail_prerequisite "Docker Compose v2 is missing ('docker compose' failed). Update Docker."
}

# check_gpu: requires a working NVIDIA driver unless --cpu was given.
check_gpu() {
  if [ "$use_gpu" -eq 0 ]; then
    return 0
  fi
  if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
    fail_prerequisite "No NVIDIA GPU was found (nvidia-smi is missing or fails). Install the driver, or run './start.sh --cpu'."
  fi
}

# warn_docker_memory: warns, without failing, when Docker has less than the recommended memory.
warn_docker_memory() {
  local bytes
  bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null)"
  case "$bytes" in
    '' | *[!0-9]*)
      echo "start.sh: warning: could not read the memory available to Docker." >&2
      return 0
      ;;
  esac
  # Integer GB, rounded down: 10 GB is 10 * 1024^3 bytes.
  if [ "$bytes" -lt $((MIN_DOCKER_MEMORY_GB * 1024 * 1024 * 1024)) ]; then
    echo "start.sh: warning: Docker has $((bytes / 1024 / 1024 / 1024)) GB of memory, less than the recommended ${MIN_DOCKER_MEMORY_GB} GB." >&2
    echo "start.sh: warning: see docs/operations.md#memory-on-windows. Model loading may be killed." >&2
  fi
}

# compose ARGS...: runs docker compose with the GPU overlay unless --cpu was given.
compose() {
  if [ "$use_gpu" -eq 1 ]; then
    docker compose -f compose.yaml -f compose.gpu.yaml "$@"
  else
    docker compose -f compose.yaml "$@"
  fi
}

# models_present: succeeds when the vision model files exist in the models volume. It only lists
# the volume read-only with the pinned vision image, and downloads nothing.
models_present() {
  local listing model mmproj
  listing="$(compose --profile local run --rm --no-deps --entrypoint ls vision -1 /models 2>/dev/null)" || return 1
  model="${AURAFRAME_VISION_MODEL:-/models/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf}"
  mmproj="${AURAFRAME_VISION_MMPROJ:-/models/mmproj-Qwen3.8-27B-BF16.gguf}"
  printf '%s\n' "$listing" | grep -qxF "$(basename "$model")" &&
    printf '%s\n' "$listing" | grep -qxF "$(basename "$mmproj")"
}

# explain_missing_models: tells what is missing and what the next step is.
explain_missing_models() {
  echo "start.sh: the model files are not in the models volume." >&2
  echo "start.sh: they are downloaded only on request, with './start.sh --fetch-models' (not available yet, planned in P03.2)." >&2
}

# choose_provider: settles the provider and sets use_vision to 1 only when the local model can run.
# Without a request, a missing model degrades to 'none'. An explicit '--provider local' fails instead.
choose_provider() {
  use_vision=0
  if [ "$provider" = "none" ]; then
    return 0
  fi
  if models_present; then
    if [ "$provider" = "" ] || [ "$provider" = "local" ]; then
      provider="local"
      use_vision=1
    fi
    return 0
  fi
  explain_missing_models
  if [ "$provider" = "local" ]; then
    fail_prerequisite "'--provider local' needs the model files. Use '--provider none' to start without them."
  fi
  echo "start.sh: starting with ai.provider none; local scoring still works." >&2
  provider="none"
}

# generate_token: prints 64 hex characters of randomness read from the kernel.
generate_token() {
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

# prepare_environment: exports the per-session token and the defaults the stack reads.
# The exported variables are inherited by docker compose; none is ever put on a command line.
prepare_environment() {
  AURAFRAME_API_TOKEN="$(generate_token)"
  if [ "${#AURAFRAME_API_TOKEN}" -lt 16 ]; then
    fail_prerequisite "Could not generate a session token from /dev/urandom."
  fi
  export AURAFRAME_API_TOKEN
  export AURAFRAME_PORT="${AURAFRAME_PORT:-8420}"
}

# write_token_file: stores the token for the development flow in an ignored file only the user can read.
write_token_file() {
  mkdir -p "$(dirname "$TOKEN_FILE")"
  (
    umask 077
    printf '%s\n' "$AURAFRAME_API_TOKEN" >"$TOKEN_FILE"
  )
}

# health_check: asks the gateway for /health with the token read from a curl config on stdin,
# so the token is not visible in the process list. Exit 3 when it does not answer 200.
health_check() {
  local code
  code="$(printf 'header = "Authorization: Bearer %s"\n' "$AURAFRAME_API_TOKEN" |
    curl -s --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' -K - \
      "http://127.0.0.1:${AURAFRAME_PORT}/health" 2>/dev/null)"
  if [ "$code" != "200" ]; then
    echo "start.sh: the gateway did not answer /health with 200 (got '${code:-nothing}'). Run './start.sh --logs'." >&2
    return 1
  fi
}

# start_stack: builds what changed, starts the services and waits for their health checks.
start_stack() {
  local profile_args=()
  if [ "$use_vision" -eq 1 ]; then
    profile_args=(--profile local)
  fi
  if [ "$rebuild" -eq 1 ]; then
    compose ${profile_args[@]+"${profile_args[@]}"} build --no-cache ||
      fail_prerequisite "Building the images failed."
  fi
  # Compose rebuilds an image only when its sources changed, thanks to the layer cache.
  if ! compose ${profile_args[@]+"${profile_args[@]}"} up -d --build --wait --wait-timeout "$HEALTH_TIMEOUT_SECONDS"; then
    echo "start.sh: a service failed its health check. Run './start.sh --status' and './start.sh --logs'." >&2
    exit 3
  fi
  if ! health_check; then
    exit 3
  fi
}

# report_started: tells what is running. The window is not launched yet, see the note printed.
report_started() {
  echo "start.sh: services are healthy on http://127.0.0.1:${AURAFRAME_PORT} (provider: ${provider})."
  if [ "$backend_only" -eq 1 ]; then
    write_token_file
    echo "start.sh: the session token is in ${TOKEN_FILE} (ignored by Git). Stop with './start.sh --stop'."
    return 0
  fi
  echo "start.sh: the desktop window is not launched by this script yet: its container build comes in P02.5." >&2
  echo "start.sh: the services stay up; stop them with './start.sh --stop'." >&2
}

# show_status: prints the state and health of every service.
show_status() {
  compose --profile local ps
}

# show_logs: follows the logs of one service, or of all of them.
show_logs() {
  if [ -n "$logs_service" ]; then
    compose --profile local logs --follow "$logs_service"
  else
    compose --profile local logs --follow
  fi
}

# stop_stack: stops and removes the containers and networks; the named volumes stay.
stop_stack() {
  compose --profile local down
}

parse_arguments "$@"

case "$mode" in
  fetch-models) not_available_yet "--fetch-models" "P03.2" ;;
  codex-login) not_available_yet "--codex-login" "P05.2" ;;
esac
if [ "$provider" = "codex" ]; then
  not_available_yet "--provider codex" "P05.2"
fi

check_docker

case "$mode" in
  status)
    show_status
    exit $?
    ;;
  logs)
    show_logs
    exit $?
    ;;
  stop)
    stop_stack
    exit $?
    ;;
esac

check_gpu
warn_docker_memory
prepare_environment
choose_provider
start_stack
report_started

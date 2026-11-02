#!/usr/bin/env bash
# Starts, inspects and stops the AuraFrame container stack; the contract is docs/operations.md#startsh.
#
# Usage: ./start.sh [--fetch-models [model|all]] [--provider local|codex|none] [--codex-opt-in]
#                   [--codex-login [device|api-key]] [--cpu] [--rebuild] [--backend-only] [--status]
#                   [--logs [service]] [--stop] [--help]
#
# Exit codes: 0 success, 1 failed prerequisite, 2 invalid arguments, 3 a service failed its health check.
# After the window has run: 0 when it closed normally, 1 when it failed or the services could not be stopped,
# 130 after Ctrl+C (SIGINT), 129 after SIGHUP and 143 after SIGTERM.
#
# Invariants:
# - Arguments are validated before anything else runs, so exit 2 never depends on the host.
# - The session token is generated per start and travels only through the environment (and, with
#   --backend-only, an ignored local file). It is never part of a command line. The window inherits it.
# - Without --backend-only, the services started here are stopped exactly once when the window ends, however it
#   ends while this script runs, and the named volumes are kept.
# - Only --fetch-models downloads model files, through the one-shot `fetch-models` service, and it then exits.
#   Every other path explains missing model files and never fetches them.
# - The codex sidecar, which sends anonymous copies off the computer, is started (`up` or `run` with the `codex`
#   profile) only by --codex-login, which runs the sign-in alone, and by --provider codex together with
#   --codex-opt-in. --provider codex alone is refused with exit 2 before anything runs.
# - An API key for --codex-login api-key is read from this script's stdin into an unexported variable and reaches the
#   sidecar only through the stdin of `codex login --with-api-key`. It is never an argument, an environment variable
#   or a file, and it is never printed.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

# Git Bash would otherwise rewrite container paths such as /photos in arguments to docker.
case "$(uname -s 2>/dev/null)" in
  MINGW* | MSYS* | CYGWIN*) export MSYS_NO_PATHCONV=1 ;;
esac

MIN_DOCKER_MEMORY_GB=10
HEALTH_TIMEOUT_SECONDS=420
TOKEN_FILE="data/session-token"
# Builder images of the desktop shell, as name@sha256:<digest>. The digests were read from the pulled :wine and
# :latest tags on 2026-11-02 and are never guessed. Empty would mean no pin, and the shell build is then skipped.
# AURAFRAME_BUILDER_IMAGE supplies another pin without editing this file.
BUILDER_IMAGE_WINDOWS="electronuserland/builder@sha256:f3a8d451994c881d0972e7b1846ee4d4fdc15eb41fc6b1f350b38cbf99ca90fe"
BUILDER_IMAGE_LINUX="electronuserland/builder@sha256:22c93521ff6ea682e7ef153bb4846d90b769aa3b3f3f3be9ec9090aabc3e877c"
# The desktop shell that the builder writes, per host platform. The Windows name was observed on 2026-11-02; the
# Linux one is electron-builder's default for this package and has not been observed.
WINDOW_WINDOWS="release/win-unpacked/AuraFrame AI.exe"
WINDOW_LINUX="release/linux-unpacked/aura-frame-ai"
# The host fallback: the Electron binary installed by `npm ci`, running the app that `npm run build` wrote.
ELECTRON_WINDOWS="node_modules/electron/dist/electron.exe"
ELECTRON_LINUX="node_modules/electron/dist/electron"
# Profiles that --status, --logs, --stop and the stop after the window name, so they also reach the vision service
# and the codex sidecar. Naming a profile there starts nothing; only `up` and `run` start services.
SERVICE_PROFILES=(--profile local --profile codex)

mode="start"
provider=""
codex_opt_in=0
login_method="device"
use_vision=0
use_codex=0
use_gpu=1
rebuild=0
backend_only=0
logs_service=""
fetch_target=""
window_cmd=()
window_pid=""
services_stopped=0

# usage: prints the flag list; used for --help and after an invalid argument.
usage() {
  cat <<'EOF'
Usage: ./start.sh [options]
  (no option)                start the services and open the window
  --fetch-models [model|all] download the pinned model files, then exit (default: the default model)
  --provider local|codex|none  choose the provider; codex also needs --codex-opt-in
  --codex-opt-in             agree that resized, anonymous copies of compared photos go to OpenAI (codex only)
  --codex-login [device|api-key]
                             sign in to Codex, then exit: device (default) runs the ChatGPT device sign-in,
                             api-key reads an API key from stdin (hidden prompt on a terminal)
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
      --fetch-models)
        set_mode fetch-models
        # The optional model id is the next word, unless it is another flag. The fetcher checks it against
        # its manifest; here it only has to be a plain id.
        if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then
          if ! [[ "$2" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]; then
            invalid_argument "invalid model name '$2' (use a model id from the manifest, or all)"
          fi
          fetch_target="$2"
          shift
        fi
        ;;
      --codex-login)
        set_mode codex-login
        # The optional sign-in method is the next word, unless it is another flag.
        if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then
          case "$2" in
            device | api-key) login_method="$2" ;;
            *) invalid_argument "unknown sign-in method '$2' (use device or api-key)" ;;
          esac
          shift
        fi
        ;;
      --codex-opt-in) codex_opt_in=1 ;;
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
  check_codex_opt_in
}

# check_codex_opt_in: refuses, with exit 2, --provider codex without --codex-opt-in, and --codex-opt-in with anything
# else, so the codex profile never starts without an explicit agreement on this command line.
check_codex_opt_in() {
  if [ "$provider" = "codex" ] && [ "$mode" = "start" ] && [ "$codex_opt_in" -eq 0 ]; then
    invalid_argument "--provider codex sends resized, anonymous copies of the compared photos to OpenAI. Add --codex-opt-in to agree; nothing was started."
  fi
  if [ "$codex_opt_in" -eq 1 ] && { [ "$provider" != "codex" ] || [ "$mode" != "start" ]; }; then
    invalid_argument "--codex-opt-in applies only to a start with --provider codex"
  fi
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
  echo "start.sh: they are downloaded only on request, with './start.sh --fetch-models' (about 9.3 GB for the default model)." >&2
}

# fetch_models: runs the one-shot fetch-models service, the only path that downloads model files, then exits.
# It passes the optional model id or 'all', and no token, GPU or photo folder. The service keeps a file only when
# its SHA-256 matches the pin in engine/src/model_fetch/manifest.json. Exit 0 when every selected file is
# verified, 1 otherwise.
fetch_models() {
  local args=()
  if [ -n "$fetch_target" ]; then
    args=("$fetch_target")
  fi
  echo "start.sh: fetching the pinned model files into the models volume; each one is checked against its SHA-256."
  if ! docker compose -f compose.yaml --profile fetch run --rm --build fetch-models ${args[@]+"${args[@]}"}; then
    fail_prerequisite "Fetching the model files failed, and no unverified file was kept. See docs/operations.md#model-files."
  fi
  echo "start.sh: the model files are verified. Start with './start.sh'."
  exit 0
}

# codex_login: runs `codex login` once in a throwaway codex container, which keeps the credentials in the codex-home
# volume, then exits 0 when the sign-in succeeded and 1 otherwise. No other service, token, GPU or photo folder is
# involved. 'device' runs the ChatGPT device sign-in, which prints a link and a code. 'api-key' reads one line from
# stdin, with a hidden prompt on a terminal, into an unexported variable, and printf, a builtin that starts no
# process, writes it to the stdin of `codex login --with-api-key`. The key is never an argument or variable of docker.
codex_login() {
  local key=""
  local base=(docker compose -f compose.yaml --profile codex run --rm --build)
  if [ "$login_method" = "device" ]; then
    echo "start.sh: starting the device sign-in in the codex sidecar; open the link it prints and enter the code."
    "${base[@]}" codex codex login --device-auth ||
      fail_prerequisite "The sign-in did not complete. See docs/ai-providers.md#sign-in."
  else
    if [ -t 0 ]; then
      IFS= read -r -s -p "API key (input hidden): " key
      echo >&2
    else
      IFS= read -r key
    fi
    key="${key%$'\r'}"
    if [ -z "$key" ]; then
      fail_prerequisite "No API key was given on stdin; nothing was changed."
    fi
    echo "start.sh: passing the API key to the codex sidecar through stdin."
    printf '%s\n' "$key" | "${base[@]}" -T codex codex login --with-api-key
    local status=$?
    key=""
    if [ "$status" -ne 0 ]; then
      fail_prerequisite "The sign-in with the API key failed. See docs/ai-providers.md#sign-in."
    fi
  fi
  echo "start.sh: signed in; the credentials stay in the codex-home volume. Start with './start.sh --provider codex --codex-opt-in'."
  exit 0
}

# choose_provider: settles the provider and sets use_vision to 1 only when the local model can run, or use_codex to 1
# for the opted-in codex sidecar. Without a request, a missing model degrades to 'none'. An explicit
# '--provider local' fails instead.
choose_provider() {
  use_vision=0
  use_codex=0
  if [ "$provider" = "none" ]; then
    return 0
  fi
  if [ "$provider" = "codex" ]; then
    use_codex=1
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

# prepare_environment: exports the per-session token and the defaults the stack reads. With --provider codex it also
# exports a second, independent token that only `api` and the codex sidecar receive, for their calls over `backend`.
# The exported variables are inherited by docker compose; none is ever put on a command line.
prepare_environment() {
  AURAFRAME_API_TOKEN="$(generate_token)"
  if [ "${#AURAFRAME_API_TOKEN}" -lt 16 ]; then
    fail_prerequisite "Could not generate a session token from /dev/urandom."
  fi
  export AURAFRAME_API_TOKEN
  if [ "$provider" = "codex" ]; then
    AURAFRAME_CODEX_TOKEN="$(generate_token)"
    if [ "${#AURAFRAME_CODEX_TOKEN}" -lt 32 ]; then
      fail_prerequisite "Could not generate the sidecar token from /dev/urandom."
    fi
    export AURAFRAME_CODEX_TOKEN
  fi
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

# build_desktop_shell: builds the app into release/ in the digest-pinned builder image; exits 1 when that fails.
# With no pin it warns and builds nothing, because a floating tag is never run. A pin in another form exits 1.
# Only the build inputs are mounted, read-only, so the personal folders, the data folder and the session token
# stay out of the container. The build works on a copy inside it and writes only to release/. It needs the internet
# for npm and the Electron download, and it runs with the host's user so that release/ stays owned by that user.
build_desktop_shell() {
  local image platform_flag root input
  local mounts=()
  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*)
      image="${AURAFRAME_BUILDER_IMAGE:-$BUILDER_IMAGE_WINDOWS}"
      platform_flag="--win"
      ;;
    *)
      image="${AURAFRAME_BUILDER_IMAGE:-$BUILDER_IMAGE_LINUX}"
      platform_flag="--linux"
      ;;
  esac
  if [ -z "$image" ]; then
    echo "start.sh: warning: the desktop shell was not built: no builder image digest is recorded yet." >&2
    echo "start.sh: warning: see docs/operations.md#desktop-shell-build." >&2
    return 0
  fi
  if ! [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
    fail_prerequisite "The builder image must be pinned as name@sha256:<64 hex characters>, got '$image'."
  fi
  mkdir -p release || fail_prerequisite "Could not create the release folder."
  root="$(pwd -W 2>/dev/null || pwd)"
  for input in package.json package-lock.json angular.json tsconfig.json tsconfig.app.json tsconfig.electron.json apps; do
    mounts+=(-v "$root/$input:/src/$input:ro")
  done
  docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    -e HOME=/tmp/home -e ELECTRON_CACHE=/tmp/home/.cache/electron \
    -e ELECTRON_BUILDER_CACHE=/tmp/home/.cache/electron-builder \
    "${mounts[@]}" -v "$root/release:/out" "$image" \
    bash -c "cp -r /src /tmp/build && cd /tmp/build && npm ci && npm run build && npx electron-builder $platform_flag --publish never && cp -r release/. /out/" ||
    fail_prerequisite "Building the desktop shell failed in $image. See docs/operations.md#desktop-shell-build."
}

# locate_window: sets window_cmd to the desktop window and succeeds, or fails when there is none. The shell built
# into release/ comes first. The host fallback needs the Electron binary from `npm ci` and the output of
# `npm run build`, and runs the app in this folder. Paths are relative to the project folder, the working directory.
locate_window() {
  local shell_path electron
  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*)
      shell_path="$WINDOW_WINDOWS"
      electron="$ELECTRON_WINDOWS"
      ;;
    *)
      shell_path="$WINDOW_LINUX"
      electron="$ELECTRON_LINUX"
      ;;
  esac
  if [ -f "$shell_path" ] && [ -x "$shell_path" ]; then
    window_cmd=("./$shell_path")
    return 0
  fi
  if [ -f "$electron" ] && [ -x "$electron" ] && [ -f dist-electron/main.js ] &&
    [ -f dist/aura-frame/browser/index.html ]; then
    window_cmd=("./$electron" .)
    return 0
  fi
  return 1
}

# prepare_window: finds the window before any service starts, and builds the shell once when it is missing, so a
# start that cannot open a window exits 1 without leaving services behind.
prepare_window() {
  if locate_window; then
    return 0
  fi
  if [ "$rebuild" -eq 0 ]; then
    echo "start.sh: no desktop window was found; building it into release/ (this needs the internet)." >&2
    build_desktop_shell
    if locate_window; then
      return 0
    fi
  fi
  fail_prerequisite "No desktop window is available. Run './start.sh --rebuild', or build it on the host with 'npm ci && npm run build'. See docs/operations.md#desktop-shell-build."
}

# start_stack: builds what changed, finds the window, starts the services and waits for their health checks.
start_stack() {
  local profile_args=()
  if [ "$use_vision" -eq 1 ]; then
    profile_args=(--profile local)
  fi
  if [ "$use_codex" -eq 1 ]; then
    profile_args+=(--profile codex)
  fi
  if [ "$rebuild" -eq 1 ]; then
    compose ${profile_args[@]+"${profile_args[@]}"} build --no-cache ||
      fail_prerequisite "Building the images failed."
    if [ "$backend_only" -eq 0 ]; then
      build_desktop_shell
    fi
  fi
  if [ "$backend_only" -eq 0 ]; then
    prepare_window
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

# report_started: tells what is running and, with --backend-only, where the token is kept.
report_started() {
  echo "start.sh: services are healthy on http://127.0.0.1:${AURAFRAME_PORT} (provider: ${provider})."
  if [ "$use_codex" -eq 1 ]; then
    echo "start.sh: the codex sidecar runs. Copies leave the computer only for a library that also confirms opt-in in the app."
  fi
  if [ "$backend_only" -eq 1 ]; then
    write_token_file
    echo "start.sh: the session token is in ${TOKEN_FILE} (ignored by Git). Stop with './start.sh --stop'."
  fi
}

# stop_services_once: stops the services after the window and keeps the named volumes. Runs `down` at most once,
# whichever of the normal end, a signal or the exit trap comes first. Returns 1 when `down` failed.
stop_services_once() {
  if [ "$services_stopped" -eq 1 ]; then
    return 0
  fi
  services_stopped=1
  echo "start.sh: stopping the services; the data stays."
  if ! compose "${SERVICE_PROFILES[@]}" down; then
    echo "start.sh: the services could not be stopped. Run './start.sh --stop'." >&2
    return 1
  fi
}

# on_signal CODE: ends the window, waits for it, stops the services and exits with CODE. Further signals are
# ignored meanwhile, so a second Ctrl+C cannot cut the stop short.
on_signal() {
  trap '' INT TERM HUP
  if [ -n "$window_pid" ]; then
    kill "$window_pid" 2>/dev/null
    wait "$window_pid" 2>/dev/null
  fi
  stop_services_once
  exit "$1"
}

# run_window: runs the window until it closes, then stops the services and exits; it never returns.
# The token reaches the window only through the inherited environment, with the project folder beside it so a
# packaged window finds compose.yaml. The window runs as a child this script waits for: a signal then interrupts
# the wait at once and reaches the trap, even when the window is a native program that sees no terminal signal.
run_window() {
  local status=0
  trap 'on_signal 130' INT
  trap 'on_signal 129' HUP
  trap 'on_signal 143' TERM
  trap 'stop_services_once' EXIT
  AURAFRAME_PROJECT_DIR="$(pwd -W 2>/dev/null || pwd)"
  export AURAFRAME_PROJECT_DIR
  echo "start.sh: opening the desktop window. Closing it stops the services; the data stays."
  "${window_cmd[@]}" &
  window_pid=$!
  wait "$window_pid" || status=$?
  window_pid=""
  if [ "$status" -ne 0 ]; then
    echo "start.sh: the desktop window exited with code ${status}." >&2
  fi
  stop_services_once || status=1
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    exit 1
  fi
  exit 0
}

# show_status: prints the state and health of every service.
show_status() {
  compose "${SERVICE_PROFILES[@]}" ps
}

# show_logs: follows the logs of one service, or of all of them.
show_logs() {
  if [ -n "$logs_service" ]; then
    compose "${SERVICE_PROFILES[@]}" logs --follow "$logs_service"
  else
    compose "${SERVICE_PROFILES[@]}" logs --follow
  fi
}

# stop_stack: stops and removes the containers and networks; the named volumes stay.
stop_stack() {
  compose "${SERVICE_PROFILES[@]}" down
}

parse_arguments "$@"

check_docker

case "$mode" in
  fetch-models)
    fetch_models
    ;;
  codex-login)
    codex_login
    ;;
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
if [ "$backend_only" -eq 0 ]; then
  run_window
fi

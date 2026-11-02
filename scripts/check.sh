#!/usr/bin/env bash
# Single entry point for every project check; exits 0 only when all selected checks pass.
#
# Usage: bash scripts/check.sh [--node] [--engine] [--compose] [--integration] [--backend-run]
#   --node         lint, Angular build, Electron build, Node tests in tests/
#   --engine       engine tests in a throwaway container (mounts engine/ read-only)
#   --compose      `docker compose config --quiet` and the static hardening policy, without starting anything
#   --integration  --compose, then starts the stack on a synthetic folder and tests its isolation
#   --backend-run  `./start.sh --backend-only` on a synthetic folder, a `fast` run through the API, then
#                  `./start.sh --stop`; it stops the project's services, including ones already running
#   (no flag)      --node, --engine and --compose; the integration check and the backend run are left out
#
# A check whose files do not exist prints a visible SKIP line. A failing check
# is named in the summary and makes the exit code non-zero.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

run_node=0
run_engine=0
run_compose=0
run_integration=0
run_backend=0
for arg in "$@"; do
  case "$arg" in
    --node) run_node=1 ;;
    --engine) run_engine=1 ;;
    --compose) run_compose=1 ;;
    --integration) run_integration=1 ;;
    --backend-run) run_backend=1 ;;
    *) echo "Unknown option: $arg (use --node, --engine, --compose, --integration, --backend-run)" >&2; exit 2 ;;
  esac
done
if [ $((run_node + run_engine + run_compose + run_integration + run_backend)) -eq 0 ]; then
  run_node=1
  run_engine=1
  run_compose=1
fi
if [ "$run_integration" -eq 1 ]; then
  run_compose=1
fi

failed=()
passed=()
skipped=()

# run_check NAME CMD...: runs CMD, records the outcome under NAME.
run_check() {
  local name="$1"
  shift
  echo "==> $name"
  if "$@"; then
    passed+=("$name")
    echo "PASS: $name"
  else
    failed+=("$name")
    echo "FAIL: $name" >&2
  fi
}

# skip_check NAME REASON: records a visible SKIP so a missing check never passes silently.
skip_check() {
  skipped+=("$1")
  echo "SKIP: $1 ($2)"
}

# host_path: prints the current directory in a form Docker accepts (Windows path under Git Bash).
host_path() {
  pwd -W 2>/dev/null || pwd
}

# node_tests: runs the Node test files in tests/ against the compiled Electron code.
node_tests() {
  node --test "tests/**/*.test.cjs"
}

# engine_tests: runs the engine suite in a throwaway container without the torch stack.
engine_tests() {
  local root
  root="$(host_path)"
  MSYS_NO_PATHCONV=1 docker run --rm -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$root/engine:/app/engine:ro" \
    python:3.12-slim bash -c '
      apt-get update -qq && apt-get install -y -qq libgl1 libglib2.0-0 &&
      pip install -q -r /app/engine/requirements.txt &&
      cd /app && python -m pytest engine/tests -q -p no:cacheprovider'
}

# compose_config: validates the stack as run by default, with the vision profile, with GPU access, and with the
# model download service.
compose_config() {
  docker compose config --quiet &&
    docker compose --profile local config --quiet &&
    docker compose --profile local -f compose.yaml -f compose.gpu.yaml config --quiet &&
    docker compose --profile fetch config --quiet
}

# compose_policy: checks the resolved configuration, every profile included, against the hardening rules in
# scripts/compose-policy.cjs.
compose_policy() {
  (
    set -o pipefail
    docker compose --profile local --profile fetch config --format json | node scripts/compose-policy.cjs
  )
}

if [ "$run_node" -eq 1 ]; then
  run_check "lint" npm run lint
  run_check "angular build" npm run build:angular
  run_check "electron build" npm run build:electron
  run_check "start.sh syntax" bash -n start.sh
  if compgen -G "tests/*.test.cjs" >/dev/null; then
    run_check "node tests" node_tests
  else
    skip_check "node tests" "no tests/*.test.cjs files"
  fi
fi

if [ "$run_engine" -eq 1 ]; then
  if [ ! -d engine/tests ]; then
    skip_check "engine tests" "engine/tests does not exist"
  elif ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; the engine check needs it" >&2
    failed+=("engine tests")
    echo "FAIL: engine tests" >&2
  else
    run_check "engine tests" engine_tests
  fi
fi

if [ "$run_compose" -eq 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; the compose checks need it" >&2
    failed+=("compose checks")
    echo "FAIL: compose checks" >&2
  else
    run_check "compose config" compose_config
    run_check "compose policy" compose_policy
  fi
fi

if [ "$run_integration" -eq 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; the integration check needs it" >&2
    failed+=("compose integration")
    echo "FAIL: compose integration" >&2
  else
    run_check "compose integration" bash tests/integration/compose-stack.sh
  fi
fi

if [ "$run_backend" -eq 1 ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; the backend run needs it" >&2
    failed+=("backend run")
    echo "FAIL: backend run" >&2
  else
    run_check "backend run" bash tests/integration/backend-run.sh
  fi
fi

echo
echo "Summary: ${#passed[@]} passed, ${#failed[@]} failed, ${#skipped[@]} skipped"
if [ "${#failed[@]}" -gt 0 ]; then
  echo "Failed checks:" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi
exit 0

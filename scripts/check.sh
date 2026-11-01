#!/usr/bin/env bash
# Single entry point for every project check; exits 0 only when all selected checks pass.
#
# Usage: bash scripts/check.sh [--node] [--engine] [--integration]
#   --node         lint, Angular build, Electron build, Node tests in tests/
#   --engine       engine tests in a throwaway container (mounts engine/ read-only)
#   --integration  slow container integration checks (none exist yet: prints SKIP)
#   (no flag)      --node and --engine; integration checks are left out
#
# A check whose files do not exist prints a visible SKIP line. A failing check
# is named in the summary and makes the exit code non-zero.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

run_node=0
run_engine=0
run_integration=0
for arg in "$@"; do
  case "$arg" in
    --node) run_node=1 ;;
    --engine) run_engine=1 ;;
    --integration) run_integration=1 ;;
    *) echo "Unknown option: $arg (use --node, --engine, --integration)" >&2; exit 2 ;;
  esac
done
if [ $((run_node + run_engine + run_integration)) -eq 0 ]; then
  run_node=1
  run_engine=1
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
      grep -vE "^(torch|open-clip-torch|timm)==" /app/engine/requirements.txt > /tmp/req.txt &&
      pip install -q -r /tmp/req.txt &&
      cd /app && python -m pytest engine/tests -q -p no:cacheprovider'
}

if [ "$run_node" -eq 1 ]; then
  run_check "lint" npm run lint
  run_check "angular build" npm run build:angular
  run_check "electron build" npm run build:electron
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

if [ "$run_integration" -eq 1 ]; then
  skip_check "integration checks" "none exist yet; planned for P02"
fi

echo
echo "Summary: ${#passed[@]} passed, ${#failed[@]} failed, ${#skipped[@]} skipped"
if [ "${#failed[@]}" -gt 0 ]; then
  echo "Failed checks:" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi
exit 0

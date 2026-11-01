#!/usr/bin/env bash
# Runtime proof of the container isolation: starts the stack on a synthetic folder and tests it from inside.
#
# Proves that api and vision cannot reach the internet, that /photos cannot be written, and that only
# gateway publishes a port, on 127.0.0.1. Exits 0 only when every assertion holds.
#
# It runs under its own project name, with its own volumes, and removes them on exit. It reads no personal
# folder and downloads no model. The api image is built from the `core` stage, since the torch stack has no
# effect on these properties. The vision image is the real one, without llama-server running.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 2

# Stops Git Bash rewriting the container paths in the commands below.
export MSYS_NO_PATHCONV=1

# Fixed on purpose: `down -v` below may only ever remove the volumes of this project.
readonly PROJECT="auraframe-integration"
readonly MARKER_NAME="synthetic-marker.txt"
readonly MARKER_TEXT="synthetic fixture"
# Public endpoints, by address and by name. Each must be unreachable from an isolated service.
readonly INTERNET_TARGETS=("1.1.1.1:443" "8.8.8.8:53" "example.com:443")

failures=0

# pass MESSAGE: records a holding assertion.
pass() {
  echo "  ok: $1"
}

# fail MESSAGE: records a broken assertion; the script exits 1 at the end.
fail() {
  failures=$((failures + 1))
  echo "  FAILED: $1" >&2
}

# compose ARGS...: runs Docker Compose on this project, with the vision profile and the integration overlay.
compose() {
  docker compose -p "$PROJECT" -f compose.yaml -f tests/integration/compose.override.yaml --profile local "$@"
}

# docker_path DIR: prints DIR in a form Docker accepts (a Windows path under Git Bash).
docker_path() {
  (cd "$1" && (pwd -W 2>/dev/null || pwd))
}

# cleanup: removes the containers, networks and volumes of this project, and the temporary folders.
cleanup() {
  compose down -v --remove-orphans --timeout 5 >/dev/null 2>&1
  rm -rf "${photos_dir:-}" "${exports_dir:-}"
}

# tcp_connect SERVICE HOST PORT: tries a TCP connection from inside SERVICE. Exit 0 means it connected;
# 124 is a timeout; 125 to 127 mean the probe itself could not run. The reason goes to stderr.
tcp_connect() {
  compose exec -T "$1" bash -c 'timeout 8 bash -c "exec 3<>/dev/tcp/$1/$2"' _ "$2" "$3"
}

# assert_reachable SERVICE HOST PORT: the positive control. It shows that the probe works in this image
# and that this network is up, so a later failure means blocked and not broken.
assert_reachable() {
  if tcp_connect "$1" "$2" "$3" 2>/dev/null; then
    pass "$1 reaches $2:$3 on the backend network"
  else
    fail "$1 cannot reach $2:$3 on the backend network, so its probe proves nothing"
  fi
}

# assert_unreachable SERVICE HOST PORT: fails when the connection succeeds or the probe could not run.
assert_unreachable() {
  local output status
  output="$(tcp_connect "$1" "$2" "$3" 2>&1)"
  status=$?
  if [ "$status" -eq 0 ]; then
    fail "$1 connected to $2:$3"
  elif [ "$status" -ge 125 ] && [ "$status" -le 127 ]; then
    fail "$1 could not run the probe for $2:$3 (exit $status): $output"
  else
    pass "$1 cannot connect to $2:$3 (exit $status: $(printf '%s' "$output" | tr -d '\r' | tail -n 1))"
  fi
}

# assert_internet_control SERVICE: the same probe, from the same image, on Docker's default network must
# reach one public endpoint. Otherwise the host is offline and the failures above would prove nothing.
assert_internet_control() {
  local container image target
  container="$(compose ps -q "$1" | tr -d '\r')"
  image="$(docker inspect --format '{{.Image}}' "$container" | tr -d '\r')"
  for target in "${INTERNET_TARGETS[@]}"; do
    if docker run --rm --entrypoint bash "$image" -c 'timeout 8 bash -c "exec 3<>/dev/tcp/$1/$2"' _ \
      "${target%:*}" "${target##*:}" >/dev/null 2>&1; then
      pass "control: the $1 image reaches $target outside the stack"
      return
    fi
  done
  fail "control: the $1 image reaches no public endpoint outside the stack, so the isolation is not proven"
}

# assert_write_refused SERVICE USER COMMAND: COMMAND must fail with EROFS. Any other reason is a failure,
# because a permission error would not show that the mount itself is read-only.
assert_write_refused() {
  local output
  if output="$(compose exec -T -u "$2" "$1" sh -c "$3" 2>&1)"; then
    fail "$1 as user $2 succeeded in: $3"
  elif [[ "$output" == *"Read-only file system"* ]]; then
    pass "$1 as user $2 refused with a read-only error: $3"
  else
    fail "$1 as user $2 failed for another reason on '$3': $output"
  fi
}

# assert_read_only_mount SERVICE PATH: the path must be a separate mount, flagged ro. A missing mount would
# also give EROFS here, from the read-only root filesystem, so this is checked apart.
assert_read_only_mount() {
  local line
  line="$(compose exec -T "$1" grep " $2 " /proc/self/mounts 2>/dev/null | tr -d '\r')"
  if [[ "$line" =~ ^[^\ ]+\ $2\ [^\ ]+\ ro(,|\ ) ]]; then
    pass "$1 mounts $2 read-only"
  else
    fail "$1 has no read-only mount at $2: ${line:-no such mount}"
  fi
}

# published_ports SERVICE: prints the host bindings of the service as `ip:port` words; empty when none.
published_ports() {
  local container
  container="$(compose ps -q "$1" | tr -d '\r')"
  # shellcheck disable=SC2016 # A Go template, not a shell expansion.
  docker inspect --format \
    '{{range $port, $bindings := .NetworkSettings.Ports}}{{range $bindings}}{{.HostIp}}:{{.HostPort}} {{end}}{{end}}' \
    "$container" | tr -d '\r'
}

# assert_http_status EXPECTED LABEL CURL_ARGS...: requests /health through the published port.
assert_http_status() {
  local expected="$1" label="$2" code
  shift 2
  code="$(curl -s --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' "$@" "http://127.0.0.1:$port/health")"
  if [ "$code" = "$expected" ]; then
    pass "gateway answers $expected for $label"
  else
    fail "gateway answered $code for $label, expected $expected"
  fi
}

trap cleanup EXIT

photos_dir="$(mktemp -d)" || exit 2
exports_dir="$(mktemp -d)" || exit 2
printf '%s' "$MARKER_TEXT" > "$photos_dir/$MARKER_NAME"
# World-writable on purpose: the host folder allows the write, so only the mount can refuse it.
chmod 777 "$photos_dir" "$exports_dir"
chmod 666 "$photos_dir/$MARKER_NAME"

port="${AURAFRAME_INTEGRATION_PORT:-18420}"
token="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"

export AURAFRAME_API_TARGET=core
export AURAFRAME_PORT="$port"
export AURAFRAME_API_TOKEN="$token"
AURAFRAME_PHOTOS_DIR="$(docker_path "$photos_dir")"
AURAFRAME_EXPORTS_DIR="$(docker_path "$exports_dir")"
export AURAFRAME_PHOTOS_DIR AURAFRAME_EXPORTS_DIR

echo "Starting the stack (project $PROJECT)"
if ! compose up -d --build --wait --wait-timeout 240 --quiet-pull; then
  compose ps -a
  compose logs --tail 40
  echo "FAILED: the stack did not become healthy" >&2
  exit 1
fi

echo "Services run as non-root"
for service in gateway api vision; do
  uid="$(compose exec -T "$service" id -u | tr -d '\r')"
  if [ -n "$uid" ] && [ "$uid" != "0" ]; then
    pass "$service runs as uid $uid"
  else
    fail "$service runs as uid '${uid}'"
  fi
done

echo "No internet from the isolated services"
assert_reachable api gateway 8080
assert_reachable vision gateway 8080
assert_reachable vision api 8000
for target in "${INTERNET_TARGETS[@]}"; do
  assert_unreachable api "${target%:*}" "${target##*:}"
  assert_unreachable vision "${target%:*}" "${target##*:}"
done
assert_internet_control api
assert_internet_control vision

echo "Photos cannot be written"
assert_read_only_mount api /photos
marker="$(compose exec -T api cat "/photos/$MARKER_NAME" | tr -d '\r')"
if [ "$marker" = "$MARKER_TEXT" ]; then
  pass "api reads the synthetic file in /photos"
else
  fail "api cannot read the synthetic file in /photos"
fi
for user in 10001 0; do
  assert_write_refused api "$user" "touch /photos/probe"
  assert_write_refused api "$user" "mkdir /photos/probe-dir"
  assert_write_refused api "$user" "echo changed >> /photos/$MARKER_NAME"
  assert_write_refused api "$user" "rm -f /photos/$MARKER_NAME"
done
if [ "$(ls -A "$photos_dir")" = "$MARKER_NAME" ] && [ "$(cat "$photos_dir/$MARKER_NAME")" = "$MARKER_TEXT" ]; then
  pass "the synthetic folder on the host is unchanged"
else
  fail "the synthetic folder on the host changed"
fi

echo "Storage"
if compose exec -T api sh -c 'touch /data/probe && rm /data/probe'; then
  pass "api writes to the data volume as its own user"
else
  fail "api cannot write to the data volume"
fi
assert_read_only_mount api /models
assert_read_only_mount vision /models
assert_write_refused api 0 "touch /models/probe"
assert_write_refused vision 0 "touch /models/probe"
assert_write_refused api 0 "touch /etc/probe"
assert_write_refused vision 0 "touch /etc/probe"
assert_write_refused gateway 0 "touch /etc/probe"

echo "Published ports"
gateway_ports="$(published_ports gateway)"
if [ "${gateway_ports% }" = "127.0.0.1:$port" ]; then
  pass "gateway publishes only 127.0.0.1:$port"
else
  fail "gateway publishes '${gateway_ports}'"
fi
for service in api vision; do
  ports="$(published_ports "$service")"
  if [ -z "$ports" ]; then
    pass "$service publishes nothing"
  else
    fail "$service publishes '${ports}'"
  fi
done

echo "No Docker socket"
for service in gateway api vision; do
  if compose exec -T "$service" sh -c 'test ! -e /var/run/docker.sock'; then
    pass "$service has no Docker socket"
  else
    fail "$service has a Docker socket"
  fi
done

echo "Gateway"
assert_http_status 200 "the session token" -H "Authorization: Bearer $token"
assert_http_status 401 "a request without the token"
assert_http_status 403 "a foreign Host" -H "Authorization: Bearer $token" -H "Host: rebinding.example"

echo "Vision image"
if compose exec -T vision /app/llama-server --version >/dev/null 2>&1; then
  pass "llama-server starts as the service user on the read-only filesystem"
else
  fail "llama-server does not start as the service user"
fi
if compose exec -T vision curl --version >/dev/null 2>&1; then
  pass "the tool of the vision health check exists"
else
  fail "the vision health check has no curl"
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures assertion(s) failed" >&2
  exit 1
fi
echo "All assertions hold"

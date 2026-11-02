#!/usr/bin/env bash
# Runtime proof of the path without the window: `./start.sh --backend-only` on a synthetic folder, then a `fast`
# run through the gateway with the session token, until the engine reports done and the photos are listed.
#
# Exits 0 only when every assertion holds. It uses the real project stack and its volumes, with a temporary
# synthetic photo folder, a temporary exports folder and a fixed synthetic library id. At exit it deletes that
# library's generated data through the API and stops the services with `./start.sh --stop`, which keeps the
# volumes. So it also stops services that were running before it started. It reads no personal folder and
# downloads no model: it starts with `--cpu --provider none`, and the images are drawn by a throwaway container of
# the api image with no network.

set -u

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 2

# Stops Git Bash rewriting the container paths in the commands below.
export MSYS_NO_PATHCONV=1

readonly LIBRARY_ID="exit-check-synthetic"
readonly IMAGE_COUNT=6
readonly RUN_TIMEOUT_SECONDS=480
readonly TOKEN_FILE="data/session-token"
# Draws IMAGE_COUNT textured colour images of 1200x900 from a fixed seed: large, sharp and mid-bright enough
# for the sanity gate, and depicting nobody.
readonly DRAW_IMAGES='
import sys
import numpy as np
from PIL import Image
count = int(sys.argv[1])
rng = np.random.default_rng(20261102)
height, width = 900, 1200
y, x = np.mgrid[0:height, 0:width]
for index in range(count):
    base = np.stack([(x * (index + 1)) % 256, (y * 2 + index * 40) % 256, ((x + y) // 3 + index * 25) % 256], axis=-1)
    noise = rng.integers(0, 60, size=(height, width, 3))
    pixels = np.clip(base * 0.6 + noise + 30, 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(f"/out/synthetic_{index:02d}.jpg", quality=90)
'

failures=0
started=0

# pass MESSAGE: records a holding assertion.
pass() {
  echo "  ok: $1"
}

# fail MESSAGE: records a broken assertion; the script exits 1 at the end.
fail() {
  failures=$((failures + 1))
  echo "  FAILED: $1" >&2
}

# docker_path DIR: prints DIR in a form Docker accepts (a Windows path under Git Bash).
docker_path() {
  (cd "$1" && (pwd -W 2>/dev/null || pwd))
}

# api METHOD PATH [CURL_ARGS...]: calls the gateway with the session token, read by curl from stdin so it is not
# on a command line. Prints the body, then a last line with the HTTP status.
api() {
  local method="$1" route="$2"
  shift 2
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl -s --noproxy '*' --max-time 60 -K - -X "$method" -w '\n%{http_code}' "$@" \
      "http://127.0.0.1:${AURAFRAME_PORT}${route}"
}

# status_of RESPONSE: prints the HTTP status, the last line of an `api` response.
status_of() {
  printf '%s' "$1" | tail -n 1
}

# body_of RESPONSE: prints the body of an `api` response, without its status line.
body_of() {
  printf '%s' "$1" | sed '$d'
}

# fingerprint DIR: prints the name, size and checksum of every file in DIR, sorted, to show it did not change.
fingerprint() {
  (cd "$1" && find . -type f -print0 | sort -z | xargs -0 cksum)
}

# cleanup: removes the synthetic library's data, stops the services and removes the temporary folders.
cleanup() {
  if [ "$started" -eq 1 ]; then
    api DELETE "/api/libraries/$LIBRARY_ID/data" >/dev/null 2>&1
    ./start.sh --stop >/dev/null 2>&1
  fi
  rm -rf "${photos_dir:-}" "${exports_dir:-}"
}

trap cleanup EXIT

photos_dir="$(mktemp -d)" || exit 2
exports_dir="$(mktemp -d)" || exit 2
# The api user (uid 10001) must write the exports on a Linux host; Docker Desktop ignores the mode.
chmod 777 "$exports_dir"
AURAFRAME_PHOTOS_DIR="$(docker_path "$photos_dir")"
AURAFRAME_EXPORTS_DIR="$(docker_path "$exports_dir")"
export AURAFRAME_PHOTOS_DIR AURAFRAME_EXPORTS_DIR
export AURAFRAME_PORT="${AURAFRAME_PORT:-8420}"

echo "Starting the services with ./start.sh --backend-only --cpu --provider none"
started=1
if ! ./start.sh --backend-only --cpu --provider none; then
  echo "FAILED: ./start.sh --backend-only did not start the services" >&2
  exit 1
fi

token="$(tr -d '\r\n' <"$TOKEN_FILE" 2>/dev/null)"
if [[ "$token" =~ ^[0-9a-f]{64}$ ]]; then
  pass "start.sh wrote a 64-hex session token to $TOKEN_FILE"
else
  echo "FAILED: no valid session token in $TOKEN_FILE" >&2
  exit 1
fi

echo "Synthetic folder"
api_container="$(docker compose -f compose.yaml ps -q api | tr -d '\r')"
api_image="$(docker inspect --format '{{.Image}}' "$api_container" | tr -d '\r')"
if docker run --rm --network none --entrypoint python -v "$AURAFRAME_PHOTOS_DIR:/out" "$api_image" \
  -c "$DRAW_IMAGES" "$IMAGE_COUNT"; then
  pass "drew $IMAGE_COUNT synthetic images in the mounted folder"
else
  echo "FAILED: the synthetic images could not be drawn" >&2
  exit 1
fi
before="$(fingerprint "$photos_dir")"

echo "Fast run through the API"
response="$(api POST "/api/libraries/$LIBRARY_ID/runs" -H 'Content-Type: application/json' \
  -d '{"preset":"fast","runGemma":false,"includeSubfolders":true}')"
if [ "$(status_of "$response")" = "202" ]; then
  pass "POST runs answered 202"
else
  echo "FAILED: POST runs answered $(status_of "$response"): $(body_of "$response")" >&2
  exit 1
fi

deadline=$((SECONDS + RUN_TIMEOUT_SECONDS))
current=""
while [ "$SECONDS" -lt "$deadline" ]; do
  current="$(body_of "$(api GET "/api/libraries/$LIBRARY_ID/runs/current")")"
  if [[ "$current" == *'"running":false'* ]]; then
    break
  fi
  sleep 3
done
echo "  final status: $current"
if [[ "$current" == *'"step":"done"'* ]] && [[ "$current" == *'"running":false'* ]]; then
  pass "the run ended with step done"
else
  fail "the run did not end with step done within ${RUN_TIMEOUT_SECONDS} s"
fi

events="$(body_of "$(api GET "/api/libraries/$LIBRARY_ID/events")")"
if printf '%s' "$events" | grep -Eq '"type": ?"done"'; then
  pass "the event stream replays a done event"
else
  fail "the event stream has no done event"
fi
if printf '%s' "$events" | grep -Eq '"type": ?"error"'; then
  fail "the event stream has an error event: $(printf '%s' "$events" | grep -E '"type": ?"error"' | head -n 1)"
else
  pass "the event stream has no error event"
fi

photos="$(api GET "/api/libraries/$LIBRARY_ID/photos")"
listed="$(body_of "$photos" | grep -oE 'IMG_[0-9A-F]{10}' | sort -u | wc -l | tr -d ' ')"
if [ "$(status_of "$photos")" = "200" ] && [ "$listed" = "$IMAGE_COUNT" ]; then
  pass "GET photos lists the $IMAGE_COUNT synthetic photos by ID"
else
  fail "GET photos answered $(status_of "$photos") with $listed photo IDs, expected $IMAGE_COUNT"
fi

if [ "$(fingerprint "$photos_dir")" = "$before" ]; then
  pass "the synthetic folder is unchanged after the run"
else
  fail "the synthetic folder changed during the run"
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures assertion(s) failed" >&2
  exit 1
fi
echo "All assertions hold"

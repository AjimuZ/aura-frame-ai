"""Container health probe: exits 0 only when the API on loopback answers /health with the session token."""

import os
import sys
import urllib.request

HEALTH_URL = "http://127.0.0.1:8000/health"
TIMEOUT_SECONDS = 3


# Returns the process exit code: 0 for an HTTP 200, 1 for any refusal or connection failure.
def main() -> int:
    token = os.environ.get("AURAFRAME_API_TOKEN", "")
    request = urllib.request.Request(HEALTH_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())

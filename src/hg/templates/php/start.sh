#!/usr/bin/env bash
# Replace with the real start command. Runs as ctf after the entrypoint drops
# privileges. Listen on the port declared in challenge.yaml.
set -euo pipefail

exec php -S "0.0.0.0:${HG_WEB_PORT:-8080}" -t /app

#!/usr/bin/env bash
# Replace with the real start command. Runs as ctf after the entrypoint drops
# privileges. Listen on the port declared in challenge.yaml.
set -euo pipefail

exec java ${HG_JAVA_OPTS:-} -jar /app/app.jar

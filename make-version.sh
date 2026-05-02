#!/bin/bash
#
# Capture the current git short hash and commit date into version.txt.
# Run this before `docker compose build` so the running server can show
# its build identity in the settings page.
#
# Format:
#   <short hash>
#   <YYYY-MM-DD>
#
# Falls back to "unknown" lines if not in a git checkout.

set -e
cd "$(dirname "$0")"

if git rev-parse --short HEAD >/dev/null 2>&1; then
    git rev-parse --short HEAD > version.txt
    git log -1 --format=%cd --date=short >> version.txt
else
    echo "unknown" > version.txt
    echo "unknown" >> version.txt
fi

echo "Wrote version.txt:"
cat version.txt

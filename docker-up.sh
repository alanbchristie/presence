#!/usr/bin/env bash
#
# Build and start the presence container with the application VERSION baked in.
#
# The image has no .git inside it, so settings.py cannot resolve the version
# from git at runtime (as a local `runserver` checkout does); it must be passed
# as a build arg. This script computes that value the same way CI does — an
# exact git tag if HEAD is tagged, else the short commit SHA — and runs build +
# up with it. An existing VERSION in the environment is respected and wins.
#
# Any arguments are forwarded to `docker compose` ahead of the subcommand, so
# compose options work as usual, e.g.:
#
#   ./docker-up.sh                 # plain HTTP on :8000
#   ./docker-up.sh --profile tls   # + Caddy TLS sidecar on :443
#
set -euo pipefail

cd "$(dirname "$0")"

VERSION="${VERSION:-$(git describe --tags --exact-match 2>/dev/null \
    || git rev-parse --short HEAD 2>/dev/null \
    || echo dev)}"
export VERSION

echo "Building presence VERSION=${VERSION}"
docker compose "$@" build
docker compose "$@" up -d

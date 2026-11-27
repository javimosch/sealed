#!/usr/bin/env bash
# Build the sealed binary (CLI + daemon). Needs machin, a C compiler, libsqlite3, libssl.
set -euo pipefail
cd "$(dirname "$0")"
MACHIN="${MACHIN:-machin}"
"$MACHIN" encode src/errors.src src/store.src src/server.src src/client.src src/spec.src src/main.src > sealed.mfl
"$MACHIN" build sealed.mfl --cflags "-I$PWD/src" -o sealed
echo "built ./sealed" >&2

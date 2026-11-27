#!/usr/bin/env bash
# Builds ./sealed (CLI + daemon): machin 0.146.0+, a C compiler, libsqlite3, libssl.
# Also builds ./sealed-dialog (the desktop windows) when the GTK 3 development
# files are present. SEALED_NO_GUI=1 skips it.
set -euo pipefail
cd "$(dirname "$0")"
MACHIN="${MACHIN:-machin}"
have="$("$MACHIN" guide | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | sed -n 1p)"
# older machin does not copy strings returned by C: sealed would misbehave
if [ "$(printf '%s\n0.146.0\n' "$have" | sort -V | sed -n 1p)" != "0.146.0" ]; then
  echo "error: machin 0.146.0 or later is required (found ${have:-unknown})" >&2; exit 1
fi
"$MACHIN" encode src/errors.src src/store.src src/server.src src/client.src src/spec.src src/main.src > sealed.mfl
"$MACHIN" build sealed.mfl --cflags "-I$PWD/src" -o sealed
echo "built ./sealed" >&2
rm -f sealed-dialog   # never leave an older window program beside a new build
if [ "${SEALED_NO_GUI:-0}" = 1 ]; then
  echo "skipped ./sealed-dialog (SEALED_NO_GUI=1)" >&2
elif pkg-config --exists gtk+-3.0 2>/dev/null; then
  "$MACHIN" encode src/dialog.src > dialog.mfl
  "$MACHIN" build dialog.mfl --cflags "$(pkg-config --cflags gtk+-3.0)" -o sealed-dialog
  echo "built ./sealed-dialog" >&2
else
  echo "skipped ./sealed-dialog: no GTK 3 development files; request and unlock --dialog will not work" >&2
fi

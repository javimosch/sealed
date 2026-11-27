#!/usr/bin/env bash
# Build the sealed binary (CLI + daemon). Needs machin, a C compiler, libsqlite3, libssl.
set -euo pipefail
cd "$(dirname "$0")"
MACHIN="${MACHIN:-machin}"
# embed the desktop window script as a C string
/usr/bin/env python3 - <<'PY' > src/dialog_py.h
lines = open("src/dialog.py", encoding="ascii").read().splitlines()
esc = lambda l: l.replace("\\", "\\\\").replace('"', '\\"')
print("/* generated from dialog.py by build.sh */")
print("static const char sh_dialog_py[] =")
for l in lines:
    print('    "' + esc(l) + '\\n"')
print("    ;")
PY
"$MACHIN" encode src/errors.src src/store.src src/server.src src/client.src src/spec.src src/main.src > sealed.mfl
"$MACHIN" build sealed.mfl --cflags "-I$PWD/src" -o sealed
echo "built ./sealed" >&2

#!/usr/bin/env bash
# A two-minute tour in a throwaway store with a FAKE secret. Run it in a terminal:
# you will choose a throwaway passphrase. It does not touch ~/.sealed.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x ./sealed ] || ./build.sh
tmp=$(mktemp -d)
export SEALED_HOME="$tmp/home" SEALED_PORT="${DEMO_PORT:-18744}"
unset SEALED_TOKEN SEALED_TOKEN_FILE SEALED_DB SEALED_HOST SEALED_URL
started=0
cleanup() { if [ "$started" = 1 ]; then ./sealed daemon stop >/dev/null || true; fi; rm -rf "$tmp"; }
trap cleanup EXIT
say() { printf '\n\033[1m# %s\033[0m\n' "$*" >&2; }

if ./sealed daemon status >/dev/null 2>&1; then
  echo "a sealed daemon already answers on port $SEALED_PORT; set DEMO_PORT" >&2; exit 1
fi
say "create a store (choose a throwaway passphrase, 12+ characters)"; ./sealed init
say "start the daemon (it starts locked), then unlock it"; ./sealed daemon start; started=1; ./sealed unlock
say "store a fake secret"; printf %s "fake-key-1234567890" | ./sealed put demo/api-key
say "list: names, never values"; ./sealed ls
say "use it: the command gets the value, the output is redacted"
./sealed exec --env API_KEY=demo/api-key -- sh -c 'echo "key=$API_KEY (${#API_KEY} characters)"'
say "THE LIMIT: a command that transforms the value defeats redaction"
./sealed exec --env API_KEY=demo/api-key -- sh -c 'printf %s "$API_KEY" | rev; echo'
say "replacing a value needs your passphrase"; printf %s "fake-key-new" | ./sealed put demo/api-key
say "who did what"; ./sealed audit

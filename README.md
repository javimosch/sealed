# sealed

[Website](https://javimosch.github.io/sealed/)

A small self-hosted secrets store with limited agent tokens, built in
[machin](https://github.com/javimosch/machin).

> **Experimental. No independent security audit. Not for production secrets.**
> `sealed` helps agents use secrets without *accidentally* exposing them. It does
> **not** stop an agent that is deliberately trying to get them.

```console
$ sealed exec --env API_KEY=demo/api-key -- sh -c 'echo "key=$API_KEY"'
key=***
$ sealed exec --env API_KEY=demo/api-key -- sh -c 'printf %s "$API_KEY" | rev'
0987654321-yek-ekaf          # <- the limit, in one line
```

## Ask for a secret without pasting it into chat

```sh
sealed request github/token --reason "to open pull requests"
```

An agent runs this; a desktop window says **"Enter value for secret github/token"**,
shows the reason (marked as unverified), and you paste the value. The agent only
learns that it was stored. Every `sealed` window has a **"Show what I type"**
checkbox, unchecked by default; only the person at the window can tick it.

- creates **new** secrets only; the daemon enforces it, even if the name is created
  while the window is open;
- needs a token that covers the name, and checks everything before opening a window;
- while hidden, the value must be repeated in a second field; when shown, it is
  entered once;
- never asks for your passphrase, and the window says not to type it;
- one window at a time; cancel or 3 minutes without an answer stores nothing (give
  the command a timeout of at least 4 minutes);
- needs a desktop session and `sealed-dialog` (GTK 3) next to `sealed`. Otherwise it exits 93 and suggests
  `sealed put`. Multi-line values must come from a file.

This keeps the value out of the chat, **not out of the agent's reach**: the same
token can use it with `exec`. A program on your desktop can fake such a window or
record your typing, so only enter values you expected to be asked for. "Stored"
does not prove that a person typed the value.

### Unlock from a desktop window

`sealed unlock --dialog` opens a window headed **"Enter sealed master password to
UNLOCK"**, so an agent can ask you to unlock. Input is hidden unless you tick the
checkbox (anyone, or any program, that can see your screen can then read it). One
attempt per window, and no window opens if it is already unlocked. `sealed` reads the
window itself; the password is not in the command's output.

**This is less careful than terminal unlock.** A program on your desktop can
imitate the window (and the caller can replace it by setting `SEALED_DIALOG`) or
record your typing. If you type your password into a
window an agent opened, do not assume the agent cannot get it. Approvals and
reveal have no dialog.

## What it does not protect against

`exec` and `render` run where the caller is, so the daemon sends **plaintext to the
client**. A process holding a token can get any **in-scope** value:

- a command that transforms the value defeats redaction;
- `render` writes the value to a file the caller owns;
- `POST /v1/resolve` with the token returns it directly.

The test suite asserts the first and third.

**An agent running as your user can also read your own token** in
`~/.sealed/token`, which covers every secret, and `sealed` uses it whenever
`SEALED_TOKEN` is unset. Giving such an agent a scoped token does not restrict it.

A process running as the same user can also stop the daemon and imitate it to
capture a typed passphrase, delay `unlock` by exhausting the passphrase throttle
(5 failures per minute), and, with write access to the database, delete rows or
restore old ones (un-revoking a token or reusing a spent approval). Edited and
forged token or approval rows are detected. The audit triggers only prevent
accidental changes. Root can do all of this and read the daemon's memory.

## What it is good for

- keeping keys out of chat transcripts, argv, shell history and logs;
- storing them encrypted instead of in `.env` files;
- limiting a token to a group of secrets, where the holder cannot read your token;
- asking you before a secret is replaced or deleted through `sealed`;
- seeing which token used what.

## Try it

Linux x86_64. Needs [machin](https://github.com/javimosch/machin) **0.146.0 or
later** (`build.sh` checks), a C compiler, and the SQLite and OpenSSL 3
development packages. `curl` is needed for `update`.

`request` and `unlock --dialog` use a second program, `sealed-dialog`.
`./build.sh` builds it when the GTK 3 development files are present
(`libgtk-3-dev`) and skips it otherwise, or with `SEALED_NO_GUI=1`. `sealed`
itself does not link GTK. It runs only the `sealed-dialog` in its own directory,
never one found on `PATH`; `SEALED_DIALOG` replaces it. `sealed install` and
`uninstall` handle both. `sealed update` replaces only `sealed`.

```sh
./scripts/demo.sh        # throwaway store, fake secret, includes the limit
```

## Use

```sh
./build.sh && ./sealed install       # to ~/.local/bin
sealed init                          # in a terminal: choose a passphrase
sealed daemon start                  # starts LOCKED, also after every restart
sealed unlock

sealed put svc/api-key               # asks twice, input hidden
sealed put svc/ssh-key < keyfile     # multi-line or long values
sealed ls                            # names, never values
sealed exec --env API_KEY=svc/api-key -- ./job.sh
sealed exec --file KEY_FILE=svc/ssh-key -- sh -c 'ssh -i "$KEY_FILE" host'
sealed get svc/api-key --reveal      # needs the passphrase
```

Paste with your terminal's paste (often `Ctrl+Shift+V`). If you pasted from tmux,
delete the buffer afterwards (`tmux delete-buffer`).

**Agent tokens.** Creating one asks for your passphrase and prints it once:

```sh
sealed token create --kind agent --scope svc/ --rate 60
SEALED_TOKEN=sld_... sealed ls       # in the agent's environment
```

When an agent's replace or delete returns exit 85, run the suggested
`sealed approve ap_...`; the agent then repeats the same command within 10 minutes
of its first attempt. `sealed guide` and `sealed help-json` describe everything,
offline.

## Design

| | |
|---|---|
| Values | AES-256-GCM, one data key per version; AAD binds name and version |
| Master key | Argon2id (t=3, 64 MiB, p=1); not written to disk; kept in mlock'd memory and wiped on lock (short-lived working copies are not wiped) |
| Integrity | token and approval rows carry an HMAC under a key derived from the master key |
| Your token | alone, it cannot reveal, overwrite, delete or mint through those commands (it **can** read values through `exec` and `render`) |
| Reveal | human token **and** passphrase at a terminal; never for agent tokens |
| Approvals | single use, 10 minutes, bound to the exact operation; the passphrase is required |
| Daemon | loopback by default, one request at a time, refuses browser and foreign-Host requests; accepts passphrases from loopback peers only |

The client refuses plain HTTP to a non-loopback daemon unless
`SEALED_ALLOW_PLAINTEXT=1`. Use an SSH tunnel for remote use, which is untested.

## What this shows about machin

One program is both the CLI and the daemon: **about 1,700 lines of MFL and 400
lines of C**. It compiles to a stripped binary of about 220 kB, dynamically linked
to glibc, OpenSSL and SQLite.

**The desktop window is MFL too:** [`src/dialog.src`](src/dialog.src), about 200
lines calling GTK 3 directly, with **no C file**. It builds to a second binary of
about 23 kB that links GTK. Two things made that work:

- **No FFI callbacks in machin yet**, so nothing is connected to a GTK signal. The
  program runs the event loop itself and reads widget state every 20 ms. Buttons
  are toggle buttons, so a click stays visible until it is read.
- **What you type never becomes an MFL string.** `gtk_entry_get_text` is declared
  as returning a `ptr`; C functions compare, scan and print GTK's own buffer. GTK
  and the C library still handle the text, so this is not a guarantee that no copy
  remains in memory.

**From machin's built-ins:** Argon2id, AES-256-GCM, HKDF, HMAC, secure random
bytes, SQLite, TCP sockets, an HTTP client, JSON, and arenas, which free each
daemon request's memory. The daemon has its own small HTTP server and handles one
request at a time, so it shares no state between goroutines.

**In C through the FFI** ([`src/shim.h`](src/shim.h)): hidden terminal input,
running a command without a shell, streaming redaction, starting the window
program, private temporary files,
locked memory and process hardening. This is the most security-sensitive code, so
review it first.

If you write a shim: since machin 0.146.0 a `string` returned by a C function is
copied, so returning `malloc`'d memory leaks it. This shim returns one reusable
buffer and wipes it as soon as MFL has taken a sensitive value. Copies inside MFL
are released with their arena but are not zeroed, so `lock` does not guarantee
that no trace remains in memory.

**Built for agents:** JSON output and typed errors with suggested next steps,
following the [agent-first CLI specs](https://cli-specs.intrane.fr). With
`cli-spec-conformance`: output and guide 19/19, daemon 9/9.

## Network calls

**No telemetry.** Besides your daemon, `sealed` connects out only when you ask:

- **feedback**, only when you run `sealed feedback`: sent to `feedback.intrane.fr`,
  run by the author (override with `FEEDBACK_RELAY`, or set it to `off`). Known
  names and credential-like words are masked, but **a short plain-word secret typed
  into a message would get through**. The command prints what it sent;
- **update**, only with `SEALED_SERVER` set. There is no default or release server.

## Known gaps

- Update has no passive notice, concurrency lock, `VERSION` file or installer.
- No `export`, master-key rotation, replication, read-only failover, remote
  approval or MCP server.

Other tools cover similar ground, with more features in places. This project is
mainly a demonstration of machin.

## Test

```sh
./build.sh && python3 tests/test_sealed.py    # real daemon, real pseudo-terminal
xvfb-run -a /usr/bin/python3 tests/test_dialog.py    # real window, simulated keys
xvfb-run -a /usr/bin/python3 tests/test_window_e2e.py   # sealed + real window
```

MIT license. See [SECURITY.md](SECURITY.md) to report a vulnerability.

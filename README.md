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

Linux x86_64. Needs [machin](https://github.com/javimosch/machin) (tested with
0.145.0), a C compiler, and the SQLite and OpenSSL 3 development packages. `curl`
is needed for `update`.

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

One program is both the CLI and the daemon: **about 1,500 lines of MFL and 350
lines of C**. It compiles to a stripped binary of about 200 kB, dynamically linked
to glibc, OpenSSL and SQLite, in under 3 seconds on the development machine.

**From machin's built-ins:** Argon2id, AES-256-GCM, HKDF, HMAC, secure random
bytes, SQLite, TCP sockets, an HTTP client, JSON, and arenas, which free each
daemon request's memory. The daemon has its own small HTTP server and handles one
request at a time, so it shares no state between goroutines.

**In C through the FFI** ([`src/shim.h`](src/shim.h)): hidden terminal input,
running a command without a shell, streaming redaction, private temporary files,
locked memory and process hardening. This is the most security-sensitive code, so
review it first.

One thing to know if you write a shim: machin 0.145.0 does not copy a string
returned by a C function, and does not free it. A static buffer is aliased by the
next call, so either return a fresh heap copy (which stays until the process
exits), or consume the buffer immediately. The shim does the latter for the master
key. Working copies made inside MFL are released with the request's arena but are
not zeroed, so `lock` does not guarantee that no trace remains in memory.

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
```

MIT license. See [SECURITY.md](SECURITY.md) to report a vulnerability.

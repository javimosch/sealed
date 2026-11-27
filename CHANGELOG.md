# Changelog

`sealed` is experimental, has had no independent security audit, and does not stop
an agent that is deliberately trying to get a secret.

## 0.3.0 — 2026-11-27

### Changed
- The desktop window is now written in MFL (`src/dialog.src`), calling GTK 3
  directly, in place of the embedded Python script. Python is no longer needed to
  build or run `sealed`.
- It is a second program, `sealed-dialog`, which `sealed` looks for only in its
  own directory. `install` and `uninstall` handle both; `update` replaces only
  `sealed`.
- What is typed in a window never becomes an MFL string.
- **machin 0.146.0 or later is required**, and `build.sh` checks it.

### Fixed
- The new window applies its state after every event. Without this (found by CI
  before the release), keys queued while it was busy could leave Enter ignored, or
  leave OK pressed so that it submitted later by itself.
- With machin 0.146.0, which copies strings returned by C, the C shim would have
  left unfreed, unwiped copies of typed and piped values. It now returns one
  buffer and wipes it.

### Upgrading
Install GTK 3 development files (`libgtk-3-dev`) for the windows, rebuild, and run
`./sealed install` again so both programs are placed together.

## 0.2.0 — 2026-11-27

### Added
- `sealed request <name> [--reason ...]`: an agent asks, and a person types a new
  secret in a desktop window. It creates new secrets only.
- `sealed unlock --dialog`: unlock from a desktop window. This is less careful than
  terminal unlock, because a window can be imitated.
- A "Show what I type" checkbox on every window, off by default. The windows need
  GTK 3 and `python3-gi`.
- Exit code 93 (`no_dialog`).

### Fixed
- `guide` now warns that without `SEALED_TOKEN` or `SEALED_TOKEN_FILE`, the
  all-access token in `~/.sealed/token` is used. It also covers token setup, a
  stopped daemon, and the limits of feedback masking.
- `help-json` lists `SEALED_DIALOG`.

### Upgrading
Rebuild, then restart the daemon and unlock it again. The store format is
unchanged.

## 0.1.0 — 2026-11-27

First public version (commit `923b744`, not tagged).

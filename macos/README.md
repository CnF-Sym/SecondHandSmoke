# Packaging Second Hand Smoke for macOS

This folder turns the project into a real Mac application: double-click, no
Terminal, no visible virtualenv step, no pip install on a shoot morning.

## How it is put together

The application is two pieces that already existed, joined:

- **The server.** PyInstaller freezes `server_main.py` — the same Flask app,
  the same offload, verification and format-gate code — into
  `Contents/Resources/server/`, together with Python itself, Flask, xxhash,
  NumPy and Pillow. Nothing has to be installed on the operator's machine.
- **The shell.** `SecondHandSmokeApp.swift` is a Cocoa and WebKit wrapper. It
  reserves a local port, launches the server, waits for it to accept
  connections, points a `WKWebView` at `127.0.0.1`, and shuts the server down
  again on quit. No Electron, no node, nothing listening off the machine.

The wrapper makes no decisions about media. Cards, copies, checksums, the
two-drive rule and the format gate stay entirely in the Python that has been
through real shoot days.

Three details worth knowing, because each is a bug that was going to happen:

- **The port is reserved, not assumed.** The wrapper asks the kernel for 3838
  and takes whatever is free if that is busy, so two copies cannot fight over
  one port and neither silently attaches to the other's server.
- **The server outlives nothing.** It is terminated on quit, and it also
  watches its parent: if the window process is force-quit or crashes, the
  server notices and exits rather than lingering with the catalog open.
- **One server per catalog.** `server_main.py` holds an exclusive lock beside
  the catalog. Two servers sharing one SQLite file, near a format gate, is not
  a race worth having.

## Local build

On an Apple Silicon Mac running macOS 13 or newer:

1. Install the Xcode Command Line Tools: `xcode-select --install`.
2. Double-click **Build Second Hand Smoke.command** in the project folder.
3. Open `dist/Second Hand Smoke.app`.

The build makes its own `macos/.build-venv`, so a failed build can never damage
the `.venv` you run the tool from.

An unsigned build is ad-hoc signed, which is enough to run on the machine that
built it — on Apple Silicon a binary with no signature at all will not launch.
The first open still needs **right-click → Open**, once.

## Release build

Set `CODESIGN_IDENTITY` to the exact name of your Developer ID Application
certificate, then double-click **Make Second Hand Smoke DMG.command**. For
notarization, also set `NOTARY_PROFILE` to an existing `notarytool` keychain
profile:

```bash
xcrun notarytool store-credentials "cnfworks" --apple-id ... --team-id ... --password ...
CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" \
NOTARY_PROFILE=cnfworks ./macos/make_dmg.sh
```

You get `dist/Second Hand Smoke.app`, `dist/SecondHandSmoke-<version>.dmg` and
a `.sha256` beside it. Test the DMG in a clean macOS account before publishing.
Notarization is the only thing that prevents the unidentified-developer warning
for people who download it.

## Permissions

The app is **not** sandboxed, and cannot be: a sandboxed application cannot
read an arbitrary camera card or write to an arbitrary drive, which is the job.
Access is governed by the ordinary macOS files-and-folders prompts, and the
reasons shown in those prompts live in `Info.plist`.

The one to watch is removable volumes. macOS asks once, per application. If it
is denied, cards do not produce an error — they appear to be empty. If a card
that is plainly mounted shows nothing, check **System Settings → Privacy &
Security → Files and Folders → Second Hand Smoke** before checking anything
else.

Running from Terminal via `start.command` inherits Terminal's permissions,
which is why this never came up before the app existed.

## ffmpeg

ffmpeg and ffprobe stay external and are deliberately **not** bundled. The app
finds them at `/opt/homebrew/bin`, `/usr/local/bin` or `/opt/local/bin` as well
as on `PATH`, because a Finder-launched app does not inherit a login shell's
`PATH` and would otherwise miss a perfectly good Homebrew install. Set
`DIT_FFMPEG` or `DIT_FFPROBE` to override.

Without ffmpeg: offload, verification, checksums, MHL manifests and the format
gate all work exactly as normal. Metadata, ProRes proxies, timecode sync, the
waveform fallback and dailies are unavailable.

## Version

`VERSION` at the project root is the only place the number lives. The build
script writes it into `Info.plist` with PlistBuddy, and `version.py` reads the
same file for the MHL `<tool>` field. Change it in one place.

## Optional icon

Put a finished `AppIcon.icns` at `macos/AppIcon.icns` before building. The
build script bundles it automatically and builds fine without it.

## Server log

Each launch writes `~/Library/Logs/Second Hand Smoke/server.log`, and the app
menu has **Reveal Server Log in Finder**. That is the file to ask for when
something goes wrong on someone else's machine.

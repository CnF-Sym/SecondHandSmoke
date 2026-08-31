# Second Hand Smoke v0.2.0 — CnF Works 2026

The version that stops being a script you run in Terminal.

## It is an application now

Double-click it. No Terminal, no virtualenv step on a shoot morning, no pip
install at 4am. Python, Flask, xxhash, NumPy, Pillow and reportlab all live
inside the bundle; nothing has to be installed on the machine.

Under the hood it is a Cocoa and WebKit shell around the same Flask server —
no Electron, no node, still nothing listening off the machine. The wrapper
reserves a free port rather than assuming 3838, holds a lock so two copies
cannot share one catalog, and exits the server if the window ever dies without
getting to shut it down properly.

Signing and notarization are optional environment variables, so an unsigned
local build still works. An unsigned build needs **right-click → Open** once.

**One thing to know:** macOS asks, once per application, before letting it read
a removable volume. Say yes — reading cards is the entire job. If you say no,
cards do not error, they appear to be empty. If a mounted card reads as blank,
check Privacy & Security → Files and Folders before checking anything else.

## Branded PDF reports

The day report can now be produced as a PDF with the production's name and
logo on it, in two layouts: **table**, the dense verification manifest — every
file, its size, its xxHash64 and its state on each destination drive — and
**metadata**, every clip with scene, take, duration, start timecode, format,
audio and proxy state.

The HTML report is unchanged and is not going anywhere. The PDF is an addition.

Both are generated from **one reading of the catalog**. The PDF writer cannot
query the database at all — it is handed the same structure the HTML report
was handed, including the same format-gate verdict, in the same call. Two
documents that could disagree about whether a card is safe to format is the one
bug this program exists to prevent, so they are not two code paths.

A logo can be picked with a file dialog, dropped onto the panel, or typed as a
path. Whichever way, it is decoded, checked to be a real image, re-encoded as
PNG and stored inside this program's own folder — so a logo cannot vanish
mid-shoot when the drive it came from is unmounted, and what goes into your
reports is a file this program wrote. The image you picked is only ever read.

reportlab is optional in the same way NumPy and Pillow are. Without it the PDF
buttons say so and everything else is untouched.

## The interface

- **The track.** A full-width rail under the header showing where the day is,
  with a count on every step and a plain sentence saying what to do next and
  why. It used to be six small chips in the top-right corner of the header.
- **Phases you cannot use yet are dimmed**, never hidden, and the coloured
  stripe on a panel now means "this phase is reachable" rather than being
  decoration.
- **Readable at 4am.** Every text colour in the palette now clears WCAG AA on
  every surface it is used on, and the test rig computes that rather than
  trusting it. The old hint colour sat at 2.9:1. Prose moved to the system
  face; paths, hashes and timecode keep monospace.
- **Calmer.** Background washes turned down, decorative glows halved, and the
  main button no longer pulses forever. Colour now means something when it
  appears.
- The application icon and the wordmark, by Brian.

## Everywhere you might need help

`hello@cnfworks.com` now appears in the interface footer, at the bottom of
every day report, inside the editorial handoff notes and in the README. The
report and the handoff notes get forwarded by people who were not in the room
when they were made, and neither used to say who to ask or which build wrote
it.

The footer also says what an absent ffmpeg does and does not break: offload,
verification, checksums, MHL manifests and the format gate are unaffected — a
card that clears is still safe to format.

## Nothing in the safety contract moved

Source media is still opened read-only. Every byte is still read back off each
destination with the macOS page cache bypassed, and verification still fails
rather than quietly weakening if that cannot be established. Two physically
distinct drives are still required. Interruption is still safe and resumable.
The tool still never formats, deletes or trashes anything, and still has no
telemetry, accounts or licence checks.

`test_rig.py` passes in full, including ten new sections covering the
application bundle, tool discovery, the report split, the PDF writer, the logo
handling, the branding assets and the interface itself.

# Roadmap

*Second Hand Smoke · A CnF Works Project*

## Where this is going

### Shipped in 0.4.0: the library

Every offload tool forgets your footage the moment the copy finishes. The
catalog always knew every file, every hash and every destination — now it
answers questions about them, across every shoot day at once.

- **Search across the whole show.** Project, day, roll, scene, take, camera,
  codec, clip name, date.
- **Drive registry** with stable identity and your own label, so *"which drive
  is Tuesday's A-cam on"* is a question with an answer.
- **Online / offline status**, without a re-scan.
- **Copy-count audit** — *"every clip that exists on fewer than three physical
  drives"*, in two honest modes: on record, and confirmed right now.
- **Export** to CSV or JSON, so the library survives this application.

### Next: a real card, and a way for people to get it

The code is ahead of the release. What is missing is not features.

- **Run it on real cards.** Every one of 521 checks runs against fixtures the
  test rig wrote. That proves the logic. It does not prove an FX9 card mounts
  the way the scanner expects, or that a 512 GB offload hits nothing a 24 KB
  fixture never could. This is the largest open question in the project and no
  amount of further code closes it.
- **Publish a release** people can download, with the manual and the rig's
  output beside it.
- **Camera and recorder coverage**, driven by what people actually send in.

### Still to come

- **Re-verify a shelved drive on demand**, per drive, from the library rather
  than from an open shoot day. The library says which copies are *on record*
  but unconfirmed; turning that into a real re-read job with a progress bar is
  the natural next step, and deliberately not something a cheap size check is
  ever allowed to stand in for.
- **Directory checksums.** ASC MHL 2.0 has `<directoryhash>`, so this is now
  reachable. It needs a design decision first: per-roll, not per-root, because
  a day folder is still being written to when a card clears.

### After that, probably

- A headless CLI, so the whole offload can run from a script or a cron job.
- Windows and Linux support. The blocker is honest: verification depends on
  macOS `F_NOCACHE` to bypass the page cache. `O_DIRECT` on Linux and
  `FILE_FLAG_NO_BUFFERING` on Windows are the equivalents, and each needs its
  own testing before the safety contract can claim to hold there. Until that
  work is done and tested, this stays macOS-only rather than shipping a weaker
  guarantee under the same name.

## Deliberately out of scope

These are not "not yet". They are "no".

- **Formatting, deleting or trashing anything.** The tool tells you a card is
  clear. You format it yourself, in Disk Utility, deliberately.
- **RAW playback, scopes, and look management.** ARRIRAW, REDCODE and X-OCN
  decoding needs vendor SDKs under licence, plus permanent per-camera
  maintenance. Silverstack does this well and charges accordingly. Buy it if
  you need it.
- **Cloud upload.** The "offsite" destination is just a folder. Point it at a
  mounted Dropbox, Backblaze or NAS folder and use a tool built for syncing.
- **Telemetry, accounts, licence checks, phoning home.** Ever.
- **Weakening verification to go faster.** If a verification method can't be
  proven to test the disk rather than the cache, it doesn't ship.

## How you can help

Verification bugs first — if this ever calls a card safe when it wasn't, stop
everything and open an issue. After that, camera and recorder coverage: send a
directory listing (names and sizes, no footage) from a card that isn't detected
properly and it can usually be fixed fast.

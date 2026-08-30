# Roadmap

*Second Hand Smoke · A CnF Works Project*

## Where this is going

### Next: the library

Every offload tool forgets your footage the moment the copy finishes. The
catalog already knows every file, every hash and every destination it was
written to — it just doesn't let you ask it anything after the shoot wraps.

Planned:

- **Search across the whole show**, not one day at a time. Project, day, roll,
  scene, take, camera, codec, date.
- **Drive registry.** Every destination gets a stable identity and a label you
  choose ("SHOW_A_01"). The catalog remembers which drive each verified copy
  lives on, so *"which drive is Tuesday's A-cam on"* is a question with an
  answer.
- **Online / offline status.** Plug a drive in and the library shows you what
  it holds and what it is missing, without a full re-scan.
- **Re-verify on demand, per drive.** Rot checking a shelved drive a year later
  is the same code path as `Re-read drives` today; it needs a home in the UI
  that isn't tied to an open shoot day.
- **Copy-count audit.** "Show me every clip that currently exists on fewer than
  three physical drives." That is the question that saves a show.

### After that, probably

- Camera and recorder coverage — more card layouts detected correctly out of
  the box, driven by what people actually send in.
- A headless CLI, so the whole offload can run from a script or a cron job.
- Export the library to CSV/JSON so it survives this application.
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

# Second Hand Smoke
## Second Hand Smoke — operator and technical manual

*A CnF Works Project*

Version 0.4.1 — read from the `VERSION` file at the repository root.
`version.py` and the macOS build scripts both read that one file, so the
number in the manual, in the app's Info.plist and in every MHL `<tool>`
field cannot drift apart.

Support: <hello@cnfworks.com>

> **On the name.** Second Hand Smoke is the product. FML is the File Management Library
> underneath it — this manual is the only place that expansion is written out,
> because by 4 am on a night shoot the acronym has usually acquired its second
> meaning and nobody needs it spelled out either way.

---

## Contents

1. [What this program is](#1-what-this-program-is)
2. [The safety contract](#2-the-safety-contract)
3. [How it is put together](#3-how-it-is-put-together)
4. [Installing and starting it](#4-installing-and-starting-it)
5. [Configuration](#5-configuration)
6. [The on-set workflow](#6-the-on-set-workflow)
7. [Phase 1 — Offload and verify](#7-phase-1--offload-and-verify)
8. [Phase 2 — Media metadata](#8-phase-2--media-metadata)
9. [Phase 3 — ProRes proxies](#9-phase-3--prores-proxies)
10. [Phase 4 — Timecode sync](#10-phase-4--timecode-sync)
11. [Phase 5 — Waveform fallback](#11-phase-5--waveform-fallback)
12. [Phase 6 — Editorial handoff](#12-phase-6--editorial-handoff)
13. [What it reads (imports)](#13-what-it-reads-imports)
14. [What it writes (exports)](#14-what-it-writes-exports)
15. [The catalog database](#15-the-catalog-database)
16. [HTTP API reference](#16-http-api-reference)
17. [Module and function reference](#17-module-and-function-reference)
18. [Security model](#18-security-model)
19. [Testing](#19-testing)
20. [Troubleshooting](#20-troubleshooting)

---

## 1. What this program is

Second Hand Smoke is a **verified offload station** for a film shoot. It runs on the DIT
cart as a local web application. You put a camera or sound card in the reader,
press one button, and it copies every file to two or more physically separate
drives, reads every byte back off each drive, checksums it, and only then tells
you the card is safe to format.

Everything after that — media metadata, editing proxies, camera/sound sync,
review dailies and a Resolve handoff package — is built on top of that verified
copy, and none of it can ever change the answer to "may I erase this card?"

**It never formats, deletes, moves or renames anything on a card.** It tells you
when a card is clear. You erase it yourself, in Disk Utility, deliberately.

### Who it is for

One operator, one cart, one card at a time. It is not a multi-user server and it
does not need to be reachable from another machine.

### What it is not

- It does not talk to Resolve directly and never writes to a Resolve project database.
- It does not upload to the cloud. "Cloud/offsite" is just a folder — a mounted
  Dropbox, Backblaze or NAS folder behaves like any other destination.
- It does not run two jobs at once. Two cards racing for the same USB bus is
  slower than doing them in sequence and much harder to reason about when it
  goes wrong.

---

## 2. The safety contract

These are rules the code enforces, not aspirations. Each one has a test behind it.

**1. Source media is opened read-only.** Nothing is moved, renamed, deleted or
written on a card, ever. Symbolic links on a card are refused rather than
followed.

**2. Every byte is read back.** Verification re-reads from the destination drive
with the macOS page cache bypassed (`F_NOCACHE`), so it tests the disk rather
than the copy of the data still sitting in RAM. If cache bypass fails,
verification fails rather than quietly weakening the guarantee.

**3. Two physical destinations minimum.** Folders, aliases and partitions on one
device count as one. Disk images resolve to whatever real drive their backing
file sits on, so two `.dmg` files on one SSD — or an image and the drive holding
it — collapse to a single identity. A destination on the source card's own device
is rejected. The format gate counts **distinct physical drives**, not destination
entries. `DIT_MIN_COPIES` can raise the requirement; it can never lower it below
two.

**4. Interruption is safe.** Files are written to `.partial` and renamed into
place only after a complete write and an `F_FULLFSYNC`. Pull a drive mid-copy and
restart: the job resumes at file granularity and re-verifies rather than starting
the card over.

**5. The tool never formats anything.** It reports clearance. You act on it.

**6. The card is checked twice.** Registration fingerprints paths, sizes and
beginning/middle/end content samples. The complete inventory is checked again
before and after the offload. New, changed, unreadable or skipped files close the
format gate.

**7. Verification is re-checked live — as far as a stat can see.** Every time
the gate is evaluated it **stats** each verified copy on every drive that is
still connected. A file **deleted, truncated, or changed in size** after
verification reopens the gate immediately, without waiting for a manual re-read.
A drive that has been disconnected is not penalised — its earlier verification
still counts.

**What this check does not catch: same-size corruption.** A `stat` reads the
size and the modification time; it does not read the bytes. A file whose
contents rotted or were overwritten with something the same length still passes
the live check, and is only caught by **Re-read drives**, which reads every byte
back with the page cache bypassed and compares checksums. The live check is a
cheap tripwire between full re-reads, not a substitute for one. Section [18] of
the rig proves what it does catch — a truncation on a connected drive reopening
the gate immediately — and section [8] proves the full re-read catches the rot
that the tripwire cannot.

### About the hash

xxHash64. Not cryptographic, and not meant to be. It catches bad cables, dying
cards, half-written files and bit rot roughly ten times faster than MD5, which is
the entire job. Nothing here defends against a person deliberately forging a
checksum; it defends against hardware.

---

### Filtering and the gate

A filter that skips files makes the plain verdict false: "everything on this
card is verified on two drives" is not true of a card that was partly copied on
purpose. The gate therefore has three answers, not two:

| Verdict | Colour | Means |
|---|---|---|
| `SAFE TO FORMAT` | green | Every file on the card is verified on 2+ physical drives |
| `CLEARED WITH EXCLUSIONS — n file(s) were not copied` | amber | Everything that was meant to be copied is verified; n files were deliberately skipped and are named |
| `DO NOT FORMAT` | red | Something is missing, short of copies, or changed since verification |

The amber verdict never reads as the green one anywhere: `reports.gate_stamp()`
is the single definition of the wording and the colour, and since this release
the interface reads `gate["stamp"]` from the server rather than writing the
sentence itself. Excluded files are recorded in the catalog when the offload
decides to skip them — never inferred at read time — so a filter changed after
a shoot day cannot retroactively change what that day says. A file that already
has a verified copy is never marked excluded whatever the filter now says, and
a filter that would skip every file on a card is refused rather than obeyed.

**When the flags move.** Exclusion is decided at the moment an offload starts,
and at no other time. Changing a filter does not touch a card that is not being
offloaded, and a verify run — "Re-read drives" — never applies filters at all,
because it is checking what is on the drives rather than deciding what to copy.
Start an offload again and the decision is remade: files the filter no longer
excludes become files the card is supposed to have, so a card that was amber
correctly turns red until they are copied. That is intent changing, not history
being rewritten; the files that were already copied keep their verified copies
either way.

## 3. How it is put together

A Python web application with no external services. Flask serves a single-page
interface on loopback; SQLite holds the catalog; worker threads do the long jobs
and report progress back through a status snapshot the interface polls.

```
     browser (127.0.0.1:3838)
              │  JSON over HTTP
              ▼
        app.py ── Flask routes, request guard, one-job-at-a-time gate
              │
    ┌─────────┼───────────┬──────────┬───────────┬──────────┐
    ▼         ▼           ▼          ▼           ▼          ▼
 cards.py  offload.py  media.py  proxies.py  sync.py   handoff.py
 storage.py     │      (ffprobe) (ffmpeg)   waveform.py (ffmpeg+PIL)
                │
                ▼
             db.py  ── SQLite catalog (~/CnF-Libraries-Installs/Second Hand Smoke/catalog.db)
                │
                ▼
            reports.py ── format gate, ASC MHL 1.1, day report
            mhl2.py ───── ASC MHL 2.0, the chain, C4 IDs
            library.py ── search, copy-count audit, export (read-only)
```

**Threading model.** Flask runs threaded, so several requests can be in flight.
Long work happens on a single daemon worker thread owned by whichever module is
running the job. `app.py` holds one lock (`_JOB_GATE`) around "is anything
running? then start" so no two job types can ever begin together. Each SQLite
connection is thread-local; the database is in WAL mode.

**Failure philosophy.** Anything downstream of the offload — metadata, proxies,
sync, waveform, dailies — can fail freely. A failed proxy is inconvenient and
regenerable; a failed original is a safety event. The interface and the day
report keep that distinction explicit, and no downstream phase can turn
**SAFE TO FORMAT** on or off.

---

### Interface conventions

Four rules, so a change to one panel does not quietly break the language the
rest of the screen speaks:

- **Colour means state, never decoration.** Green cleared, amber working or
  needs attention, red blocked or failed. The stripe down the side of a panel
  lights up when that phase is reachable and stays grey when it is not.
- **Nothing is hidden, only dimmed.** A phase you cannot use yet is greyed with
  a line saying what unlocks it. Controls that vanish mid-shoot read as a
  broken program.
- **Monospace for machine data, the system face for sentences.** Paths, hashes
  and timecode need character shape and alignment. Explanations do not, and
  monospace prose is tiring to read at the end of a long day.
- **Colour is never the only carrier.** Every status has words beside it, and
  every step on the track states its name, count and state in its accessible
  label. Every text colour in the palette clears WCAG AA on every surface it
  is used on, and `test_rig.py` section [28] computes that rather than trusting
  it.

---

## 4. Installing and starting it

### Requirements

- macOS (the read-back cache bypass and drive identity use Darwin facilities)
- Python 3.10 or newer
- `ffmpeg` and `ffprobe` for metadata, proxies, waveform sync and dailies
  (Homebrew: `brew install ffmpeg`)

Copies and verification work without ffmpeg; metadata is simply marked
unavailable and the later phases say so.

### Two ways to run it

**The application.** Open the DMG, drag Second Hand Smoke to Applications,
double-click. Python, Flask, xxhash, NumPy and Pillow are all inside the bundle;
nothing is installed on the machine and there is no first-launch build step.
For a release, run **Build Second Hand Smoke.command** to build, sign,
notarize and staple the app. Then run **Make Second Hand Smoke DMG.command**
to package that app, sign and notarize the image, and write the checksum
after stapling. The full setup is in [`macos/README.md`](macos/README.md).

Use **Reveal Server Log in Finder** in the app menu when troubleshooting.
The log is `Second Hand Smoke/Logs/server.log` under the resolved
`CnF-Libraries-Installs` folder, usually in your home folder.

Three things behave differently in the application, all deliberately:

- **The port is reserved, not fixed.** The wrapper asks the kernel for 3838 and
  takes whatever is free if it is busy, then sets `DIT_PORT` for the server. Two
  copies cannot fight over one port, and neither can silently attach to the
  other's server.
- **One server per catalog.** The server holds an exclusive lock beside the
  catalog and refuses to start if another copy holds it. Two servers sharing one
  SQLite file, near a format gate, is not a race worth having.
- **The server cannot outlive the window.** It is terminated on quit, and it
  also watches its parent process: a force quit or a crash of the window leaves
  no server behind holding the catalog open.

macOS asks once, per application, before it will let the app read a removable
volume. A denial does not produce an error — cards simply appear to be empty.
That is the first thing to check when a mounted card reads as blank.

**From source.** Double-click **`start.command`**, or from Terminal:

```bash
./start.command
```

On first launch it creates a private Python environment in `.venv` and installs
Flask and xxhash (required), then NumPy and Pillow (optional — waveform sync and
dailies slates). It then opens <http://127.0.0.1:3838>.

If NumPy or Pillow cannot be installed, everything else still launches and the
interface marks the affected phase unavailable.

### Python packages

| Package | Required? | Used for |
|---|---|---|
| Flask ≥3,<4 | yes | The local web interface |
| xxhash ≥3.4,<4 | yes | Every checksum |
| NumPy ≥2,<3 | optional | Phase 5 waveform correlation |
| Pillow ≥11,<13 | optional | Phase 6 dailies slate |

---

## 5. Configuration

All configuration is environment variables. Set them in your shell profile or
before `start.command`.

| Variable | Default | Purpose |
|---|---|---|
| `DIT_DB` | `~/CnF-Libraries-Installs/Second Hand Smoke/catalog.db` | Catalog location |
| `DIT_SCAN_ROOT` | `/Volumes` | Where to look for cards |
| `DIT_MIN_COPIES` | `2` | Verified drives before a card clears. Values below 2 are ignored |
| `DIT_PORT` | `3838` | Web interface port |
| `DIT_FFPROBE` | auto-detected | Explicit path to `ffprobe` |
| `DIT_FFMPEG` | auto-detected | Explicit path to `ffmpeg` |
| `DIT_PROBE_TIMEOUT` | `90` | Per-file metadata timeout, seconds |
| `DIT_THUMB_TIMEOUT` | `30` | Per-clip report-thumbnail timeout, seconds |
| `DIT_OPERATOR` | login account | Name written into the MHL `<name>` field |
| `DIT_BROWSE_ROOT` | — | An extra folder the picker may list |
| `DIT_SLATE_FONT` | auto-detected | Font for the dailies slate |
| `DIT_KEEP_TEST_RIG` | — | Set to `1` to keep the test rig's temp folder |

Two settings are stored in the catalog rather than the environment, because
they belong to the production rather than the machine:

| Setting | Set from | Purpose |
|---|---|---|
| `report_production_name` | Reports panel | Printed at the top of the PDF report |
| `report_logo_path` | Reports panel | Image placed on the PDF's first page |

Presets live in their own tables rather than in settings — `preset_folders`,
`presets` and `preset_destinations` — because they are a list, not a
preference. Each preset's logo copy lives beside the active one, as
`branding/preset_<id>.png`.

A logo can be picked with a file dialog, dropped onto the Reports panel, or
typed as a path. All three arrive at `pdfreport.store_logo()`, which decodes the
bytes, verifies they are really an image, resizes anything over 2400 px, and
re-encodes as PNG into `~/CnF-Libraries-Installs/Second Hand Smoke/branding/logo.png`. The setting then
points at that copy.

A picked file travels base64-encoded inside the JSON request body rather than as
a multipart upload. That is deliberate: every state-changing request to this API
must be `application/json`, because a cross-origin HTML form can post here but
cannot send that content type. Accepting multipart would mean removing that
guard from the whole API to add one convenience.

A logo is decoration. If the file is missing or unreadable the report is still
produced, with a line saying the logo could not be drawn.

A non-numeric value in any of the numeric variables falls back to the default
rather than refusing to start.

### Rehearsing without hardware

Point `DIT_SCAN_ROOT` at a folder of fake cards and add ordinary folders as
destinations. `test_rig.py` builds and exercises exactly that arrangement.
Note that two folders on the same disk correctly count as **one** destination —
that is the gate doing its job, not a bug.

---

## 6. The on-set workflow

The header carries a live step tracker — Day → Destinations → Ingest cards →
Sync → Proxies → Handoff — with a red/green lamp on each step. Clicking a step
scrolls to it.

**1. Start day.** Name it `DAY_01` and set the date. Names may be 1–64 letters,
numbers, spaces, dots, dashes or underscores.

**2. Fill the three destination slots.** Drive A (primary), Drive B (backup),
Cloud/offsite. The gate only requires two physically separate drives, but the
third slot exists because it is the copy that should leave the location. Click a
slot to open the folder picker, or type a path under "Add destination".

The **primary** destination is special: proxies, waveform analysis and the whole
Phase 6 handoff read from it, and the day report is written to it.

**3. Scan, then press Ingest card.** One button registers the card if it is new
and immediately starts the verified copy to every destination. "Register only"
is there if you want to inspect a card before committing to a multi-hour copy.

**4. Metadata is read automatically** after a successful offload. **Read
metadata** re-runs it by hand after installing ffmpeg or editing a sound report.
This works after the card has been unmounted and returned, by reading the
verified primary-drive copy instead.

**5. Wait for the slate to turn green — SAFE TO FORMAT.** Then **Write
manifest**. Only then does the card go back in the bag as clear.

**6. Sync day's footage** once camera and sound metadata are present. Re-run it
after any metadata refresh.

**7. Check TC waveforms** to independently verify single-take timecode
relationships. If timecode left anything unresolved, the same control becomes
**Waveform fallback**. Treat amber suggestions as suggestions.

**8. Build all proxies** walks every eligible roll automatically, one drive-safe
job at a time. It reads verified primary-drive masters, so the cards can already
be gone.

**9. Build Resolve handoff.** Reads the working drive, not the cards. If sync
needs attention the package carries explicit review markers rather than guessed
production sound.

**Re-read drives** re-verifies copies that already say verified. Use it before
wiping cards, and after a drive has travelled. It is the only thing that catches
silent rot, because an offload run trusts the catalog and skips work it has
already done.

---

## 7. Phase 1 — Offload and verify

### Card detection

`cards.py` walks each volume under the scan root and classifies files by
extension rather than by camera folder layout, because cameras change their
directory structure between firmware revisions.

| Category | Extensions |
|---|---|
| video | `.mxf` `.crm` `.mov` `.mp4` `.braw` `.r3d` |
| audio | `.wav` `.bwf` `.aif` `.aiff` |
| sidecar | `.xml` `.cpf` `.thm` `.if` `.smi` `.sfl` `.txt` `.csv` `.json` |
| other | everything else |

A volume with both video and loose audio is classed **mixed** — camera cards
carry scratch audio inside the MXF, not as loose WAVs, so loose WAVs beside video
means two cards were pooled. Volumes named `Macintosh HD`, `Preboot`, `Recovery`,
`VM`, `Update`, `xarts`, `iSCPreboot`, `Hardware`, `Data` and Time Machine
snapshots are skipped, as is anything with no media on it.

macOS junk (`.DS_Store`, `._` resource forks, `.Spotlight-V100`, `.fseventsd`,
`.TemporaryItems`, `.Trashes`) is excluded. A symbolic link anywhere on the card
is reported as an error and blocks ingest.

**Roll naming.** Camera: Canon names clips `A003C007_260118AB`, so the four
characters before the `C` are the roll. Sound: if every audio file sits under one
top-level folder, that folder's name is the roll.

### Two fingerprints

- **Content fingerprint** — path, size and 64 KB samples from the beginning,
  middle and end of every file. This answers "have I already offloaded this
  exact card?" `mtime` is deliberately excluded because exFAT stores local time
  with no timezone.
- **Inventory fingerprint** — path and size only. This answers "did the file
  list change?"

Both are checked at registration, again before the copy starts, and again after
it finishes.

### The copy

For each file, one read of the source is fanned out to every destination at once,
hashing as it goes, in 4 MB chunks. Each destination is written to
`<target>.partial`, flushed, `F_FULLFSYNC`ed, renamed into place with
`os.replace`, and then `copystat`ed. Only then is a `copies` row written.

Then each destination copy is **re-read from disk** with the page cache bypassed
and re-hashed. A mismatch, a size mismatch or a missing file marks that copy
`failed` with the reason.

### Resuming

An offload run skips copies already marked `verified` — that is what makes
resuming a 400 GB card cheap. A **verify** run deliberately re-reads them, which
is the only way to find rot. If a destination file exists at the wrong size, the
tool recopies it and logs an amber warning; a drive that does that twice should
leave the set.

### Destination layout

```
DRIVE_A/
└── PROJECT_01/          ← project
    └── DAY_01/              ← shoot day
        ├── CAMERA/A001/CONTENTS/CLIPS001/…   ← card structure preserved
        ├── SOUND/CRACK_R01/…
        ├── PROXY/A001/A001C001_…_PXY.mov
        ├── HANDOFF/…
        ├── ascmhl/                           ← ASC MHL 2.0 + chain
        ├── DAY_01_A001.mhl                   ← ASC MHL 1.1
        └── REPORTS/ingest_DAY_01.html
```

The card's own directory tree is preserved inside the roll folder — Canon's
`CONTENTS/CLIPS001` tree carries clip metadata some tools expect to find beside
the MXF, so it is not flattened. Sound recorders that already wrap every take in
a folder named for the roll do not get `SOUND/ROLL/ROLL`; the duplicate level is
collapsed while its contents are preserved.

If two files on one card would land on the same destination path after that
collapse, the job refuses to start and names both files.

### The format gate

A card is **SAFE TO FORMAT** only when every catalogued file on it has been read
back and checksum-matched on at least `DIT_MIN_COPIES` **distinct physical
drives**, and:

- no job is currently running on it, and the last one did not fail or cancel
- there are no zero-byte files on the card
- every verified copy that is on a currently-connected drive is still present at
  the size it was verified at

Anything less gives **DO NOT FORMAT** with the reason and the list of short files.

---

## 8. Phase 2 — Media metadata

`ffprobe` reads each camera and sound file without decoding or modifying it, and
the results go into `media_metadata`. Reading happens from the mounted card if it
is there, and otherwise from the verified primary-drive copy — so a metadata
refresh still works after the card has been cleared and returned.

Captured per file: duration, container and codec, resolution, frame rate,
embedded start timecode, timecode rate and drop-frame flag, audio channels,
sample rate, bit depth, BWF `TimeReference`, origination date and time, reel name,
and the complete raw ffprobe JSON.

### BWF and iXML

For `.wav` files the tool additionally reads the RIFF chunks itself, because not
every recorder firmware writes iXML the same way and ffprobe does not always
surface it:

- **`bext`** — the 64-bit `TimeReference` (audio samples since timecode
  midnight), origination date and origination time.
- **`iXML`** — the recorder's explicit timecode rate and drop/non-drop flag.

RF64/BW64 and big-endian RIFX files are handled. Chunks carrying a DTD are
refused outright, since no recorder writes one and it is the only way to attack
an XML parser.

### Sound reports

Any `.csv` sidecar on the card is examined. The tool looks for a header row it
recognises, tries comma, tab and semicolon delimiters, and tries UTF-8, UTF-16,
Mac Roman and Latin-1 encodings — accepting the first combination that yields a
usable header.

| Field | Accepted column names |
|---|---|
| filename | file, file name, filename, clip, clip name |
| scene | scene, scene name, slate |
| take | take, take name, take number, take no, take # |
| timecode | tc, timecode, time code, start tc, start timecode |
| notes | note, notes, comments, comment |
| circled | circle, circled, good, print |
| sound roll | roll, sound roll, tape, folder, record folder |

Rows are matched to audio files by exact filename, then by stem, then by a single
unambiguous mono-split prefix (`take1_L`, `take1-1`). Circled is read from `1`,
`true`, `yes`, `y`, `circle`, `circled`, `good`, `print`, `*` or `x`.

Metadata failures are visible in amber and **never** weaken or invalidate a
verified copy.

---

## 9. Phase 3 — ProRes proxies

**Build proxies** creates 1920×1080 Apple ProRes Proxy QuickTimes on the
destination marked `primary`. It never reads the camera card — every proxy is
decoded from the already-verified primary master.

Each output carries a QuickTime timecode track matching the source's start
timecode and the card roll as reel metadata. Video is padded to 1920×1080 with
the original aspect preserved; audio is carried as 48 kHz 24-bit PCM.

### Resumability

On a second run, a completed output is accepted only if all of this holds: the
profile matches, the recorded source hash matches the master's, the file exists
at its recorded size, its xxHash64 matches, and an `ffprobe` inspection confirms
it is ProRes Proxy at 1920×1080 with the right duration, timecode and reel.
Anything else is rebuilt. **Rebuild proxies** regenerates the whole roll
regardless.

New work is written to `.partial.mov`, inspected, flushed and only then renamed.

### Preconditions

The card must be **SAFE TO FORMAT**, a primary destination must be marked and
still be the same physical drive, every master must be verified and present at
its recorded size, and every clip must have a duration and a start timecode.
Free space is checked with a 10% reserve plus room for one atomic rebuild.

---

## 10. Phase 4 — Timecode sync

**Run timecode sync** turns timing metadata into persistent camera-to-sound
relationships. It is metadata-only: it does not trim, render, rename, move or
rewrite anything.

Camera timecode is converted to elapsed seconds at the clip's **timecode rate**
(which differs from the recording rate when shooting off-speed), including SMPTE
drop-frame numbering. For BWF audio the authoritative start is the 64-bit
`TimeReference` — audio samples from timecode midnight to the first sample —
divided by the sample rate. iXML supplies the recorder's TC rate and drop flag
when present. If a sound report's timecode disagrees with BWF by more than two
frames, the BWF value is used and the discrepancy is reported.

Clips that cross timecode midnight are handled by testing the sound take at
−1 day, 0 and +1 day and taking the best overlap.

### The five outcomes

| Status | Meaning |
|---|---|
| **matched** | Exactly one recorder take group overlaps this camera clip |
| **multiple** | Several non-overlapping takes sit inside one long camera clip; all are kept |
| **ambiguous** | Competing takes overlap each other — never silently chosen |
| **unmatched** | No timecoded sound overlaps the clip |
| **invalid** | Camera timing metadata is missing or malformed |

Split mono files sharing roll, scene, take, start and duration are grouped as one
recorder take, while each individual file relationship is preserved. A sound take
whose TC rate or drop flag disagrees with the camera is reported and excluded
rather than converted at the wrong rate.

Refreshing media metadata invalidates old relationships, and the next run rebuilds
them from scratch. **None of these states can turn SAFE TO FORMAT on or off.**

---

## 11. Phase 5 — Waveform fallback

Also metadata-only. It decodes audio from the verified masters on the primary
destination, so the source cards can be gone.

`ffmpeg` reduces each track to a small band-limited mono fingerprint (50–220 Hz,
resampled to 500 Hz), and NumPy performs normalised FFT cross-correlation.
Polarity is ignored, so an inverted scratch track still matches. Fingerprints are
cached across comparisons with a bounded, least-recently-used budget.

### Two modes

**Verification.** For a clip already matched by timecode, the fallback re-tests
only the take that timecode chose. A unique strong match that agrees leaves the
link intact and records it as **confirmed**. If the same take is right but the
offset differs by more than 100 ms, the waveform offset replaces it and the
relationship records `waveform` as its method.

**Search.** For unresolved, invalid or ambiguous clips, the camera audio is
compared against every recorder-take group on the day.

### Acceptance thresholds

Automatic acceptance requires **all three**:

| Guard | Value |
|---|---|
| Normalised score | ≥ `0.62` |
| Separation from the runner-up take *and* the next independent peak | ≥ `0.08` |
| Overlap | ≥ 3 seconds |

Scores from `0.35` to the acceptance boundary become **review** suggestions with
no accepted link. Below that they stay **unmatched**. Silent audio, decoder
errors and overly long inputs fail visibly rather than producing guesses.

The catalog and day report retain the score, the runner-up margin, the offset,
the overlap, the prior timecode state and the top three candidates. Re-running
timecode sync or refreshing metadata deliberately invalidates waveform results so
stale analysis cannot survive changed facts.

> The score is not a Pearson coefficient — it is a normalised correlation peak
> divided by overlap length. The thresholds are calibrated to that specific
> formula. If you change the maths, re-calibrate the numbers.

---

## 12. Phase 6 — Editorial handoff

**Build Resolve handoff** creates one deterministic `HANDOFF` folder on the
primary working drive. It requires verified originals, current ProRes proxies,
complete camera timing and one completed timecode-sync pass. Source cards can be
unmounted.

Unmatched, ambiguous or review clips do **not** block the export, but they are
marked **SYNC REVIEW** in the timeline, on the review slate and in the operator
notes. The tool never invents a production-audio relationship.

### Resumability and staleness

Every camera clip gets a signature covering its source hash, proxy hash,
duration, timecode, rate, sync status and every link's file, hash, offset and
overlap. A completed daily is reused only when its signature matches, its file is
present at its recorded size, its xxHash64 matches and an `ffprobe` inspection
confirms H.264/AAC at 1280×720 with the right duration. Any change to sync or
proxies marks the package stale. An interrupted run leaves
`EXPORT_INCOMPLETE.txt` in the bundle until a complete resume succeeds.

Phase 6 never changes copy verification or **SAFE TO FORMAT**.

---

## 13. What it reads (imports)

| Input | Where from | Read by |
|---|---|---|
| Camera and sound media | Any volume under `DIT_SCAN_ROOT` | `cards.py`, `offload.py` |
| Container and stream metadata | `ffprobe` JSON | `media.py` |
| BWF `bext` chunk | Directly from `.wav` bytes | `media.read_wave_metadata` |
| iXML chunk | Directly from `.wav` bytes | `media._ixml_values` |
| Recorder sound reports | `.csv` sidecars on the card | `media._read_report` |
| Verified masters | The primary destination | `proxies.py`, `waveform.py`, `handoff.py` |
| Existing proxies | `PROXY/` on the primary | `handoff.py` |

Nothing is imported from the network. There is no project-file import.

---

## 14. What it writes (exports)

### 14.1 The verified copies

The card's own tree, preserved, under `PROJECT/DAY/CAMERA/<roll>/` or
`PROJECT/DAY/SOUND/<roll>/` on every destination.

### 14.2 MHL manifest — `DAY_01_A001.mhl`

Written per card, per destination, at `PROJECT/DAY/`, and only once the complete
card is green on that destination. Original-format **MHL 1.1 XML** using
XXHash64:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<hashlist version="1.1">
  <creatorinfo>
    <name>…</name>            <!-- DIT_OPERATOR, or the login account -->
    <username>…</username>    <!-- the login account -->
    <hostname>…</hostname>
    <tool>Second Hand Smoke 1.0 (CnF Works)</tool>
    <startdate>…</startdate>
    <finishdate>…</finishdate>
  </creatorinfo>
  <hash>
    <file>CAMERA/A001/CONTENTS/…/A001C001.MXF</file>
    <size>1234567</size>
    <lastmodificationdate>…</lastmodificationdate>
    <xxhash64be>…</xxhash64be>
    <hashdate>…</hashdate>
  </hash>
</hashlist>
```

Paths are relative to the `.mhl` file's own folder. Silverstack, ShotPut and
Resolve's clone tool all read this. If this application disappears tomorrow, the
manifest remains human-readable and independently verifiable.

This file is written only when the day's checksum is one ASC MHL 1.1 can name —
xxHash64, MD5 or SHA-1. For xxHash128, xxHash3 and C4 the 1.1 schema has no
element, so no 1.1 file is written rather than one written under a name that
does not mean what it says. The 2.0 folder below covers all six.

### 14.2.1 ASC MHL 2.0 — `ascmhl/`

Written from the same call and the same rows as the 1.1 file above, by
`mhl2.write()`, at `PROJECT/DAY/ascmhl/`. Always written, for every algorithm.

```
PROJECT_01/DAY_01/ascmhl/
├── 0001_A001_2026-01-20_193000Z.mhl
├── 0002_CRACK_R01_2026-01-20_201500Z.mhl
└── ascmhl_chain.xml
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<hashlist version="2.0" xmlns="urn:ASC:MHL:v2.0">
  <creatorinfo>
    <creationdate>…</creationdate>
    <hostname>…</hostname>
    <tool version="1.0">Second Hand Smoke</tool>
    <author>…</author>            <!-- DIT_OPERATOR, or the login account -->
    <comment>PROJECT_01 / DAY_01 / A001 to RAID_A</comment>
  </creatorinfo>
  <processinfo>
    <process>transfer</process>   <!-- read on one volume, written to another -->
    <ignore/>
  </processinfo>
  <hashes>
    <hash>
      <path size="1234567" lastmodificationdate="…">CAMERA/A001/…/A001C001.MXF</path>
      <xxh128 action="original" hashdate="…">…</xxh128>
      <previousPath>PRIVATE/M4ROOT/CLIP/C0001.MP4</previousPath>
    </hash>
  </hashes>
</hashlist>
```

`action="original"` because every digest here was taken off the card during the
copy that produced the file, not recomputed from the destination afterwards.

**No root hash.** A directory hash covers everything under the root, and this
program writes one card at a time into a day folder that other cards are still
landing in — a root hash written after the first card would be wrong before the
second finished. `<processinfo>` carries none, which is valid and true.

**`ascmhl_chain.xml`** records the C4 ID of every manifest in the folder:

```xml
<ascmhldirectory xmlns="urn:ASC:MHL:DIRECTORY:v2.0">
  <hashlist sequencenr="1">
    <path>0001_A001_2026-01-20_193000Z.mhl</path>
    <c4>c4…</c4>
  </hashlist>
</ascmhldirectory>
```

Before appending a generation, every manifest the chain already covers is
re-hashed. A manifest that no longer matches the C4 ID recorded for it has been
changed since it was written; that is reported in the interface and **nothing is
repaired, rewritten or deleted** — silently fixing it is the one thing a chain
must never do. Generations only accumulate. Re-running a manifest write for a
card that has not changed adds nothing, so re-reading the drives does not pile
up generations that differ only in their clock reading.

### 14.3 Day report — `REPORTS/ingest_DAY_01.html`

A self-contained HTML page written to the primary destination and served live at
`/api/day/<id>/report`. Contains destinations, a day summary line, and per card:
the SAFE TO FORMAT stamp with its reason, a media table (scene/take, duration,
start TC, format, audio, metadata state, proxy state), a camera/sound sync table,
a waveform table, and a per-file verification table with one column per
destination.

### 14.3b Day report PDF — `REPORTS/ingest_DAY_01_<layout>.pdf`

The same day, branded and paginated, in the layout chosen in the Reports panel:
`table` for the dense verification manifest, `metadata` for the per-clip
listing. Landscape A4, the production's name and logo on the first page, and a
running footer carrying the build that wrote it, the licence and the support
address on every page.

It is generated from the same single reading of the catalog as the HTML report
above — same destinations, same media, same format-gate verdict — so the two
documents cannot describe the same day differently. reportlab is required for
this and nothing else; the HTML report is written either way.

### 14.3c Report thumbnails — `~/CnF-Libraries-Installs/Second Hand Smoke/thumbnails/<hash>.jpg`

One JPEG per camera clip, 480 px wide, decoded from the verified primary-drive
copy at roughly ten per cent into the clip so the frame is not a black slate.
Cached by content hash on this machine only; never written to a production
drive and never part of a delivery.

### 14.4 ProRes proxies — `PROXY/<roll>/<clip>_PXY.mov`

1920×1080 Apple ProRes Proxy, `yuv422p10le`, Apple vendor tag, with a QuickTime
timecode track and the roll as reel metadata. Audio is 48 kHz 24-bit PCM. Where
two clips on one card share a filename stem, an 8-character hash of the relative
path is inserted before `_PXY`.

### 14.5 The handoff bundle — `HANDOFF/`

```
HANDOFF/
├── DAILIES/A001/A001C001_…_DAILY.mp4
├── DAY_01_RESOLVE.fcpxml
├── DAY_01_METADATA.ale
├── DAY_01_MEDIA.json
├── README_HANDOFF.txt
└── CHECKSUMS.txt
```

**`DAY_01_RESOLVE.fcpxml`** — Unicode FCPXML 1.10. Assets reference the existing
ProRes proxies by `file://` URI. A `DAILIES STRINGOUT` project holds every camera
clip in order, with accepted production WAVs attached on negative lanes at their
stored offsets and a scene/take/sync marker on each clip. Every camera clip also
appears in the event browser for easy relinking. **Every time value is a whole
multiple of the sequence frame duration**, so nothing snaps or drifts on import.

**`DAY_01_METADATA.ale`** — tab-delimited Avid Log Exchange with columns: Name,
Tape, Start, End, FPS, Tracks, Scene, Take, SoundRoll, Comments, Good, Source
File, Proxy Path, Sync Status, Sync Method, Sync Offset. Clip names match the
FCPXML, so importing the ALE into the media pool populates the columns.

**`DAY_01_MEDIA.json`** — schema `cf-smoker-handoff-1`. Per clip: roll, duration,
start timecode, frame rate, and the path plus xxHash64 of the original, the proxy
and the daily, along with sync status, method, waveform score and margin, and
every sound link's path, hash, scene, take, offset and overlap. This is the file
to relink from, or to feed to your own tooling.

**`DAILIES/`** — 1280×720 H.264/AAC review copies with a burned-in slate showing
filename, roll, scene/take, source start TC and sync state. Accepted production
sound is aligned from the catalog; unresolved clips keep camera scratch audio and
carry an amber review label. For review, not for online finishing.

**`CHECKSUMS.txt`** — `<xxhash64>  <relative path>` for every document and every
daily.

**`README_HANDOFF.txt`** — plain-text instructions for the editor, including the
list of clips that need editorial review.

---

## 15. The catalog database

SQLite in WAL mode at `DIT_DB` (default `~/CnF-Libraries-Installs/Second Hand Smoke/catalog.db`). A catalog left at
an earlier location (`~/FML/`, then `~/CrackodileDIT/`) is moved across automatically
on first launch, newest first. One
connection per thread, foreign keys on. Schema migrations are forward-only column
additions.

| Table | Holds |
|---|---|
| `days` | project, name, shoot date. Unique on (project, name) |
| `destinations` | label, path, canonical path, device id, volume UUID, role. Unique on (day, path) |
| `cards` | both fingerprints, label, kind, roll, source path, file and byte counts, status |
| `files` | one row per file on a card: relative path, size, mtime, category, source hash |
| `copies` | one row per (file, destination): destination path, state, read-back hash, error |
| `media_metadata` | everything Phase 2 extracts, plus the raw ffprobe JSON |
| `proxies` | one row per video file: state, profile, source and output hashes, inspected facts |
| `sync_status` | one row per camera clip: status, method, camera start/end, rate, drop flag |
| `sync_links` | one row per (camera, sound) relationship: absolute sound start/end, offset, overlap, day shift |
| `waveform_results` | state, prior TC state, best sound file, score, runner-up, margin, offset, overlap |
| `waveform_candidates` | top three ranked candidates per camera clip |
| `dailies` | one row per camera clip: state, source signature, output path, size, hash |
| `handoff_exports` | one row per day: state, catalog signature, and every document path |
| `jobs` | job history: card, type, state, message, start and finish |
| `settings` | key/value; currently just the active day |

**Units matter.** `sync_links.sound_start_seconds` and `sound_end_seconds` are
**absolute positions on the day's timecode axis**, in seconds. `offset_seconds`
is **relative to the camera clip's start**. Both Phase 4 and Phase 5 write those
columns in exactly those units.

Copy state is one of `pending`, `copied`, `verified`, `failed`. Only `verified`
counts toward the format gate.

---

## 16. HTTP API reference

Everything is JSON over `http://127.0.0.1:<port>`. Every state-changing request
must be `application/json` and must come from the tool's own origin.

### Days and destinations

| Method | Path | Does |
|---|---|---|
| `POST` | `/api/day` | Create or re-select a day. Body: `project`, `name`, `shoot_date` |
| `POST` | `/api/day/<id>/activate` | Make an existing day active |
| `POST` | `/api/destination` | Add a destination. Body: `path`, `label`, `role` |
| `DELETE` | `/api/destination/<id>` | Remove a destination that has no copies yet |
| `GET` | `/api/browse?path=…` | List folders. `volumes`, `home`, or a path inside an allowed root |

### Cards and jobs

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/scan` | Scan for cards; returns fingerprints, roll, kind, counts, errors |
| `POST` | `/api/register` | Register a scanned card. Body: `fingerprint`, optional `roll`, `kind` |
| `POST` | `/api/ingest` | Register **and** start the verified offload |
| `POST` | `/api/offload/<card>?mode=offload\|verify` | Start an offload or a re-read pass |
| `POST` | `/api/cancel` | Stop whatever job is running, safely |
| `GET` | `/api/card/<card>/status` | The live format-gate answer for one card |
| `POST` | `/api/card/<card>/analyze` | Re-read metadata and sound reports |
| `POST` | `/api/card/<card>/manifest` | Write every manifest format to every destination. Returns `paths` and `warnings` |

### Phases and reporting

| Method | Path | Does |
|---|---|---|
| `POST` | `/api/proxy/<card>?rebuild=1` | Build (or rebuild) proxies for one roll |
| `POST` | `/api/day/<day>/proxies/next` | Start the next eligible roll; `done:true` when finished |
| `POST` | `/api/day/<day>/sync` | Run timecode sync for the day |
| `POST` | `/api/day/<day>/waveform` | Run the waveform fallback |
| `POST` | `/api/day/<day>/handoff?rebuild=1` | Build the editorial handoff |
| `GET` | `/api/day/<day>/handoff/notes` | The operator notes, rendered |
| `POST` | `/api/day/<id>/checksum` | Set the day's checksum. Body: `hash_algo` |
| `POST` | `/api/day/<id>/naming` | Store the day's patterns. Body: `pattern_camera`, `pattern_sound` |
| `GET` | `/api/day/<id>/naming/preview` | What every registered card would be called |
| `GET` | `/api/drives` | The drive registry and what each drive holds |
| `POST` | `/api/drive/speed` | Verified speed test. Body: `path`, optional `bytes` |
| `POST` | `/api/drive/index` | Index a drive. Body: `path`, optional `label` |
| `DELETE` | `/api/drive/<id>` | Forget a drive; nothing on the drive is touched |
| `GET` | `/api/presets` | Every preset folder and its presets |
| `POST` | `/api/preset` | Save the active day's setup. Body: `name`, `folder_id` |
| `POST` | `/api/preset/<id>/apply` | Attach a preset's drives to the active day |
| `DELETE` | `/api/preset/<id>` | Delete a preset and its own logo copy |
| `POST` | `/api/preset-folder` | Create a folder. Body: `name` |
| `DELETE` | `/api/preset-folder/<id>` | Delete a folder; its presets are kept |
| `GET` | `/api/day/<day>/report` | The day report HTML |
| `GET` | `/api/day/<day>/report.pdf?layout=` | The day report as a branded PDF; `table` or `metadata`. 503 with the reason when reportlab is absent |
| `POST` | `/api/report-branding` | Store the production name and logo path. Body: `production`, `logo` |
| `GET` | `/api/state` | Everything the interface draws: days, destinations, cards, gates, summaries, active job |

### Status codes

`400` a precondition failed (the message says which) · `403` the request did not
come from this machine's interface · `404` unknown id · `409` another job is
running, or the card changed since the scan · `415` not sent as JSON.

---

## 17. Module and function reference

### `app.py` — Flask app and JSON API

| Function | Purpose |
|---|---|
| `only_local_requests()` | `before_request` guard: loopback `Host`, own `Origin`, JSON body |
| `drop_stale_gate_cache(response)` | Clears cached gate answers after any mutation |
| `json_error(exc)` | Turns every failure into a JSON `error` the interface can show |
| `start_job(starter, …)` | Check "anything running?" and start, atomically |
| `job_running()` | True if any of the four job types is active |
| `component(value, label)` | Validates a project/day/roll name that will become a folder |
| `_register_card(day, d)` | Shared registration used by both `/api/register` and `/api/ingest` |
| `_browse_roots()` / `_inside_browse_root(path)` | The folder picker's allow-list |
| `latest_job()` | The active job snapshot, or the most recent finished one |

### `db.py` — SQLite catalog

`now()` UTC timestamp · `conn()` thread-local connection · `init()` create and
migrate · `q(sql, args)` all rows · `q1(sql, args)` one row · `ex(sql, args)`
execute, returns last row id · `transaction()` context manager wrapping
`BEGIN IMMEDIATE`.

### `cards.py` — detection and fingerprinting

| Function | Purpose |
|---|---|
| `walk_volume(root)` | Every real file with category and sample hash, plus an error list |
| `fingerprint(files)` | Content fingerprint: path, size and beginning/middle/end samples |
| `inventory_fingerprint(files)` | Path-and-size-only fingerprint |
| `classify(files)` | `camera`, `sound`, `mixed` or `unknown` |
| `guess_roll(files)` | Roll from Canon clip naming or the sound folder |
| `inspect_volume(path)` | Everything above, for one volume |
| `scan(root, exclude_paths, exclude_device_ids)` | Candidate cards under the scan root |

### `storage.py` — physical drive identity

| Function | Purpose |
|---|---|
| `canonical(path)` | Expand, absolutise and resolve symlinks |
| `inspect_path(path)` | Canonical path, `device_id`, volume UUID, free and total bytes |
| `paths_overlap(a, b)` | True if either path contains the other |
| `validate_destination_set(source, dests, min_copies, require_writable)` | Mount, identity, writability, same-device and source-overlap checks; enforces the floor of two |

`device_id` is `disk:<ParentWholeDisk>` where diskutil can say, `st_dev:<n>`
otherwise. A disk image resolves to the identity of the drive its backing file
sits on.

### `offload.py` — the copy and verify engine

| Function | Purpose |
|---|---|
| `hash_file(path, progress, require_uncached)` | xxHash64 of a file, optionally bypassing the page cache |
| `dest_path_for(root, project, day, card, rel_path)` | Where one source file goes, with traversal refused |
| `_target_collision(files, dests, day, card)` | Refuses a card whose files would overwrite each other |
| `preflight(card_id, mode, min_copies)` | Every operator-facing check before a thread starts |
| `Job._copy(...)` | One source read fanned out to N destinations, `.partial` then rename |
| `Job._verify(...)` | Cache-bypassed re-read and hash compare; upserts the `copies` row |
| `start / current / status_snapshot / cancel` | Job lifecycle |

### `reports.py` — the gate, the manifest, the paperwork

Since the PDF report arrived, this module is in two halves: `day_data()`
gathers a shoot day from the catalog once, and `render_html()` turns that into
the HTML report. `pdfreport.py` renders the same structure. Nothing after
`day_data()` queries anything.

| Function | Purpose |
|---|---|
| `card_status(card_id)` | **The format gate.** Live, authoritative |
| `card_status_cached(card_id)` | Display-only, 4-second TTL. Never used for a decision |
| `invalidate_gate_cache(card_id=None)` | Drop cached answers |
| `_device_map(card_id)` | Destination id → physical drive identity |
| `_live_gap(f, dest_path, dest_root)` | Is this verified copy still on a connected drive at the right size? |
| `manifest_data(card_id, destination_id)` | The facts both manifest formats are rendered from |
| `write_manifests(card_id, destination_id)` | Writes every format this day's checksum can be named in |
| `write_mhl(card_id, destination_id)` | Renders ASC MHL 1.1, or `None` if 1.1 cannot name the digest |
| `day_report(day_id)` | Returns `(path, html)` and writes the page to the primary drive |

### `media.py` — metadata and sound reports

| Function | Purpose |
|---|---|
| `probe_file(path)` | Run `ffprobe`, return parsed JSON |
| `parse_probe(payload)` | Normalise ffprobe output into catalog columns |
| `read_wave_metadata(path)` | BWF `bext` and iXML, read directly |
| `resolve_media_path(card, file_row)` | The mounted card if present, else the verified primary copy |
| `import_sound_reports(card_id, card)` | Match CSV rows to audio files and layer them on |
| `analyze_card(card_id)` | The whole phase, serialized against sync |
| `card_summary(card_id)` | Counts and durations for the interface |

### `proxies.py` — ProRes queue

`capability(recheck)` · `output_path_for(...)` · `preflight(card_id, rebuild)` ·
`transcode_file(...)` the ffmpeg call · `inspect_proxy(path)` and
`_validated_facts(...)` the acceptance checks · `Job` · `card_summary(card_id)`.

### `sync.py` — timecode matching

| Function | Purpose |
|---|---|
| `parse_rate(value)` | `"24000/1001"`, `"23.976"` or a `Fraction` → exact `Fraction` |
| `timecode_to_frames(tc, rate, drop_frame)` | SMPTE frame index, drop-frame aware |
| `timecode_to_seconds(tc, rate, drop_frame)` | Elapsed seconds at the real rate |
| `day_length_seconds(rate, drop)` | Length of one timecode day, for midnight wrap |
| `camera_timecode_rate(row)` | Timecode rate, falling back to the recording rate |
| `overlap_with_rollover(...)` | Best overlap across −1 day, 0, +1 day |
| `run_day(day_id)` | The whole phase, under `METADATA_LOCK` |
| `invalidate_card(card_id)` | Discard relationships built from metadata about to change |

### `waveform.py` — correlation fallback

| Function | Purpose |
|---|---|
| `extract_audio(path, cancel_event, …)` | Band-limited mono fingerprint via ffmpeg |
| `correlate(camera, sound, sample_rate)` | Score, next independent peak, offset, overlap |
| `_sound_group_key(row)` | Groups split mono files into one recorder take |
| `_set_sync_result(...)` | Writes accepted links on the absolute day axis |
| `preflight / start / cancel / day_summary` | Lifecycle and reporting |

Constants: `AUTO_SCORE 0.62`, `AUTO_MARGIN 0.08`, `REVIEW_SCORE 0.35`,
`MIN_AUTO_OVERLAP_SECONDS 3.0`, `TC_DISAGREEMENT_SECONDS 0.10`,
`SAMPLE_RATE 500`, `MAX_SAMPLES 4,000,000`.

### `handoff.py` — dailies and the Resolve package

| Function | Purpose |
|---|---|
| `_fraction_time(value, rate)` | An FCPXML time, quantized to whole frames |
| `frames_to_timecode(n, rate, drop)` | Inverse of `sync.timecode_to_frames` |
| `_camera_signature(row, links)` | The staleness signature for one clip |
| `render_slate(path, row, links)` | The transparent burn-in overlay |
| `transcode_daily(...)` | The ffmpeg call that builds one review movie |
| `build_fcpxml / build_ale / build_manifest / build_readme` | The four documents |
| `preflight / start / cancel / day_summary` | Lifecycle and reporting |

### `pdfreport.py` — the branded PDF report

| Function | Purpose |
|---|---|
| `availability()` | `(available, reason)`; the reason is shown to the operator verbatim |
| `branding()` | Production name and logo path from settings; never raises |
| `build(day_id, layout, out_path)` | Writes the PDF and returns its path |
| `LAYOUTS` | `table` (verification manifest) and `metadata` |
| `store_logo(raw)` | Decodes, verifies, re-encodes and stores a chosen logo |
| `clear_logo()` | Forgets the logo and removes this program's copy of it |

This module never touches the catalog. `reports.day_data()` gathers the day
once and hands it over, so the SAFE TO FORMAT stamp printed in the PDF is
quoting the same `card_status()` result the HTML report quoted, from the same
dictionary, in the same call. There is no second query, so there is no second
opinion. `reports.gate_stamp()` supplies the wording to both, so even the
sentence has one definition.

reportlab is optional. Without it the PDF is reported unavailable, the HTTP
route answers 503 with the reason, and the offload, the verification, the HTML
report and the format gate are untouched.

### `server_main.py` — the packaged server's entry point

| Function | Purpose |
|---|---|
| `claim_single_instance()` | An exclusive lock beside the catalog; one server per catalog |
| `watch_parent(interval)` | Exits if the launching application disappears |
| `main()` | Locks, starts the watchdog, prints the banner, serves |

`start.command` runs `app.py` directly, which claims the same catalog lock
before initialization. The packaged server also starts the parent watchdog.

### `destinations.py` — attaching a drive to a day

| Function | Purpose |
|---|---|
| `attach(day_id, path, label, role)` | `(info, error)`; exactly one is None |
| `ROLES` | `primary`, `backup`, `offsite` |

One function, because there is more than one way to ask: typed into the
interface, or applied from a preset. A preset that could attach a drive by a
slacker set of rules than the interface does would be a hole straight through
the two-drive requirement.

### `hashing.py` — checksum choice

| Function | Purpose |
|---|---|
| `ALGORITHMS` | The offered set: what we can compute *and* an MHL can name |
| `normalise(algo)` | Falls back to xxHash64, what older rows were hashed with |
| `mhl_element(algo)` | The ASC MHL 1.1 element name, or `None` if 1.1 has none |
| `mhl2_element(algo)` | The ASC MHL 2.0 element name. Every algorithm has one |
| `manifests_for(algo)` | Which manifest formats this choice will actually produce |
| `choices()` | The list the interface offers, with notes |
| `new(algo)` | A fresh hasher |

No manifest ever names a digest something it is not. An algorithm written under
a neighbouring element name is a manifest that lies about its own contents, and
that is worse than not offering the algorithm at all. So each entry carries the
element name each schema defines for it and `None` where that schema defines
none: 1.1 names five digests, 2.0 names six. xxHash128, xxHash3 and C4 are
therefore offered — 2.0 can name them — and a day using one gets a full 2.0
`ascmhl/` folder and no 1.1 sidecar. `manifests_for()` is what the interface
says out loud before the choice is made, so nobody discovers it afterwards.

The C4 encoder lives here too, because the 2.0 chain file identifies every
manifest by its C4 ID whatever the media is hashed with. It is SHA-512
re-encoded in base58 and padded to 90 characters, cross-checked byte-for-byte
against Pomfort's reference `ascmhl` implementation, with two pinned vectors in
section [37] of the rig.

The setting belongs to the shoot day, and `offload._algo_changed()` refuses to
change it once a card has copies. `reports.write_mhl()` refuses to write a
manifest covering more than one algorithm at all, as a second line of defence.

Proxies, review dailies and thumbnails always use xxHash64: internal integrity
checks on files this program made.

### `naming.py` — token patterns

| Function | Purpose |
|---|---|
| `validate(pattern)` | None, or a sentence saying why the pattern cannot work |
| `resolve(pattern, facts)` | One stem, sanitised; `""` if it resolved to nothing |
| `plan(files, card, day, pattern)` | Every destination name for a card |
| `collisions(planned)` | Names two source files would both claim |
| `group_key(rel_path)` | Files renamed together: same folder, same stem |
| `DEFAULT_PATTERN` | `{original}` — renaming is asked for, never automatic |
| `parse_filters(text)` | `(includes, excludes)`; `-` prefixes an exclude |
| `is_included(rel_path, text)` | Would this file be copied? |
| `split_by_filter(files, text)` | `(kept, skipped)`, pure and order-preserving |
| `describe_filters(text)` | The filter as a sentence, for the interface |

Deliberately pure: no database, no filesystem, no clock. That is not tidiness —
a function with no hidden inputs is one whose output can be proven identical
across a cancel and a resume, and a rename that came out differently the second
time would copy a second file beside the first while the gate counted both.
`{index}` is computed from the card's groups in sorted order for the same
reason.

### `library.py` — search, audit and export

| Function | Purpose |
|---|---|
| `search(query, limit, offset, check_presence)` | Everything matching, newest shoot day first |
| `audit(minimum, mode, query)` | Files below N drives. `mode` is `record` or `present` |
| `only_copy(query)` | Files on exactly one physical drive, on record |
| `drives()` | Every registered drive, with catalog and index counts kept apart |
| `export(result, fmt)` | CSV or JSON, carrying both counts and the "as at" stamp |
| `Presence` | Which registered drives answered, cached for one request |

**Two numbers, never one.** The catalog knows what happened, not what is true
right now. It knows a file was read back off two drives with the cache bypassed
and the checksums matched; it does not know that drive is still readable, still
has the file, or still exists. So every row carries `verified_drives` (from the
catalog) *and* `present_drives` (from the filesystem, counted only for drives
that answered). `present_drives` is `None` — never `0` — when presence was not
checked, and the interface renders that as "not checked".

**`present` is `os.stat`, not a checksum.** Re-verifying a shelved drive is a
real job with a progress bar, subject to the job gate. Nothing here re-hashes
anything, and nothing here calls a size check "verified".

**Nothing checked is not nothing there.** If no registered drive answered, the
`present` audit returns `nothing_checked: True` and zero rows, rather than
reporting every file in the library as missing.

**Filters are an allow-list.** `library.FIELDS` maps a fixed set of names to
their SQL. Nothing else reaches the database, and every value is a bound
parameter.

**Deliberately excluded files are out of the audit by default.** A file a
filter skipped was never meant to be on a drive; counting it as a missing copy
would bury a real answer. `include_excluded` asks for them on purpose.

**Read-only, proven.** No `db.ex`. No call to `card_status`. No source card
opened. Section [38] of the rig dumps the catalog, the source card trees and
every format-gate verdict before and after running every function in this
module, and requires all three to be byte-identical.

**Routes.** `POST /api/library/search`, `POST /api/library/audit`,
`GET /api/library/drives`, `GET /api/library/export`. The export is a GET
because a download is a browser navigation and `fetch()` cannot hand the
browser a file — the alternative was a form POST, which would have meant
loosening the `application/json` rule that keeps other sites out of this API.
Nothing about an export changes state, so GET is both the honest verb and the
one that leaves that guard alone.

### `drivetools.py` — speed tests, indexing and the drive registry

| Function | Purpose |
|---|---|
| `register(path, label)` | Records a drive by volume identity, not mount point |
| `known_drives()` | The registry, with online state and what each drive holds |
| `read_manifest(path)` | Parses any ASC MHL manifest, 1.1 or 2.0 — ours or another tool's |
| `start(mode, path, ...)` | `speed` or `index`; runs through the same job gate |
| `forget(drive_id)` | Removes a drive from the registry; touches no drive |

The speed test writes a real file, fsyncs it, then reads it back with the page
cache bypassed and checksums the result. Without the fsync the write figure is
how fast the OS accepted the bytes; without the cache bypass the read figure
may be memory. When bypass cannot be established the result says so rather than
reporting a fast number. The scratch file lives in the drive's own
`.shs-speedtest` folder and is removed afterwards — the only file this program
ever removes from a drive.

Indexing is read-only and stores a snapshot in `drive_index`, plus anything it
learned from manifests in `drive_manifests`. Neither table is evidence a file is
still present or was ever verified: `copies` is what says a file is safe, and
only a read-back with a matching checksum writes to it.

### `presets.py` — saved job configurations

| Function | Purpose |
|---|---|
| `list_all()` | Folders, each with its presets and their drives |
| `create_folder(name)` / `delete_folder(id)` | Deleting a folder keeps its presets |
| `save_from_day(day_id, name, folder_id)` | Captures drives, project and branding |
| `apply(preset_id, day_id)` | Attaches drives; reports each one individually |
| `preset_logo_path(preset_id)` | Each preset owns a copy of its logo |
| `delete_preset(id)` | Removes the preset, its drives and its own logo copy |

Applying is never all-or-nothing: each destination comes back as attached,
skipped or failed with a reason, because three drives where two are mounted is
an ordinary morning. A preset records the `device_id` each path had when it was
saved; that is never used to *choose* a drive, only to notice that the drive
mounted there now is a different one and say so.

### `thumbnails.py` — one frame per clip, for the report

| Function | Purpose |
|---|---|
| `available()` | `(available, reason)`; frames need ffmpeg, nothing else does |
| `cached_frame(src_hash)` | The frame already on disk, or None. Decodes nothing |
| `frame_for(file_row, day_id)` | The frame, building it if needed. Never raises |
| `build_for_day(day_id)` | Warms the cache; returns made / cached / missing |
| `clear_cache()` | Deletes this machine's frames and nothing else |

Frames are decoded from the **verified copy on the primary drive**, never from
the source card, and only from a copy in the `verified` state. The cache is
keyed by the file's content hash and lives beside the catalog, so it never
needs invalidating — different content is a different key — and nothing of ours
is written to the production's drives.

Everything in this module is cosmetic. It writes nothing to the catalog, never
calls the format gate, and returns None rather than raising on any failure. A
clip that will not decode is a gap in a report, never a card that fails to
clear. `reports.day_data()` only ever performs a cache lookup, so gathering a
shoot day never runs ffmpeg; the thumbnail layout warms the cache before it
asks.

### `version.py` — the version number

| Name | Purpose |
|---|---|
| `VERSION_FILE` | Absolute path to the `VERSION` file at the repository root |
| `VERSION` | Its contents, or `0.0.0-unknown` if it cannot be read |
| `TOOL_STRING` | `Second Hand Smoke <version> (CnF Works)`, written into every MHL |

An unreadable `VERSION` file does not stop an offload. It marks the manifest as
coming from an unidentified build, which is visible in the paperwork rather
than silently wrong.

### `toolpaths.py` — finding ffmpeg and ffprobe

| Function | Purpose |
|---|---|
| `find(name, override_var)` | Override, then `PATH`, then the fallback directories |
| `ffmpeg()` / `ffprobe()` | The two tools, honouring `DIT_FFMPEG` / `DIT_FFPROBE` |
| `FALLBACK_DIRS` | Homebrew (both architectures), MacPorts, `/usr/bin`, `/bin` |

`PATH` differs by launch mode. A shell inherits the login shell's `PATH`, which
normally includes `/opt/homebrew/bin`. An application launched from Finder or
the Dock gets a minimal `PATH` from launchd that does not. Without the fallback
search, the packaged app reports "ffmpeg not found" on a machine where ffmpeg
plainly works in Terminal, and silently loses metadata, proxies, waveform sync
and dailies. Discovery never touches the copy or verification path: if ffmpeg
really is absent, those features report unavailable and the offload, the
checksums and the format gate behave exactly as before.

---

## 18. Security model

The tool runs on a cart that may also have a browser open. Loopback binding stops
other machines; it does not stop a page in another tab. So:

- **Host check.** Any request whose `Host` is not `127.0.0.1`, `localhost` or
  `[::1]` on the configured port is refused with `403`. This closes DNS
  rebinding, where an attacker's page repoints a hostname at 127.0.0.1 and reads
  the API as a same-origin resource.
- **Origin check.** Any state-changing request carrying an `Origin` that is not
  the tool's own is refused with `403`.
- **JSON requirement.** Every `POST` and `DELETE` must be `application/json`,
  which a cross-origin HTML form cannot send.
- **Folder picker allow-list.** `/api/browse` will only list `/Volumes`, your
  home folder, the scan root and an optional `DIT_BROWSE_ROOT`. It never lists
  file contents, only directory names.
- **No shell.** Every `ffmpeg`, `ffprobe`, `diskutil` and `hdiutil` call passes
  its arguments as a list. There is no shell interpolation anywhere.
- **Parameterised SQL** throughout. The only f-strings in SQL build column lists
  from module constants.
- **XML from cards** is rejected if it carries a DTD or entity declaration.

What this does **not** defend against: someone with an account on the cart. It is
a single-user local tool and anyone who can run programs on the machine can read
the same drives directly.

---

## 19. Testing

```bash
.venv/bin/python test_rig.py
```

The rig builds disposable synthetic camera and sound cards in a temp folder,
offloads them, then deliberately changes the source and corrupts, truncates and
deletes destination files to confirm each fault is caught. It never touches
mounted media. Set `DIT_KEEP_TEST_RIG=1` to keep the temp folder for inspection.

**633 checks across 40 sections**, covering: registration and
identity, the copy and verify path,
corruption and rot, cancellation and resume for all four job types, the format
gate, MHL and day report, metadata and sound reports, timecode sync across every
outcome, waveform acceptance and refusal, proxies, the handoff package, and a
regression section that goes at the real functions for faults the stubbed rig
could not otherwise see — device identity, catalog self-healing, destination path
collisions, waveform coordinate units, FCPXML frame alignment, and the local-only
request guard.

Run it after any change. It takes about a minute.

---

## 20. Troubleshooting

**"Only 1 separate destination drive(s) are ready; 2 are required."**
The two destinations resolved to one physical drive — two folders on one disk,
two partitions, an alias, or a disk image sitting on the other destination's
drive. Use genuinely separate hardware.

**"DO NOT FORMAT" with "copies on X share a physical drive and count once."**
Same cause, caught at the gate. The copies exist; they are just not on two
drives.

**"Card contents changed after the scan."**
Something wrote to the card between the scan and the offload, or the card was
swapped. Scan again. If it repeats with no card change, suspect the reader.

**"Source card has read errors."**
The card could not be fully read. Do not format it. Try another reader and
another cable before concluding the card is failing.

**A wrong-size file was recopied with an amber warning.**
The destination drive wrote something other than what was sent. Once is a
warning. Twice from the same drive and that drive should leave the set.

**"Metadata unavailable" everywhere.**
`ffprobe` is not installed or not on `PATH`. `brew install ffmpeg`, then press
**Read metadata**. The tool re-detects without a restart.

**"Waveform sync unavailable."**
NumPy is not installed. Run `start.command` once while online, or
`.venv/bin/pip install 'numpy>=2,<3'`.

**"Phase 6 unavailable."**
Pillow is missing, or ffmpeg lacks `libx264`, `aac`, `overlay`, `scale` or
`amix`. Everything before Phase 6 still works.

**Sound is not matching camera.**
Check the sync table in the day report. `invalid` means camera timing metadata is
missing or malformed. `unmatched` with valid timing usually means the recorder's
timecode rate or drop-frame flag disagrees with the camera — the warnings list
says which file. `ambiguous` means competing takes genuinely overlap and a human
has to choose.

**The card cleared but I want to check again.**
**Re-read drives** re-verifies every copy from the disk. Do this before wiping
cards and after a drive has travelled.

**Two jobs at once.**
Not possible by design. The interface returns "Another job is already running".

---

*This manual describes Second Hand Smoke 1.0 after the August 2026 audit. The safety
contract in section 2 is the part to hold the code to; everything else is
implementation.*

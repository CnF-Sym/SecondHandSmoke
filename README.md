<img src="assets/logo.png" alt="Second Hand Smoke" width="420">

# Second Hand Smoke

*Verified card offload for camera and sound media. A CnF Works Project.*

Free and open source, GPL-3.0. macOS.

Second Hand Smoke copies camera and sound cards to multiple drives, reads every
byte back off each drive and checksums it, and refuses to tell you a card is
safe to format until it genuinely is.

It does offload, verification, media metadata, sound reports, paperwork,
relink-safe ProRes proxies, deterministic timecode sync with a conservative
waveform-correlation fallback, and a checksummed editorial handoff with review
dailies.

It will not format a card, upload anything, phone home, or ask you for a
licence key. It runs entirely on your machine, bound to loopback.

> **Status: early public release.** This has done real shoot days, and the test
> rig is thorough, but it has been used by a small number of people on a small
> number of camera and recorder models. Run it alongside whatever you use now
> for a shoot or two before you trust it alone. If it disagrees with your
> current tool about anything, that is a bug report I want.

---

## Why this exists

The paid tools in this space are good. Silverstack is a full DIT station and
prices accordingly; ShotPut Pro and OffShoot are solid one-time purchases that
copy, verify and report.

What none of them publish is the *exact* strength of their verification, and
none of the affordable ones remember your footage after the copy finishes.
Second Hand Smoke answers both:

- **The safety contract below is the whole product**, written down, enforced in
  code, and testable. Read it and decide whether you trust it. That offer is
  the point.
- **Verification bypasses the OS page cache.** A read-back that hits RAM proves
  nothing about the drive. Ours proves the drive.
- **The catalog outlives the shoot.** Which drive is Tuesday's A-cam on, four
  months later? That question is what the catalog is for, and where this
  project is headed next. See `ROADMAP.md`.

It is not trying to be Silverstack. There is no RAW playback, no scopes and no
look management, and there are no plans for any of them — those need vendor
SDKs and a full-time team. If you need on-set colour, buy Silverstack; it is
worth the money.

---

## The safety contract

These are the rules the code actually enforces, not aspirations:

1. **Source media is opened read-only.** Nothing is moved, renamed, deleted or
   written on a card. Ever.
2. **Every byte is read back.** Verification re-reads from the destination
   drive with the macOS page cache bypassed (`F_NOCACHE`), so it tests the disk
   rather than the copy of the data still sitting in RAM. If cache bypass fails,
   verification fails rather than quietly weakening the guarantee.
3. **Two physical destinations minimum.** Folders, aliases or partitions on
   the same physical device count as one. A destination on the source card's
   device is rejected. `DIT_MIN_COPIES` can raise the requirement, never lower
   it below two. Disk images (`.dmg`/`.sparsebundle`) resolve to whatever real
   drive their backing file sits on, so two images on one SSD — or an image
   and the drive holding it — collapse to a single identity. The format gate
   counts distinct physical drives, not destination entries.
4. **Interruption is safe.** Files are written to `.partial` and only renamed
   into place after a full write and `fsync`. Yank a drive mid-copy and restart —
   it resumes and re-verifies rather than starting over.
5. **The tool never formats anything.** It tells you when a card is clear. You
   format it in Disk Utility, deliberately, with your own hands.
6. **The card is checked twice.** Registration fingerprints paths, sizes and
   beginning/middle/end content samples. The complete inventory is checked
   again before and after offload. New, changed, unreadable or skipped files
   close the format gate.

Hash is xxHash64 — not cryptographic, and not meant to be. It catches bad
cables, dying cards, half-written files and bit rot roughly ten times faster
than MD5, which is the entire job.

---

## Install

Download `SecondHandSmoke-<version>.dmg` from the
[releases page](https://github.com/CnF-Sym/SecondHandSmoke/releases), open it,
and drag **Second Hand Smoke** to Applications. Double-click it. That is the
whole installation — no Terminal, no Python to install, nothing to configure.

**If macOS says the app is from an unidentified developer**, the build you have
is not notarized. Right-click (or Control-click) the app in Applications and
choose **Open**, then **Open** again in the dialog. macOS remembers the
decision; every launch after that is an ordinary double-click. This is the same
step every unsigned Mac app needs, and the reason it exists is worth knowing:
Apple charges an annual fee for the certificate that removes it.

**macOS will ask for permission to access removable volumes** the first time
the app looks for a card. Say yes — reading cards is the entire job. If you say
no, cards do not produce an error, they simply appear to be empty. If a card
that is plainly mounted shows nothing, look in **System Settings → Privacy &
Security → Files and Folders** before looking anywhere else.

**Install `ffmpeg` before the shoot** (Homebrew: `brew install ffmpeg`). It is
deliberately not bundled. Without it, offload, verification, checksums, MHL
manifests and the format gate all work exactly as normal; metadata, ProRes
proxies, timecode sync, the waveform fallback and dailies are unavailable, and
the interface says so rather than failing quietly.

Each launch writes a log to `~/Library/Logs/Second Hand Smoke/server.log`, and
the app menu has **Reveal Server Log in Finder**. That is the file to send if
something goes wrong.

### Running from source instead

Double-click `start.command`, or from Terminal:

```bash
./start.command
```

On first launch it creates a private Python environment and installs the core
packages plus the optional phase helpers. Then open <http://127.0.0.1:3838>.

Flask prints `WARNING: This is a development server` on startup. That warning is
aimed at people exposing Flask to the internet. This server is bound to
loopback, is reachable only from your own machine, and serves exactly one
operator — the warning does not apply, and swapping in a production WSGI server
would add a dependency without changing anything about how it runs. Bound to
loopback only, and the API additionally refuses any request whose `Host` is not
loopback and any state-changing request that did not come from the tool's own
page — so a web page open in another tab cannot reach in and stop an offload.

### Building the app yourself

See [`macos/README.md`](macos/README.md). Short version: install the Xcode
Command Line Tools and double-click **Build Second Hand Smoke.command**.

### Environment variables

These apply when running from source. The application sets `DIT_PORT` itself —
it reserves a free port so two copies cannot fight over one — and reads the
rest from the environment as normal.

| Variable | Default | Purpose |
|---|---|---|
| `DIT_DB` | `~/SecondHandSmoke/catalog.db` | Catalog location |
| `DIT_SCAN_ROOT` | `/Volumes` | Where to look for cards |
| `DIT_MIN_COPIES` | `2` | Verified copies before a card clears; values below 2 are ignored |
| `DIT_PORT` | `3838` | Web UI port |
| `DIT_FFPROBE` | detected automatically | Optional explicit path to `ffprobe` |
| `DIT_FFMPEG` | detected automatically | Optional explicit path to `ffmpeg` |
| `DIT_PROBE_TIMEOUT` | `90` | Per-file metadata timeout in seconds |
| `DIT_OPERATOR` | login account | Name written into the MHL `<name>` field |
| `DIT_BROWSE_ROOT` | — | Extra folder the picker is allowed to list |
| `DIT_SLATE_FONT` | detected automatically | Font used to draw the dailies slate |

`ffprobe` and `ffmpeg` are found on `PATH` and, failing that, in the usual
Homebrew and MacPorts locations. That second search is what makes the packaged
app work: an app launched from Finder does not inherit a login shell's `PATH`.

To rehearse without hardware, point `DIT_SCAN_ROOT` at a folder of fake cards.
`test_rig.py` builds and exercises exactly that.

---

## On-set procedure

The header carries a live step tracker (Day → Destinations → Ingest cards →
Sync → Proxies → Handoff) with a red/green LED on each step, so at a glance
you can see what's done and what's next; clicking a step scrolls to it.

1. **Start day** — name it `DAY_01` and set the date.
2. **Fill the three destination slots** — Drive A (primary), Drive B (backup),
   and Cloud/offsite. The gate only requires two physically separate drives
   (`DIT_MIN_COPIES`), but the third slot is there because it's the copy that
   should leave the location. "Cloud/offsite" is just a folder — a mounted
   sync folder (Dropbox, Backblaze, a NAS) works exactly like a local path.
3. **Scan**, then hit the single **Ingest card** button next to whatever it
   finds. That one button registers the card (if it's new) and starts the
   verified copy to every destination — no separate Register-then-Offload
   step needed. "Register only" is still there under it for anyone who wants
   to inspect a card before committing to a multi-hour copy.
4. Metadata is read automatically after a successful offload. **Read
   metadata** reruns it manually after installing ffmpeg or editing a sound
   report — this now also works after the card has been safely unmounted and
   returned, by reading the verified primary-drive copy instead.
5. Wait for the slate's lamp to turn green — **SAFE TO FORMAT**. **Write
   manifest.** Only then does the card go back in the bag as clear. The gate
   also does a live check of every verified copy on any drive that's still
   connected, so a file that gets altered or a drive that gets swapped after
   verification reopens the gate immediately rather than staying stale until
   the next manual re-read.
6. Once camera and sound metadata are present, hit **Sync day's footage**.
   Clean matches, sequential sound takes, ambiguous overlaps and missing
   timing metadata are reported explicitly. Re-run it after any metadata
   refresh.
7. Choose **Check TC waveforms** to independently verify single-take timecode
   relationships. If TC leaves anything unresolved, the same control becomes
   **Waveform fallback**. Review amber suggestions rather than treating them
   as accepted sync.
8. **Build all proxies** walks every eligible roll automatically, one
   drive-safe job at a time, instead of clicking into each card. It reads the
   verified primary-drive masters after the safety work is complete — the
   source card may already be unmounted. **Day report** records the proxy
   state beside every camera clip.
9. Choose **Build Resolve handoff**. It reads the verified working drive, not
   the cards. If sync needs attention, the package carries explicit review
   markers instead of guessed production sound.

**Re-read drives** re-verifies copies that already say verified. Use it before
wiping cards, and after a drive has travelled. It is the only thing that catches
silent rot, because an offload run trusts the database and skips work it has
already done.

If a destination produces a wrong-size file, the tool repairs it and logs an
amber warning. A drive that does that twice should leave the set.

---

## Reports

Every shoot day produces an HTML report — destinations, per-card format
clearance, media metadata, sync, waveform results and a file-by-file
verification table showing the state of every file on every drive.

The same day can also be produced as a **branded PDF**, which is what producers
and post houses actually want in an email. Set a production name and a logo in
the **Reports** panel, then choose a layout:

- **Verification PDF** — the dense manifest. Every file, its size, its xxHash64,
  and its verified/failed state on each destination drive.
- **Metadata PDF** — every clip with scene, take, duration, start timecode,
  format, audio and proxy state.

Both carry the SAFE TO FORMAT or DO NOT FORMAT stamp for each card, and both
are generated from **one reading of the catalog**. The PDF writer cannot query
the database at all; it is handed the same structure the HTML report was handed,
including the same format-gate verdict, in the same call. Two documents that
could disagree about whether a card is safe to format is the one bug this
program exists to prevent, so they are not two code paths.

The HTML report is unchanged and is not going anywhere. The PDF is an addition.

PDFs need `reportlab`, which is optional in the same way NumPy and Pillow are:
without it the PDF buttons explain that they are unavailable and everything
else — the offload, the verification, the HTML report, the format gate — is
untouched. `start.command` installs it if it can. Inside the macOS application
it is always present.

---

## Output layout

```
DRIVE_A/
└── PROJECT_01/
    └── DAY_01/
        ├── CAMERA/A001/CONTENTS/CLIPS001/…    # card structure preserved
        ├── SOUND/CRACK_R01/…
        ├── PROXY/A001/A001C001_…_PXY.mov      # ProRes Proxy 1080p
        ├── HANDOFF/
        │   ├── DAILIES/A001/A001C001_…_DAILY.mp4
        │   ├── DAY_01_RESOLVE.fcpxml
        │   ├── DAY_01_METADATA.ale
        │   ├── DAY_01_MEDIA.json
        │   ├── README_HANDOFF.txt
        │   └── CHECKSUMS.txt
        ├── DAY_01_A001.mhl                    # per-card manifest
        └── REPORTS/ingest_DAY_01.html
```

The card's own directory structure is preserved inside the roll folder. Canon's
`CONTENTS/CLIPS001` tree carries clip metadata that some tools expect to find
next to the MXF, so it is not flattened.

The `.mhl` is an original-format MHL 1.1 XML manifest using XXHash64. It is only
written after the complete card is green on every required destination. If this
application vanishes, the manifest remains human-readable and independently
verifiable.

---

## Storage planning

Work out your budget before the shoot, not on the day. As a worked example:
XF-AVC 2K/HD at 160 Mbps is roughly **72 GB per hour**; at 310 Mbps Intra it is
roughly **140 GB per hour**. Twelve hours of footage, three copies, lands
somewhere between 2.6 TB and 5 TB.

A fast SSD makes a good working drive; camera originals are usually happier on
larger spinning storage. The format gate counts distinct physical drives, so
plan for at least two real ones.

---

## What the current build deliberately does not do

- No direct control of Resolve, no project-database writes and no cloud upload.
  Phase 6 creates an interchange package that remains inspectable without Resolve.
- No deleting, formatting or trashing. Not planned, ever.
- One job at a time. Two cards racing for the same USB bus is slower than
  doing them in sequence, and much harder to reason about when it goes wrong.

## Phase 2 metadata

`ffprobe` reads each camera and sound file without decoding or modifying it.
The catalog stores duration, codec, resolution, frame rate, embedded start
timecode, audio format and BWF `TimeReference`. Recorder CSV reports are matched
by filename and add scene, take, notes, circle status and sound roll. Metadata
failures are visible in amber but never weaken or invalidate a verified copy.

The day report has a media summary above the per-file verification table.

## Phase 3 proxies

**Build proxies** creates 1920×1080 Apple ProRes Proxy QuickTimes on the
destination marked `primary`. It never reads from the camera card. Each file is
decoded from the already verified primary master and receives a QuickTime
timecode track plus the card roll as reel metadata. Audio is carried as 48 kHz
24-bit PCM.

The worker is resumable: completed outputs are checksum-checked, inspected and
skipped on the next run. A missing, corrupted, truncated, wrong-profile or
wrong-timecode output is rebuilt. New work is written to `.partial.mov`, flushed, inspected with
`ffprobe`, and atomically renamed only after all checks pass. **Rebuild
proxies** deliberately regenerates the complete roll.

Proxy failures never affect checksums, copy records or the format gate. A
failed proxy is inconvenient and regenerable; a failed original is a safety
event. The interface and day report keep that distinction explicit.

## Phase 4 timecode sync

**Run timecode sync** turns timing metadata into persistent camera-to-sound
relationships. Camera timecode is converted at the clip's real frame rate,
including SMPTE drop-frame numbering. For BWF audio, the authoritative start
is the 64-bit BEXT `TimeReference`: the number of audio samples from timecode
midnight to the first sample. The small iXML chunk supplies the recorder's TC
rate and drop/non-drop flag when present. Matches also account for clips that
cross midnight.

One sound take overlapping a camera clip is **matched**. Sequential takes
inside a long camera clip are retained as **multiple**. Competing takes that
overlap one another are **ambiguous** and are never silently chosen. Missing or
malformed camera timing is **invalid**, and a clip with no overlap is
**unmatched**. Split mono files with the same roll, scene, take, start and
duration are grouped as one recorder take while each file relationship is
preserved.

This phase is metadata-only. It does not trim, render, rename, move or rewrite
camera originals, sound, or proxies. Refreshing media metadata invalidates old
relationships, and the next run rebuilds them from scratch. Sync status and
per-link offsets/overlaps appear in the live screen and day report. None of
these states can turn **SAFE TO FORMAT** on or off; only verified original
copies control that gate.

## Phase 5 waveform fallback

Waveform analysis also remains metadata-only. It decodes audio from the
already verified masters on the destination marked `primary`; source cards can
be unmounted. `ffmpeg` reduces each track to a small band-limited mono
fingerprint, and NumPy performs normalized FFT cross-correlation. Polarity is
ignored, so an inverted scratch track can still match.

The fallback first checks simple single-take TC relationships. A unique strong
waveform that agrees leaves the TC link intact and records it as **confirmed**.
If the same sound take is correct but the TC offset differs by more than 100 ms,
the high-confidence waveform offset replaces it and the relationship records
`waveform` as its method. Unresolved, invalid or ambiguous TC clips are compared
against recorder-take groups across the day.

Automatic acceptance requires all three safeguards: normalized score at least
`0.62`, at least `0.08` separation from both the runner-up take and the next
independent peak, and at least three seconds of overlap. Scores from `0.35` to
the acceptance boundary become **review** suggestions with no accepted link.
Lower scores remain **unmatched**. Silent audio, decoder errors and overly long
inputs fail visibly rather than producing guesses.

Split mono recorder files remain one take group, while an accepted relationship
keeps a link to every member. The catalog and day report retain the score,
runner-up margin, offset, overlap, prior TC state and top candidates. Re-running
timecode sync or refreshing metadata deliberately invalidates waveform results
so stale analysis cannot survive changed facts.

NumPy is optional for the safety-critical app: if it cannot be installed,
verified offload, format clearance, metadata, TC sync and proxies still launch
and work. The interface simply marks waveform sync unavailable.

## Phase 6 editorial handoff

**Build Resolve handoff** creates one deterministic `HANDOFF` folder on the
primary working drive. It requires verified originals, current ProRes proxies,
complete camera timing and one completed timecode-sync pass. Source cards can
be unmounted. Unmatched, ambiguous or review clips do not block the export, but
they are marked **SYNC REVIEW** in the timeline, review slate and operator
notes; the tool never invents a production-audio relationship.

The Resolve timeline is Unicode FCPXML 1.10. It references the existing ProRes
proxies and attaches only accepted production WAV links at their stored
subframe offsets. Each camera clip also appears in the event browser, and the
stringout contains a scene/take/sync marker. The accompanying ALE exposes reel,
source TC, scene, take, sound roll, circle status, notes and sync method. A JSON
manifest records original/proxy/sound paths, xxHash64 values, waveform
confidence and exact offsets for relinking or independent tooling.

`DAILIES` contains 1280×720 H.264/AAC viewing copies. A system-font slate shows
filename, roll, scene/take, source start TC and sync state. Accepted production
sound is aligned from the catalog; unresolved clips retain camera scratch audio
and carry an amber review label. These files are for review, not online
finishing.

Every document and daily is covered by `CHECKSUMS.txt`. Completed dailies are
checksum-checked and inspected on resume; a changed or truncated file is
rebuilt. A sync or proxy change marks the package stale. Interrupted runs leave
`EXPORT_INCOMPLETE.txt` until a complete resume succeeds. Phase 6 never changes
copy verification or **SAFE TO FORMAT**.

Pillow is optional for the safety-critical app and is used only to draw the
offline review slate. If Pillow or the required ffmpeg encoders are missing,
the interface marks Phase 6 unavailable while every earlier phase still works.

## What's shipped

| Phase | Scope | State |
|---|---|---|
| 1 | Offload, verify, MHL, format gate, day report | **Done** |
| 2 | Media metadata, organise refinements, sound report parsing | **Done** |
| 3 | ProRes Proxy transcode via ffmpeg, TC and reel embedded | **Done** |
| 4 | Timecode sync from BWF `bext` + camera start TC | **Done** |
| 5 | Waveform cross-correlation fallback | **Done** |
| 6 | Dailies export, Resolve handoff | **Done** |
| 7 | The library — search and locate footage months later | **Next** |

`ROADMAP.md` has the detail on where this goes next and what is deliberately
out of scope.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app and JSON API |
| `db.py` | SQLite schema and connection handling |
| `cards.py` | Card detection, classification, content fingerprinting |
| `offload.py` | The copy and verify engine |
| `reports.py` | Format gate, MHL writer, report data and the HTML report |
| `pdfreport.py` | The branded PDF report, rendered from `reports.day_data()` |
| `media.py` | ffprobe metadata and recorder CSV sound-report parsing |
| `proxies.py` | Resumable ProRes Proxy queue, validation and progress |
| `sync.py` | Frame-accurate camera/sound overlap matching and relationships |
| `waveform.py` | Verified-master audio fingerprints and conservative FFT matching |
| `handoff.py` | Checksummed review dailies and FCPXML/ALE/JSON handoff queue |
| `storage.py` | Physical-drive identity, free-space and path-safety checks |
| `version.py`, `VERSION` | The version number, read from one file |
| `toolpaths.py` | Finding ffmpeg and ffprobe, from a shell or from Finder |
| `server_main.py` | Server entry point used inside the macOS application |
| `macos/` | The Mac application: Swift shell, build and DMG scripts |
| `SecondHandSmoke.spec` | PyInstaller recipe that freezes the server |
| `test_rig.py` | End-to-end tests including corruption and rot detection |
| `MANUAL.md` | Full operator and technical manual |
| `index.html`, `app.js`, `app.css` | Offline web UI |
| `requirements.txt` | Locked major versions for Python dependencies |
| `start.command` | Double-clickable launcher, for running from source |
| `Build Second Hand Smoke.command` | Double-clickable app build |
| `Make Second Hand Smoke DMG.command` | Double-clickable release build |
| `push.command` | Double-clickable git commit and push |
| `ROADMAP.md` | Where this is going, and what it will never do |
| `assets/` | The wordmark and application icon |
| `macos/AppIcon.icns` | The application icon, in the format macOS wants |
| `LICENSE` | GPL-3.0 |

Run the tests any time you change something:

```bash
.venv/bin/python test_rig.py
```

The test builds disposable synthetic camera and sound cards, offloads them, then
deliberately changes the source and corrupts, truncates and deletes destination
files to confirm each fault is caught. It never touches mounted media.

---

## Support

**<hello@cnfworks.com>**

The same address is in the app's footer, at the bottom of every day report, and
inside the editorial handoff notes — so whoever is holding the problem at 4am
can find it on whatever is in front of them.

Verification bugs first. If this ever calls a card safe when it wasn't, say so
immediately and everything else waits.

When you write, the useful things to include are the version from the footer,
what you were doing, and — if the app would not start —
`~/Library/Logs/Second Hand Smoke/server.log`. Never send footage.

Bugs and feature requests are also welcome as
[GitHub issues](https://github.com/CnF-Sym/SecondHandSmoke/issues), which have
the advantage of being searchable by the next person with the same problem.

---

## Contributing

Bug reports about *verification* are the most valuable thing you can send. If
this tool ever calls a card safe when it wasn't, that is a stop-everything
issue — open an issue with the camera or recorder model, the filesystem, and
the day report if you still have it.

Camera and recorder coverage is the other gap. If a card from your camera is
mis-detected, or a sound report from your recorder doesn't parse, send a
directory listing (names and sizes only — no footage) and it can usually be
fixed quickly.

Run the test rig before opening a pull request:

```bash
.venv/bin/python test_rig.py
```

## Licence

GPL-3.0-or-later. See `LICENSE`.

In short: use it for anything, including paid work, on any production you like.
If you distribute a modified version, it has to stay open under the same
licence. Nobody gets to close this and sell it back to you.

## Warranty

There is none — see sections 15 and 16 of the licence. This software's whole
purpose is to tell you when your footage is safe, and it is built to be
conservative about that, but you are the one responsible for your media. Keep
your originals until the show is delivered.

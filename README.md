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

## Don't trust it. Check it.

You are being asked to let an unfamiliar program tell you when it is safe to
erase the only copy of a day's work. You should not take that on faith, and you
do not have to.

```bash
.venv/bin/python test_rig.py
```

That runs in under a minute on any Mac and needs no hardware. It builds
synthetic camera and sound cards, offloads them for real, and then attacks the
result: it corrupts a file without changing its size, truncates another,
deletes a third, alters a source file after registration, silently rots a copy
that had already been verified, collapses two destinations onto one physical
drive, and cancels jobs mid-write. After each one it checks that the format
gate closed, that the reason given is the right reason, and that nothing on the
simulated card was touched.

More than **500 checks across 38 sections**. The ones that matter most:

| Section | What it proves |
|---|---|
| **[4], [5]** | A verified offload, and that the source card is byte-for-byte unchanged |
| **[7]** | Same-size corruption, truncation and deletion are each caught |
| **[8]** | Silent rot in an already-verified copy is caught on re-read |
| **[16]** | A cancelled offload leaves no partial files and cannot clear a card |
| **[18]** | A copy corrupted on a connected drive reopens the gate immediately |
| **[19]** | Two folders on one physical drive count as one, and cross-site requests are refused |
| **[33]** | A card with filtered files can never read plain SAFE TO FORMAT |
| **[37]** | A manifest altered after it was written is caught by the chain that covers it |
| **[38]** | The library changes nothing, and never reports an unchecked drive as an empty one |

If any of that fails on your machine, do not use this, and please tell us:
**<hello@cnfworks.com>**.

This is not a claim that the program is correct. It is a claim that you can
find out for yourself in a minute, without taking anyone's word for it — and
that is a claim worth more than any feature list.

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

A rail under the header shows where the day is: Day → Destinations →
Ingest cards → Sync → Proxies → Handoff. Each step carries a count, the
current one is filled, and a line underneath says in plain words what to
do next and why. Click any step to jump to its panel.

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
   connected, so a file that gets **deleted, truncated or changed in size**, or
   a drive that gets swapped after verification, reopens the gate immediately
   rather than staying stale until the next manual re-read. That check is a
   `stat`, not a checksum — **same-size corruption is not caught by it** and
   needs **Re-read drives**, which reads every byte back with the cache
   bypassed. It is a cheap tripwire between full re-reads, not a substitute
   for one.
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

## Presets

The same show comes back tomorrow with the same three drives, the same project
and the same logo on the paperwork. Setting that up by hand every morning is
how a drive ends up in the wrong slot.

Save a day's setup as a preset and put it back on the next day in one click.
Presets group into folders, so a season or a client is one list rather than
twenty. A preset holds the destinations and their roles, the project name and
the report branding — including its own copy of the logo, so two shows do not
end up sharing whichever logo was set up last.

Two things a preset deliberately does **not** do:

- **It cannot loosen anything.** Drives are attached through exactly the same
  function the interface uses, with the same path checks, the same access
  checks, and the same refusal to count two folders on one physical disk as two
  drives. There is no second, slacker route to a destination.
- **It does not rename anything.** Applying a preset never changes an existing
  day's project name — that day already has folders on three drives with that
  name in the path. The preset's project is reported, for a day you have not
  made yet.

Applying is never all-or-nothing. Three drives where two are mounted is an
ordinary morning, so each destination is reported on its own: attached, already
there, or refused with the reason. And if the drive currently at a familiar
path is not the physical drive the preset was saved with, it is attached and
you are told — drives get rotated, cloned and relabelled, but "SHOW_A already
has Monday on it" quietly ceasing to be true is worth a sentence.

## Checksums

xxHash64 by default — fast enough to keep up with a card reader, which matters
because a verification nobody has time to run is a verification nobody runs. It
is also what almost every offload tool writes, so manifests interchange cleanly.

Some archives and delivery specs mandate a particular algorithm by name, so the
checksum is a per-day setting: **xxHash64**, **xxHash128**, **xxHash3**,
**MD5**, **SHA-1** or **C4 ID**. Per day rather than global, because a manifest
covers a day's worth of files and every hash in it has to be the same kind of
thing.

Which manifests you get depends on the choice, and the interface says so before
you make it:

| Checksum | ASC MHL 1.1 | ASC MHL 2.0 |
|---|---|---|
| xxHash64 (default) | `<xxhash64be>` | `<xxh64>` |
| MD5 | `<md5>` | `<md5>` |
| SHA-1 | `<sha1>` | `<sha1>` |
| xxHash128 | — | `<xxh128>` |
| xxHash3 | — | `<xxh3>` |
| C4 ID | — | `<c4>` |

Two rules:

- **The manifest never mislabels a digest.** Each entry above is the name that
  schema actually defines for that algorithm, and a dash means that schema has
  no name for it — so no 1.1 file is written at all, rather than one written
  under a neighbouring element name. A manifest that lies about its own
  contents is worse than no manifest.
- **A card keeps the checksum it started with.** Changing it on a day that has
  already copied files is refused — half a card hashed one way and half the
  other is not a resumable state, and nobody can write a manifest for it.

Proxies, review dailies and thumbnails are always checked with xxHash64
whatever this is set to. Those are internal integrity checks on files this
program made; no archive policy is about them, and making them configurable
would only add a way for two things to disagree.

---

## Naming

Standardise names across cameras with a token pattern per card kind, saved in
a preset so it comes back tomorrow:

`{project}` `{day}` `{date}` `{roll}` `{card}` `{original}` `{scene}` `{take}`
`{camera}` `{index}`

`{original}` is the default, so **nothing is renamed unless you ask for it**.
Preview what a pattern would do before any copy starts — the interface lists
every file and what it would become.

The rules it holds to:

- **Renaming happens on the destination. The card is never written to.** A
  rename on a card is an edit to the only copy in existence.
- **The manifest records both names.** ASC MHL 2.0 has an element for it:
  `<path>` is the name the file was written under and `<previousPath>` is the
  name it had on the card. ASC MHL 1.1 has no such element and does not allow
  extra ones, so there the old name goes in an XML comment above each hash plus
  a `_NAMES.csv` beside the manifest — a manifest Silverstack or ShotPut
  refuses to read is worse than no manifest.
- **Sidecars stay with their clip.** Files sharing a stem are renamed as a
  group, so `CLIP.MXF` and `CLIP.XML` never come apart.
- **The extension is carried over exactly**, case included.
- **Names are deterministic.** `{index}` is computed from the card's own files
  in sorted order, never from job progress, so an interrupted offload resumes
  onto the identical paths instead of copying a second file beside the first.
- **Collisions are refused, never suffixed.** If a pattern would write two
  files to one name, the offload will not start and both files are named. Two
  takes nobody can tell apart six weeks later is worse than a job that stops.
- **Changing the pattern on a half-copied card is refused**, because half a
  card named one way and half the other is not a resumable state.

Missing metadata degrades to the original name rather than to `A001__.MXF`.

## Filtering

Wildcard include/exclude patterns, per card kind, saved in a preset alongside
the naming pattern. Comma-separated shell-style globs matched against the
file's path on the card, case-insensitively; a pattern starting with `-`
excludes. `-*.THM, -*/PROXY/*` skips camera thumbnails and in-camera proxies.

**Read this part.** The format gate means one sentence: *everything on this
card is verified on two drives, so you can wipe it.* Skipping files makes that
sentence false. If the screen still said SAFE TO FORMAT, someone would format a
card that held the only copy of whatever the filter dropped — no checksum
wrong, no drive failed, the tool simply answering a different question than the
one being asked.

So a card with exclusions gets its own verdict and its own colour:

> **CLEARED WITH EXCLUSIONS — 2 file(s) were not copied**

Amber, never green. The skipped files are **named** on the card, in the day
report, in an `_EXCLUDED.csv` beside the manifest, and in a comment inside the
manifest saying it does not cover everything. You can still format the card.
You cannot do it believing the whole card was copied.

Three more rules:

- **Nothing is skipped without being recorded.** Excluded files stay in the
  catalog, marked as deliberately not copied.
- **A filter changed afterwards cannot rewrite history.** A file that already
  has a verified copy is never marked excluded, whatever the filter now says.
- **A filter that would skip everything is refused**, not obeyed.

Preview before you run. The interface lists exactly which files a filter would
drop, and previewing changes nothing.

---

## Reports

Every shoot day produces an HTML report — destinations, per-card format
clearance, media metadata, sync, waveform results and a file-by-file
verification table showing the state of every file on every drive.

The same day can also be produced as a **branded PDF**, which is what producers
and post houses actually want in an email. Put a production name in the
**Reports** panel and give it a logo — choose one with a file picker, drag one
onto the panel, or type a path, whichever is quickest. Then choose a layout:

- **Verification PDF** — the dense manifest. Every file, its size, its xxHash64,
  and its verified/failed state on each destination drive.
- **Metadata PDF** — every clip with scene, take, duration, start timecode,
  format, audio and proxy state.
- **Thumbnail PDF** — a contact sheet. One frame per camera clip with its name,
  scene and take, duration and start timecode. This is the one a producer
  actually reads: a page of frames answers "is this the day I think it is" in a
  second, which a wall of hashes does not.

Both carry the SAFE TO FORMAT or DO NOT FORMAT stamp for each card, and both
are generated from **one reading of the catalog**. The PDF writer cannot query
the database at all; it is handed the same structure the HTML report was handed,
including the same format-gate verdict, in the same call. Two documents that
could disagree about whether a card is safe to format is the one bug this
program exists to prevent, so they are not two code paths.

The HTML report is unchanged and is not going anywhere. The PDF is an addition.

**About the frames.** They are pulled with ffmpeg from the **verified copy on
the primary drive**, never from the card — the same rule the proxy builder
follows, and the reason report frames still work after a card has cleared and
been pulled. A frame is only ever made from a copy in the `verified` state; a
picture of a half-written file in a document that also says SAFE TO FORMAT is
the wrong thing to hand anyone.

Thumbnails are cosmetic and nothing about them can change a checksum, a copy
record, a manifest or the format gate. A clip that will not decode gets a
labelled empty space in the report rather than being quietly dropped, and
without ffmpeg the layout still builds and says why the frames are missing.

Frames are cached by content hash beside the catalog, so they never go stale
and re-generating a report does not re-decode the day. The first thumbnail
report of a big day takes a moment; every one after it is immediate. Nothing is
written to the production's drives.

**What happens to a logo you choose.** It is copied into this program's own
folder, not referenced where it sits. Two reasons, and neither is convenience: a
path into a show folder stops resolving the moment that drive is unmounted,
which is halfway through every shoot day; and the bytes are never stored as they
arrived. They are decoded, checked to really be an image, and re-encoded as PNG,
so what ends up in your reports is a file this program wrote. The image you
picked is read and nothing else — never moved, renamed or altered. Removing a
logo removes this program's copy and nothing of yours.

PDFs need `reportlab`, which is optional in the same way NumPy and Pillow are:
without it the PDF buttons explain that they are unavailable and everything
else — the offload, the verification, the HTML report, the format gate — is
untouched. `start.command` installs it if it can. Inside the macOS application
it is always present.

## Drive tools

**Speed test.** How fast this drive actually is for an offload, not what the box
says. It writes a real file, **fsyncs it** so the drive has genuinely taken the
bytes, then reads it back with the page cache bypassed and checksums what comes
off. The fsync is the honest part of the write figure — without it you are
measuring how fast the operating system accepted the bytes, which is a fine way
to report 3 GB/s from a drive that cannot do 200. If cache bypass cannot be
established, the read is reported as unverified rather than printed as a fast
number, because a read speed measured out of RAM is the same lie that makes an
unverified offload feel quick.

The scratch file goes in the drive's own `.shs-speedtest` folder and is removed
when the test finishes. It is the only file this program ever removes from a
drive.

**Drive index.** Walks a drive read-only and remembers what is on it — and
reads any ASC MHL manifest it finds along the way, ours or another tool's. A
manifest already lists the files and the day and roll they belong to, so a
drive that has ever been offloaded by anything that speaks MHL can say what it
holds without re-hashing a terabyte.

Drives are remembered by **volume identity, not mount point**. `/Volumes/SHOW_A`
is a place a different disk can be tomorrow; two folders on one physical drive
are one drive in the registry, the same way they are one destination to the
format gate. The registry shows which drives are online and what each holds.

An index is a snapshot and is never mistaken for a verification. Nothing in
drive tools writes to the copy records or touches the format gate — only the
`copies` table says a file is safe, and only a read-back with a matching
checksum puts it there.

---

## Library

Every offload tool forgets your footage the moment the copy finishes. This one
already knows every file, every hash, every drive it was written to — the
library is the part that lets you ask, across every shoot day at once.

Search by roll, scene, take, camera, codec, clip name or date. Then the
question worth asking:

> **Show me every clip on fewer than three physical drives.**

**Every row shows two numbers, and they are not the same claim.**

- **Verified** — how many separate physical drives this file was read back and
  checksum-matched on. A fact from the catalog. True whether or not anything is
  plugged in, and complete the moment you ask.
- **Present** — how many of those drives are connected *right now* and still
  have a file of the right size at the right path.

Present is never higher than verified, and the gap is the point. `3 verified ·
1 present` is a healthy row: two drives are on a shelf, which is where drives
are supposed to be. `3 verified · 0 present` when you thought all three were
plugged in is the thing you would otherwise find out about a year later.

So the audit asks you which question you meant:

- **on record** — which clips never got their third copy in the first place.
  Complete, instant, works with nothing connected.
- **right now** — which clips cannot be confirmed on three drives today. Needs
  the drives connected, and names every registered drive that was offline so
  that *could not check* is never mistaken for *not there*. If nothing is
  connected at all, it says so rather than reporting your entire library as
  missing.

Three things the library will not do:

- **Present is a size check, not a checksum.** Re-verifying a shelved drive is
  a real job with a progress bar. Quietly doing a cheap `stat` and calling the
  result verified would be exactly the weakening this program exists to refuse,
  so the interface says *a file of the right size is at that path*, in those
  words.
- **It never changes anything.** No writes to the catalog, no call to the
  format gate, no source card opened. A card's clearance is the gate's answer
  at the moment you ask it, never something a search moved.
- **It counts drives the way the format gate does** — one shared definition, so
  the program cannot end up with two different answers to "how many drives is
  this on".

Export to CSV or JSON, carrying both numbers, the drives that were offline, and
when it was true. A library that only exists inside this application is a
library that dies with it.

**The catch, said plainly:** this is a library of what *this program* copied.
Footage dragged across in the Finder is not in it. Drive indexing reads anyone's
ASC MHL manifest, so a drive offloaded by another tool can still tell you what
it holds — it just cannot tell you it was verified, because this program did
not verify it.

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
        ├── ascmhl/                            # ASC MHL 2.0
        │   ├── 0001_A001_2026-01-20_193000Z.mhl
        │   └── ascmhl_chain.xml
        ├── DAY_01_A001.mhl                    # ASC MHL 1.1, same facts
        └── REPORTS/ingest_DAY_01.html
```

The card's own directory structure is preserved inside the roll folder. Canon's
`CONTENTS/CLIPS001` tree carries clip metadata that some tools expect to find
next to the MXF, so it is not flattened.

Manifests are only written after the complete card is green on every required
destination. Both formats are rendered from one set of facts in one pass, so
the two files cannot disagree about what happened.

The `ascmhl/` folder is **ASC MHL 2.0**: one numbered manifest per card, and an
`ascmhl_chain.xml` that records the C4 ID of every manifest in the folder.
Alter a manifest afterwards to hide a bad file and the chain no longer matches
it — Second Hand Smoke says so, in the interface, the next time a card lands on
that drive, and it neither repairs nor deletes the evidence.

`DAY_ROLL.mhl` is **ASC MHL 1.1**, for the many tools that still expect one. It
is written whenever the day's checksum is one that 1.1 can name, and skipped —
never faked — when it is not.

If this application vanishes, both remain human-readable and independently
verifiable, and both are readable by Pomfort's reference `ascmhl` tool.

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
| 7 | The library — search, copy-count audit, export | **Done** |
| — | A real card, a real shoot, a notarized build | **Next** |

`ROADMAP.md` has the detail on where this goes next and what is deliberately
out of scope.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app and JSON API |
| `db.py` | SQLite schema and connection handling |
| `cards.py` | Card detection, classification, content fingerprinting |
| `offload.py` | The copy and verify engine |
| `reports.py` | Format gate, ASC MHL 1.1 writer, report data and the HTML report |
| `mhl2.py` | ASC MHL 2.0 manifests, the chain file and the C4 IDs in it |
| `library.py` | Search, the copy-count audit and export. Read-only |
| `pdfreport.py` | The branded PDF report, rendered from `reports.day_data()` |
| `thumbnails.py` | One cached frame per camera clip, for the report |
| `media.py` | ffprobe metadata and recorder CSV sound-report parsing |
| `proxies.py` | Resumable ProRes Proxy queue, validation and progress |
| `sync.py` | Frame-accurate camera/sound overlap matching and relationships |
| `waveform.py` | Verified-master audio fingerprints and conservative FFT matching |
| `handoff.py` | Checksummed review dailies and FCPXML/ALE/JSON handoff queue |
| `storage.py` | Physical-drive identity, free-space and path-safety checks |
| `destinations.py` | Attaching a drive to a day — the one route, used by both |
| `presets.py` | Saved job configurations and their folders |
| `drivetools.py` | Drive speed tests, indexing, and the drive registry |
| `naming.py` | Resolving a token pattern into destination names |
| `hashing.py` | Which checksum, and what a manifest may call it |
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

Everything must pass before a commit. See **[Don't trust it. Check it.](#dont-trust-it-check-it)**
above for what it actually does — it is the argument for using this program at
all, not a chore. Anything touching the verification path needs a test that
proves the *failure* is still caught, not just that the happy path works.

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

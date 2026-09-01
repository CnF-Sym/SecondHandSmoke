# Second Hand Smoke v0.3.0 — CnF Works 2026

Saved setups, standardised names, and a third answer from the format gate.

## Presets and preset folders

The same show comes back tomorrow with the same three drives, the same project
and the same logo. Save a day's setup as a preset and put it back on the next
day in one click. Presets group into folders, and each one keeps **its own copy
of its logo**, so two shows do not end up sharing whichever was set up last.

A preset cannot loosen anything. Drives are attached through exactly the same
code the interface uses — same path checks, same access checks, same refusal to
count two folders on one physical disk as two drives. There is no second,
slacker route to a destination.

Applying is never all-or-nothing: three drives where two are mounted is an
ordinary morning, so each is reported on its own as attached, already there, or
refused with the reason. And if the drive at a familiar path is not the physical
drive the preset was saved with, it is attached **and you are told** — drives
get rotated and relabelled, but "SHOW_A already has Monday on it" quietly
ceasing to be true is worth a sentence.

## Token-based renaming

Standardise names across cameras with a pattern per card kind, saved in the
preset: `{project} {day} {date} {roll} {card} {original} {scene} {take}
{camera} {index}`. `{original}` is the default, so **nothing is renamed unless
you ask for it**. Preview shows every file and what it would become before a
byte moves.

- **Renaming happens on the destination. The card is never written to.**
- **The manifest records both names.** The MHL's `<file>` carries the name the
  file was written under; an XML comment above each hash carries the name it
  had on the card; a `_NAMES.csv` beside the manifest is the machine-readable
  map. The MHL stays valid against the ASC MHL 1.1 schema, which permits no
  extra elements — a manifest Silverstack or ShotPut refuses to read is worse
  than no manifest.
- **Sidecars stay with their clip.** `CLIP.MXF` and `CLIP.XML` never come apart.
- **Names are deterministic**, so an interrupted offload resumes onto the same
  paths instead of copying a second file beside the first.
- **Collisions are refused, never suffixed.** Two takes nobody can tell apart
  six weeks later is worse than a job that will not start.
- **Changing the pattern on a half-copied card is refused.**

## Filtering, and a third verdict

Wildcard include/exclude patterns per card kind. `-*.THM, -*/PROXY/*` skips
camera thumbnails and in-camera proxies.

**Read this part.** The format gate has always meant one sentence: everything
on this card is verified on two drives, so you can wipe it. Skipping files
makes that sentence false. If the screen still said SAFE TO FORMAT, someone
formats a card that held the only copy of what the filter dropped — no checksum
wrong, no drive failed, the tool simply answering a different question than the
one being asked.

So the gate now has three answers:

| Verdict | Colour |
|---|---|
| `SAFE TO FORMAT` | green — every file on the card is verified on 2+ drives |
| `CLEARED WITH EXCLUSIONS — n file(s) were not copied` | amber — everything meant to be copied is verified; n files were deliberately skipped and are named |
| `DO NOT FORMAT` | red — something is missing, short of copies, or changed |

The skipped files are **named** on the card, in the day report, in an
`_EXCLUDED.csv` beside the manifest, and in a comment inside the manifest
saying it does not cover everything. You can still format the card. You cannot
do it believing the whole card was copied.

Exclusion is decided when an offload starts and at no other time. Changing a
filter does not touch a card that is not being offloaded, and "Re-read drives"
never applies filters at all. A file that already has a verified copy is never
marked excluded whatever the filter now says, and a filter that would skip
every file on a card is refused rather than obeyed.

## Report thumbnails

A third PDF layout: a contact sheet, one frame per camera clip with its name,
scene and take, duration and start timecode. A page of frames answers "is this
the day I think it is" in a second, which a wall of hashes does not.

Frames are pulled from the **verified copy on the primary drive**, never from
the card, and only from a copy in the `verified` state. Thumbnails are cosmetic
throughout: nothing about them can change a checksum, a copy record, a manifest
or the format gate, and a clip that will not decode gets a labelled empty space
rather than being quietly dropped. Frames are cached by content hash on this
machine only, so re-generating a report does not re-decode the day and nothing
of ours is written to a production's drives.

## Nothing in the safety contract moved

Source media is still opened read-only. Every byte is still read back with the
macOS page cache bypassed, and verification still fails rather than quietly
weakening if that cannot be established. Two physically distinct drives are
still required. Interruption is still safe and resumable. The tool still never
formats, deletes or trashes anything, and still has no telemetry, accounts or
licence checks.

`test_rig.py` passes in full, including four new sections covering thumbnails,
presets, renaming and filtering — and the nineteen original sections, which
cover corruption, truncation, rot, cancellation and the two-drive rule, are
unchanged and still green.

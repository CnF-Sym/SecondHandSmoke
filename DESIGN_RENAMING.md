# Design — token renaming and wildcard filtering

*Second Hand Smoke · A CnF Works Project · written for review before any code*

This is the highest-risk feature on the list. The MHL manifest, the catalog and
the proxies all key off paths, and filtering can quietly break the one promise
the program exists to make. Nothing below has been built. It needs your
decisions on the six questions at the end first.

---

## 1. Why this is tractable at all

There is exactly one function in the program that decides where a source file
lands:

```python
offload.dest_path_for(dest_root, project, day_name, card, rel_path)
```

Everything else derives from what it returns. `copies.dest_path` records it,
the MHL writes `os.path.relpath(dest_path, root)`, and the format gate counts
verified copies by `file_id` and never looks at a name at all.

So renaming is a change to that one function plus a preflight. It does **not**
touch the copy loop, the hashing, the read-back or the gate. That is the whole
shape of the plan, and if it ever stops being true, the plan is wrong.

## 2. What is anchored to what

| Thing | Keyed by | Effect of renaming |
|---|---|---|
| `files.rel_path` | the **source** name | **Never changes.** This is the anchor |
| `files.src_hash` | content | unaffected |
| `copies.dest_path` | the destination name | this is where the new name lives |
| Format gate | `file_id`, copy states | unaffected |
| MHL `<file>` | `relpath(dest_path, root)` | picks up the new name for free |
| Sync / waveform | `file_id` | unaffected |
| Proxies | `rel_path` → output stem | **needs a decision — question 4** |
| Handoff FCPXML / ALE / JSON | proxy and dest paths | follows whatever the above do |
| Thumbnails | content hash | unaffected |

The source card is never written to, renamed on, or reorganised. Renaming
happens on the destination only, which is what you asked for and also the only
version of this that is safe: a rename on a card is an edit to the only copy.

## 3. The naming pattern

A preset holds one pattern string per card kind. Tokens are `{name}`-style,
resolved from facts the catalog already has:

| Token | From | Example |
|---|---|---|
| `{project}` | day | `PROJECT_01` |
| `{day}` | day | `DAY_01` |
| `{date}` | day shoot date | `2026-01-16` |
| `{roll}` | card | `A001` |
| `{card}` | card label | `A001_CFEXPRESS` |
| `{original}` | source filename, no extension | `A001C001_260116AB_CANON` |
| `{scene}` / `{take}` | sound report or camera metadata | `12A` / `1` |
| `{camera}` | reel / camera id from metadata | `A` |
| `{index}` | position within the card | `0001` |
| `{ext}` | never a token — the extension is always carried over verbatim |

Rules the resolver must hold:

- **Deterministic.** The same card, pattern and day produce the same names on
  every run. An interrupted offload that resumes must land on the identical
  path or it will copy a second file beside the first. `{index}` is therefore
  computed from the card's files sorted by `rel_path`, **not** from job
  progress or arrival order.
- **Metadata may be missing.** `{scene}` on a card that has not been read yet
  is empty. An empty token collapses along with one adjacent separator rather
  than leaving `A001__001`. If a pattern resolves to an empty stem, the file
  keeps its original name and the report says so.
- **Filesystem-safe.** The resolved stem is restricted to letters, numbers,
  dot, dash, underscore and space; anything else becomes `_`. Length capped so
  the full path stays under the platform limit.
- **Extension untouched.** Always the source extension, unchanged, including
  its case. Cameras and NLEs both care.

## 4. Sidecars travel with their clip

Cameras write `CLIP.MXF` next to `CLIP.XML`. If the clip is renamed and the
sidecar is not, the pair is broken and no NLE will find the metadata again.

Proposal: files on the same card whose stems match, in the same folder, are
renamed **as a group** — one resolved stem, each keeping its own extension.
Anything that does not match a media file's stem keeps its original name.
Sound-report CSVs are never renamed, because `media.py` finds them by name.

## 5. The MHL, and both names

I checked the specification rather than guessing. The ASC MHL 1.1 XSD defines
`<hash>` as an `xs:sequence` of `file`, `size`, optional `creationdate`,
`lastmodificationdate`, one or more hash elements, and `hashdate`. There is no
`xs:any`, so **any extra child element makes the manifest schema-invalid**, and
a manifest that Silverstack or ShotPut refuses to read is worse than no
manifest.

So both names get recorded, but not by breaking the format:

1. **`<file>` carries the destination name**, as it does today — that is what
   the hash actually describes, and what a verifying tool needs.
2. **An XML comment above each `<hash>` carries the source name** when it
   differs: `<!-- source: A001C001_260116AB_CANON.MXF -->`. Comments are legal
   anywhere and ignored by every parser, so the manifest stays valid while the
   file itself still tells a human both names.
3. **A sidecar written beside the MHL** — `DAY_01_A001_NAMES.csv`, columns
   `original,renamed,size,xxhash64be` — is the machine-readable record. It also
   travels with the day rather than living only in this program's catalog.
4. The catalog remains authoritative: `files.rel_path` and `copies.dest_path`
   have always held both, and the day report gains an "Original name" column
   whenever any file on the card was renamed.

## 6. Collisions

`_target_collision()` already refuses to start when two source files would land
on one destination path, and names both. Renaming makes collisions far more
likely — `{scene}_{take}` across two cameras collides immediately — so that
check runs on the **resolved** names, before a single byte is copied.

It should keep refusing rather than auto-suffixing. A silent `_2` on a file is
how you get two takes that nobody can tell apart six weeks later. The preflight
names both files and the pattern that collided.

## 7. Filtering, and the thing that worries me

Wildcard include/exclude is the dangerous half of this feature, and not for the
reason it looks.

The format gate currently means one thing: **everything on this card is
verified on two separate drives, so you can wipe it.** If files can be excluded
from the copy, that sentence stops being true while the screen still says SAFE
TO FORMAT. Someone formats a card that still held the only copy of the files
the filter quietly dropped. No checksum was wrong. No drive failed. The tool
was just answering a different question than the one being asked.

I will not build it that way. Three options, in order of my preference:

**(a) Excluded files are tracked, and clearance is a different sentence.**
Excluded files are registered in the catalog like any other, with copy state
`excluded`. The gate then has two positive verdicts:

- `SAFE TO FORMAT` — every file verified on two drives. Unchanged.
- `CLEARED WITH EXCLUSIONS — 3 files were not copied` — in amber, never green,
  with the three files listed by name in the interface, the day report and the
  MHL sidecar. The operator can still format; they simply cannot do it while
  believing everything was copied.

**(b) Filtering only ever hides, never skips.** Everything is copied and
verified; the filter drives what the reports and the handoff show. Perfectly
safe, and probably not what you want, since the reason people filter is to not
copy 40 GB of camera-generated junk.

**(c) Excluded files are never registered.** Simplest, and the one I would
refuse to ship: the card's file list silently does not contain them, the gate
says SAFE TO FORMAT with a clear conscience, and the promise is broken in
exactly the way described above.

Filter syntax, whichever is chosen: shell-style globs against the source
relative path, case-insensitive, include patterns first then exclude patterns,
with a live preview in the interface showing which files a pattern would drop
**before** the job starts. A filter nobody previewed is a filter nobody
understood.

## 8. Where this lives, and how it is verified

- Patterns and filters live in the preset, per card kind, alongside the drives —
  which is why presets were built first.
- New module `naming.py`: pure functions, no database writes, no filesystem
  access. `resolve(pattern, facts)` and `plan(card_id, pattern, filters)`.
  Being pure is what makes it testable to the level this needs.
- `offload.dest_path_for()` gains the resolved stem as an argument. The copy
  loop, the hashing and the read-back are not touched.
- `reports.write_mhl()` gains the comment line and the sidecar.

Test rig additions, all of which must pass before it ships:

1. A resolved name is identical across a cancel and resume, and the resumed job
   copies no second file.
2. Renaming does not change any checksum, any copy record, or the gate.
3. The MHL still validates against the 1.1 schema with renamed files, and
   carries both names.
4. A colliding pattern is refused before any byte is copied, naming both files.
5. Sidecars stay with their clip.
6. A card with an active exclude filter can never report plain `SAFE TO
   FORMAT`.
7. Missing metadata degrades to the original name rather than to `A001__.MXF`.
8. A pattern cannot produce a path outside the destination root, and cannot
   produce `..`, a leading `/`, or a reserved name.

---

## What I need from you

1. **Filtering** — (a), (b) or (c) in section 7? I recommend (a), and I will
   not build (c).
2. **Proxy names** — should a proxy follow the renamed master, or keep the
   original camera name? I lean towards following the master, so a clip and its
   proxy share a stem.
3. **Sidecar grouping** — is section 4 right, that matching stems rename
   together?
4. **The `{index}` token** — per card, or per day? Per card is simpler and
   resume-safe; per day means two cards ingested in a different order produce
   different numbers.
5. **Default pattern** — should a fresh preset default to `{original}`, i.e.
   no renaming at all unless someone asks for it? I think yes.
6. **Retrofit** — renaming applies to new offloads only. A card already
   offloaded keeps the names it has. Agreed?

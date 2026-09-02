# The library — design

*Second Hand Smoke · CnF Works*

Written before the code, because there is one decision in here that decides
whether the library is trustworthy or just plausible.

---

## What it is for

Every offload tool forgets your footage the moment the copy finishes. The
catalog already knows every file, every hash, every destination it was written
to and which physical drive that destination was. It just doesn't let you ask
it anything once the shoot day is closed.

The question worth answering:

> **Show me every clip that currently exists on fewer than three physical
> drives.**

That is the question that saves a show, and no competitor answers it. It is
also, as written, a question this program cannot honestly answer — see below.

---

## The one decision: "exists" is not a thing the catalog knows

The catalog knows that on 16 January, file `A001C001.MXF` was read back off
`SHOW_A_01` and `SHOW_B_01` with the page cache bypassed and the checksums
matched. That is a fact about the past, and it is a strong one.

It does **not** know whether that file is on those drives right now. The drive
could be on a shelf, in a courier van, wiped, or dead. A library that answers
"3 copies" for a drive nobody has plugged in since February is telling someone
their footage is safe on evidence it does not have. That is the same failure
mode as a verification that reads out of the page cache — a confident number
that was never actually checked — and it is the thing this program exists to
refuse.

So the library never reports one number. It reports two, always, in the same
row:

| | meaning | comes from |
|---|---|---|
| **Verified** | read back and checksum-matched at some point, on N distinct physical drives | `copies.state='verified'` grouped by drive identity |
| **Present** | of those, how many drives are connected *right now* and still hold a file of the right size at the right path | live `os.stat` on connected drives only |

**Present is never higher than Verified**, and the gap is the point. A row
reading `3 verified · 1 present` is not a problem — it means two drives are on
a shelf, which is where drives are supposed to be. A row reading
`3 verified · 0 present` when you thought all three were plugged in *is* a
problem, and it is one you would otherwise find out about a year later.

The audit therefore has two modes, and the interface makes you choose which
you asked for rather than picking one for you:

- **"On fewer than N drives on record"** — the archive question. Which clips
  never got their third copy in the first place. Answerable offline, instantly,
  and it is a complete answer.
- **"Not confirmed on N drives right now"** — the shelf question. Needs the
  drives connected, and says plainly which registered drives were offline when
  it ran, so nobody mistakes "couldn't check" for "not there".

Anything the library cannot check, it says it could not check. It never
degrades to a guess.

---

## What counts as one drive

Reusing the format gate's own answer, not a second one.

`reports._device_map()` already decides when two destinations are the same
physical drive — that is what stops two folders on one disk clearing a card.
The library must count drives the same way, or the program will have two
different answers to "how many drives is this on" and the quieter one will be
wrong.

**Change:** lift that identity logic out of `reports.py` into one function both
call. No behaviour change to the gate; the rig's section [19] already proves
that behaviour and must keep passing untouched.

---

## What you can ask

Across the whole catalog, not one day at a time:

- Project, shoot day, roll, card
- Scene, take, circled — from `media_metadata`, which already holds the sound
  report's columns
- Camera / reel, codec, resolution, frame rate
- Shoot date, and date range
- Copy count, verified or present

Plus, per drive: what it holds, when it was last seen, and what is on it that
is on nothing else — the single-copy list, which is the other question worth
answering and is a one-line variant of the audit.

## What it can export

CSV and JSON, because a library that only exists inside this application is a
library that dies with it. The export carries the same two numbers and the same
"as at" timestamp as the screen.

---

## What it must never do

The library is **read-only**, in the same sense that thumbnails and drive tools
are read-only:

1. It never writes to `copies`, `files` or `cards`.
2. It never calls the format gate and never changes a verdict. A card's
   clearance is decided by the gate at the moment you ask it, not by anything
   the library found.
3. It never opens a source card.
4. It reads destination files with `os.stat` only — sizes and paths. It does
   **not** re-checksum. Re-verification is a real job with a progress bar,
   subject to the job gate, and confusing a cheap size check with a verified
   read would be exactly the weakening this program refuses. "Present" means
   *a file of the right size is at that path*, and the interface says so in
   those words.
5. A drive being offline is never an error, and never a red row. It is
   "not checked", in its own colour.

Rig section [38] proves 1–4 the same way [30] and [34] prove them for
thumbnails and drive tools: run the whole library over a populated catalog, and
assert the copies table, the gate verdicts and the source cards are
byte-identical before and after.

---

## Shape of the work

| | |
|---|---|
| `library.py` | New. Pure queries + the live presence check. No writes. |
| `storage.py` | Gains the shared drive-identity function lifted out of `reports.py`. |
| `app.py` | `GET /api/library/search`, `/api/library/audit`, `/api/library/export` |
| `app.js` | A library panel that is not tied to an open shoot day |
| `test_rig.py` | Section [38] |

No new dependency. No schema change — every column this needs already exists.

---

## The catch, stated plainly

This is a library of **what this program copied**. Footage offloaded by
ShotPut, Silverstack or a card reader and a Finder drag is not in it, and the
library will not pretend otherwise — it shows a drive's indexed contents
separately from its catalog contents, and says which is which. Drive indexing
(already built) reads any ASC MHL manifest it finds, so a drive offloaded by
another tool that speaks MHL can still tell you what it holds. It just cannot
tell you it was verified, because this program did not verify it.

# Second Hand Smoke v0.4.0 — CnF Works 2026

Six checksums instead of three, manifests that catch their own tampering, and
a library that stops the program forgetting your footage the moment the copy
finishes.

## The checksum is a choice

xxHash64 is still the default, so nothing changes for anyone who ignores this.
But some archives and delivery specs mandate an algorithm by name, and "we only
do xxHash64" loses that job for reasons that have nothing to do with whether
the tool is any good.

Six now, set per shoot day — because a manifest covers a day's files and every
hash in it has to be the same kind of thing:

| Checksum | ASC MHL 1.1 | ASC MHL 2.0 |
|---|---|---|
| xxHash64 (default) | `<xxhash64be>` | `<xxh64>` |
| MD5 | `<md5>` | `<md5>` |
| SHA-1 | `<sha1>` | `<sha1>` |
| xxHash128 | — | `<xxh128>` |
| xxHash3 | — | `<xxh3>` |
| C4 ID | — | `<c4>` |

A dash means that schema has no element name for that digest — so **no file of
that kind is written at all**, rather than one written under a neighbouring name
that does not mean what it says. A manifest that lies about its own contents is
worse than no manifest. The interface says which manifests a choice will produce
before you make it, so nobody discovers it after the shoot.

A card keeps the checksum it started with. Changing it on a day that has already
copied files is refused: half a card hashed one way and half the other is not a
resumable state, and nobody can write a manifest for it.

## ASC MHL 2.0

Every offload now also writes an `ascmhl/` folder beside the day's media,
rendered from the same rows and the same pass as the 1.1 sidecar — so the two
files sitting next to each other cannot disagree about what happened.

```
PROJECT_01/DAY_01/
├── ascmhl/
│   ├── 0001_A001_2026-01-20_193000Z.mhl
│   └── ascmhl_chain.xml
└── DAY_01_A001.mhl
```

**It is a chain, not a snapshot.** Each pass adds a numbered generation, and
`ascmhl_chain.xml` records the C4 ID of every manifest in the folder. Before
appending, every manifest the chain already covers is re-hashed. Alter one
afterwards to hide a bad file and Second Hand Smoke says so — in the interface,
the next time a card lands on that drive — and **neither repairs nor deletes
it**. Silently fixing a chain is the one thing a chain must never do.

**It records both names natively.** A renamed file gets a `<previousPath>`
element — a real part of the schema. What 1.1 could only put in an XML comment
for a human, 2.0 says to a parser.

**No root hash.** A directory hash covers everything under the root, and this
program writes one card at a time into a day folder that other cards are still
landing in — a root hash written after the first card would be wrong before the
second finished. `<processinfo>` says `transfer` and carries none, which is
valid and true.

`action="original"` on every digest, because every one was taken off the card
during the copy that produced the file, not recomputed from the destination
afterwards.

The C4 encoder was cross-checked byte-for-byte against Pomfort's reference
`ascmhl` implementation, and that implementation parses everything written here
and loads the chain — including its own verification of it. Two pinned vectors
live in the rig.

Drive indexing reads 2.0 manifests too, ours or another tool's.

## The library

Every offload tool forgets your footage the moment the copy finishes. This one
already knows every file, every hash, every drive it was written to. The library
is the part that lets you ask, across every shoot day at once — by roll, scene,
take, camera, codec, clip name or date.

Then the question worth asking:

> **Show me every clip on fewer than three physical drives.**

**Every row shows two numbers, and they are not the same claim.**

- **Verified** — how many separate physical drives this file was read back and
  checksum-matched on. A catalog fact. True whether or not anything is plugged
  in, and complete the moment you ask.
- **Present** — how many of those drives are connected *right now* and still
  have a file of the right size at the right path.

Present is never higher than verified, and the gap is the point. `3 verified ·
1 present` is a healthy row: two drives are on a shelf, which is where drives
are supposed to be. `3 verified · 0 present`, when you thought all three were
plugged in, is the thing you would otherwise find out about a year later.

The catalog knows what happened, not what is true right now. It knows a file was
read back off two drives with the cache bypassed and the checksums matched; it
does not know that drive is still readable, still has the file, or still exists.
Reporting one number would be a confident answer nobody checked — the same
failure as a verification that reads out of the page cache, just further from
the event.

So the audit asks which question you meant:

- **on record** — which clips never got their third copy in the first place.
  Complete, instant, works with nothing connected.
- **right now** — which cannot be confirmed on three drives today. Needs the
  drives connected, and names every registered drive that was offline so that
  *could not check* is never mistaken for *not there*. With nothing connected at
  all it says so, rather than reporting your entire library as missing.

Three things it will not do:

- **Present is a size check, not a checksum**, and the interface says so in
  those words. Re-verifying a shelved drive is a real job with a progress bar.
  Quietly doing a cheap `stat` and calling the result verified would be exactly
  the weakening this program exists to refuse.
- **It changes nothing.** No writes to the catalog, no call to the format gate,
  no source card opened. A card's clearance is the gate's answer at the moment
  you ask it, never something a search moved.
- **It counts drives the way the format gate does** — `storage.drive_key()` is
  now the one definition, shared by both, so the program cannot end up with two
  different answers to "how many drives is this file on".

Export to CSV or JSON, carrying both numbers, the drives that were offline, and
when it was true. A library that only exists inside this application is a
library that dies with it.

**The catch, said plainly:** this is a library of what *this program* copied.
Footage dragged across in the Finder is not in it. Drive indexing reads anyone's
ASC MHL manifest, so a drive offloaded by another tool can still tell you what
it holds — it just cannot tell you it was verified, because this program did not
verify it.

## Nothing in the safety contract moved

Source media is still opened read-only. Every byte is still read back with the
macOS page cache bypassed, and verification still fails rather than quietly
weakening if that cannot be established. Two physically distinct drives are
still required. Interruption is still safe and resumable. The tool still never
formats, deletes or trashes anything, and still has no telemetry, accounts or
licence checks.

`test_rig.py` passes in full — now **more than 500 checks across 38 sections**,
including two new ones:

- **[37]** proves the ASC MHL 2.0 folder: that a digest 1.1 cannot name gets no
  1.1 file at all, that the chain records the C4 ID of the manifest as it landed
  on disk, that writing again with nothing changed adds no generation, and that
  a manifest altered afterwards is reported, left exactly where it is, and
  followed by a new generation rather than a rewrite.
- **[38]** proves the library changes nothing: it dumps the catalog, every
  source card tree and every format-gate verdict before and after running every
  function in `library.py`, and requires all three byte-identical — plus a
  shelved drive, an imposter disk mounted at a familiar path, SQL injection
  attempts, and nothing connected at all.

The nineteen original sections — corruption, truncation, rot, cancellation and
the two-drive rule — are unchanged and still green.

## Known limits

- **macOS only.** Verification depends on `F_NOCACHE` to bypass the page cache.
  `O_DIRECT` on Linux and `FILE_FLAG_NO_BUFFERING` on Windows are the
  equivalents and each needs its own testing before the safety contract can
  claim to hold there. Until that work is done this stays macOS-only rather than
  shipping a weaker guarantee under the same name.
- **Ad-hoc signed, not notarized.** First launch needs right-click → Open, once.
- **Camera and recorder coverage is driven by real cards.** If yours is not
  detected properly, send a directory listing — names and sizes, no footage —
  to <hello@cnfworks.com> and it can usually be fixed fast.

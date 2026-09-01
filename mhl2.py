# Second Hand Smoke — verified card offload for camera and sound media.
# Copyright (C) 2026  CnF Works
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <https://www.gnu.org/licenses/>.

"""ASC MHL 2.0 — the `ascmhl/` folder, its manifests, and the chain file.

Second Hand Smoke writes both manifest formats from one set of facts, the same
way the day report renders once as HTML and once as PDF. `reports.manifest_data`
gathers what happened; `reports.write_mhl` renders ASC MHL 1.1 and this module
renders 2.0. Neither one recomputes anything, so the two files beside each
other cannot disagree.

Why bother with 2.0 at all, when 1.1 is what most tools still read:

* **It can name digests 1.1 cannot.** XXH3, XXH128 and C4 have element names in
  2.0 and none in 1.1. Offering those algorithms honestly *requires* this file;
  without it the only choices are to refuse them or to mislabel them.
* **It records both names natively.** A renamed file gets a `<previousPath>`
  element — a real part of the schema, not a comment and not a CSV beside the
  manifest. What 1.1 could only say to a human, 2.0 says to a parser.
* **It is a chain, not a snapshot.** Each pass over the media adds a numbered
  generation, and `ascmhl_chain.xml` records the C4 ID of every manifest in the
  folder. Change an old manifest to hide a bad file and the chain no longer
  matches it.

What this module deliberately does not do:

* **No root hash.** A directory hash covers everything under the root, and this
  program writes one card at a time into a day folder that other cards are
  still landing in. A root hash written after the first card would be wrong by
  the time the second finished. `<processinfo>` says `transfer` and carries no
  `roothash`, which is valid and true; a roothash would be neither.
* **No `original` claim on a file this program did not read from the card.**
  Every entry here was hashed off the source during the copy that produced it,
  so `action="original"` is the accurate word, and it is only ever written for
  rows in that state.
* **Nothing is ever deleted or rewritten.** Generations only accumulate. If a
  manifest on disk no longer matches the C4 ID the chain recorded for it, this
  module reports that and leaves both files exactly where they are — it is
  evidence, and repairing it silently is the one thing a chain must never do.
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import hashing
import version

FOLDER = "ascmhl"
CHAIN_NAME = "ascmhl_chain.xml"
NS = "urn:ASC:MHL:v2.0"
CHAIN_NS = "urn:ASC:MHL:DIRECTORY:v2.0"
EXTENSION = ".mhl"

# The spec's own example is 0001_A002R2EC_2019-01-07_080228Z.mhl.
FILENAME_TIME = "%Y-%m-%d_%H%M%SZ"


def _safe(name):
    """A roll name that is safe as one path component and nothing else."""
    cleaned = "".join(c for c in str(name or "") if c.isalnum() or c in "-_.")
    return cleaned or "media"


def _posix(path):
    return str(path).replace(os.sep, "/")


def c4_of_file(path, chunk=1024 * 1024):
    h = hashing.new("c4")
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _local(tag):
    """The tag name without its namespace.

    Comments and processing instructions have a callable where a tag should be;
    they are given an empty name so nothing has to special-case them twice.
    """
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1]


# --------------------------------------------------------------------------
# Reading what is already there


def existing_manifests(folder):
    """Every numbered manifest in an ascmhl folder, oldest generation first."""
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in sorted(names):
        # macOS resource forks travel with files copied through some Windows
        # shares and are not manifests.
        if name.startswith("._") or not name.endswith(EXTENSION):
            continue
        head = name.split("_", 1)[0]
        if len(head) >= 4 and head.isdigit():
            found.append((int(head), name))
    found.sort()
    return found


def next_index(folder):
    found = existing_manifests(folder)
    return (found[-1][0] + 1) if found else 1


def manifest_fingerprint(path):
    """What a manifest on disk asserts, with the timestamps taken out."""
    try:
        return fingerprint(ET.parse(path).getroot())
    except (OSError, ET.ParseError):
        return None


def fingerprint(root):
    """What a manifest asserts, with the timestamps taken out.

    Two manifests with the same fingerprint say the same thing about the same
    files. Used to decide whether a second pass over an already-verified card
    has anything new to record, so that re-reading the drives does not add a
    generation that differs from the last one only in its clock reading.
    """
    items = []
    for hash_el in root.iter():
        # Matched on the local name so that this works both on a document
        # parsed back off disk (where every tag carries the namespace) and on
        # one this module has just built in memory (where none of them do).
        if _local(hash_el.tag) != "hash":
            continue
        path_el = digest = element = None
        for child in hash_el:
            local = _local(child.tag)
            if local == "path":
                path_el = child
            elif local not in ("previousPath", "metadata") and element is None:
                element, digest = local, (child.text or "")
        if path_el is None:
            continue
        items.append((path_el.text or "", path_el.get("size") or "",
                      element or "", digest or ""))
    return tuple(sorted(items))


def read_chain(path):
    """The generations a chain file already records, in order.

    Returns a list of (sequencenr, filename, c4). An unreadable or absent chain
    reads as empty; a chain that exists but cannot be parsed is reported by the
    caller rather than quietly discarded.
    """
    if not os.path.isfile(path):
        return [], None
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return [], f"{CHAIN_NAME} could not be read ({exc})"
    out = []
    for entry in root.iter(f"{{{CHAIN_NS}}}hashlist"):
        name_el = entry.find(f"{{{CHAIN_NS}}}path")
        c4_el = entry.find(f"{{{CHAIN_NS}}}c4")
        if name_el is None or c4_el is None:
            continue
        try:
            seq = int(entry.get("sequencenr") or 0)
        except ValueError:
            seq = 0
        out.append((seq, (name_el.text or "").strip(), (c4_el.text or "").strip()))
    out.sort()
    return out, None


# --------------------------------------------------------------------------
# Rendering


def _write_atomic(path, payload):
    partial = path + ".partial"
    with open(partial, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(partial, path)


def _serialise(root):
    # ElementTree writes a declared default namespace only if you register it
    # globally, which would leak into anything else in this process that uses
    # ElementTree. Setting xmlns as a plain attribute produces byte-identical
    # output with no global state, at the cost of looking like a trick — which
    # is what this comment is for.
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n").encode("utf-8")


def build_manifest(data):
    """The `<hashlist version="2.0">` document for one card, as an Element."""
    element_name = hashing.mhl2_element(data["algo"])
    root = ET.Element("hashlist", {"version": "2.0", "xmlns": NS})

    creator = ET.SubElement(root, "creatorinfo")
    ET.SubElement(creator, "creationdate").text = data["stamp"]
    ET.SubElement(creator, "hostname").text = data["hostname"]
    ET.SubElement(creator, "tool", {"version": version.VERSION}).text = version.NAME
    ET.SubElement(creator, "author").text = data["operator"]
    ET.SubElement(creator, "comment").text = (
        f"{data['day']['project']} / {data['day']['name']} / "
        f"{data['roll']} to {data['dest']['label']}"
    )

    process = ET.SubElement(root, "processinfo")
    # 'transfer': these files were read on one volume and written to another,
    # which is exactly what this program does and none of the other two.
    ET.SubElement(process, "process").text = "transfer"
    ET.SubElement(process, "ignore")

    if data["skipped"]:
        # 2.0 has no element for "a file was on the card and deliberately not
        # copied", and inventing one would make this schema-invalid. So it goes
        # where a human reading the manifest will find it, and the day's own
        # report carries the list.
        root.append(ET.Comment(
            f" {len(data['skipped'])} file(s) on this card were deliberately not "
            f"copied and are not covered by this manifest. "
            f"See the _EXCLUDED.csv beside the ascmhl folder. "))

    hashes = ET.SubElement(root, "hashes")
    for entry in data["entries"]:
        hash_el = ET.SubElement(hashes, "hash")
        ET.SubElement(hash_el, "path", {
            "size": str(entry["size"]),
            "lastmodificationdate": entry["modified"],
        }).text = entry["posix"]
        ET.SubElement(hash_el, element_name, {
            # Hashed off the card during the copy, not recomputed from the
            # destination afterwards. 'original' is the accurate word for that.
            "action": "original",
            "hashdate": entry["hashdate"],
        }).text = entry["digest"]
        if entry["renamed"]:
            # What 1.1 could only put in a comment. A parser reading this
            # manifest can recover the name the camera gave the file.
            ET.SubElement(hash_el, "previousPath").text = _posix(entry["source_rel"])
    return root


def build_chain(generations):
    root = ET.Element("ascmhldirectory", {"xmlns": CHAIN_NS})
    for seq, name, c4 in generations:
        entry = ET.SubElement(root, "hashlist", {"sequencenr": str(seq)})
        ET.SubElement(entry, "path").text = _posix(name)
        ET.SubElement(entry, "c4").text = c4
    return root


# --------------------------------------------------------------------------
# The one entry point


def write(data):
    """Write (or decline to rewrite) the ascmhl folder for one card.

    Returns a dict: `path` (the manifest this card is described by, new or
    already there), `chain`, `written` (False when nothing had changed) and
    `warnings`.
    """
    folder = os.path.join(data["root"], FOLDER)
    os.makedirs(folder, exist_ok=True)
    chain_path = os.path.join(folder, CHAIN_NAME)
    warnings = []

    generations, chain_error = read_chain(chain_path)
    if chain_error:
        warnings.append(chain_error)

    # A chain is only worth writing if it is checked. Anything it already
    # covers is re-hashed before a new generation is appended, so a manifest
    # that was edited after the fact is caught the next time a card lands
    # rather than at some archive six months later.
    for seq, name, recorded in generations:
        target = os.path.join(folder, name)
        if not os.path.isfile(target):
            warnings.append(f"{name} is listed in {CHAIN_NAME} but is missing "
                            f"from the ascmhl folder")
            continue
        if c4_of_file(target) != recorded:
            warnings.append(f"{name} no longer matches the C4 ID recorded for "
                            f"it in {CHAIN_NAME} — it has been changed since "
                            f"it was written")

    root = build_manifest(data)
    payload = _serialise(root)

    prefix = f"{_safe(data['roll'])}_"
    mine = [name for _, name in existing_manifests(folder)
            if name.split("_", 1)[-1].startswith(prefix)]
    if mine:
        newest = os.path.join(folder, mine[-1])
        # Compare what the two documents assert, not their bytes: the new one
        # always has a fresh creationdate, and a different clock reading is not
        # a different set of facts. Nothing is written to disk to do this.
        if fingerprint(root) == manifest_fingerprint(newest):
            return {"path": newest, "chain": chain_path, "written": False,
                    "warnings": warnings}

    index = next_index(folder)
    name = (f"{index:04d}_{_safe(data['roll'])}_"
            f"{datetime.now(timezone.utc).strftime(FILENAME_TIME)}{EXTENSION}")
    out_path = os.path.join(folder, name)
    _write_atomic(out_path, payload)

    # The chain hashes the manifest as it landed on disk, not the bytes we
    # meant to write. If the write went wrong, the chain records what is
    # actually there and the mismatch shows up on the next pass.
    generations.append((index, name, c4_of_file(out_path)))
    _write_atomic(chain_path, _serialise(build_chain(sorted(generations))))

    return {"path": out_path, "chain": chain_path, "written": True,
            "warnings": warnings}

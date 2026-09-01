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

"""Which checksum, and what a manifest is allowed to call it.

xxHash64 is the right default: it is fast enough to keep up with a card reader,
and speed matters because a verification nobody has time to run is a
verification nobody runs. But some archives and post houses *mandate* a
particular algorithm, and "we only do xxHash64" loses that job outright, for
reasons that have nothing to do with whether the tool is any good.

Two rules that shape what is offered here.

**A manifest must never mislabel a digest.** The ASC MHL 1.1 schema defines a
fixed set of hash elements. An algorithm this program can compute but that
format cannot name has no honest home in an MHL, and writing it under a
neighbouring element name would produce a manifest that lies about what is in
it — worse than no manifest at all. So the list below is the intersection of
what we can compute and what the manifest can truthfully carry. It grows when
the manifest format does, not before.

**The algorithm belongs to the media, not to the program.** Proxies, review
dailies and thumbnails are checked with xxHash64 whatever this is set to. Those
are internal integrity checks on files this program made; nobody's archive
policy is about them, and making them configurable would only add a way for two
things to disagree.
"""

import hashlib

import xxhash

CHUNK = 8 * 1024 * 1024
DEFAULT = "xxh64"


def _md5():
    # usedforsecurity=False says out loud what this is for: catching a drive
    # that changed a byte, not resisting anyone.
    try:
        return hashlib.md5(usedforsecurity=False)
    except TypeError:
        return hashlib.md5()


def _sha1():
    try:
        return hashlib.sha1(usedforsecurity=False)
    except TypeError:
        return hashlib.sha1()


ALGORITHMS = {
    "xxh64": {
        "label": "xxHash64",
        "mhl_element": "xxhash64be",
        "new": xxhash.xxh64,
        "note": "Fast enough to keep up with a card reader. The default, and "
                "what almost every offload tool writes.",
    },
    "md5": {
        "label": "MD5",
        "mhl_element": "md5",
        "new": _md5,
        "note": "Slower, and still what some archives and delivery specs ask "
                "for by name.",
    },
    "sha1": {
        "label": "SHA-1",
        "mhl_element": "sha1",
        "new": _sha1,
        "note": "Slower again. Asked for by some broadcast and archive "
                "specifications.",
    },
}


def is_known(algo):
    return algo in ALGORITHMS


def normalise(algo):
    """Fall back to the default rather than refusing to hash anything.

    A catalog row written before this setting existed has no algorithm on it,
    and the answer to that is xxHash64 — which is what it was hashed with.
    """
    algo = (algo or "").strip().lower()
    return algo if algo in ALGORITHMS else DEFAULT


def label(algo):
    return ALGORITHMS[normalise(algo)]["label"]


def mhl_element(algo):
    return ALGORITHMS[normalise(algo)]["mhl_element"]


def choices():
    return [{"id": key, "label": value["label"], "note": value["note"],
             "mhl_element": value["mhl_element"]}
            for key, value in ALGORITHMS.items()]


def new(algo=DEFAULT):
    return ALGORITHMS[normalise(algo)]["new"]()


def digest_bytes(data, algo=DEFAULT):
    h = new(algo)
    h.update(data)
    return h.hexdigest()

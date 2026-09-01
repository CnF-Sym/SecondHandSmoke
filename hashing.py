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

**A manifest must never mislabel a digest.** Each MHL schema defines a fixed
set of hash elements. An algorithm this program can compute but that format
cannot name has no honest home in that manifest, and writing it under a
neighbouring element name would produce a file that lies about what is in it —
worse than no manifest at all. So each entry below carries the element name it
is allowed to use in each format, and `None` where that format has no name for
it.

ASC MHL 1.1 names five digests; 2.0 names six, including XXH3, XXH128 and C4.
That is why this list is longer than it was: the format grew, so the list grew
with it. A day set to an algorithm 1.1 cannot name still gets a full, valid
2.0 `ascmhl/` folder — it just gets no 1.1 sidecar, because there is no
truthful 1.1 sidecar to write. The interface says which manifests a choice
will produce before the choice is made.

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


# The C4 ID is a SHA-512 digest re-encoded in base58 and padded to a fixed 90
# characters. It is the one digest in ASC MHL 2.0 that is not plain hex, and it
# is not optional: the chain file (ascmhl_chain.xml) identifies every manifest
# it covers by its C4 ID, so a tool that writes a v2 folder has to be able to
# compute one whatever the media is hashed with.
#
# Encoding cross-checked byte-for-byte against the reference implementation
# (Pomfort's `ascmhl` package, ascmhl/hasher.py) on empty, short, long and
# random inputs. Reimplemented here rather than depended on because it is
# fifteen lines of arithmetic, and a DIT at 4am should not have to install a
# package to get a manifest.
C4_CHARSET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
C4_LENGTH = 90


def c4_from_sha512(digest):
    """Base58-encode a 64-byte SHA-512 digest as a C4 ID."""
    value = int.from_bytes(digest, "big")
    out = ""
    while value:
        value, remainder = divmod(value, 58)
        out = C4_CHARSET[remainder] + out
    # '1' is zero in this alphabet, so left-padding with it is padding with
    # leading zeroes, not with a different number.
    return "c4" + out.rjust(C4_LENGTH - 2, C4_CHARSET[0])


class _C4:
    """A hashlib-shaped wrapper so C4 can be used exactly like every other
    algorithm here — update() as the bytes go past, hexdigest() at the end.

    The name hexdigest() is a lie about the alphabet and honest about the role:
    everything that hashes a file in this program calls it, and the digest it
    returns is the string that belongs in the manifest.
    """

    def __init__(self):
        self._h = hashlib.sha512()

    def update(self, data):
        self._h.update(data)

    def hexdigest(self):
        return c4_from_sha512(self._h.digest())


# mhl_element  — what ASC MHL 1.1 calls this digest, or None if it has no name
#                for it. None means: no 1.1 sidecar for a day using it.
# mhl2_element — what ASC MHL 2.0 calls it. Every entry has one; a day always
#                gets an ascmhl/ folder.
ALGORITHMS = {
    "xxh64": {
        "label": "xxHash64",
        "mhl_element": "xxhash64be",
        "mhl2_element": "xxh64",
        "new": xxhash.xxh64,
        "note": "Fast enough to keep up with a card reader. The default, and "
                "what almost every offload tool writes.",
    },
    "xxh128": {
        "label": "xxHash128",
        "mhl_element": None,
        "mhl2_element": "xxh128",
        "new": xxhash.xxh3_128,
        "note": "As fast as xxHash64 with twice the digest. What the ASC MHL "
                "2.0 reference tool picks by default.",
    },
    "xxh3": {
        "label": "xxHash3",
        "mhl_element": None,
        "mhl2_element": "xxh3",
        "new": xxhash.xxh3_64,
        "note": "The newer 64-bit xxHash. Same length as xxHash64, different "
                "algorithm — ask for it by name or leave it alone.",
    },
    "md5": {
        "label": "MD5",
        "mhl_element": "md5",
        "mhl2_element": "md5",
        "new": _md5,
        "note": "Slower, and still what some archives and delivery specs ask "
                "for by name.",
    },
    "sha1": {
        "label": "SHA-1",
        "mhl_element": "sha1",
        "mhl2_element": "sha1",
        "new": _sha1,
        "note": "Slower again. Asked for by some broadcast and archive "
                "specifications.",
    },
    "c4": {
        "label": "C4 ID",
        "mhl_element": None,
        "mhl2_element": "c4",
        "new": _C4,
        "note": "SHA-512 in base58. Slowest of the six. Used as an asset ID "
                "by some post and VFX pipelines.",
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
    """The ASC MHL 1.1 element name, or None if 1.1 cannot name this digest."""
    return ALGORITHMS[normalise(algo)]["mhl_element"]


def mhl2_element(algo):
    """The ASC MHL 2.0 element name. Every algorithm offered here has one."""
    return ALGORITHMS[normalise(algo)]["mhl2_element"]


def manifests_for(algo):
    """Which manifest formats a choice will actually produce.

    Said out loud so the interface can say it out loud, rather than a DIT
    discovering after the fact that the .mhl they expected is not there.
    """
    formats = []
    if ALGORITHMS[normalise(algo)]["mhl_element"]:
        formats.append("ASC MHL 1.1")
    formats.append("ASC MHL 2.0")
    return formats


def choices():
    return [{"id": key, "label": value["label"], "note": value["note"],
             "mhl_element": value["mhl_element"],
             "mhl2_element": value["mhl2_element"],
             "manifests": manifests_for(key)}
            for key, value in ALGORITHMS.items()]


def new(algo=DEFAULT):
    return ALGORITHMS[normalise(algo)]["new"]()


def digest_bytes(data, algo=DEFAULT):
    h = new(algo)
    h.update(data)
    return h.hexdigest()

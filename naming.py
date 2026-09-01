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

"""Working out what a file will be called on the destination.

Deliberately pure: no database, no filesystem, no clock. Everything it needs
arrives as arguments and everything it decides comes back as a return value.
That is not tidiness for its own sake — renaming is the highest-risk thing in
this program, and a function with no hidden inputs is a function whose output
can be proven identical across a cancel and a resume. A rename that came out
differently the second time would copy a second file beside the first, and the
gate would count both.

Three rules that are not negotiable:

* **The source card is never renamed.** This decides destination names only.
  A rename on a card is an edit to the only copy in existence.
* **The extension is carried over verbatim**, case included. Cameras and NLEs
  both care, and neither forgives.
* **A pattern that resolves to nothing falls back to the original name.**
  Metadata is often missing at ingest time; an empty pattern must produce
  A001C001.MXF, never A001__.MXF and never an empty name.
"""

import fnmatch
import os
import re

# The default. A fresh preset does not rename anything: renaming is something
# you ask for, not something that happens to you.
DEFAULT_PATTERN = "{original}"

TOKENS = ("project", "day", "date", "roll", "card", "original",
          "scene", "take", "camera", "index")

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
_SEPARATOR_RUN_RE = re.compile(r"[ _.\-]{2,}")

MAX_STEM = 120

# Windows reserves these whatever the extension. Second Hand Smoke is macOS
# only, but the drives it writes leave the building and get read on Windows,
# and a delivery nobody can open is still our fault.
_RESERVED = {"con", "prn", "aux", "nul", "clock$"}
_RESERVED |= {f"com{n}" for n in range(1, 10)}
_RESERVED |= {f"lpt{n}" for n in range(1, 10)}


def tokens_in(pattern):
    """Every token named in a pattern, known or not."""
    return _TOKEN_RE.findall(pattern or "")


def unknown_tokens(pattern):
    return [name for name in tokens_in(pattern) if name not in TOKENS]


def validate(pattern):
    """None if the pattern is usable, otherwise a sentence saying why not."""
    pattern = (pattern or "").strip()
    if not pattern:
        return "The naming pattern is empty. Use {original} to keep camera names."
    unknown = unknown_tokens(pattern)
    if unknown:
        known = ", ".join(f"{{{name}}}" for name in TOKENS)
        return (f"Unknown token {{{unknown[0]}}}. Available tokens are: {known}")
    if os.sep in pattern or (os.altsep and os.altsep in pattern):
        return "A naming pattern names a file, not a folder — it cannot contain a path separator."
    if ".." in pattern:
        return "A naming pattern cannot contain '..'."
    return None


def sanitise(stem):
    """Make a resolved stem safe to write, without silently emptying it."""
    stem = _UNSAFE_RE.sub("_", stem or "")
    stem = _SEPARATOR_RUN_RE.sub(lambda m: m.group(0)[0], stem)
    stem = stem.strip(" _-.")
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].strip(" _-.")
    if stem.lower() in _RESERVED:
        stem = f"{stem}_"
    return stem


def resolve(pattern, facts):
    """Resolve a pattern into a filename stem.

    Returns the stem, or "" if the pattern resolved to nothing usable — the
    caller falls back to the original name and says so in the report.
    """
    def substitute(match):
        value = facts.get(match.group(1))
        return "" if value is None else str(value)

    return sanitise(_TOKEN_RE.sub(substitute, pattern or ""))


def group_key(rel_path):
    """Files that must be renamed together: same folder, same stem.

    Cameras write CLIP.MXF beside CLIP.XML. Rename one and not the other and
    the pair is broken — no NLE will find that metadata again.
    """
    folder, name = os.path.split(rel_path)
    stem, _ = os.path.splitext(name)
    return (os.path.normcase(folder), os.path.normcase(stem))


def facts_for(file_row, card, day, index):
    """The facts one file offers a pattern. Missing metadata is simply absent."""
    original = os.path.splitext(os.path.basename(file_row["rel_path"]))[0]
    return {
        "project": day["project"],
        "day": day["name"],
        "date": day["shoot_date"],
        "roll": card["roll"] or card["label"],
        "card": card["label"],
        "original": original,
        "scene": file_row.get("scene") or "",
        "take": file_row.get("take_name") or "",
        "camera": file_row.get("reel_name") or "",
        "index": f"{index:04d}",
    }


def plan(files, card, day, pattern=DEFAULT_PATTERN):
    """Work out every destination name for one card.

    `files` is a list of dicts with at least id, rel_path and category, plus
    whatever metadata is known. Returns a list in the same order, each entry
    carrying the original relative path and the renamed one.

    The index is per card and computed from the card's groups sorted by
    relative path, never from job progress. That is what makes a resumed
    offload land on the same paths as the run it is resuming.
    """
    pattern = (pattern or DEFAULT_PATTERN).strip() or DEFAULT_PATTERN

    ordered = sorted(files, key=lambda row: row["rel_path"])
    group_index = {}
    for row in ordered:
        key = group_key(row["rel_path"])
        if key not in group_index:
            group_index[key] = len(group_index) + 1

    # One resolved stem per group, taken from the media file in it. A sidecar
    # has no scene or take of its own; it inherits the clip's name because it
    # belongs to the clip.
    leaders = {}
    for row in ordered:
        key = group_key(row["rel_path"])
        current = leaders.get(key)
        if current is None or (current.get("category") not in ("video", "audio")
                               and row.get("category") in ("video", "audio")):
            leaders[key] = row

    stems = {}
    for key, leader in leaders.items():
        facts = facts_for(leader, card, day, group_index[key])
        stems[key] = resolve(pattern, facts)

    planned = []
    for row in files:
        key = group_key(row["rel_path"])
        folder, name = os.path.split(row["rel_path"])
        original_stem, extension = os.path.splitext(name)
        stem = stems.get(key) or ""
        renamed = bool(stem) and stem != original_stem
        if not stem:
            stem = original_stem
        planned.append({
            "id": row.get("id"),
            "rel_path": row["rel_path"],
            "dest_rel_path": os.path.join(folder, stem + extension) if folder
                             else stem + extension,
            "original_name": name,
            "dest_name": stem + extension,
            "renamed": renamed,
            "group": group_index[key],
        })
    return planned


def collisions(planned):
    """Destination names that two different source files would both claim.

    Returns a list of (dest_name, [source rel_paths]). Renaming makes this far
    more likely than it used to be — {scene}_{take} across two cameras collides
    immediately — and the answer is to refuse and name both files, never to add
    a silent _2. Two takes nobody can tell apart six weeks later is worse than
    an offload that would not start.
    """
    seen = {}
    for entry in planned:
        key = os.path.normcase(entry["dest_rel_path"])
        seen.setdefault(key, []).append(entry["rel_path"])
    return [(key, sources) for key, sources in seen.items() if len(sources) > 1]


# --- filtering -------------------------------------------------------------
#
# Filtering is the dangerous half of this feature, and not for the reason it
# looks. The format gate means one sentence: everything on this card is
# verified on two drives, so you can wipe it. Excluding files from the copy
# makes that sentence false while the screen still says SAFE TO FORMAT, and
# someone formats a card that held the only copy of what the filter dropped.
#
# So nothing here silently drops anything. This decides which files a filter
# would skip; the catalog records every one of them as deliberately excluded,
# and the gate gives a card with exclusions its own verdict that can never be
# mistaken for the plain one.
#
# Syntax: a comma-separated list of shell-style globs matched against the
# file's path on the card, case-insensitively. A pattern starting with "-"
# excludes. If any plain (include) pattern is given, a file must match one of
# them to be copied at all.


def parse_filters(text):
    """Split a filter string into (includes, excludes). Never raises."""
    includes, excludes = [], []
    for raw in (text or "").replace("\n", ",").split(","):
        pattern = raw.strip()
        if not pattern:
            continue
        if pattern.startswith("-") or pattern.startswith("!"):
            pattern = pattern[1:].strip()
            if pattern:
                excludes.append(pattern)
        else:
            includes.append(pattern)
    return includes, excludes


def describe_filters(text):
    """A sentence for the interface, or "" when nothing is filtered."""
    includes, excludes = parse_filters(text)
    if not includes and not excludes:
        return ""
    parts = []
    if includes:
        parts.append("only " + ", ".join(includes))
    if excludes:
        parts.append("never " + ", ".join(excludes))
    return "Copying " + "; ".join(parts) + "."


def _matches(rel_path, pattern):
    """Match against the whole path and the bare filename.

    Someone typing *.THM means "no thumbnails", not "no thumbnails in the root
    folder", and a filter that quietly does the second thing is worse than no
    filter at all.
    """
    lowered = rel_path.replace(os.sep, "/").lower()
    pattern = pattern.replace(os.sep, "/").lower()
    return (fnmatch.fnmatch(lowered, pattern)
            or fnmatch.fnmatch(os.path.basename(lowered), pattern))


def is_included(rel_path, text):
    """Would this file be copied under these filters?"""
    includes, excludes = parse_filters(text)
    if includes and not any(_matches(rel_path, p) for p in includes):
        return False
    return not any(_matches(rel_path, p) for p in excludes)


def split_by_filter(files, text):
    """(kept, skipped) for a list of rows with rel_path. Pure."""
    if not (text or "").strip():
        return list(files), []
    kept, skipped = [], []
    for row in files:
        (kept if is_included(row["rel_path"], text) else skipped).append(row)
    return kept, skipped

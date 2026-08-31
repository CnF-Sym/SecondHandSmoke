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

"""One still frame per camera clip, for the report.

A verification manifest is a wall of filenames and hashes. A producer looks at
a page of frames and knows in a second whether the day is what they think it
is. That is the whole value here, and it is worth being clear about what this
is not: **thumbnails are cosmetic**. Nothing in this module can change a
checksum, a copy record, a manifest or the format gate, and every failure in it
is silent by design — a clip with no picture is a report with a gap in it, not
a card that fails to clear.

Two rules it does not bend:

* **Frames come off the verified primary-drive copy, never the source card.**
  Same rule the proxy builder follows. The card is opened read-only everywhere
  in this program, but a thumbnail is a cosmetic reason to touch a card at all,
  and cosmetic reasons are not good enough. It also means report frames still
  work after a card has cleared and been pulled, which is when reports get
  written.
* **A frame is only made from a copy in the `verified` state.** An unverified
  copy might be half-written. A picture of a half-written file in a document
  that also says SAFE TO FORMAT is exactly the wrong thing to put in front of
  a producer.

The cache is keyed by the file's content hash, so it never needs invalidating:
if the content changes the hash changes, and a different frame is made. It
lives beside the catalog rather than on the production's drives, because it is
this machine's scratch and nobody should find our JPEGs in their delivery.
"""

import os
import subprocess

import db
import toolpaths

WIDTH = 480
JPEG_QUALITY = "4"          # ffmpeg -q:v, 2 is best, 31 is worst

try:
    TIMEOUT = max(5, int(os.environ.get("DIT_THUMB_TIMEOUT", "30")))
except ValueError:
    TIMEOUT = 30


def available():
    """(available, reason). Thumbnails need ffmpeg; nothing else does not."""
    if not toolpaths.ffmpeg():
        return False, ("ffmpeg was not found, so report thumbnails are "
                       "unavailable. Every other part of the report is "
                       "unaffected.")
    return True, ""


def cache_dir():
    folder = os.path.join(os.path.dirname(db.DB_PATH), "thumbnails")
    os.makedirs(folder, exist_ok=True)
    return folder


def cache_path(src_hash):
    """Where this file's frame lives. Keyed by content, so never stale."""
    return os.path.join(cache_dir(), f"{src_hash}.jpg")


def cached_frame(src_hash):
    """The frame already on disk for this content, or None. Decodes nothing.

    reports.day_data() uses this so gathering a day never runs ffmpeg. The
    layout that wants pictures warms the cache first; every other reader just
    gets None and carries on.
    """
    if not src_hash:
        return None
    path = cache_path(src_hash)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


def _verified_primary_copy(file_id, day_id):
    """The verified copy on the primary drive, or None.

    Deliberately not media.resolve_media_path(), which prefers a mounted card.
    A thumbnail is not a good enough reason to read a card.
    """
    primary = db.q1(
        """SELECT id FROM destinations WHERE day_id=? AND role='primary'
           ORDER BY id LIMIT 1""", (day_id,))
    if not primary:
        return None
    copy = db.q1(
        """SELECT dest_path FROM copies
           WHERE file_id=? AND destination_id=? AND state='verified'""",
        (file_id, primary["id"]))
    if copy and copy["dest_path"] and os.path.isfile(copy["dest_path"]):
        return copy["dest_path"]
    return None


def _seek_seconds(duration):
    """A frame from a little way in — the first frame is often a black slate."""
    try:
        duration = float(duration or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        return 1.0
    return max(0.0, min(duration * 0.1, duration - 0.05, 10.0))


def _extract(input_path, output_path, seek):
    """Decode one frame. Returns True on success, False for any failure."""
    ffmpeg = toolpaths.ffmpeg()
    if not ffmpeg:
        return False
    partial = output_path + ".partial"
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{seek:.3f}", "-i", input_path,
        "-frames:v", "1",
        "-vf", f"scale={WIDTH}:-2:force_original_aspect_ratio=decrease",
        "-q:v", JPEG_QUALITY, "-f", "image2", partial,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=TIMEOUT)
        if result.returncode != 0 or not os.path.isfile(partial) \
                or os.path.getsize(partial) == 0:
            return False
        # Atomic rename, like every other write in this program: a reader can
        # only ever see a whole frame or no frame.
        os.replace(partial, output_path)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass


def frame_for(file_row, day_id, build=True):
    """The cached frame for one file, building it if needed. Never raises.

    Returns a path, or None when there is no frame and no way to make one —
    which is a perfectly ordinary outcome and never an error anyone is shown.
    """
    # Works for a sqlite3.Row straight out of a query and for the plain dicts
    # reports.day_data() hands to the renderers.
    src_hash = file_row["src_hash"]
    if not src_hash:
        return None
    target = cache_path(src_hash)
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target
    if not build:
        return None

    file_id = file_row["id"]
    source = _verified_primary_copy(file_id, day_id)
    if not source:
        return None
    duration = db.q1("SELECT duration_seconds FROM media_metadata WHERE file_id=?",
                     (file_id,))
    seek = _seek_seconds(duration["duration_seconds"] if duration else None)
    if _extract(source, target, seek):
        return target
    # One retry from the very start of the file: a seek past the end of a very
    # short clip is the common reason a decode comes back empty.
    if seek > 0 and _extract(source, target, 0.0):
        return target
    return None


def build_for_day(day_id, limit=None):
    """Warm the cache for every verified camera clip on a day.

    Returns counts. Cosmetic throughout: a failure here is a missing picture,
    never a changed record.
    """
    ok, _ = available()
    if not ok:
        return {"available": False, "made": 0, "cached": 0, "missing": 0}

    rows = db.q(
        """SELECT f.id, f.src_hash, f.rel_path
           FROM files f JOIN cards c ON c.id=f.card_id
           WHERE c.day_id=? AND f.category='video'
           ORDER BY f.rel_path""", (day_id,))
    made = cached = missing = 0
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        existing = row["src_hash"] and os.path.isfile(cache_path(row["src_hash"]))
        path = frame_for(row, day_id)
        if not path:
            missing += 1
        elif existing:
            cached += 1
        else:
            made += 1
    return {"available": True, "made": made, "cached": cached, "missing": missing}


def clear_cache():
    """Delete this machine's cached frames. Touches nothing but our own cache."""
    folder = cache_dir()
    removed = 0
    for name in os.listdir(folder):
        if not name.endswith((".jpg", ".jpg.partial")):
            continue
        try:
            os.remove(os.path.join(folder, name))
            removed += 1
        except OSError:
            pass
    return removed

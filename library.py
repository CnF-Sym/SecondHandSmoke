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

"""The library — asking the catalog questions after the shoot has wrapped.

Every offload tool forgets your footage the moment the copy finishes. This one
already knows every file, every hash, every destination it was written to and
which physical drive that destination was. The library is the part that lets
you ask.

The question worth answering is *"show me every clip that exists on fewer than
three physical drives"*, and it is also the question this program has to be
careful about, because **the catalog knows what happened, not what is true
right now.**

It knows that on 16 January a file was read back off two drives with the page
cache bypassed and the checksums matched. That is a strong fact about the past.
It does not know whether that file is on those drives today: the drive could be
on a shelf, in a courier van, wiped, or dead. Answering "3 copies" for a drive
nobody has plugged in since February would be a confident number nobody
checked — the same failure as a verification that reads out of the page cache,
just further from the event.

So nothing here ever reports one number. Every row carries two:

* **verified** — how many distinct physical drives this file was read back and
  checksum-matched on. Comes from the catalog. Complete, instant, offline.
* **present** — how many of those drives are connected *right now* and still
  have a file of the right size at the right path. Comes from the filesystem,
  and is only ever counted for drives that answered.

`present` is never higher than `verified`, and the gap is the point. Three
verified and one present is a normal, healthy row: two drives are on a shelf,
which is where drives are supposed to be. Three verified and zero present, when
you thought all three were plugged in, is the thing you would otherwise find
out about a year later.

Two rules this module holds to, both proven in section [38] of the rig:

**It is read-only.** No writes to `copies`, `files` or `cards`. It never calls
the format gate and never changes a verdict — a card's clearance is decided by
the gate at the moment you ask it, not by anything found here. It never opens a
source card.

**A size check is not a verification.** `present` is established with `os.stat`,
and this module says so in those words rather than borrowing the word
"verified" for it. Re-checksumming a shelved drive is a real job with a
progress bar, subject to the job gate; quietly doing it here — or quietly
*not* doing it and calling the result verified anyway — is exactly the
weakening this program exists to refuse.
"""

import csv
import io
import json
import os
from datetime import datetime, timezone

import db
import storage

# The catalog can be large and a browser is not. A page of results is a page;
# the count of what matched is always reported alongside, so a truncated answer
# announces itself instead of looking like a complete one.
PAGE = 500
MAX_PAGE = 5000

DRIVE_KEY = storage.DRIVE_KEY_SQL.format(device_id="dest.device_id",
                                         destination_id="dest.id")

# Everything a row can be filtered or sorted by, mapped to its SQL. Written out
# rather than interpolated from user input, because a library is a place where
# arbitrary text meets a database and the answer to that is an allow-list.
FIELDS = {
    "project": "d.project",
    "day": "d.name",
    "shoot_date": "d.shoot_date",
    "roll": "c.roll",
    "card": "c.label",
    "kind": "c.kind",
    "rel_path": "f.rel_path",
    "category": "f.category",
    "size": "f.size",
    "scene": "m.scene",
    "take": "m.take_name",
    "circled": "m.circled",
    "codec": "m.codec_name",
    "camera": "m.reel_name",
    "sound_roll": "m.sound_roll",
    "frame_rate": "m.frame_rate",
    "duration_seconds": "m.duration_seconds",
}
TEXT_FIELDS = ("project", "day", "roll", "card", "rel_path", "scene", "take",
               "codec", "camera", "sound_roll")
SORTS = dict(FIELDS, verified="verified_drives")

SELECT_FIELDS = f"""
       f.id, f.rel_path, f.size, f.category, f.src_hash, f.hash_algo,
       COALESCE(f.excluded, 0) AS excluded,
       c.id AS card_id, c.label AS card_label, c.roll, c.kind,
       d.id AS day_id, d.project, d.name AS day_name, d.shoot_date,
       m.scene, m.take_name, m.circled, m.codec_name, m.reel_name,
       m.sound_roll, m.frame_rate, m.start_timecode, m.duration_seconds,
       m.width, m.height, m.media_type,
       COUNT(DISTINCT CASE WHEN cp.state = 'verified' THEN {DRIVE_KEY} END)
           AS verified_drives
"""

# Kept separate from the field list so the count query can reuse the joins
# without the columns, rather than slicing the string apart and hoping.
FROM_JOINS = """
FROM files f
JOIN cards c ON c.id = f.card_id
JOIN days d ON d.id = c.day_id
LEFT JOIN media_metadata m ON m.file_id = f.id
LEFT JOIN copies cp ON cp.file_id = f.id
LEFT JOIN destinations dest ON dest.id = cp.destination_id
"""

BASE = f"SELECT {SELECT_FIELDS} {FROM_JOINS}"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _where(query):
    """Turn a filter dict into SQL, using only field names from FIELDS."""
    clauses, params = [], []
    text = (query.get("text") or "").strip()
    if text:
        # One box that searches the things a person actually remembers about a
        # clip: where it came from and what was written on the slate.
        like = f"%{text}%"
        parts = [f"{FIELDS[name]} LIKE ?" for name in TEXT_FIELDS]
        clauses.append("(" + " OR ".join(parts) + ")")
        params += [like] * len(parts)
    for name in FIELDS:
        value = query.get(name)
        if value in (None, "", []):
            continue
        if name in TEXT_FIELDS:
            clauses.append(f"{FIELDS[name]} LIKE ?")
            params.append(f"%{value}%")
        else:
            clauses.append(f"{FIELDS[name]} = ?")
            params.append(value)
    if query.get("date_from"):
        clauses.append("d.shoot_date >= ?")
        params.append(query["date_from"])
    if query.get("date_to"):
        clauses.append("d.shoot_date <= ?")
        params.append(query["date_to"])
    if not query.get("include_excluded"):
        # Files a filter deliberately skipped were never meant to be on a
        # drive. Counting them as missing copies would bury a real answer under
        # a list of thumbnails somebody chose not to keep.
        clauses.append("COALESCE(f.excluded, 0) = 0")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _order(query):
    field = SORTS.get(query.get("sort") or "")
    direction = "DESC" if str(query.get("direction", "")).lower() == "desc" else "ASC"
    if not field:
        return "ORDER BY d.shoot_date DESC, d.name, c.roll, f.rel_path"
    return f"ORDER BY {field} {direction}, d.name, f.rel_path"


# --------------------------------------------------------------------------
# Is it there right now?


class Presence:
    """Which registered drives answered, and what they still hold.

    One instance per request. `inspect_path` shells out to diskutil, so each
    destination path is identified once and remembered for the life of the
    answer rather than once per file.
    """

    def __init__(self):
        self._drives = {}

    def destination(self, destination_id):
        if destination_id in self._drives:
            return self._drives[destination_id]
        row = db.q1("SELECT * FROM destinations WHERE id=?", (destination_id,))
        state = {"online": False, "reason": "", "label": row["label"] if row else "?"}
        if row:
            try:
                live = storage.inspect_path(row["path"])
            except Exception as exc:
                # Not mounted is the ordinary case, not an error. A drive on a
                # shelf is a drive doing its job.
                state["reason"] = str(exc)
            else:
                recorded = row["device_id"]
                if recorded and live["device_id"] != recorded:
                    # Something is mounted there, but it is not the drive this
                    # copy was written to. Reporting it as online would count a
                    # stranger's disk as one of your backups.
                    state["reason"] = (f"a different drive is mounted at "
                                       f"{row['path']}")
                else:
                    state["online"] = True
        self._drives[destination_id] = state
        return state

    def offline(self):
        return sorted({s["label"] for s in self._drives.values()
                       if not s["online"]})

    def count(self, file_id, size):
        """Distinct online physical drives holding a file of the right size.

        `os.stat` only. This is deliberately not a checksum, and the number it
        produces is never called "verified" anywhere it is displayed.
        """
        rows = db.q("""SELECT cp.dest_path, cp.destination_id, dest.device_id
                       FROM copies cp
                       JOIN destinations dest ON dest.id = cp.destination_id
                       WHERE cp.file_id=? AND cp.state='verified'""", (file_id,))
        keys, checked = set(), 0
        for row in rows:
            state = self.destination(row["destination_id"])
            if not state["online"]:
                continue
            checked += 1
            try:
                if os.path.getsize(row["dest_path"]) == size:
                    keys.add(storage.drive_key(row["device_id"],
                                               row["destination_id"]))
            except OSError:
                pass
        return len(keys), checked


# --------------------------------------------------------------------------
# The questions


def search(query=None, limit=PAGE, offset=0, check_presence=False):
    """Everything matching, newest shoot day first.

    `check_presence` costs a stat per copy of every row returned, so it is off
    unless asked for. Without it a row carries `present: None`, which the
    interface renders as "not checked" — never as zero.
    """
    query = query or {}
    limit = max(1, min(int(limit or PAGE), MAX_PAGE))
    offset = max(0, int(offset or 0))
    where, params = _where(query)

    total = db.q1(f"SELECT COUNT(*) AS c FROM (SELECT f.id {FROM_JOINS}"
                  f"{where} GROUP BY f.id)", params)["c"]
    rows = [dict(r) for r in db.q(
        f"{BASE}{where} GROUP BY f.id {_order(query)} LIMIT ? OFFSET ?",
        params + [limit, offset])]

    presence = Presence() if check_presence else None
    for row in rows:
        if presence:
            row["present_drives"], row["drives_checked"] = presence.count(
                row["id"], row["size"])
        else:
            row["present_drives"] = None
            row["drives_checked"] = None
    return {
        "rows": rows,
        "total": total,
        "shown": len(rows),
        "offset": offset,
        "truncated": offset + len(rows) < total,
        "presence_checked": bool(check_presence),
        "drives_offline": presence.offline() if presence else [],
        "as_at": _now(),
    }


def audit(minimum=None, mode="record", query=None, limit=MAX_PAGE):
    """Which files are on fewer than `minimum` physical drives.

    Two modes, because they are two different questions and picking one for
    somebody would be answering a question they did not ask:

    * `record` — on fewer than N drives *on record*. The archive question:
      which clips never got their third copy in the first place. Complete,
      offline, instant.
    * `present` — not confirmed on N drives *right now*. The shelf question.
      Needs the drives connected, and names every registered drive that was
      offline when it ran so that "could not check" is never mistaken for
      "not there".
    """
    import reports  # local: reports imports nothing from here, and this keeps
                    # the library off the import path of the copy engine.
    minimum = int(minimum or reports.MIN_VERIFIED_COPIES)
    query = dict(query or {})

    if mode == "record":
        where, params = _where(query)
        rows = [dict(r) for r in db.q(
            f"{BASE}{where} GROUP BY f.id HAVING verified_drives < ? "
            f"ORDER BY verified_drives, d.shoot_date DESC, d.name, f.rel_path "
            f"LIMIT ?", params + [minimum, limit])]
        for row in rows:
            row["present_drives"] = None
            row["drives_checked"] = None
        return {"mode": "record", "minimum": minimum, "rows": rows,
                "count": len(rows), "drives_offline": [],
                "complete": True, "as_at": _now()}

    # The shelf question. Everything that could conceivably be short is looked
    # at: a file on three drives on record can still be present on one.
    where, params = _where(query)
    candidates = db.q(f"{BASE}{where} GROUP BY f.id "
                      f"ORDER BY d.shoot_date DESC, d.name, f.rel_path", params)
    presence = Presence()
    rows = []
    answered = False
    for row in candidates:
        present, checked = presence.count(row["id"], row["size"])
        answered = answered or checked > 0
        if present >= minimum:
            continue
        row = dict(row)
        row["present_drives"] = present
        row["drives_checked"] = checked
        rows.append(row)
        if len(rows) >= limit:
            break
    offline = presence.offline()

    if candidates and not answered:
        # Not one drive answered, so every file "failed" — which would hand
        # somebody a list of their entire library formatted as a disaster. The
        # truthful answer to "is it on three drives right now" when nothing is
        # plugged in is not "no", it is "nothing was there to ask".
        return {
            "mode": "present", "minimum": minimum, "rows": [], "count": 0,
            "drives_offline": offline, "complete": False,
            "nothing_checked": True, "as_at": _now(),
        }
    return {
        "mode": "present", "minimum": minimum, "rows": rows,
        "count": len(rows), "drives_offline": offline,
        "nothing_checked": False,
        # An answer with drives offline is not wrong, but it is not complete,
        # and it must not be read as one.
        "complete": not offline,
        "as_at": _now(),
    }


def drives():
    """Every registered drive, what the catalog says is on it, and whether it
    is connected.

    Two counts per drive, kept apart on purpose: what this program verified
    onto it, and what a drive index found sitting on it. A drive offloaded by
    another tool can have plenty of the second and none of the first, and
    running them together would let another tool's work look like ours.
    """
    import drivetools
    out = []
    for drive in drivetools.known_drives():
        verified = db.q1(
            """SELECT COUNT(DISTINCT cp.file_id) AS files,
                      COALESCE(SUM(f.size), 0) AS bytes
               FROM copies cp
               JOIN destinations dest ON dest.id = cp.destination_id
               JOIN files f ON f.id = cp.file_id
               WHERE cp.state='verified' AND dest.device_id IS NOT NULL
                     AND dest.device_id = ?""",
            (drive.get("device_id"),))
        row = dict(drive)
        row["catalog_files"] = verified["files"]
        row["catalog_bytes"] = verified["bytes"]
        out.append(row)
    return {"drives": out, "as_at": _now()}


def only_copy(query=None, limit=MAX_PAGE):
    """Files that exist on exactly one physical drive, on record.

    The other question worth answering, and a one-line variant of the audit.
    """
    return audit(minimum=2, mode="record", query=query, limit=limit)


# --------------------------------------------------------------------------
# Getting it out of here

EXPORT_COLUMNS = [
    ("project", "project"), ("day_name", "day"), ("shoot_date", "shoot_date"),
    ("roll", "roll"), ("card_label", "card"), ("kind", "kind"),
    ("rel_path", "path_on_card"), ("size", "size"), ("category", "category"),
    ("scene", "scene"), ("take_name", "take"), ("circled", "circled"),
    ("reel_name", "camera"), ("codec_name", "codec"),
    ("frame_rate", "frame_rate"), ("start_timecode", "start_timecode"),
    ("duration_seconds", "duration_seconds"),
    ("src_hash", "checksum"), ("hash_algo", "checksum_algorithm"),
    ("verified_drives", "verified_drives"),
    ("present_drives", "present_drives_at_export"),
]


def export(result, fmt="csv"):
    """A library that only exists inside this application dies with it.

    Carries the same two numbers and the same "as at" stamp as the screen, so
    an exported row cannot be read as more current than it is.
    """
    rows = result["rows"]
    if fmt == "json":
        return json.dumps({
            "tool": _tool_string(),
            "as_at": result["as_at"],
            "presence_checked": result.get("presence_checked",
                                           result.get("mode") == "present"),
            "drives_offline": result.get("drives_offline", []),
            "complete": result.get("complete", True),
            "rows": [{name: row.get(key) for key, name in EXPORT_COLUMNS}
                     for row in rows],
        }, indent=2), "application/json"

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"# {_tool_string()} — library export"])
    writer.writerow([f"# as at {result['as_at']}"])
    if result.get("drives_offline"):
        writer.writerow(["# drives offline when this ran, not checked: "
                         + ", ".join(result["drives_offline"])])
    writer.writerow([name for _, name in EXPORT_COLUMNS])
    for row in rows:
        writer.writerow([row.get(key) for key, _ in EXPORT_COLUMNS])
    return buffer.getvalue(), "text/csv"


def _tool_string():
    import version
    return version.TOOL_STRING

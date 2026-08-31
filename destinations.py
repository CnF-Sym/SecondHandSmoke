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

"""Attaching a drive to a shoot day.

One function, because there is now more than one way to ask for it: typed into
the interface, or applied from a preset. A preset that could attach a drive by
a slacker set of rules than the interface does would be a hole straight through
the two-drive requirement — the whole point of the gate is that there is no
second route to the same place.
"""

import os

import db
import storage

ROLES = ("primary", "backup", "offsite")


def attach(day_id, raw_path, label=None, role="backup"):
    """Add one destination to a day.

    Returns (info, error). Exactly one of them is None. `info` is the inspected
    path, so callers can report which physical drive was actually attached.
    """
    raw_path = (raw_path or "").strip()
    if not raw_path:
        return None, "Choose a destination folder"
    if role not in ROLES:
        return None, "Destination role must be primary, backup or offsite"

    path = storage.canonical(raw_path)
    label = (label or os.path.basename(path) or "Destination").strip()[:80]
    try:
        info = storage.inspect_path(path)
    except storage.StorageError as exc:
        return None, str(exc)
    if not os.access(info["path"], os.W_OK | os.R_OK):
        return None, f"No read/write access to {info['path']}"

    # Two folders on one disk are one place for a copy to die. This is the
    # check that makes "two drives" mean two drives.
    existing_device = db.q1(
        "SELECT label FROM destinations WHERE day_id=? AND device_id=?",
        (day_id, info["device_id"]),
    )
    if existing_device:
        return None, f"{existing_device['label']} is already on this physical drive"

    try:
        db.ex("""INSERT INTO destinations
              (day_id,label,path,canonical_path,device_id,volume_uuid,role)
              VALUES (?,?,?,?,?,?,?)""",
              (day_id, label, info["path"], info["path"], info["device_id"],
               info["volume_uuid"], role))
    except Exception:
        return None, "That destination is already on this day"
    return info, None

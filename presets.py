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

"""Saved job configurations, grouped into folders.

The same show comes back tomorrow with the same three drives, the same project
name and the same logo on the paperwork. Setting all of that up again by hand,
every morning, is how a drive ends up in the wrong slot.

A preset holds what does not change day to day: the destinations and their
roles, the project name, and the report branding. It never holds a shoot day,
a card, or anything about verification.

**A preset cannot loosen anything.** Applying one attaches drives through
exactly the same function the interface uses, which means the same path checks,
the same access checks, and the same refusal to count two folders on one
physical disk as two drives. If a preset cannot be applied cleanly it says
which parts failed and why, and the parts that did work stay. There is no code
path here that reaches the catalog behind that check.

**A preset remembers which physical drive it was built from, and does not
insist on it.** Drives get rotated, cloned and relabelled; a preset that
refused to run because SHOW_A is a different disk this week would be useless.
But a different disk mounted at a familiar path is worth knowing about, because
the operator's mental model of "SHOW_A already has Monday on it" is the thing
that quietly stops being true. So it is applied, and it is reported.
"""

import os
import shutil

import db
import destinations
import hashing
import naming
import pdfreport
import storage

UNGROUPED = 0


def preset_logo_path(preset_id):
    """Each preset keeps its own copy of its logo.

    The active logo is a single slot, overwritten whenever one is chosen. If a
    preset merely pointed at that slot, every preset would end up branded with
    whichever show was set up last — which is the exact opposite of what a
    preset is for. So a preset owning a logo owns a copy of it.
    """
    return os.path.join(os.path.dirname(pdfreport.logo_store_path()),
                        f"preset_{int(preset_id)}.png")


def _copy_logo(source, target):
    """Copy a logo, or leave things as they are. Never raises."""
    try:
        if source and os.path.isfile(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            partial = target + ".partial"
            shutil.copyfile(source, partial)
            os.replace(partial, target)
            return target
    except OSError:
        pass
    return ""


def _folder_name(folder_id):
    if not folder_id:
        return ""
    row = db.q1("SELECT name FROM preset_folders WHERE id=?", (folder_id,))
    return row["name"] if row else ""


def list_all():
    """Every folder and every preset, ready for the interface."""
    folders = [{"id": UNGROUPED, "name": "Ungrouped"}]
    folders += [dict(row) for row in
                db.q("SELECT id, name FROM preset_folders ORDER BY name")]

    out = []
    for folder in folders:
        rows = db.q("""SELECT * FROM presets WHERE folder_id=?
                       ORDER BY name""", (folder["id"],))
        items = []
        for row in rows:
            preset = dict(row)
            preset["destinations"] = [dict(d) for d in db.q(
                """SELECT label, path, role, device_id FROM preset_destinations
                   WHERE preset_id=? ORDER BY position, id""", (row["id"],))]
            items.append(preset)
        if items or folder["id"] != UNGROUPED:
            out.append({**folder, "presets": items})
    return out


def create_folder(name):
    name = (name or "").strip()[:80]
    if not name:
        return None, "Name the folder"
    if db.q1("SELECT id FROM preset_folders WHERE name=?", (name,)):
        return None, f"There is already a folder called {name}"
    db.ex("INSERT INTO preset_folders (name, created_at) VALUES (?,?)",
          (name, db.now()))
    return db.q1("SELECT id FROM preset_folders WHERE name=?", (name,))["id"], None


def delete_folder(folder_id):
    """Remove a folder. Its presets are kept and become ungrouped.

    Deleting a container should not delete its contents by surprise. Anyone who
    wants a preset gone can say so about that preset.
    """
    if not folder_id:
        return "Ungrouped is not a folder you can delete"
    db.ex("UPDATE presets SET folder_id=? WHERE folder_id=?", (UNGROUPED, folder_id))
    db.ex("DELETE FROM preset_folders WHERE id=?", (folder_id,))
    return None


def save_from_day(day_id, name, folder_id=UNGROUPED, production=None, logo=None):
    """Capture the current day's setup as a preset.

    Overwrites a preset of the same name in the same folder, because that is
    what "save" means to everyone who has ever used a menu, and the alternative
    is nine presets called SHOW A.
    """
    name = (name or "").strip()[:80]
    if not name:
        return None, "Name the preset"
    day = db.q1("SELECT * FROM days WHERE id=?", (day_id,))
    if not day:
        return None, "Start a shoot day before saving a preset"
    rows = db.q("""SELECT label, path, role, device_id, volume_uuid
                   FROM destinations WHERE day_id=?
                   ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id""",
                (day_id,))
    if not rows:
        return None, "Add the destinations you want to save before saving a preset"
    if folder_id and not db.q1("SELECT id FROM preset_folders WHERE id=?", (folder_id,)):
        return None, "That folder no longer exists"

    stamp = db.now()
    existing = db.q1("SELECT id, created_at FROM presets WHERE folder_id=? AND name=?",
                     (folder_id, name))
    if existing:
        preset_id = existing["id"]
        db.ex("""UPDATE presets SET project=?, production_name=?, logo_path=?,
                 updated_at=? WHERE id=?""",
              (day["project"], production or "", logo or "", stamp, preset_id))
        db.ex("DELETE FROM preset_destinations WHERE preset_id=?", (preset_id,))
    else:
        db.ex("""INSERT INTO presets
                 (folder_id, name, project, production_name, logo_path,
                  created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
              (folder_id, name, day["project"], production or "", logo or "",
               stamp, stamp))
        preset_id = db.q1("SELECT id FROM presets WHERE folder_id=? AND name=?",
                          (folder_id, name))["id"]

    db.ex("UPDATE presets SET pattern_camera=?, pattern_sound=?, "
          "filter_camera=?, filter_sound=?, hash_algo=? WHERE id=?",
          (day["pattern_camera"] or "", day["pattern_sound"] or "",
           day["filter_camera"] or "", day["filter_sound"] or "",
           day["hash_algo"] or "", preset_id))

    # Branding travels with the preset, including its own copy of the logo.
    stored_logo = _copy_logo(logo or pdfreport.branding()["logo"],
                             preset_logo_path(preset_id))
    db.ex("UPDATE presets SET production_name=?, logo_path=? WHERE id=?",
          (production if production is not None
           else (db.setting(pdfreport.SETTING_PRODUCTION) or ""),
           stored_logo, preset_id))

    for position, row in enumerate(rows):
        db.ex("""INSERT INTO preset_destinations
                 (preset_id, position, label, path, role, device_id, volume_uuid)
                 VALUES (?,?,?,?,?,?,?)""",
              (preset_id, position, row["label"], row["path"], row["role"],
               row["device_id"], row["volume_uuid"]))
    return preset_id, None


def delete_preset(preset_id):
    db.ex("DELETE FROM preset_destinations WHERE preset_id=?", (preset_id,))
    db.ex("DELETE FROM presets WHERE id=?", (preset_id,))
    # Only this preset's own copy of a logo, which lives in our folder and
    # nowhere near anyone's media.
    own_logo = preset_logo_path(preset_id)
    if os.path.isfile(own_logo):
        try:
            os.remove(own_logo)
        except OSError:
            pass
    return None


def apply(preset_id, day_id):
    """Attach a preset's destinations to a day.

    Never all-or-nothing. On a cart, three drives where two mounted is a normal
    morning, and refusing the two that are there because the third is not would
    be actively unhelpful. Every destination is reported individually: attached,
    already present, or refused with the reason.
    """
    preset = db.q1("SELECT * FROM presets WHERE id=?", (preset_id,))
    if not preset:
        return None, "That preset no longer exists"
    day = db.q1("SELECT * FROM days WHERE id=?", (day_id,))
    if not day:
        return None, "Start a shoot day before applying a preset"

    result = {"preset": preset["name"], "folder": _folder_name(preset["folder_id"]),
              "attached": [], "skipped": [], "failed": [], "warnings": []}

    for row in db.q("""SELECT * FROM preset_destinations WHERE preset_id=?
                       ORDER BY position, id""", (preset_id,)):
        already = db.q1(
            "SELECT label FROM destinations WHERE day_id=? AND canonical_path=?",
            (day_id, storage.canonical(row["path"])))
        if already:
            result["skipped"].append({"label": row["label"], "path": row["path"],
                                      "reason": "already on this day"})
            continue

        info, error = destinations.attach(day_id, row["path"], row["label"], row["role"])
        if error:
            result["failed"].append({"label": row["label"], "path": row["path"],
                                     "reason": error})
            continue
        result["attached"].append({"label": row["label"], "path": info["path"],
                                   "role": row["role"]})
        # Applied either way; said out loud either way.
        if row["device_id"] and info["device_id"] != row["device_id"]:
            result["warnings"].append(
                f"{row['label']} is a different physical drive than when this "
                f"preset was saved. It was attached anyway — check it is the one "
                f"you mean before you start copying.")

    # Report branding is part of the setup, so it comes across too. The day's
    # project name deliberately does not: renaming the project of a day that
    # already has folders on three drives is not something a preset gets to do
    # quietly. It is reported instead, for a day that has not been made yet.
    # Naming patterns are part of the setup, and are applied to the day the
    # same way the drives are. A pattern that would not validate is dropped
    # rather than written: a preset is not a way to smuggle a bad pattern past
    # the check the interface makes.
    for column, key in (("pattern_camera", "pattern_camera"),
                        ("pattern_sound", "pattern_sound")):
        value = (preset[column] or "").strip()
        if not value:
            continue
        if naming.validate(value):
            result["warnings"].append(
                f"The {key.replace('pattern_', '')} naming pattern in this preset "
                f"is not valid and was not applied.")
            continue
        db.ex(f"UPDATE days SET {column}=? WHERE id=?", (value, day_id))
        result[key] = value

    # Filters come across too, and are announced rather than applied quietly.
    # A preset that silently starts skipping files is the exact failure the
    # amber verdict exists to prevent, arriving one step earlier.
    for column in ("filter_camera", "filter_sound"):
        value = (preset[column] or "").strip()
        db.ex(f"UPDATE days SET {column}=? WHERE id=?", (value, day_id))
        result[column] = value
        if value:
            result["warnings"].append(
                f"This preset filters {column.replace('filter_', '')} cards: "
                f"{naming.describe_filters(value)} Files it skips are listed on "
                f"the card and the card can never read plain SAFE TO FORMAT.")

    # The checksum comes across only if nothing on this day has been copied
    # yet. A preset is not a way around the rule that a card keeps the
    # algorithm it started with.
    wanted = hashing.normalise(preset["hash_algo"]) if preset["hash_algo"] else None
    if wanted and wanted != hashing.normalise(day["hash_algo"]):
        copied = db.q1("""SELECT 1 FROM copies c JOIN files f ON f.id=c.file_id
                          JOIN cards k ON k.id=f.card_id
                          WHERE k.day_id=? AND c.state IN ('copied','verified')
                          LIMIT 1""", (day_id,))
        if copied:
            result["warnings"].append(
                f"This preset uses {hashing.label(wanted)} checksums, but this "
                f"day has already copied files. The checksum was left as "
                f"{hashing.label(day['hash_algo'])}.")
        else:
            db.ex("UPDATE days SET hash_algo=? WHERE id=?", (wanted, day_id))
            result["hash_algo"] = wanted

    result["project"] = preset["project"] or ""
    if preset["production_name"] is not None:
        db.setting(pdfreport.SETTING_PRODUCTION, preset["production_name"])
        result["production"] = preset["production_name"]
    if preset["logo_path"] and os.path.isfile(preset["logo_path"]):
        active = _copy_logo(preset["logo_path"], pdfreport.logo_store_path())
        if active:
            db.setting(pdfreport.SETTING_LOGO, active)
            result["logo"] = active
    return result, None

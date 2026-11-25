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

"""Finding cards and working out what is on them.

Deliberately does not rely on Canon's folder layout. Cameras change their
directory structure between firmware revisions and a tool that hard-codes
CONTENTS/CLIPS001 will silently see an empty card one day. Instead this
walks the volume and classifies by file extension, which is stable.

Nothing here writes to the source volume.
"""

import os
import xxhash

import storage

SCAN_ROOT = os.environ.get("DIT_SCAN_ROOT", "/Volumes")

VIDEO_EXT = {".mxf", ".crm", ".mov", ".mp4", ".braw", ".r3d"}
AUDIO_EXT = {".wav", ".bwf", ".aif", ".aiff"}
SIDECAR_EXT = {".xml", ".cpf", ".thm", ".if", ".smi", ".sfl", ".txt", ".csv", ".json"}

# Volumes that are never cards.
SKIP_NAMES = {
    "Macintosh HD", "Preboot", "Recovery", "VM", "Update", "xarts",
    "iSCPreboot", "Hardware", "Data", "com.apple.TimeMachine.localsnapshots",
}
SKIP_PREFIXES = ("/System",)
SKIP_FOLDERS = {".Spotlight-V100", ".fseventsd", ".TemporaryItems", ".Trashes"}
SAMPLE_BYTES = 64 * 1024


def _category(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in SIDECAR_EXT:
        return "sidecar"
    return "other"


def _sample_hash(path, size):
    """Identify content without reading a whole card during every scan."""
    h = xxhash.xxh64()
    with open(path, "rb") as fh:
        h.update(fh.read(SAMPLE_BYTES))
        if size > SAMPLE_BYTES * 2:
            fh.seek(max(SAMPLE_BYTES, (size // 2) - (SAMPLE_BYTES // 2)))
            h.update(fh.read(SAMPLE_BYTES))
        if size > SAMPLE_BYTES:
            fh.seek(max(SAMPLE_BYTES, size - SAMPLE_BYTES))
            h.update(fh.read(SAMPLE_BYTES))
    return h.hexdigest()


def walk_volume(root):
    """Return every real file on the volume, with category, skipping the
    junk macOS sprinkles on removable media."""
    out = []
    errors = []

    def walk_error(exc):
        errors.append(f"Cannot read folder: {exc}")

    for dirpath, dirnames, filenames in os.walk(
            root, followlinks=False, onerror=walk_error):
        for dirname in tuple(dirnames):
            full_dir = os.path.join(dirpath, dirname)
            if os.path.islink(full_dir):
                errors.append(
                    f"Symbolic link is not safe to ingest: "
                    f"{os.path.relpath(full_dir, root)}"
                )
        dirnames[:] = [d for d in dirnames if d not in SKIP_FOLDERS
                      and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            if fn.startswith("._") or fn in (".DS_Store", ".metadata_never_index"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.lstat(full)
            except OSError as exc:
                errors.append(f"Cannot inspect {os.path.relpath(full, root)}: {exc}")
                continue
            if os.path.islink(full):
                errors.append(f"Symbolic link is not safe to ingest: {os.path.relpath(full, root)}")
                continue
            if not os.path.isfile(full):
                continue
            try:
                sample_hash = _sample_hash(full, st.st_size)
            except OSError as exc:
                errors.append(f"Cannot read {os.path.relpath(full, root)}: {exc}")
                continue
            out.append({
                "rel_path": os.path.relpath(full, root),
                "abs_path": full,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "category": _category(fn),
                "sample_hash": sample_hash,
            })
    out.sort(key=lambda f: f["rel_path"])
    return out, errors


def inventory_fingerprint(files):
    """Fingerprint the complete path-and-size inventory."""
    h = xxhash.xxh64()
    for f in files:
        h.update(f"{f['rel_path']}|{f['size']}\n".encode("utf-8"))
    return h.hexdigest()


def fingerprint(files):
    """Content fingerprint for 'have I already offloaded this card?'.

    Path, size and samples from the beginning, middle and end of every file. mtime is
    deliberately excluded because exFAT stores local time with no timezone.
    Sampling prevents two same-shaped cards from inheriting one another's
    verification state without making a scan read hundreds of gigabytes.
    """
    h = xxhash.xxh64()
    for f in files:
        h.update(
            f"{f['rel_path']}|{f['size']}|{f['sample_hash']}\n".encode("utf-8")
        )
    return h.hexdigest()


def classify(files):
    has_video = any(f["category"] == "video" for f in files)
    has_audio = any(f["category"] == "audio" for f in files)
    if has_video and has_audio:
        # Camera cards carry scratch audio inside the MXF, not as loose WAVs,
        # so loose WAVs alongside video means someone pooled two cards.
        return "mixed"
    if has_video:
        return "camera"
    if has_audio:
        return "sound"
    return "unknown"


def guess_roll(files):
    """Work out the roll designation.

    Camera: Canon names clips A003C007_260118AB, so the four characters
    before the 'C' are the roll.

    Sound: recorders normally write one folder per roll (CRACK_R01), so if
    every audio file sits under a single top-level folder, use that name.
    """
    for f in files:
        if f["category"] != "video":
            continue
        stem = os.path.splitext(os.path.basename(f["rel_path"]))[0]
        if len(stem) >= 8 and stem[0].isalpha() and stem[1:4].isdigit() and stem[4].upper() == "C":
            return stem[:4].upper()

    tops = {f["rel_path"].split(os.sep)[0] for f in files if f["category"] == "audio"
            and os.sep in f["rel_path"]}
    if len(tops) == 1:
        return tops.pop().upper()
    return None


def inspect_volume(path):
    files, errors = walk_volume(path)
    media = [f for f in files if f["category"] in ("video", "audio")]
    return {
        "path": storage.canonical(path),
        "files": files,
        "errors": errors,
        "kind": classify(files),
        "roll": guess_roll(files),
        "fingerprint": fingerprint(files),
        "inventory_fingerprint": inventory_fingerprint(files),
        "file_count": len(files),
        "media_count": len(media),
        "total_bytes": sum(f["size"] for f in files),
    }


def _has_media(path):
    # A skipped destination can contain a whole shoot. Stop at the first
    # media file without opening it or fingerprinting the drive.
    for folder, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP_FOLDERS]
        for name in files:
            full = os.path.join(folder, name)
            if (not name.startswith("._") and _category(name) in ("video", "audio")
                    and not os.path.islink(full) and os.path.isfile(full)):
                return True
    return False


def scan(root=None, exclude_paths=(), exclude_device_ids=(), *, destinations=()):
    """List candidate cards under the scan root."""
    root = root or SCAN_ROOT
    found = []
    problems = []
    excluded = {storage.canonical(p): p for p in exclude_paths}
    excluded.update((storage.canonical(d["path"]), d["label"]) for d in destinations)
    excluded_devices = {device: device for device in exclude_device_ids}
    excluded_devices.update((d["device_id"], d["label"]) for d in destinations
                            if d["device_id"])
    if not os.path.isdir(root):
        return found, [f"Scan root is not mounted: {root}"]
    for name in sorted(os.listdir(root)):
        if name in SKIP_NAMES or name.startswith("."):
            continue
        path = os.path.join(root, name)
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            continue
        if not os.path.isdir(path):
            continue
        real = storage.canonical(path)
        overlap = next((label for p, label in excluded.items()
                        if storage.paths_overlap(real, p)), None)
        if overlap is not None:
            # A volume that is, or holds, a destination is one of today's
            # drives, not a card. Every destination in /Volumes lands here on
            # every scan once it holds footage, so reporting it would put a red
            # line under the scan button for each drive, all day long.
            continue
        try:
            device = storage.inspect_path(real)["device_id"]
            if device in excluded_devices:
                if _has_media(real):
                    label = excluded_devices[device]
                    problems.append(f"{name}: skipped — it is on the same physical drive "
                                    f"as destination {label}.")
                continue
            item = inspect_volume(real)
        except (PermissionError, OSError, storage.StorageError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        if item["errors"]:
            problems.extend(f"{name}: {message}" for message in item["errors"])
        if not item["media_count"]:
            continue
        item.update({
            "label": name,
        })
        found.append(item)
    return found, problems

# Second Hand Smoke — verified card offload for camera and sound media.
# Copyright (C) 2026  Brian / CnF Works
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

"""Filesystem identity and path-safety helpers.

The ingest gate must count physical storage devices, not destination folders.
On macOS ``diskutil`` gives us the parent disk; elsewhere ``st_dev`` is the
best portable identity available.  All paths are canonicalized before they are
stored or compared so aliases and symlinks cannot create fake destinations.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys


class StorageError(RuntimeError):
    pass


def canonical(path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _diskutil_info(path):
    if sys.platform != "darwin":
        return {}
    mount_point = canonical(path)
    while not os.path.ismount(mount_point):
        parent = os.path.dirname(mount_point)
        if parent == mount_point:
            break
        mount_point = parent
    try:
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", mount_point],
            check=True, capture_output=True, timeout=10,
        )
        return plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException,
            ValueError):
        return {}


def _hdiutil_images():
    """List every disk image currently attached via hdiutil, if any."""
    if sys.platform != "darwin":
        return []
    try:
        result = subprocess.run(
            ["/usr/bin/hdiutil", "info", "-plist"],
            check=True, capture_output=True, timeout=10,
        )
        return plistlib.loads(result.stdout).get("images") or []
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException,
            ValueError):
        return []


def _disk_image_backing_path(disk_identifier):
    """If disk_identifier (e.g. 'disk4') is a mounted disk image (.dmg,
    .sparsebundle, .sparseimage …), return the real path to its backing
    image file. A disk image's own DeviceIdentifier is a *virtual* disk —
    diskutil correctly treats it as physically separate from every other
    virtual disk, even when two images both live on one real drive. Without
    this, two disk images on a single physical SSD would pass the "two
    physically distinct destinations" check even though one drive failure
    destroys both copies.
    """
    if not disk_identifier:
        return None
    for image in _hdiutil_images():
        entities = image.get("system-entities") or []
        if any(str(e.get("dev-entry", "")).rsplit("/", 1)[-1] == disk_identifier
               for e in entities if isinstance(e, dict)):
            image_path = image.get("image-path")
            return os.path.realpath(image_path) if image_path else None
    return None


def inspect_path(path, _depth=0):
    """Return the canonical path and stable-enough volume/device identities."""
    real = canonical(path)
    if not os.path.isdir(real):
        raise StorageError(f"Not a mounted folder: {real}")

    try:
        stat = os.stat(real)
        usage = shutil.disk_usage(real)
    except OSError as exc:
        raise StorageError(f"Cannot inspect {real}: {exc}") from exc

    info = _diskutil_info(real)
    volume_uuid = info.get("VolumeUUID") or info.get("APFSVolumeUUID")

    # ParentWholeDisk correctly treats two partitions on one physical disk as
    # one backup device. APFS containers may expose their stores explicitly.
    physical = info.get("ParentWholeDisk")
    stores = info.get("APFSPhysicalStores") or []
    store_ids = sorted(
        str(x.get("DeviceIdentifier")) for x in stores
        if isinstance(x, dict) and x.get("DeviceIdentifier")
    )
    if store_ids:
        physical = "+".join(store_ids)
    if not physical:
        physical = info.get("DeviceIdentifier")

    device_id = f"disk:{physical}" if physical else f"st_dev:{stat.st_dev}"

    # Fold a disk image into the identity of whatever physical drive its
    # backing file actually sits on. The folded value is the backing drive's
    # own device_id, with no prefix, so an image collapses against both other
    # images on that drive AND against a destination written straight to it.
    # Depth-capped in case of a pathological self-referencing mount.
    disk_id = info.get("DeviceIdentifier")
    if disk_id and _depth < 6:
        backing_path = _disk_image_backing_path(disk_id)
        if backing_path and os.path.exists(backing_path):
            try:
                backing = inspect_path(os.path.dirname(backing_path), _depth=_depth + 1)
                device_id = backing["device_id"]
            except StorageError:
                pass

    return {
        "path": real,
        "device_id": device_id,
        "volume_uuid": str(volume_uuid) if volume_uuid else None,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    }


def paths_overlap(first, second):
    first = canonical(first)
    second = canonical(second)
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return False
    return common == first or common == second


def validate_destination_set(source_path, destinations, min_copies,
                             require_writable=True):
    """Validate mounts and return their current identities.

    A stored device identity is checked on every job so a different drive
    mounted later at the same path cannot inherit verification records.
    The two-copy floor is enforced here and cannot be lowered by a caller.
    """
    min_copies = max(2, int(min_copies))
    errors = []
    checked = []
    seen_devices = {}

    source = None
    if source_path and os.path.isdir(canonical(source_path)):
        try:
            source = inspect_path(source_path)
        except StorageError as exc:
            errors.append(str(exc))

    for dest in destinations:
        label = dest["label"]
        try:
            current = inspect_path(dest["path"])
        except StorageError as exc:
            errors.append(f"{label}: {exc}")
            continue

        if require_writable and not os.access(current["path"], os.W_OK):
            errors.append(f"{label}: destination is not writable")
        if not os.access(current["path"], os.R_OK):
            errors.append(f"{label}: destination is not readable")

        stored_device = dest["device_id"] if "device_id" in dest.keys() else None
        stored_uuid = dest["volume_uuid"] if "volume_uuid" in dest.keys() else None
        # Identity check. A stored UUID is the strongest signal, but if the
        # current probe cannot produce one we must NOT silently skip the check
        # -- that is exactly the moment something about the mount has changed.
        # Fall through to the device_id comparison instead.
        swapped = False
        if stored_uuid and current["volume_uuid"]:
            swapped = stored_uuid != current["volume_uuid"]
        elif stored_device:
            swapped = stored_device != current["device_id"]
        if swapped:
            errors.append(
                f"{label}: a different device is mounted at {current['path']}; "
                "remove and add the destination again"
            )

        other = seen_devices.get(current["device_id"])
        if other:
            errors.append(
                f"{label} and {other} are on the same physical device; "
                "verified copies require separate drives"
            )
        else:
            seen_devices[current["device_id"]] = label

        if source:
            if current["device_id"] == source["device_id"]:
                errors.append(f"{label}: destination is on the source card's device")
            if paths_overlap(source["path"], current["path"]):
                errors.append(f"{label}: destination overlaps the source card path")

        checked.append((dest, current))

    if len(seen_devices) < min_copies:
        errors.append(
            f"Only {len(seen_devices)} separate destination drive(s) are ready; "
            f"{min_copies} are required"
        )
    return checked, errors

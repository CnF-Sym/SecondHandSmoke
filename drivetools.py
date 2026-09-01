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

"""Drive tools: how fast is this drive, and what is already on it.

Two jobs that answer questions about a drive rather than about a card.

**The speed test measures what an offload would actually get.** Not a
manufacturer's number and not a burst into RAM: it writes a real file, fsyncs
it so the drive has genuinely taken it, then reads it back with the page cache
bypassed and checksums what comes off. A "read speed" measured out of RAM is
the same lie that makes an unverified offload feel fast, and this program
exists because of that lie. If cache bypass cannot be established the test
reports the read as unverified rather than quietly printing a fast number.

**The index is the first half of the library.** It walks a drive read-only and
records what is there, and it reads any ASC MHL manifest it finds along the
way — ours or another tool's. An MHL is a list of files with hashes and the day
and roll they belong to, so a drive that has ever been offloaded by anything
that speaks MHL can say what it holds without re-hashing a terabyte. That is
what makes "which drive is Tuesday's A-cam on" answerable later.

Neither job writes anything to a drive except the speed test's own scratch
file, which it removes when it is done, and neither touches the catalog's
record of any card, copy or verification.
"""

import os
import re
import threading
import time
import xml.etree.ElementTree as ET

import db
import offload
import storage

# Big enough that the drive, rather than the queue, is what is being measured.
DEFAULT_TEST_BYTES = 512 * 1024 * 1024
MIN_TEST_BYTES = 32 * 1024 * 1024
MAX_TEST_BYTES = 8 * 1024 * 1024 * 1024
SCRATCH_DIR = ".shs-speedtest"
CHUNK = 8 * 1024 * 1024

_lock = threading.Lock()
_current = None
_last_snapshot = None


# --------------------------------------------------------------------------
# The drive registry


def register(path, label=None):
    """Record this physical drive, or update what we know about it.

    Identity is the device, not the path: /Volumes/SHOW_A is a mount point that
    a different disk can occupy tomorrow. Returns (row, error).
    """
    try:
        info = storage.inspect_path(path)
    except storage.StorageError as exc:
        return None, str(exc)

    key = info["volume_uuid"] or info["device_id"]
    if not key:
        return None, f"{path} has no identity this machine can pin down"

    existing = db.q1("SELECT * FROM drives WHERE identity=?", (key,))
    stamp = db.now()
    if existing:
        db.ex("""UPDATE drives SET label=COALESCE(?, label), last_path=?,
                 device_id=?, volume_uuid=?, last_seen=? WHERE id=?""",
              (label or None, info["path"], info["device_id"],
               info["volume_uuid"], stamp, existing["id"]))
    else:
        db.ex("""INSERT INTO drives
                 (identity, label, last_path, device_id, volume_uuid,
                  first_seen, last_seen)
                 VALUES (?,?,?,?,?,?,?)""",
              (key, label or os.path.basename(info["path"].rstrip("/")) or "Drive",
               info["path"], info["device_id"], info["volume_uuid"], stamp, stamp))
    return db.q1("SELECT * FROM drives WHERE identity=?", (key,)), None


def known_drives():
    rows = db.q("SELECT * FROM drives ORDER BY label")
    out = []
    for row in rows:
        drive = dict(row)
        drive["online"] = bool(drive["last_path"]
                               and os.path.isdir(drive["last_path"]))
        if drive["online"]:
            try:
                current = storage.inspect_path(drive["last_path"])
                identity = current["volume_uuid"] or current["device_id"]
                # A different disk at a familiar mount point is exactly the
                # thing a drive registry exists to notice.
                drive["online"] = identity == drive["identity"]
            except storage.StorageError:
                drive["online"] = False
        summary = db.q1(
            """SELECT COUNT(*) files, COALESCE(SUM(size),0) bytes,
                      MAX(indexed_at) indexed_at
               FROM drive_index WHERE drive_id=?""", (drive["id"],))
        drive.update({"indexed_files": summary["files"],
                      "indexed_bytes": summary["bytes"],
                      "indexed_at": summary["indexed_at"]})
        drive["manifests"] = [dict(m) for m in db.q(
            """SELECT rel_path, project, day_name, roll, file_count, tool
               FROM drive_manifests WHERE drive_id=? ORDER BY rel_path""",
            (drive["id"],))]
        out.append(drive)
    return out


def forget(drive_id):
    """Remove a drive from the registry. Touches the registry, never the drive."""
    db.ex("DELETE FROM drive_index WHERE drive_id=?", (drive_id,))
    db.ex("DELETE FROM drive_manifests WHERE drive_id=?", (drive_id,))
    db.ex("DELETE FROM drives WHERE id=?", (drive_id,))


# --------------------------------------------------------------------------
# Reading manifests that are already on the drive


def _local(tag):
    """A tag name without its namespace. ASC MHL 1.1 has none; 2.0 does."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1]


def read_manifest(path):
    """What an ASC MHL manifest says about the files beside it.

    Reads both formats — 1.1, where the entry is <file> and the digest element
    names the algorithm, and 2.0, where it is <path> under a namespace. Ours or
    anyone's. Returns a dict, or None if this is not a manifest we can read,
    which is never an error, just a file we learned nothing from.
    """
    try:
        if os.path.getsize(path) > 64 * 1024 * 1024:
            return None
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    if _local(root.tag) != "hashlist":
        return None

    # 2.0 names the digest element after the algorithm too, but adds three the
    # 1.1 schema never had. Order is only a preference for which to report when
    # a manifest carries several.
    digest_tags = ("xxhash64be", "xxhash64", "xxhash", "xxh64", "xxh128",
                   "xxh3", "md5", "sha1", "c4")
    files = []
    for entry in root.iter():
        if _local(entry.tag) != "hash":
            continue
        children = {}
        for child in entry:
            children.setdefault(_local(child.tag), child)
        # <file> is 1.1; <path> is 2.0, and there the size is an attribute.
        name_el = children.get("file") or children.get("path")
        if name_el is None or not (name_el.text or "").strip():
            continue
        size = children.get("size")
        size = (size.text if size is not None else None) or name_el.get("size") or 0
        digest = next((children[tag].text for tag in digest_tags
                       if tag in children and children[tag].text), None)
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        files.append({"rel_path": name_el.text.strip(),
                      "size": size,
                      "hash": (digest or "").strip() or None})
    if not files:
        return None
    creator = next((el for el in root.iter()
                    if _local(el.tag) == "creatorinfo"), None)
    tool = ""
    if creator is not None:
        tool_el = next((el for el in creator if _local(el.tag) == "tool"), None)
        if tool_el is not None:
            # 1.1 puts the version in the text; 2.0 puts it in an attribute.
            tool = " ".join(x for x in ((tool_el.text or "").strip(),
                                        tool_el.get("version") or "") if x)
    return {
        "tool": tool,
        "file_count": len(files),
        "files": files,
    }


def _day_from_manifest_path(drive_root, manifest_path):
    """Guess project / day / roll from where the manifest sits.

    A guess, and labelled as one. Our own layout is <drive>/PROJECT/DAY/, and
    the filename is DAY_ROLL.mhl; other tools differ. Wrong labels here cost
    nothing — the file list underneath is the part that matters.
    """
    relative = os.path.relpath(manifest_path, drive_root)
    parts = relative.split(os.sep)
    # An ASC MHL 2.0 manifest sits one level deeper, inside ascmhl/, so the
    # day folder is its grandparent rather than its parent.
    if len(parts) > 1 and parts[-2] == "ascmhl":
        parts = parts[:-2] + parts[-1:]
    project = parts[0] if len(parts) > 1 else ""
    day_name = parts[1] if len(parts) > 2 else ""
    stem = os.path.splitext(os.path.basename(manifest_path))[0]
    if day_name and stem.startswith(day_name + "_"):
        # Our own 1.1 name: DAY_ROLL.mhl
        return project, day_name, stem[len(day_name) + 1:]
    # A 2.0 name: NNNN_ROLL_YYYY-MM-DD_HHMMSSZ.mhl. Strip the generation
    # number off the front and the timestamp off the back, and what is left
    # is what the tool that wrote it called the folder.
    match = re.match(r"^\d{4,}_(.+)_\d{4}-\d{2}-\d{2}_\d{6}Z$", stem)
    return project, day_name, match.group(1) if match else stem


# --------------------------------------------------------------------------
# The jobs


class Job:
    """A speed test or an index. One at a time, like every other job here."""

    def __init__(self, mode, path, label=None, test_bytes=DEFAULT_TEST_BYTES):
        self.mode = mode                     # "speed" | "index"
        self.path = path
        self.label = label
        self.test_bytes = max(MIN_TEST_BYTES, min(int(test_bytes), MAX_TEST_BYTES))
        self.state = "queued"
        self.phase = ""
        self.message = ""
        self.current_file = ""
        self.files_done = 0
        self.bytes_done = 0
        self.bytes_total = 0
        self.result = {}
        self.errors = []
        self.started_epoch = time.time()
        self.finished_epoch = None
        self._cancel = threading.Event()

    def request_cancel(self):
        self._cancel.set()

    def snapshot(self):
        return {
            "job_type": "drive", "mode": self.mode, "state": self.state,
            "phase": self.phase, "message": self.message,
            "current_file": self.current_file,
            "files_done": self.files_done, "files_total": 0,
            "bytes_done": self.bytes_done, "bytes_total": self.bytes_total,
            "percent": (round(self.bytes_done / self.bytes_total * 100, 1)
                        if self.bytes_total else 0.0),
            "seconds_done": 0, "seconds_total": 0,
            "result": self.result, "errors": self.errors[-20:], "warnings": [],
            "started_epoch": self.started_epoch,
            "finished_epoch": self.finished_epoch,
        }

    def run(self):
        global _current, _last_snapshot
        try:
            self.state = "running"
            if self.mode == "speed":
                self.result = self._speed_test()
            else:
                self.result = self._index()
            self.state = "cancelled" if self._cancel.is_set() else "done"
        except Exception as exc:                       # noqa: BLE001
            self.state = "failed"
            self.message = str(exc)
            self.errors.append(str(exc))
        finally:
            self.finished_epoch = time.time()
            with _lock:
                _last_snapshot = self.snapshot()
                _current = None

    # -- speed ------------------------------------------------------------

    def _speed_test(self):
        """Write, fsync, then read back with the cache bypassed.

        The fsync is the honest part of the write number: without it the
        measurement is how fast the operating system accepted the bytes, which
        is a fine way to report 3 GB/s from a drive that cannot do 200 MB/s.
        """
        scratch = os.path.join(self.path, SCRATCH_DIR)
        os.makedirs(scratch, exist_ok=True)
        target = os.path.join(scratch, f"speedtest-{int(time.time())}.bin")
        block = os.urandom(CHUNK)
        self.bytes_total = self.test_bytes * 2      # write pass + read pass

        try:
            self.phase = "Writing"
            written = 0
            started = time.monotonic()
            with open(target, "wb") as handle:
                while written < self.test_bytes:
                    if self._cancel.is_set():
                        raise offload.JobCancelled()
                    chunk = block[:min(CHUNK, self.test_bytes - written)]
                    handle.write(chunk)
                    written += len(chunk)
                    self.bytes_done += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            write_seconds = max(time.monotonic() - started, 1e-6)

            self.phase = "Reading back"
            uncached = True
            started = time.monotonic()
            try:
                digest, read_bytes = offload.hash_file(
                    target, progress=self._tick, require_uncached=True)
            except OSError:
                # Say so rather than printing a number measured out of RAM.
                uncached = False
                digest, read_bytes = offload.hash_file(target, progress=self._tick)
            read_seconds = max(time.monotonic() - started, 1e-6)

            verify_digest, _ = offload.hash_file(target)
            return {
                "bytes": written,
                "write_mb_s": round(written / write_seconds / 1_000_000, 1),
                "read_mb_s": round(read_bytes / read_seconds / 1_000_000, 1),
                "verified": digest == verify_digest,
                "cache_bypassed": uncached,
                "note": ("Read back with the page cache bypassed, so this is the "
                         "drive's speed and not memory's."
                         if uncached else
                         "Cache bypass could not be established on this volume, "
                         "so the read figure may include memory and should not "
                         "be trusted as a drive speed."),
            }
        finally:
            # Our own scratch file, in our own folder, and nothing else. This
            # is the only file this program ever removes from a drive.
            for path in (target,):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(scratch)
            except OSError:
                pass

    def _tick(self, n):
        self.bytes_done += n

    # -- index ------------------------------------------------------------

    def _index(self):
        drive, error = register(self.path, self.label)
        if error:
            raise RuntimeError(error)

        self.phase = "Reading the drive"
        db.ex("DELETE FROM drive_index WHERE drive_id=?", (drive["id"],))
        db.ex("DELETE FROM drive_manifests WHERE drive_id=?", (drive["id"],))
        stamp = db.now()
        root = drive["last_path"]
        files = total_bytes = 0
        manifests = []

        for folder, dirnames, filenames in os.walk(root):
            if self._cancel.is_set():
                raise offload.JobCancelled()
            dirnames[:] = [d for d in dirnames
                           if d not in (".Spotlight-V100", ".Trashes",
                                        ".fseventsd", SCRATCH_DIR)]
            for name in filenames:
                if name.startswith("._") or name == ".DS_Store":
                    continue
                full = os.path.join(folder, name)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                relative = os.path.relpath(full, root)
                db.ex("""INSERT INTO drive_index
                         (drive_id, rel_path, size, mtime, indexed_at)
                         VALUES (?,?,?,?,?)""",
                      (drive["id"], relative, stat.st_size, stat.st_mtime, stamp))
                files += 1
                total_bytes += stat.st_size
                self.files_done = files
                self.bytes_done = total_bytes
                self.current_file = relative

                if name.lower().endswith(".mhl"):
                    manifest = read_manifest(full)
                    if manifest:
                        project, day_name, roll = _day_from_manifest_path(root, full)
                        db.ex("""INSERT INTO drive_manifests
                                 (drive_id, rel_path, project, day_name, roll,
                                  file_count, tool, indexed_at)
                                 VALUES (?,?,?,?,?,?,?,?)""",
                              (drive["id"], relative, project, day_name, roll,
                               manifest["file_count"], manifest["tool"], stamp))
                        manifests.append({"rel_path": relative, "roll": roll,
                                          "day": day_name, "project": project,
                                          "files": manifest["file_count"],
                                          "tool": manifest["tool"]})

        self.phase = "Done"
        return {"drive_id": drive["id"], "label": drive["label"],
                "files": files, "bytes": total_bytes,
                "manifests": manifests}


def start(mode, path, label=None, test_bytes=DEFAULT_TEST_BYTES):
    global _current, _last_snapshot
    if mode not in ("speed", "index"):
        return None, "Unknown drive job"
    if not os.path.isdir(path):
        return None, f"{path} is not a mounted folder"
    if mode == "speed" and not os.access(path, os.W_OK):
        return None, (f"{path} is read-only, so its write speed cannot be "
                      f"measured. Index it instead.")
    with _lock:
        if _current is not None:
            return None, "Another drive job is already running"
        job = Job(mode, path, label=label, test_bytes=test_bytes)
        _current = job
        _last_snapshot = None
    threading.Thread(target=job.run, daemon=True).start()
    return job, None


def current():
    return _current


def status_snapshot():
    job = _current
    return job.snapshot() if job else _last_snapshot


def cancel():
    if _current:
        _current.request_cancel()

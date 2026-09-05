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

"""Verified offload.

The contract:

  1. Source media is opened read-only and never renamed, moved or deleted.
  2. Every byte written is read back off the destination and re-hashed.
  3. Read-back bypasses the OS page cache, so verification tests the drive
     and not the RAM copy of what we just wrote.
  4. Interrupting the job is safe. Restarting it resumes.

Hash is xxHash64. It is not cryptographic and is not meant to be. It
catches bad cables, dying cards, half-written files and bit rot roughly
ten times faster than MD5, which is the entire job.
"""

import concurrent.futures
import fcntl
import os
import shutil
import sys
import threading
import time
import traceback

import xxhash

import cards
import db
import hashing
import media
import naming
import storage

CHUNK = 4 * 1024 * 1024
F_NOCACHE = 48  # Darwin only
F_FULLFSYNC = 51  # Darwin only

_lock = threading.Lock()
_current = None  # active Job, if any
_last_snapshot = None


class JobCancelled(Exception):
    pass


def _open_uncached(path, mode="rb", require_uncached=False):
    """Open a file with the macOS page cache disabled for this descriptor."""
    f = open(path, mode)
    if sys.platform == "darwin":
        try:
            fcntl.fcntl(f.fileno(), F_NOCACHE, 1)
        except OSError as exc:
            if require_uncached:
                f.close()
                raise OSError(f"macOS cache bypass failed for {path}: {exc}") from exc
    return f


def hash_file(path, progress=None, require_uncached=False, algo=None,
              should_stop=None):
    """Checksum a file with the page cache bypassed.

    `algo` defaults to xxHash64, which is what every internal check in this
    program uses. Media hashing passes the shoot day's chosen algorithm.

    `should_stop` is checked between chunks and raises JobCancelled. Without
    it, cancelling during a read-back had to wait for the whole file: fine for
    a rig fixture, minutes for a 100 GB camera file on a tired drive. Cancel
    is a promise that the program stops promptly, and a promise that only
    holds on small files is not one.
    """
    h = hashing.new(algo or hashing.DEFAULT)
    done = 0
    with _open_uncached(path, require_uncached=require_uncached) as f:
        while True:
            if should_stop is not None and should_stop():
                raise JobCancelled()
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(len(chunk))
    return h.hexdigest(), done


def kind_dir(kind):
    return {"camera": "CAMERA", "sound": "SOUND"}.get(kind, "MEDIA")


def dest_path_for(dest_root, project, day_name, card, rel_path):
    bucket = card["roll"] or card["label"]
    if os.path.isabs(rel_path) or ".." in rel_path.split(os.sep):
        raise ValueError(f"Unsafe source path: {rel_path}")
    parts = rel_path.split(os.sep)
    if len(parts) > 1 and parts[0].casefold() == str(bucket).casefold():
        # Sound recorders commonly wrap every take in a folder already named
        # for the roll. Avoid SOUND/ROLL/ROLL while preserving its contents.
        rel_path = os.path.join(*parts[1:])
    root = storage.canonical(dest_root)
    target = os.path.abspath(os.path.join(
        root, project, day_name, kind_dir(card["kind"]), bucket, rel_path
    ))
    if os.path.commonpath((root, target)) != root:
        raise ValueError("Project, day or roll would write outside the destination")
    return target


def algo_for(day):
    """The checksum this shoot day uses for media.

    A day, not a card and not a global preference: a manifest covers a day's
    worth of files and every hash in it has to be the same kind of thing.
    """
    try:
        return hashing.normalise(day["hash_algo"])
    except (IndexError, KeyError):
        return hashing.DEFAULT


def _algo_changed(files, dests, algo):
    """Refuse to resume a card under a different checksum than it started with.

    Half a card hashed one way and half the other is not a resumable state, and
    it is not a manifest anyone can write. The already-verified copies keep
    their digests; a new run would produce a different kind of digest for the
    rest and have nothing to compare either against.
    """
    for file_row in files:
        stored = file_row["hash_algo"] if "hash_algo" in file_row.keys() else None
        if not stored or hashing.normalise(stored) == algo:
            continue
        if db.q1("SELECT 1 FROM copies WHERE file_id=? AND "
                 "state IN ('copied','verified') LIMIT 1", (file_row["id"],)):
            return (f"This card was already copied using "
                    f"{hashing.label(stored)}, and the shoot day is now set to "
                    f"{hashing.label(algo)}. Put the checksum back, or start "
                    f"this card again on a clean destination folder.")
    return None


def pattern_for(day, kind):
    """The naming pattern this day uses for this kind of card.

    NULL or empty means the default, which renames nothing. Renaming is
    something an operator asks for, never something that happens to them.
    """
    column = "pattern_sound" if kind == "sound" else "pattern_camera"
    try:
        value = day[column]
    except (IndexError, KeyError):
        value = None
    return (value or "").strip() or naming.DEFAULT_PATTERN


def filters_for(day, kind):
    """The include/exclude filter this day uses for this kind of card."""
    column = "filter_sound" if kind == "sound" else "filter_camera"
    try:
        return (day[column] or "").strip()
    except (IndexError, KeyError):
        return ""


def apply_filters(card, day):
    """Record which files this offload will deliberately skip.

    Written once, at the point the decision is made, and never inferred at read
    time. A filter changed after a card was copied must not retroactively
    rewrite what happened on a shoot day — and a file that already has a copy
    on a drive is not "skipped" by any meaningful definition, so it is never
    marked excluded no matter what the filter now says.

    Returns (kept, skipped) counts.
    """
    text = filters_for(day, card["kind"])
    rows = db.q("SELECT id, rel_path FROM files WHERE card_id=?", (card["id"],))
    kept = skipped = 0
    for row in rows:
        already = db.q1("SELECT 1 FROM copies WHERE file_id=? AND "
                        "state IN ('copied','verified') LIMIT 1", (row["id"],))
        excluded = 0 if already else (0 if naming.is_included(row["rel_path"], text) else 1)
        db.ex("UPDATE files SET excluded=? WHERE id=?", (excluded, row["id"]))
        if excluded:
            skipped += 1
        else:
            kept += 1
    return kept, skipped


def active_files(card_id):
    """The files this card will actually copy — everything not filtered out."""
    return db.q("SELECT * FROM files WHERE card_id=? AND COALESCE(excluded,0)=0 "
                "ORDER BY rel_path", (card_id,))


def name_plan(card, day):
    """Every destination name for this card, keyed by file id.

    Computed once per job from the card's whole file list, so the index token
    is stable and a resumed offload lands on exactly the paths the interrupted
    one was heading for.
    """
    rows = db.q("""SELECT f.id, f.rel_path, f.category,
                          m.scene, m.take_name, m.reel_name
                   FROM files f LEFT JOIN media_metadata m ON m.file_id=f.id
                   WHERE f.card_id=? AND COALESCE(f.excluded,0)=0
                   ORDER BY f.rel_path""", (card["id"],))
    planned = naming.plan([dict(row) for row in rows], card, day,
                          pattern_for(day, card["kind"]))
    return {entry["id"]: entry for entry in planned}


def _card_source_problem(card):
    if not os.path.isdir(card["source_path"]):
        return f"Card is not mounted at {card['source_path']}"
    current = cards.inspect_volume(card["source_path"])
    if current["errors"]:
        return "Source card has read errors: " + current["errors"][0]
    if current["fingerprint"] != card["fingerprint"]:
        return "Source card contents changed; scan and register it again"
    inventory = card["inventory_fingerprint"]
    if not inventory:
        return "This card uses an older identity record; scan and re-link it before offload"
    if current["inventory_fingerprint"] != inventory:
        return "Source card file inventory changed; scan and register it again"
    return None


def _row_id(row):
    """A row's id, or None.

    The collision check is deliberately callable with hand-built rows that
    carry nothing but a relative path — it is the one piece of the copy path
    that can be exercised without a catalog behind it, and keeping it that way
    is worth a three-line helper. A sqlite3.Row raises IndexError for a missing
    column while a dict raises KeyError; both mean the same thing here.
    """
    try:
        return row["id"]
    except (KeyError, IndexError):
        return None


def _target_collision(files, dests, day, card, plan=None):
    """Refuse to start if two source files would land on one destination path.

    The roll-folder strip in dest_path_for() means a card holding both
    ``CRACK_R01/take1.wav`` and a loose ``take1.wav`` maps both to
    ``SOUND/CRACK_R01/take1.wav``. Verification would eventually catch the
    overwrite as a checksum mismatch, but it would read like a failing drive.
    Catch it up front and name both files instead.

    Renaming makes this far more likely — {scene}_{take} across two cameras
    collides immediately — so the check runs on the resolved names. It refuses
    rather than adding a silent _2: two takes nobody can tell apart six weeks
    later is worse than an offload that would not start.
    """
    if not dests:
        return None
    plan = plan or {}
    seen = {}
    for file_row in files:
        entry = plan.get(_row_id(file_row))
        relative = entry["dest_rel_path"] if entry else file_row["rel_path"]
        try:
            target = dest_path_for(dests[0]["path"], day["project"], day["name"],
                                   card, relative)
        except ValueError as exc:
            return f"{file_row['rel_path']}: {exc}"
        key = os.path.normcase(target)
        if key in seen:
            renamed = entry and entry["renamed"]
            return (f"{file_row['rel_path']} and {seen[key]} would both be written "
                    f"to {os.path.basename(target)} on every destination. "
                    + ("Change the naming pattern so it tells them apart."
                       if renamed else
                       "Rename one on a copy of the card, or offload them as "
                       "separate rolls."))
        seen[key] = file_row["rel_path"]
    return None


def _pattern_changed(files, dests, plan):
    """Refuse to resume a card under a different pattern than it started with.

    Half a card named one way and half the other is not a resumable state: the
    already-verified copies keep their old paths, the new ones land beside
    them, and the gate counts both as present. Whoever changed the pattern
    almost certainly did not mean to do it to a card that is already half
    copied.
    """
    for file_row in files:
        entry = plan.get(_row_id(file_row))
        if not entry:
            continue
        for dest in dests:
            row = db.q1("SELECT dest_path FROM copies WHERE file_id=? AND "
                        "destination_id=? AND state IN ('copied','verified')",
                        (file_row["id"], dest["id"]))
            if not row or not row["dest_path"]:
                continue
            if os.path.basename(row["dest_path"]) != entry["dest_name"]:
                return (f"{file_row['rel_path']} was already copied as "
                        f"{os.path.basename(row['dest_path'])}, but the naming "
                        f"pattern now makes it {entry['dest_name']}. Put the "
                        f"pattern back, or start this card again on a clean "
                        f"destination folder.")
    return None


def preflight(card_id, mode, min_copies):
    """Perform operator-facing checks before a worker thread starts."""
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return "Unknown card"
    day = db.q1("SELECT * FROM days WHERE id=?", (card["day_id"],))
    dests = db.q("SELECT * FROM destinations WHERE day_id=? ORDER BY id", (day["id"],))

    if mode == "offload":
        problem = _card_source_problem(card)
        if problem:
            return problem

    checked, errors = storage.validate_destination_set(
        card["source_path"] if mode == "offload" else None,
        dests, min_copies, require_writable=(mode == "offload"),
    )
    if errors:
        return errors[0]

    # Upgrade older destination rows once the mount has been positively
    # identified, then require that identity on future runs.
    for dest, current in checked:
        db.ex("""UPDATE destinations
                 SET path=?, canonical_path=?, device_id=?, volume_uuid=? WHERE id=?""",
              (current["path"], current["path"], current["device_id"],
               current["volume_uuid"], dest["id"]))

    if mode == "offload":
        problem = naming.validate(pattern_for(day, card["kind"]))
        if problem:
            return problem
        # Decide what is being skipped before anything else looks at the card,
        # so every check below sees the same set of files the copy will.
        apply_filters(card, day)
        files = active_files(card_id)
        if not files:
            return ("Every file on this card matches the exclude filter. "
                    "Nothing would be copied.")
        plan = name_plan(card, day)
        collision = _target_collision(files, dests, day, card, plan)
        if collision:
            return collision
        changed = _pattern_changed(files, dests, plan)
        if changed:
            return changed
        algo_problem = _algo_changed(files, dests, algo_for(day))
        if algo_problem:
            return algo_problem
        for dest, current in checked:
            required = 0
            for file_row in files:
                copy = db.q1(
                    "SELECT state,dest_path FROM copies WHERE file_id=? AND destination_id=?",
                    (file_row["id"], dest["id"]),
                )
                if copy and copy["state"] in ("copied", "verified"):
                    path = copy["dest_path"]
                    if os.path.exists(path) and os.path.getsize(path) == file_row["size"]:
                        continue
                required += file_row["size"]
            reserve = max(64 * 1024 * 1024, int(required * 0.02)) if required else 0
            if current["free_bytes"] < required + reserve:
                return (
                    f"{dest['label']} needs {required + reserve:,} free bytes but has "
                    f"{current['free_bytes']:,}"
                )
    return None


class Job:
    def __init__(self, card_id, mode="offload", min_copies=2):
        self.card_id = card_id
        self.mode = mode          # offload | verify
        # Never let a caller weaken the two-copy floor by passing a small
        # int through — reports.py enforces its own floor independently for
        # the format gate, but this keeps the job layer honest too.
        self.min_copies = max(2, int(min_copies))
        self.state = "queued"
        self.message = ""
        self.bytes_total = 0
        self.bytes_done = 0
        self.files_total = 0
        self.files_done = 0
        self.current_file = ""
        self.phase = ""
        self.errors = []
        self.warnings = []
        self.plan = {}
        self.algo = hashing.DEFAULT
        self.cancel = threading.Event()
        # bytes_done is now incremented from several read-back threads at
        # once, and `+=` is a read-modify-write. Without this the progress bar
        # loses ticks and appears to stall on a job that is running fine.
        self._progress_lock = threading.Lock()
        # Reused for the life of the job rather than one pool per file: each
        # worker thread gets its own SQLite connection from db.conn(), and
        # opening one of those per file per drive would cost more than the
        # parallelism wins back.
        self._readers = None
        self.job_row = None
        self.started_epoch = None
        self.finished_epoch = None

    def snapshot(self):
        if self.bytes_total:
            pct = min(100, self.bytes_done / self.bytes_total * 100)
        else:
            pct = 100 if self.state == "done" else 0
        return {
            "card_id": self.card_id, "state": self.state, "phase": self.phase,
            "message": self.message, "current_file": self.current_file,
            "bytes_total": self.bytes_total, "bytes_done": self.bytes_done,
            "files_total": self.files_total, "files_done": self.files_done,
            "percent": round(pct, 1), "mode": self.mode, "job_type": "offload",
            "started_epoch": self.started_epoch, "finished_epoch": self.finished_epoch,
            "errors": self.errors[-20:], "warnings": self.warnings[-20:],
        }

    # -- progress helpers -------------------------------------------------
    def _tick(self, n):
        with self._progress_lock:
            self.bytes_done += n

    def _close_readers(self):
        """Let the read-back threads go, and their catalog connections with
        them. Safe to call twice; called from the normal end of a job and
        again from run()'s finally, so a cancelled or failed job does not
        leave threads behind."""
        pool, self._readers = self._readers, None
        if pool is not None:
            pool.shutdown(wait=True)

    def run(self):
        self.started_epoch = time.time()
        try:
            self.state = "running"
            self._run()
            self.state = "failed" if self.errors else "done"
            if self.errors:
                self.message = f"{len(self.errors)} problem(s) — card must not be formatted"
            elif self.warnings:
                self.message = f"All files verified with {len(self.warnings)} warning(s)"
            else:
                self.message = "All files verified"
        except JobCancelled:
            self.state = "cancelled"
            self.message = "Cancelled safely — source card must not be formatted"
            db.ex("UPDATE cards SET status='cancelled' WHERE id=?", (self.card_id,))
        except Exception:
            self.state = "failed"
            self.message = "Job crashed"
            self.errors.append(traceback.format_exc(limit=3))
            db.ex("UPDATE cards SET status='failed' WHERE id=?", (self.card_id,))
        finally:
            # A cancelled or crashed job leaves the pool up otherwise, and its
            # threads hold open catalog connections.
            self._close_readers()
            self.finished_epoch = time.time()
            if self.job_row:
                db.ex("UPDATE jobs SET state=?, message=?, finished_at=? WHERE id=?",
                      (self.state, self.message, db.now(), self.job_row))
            # Copy state just changed, so any cached gate answer is stale.
            import reports
            reports.invalidate_gate_cache(self.card_id)
            global _current, _last_snapshot
            with _lock:
                _last_snapshot = self.snapshot()
                if _current is self:
                    _current = None

    def _run(self):
        card = db.q1("SELECT * FROM cards WHERE id=?", (self.card_id,))
        day = db.q1("SELECT * FROM days WHERE id=?", (card["day_id"],))
        dests = db.q("SELECT * FROM destinations WHERE day_id=? ORDER BY id", (day["id"],))
        if not dests:
            raise RuntimeError("No destinations configured for this day")

        _, destination_errors = storage.validate_destination_set(
            card["source_path"] if self.mode == "offload" else None,
            dests, self.min_copies, require_writable=(self.mode == "offload"),
        )
        if destination_errors:
            raise RuntimeError(destination_errors[0])

        self.job_row = db.ex(
            "INSERT INTO jobs (card_id, job_type, state, started_at) "
            "VALUES (?,'offload',?,?)",
            (self.card_id, "running", db.now()))

        # Only an offload decides what to skip. A verify run is re-reading what
        # is already on the drives; letting it move exclusion flags would mean
        # "Re-read drives" could quietly change what a finished card claims.
        if self.mode == "offload":
            apply_filters(card, day)
        files = active_files(self.card_id)
        if not files:
            raise RuntimeError("Every file on this card matches the exclude "
                               "filter. Nothing would be copied.")
        self.algo = algo_for(day)
        algo_problem = _algo_changed(files, dests, self.algo)
        if algo_problem:
            raise RuntimeError(algo_problem)
        # One plan for the whole card, computed once. Every destination path
        # below comes from it, so a resumed job cannot land somewhere else.
        plan = name_plan(card, day)
        collision = _target_collision(files, dests, day, card, plan)
        if collision:
            raise RuntimeError(collision)
        changed = _pattern_changed(files, dests, plan)
        if changed:
            raise RuntimeError(changed)
        self.plan = plan
        # Work out what is genuinely left to do, so a resumed job doesn't
        # re-copy 400 GB it already verified.
        todo = []
        for f in files:
            per_dest = []
            for d in dests:
                row = db.q1("SELECT * FROM copies WHERE file_id=? AND destination_id=?",
                            (f["id"], d["id"]))
                # A verify run deliberately re-reads copies that already say
                # 'verified'. That is the whole point of it: drives rot, and
                # trusting yesterday's database entry is how you find out too
                # late. An offload run skips them so it can resume cheaply.
                if row and row["state"] == "verified" and self.mode != "verify":
                    continue
                per_dest.append((d, row))
            if per_dest:
                todo.append((f, per_dest))
        self.files_total = len(todo)

        # Copy phase reads the source once and writes N times; verify phase
        # reads each destination back once.
        if self.mode == "verify":
            self.bytes_total = sum(f["size"] * len(pd) for f, pd in todo)
        else:
            self.bytes_total = 0
            for f, per_dest in todo:
                copies_needed = 0
                for d, row in per_dest:
                    path = row["dest_path"] if row else dest_path_for(
                        d["path"], day["project"], day["name"], card,
                        plan[f["id"]]["dest_rel_path"]
                    )
                    if (not os.path.exists(path) or os.path.getsize(path) != f["size"]
                            or not (row and row["state"] == "copied")):
                        copies_needed += 1
                source_reads = 1 if copies_needed or not f["src_hash"] else 0
                self.bytes_total += f["size"] * (
                    source_reads + copies_needed + len(per_dest)
                )

        db.ex("UPDATE cards SET status=? WHERE id=?",
              ("verifying" if self.mode == "verify" else "offloading", self.card_id))

        for f, per_dest in todo:
            if self.cancel.is_set():
                raise JobCancelled()
            self.current_file = f["rel_path"]
            src = os.path.join(card["source_path"], f["rel_path"])
            if self.mode == "offload":
                try:
                    source_size = os.path.getsize(src)
                except OSError as exc:
                    self.errors.append(f"{f['rel_path']}: source cannot be read — {exc}")
                    continue
                if source_size != f["size"]:
                    self.errors.append(
                        f"{f['rel_path']}: source changed size after registration "
                        f"({source_size} vs {f['size']})"
                    )
                    continue
            targets = []
            for d, row in per_dest:
                dp = row["dest_path"] if row else dest_path_for(
                    d["path"], day["project"], day["name"], card,
                    self.plan[f["id"]]["dest_rel_path"])
                targets.append((d, dp))

            if self.mode == "verify":
                needs_copy = []
            else:
                needs_copy = []
                for (d, dp), (_, row) in zip(targets, per_dest):
                    if not os.path.exists(dp):
                        needs_copy.append((d, dp))
                    elif os.path.getsize(dp) != f["size"]:
                        # Wrong size on a copy we previously wrote means the
                        # destination drive misbehaved. Heal it, but say so —
                        # a drive that does this twice should leave the set.
                        if row and row["state"] in ("copied", "verified"):
                            self.warnings.append(
                                f"{f['rel_path']} @ {d['label']}: wrong size on disk "
                                f"({os.path.getsize(dp)} vs {f['size']}) — recopied")
                        needs_copy.append((d, dp))
                    elif not (row and row["state"] == "copied"):
                        needs_copy.append((d, dp))

            self.phase = "Re-reading" if self.mode == "verify" else "Copying"
            src_hash = f["src_hash"]
            if self.mode == "verify" and not src_hash:
                self.errors.append(f"{f['rel_path']}: never offloaded, nothing to verify")
                continue
            if needs_copy or not src_hash:
                src_hash = self._copy(src, f, needs_copy, day, card)
                if src_hash is None:
                    continue
                db.ex("UPDATE files SET src_hash=?, hash_algo=? WHERE id=?",
                      (src_hash, self.algo, f["id"]))

            self.phase = "Verifying"
            if self.cancel.is_set():
                raise JobCancelled()
            self._verify_all(f, targets, src_hash)

            self.files_done += 1

        self._close_readers()
        self.phase = "Finished"
        self.current_file = ""
        if self.mode == "offload" and not self.errors:
            self.phase = "Reading metadata"
            metadata = media.analyze_card(self.card_id)
            if metadata["errors"]:
                self.warnings.append(
                    f"Metadata: {len(metadata['errors'])} warning(s)"
                )
                self.warnings.extend(
                    f"Metadata: {message}" for message in metadata["errors"][:5]
                )
            if (metadata["sound_rows"]
                    and metadata["sound_rows_matched"] < metadata["sound_rows"]):
                self.warnings.append(
                    "Sound report: "
                    f"{metadata['sound_rows'] - metadata['sound_rows_matched']} row(s) "
                    "did not match an audio filename"
                )
        if self.mode == "offload":
            source_problem = _card_source_problem(card)
            if source_problem:
                self.errors.append(source_problem)
        self.phase = "Finished"
        db.ex("UPDATE cards SET status=? WHERE id=?",
              ("verified" if not self.errors else "failed", self.card_id))

    # -- the two primitives ----------------------------------------------
    def _copy(self, src, f, targets, day, card):
        """Single read of the source, fanned out to every destination, hashing
        as we go. Writes go to a .partial file that is only renamed into place
        once the whole file is down and fsynced."""
        h = hashing.new(self.algo)
        handles = []
        try:
            for d, dp in targets:
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                handles.append((d, dp, open(dp + ".partial", "wb")))
            with _open_uncached(src, require_uncached=(sys.platform == "darwin")) as sf:
                while True:
                    if self.cancel.is_set():
                        raise JobCancelled()
                    chunk = sf.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    for _, _, out in handles:
                        out.write(chunk)
                    self._tick(len(chunk))
                    for _ in handles:
                        self._tick(len(chunk))
        except JobCancelled:
            for _, dp, out in handles:
                out.close()
                if os.path.exists(dp + ".partial"):
                    os.remove(dp + ".partial")
            raise
        except Exception as e:
            for _, dp, out in handles:
                out.close()
                if os.path.exists(dp + ".partial"):
                    os.remove(dp + ".partial")
            self.errors.append(f"{f['rel_path']}: copy failed — {e}")
            return None

        for d, dp, out in handles:
            try:
                out.flush()
                if sys.platform == "darwin":
                    try:
                        fcntl.fcntl(out.fileno(), F_FULLFSYNC)
                    except OSError:
                        os.fsync(out.fileno())
                else:
                    os.fsync(out.fileno())
                out.close()
                os.replace(dp + ".partial", dp)
                shutil.copystat(src, dp, follow_symlinks=False)
            except Exception as exc:
                # The write itself never completed cleanly — nothing usable
                # was left behind, so the .partial cleanup below is correct.
                if not out.closed:
                    out.close()
                if os.path.exists(dp + ".partial"):
                    os.remove(dp + ".partial")
                self.errors.append(f"{f['rel_path']} @ {d['label']}: finalize failed — {exc}")
                continue
            try:
                db.ex("""INSERT INTO copies
                         (file_id, destination_id, dest_path, state, updated_at)
                         VALUES (?,?,?,'copied',?)
                         ON CONFLICT(file_id, destination_id)
                         DO UPDATE SET state='copied', dest_path=excluded.dest_path,
                                       error=NULL, updated_at=excluded.updated_at""",
                      (f["id"], d["id"], dp, db.now()))
            except Exception as exc:
                # The file itself is fine and already on disk at dp — only the
                # catalog write failed. Do not delete a good copy here. The
                # _verify() call that runs immediately after this method
                # returns will read the file straight off disk and INSERT its
                # own 'copies' row regardless of whether this one landed, so
                # the catalog self-heals within the same job run.
                self.warnings.append(
                    f"{f['rel_path']} @ {d['label']}: catalog write failed after a "
                    f"successful copy ({exc}) — will be reconciled during verification"
                )
        return h.hexdigest()

    def _verify_all(self, f, targets, src_hash):
        """Read every destination back at the same time, not one after another.

        This program refuses to start unless the destinations are physically
        distinct drives — that is rule three of the safety contract. So reading
        them simultaneously is not two jobs fighting over one spindle; it is
        one job finally using the hardware the rule already required. The
        read-back is the slowest part of an offload and it was queueing behind
        itself for no reason.

        **Nothing about what is checked changes.** Each destination is still
        read byte for byte with the page cache bypassed, still hashed with the
        day's algorithm, still compared against the digest taken off the card,
        and still writes its own catalog row. `_verify` is called unmodified;
        this only decides when.

        Two threads and one drive would be a real slowdown, so a single
        destination stays on this thread.

        Cancellation reaches inside the read: every worker checks the job's
        cancel flag between chunks, so pressing stop during a 100 GB file
        stops during that file rather than after it. A cancelled read-back is
        never recorded as a failed copy — the drive did nothing wrong — and a
        verify writes no catalog row until it has an answer, so the catalog is
        in the same state whether the job finished or was stopped.
        """
        if len(targets) < 2:
            return [self._verify(f, d, dp, src_hash) for d, dp in targets]
        if self._readers is None:
            self._readers = concurrent.futures.ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="shs-verify")
        futures = [self._readers.submit(self._verify, f, d, dp, src_hash)
                   for d, dp in targets]
        # Wait for every one of them. A destination that raises something
        # _verify did not catch must not be silently dropped: it becomes an
        # error on the job, which closes the format gate, which is the
        # conservative answer.
        results, cancelled = [], False
        for future in futures:
            try:
                results.append(future.result())
            except JobCancelled:
                # Wait for the others rather than returning while they are
                # still reading, then raise once. They are cancelling too.
                cancelled = True
                results.append(False)
            except Exception as exc:
                self.errors.append(f"{f['rel_path']}: read-back failed — {exc}")
                results.append(False)
        if cancelled:
            raise JobCancelled()
        return results

    def _verify(self, f, dest, dest_path, src_hash):
        try:
            if not os.path.exists(dest_path):
                raise FileNotFoundError("file missing at destination")
            size = os.path.getsize(dest_path)
            if size != f["size"]:
                raise ValueError(f"size mismatch: {size} on disk vs {f['size']} on card")
            got, _ = hash_file(
                dest_path, progress=self._tick,
                require_uncached=(sys.platform == "darwin"), algo=self.algo,
                should_stop=self.cancel.is_set,
            )
            if got != src_hash:
                raise ValueError(f"checksum mismatch: {got} vs {src_hash}")
        except JobCancelled:
            # A cancelled read-back is not a bad copy. Recording it as failed
            # would leave the catalog claiming a drive misbehaved because
            # somebody pressed stop, and that lie would outlive the job.
            raise
        except Exception as e:
            self.errors.append(f"{f['rel_path']} @ {dest['label']}: {e}")
            db.ex("""INSERT INTO copies (file_id, destination_id, dest_path, state, error, updated_at)
                     VALUES (?,?,?,'failed',?,?)
                     ON CONFLICT(file_id, destination_id)
                     DO UPDATE SET state='failed', error=excluded.error,
                                   updated_at=excluded.updated_at""",
                  (f["id"], dest["id"], dest_path, str(e), db.now()))
            return False
        # Upsert, not UPDATE. If the catalog write in _copy() failed, no row
        # exists yet -- an UPDATE would match nothing, report success and
        # leave the verified copy unrecorded. This is the statement _copy()'s
        # recovery path depends on, so it has to be able to create the row.
        db.ex("""INSERT INTO copies
                 (file_id, destination_id, dest_path, state, read_back_hash,
                  error, updated_at)
                 VALUES (?,?,?,'verified',?,NULL,?)
                 ON CONFLICT(file_id, destination_id)
                 DO UPDATE SET state='verified', dest_path=excluded.dest_path,
                               read_back_hash=excluded.read_back_hash,
                               error=NULL, updated_at=excluded.updated_at""",
              (f["id"], dest["id"], dest_path, src_hash, db.now()))
        return True


def start(card_id, mode="offload", min_copies=2):
    global _current, _last_snapshot
    with _lock:
        if _current is not None:
            return None, "Another job is already running"
        job = Job(card_id, mode=mode, min_copies=min_copies)
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
        _current.cancel.set()

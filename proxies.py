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

"""Resumable ProRes Proxy generation from verified primary-drive masters.

Proxy work is deliberately downstream of ingest safety. It never reads the
camera card, never changes a copy record and never controls SAFE TO FORMAT.
Each output is written as a partial QuickTime, inspected, flushed to disk and
only then renamed into its final deterministic path.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter

import xxhash

import db
import media
import storage


FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
PROFILE = "prores_proxy_1080p_v1"
PROFILE_LABEL = "ProRes Proxy 1080p"
ESTIMATED_BYTES_PER_SECOND = 6_000_000
F_FULLFSYNC = 51  # Darwin only

_lock = threading.Lock()
_current = None
_last_snapshot = None
_capability = None


class ProxyCancelled(Exception):
    pass


def capability(recheck=False):
    """Return whether this machine has the encoder and probe tools we need.

    A positive answer is cached for the life of the process. A negative one is
    re-probed on request, so installing ffmpeg during a shoot takes effect at
    the next screen refresh rather than needing a restart.
    """
    global _capability, FFMPEG
    if _capability is not None and (_capability[0] or not recheck):
        return _capability
    FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
    if not media.FFPROBE:
        media.refresh_tools()
    if not FFMPEG:
        _capability = (False, "ffmpeg is not installed")
        return _capability
    if not media.FFPROBE:
        _capability = (False, "ffprobe is not installed")
        return _capability
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _capability = (False, f"ffmpeg could not start: {exc}")
    else:
        present = result.returncode == 0 and "prores_ks" in result.stdout
        _capability = (present, "" if present else "ProRes encoder is unavailable")
    return _capability


def _primary(card_id):
    return db.q1(
        """SELECT d.* FROM destinations d JOIN cards c ON c.day_id=d.day_id
           WHERE c.id=? AND d.role='primary' ORDER BY d.id LIMIT 1""",
        (card_id,),
    )


def _video_rows(card_id, destination_id):
    return db.q(
        """SELECT f.id file_id, f.rel_path, f.size source_size, f.src_hash,
                  m.duration_seconds, m.start_timecode, m.frame_rate,
                  m.reel_name source_reel, m.status metadata_status,
                  cp.state copy_state, cp.dest_path input_path,
                  p.state proxy_state, p.profile proxy_profile,
                  p.source_hash proxy_source_hash, p.output_path existing_output,
                  p.output_size existing_size, p.output_hash existing_hash
           FROM files f
           LEFT JOIN media_metadata m ON m.file_id=f.id
           LEFT JOIN copies cp ON cp.file_id=f.id AND cp.destination_id=?
           LEFT JOIN proxies p ON p.file_id=f.id
           WHERE f.card_id=? AND f.category='video'
           ORDER BY f.rel_path""",
        (destination_id, card_id),
    )


def output_path_for(destination_root, project, day_name, roll, rel_path,
                    disambiguator=None):
    stem = os.path.splitext(os.path.basename(rel_path))[0]
    output_name = f"{stem}_{disambiguator}_PXY.mov" if disambiguator \
        else f"{stem}_PXY.mov"
    root = storage.canonical(destination_root)
    target = os.path.abspath(os.path.join(
        root, project, day_name, "PROXY", roll, output_name
    ))
    if os.path.commonpath((root, target)) != root:
        raise ValueError("Proxy path would escape the primary destination")
    return target


def preflight(card_id, rebuild=False):
    ok, reason = capability()
    if not ok:
        return reason
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return "Unknown card"
    if card["kind"] not in ("camera", "mixed"):
        return "This card has no camera media to proxy"

    # Local import avoids a reports -> proxies cycle in the day report.
    import reports
    gate = reports.card_status(card_id)
    if not gate["safe"]:
        return f"Proxies wait for verified originals: {gate['reason']}"

    primary = _primary(card_id)
    if not primary:
        return "Mark one destination primary before building proxies"
    try:
        current = storage.inspect_path(primary["path"])
    except storage.StorageError as exc:
        return str(exc)
    if primary["device_id"] and current["device_id"] != primary["device_id"]:
        return f"{primary['label']} is not the same physical drive registered for this day"
    if not os.access(current["path"], os.R_OK | os.W_OK):
        return f"No read/write access to {current['path']}"

    rows = _video_rows(card_id, primary["id"])
    if not rows:
        return "No camera clips were catalogued on this card"
    for row in rows:
        if row["copy_state"] != "verified" or not row["input_path"]:
            return f"Primary copy is not verified for {row['rel_path']}"
        if not os.path.isfile(row["input_path"]):
            return f"Verified primary file is missing: {row['rel_path']}"
        if os.path.getsize(row["input_path"]) != row["source_size"]:
            return f"Verified primary file changed size: {row['rel_path']}"
        if not row["duration_seconds"] or row["duration_seconds"] <= 0:
            return f"Camera duration is missing for {row['rel_path']}; read metadata first"

    pending = rows if rebuild else [row for row in rows if not (
        row["proxy_state"] == "ready" and row["proxy_profile"] == PROFILE
        and row["proxy_source_hash"] == row["src_hash"]
        and row["existing_output"] and os.path.isfile(row["existing_output"])
        and os.path.getsize(row["existing_output"]) == row["existing_size"]
    )]
    estimates = [max(1_000_000, int(row["duration_seconds"]
                                    * ESTIMATED_BYTES_PER_SECOND)) for row in rows]
    estimated = sum(max(1_000_000, int(row["duration_seconds"]
                                      * ESTIMATED_BYTES_PER_SECOND)) for row in pending)
    if len(pending) < len(rows):
        # Keep room for one atomic rebuild if a completed output fails its
        # checksum during the worker's resume validation.
        estimated += max(estimates, default=0)
    reserve = max(256 * 1024 * 1024, int(estimated * 0.10)) if estimated else 0
    if current["free_bytes"] < estimated + reserve:
        return (f"{primary['label']} needs about {estimated + reserve:,} free bytes "
                f"for these proxies but has {current['free_bytes']:,}")
    return None


def _save(file_id, **values):
    db.ex("UPDATE dailies SET state='stale',updated_at=? WHERE camera_file_id=?",
          (db.now(), file_id))
    db.ex("""UPDATE handoff_exports SET state='stale',updated_at=? WHERE day_id IN
             (SELECT c.day_id FROM files f JOIN cards c ON c.id=f.card_id WHERE f.id=?)""",
          (db.now(), file_id))
    columns = (
        "state", "profile", "source_hash", "input_path", "output_path",
        "output_size", "output_hash", "duration_seconds", "start_timecode", "reel_name",
        "width", "height", "codec_name", "error",
    )
    record = {name: values.get(name) for name in columns}
    record["profile"] = record["profile"] or PROFILE
    names = ",".join(columns)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{name}=excluded.{name}" for name in columns)
    db.ex(
        f"""INSERT INTO proxies (file_id,{names},updated_at)
            VALUES (?,{placeholders},?)
            ON CONFLICT(file_id) DO UPDATE SET {updates},updated_at=excluded.updated_at""",
        (file_id, *(record[name] for name in columns), db.now()),
    )


def inspect_proxy(path):
    payload = media.probe_file(path)
    facts = media.parse_probe(payload)
    video = next((stream for stream in payload.get("streams", [])
                  if stream.get("codec_type") == "video"), {})
    return {
        "codec_name": video.get("codec_name"),
        "profile_name": video.get("profile"),
        "width": video.get("width"),
        "height": video.get("height"),
        "duration_seconds": facts.get("duration_seconds"),
        "start_timecode": facts.get("start_timecode"),
        "reel_name": facts.get("reel_name"),
    }


def _validated_facts(path, expected_duration, expected_timecode, expected_reel):
    facts = inspect_proxy(path)
    if facts["codec_name"] != "prores" or str(facts["profile_name"]).casefold() != "proxy":
        raise ValueError("output is not ProRes Proxy")
    if facts["width"] != 1920 or facts["height"] != 1080:
        raise ValueError(
            f"output raster is {facts['width']}x{facts['height']}, expected 1920x1080"
        )
    if expected_duration is not None and facts["duration_seconds"] is not None:
        if abs(float(facts["duration_seconds"]) - float(expected_duration)) > 0.1:
            raise ValueError("output duration does not match the camera master")
    if expected_timecode and facts["start_timecode"] != expected_timecode:
        raise ValueError(
            f"output timecode is {facts['start_timecode'] or 'missing'}, "
            f"expected {expected_timecode}"
        )
    if expected_reel and facts["reel_name"] != expected_reel:
        raise ValueError(
            f"output reel is {facts['reel_name'] or 'missing'}, expected {expected_reel}"
        )
    return facts


def transcode_file(input_path, partial_path, timecode, reel, duration,
                   progress, cancel_event, process_started=None):
    """Encode one file and report processed seconds through ``progress``."""
    command = [
        FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", input_path,
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", ("scale=w=1920:h=1080:force_original_aspect_ratio=decrease:"
                "force_divisible_by=2,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1"),
        "-c:v", "prores_ks", "-profile:v", "proxy", "-pix_fmt", "yuv422p10le",
        "-vendor", "apl0", "-c:a", "pcm_s24le", "-ar", "48000",
        "-map_metadata", "0", "-metadata:s:v:0", f"reel_name={reel}",
        "-timecode", timecode, "-movflags", "+write_colr",
        "-stats_period", "0.5", "-progress", "pipe:1", partial_path,
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    if process_started:
        process_started(process)
    output = []
    try:
        for raw in process.stdout:
            line = raw.strip()
            if cancel_event.is_set():
                process.terminate()
            if line.startswith("out_time_us="):
                try:
                    progress(max(0, int(line.split("=", 1)[1])) / 1_000_000)
                except ValueError:
                    pass
            elif line and not any(line.startswith(prefix) for prefix in (
                    "frame=", "fps=", "stream_", "bitrate=", "total_size=",
                    "out_time_ms=", "out_time=", "dup_frames=", "drop_frames=",
                    "speed=", "progress=")):
                output.append(line)
        returncode = process.wait()
    finally:
        if process_started:
            process_started(None)
    if cancel_event.is_set():
        raise ProxyCancelled()
    if returncode:
        raise RuntimeError(("\n".join(output[-12:]) or
                            f"ffmpeg exited with status {returncode}")[-2000:])
    if duration is not None:
        progress(float(duration))


def _flush_file(path):
    with open(path, "r+b") as handle:
        handle.flush()
        if sys.platform == "darwin":
            try:
                fcntl.fcntl(handle.fileno(), F_FULLFSYNC)
            except OSError:
                os.fsync(handle.fileno())
        else:
            os.fsync(handle.fileno())


def _hash_file(path):
    digest = xxhash.xxh64()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class Job:
    def __init__(self, card_id, rebuild=False):
        self.card_id = card_id
        self.rebuild = rebuild
        self.state = "queued"
        self.phase = "Queued"
        self.message = ""
        self.current_file = ""
        self.files_total = 0
        self.files_done = 0
        self.seconds_total = 0.0
        self.seconds_done = 0.0
        self.output_bytes = 0
        self.errors = []
        self.warnings = []
        self.cancel = threading.Event()
        self.process = None
        self.job_row = None
        self.started_epoch = None
        self.finished_epoch = None

    def snapshot(self):
        if self.seconds_total:
            percent = min(100, self.seconds_done / self.seconds_total * 100)
        elif self.files_total:
            percent = min(100, self.files_done / self.files_total * 100)
        else:
            percent = 100 if self.state == "done" else 0
        return {
            "job_type": "proxy", "mode": "proxy", "card_id": self.card_id,
            "state": self.state, "phase": self.phase, "message": self.message,
            "current_file": self.current_file, "files_total": self.files_total,
            "files_done": self.files_done, "seconds_total": self.seconds_total,
            "seconds_done": self.seconds_done, "output_bytes": self.output_bytes,
            "bytes_total": 0, "bytes_done": self.output_bytes,
            "percent": round(percent, 1), "errors": self.errors[-20:],
            "warnings": self.warnings[-20:], "started_epoch": self.started_epoch,
            "finished_epoch": self.finished_epoch,
        }

    def request_cancel(self):
        self.cancel.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def _set_process(self, process):
        self.process = process

    def run(self):
        self.started_epoch = time.time()
        try:
            self.state = "running"
            self._run()
            self.state = "failed" if self.errors else "done"
            if self.errors:
                self.message = (f"{len(self.errors)} proxy problem(s) — "
                                "verified originals are unaffected")
            elif self.warnings:
                self.message = f"Proxies ready with {len(self.warnings)} warning(s)"
            else:
                self.message = "All proxies ready"
        except ProxyCancelled:
            self.state = "cancelled"
            self.message = "Proxy generation stopped safely — originals are unaffected"
        except Exception:
            self.state = "failed"
            self.message = "Proxy job crashed — originals are unaffected"
            self.errors.append(traceback.format_exc(limit=4))
        finally:
            self.finished_epoch = time.time()
            if self.job_row:
                db.ex("UPDATE jobs SET state=?, message=?, finished_at=? WHERE id=?",
                      (self.state, self.message, db.now(), self.job_row))
            global _current, _last_snapshot
            with _lock:
                _last_snapshot = self.snapshot()
                if _current is self:
                    _current = None

    def _run(self):
        card = db.q1("SELECT * FROM cards WHERE id=?", (self.card_id,))
        day = db.q1("SELECT * FROM days WHERE id=?", (card["day_id"],))
        primary = _primary(self.card_id)
        if not card or not primary:
            raise RuntimeError("Mark one destination primary before building proxies")
        rows = _video_rows(self.card_id, primary["id"])
        stem_counts = Counter(os.path.splitext(os.path.basename(row["rel_path"]))[0].casefold()
                              for row in rows)
        self.files_total = len(rows)
        self.seconds_total = sum(float(row["duration_seconds"] or 0) for row in rows)
        self.job_row = db.ex(
            "INSERT INTO jobs (card_id,job_type,state,started_at) VALUES (?,'proxy','running',?)",
            (self.card_id, db.now()),
        )

        for row in rows:
            if self.cancel.is_set():
                raise ProxyCancelled()
            duration = float(row["duration_seconds"] or 0)
            base_seconds = self.seconds_done
            self.current_file = os.path.basename(row["rel_path"])
            stem = os.path.splitext(os.path.basename(row["rel_path"]))[0].casefold()
            disambiguator = (hashlib.sha1(row["rel_path"].encode("utf-8")).hexdigest()[:8]
                             if stem_counts[stem] > 1 else None)
            output = output_path_for(
                primary["path"], day["project"], day["name"],
                card["roll"] or card["label"], row["rel_path"], disambiguator,
            )
            reel = card["roll"] or card["label"]
            prior = db.q1("SELECT * FROM proxies WHERE file_id=?", (row["file_id"],))

            if not self.rebuild and prior and prior["state"] == "ready" \
                    and prior["profile"] == PROFILE \
                    and prior["source_hash"] == row["src_hash"] \
                    and prior["output_path"] == output \
                    and os.path.isfile(output) \
                    and os.path.getsize(output) == prior["output_size"] \
                    and prior["output_hash"]:
                try:
                    if _hash_file(output) != prior["output_hash"]:
                        raise ValueError("proxy checksum changed")
                    _validated_facts(output, duration, row["start_timecode"], reel)
                except Exception as exc:
                    self.warnings.append(
                        f"{self.current_file}: existing proxy failed validation ({exc}); rebuilt"
                    )
                else:
                    self.output_bytes += prior["output_size"] or 0
                    self.seconds_done = base_seconds + duration
                    self.files_done += 1
                    continue

            if row["copy_state"] != "verified" or not row["input_path"]:
                error = "primary master is no longer verified"
                self.errors.append(f"{self.current_file}: {error}")
                _save(row["file_id"], state="failed", source_hash=row["src_hash"],
                      input_path=row["input_path"], output_path=output, error=error)
                self.seconds_done = base_seconds + duration
                self.files_done += 1
                continue
            if not row["start_timecode"]:
                error = "camera start timecode is missing; read metadata and try again"
                self.errors.append(f"{self.current_file}: {error}")
                _save(row["file_id"], state="failed", source_hash=row["src_hash"],
                      input_path=row["input_path"], output_path=output, error=error)
                self.seconds_done = base_seconds + duration
                self.files_done += 1
                continue

            partial = output[:-4] + ".partial.mov"
            os.makedirs(os.path.dirname(output), exist_ok=True)
            if os.path.exists(partial):
                os.remove(partial)
            _save(row["file_id"], state="running", source_hash=row["src_hash"],
                  input_path=row["input_path"], output_path=output,
                  duration_seconds=duration, start_timecode=row["start_timecode"],
                  reel_name=reel)
            self.phase = "Encoding ProRes Proxy"
            try:
                transcode_file(
                    row["input_path"], partial, row["start_timecode"], reel, duration,
                    lambda seconds, base=base_seconds, length=duration: setattr(
                        self, "seconds_done", base + min(max(seconds, 0), length)
                    ),
                    self.cancel, self._set_process,
                )
                if self.cancel.is_set():
                    raise ProxyCancelled()
                self.phase = "Checking timecode and reel"
                facts = _validated_facts(
                    partial, duration, row["start_timecode"], reel
                )
                _flush_file(partial)
                output_hash = _hash_file(partial)
                os.replace(partial, output)
                size = os.path.getsize(output)
                _save(
                    row["file_id"], state="ready", source_hash=row["src_hash"],
                    input_path=row["input_path"], output_path=output, output_size=size,
                    output_hash=output_hash,
                    duration_seconds=facts["duration_seconds"],
                    start_timecode=facts["start_timecode"], reel_name=facts["reel_name"],
                    width=facts["width"], height=facts["height"],
                    codec_name=facts["codec_name"], error=None,
                )
                self.output_bytes += size
            except ProxyCancelled:
                if os.path.exists(partial):
                    os.remove(partial)
                _save(row["file_id"], state="cancelled", source_hash=row["src_hash"],
                      input_path=row["input_path"], output_path=output,
                      duration_seconds=duration, start_timecode=row["start_timecode"],
                      reel_name=reel, error="Stopped by operator")
                raise
            except Exception as exc:
                if os.path.exists(partial):
                    os.remove(partial)
                error = str(exc) or exc.__class__.__name__
                self.errors.append(f"{self.current_file}: {error}")
                _save(row["file_id"], state="failed", source_hash=row["src_hash"],
                      input_path=row["input_path"], output_path=output,
                      duration_seconds=duration, start_timecode=row["start_timecode"],
                      reel_name=reel, error=error)
            self.seconds_done = base_seconds + duration
            self.files_done += 1

        self.phase = "Finished"
        self.current_file = ""


def start(card_id, rebuild=False):
    global _current, _last_snapshot
    with _lock:
        if _current is not None:
            return None, "Another proxy job is already running"
        job = Job(card_id, rebuild=rebuild)
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


def card_summary(card_id):
    ok, reason = capability(recheck=True)
    row = db.q1(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN p.state='ready' AND p.profile=? THEN 1 ELSE 0 END) ready,
                  SUM(CASE WHEN p.state IN ('failed','cancelled') THEN 1 ELSE 0 END) problems,
                  SUM(CASE WHEN p.state='running' THEN 1 ELSE 0 END) running,
                  SUM(CASE WHEN p.state='ready' AND p.profile=? THEN p.output_size ELSE 0 END)
                      output_bytes,
                  MAX(p.updated_at) updated_at
           FROM files f LEFT JOIN proxies p ON p.file_id=f.id
           WHERE f.card_id=? AND f.category='video'""",
        (PROFILE, PROFILE, card_id),
    )
    return {
        "total": row["total"] or 0, "ready": row["ready"] or 0,
        "problems": row["problems"] or 0, "running": row["running"] or 0,
        "output_bytes": row["output_bytes"] or 0, "updated_at": row["updated_at"],
        "available": ok, "unavailable_reason": reason, "profile": PROFILE_LABEL,
    }

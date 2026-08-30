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

"""Conservative waveform-correlation fallback for camera/sound sync.

Only verified primary-drive masters are read. Audio is reduced by ffmpeg to a
small, band-limited mono fingerprint and aligned with normalized FFT cross-
correlation. The worker writes metadata relationships; media is never changed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from collections import OrderedDict

try:
    import numpy as np
except ImportError:  # The UI must still launch and explain what is unavailable.
    np = None

import db
import storage


FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
SAMPLE_RATE = 500
MAX_SAMPLES = 4_000_000             # A little over two hours per input.
AUTO_SCORE = 0.62
AUTO_MARGIN = 0.08
REVIEW_SCORE = 0.35
MIN_AUTO_OVERLAP_SECONDS = 3.0
TC_DISAGREEMENT_SECONDS = 0.10
MAX_RECORDED_CANDIDATES = 3
# ~24 fingerprints at the per-file ceiling, about 400 MB of float32.
CACHE_SAMPLE_BUDGET = 100_000_000

_lock = threading.Lock()
_current = None
_last_snapshot = None
_capability = None


class WaveformCancelled(Exception):
    pass


def capability(recheck=False):
    """Is waveform analysis possible on this machine?

    A positive answer is cached for the life of the process. A negative one is
    re-probed, so installing NumPy or ffmpeg during a shoot takes effect at
    the next screen refresh instead of requiring a restart.
    """
    global _capability, FFMPEG, np
    if _capability is not None and (_capability[0] or not recheck):
        return _capability
    if np is None:
        try:
            import numpy as _np
            np = _np
        except ImportError:
            pass
    FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
    if np is None:
        _capability = (False, "NumPy is not installed; run start.command while online once")
        return _capability
    if not FFMPEG:
        _capability = (False, "ffmpeg is not installed")
        return _capability
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-version"], capture_output=True,
            text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _capability = (False, f"ffmpeg could not start: {exc}")
    else:
        _capability = (result.returncode == 0,
                       "" if result.returncode == 0 else "ffmpeg is unavailable")
    return _capability


def _primary(day_id):
    return db.q1(
        """SELECT * FROM destinations WHERE day_id=? AND role='primary'
           ORDER BY id LIMIT 1""", (day_id,)
    )


def _camera_rows(day_id, destination_id):
    return db.q(
        """SELECT f.id file_id,f.rel_path,f.size source_size,f.src_hash,
                  c.id card_id,c.roll,m.duration_seconds,m.audio_channels,
                  cp.state copy_state,cp.dest_path input_path,
                  s.status sync_status,s.method sync_method,
                  w.camera_file_id waveform_done
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN media_metadata m ON m.file_id=f.id
           LEFT JOIN copies cp ON cp.file_id=f.id AND cp.destination_id=?
           LEFT JOIN sync_status s ON s.camera_file_id=f.id
           LEFT JOIN waveform_results w ON w.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'
             AND (s.status IN ('ambiguous','review','unmatched','invalid')
                  OR (s.status='matched' AND s.method='timecode'
                      AND w.camera_file_id IS NULL))
           ORDER BY c.roll,f.rel_path""", (destination_id, day_id)
    )


def _sound_rows(day_id, destination_id):
    return db.q(
        """SELECT f.id file_id,f.rel_path,f.size source_size,f.src_hash,
                  c.id card_id,c.roll card_roll,m.duration_seconds,
                  m.audio_channels,m.start_timecode,m.bwf_time_reference,
                  m.bwf_sample_rate,m.sample_rate,m.scene,m.take_name,m.sound_roll,
                  cp.state copy_state,cp.dest_path input_path
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN media_metadata m ON m.file_id=f.id
           LEFT JOIN copies cp ON cp.file_id=f.id AND cp.destination_id=?
           WHERE c.day_id=? AND f.category='audio'
             AND c.kind IN ('sound','mixed') AND cp.state='verified'
           ORDER BY c.roll,f.rel_path""", (destination_id, day_id)
    )


def _path_problem(row):
    if row["copy_state"] != "verified" or not row["input_path"]:
        return f"Primary copy is not verified for {row['rel_path']}"
    if not os.path.isfile(row["input_path"]):
        return f"Verified primary file is missing: {row['rel_path']}"
    try:
        size = os.path.getsize(row["input_path"])
    except OSError as exc:
        return f"Verified primary file cannot be read: {row['rel_path']} ({exc})"
    if size != row["source_size"]:
        return f"Verified primary file changed size: {row['rel_path']}"
    if not row["duration_seconds"] or row["duration_seconds"] <= 0:
        return f"Duration is missing for {row['rel_path']}; read metadata first"
    if not row["audio_channels"] or row["audio_channels"] <= 0:
        return f"No embedded audio was found in {row['rel_path']}"
    if float(row["duration_seconds"]) * SAMPLE_RATE > MAX_SAMPLES:
        return f"{row['rel_path']} is longer than the waveform-analysis limit"
    return None


def preflight(day_id):
    ok, reason = capability()
    if not ok:
        return reason
    day = db.q1("SELECT id FROM days WHERE id=?", (day_id,))
    if not day:
        return "Unknown shoot day"
    total = db.q1(
        """SELECT COUNT(*) total,COUNT(s.camera_file_id) analyzed
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN sync_status s ON s.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'""", (day_id,)
    )
    if not total["total"]:
        return "No camera clips are catalogued on this day"
    if total["analyzed"] != total["total"]:
        return "Run timecode sync before waveform fallback"
    primary = _primary(day_id)
    if not primary:
        return "Mark one destination primary before waveform fallback"
    try:
        current = storage.inspect_path(primary["path"])
    except storage.StorageError as exc:
        return str(exc)
    if primary["device_id"] and current["device_id"] != primary["device_id"]:
        return f"{primary['label']} is not the same physical drive registered for this day"
    if not os.access(current["path"], os.R_OK):
        return f"No read access to {current['path']}"
    cameras = _camera_rows(day_id, primary["id"])
    if not cameras:
        return "No clips need waveform fallback"
    for row in cameras:
        problem = _path_problem(row)
        if problem:
            return problem
    sounds = _sound_rows(day_id, primary["id"])
    usable_sounds = []
    for row in sounds:
        problem = _path_problem(row)
        if not problem:
            usable_sounds.append(row)
    if not usable_sounds:
        return "No verified sound with readable audio is available on the primary drive"
    return None


def extract_audio(path, cancel_event, process_started=None):
    """Decode a read-only, band-limited mono fingerprint to a NumPy array."""
    command = [
        FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path,
        "-t", str(MAX_SAMPLES / SAMPLE_RATE + 1),
        "-map", "0:a:0", "-vn", "-af", "highpass=f=50,lowpass=f=220",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process_started:
        process_started(process)
    try:
        payload, error = process.communicate()
    finally:
        if process_started:
            process_started(None)
    if cancel_event.is_set():
        raise WaveformCancelled()
    if process.returncode:
        message = error.decode("utf-8", "replace").strip()
        raise RuntimeError((message or f"ffmpeg exited with status {process.returncode}")[-2000:])
    signal = np.frombuffer(payload, dtype="<f4").copy()
    if signal.size > MAX_SAMPLES:
        raise ValueError("audio exceeds the waveform-analysis limit")
    return signal


def correlate(camera, sound, sample_rate=SAMPLE_RATE):
    """Return the strongest normalized match and its next independent peak.

    ``offset_seconds`` is sound-file start relative to camera-file start, the
    same convention used by Phase 4 sync links.
    """
    camera = np.asarray(camera, dtype=np.float32).reshape(-1)
    sound = np.asarray(sound, dtype=np.float32).reshape(-1)
    if camera.size < sample_rate or sound.size < sample_rate:
        raise ValueError("less than one second of audio is available")
    if camera.size > MAX_SAMPLES or sound.size > MAX_SAMPLES:
        raise ValueError("audio exceeds the waveform-analysis limit")
    if not np.isfinite(camera).all() or not np.isfinite(sound).all():
        raise ValueError("audio fingerprint contains invalid samples")
    camera = camera - np.mean(camera, dtype=np.float64)
    sound = sound - np.mean(sound, dtype=np.float64)
    camera_std = float(np.std(camera, dtype=np.float64))
    sound_std = float(np.std(sound, dtype=np.float64))
    if camera_std < 1e-6 or sound_std < 1e-6:
        raise ValueError("audio is silent or has no usable waveform")
    camera = camera / camera_std
    sound = sound / sound_std

    full_length = camera.size + sound.size - 1
    fft_length = 1 << (full_length - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(sound, fft_length) * np.fft.rfft(camera[::-1], fft_length),
        fft_length,
    )[:full_length]

    shorter = min(camera.size, sound.size)
    minimum = min(shorter, max(int(MIN_AUTO_OVERLAP_SECONDS * sample_rate),
                               int(0.60 * shorter)))
    first = minimum - 1
    last = sound.size + camera.size - minimum
    values = correlation[first:last]
    lags = np.arange(first - (camera.size - 1),
                     last - (camera.size - 1), dtype=np.int64)
    overlaps = np.minimum(sound.size - np.maximum(lags, 0),
                          camera.size - np.maximum(-lags, 0))
    scores = np.abs(values) / overlaps
    peak_index = int(np.argmax(scores))
    peak_score = float(min(1.0, scores[peak_index]))
    peak_lag = int(lags[peak_index])
    peak_overlap = int(overlaps[peak_index])

    radius = max(1, int(0.5 * sample_rate))
    lo, hi = max(0, peak_index - radius), min(scores.size, peak_index + radius + 1)
    saved = scores[lo:hi].copy()
    scores[lo:hi] = -np.inf
    alternate = float(max(0.0, np.max(scores))) if scores.size > saved.size else 0.0
    scores[lo:hi] = saved
    return {
        "score": peak_score,
        "alternate_peak_score": min(1.0, alternate),
        "offset_seconds": -peak_lag / float(sample_rate),
        "overlap_seconds": peak_overlap / float(sample_rate),
    }


def _sound_group_key(row):
    if row["bwf_time_reference"] is not None and (row["bwf_sample_rate"] or row["sample_rate"]):
        timing = round(row["bwf_time_reference"] /
                       (row["bwf_sample_rate"] or row["sample_rate"]), 3)
    else:
        timing = row["start_timecode"]
    identity = (
        row["sound_roll"] or row["card_roll"] or "",
        row["scene"] or "", row["take_name"] or "", timing,
        round(float(row["duration_seconds"] or 0), 3),
    )
    if not any(identity[:-1]):
        identity += (row["file_id"],)
    return json.dumps(identity, ensure_ascii=False, separators=(",", ":"))


def _groups(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(_sound_group_key(row), []).append(row)
    return grouped


def _save_result(camera, state, prior_status, prior_method, best=None,
                 runner=0.0, margin=0.0, candidate_groups=0, error=None):
    db.ex("UPDATE dailies SET state='stale',updated_at=? WHERE camera_file_id=?",
          (db.now(), camera["file_id"]))
    db.ex("""UPDATE handoff_exports SET state='stale',updated_at=? WHERE day_id IN
             (SELECT c.day_id FROM files f JOIN cards c ON c.id=f.card_id WHERE f.id=?)""",
          (db.now(), camera["file_id"]))
    db.ex(
        """INSERT INTO waveform_results
           (camera_file_id,state,prior_status,prior_method,best_sound_file_id,
            score,runner_up_score,margin,offset_seconds,overlap_seconds,
            candidate_groups,camera_source_hash,sound_source_hash,error,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(camera_file_id) DO UPDATE SET
             state=excluded.state,prior_status=excluded.prior_status,
             prior_method=excluded.prior_method,best_sound_file_id=excluded.best_sound_file_id,
             score=excluded.score,runner_up_score=excluded.runner_up_score,
             margin=excluded.margin,offset_seconds=excluded.offset_seconds,
             overlap_seconds=excluded.overlap_seconds,
             candidate_groups=excluded.candidate_groups,
             camera_source_hash=excluded.camera_source_hash,
             sound_source_hash=excluded.sound_source_hash,error=excluded.error,
             updated_at=excluded.updated_at""",
        (camera["file_id"], state, prior_status, prior_method,
         best["row"]["file_id"] if best else None,
         best["score"] if best else None, runner, margin,
         best["offset_seconds"] if best else None,
         best["overlap_seconds"] if best else None,
         candidate_groups, camera["src_hash"],
         best["row"]["src_hash"] if best else None, error, db.now()),
    )


def _save_candidates(camera_id, ranked):
    db.ex("DELETE FROM waveform_candidates WHERE camera_file_id=?", (camera_id,))
    for rank, candidate in enumerate(ranked[:MAX_RECORDED_CANDIDATES], 1):
        db.ex(
            """INSERT INTO waveform_candidates
               (camera_file_id,sound_file_id,group_key,rank,score,
                alternate_peak_score,offset_seconds,overlap_seconds,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (camera_id, candidate["row"]["file_id"], candidate["group_key"], rank,
             candidate["score"], candidate["alternate_peak_score"],
             candidate["offset_seconds"], candidate["overlap_seconds"], db.now()),
        )


def _set_sync_result(camera, state, best, members, candidate_groups, error):
    db.ex("DELETE FROM sync_links WHERE camera_file_id=?", (camera["file_id"],))
    if state == "matched":
        # sound_start_seconds is an ABSOLUTE position on the day's timecode
        # axis -- the same units Phase 4 writes, and the units handoff.py
        # renders as an FCPXML asset/clip `start`. offset_seconds stays
        # camera-relative. Storing the offset in both columns produced
        # negative source starts for every waveform-accepted clip.
        camera_start = db.q1(
            "SELECT camera_start_seconds FROM sync_status WHERE camera_file_id=?",
            (camera["file_id"],),
        )
        base = camera_start["camera_start_seconds"] if camera_start else None
        if base is None:
            # No usable camera timecode (the 'invalid' path). Anchor at zero so
            # the stored value is still a real position rather than a negative
            # one, and record the offset separately as always.
            base = max(0.0, -float(best["offset_seconds"]))
        sound_start = float(base) + float(best["offset_seconds"])
        for member in members:
            duration = float(member["duration_seconds"] or 0)
            db.ex(
                """INSERT INTO sync_links
                   (camera_file_id,sound_file_id,method,sound_start_seconds,
                    sound_end_seconds,offset_seconds,overlap_seconds,
                    day_shift_seconds,updated_at)
                   VALUES (?,?,'waveform',?,?,?,?,0,?)""",
                (camera["file_id"], member["file_id"], sound_start,
                 sound_start + duration, best["offset_seconds"],
                 best["overlap_seconds"], db.now()),
            )
    db.ex(
        """UPDATE sync_status SET status=?,method='waveform',candidate_groups=?,
                  link_count=?,error=?,updated_at=? WHERE camera_file_id=?""",
        (state, candidate_groups, len(members) if state == "matched" else 0,
         error, db.now(), camera["file_id"]),
    )


class Job:
    def __init__(self, day_id):
        self.day_id = day_id
        self.state = "queued"
        self.phase = "Queued"
        self.message = ""
        self.current_file = ""
        self.files_total = 0
        self.files_done = 0
        self.errors = []
        self.warnings = []
        self.cancel = threading.Event()
        self.process = None
        self.job_row = None
        self.started_epoch = None
        self.finished_epoch = None

    def snapshot(self):
        percent = (min(100, self.files_done / self.files_total * 100)
                   if self.files_total else (100 if self.state == "done" else 0))
        return {
            "job_type": "waveform", "mode": "waveform", "day_id": self.day_id,
            "state": self.state, "phase": self.phase, "message": self.message,
            "current_file": self.current_file, "files_total": self.files_total,
            "files_done": self.files_done, "seconds_total": 0, "seconds_done": 0,
            "bytes_total": 0, "bytes_done": 0, "percent": round(percent, 1),
            "errors": self.errors[-20:], "warnings": self.warnings[-20:],
            "started_epoch": self.started_epoch, "finished_epoch": self.finished_epoch,
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
                self.message = (f"Waveform fallback finished with {len(self.errors)} problem(s) — "
                                "media and format clearance are unaffected")
            elif self.warnings:
                self.message = f"Waveform fallback finished with {len(self.warnings)} notice(s)"
            else:
                self.message = "Waveform fallback complete"
        except WaveformCancelled:
            self.state = "cancelled"
            self.message = "Waveform fallback stopped safely — media is unaffected"
        except Exception:
            self.state = "failed"
            self.message = "Waveform fallback crashed — media and format clearance are unaffected"
            self.errors.append(traceback.format_exc(limit=5))
        finally:
            self.finished_epoch = time.time()
            if self.job_row:
                db.ex("UPDATE jobs SET state=?,message=?,finished_at=? WHERE id=?",
                      (self.state, self.message, db.now(), self.job_row))
            global _current, _last_snapshot
            with _lock:
                _last_snapshot = self.snapshot()
                if _current is self:
                    _current = None

    def _run(self):
        primary = _primary(self.day_id)
        if not primary:
            raise RuntimeError("Mark one destination primary before waveform fallback")
        cameras = _camera_rows(self.day_id, primary["id"])
        if not cameras:
            raise RuntimeError("No clips need waveform fallback")
        sounds = [row for row in _sound_rows(self.day_id, primary["id"])
                  if not _path_problem(row)]
        groups = _groups(sounds)
        self.files_total = len(cameras)
        self.job_row = db.ex(
            "INSERT INTO jobs(card_id,job_type,state,started_at) VALUES (?,'waveform','running',?)",
            (cameras[0]["card_id"], db.now()),
        )
        # Fingerprints are reused across cameras, so caching them matters --
        # but each one is up to MAX_SAMPLES float32 (~16 MB), and a long day
        # has a lot of takes. Bound the cache by total samples and evict the
        # least recently used, on a machine that is simultaneously moving
        # terabytes.
        cache = OrderedDict()
        cached_samples = 0

        def fingerprint(row):
            nonlocal cached_samples
            key = row["file_id"]
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
            self.phase = "Reading verified audio"
            signal = extract_audio(row["input_path"], self.cancel, self._set_process)
            cache[key] = signal
            cached_samples += signal.size
            while cached_samples > CACHE_SAMPLE_BUDGET and len(cache) > 1:
                _, evicted = cache.popitem(last=False)
                cached_samples -= evicted.size
            return signal

        for camera in cameras:
            if self.cancel.is_set():
                raise WaveformCancelled()
            self.current_file = os.path.basename(camera["rel_path"])
            prior_status, prior_method = camera["sync_status"], camera["sync_method"]
            try:
                camera_signal = fingerprint(camera)
                ranked = []
                self.phase = "Comparing waveforms"
                verification = prior_status == "matched" and prior_method == "timecode"
                if verification:
                    prior_links = db.q(
                        "SELECT sound_file_id,offset_seconds FROM sync_links WHERE camera_file_id=?",
                        (camera["file_id"],),
                    )
                    linked_ids = {row["sound_file_id"] for row in prior_links}
                    prior_offset = float(prior_links[0]["offset_seconds"]) if prior_links else None
                    candidate_groups = {
                        key: members for key, members in groups.items()
                        if any(member["file_id"] in linked_ids for member in members)
                    }
                    if not candidate_groups or prior_offset is None:
                        raise ValueError("timecode relationship has no linked sound to verify")
                else:
                    candidate_groups = groups
                    prior_offset = None
                for group_index, (group_key, members) in enumerate(candidate_groups.items(), 1):
                    if self.cancel.is_set():
                        raise WaveformCancelled()
                    self.current_file = (f"{os.path.basename(camera['rel_path'])} · "
                                         f"sound {group_index}/{len(candidate_groups)}")
                    best_member = None
                    for sound in members:
                        try:
                            result = correlate(camera_signal, fingerprint(sound))
                        except ValueError:
                            continue
                        candidate = {**result, "row": sound, "members": members,
                                     "group_key": group_key}
                        if best_member is None or candidate["score"] > best_member["score"]:
                            best_member = candidate
                    if best_member:
                        ranked.append(best_member)
                ranked.sort(key=lambda item: item["score"], reverse=True)
                if not ranked:
                    raise ValueError("no sound file contained a usable waveform")
                best = ranked[0]
                runner = max(
                    best["alternate_peak_score"],
                    ranked[1]["score"] if len(ranked) > 1 else 0.0,
                )
                margin = max(0.0, best["score"] - runner)
                confident = (best["score"] >= AUTO_SCORE and margin >= AUTO_MARGIN
                             and best["overlap_seconds"] >= MIN_AUTO_OVERLAP_SECONDS)
                if verification and confident:
                    disagreement = abs(best["offset_seconds"] - prior_offset)
                    if disagreement > TC_DISAGREEMENT_SECONDS:
                        state, error = "matched", None
                        self.warnings.append(
                            f"{self.current_file}: waveform corrected TC offset by "
                            f"{best['offset_seconds'] - prior_offset:+.3f}s"
                        )
                    else:
                        state, error = "confirmed", None
                elif verification:
                    state = "unverifiable"
                    error = (f"Waveform could not independently confirm TC; "
                             f"best score {best['score']:.3f}, margin {margin:.3f}")
                    self.warnings.append(f"{self.current_file}: {error}")
                elif confident:
                    state, error = "matched", None
                elif best["score"] >= REVIEW_SCORE:
                    state = "review"
                    error = (f"Waveform candidate needs review: score {best['score']:.3f}, "
                             f"margin {margin:.3f}")
                    self.warnings.append(f"{self.current_file}: {error}")
                else:
                    state = "unmatched"
                    error = f"No confident waveform match; best score {best['score']:.3f}"
                    self.warnings.append(f"{self.current_file}: {error}")
                with db.transaction():
                    _save_candidates(camera["file_id"], ranked)
                    _save_result(camera, state, prior_status, prior_method, best,
                                 runner, margin, len(ranked), error)
                    if state in ("matched", "review", "unmatched"):
                        _set_sync_result(camera, state, best, best["members"],
                                         len(ranked), error)
            except WaveformCancelled:
                raise
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                self.errors.append(f"{self.current_file}: {error}")
                with db.transaction():
                    db.ex("DELETE FROM waveform_candidates WHERE camera_file_id=?",
                          (camera["file_id"],))
                    _save_result(camera, "failed", prior_status, prior_method, error=error)
            self.files_done += 1
        self.current_file = ""
        self.phase = "Finished"


def start(day_id):
    global _current, _last_snapshot
    with _lock:
        if _current is not None:
            return None, "Another waveform job is already running"
        job = Job(day_id)
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


def day_summary(day_id):
    ok, reason = capability(recheck=True)
    row = db.q1(
        """SELECT COUNT(*) analyzed,
                  SUM(CASE WHEN w.state='matched' THEN 1 ELSE 0 END) matched,
                  SUM(CASE WHEN w.state='confirmed' THEN 1 ELSE 0 END) confirmed,
                  SUM(CASE WHEN w.state='review' THEN 1 ELSE 0 END) review,
                  SUM(CASE WHEN w.state='unmatched' THEN 1 ELSE 0 END) unmatched,
                  SUM(CASE WHEN w.state IN ('failed','cancelled','unverifiable')
                           THEN 1 ELSE 0 END) problems,
                  MAX(w.updated_at) updated_at
           FROM waveform_results w JOIN files f ON f.id=w.camera_file_id
           JOIN cards c ON c.id=f.card_id WHERE c.day_id=?""", (day_id,)
    )
    result = {name: row[name] or 0 for name in
              ("analyzed", "matched", "confirmed", "review", "unmatched", "problems")}
    pending = db.q1(
        """SELECT COUNT(*) count FROM files f JOIN cards c ON c.id=f.card_id
           JOIN sync_status s ON s.camera_file_id=f.id
           LEFT JOIN waveform_results w ON w.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video' AND
             (s.status IN ('ambiguous','review','unmatched','invalid') OR
              (s.status='matched' AND s.method='timecode' AND w.camera_file_id IS NULL))""",
        (day_id,),
    )["count"]
    result.update({"updated_at": row["updated_at"], "available": ok,
                   "unavailable_reason": reason, "pending": pending})
    return result

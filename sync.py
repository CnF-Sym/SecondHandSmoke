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

"""Deterministic camera/sound matching from embedded timecode metadata.

This module writes relationships only. It never modifies, renders or moves
media. BWF TimeReference is an audio-sample count since timecode midnight;
camera timecode is converted to the same elapsed-time axis at the clip's real
frame rate, including SMPTE drop-frame numbering when present.
"""

from __future__ import annotations

import math
import re
import threading
from fractions import Fraction
from itertools import combinations

import db


TIMECODE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})([:;])(\d{2})$")
MIN_OVERLAP_SECONDS = 0.25
RATE_TOLERANCE = 0.01
METADATA_LOCK = threading.Lock()


def parse_rate(value):
    if value in (None, "", "0/0", "N/A"):
        raise ValueError("frame rate is missing")
    try:
        if isinstance(value, Fraction):
            rate = value
        elif "/" in str(value):
            numerator, denominator = str(value).split("/", 1)
            rate = Fraction(int(numerator), int(denominator))
        else:
            rate = Fraction(str(value)).limit_denominator(100_000)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid frame rate {value}") from exc
    if rate <= 0 or rate > 240:
        raise ValueError(f"invalid frame rate {value}")
    return rate


def nominal_rate(rate):
    nominal = int(round(float(parse_rate(rate))))
    if nominal <= 0:
        raise ValueError(f"invalid frame rate {rate}")
    return nominal


def timecode_to_frames(timecode, rate, drop_frame=None):
    match = TIMECODE_RE.fullmatch((timecode or "").strip())
    if not match:
        raise ValueError(f"invalid timecode {timecode or 'missing'}")
    hours, minutes, seconds, separator, frames = match.groups()
    hours, minutes, seconds, frames = map(int, (hours, minutes, seconds, frames))
    nominal = nominal_rate(rate)
    if hours > 23 or minutes > 59 or seconds > 59 or frames >= nominal:
        raise ValueError(f"invalid timecode {timecode}")
    drop = separator == ";" if drop_frame is None else bool(drop_frame)
    total = ((hours * 3600 + minutes * 60 + seconds) * nominal) + frames
    if not drop:
        return total
    if nominal not in (30, 60):
        raise ValueError(f"drop-frame timecode is invalid at {float(parse_rate(rate)):.3f} fps")
    drop_count = 2 if nominal == 30 else 4
    if minutes % 10 and seconds == 0 and frames < drop_count:
        raise ValueError(f"{timecode} is a skipped drop-frame label")
    total_minutes = hours * 60 + minutes
    return total - drop_count * (total_minutes - total_minutes // 10)


def timecode_to_seconds(timecode, rate, drop_frame=None):
    return timecode_to_frames(timecode, rate, drop_frame) / float(parse_rate(rate))


def day_length_seconds(rate, drop_frame=False):
    parsed = parse_rate(rate)
    nominal = nominal_rate(parsed)
    frames = nominal * 24 * 3600
    if drop_frame:
        if nominal not in (30, 60):
            raise ValueError("drop-frame day length is only defined for 30/60-based rates")
        drop_count = 2 if nominal == 30 else 4
        frames -= drop_count * (24 * 60 - (24 * 60) // 10)
    return frames / float(parsed)


def _circular_delta(a, b, period):
    return min((a - b, a - b - period, a - b + period), key=abs)


def overlap_with_rollover(camera_start, camera_duration, sound_start, sound_duration,
                          day_seconds):
    """Return best (overlap, aligned sound start, day shift) around midnight."""
    camera_end = camera_start + camera_duration
    choices = []
    for shift in (-day_seconds, 0.0, day_seconds):
        aligned = sound_start + shift
        sound_end = aligned + sound_duration
        overlap = max(0.0, min(camera_end, sound_end) - max(camera_start, aligned))
        choices.append((overlap, aligned, shift))
    return max(choices, key=lambda item: (item[0], -abs(item[1] - camera_start)))


def _camera_rows(day_id):
    return db.q(
        """SELECT f.id file_id, f.rel_path, c.id card_id, c.roll,
                  m.start_timecode, m.duration_seconds, m.frame_rate,
                  m.timecode_rate, m.timecode_drop_frame
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN media_metadata m ON m.file_id=f.id
           WHERE c.day_id=? AND f.category='video'
           ORDER BY c.roll, f.rel_path""",
        (day_id,),
    )


def camera_timecode_rate(row):
    """The rate a camera clip's timecode is numbered at.

    Normally the same as the recording frame rate. For off-speed recording --
    60 fps captured while timecode runs at 24 -- they differ, and converting
    at the recording rate puts the clip hours away on the day axis, where it
    reads as `unmatched` rather than as an error. Sound rows already prefer
    the explicit timecode rate; cameras now do the same.
    """
    if "timecode_rate" in row.keys() and row["timecode_rate"]:
        try:
            return parse_rate(row["timecode_rate"])
        except ValueError:
            pass
    return parse_rate(row["frame_rate"])


def _sound_rows(day_id):
    return db.q(
        """SELECT f.id file_id, f.rel_path, c.id card_id, c.roll card_roll,
                  m.start_timecode, m.duration_seconds, m.timecode_rate,
                  m.timecode_drop_frame, m.bwf_time_reference, m.bwf_sample_rate,
                  m.sample_rate, m.scene, m.take_name, m.sound_roll, m.notes,
                  m.circled
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN media_metadata m ON m.file_id=f.id
           WHERE c.day_id=? AND f.category='audio' AND c.kind IN ('sound','mixed')
           ORDER BY c.roll, f.rel_path""",
        (day_id,),
    )


def _audio_start(row, camera_rate, camera_drop, day_seconds, warnings):
    sound_rate = None
    if row["timecode_rate"]:
        try:
            sound_rate = parse_rate(row["timecode_rate"])
        except ValueError as exc:
            warnings.append(f"{row['rel_path']}: {exc}")
            return None
        if abs(float(sound_rate) - float(camera_rate)) > RATE_TOLERANCE:
            warnings.append(
                f"{row['rel_path']}: sound TC rate {float(sound_rate):.3f} "
                f"does not match camera {float(camera_rate):.3f}"
            )
            return None
    if row["timecode_drop_frame"] is not None \
            and bool(row["timecode_drop_frame"]) != bool(camera_drop):
        warnings.append(f"{row['rel_path']}: drop/non-drop TC does not match camera")
        return None

    sample_rate = row["bwf_sample_rate"] or row["sample_rate"]
    if row["bwf_time_reference"] is not None and sample_rate:
        start = row["bwf_time_reference"] / sample_rate
        if row["start_timecode"]:
            try:
                reported = timecode_to_seconds(
                    row["start_timecode"], sound_rate or camera_rate,
                    row["timecode_drop_frame"] if row["timecode_drop_frame"] is not None
                    else camera_drop,
                )
                difference = _circular_delta(reported, start, day_seconds)
                tolerance = max(0.1, 2 / float(camera_rate))
                if abs(difference) > tolerance:
                    warnings.append(
                        f"{row['rel_path']}: sound report TC differs from BWF by "
                        f"{difference:+.3f}s; BWF used"
                    )
            except ValueError as exc:
                warnings.append(f"{row['rel_path']}: {exc}; BWF used")
        return start
    if row["start_timecode"]:
        try:
            return timecode_to_seconds(
                row["start_timecode"], sound_rate or camera_rate,
                row["timecode_drop_frame"] if row["timecode_drop_frame"] is not None
                else camera_drop,
            )
        except ValueError as exc:
            warnings.append(f"{row['rel_path']}: {exc}")
    return None


def _groups_ambiguous(groups):
    representatives = [members[0] for members in groups.values()]
    for first, second in combinations(representatives, 2):
        overlap = max(0.0, min(first["end"], second["end"])
                      - max(first["start"], second["start"]))
        if overlap >= MIN_OVERLAP_SECONDS:
            return True
    return False


def _save_status(camera_id, status, start=None, end=None, frame_rate=None,
                 drop_frame=None, candidate_groups=0, link_count=0, error=None):
    db.ex(
        """INSERT INTO sync_status
           (camera_file_id,status,method,camera_start_seconds,camera_end_seconds,
            frame_rate,drop_frame,candidate_groups,link_count,error,updated_at)
           VALUES (?,?,'timecode',?,?,?,?,?,?,?,?)
           ON CONFLICT(camera_file_id) DO UPDATE SET
             status=excluded.status, method=excluded.method,
             camera_start_seconds=excluded.camera_start_seconds,
             camera_end_seconds=excluded.camera_end_seconds,
             frame_rate=excluded.frame_rate, drop_frame=excluded.drop_frame,
             candidate_groups=excluded.candidate_groups, link_count=excluded.link_count,
             error=excluded.error, updated_at=excluded.updated_at""",
        (camera_id, status, start, end, frame_rate,
         int(drop_frame) if drop_frame is not None else None,
         candidate_groups, link_count, error, db.now()),
    )


def _run_day(day_id):
    day = db.q1("SELECT id FROM days WHERE id=?", (day_id,))
    if not day:
        raise ValueError("Unknown shoot day")
    cameras = _camera_rows(day_id)
    sounds = _sound_rows(day_id)
    warnings = []
    counts = {name: 0 for name in ("matched", "multiple", "ambiguous", "unmatched", "invalid")}

    with db.transaction():
        db.ex("UPDATE handoff_exports SET state='stale',updated_at=? WHERE day_id=?",
              (db.now(), day_id))
        db.ex("""UPDATE dailies SET state='stale',updated_at=? WHERE camera_file_id IN
                 (SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
                  WHERE c.day_id=? AND f.category='video')""", (db.now(), day_id))
        db.ex("""DELETE FROM waveform_candidates WHERE camera_file_id IN
                 (SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
                  WHERE c.day_id=?)""", (day_id,))
        db.ex("""DELETE FROM waveform_results WHERE camera_file_id IN
                 (SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
                  WHERE c.day_id=?)""", (day_id,))
        db.ex("""DELETE FROM sync_links WHERE camera_file_id IN
                 (SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
                  WHERE c.day_id=?)""", (day_id,))
        db.ex("""DELETE FROM sync_status WHERE camera_file_id IN
                 (SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
                  WHERE c.day_id=?)""", (day_id,))

        for camera in cameras:
            try:
                rate = camera_timecode_rate(camera)
                drop = (bool(camera["timecode_drop_frame"])
                        if camera["timecode_drop_frame"] is not None
                        else ";" in (camera["start_timecode"] or ""))
                camera_start = timecode_to_seconds(camera["start_timecode"], rate, drop)
                duration = float(camera["duration_seconds"])
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError("duration is missing")
                day_seconds = day_length_seconds(rate, drop)
            except (TypeError, ValueError) as exc:
                error = str(exc)
                _save_status(camera["file_id"], "invalid", frame_rate=camera["frame_rate"],
                             error=error)
                counts["invalid"] += 1
                warnings.append(f"{camera['rel_path']}: {error}")
                continue

            camera_end = camera_start + duration
            candidates = []
            for sound in sounds:
                sound_duration = sound["duration_seconds"]
                try:
                    sound_duration = float(sound_duration)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(sound_duration) or sound_duration <= 0:
                    continue
                sound_start = _audio_start(sound, rate, drop, day_seconds, warnings)
                if sound_start is None:
                    continue
                overlap, aligned, shift = overlap_with_rollover(
                    camera_start, duration, sound_start, sound_duration, day_seconds
                )
                if overlap < MIN_OVERLAP_SECONDS:
                    continue
                group_key = (
                    sound["sound_roll"] or sound["card_roll"] or "",
                    sound["scene"] or "", sound["take_name"] or "",
                    round(sound_start % day_seconds, 3), round(sound_duration, 3),
                )
                candidates.append({
                    "row": sound, "start": aligned, "end": aligned + sound_duration,
                    "offset": aligned - camera_start, "overlap": overlap,
                    "shift": shift, "group": group_key,
                })

            groups = {}
            for candidate in candidates:
                groups.setdefault(candidate["group"], []).append(candidate)
            if not groups:
                status = "unmatched"
                error = "No timecoded sound overlaps this camera clip"
            elif len(groups) == 1:
                status, error = "matched", None
            elif _groups_ambiguous(groups):
                status = "ambiguous"
                error = f"{len(groups)} overlapping sound takes need review"
            else:
                status, error = "multiple", None

            for candidate in candidates:
                db.ex(
                    """INSERT INTO sync_links
                       (camera_file_id,sound_file_id,method,sound_start_seconds,
                       sound_end_seconds,offset_seconds,overlap_seconds,
                        day_shift_seconds,updated_at)
                       VALUES (?,?,'timecode',?,?,?,?,?,?)""",
                    (camera["file_id"], candidate["row"]["file_id"],
                     candidate["start"], candidate["end"], candidate["offset"],
                     candidate["overlap"], candidate["shift"], db.now()),
                )
            _save_status(
                camera["file_id"], status, camera_start, camera_end,
                camera["frame_rate"], drop, len(groups), len(candidates), error,
            )
            counts[status] += 1

    return {"total": len(cameras), **counts, "links": db.q1(
        """SELECT COUNT(*) count FROM sync_links l JOIN files f ON f.id=l.camera_file_id
           JOIN cards c ON c.id=f.card_id WHERE c.day_id=?""", (day_id,))["count"],
            "warnings": list(dict.fromkeys(warnings))[:100], "updated_at": db.now()}


def run_day(day_id):
    # Manual re-analysis and an explicit sync request can arrive on separate
    # Flask threads. Serialize them so links always describe one complete
    # metadata snapshot rather than a card halfway through re-analysis.
    with METADATA_LOCK:
        return _run_day(day_id)


def invalidate_card(card_id):
    """Discard relationships made from metadata that is about to change."""
    card = db.q1("SELECT day_id,kind FROM cards WHERE id=?", (card_id,))
    if not card:
        return
    if card["kind"] in ("sound", "mixed"):
        camera_ids = db.q(
            """SELECT f.id FROM files f JOIN cards c ON c.id=f.card_id
               WHERE c.day_id=? AND f.category='video'""", (card["day_id"],)
        )
    else:
        camera_ids = db.q(
            "SELECT id FROM files WHERE card_id=? AND category='video'", (card_id,)
        )
    ids = [row["id"] for row in camera_ids]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with db.transaction():
        db.ex(f"UPDATE dailies SET state='stale',updated_at=? "
              f"WHERE camera_file_id IN ({placeholders})", (db.now(), *ids))
        db.ex("UPDATE handoff_exports SET state='stale',updated_at=? WHERE day_id=?",
              (db.now(), card["day_id"]))
        db.ex(f"DELETE FROM waveform_candidates WHERE camera_file_id IN ({placeholders})", ids)
        db.ex(f"DELETE FROM waveform_results WHERE camera_file_id IN ({placeholders})", ids)
        db.ex(f"DELETE FROM sync_links WHERE camera_file_id IN ({placeholders})", ids)
        db.ex(f"DELETE FROM sync_status WHERE camera_file_id IN ({placeholders})", ids)


def day_summary(day_id):
    row = db.q1(
        """SELECT COUNT(f.id) total,
                  COUNT(s.camera_file_id) analyzed,
                  SUM(CASE WHEN s.status='matched' THEN 1 ELSE 0 END) matched,
                  SUM(CASE WHEN s.status='multiple' THEN 1 ELSE 0 END) multiple,
                  SUM(CASE WHEN s.status='ambiguous' THEN 1 ELSE 0 END) ambiguous,
                  SUM(CASE WHEN s.status='review' THEN 1 ELSE 0 END) review,
                  SUM(CASE WHEN s.status='unmatched' THEN 1 ELSE 0 END) unmatched,
                  SUM(CASE WHEN s.status='invalid' THEN 1 ELSE 0 END) invalid,
                  SUM(CASE WHEN s.status IN ('matched','multiple')
                                AND s.method='waveform' THEN 1 ELSE 0 END) waveform,
                  SUM(CASE WHEN s.status IN ('matched','multiple')
                                AND s.method='timecode' THEN 1 ELSE 0 END) timecode,
                  SUM(CASE WHEN s.status IN ('matched','multiple') THEN s.link_count ELSE 0 END)
                      links,
                  MAX(s.updated_at) updated_at
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN sync_status s ON s.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'""",
        (day_id,),
    )
    return {name: row[name] or 0 for name in
            ("total", "analyzed", "matched", "multiple", "ambiguous", "review",
             "unmatched", "invalid", "waveform", "timecode", "links")} \
        | {"updated_at": row["updated_at"]}


def card_summary(card_id):
    row = db.q1(
        """SELECT COUNT(f.id) total,
                  SUM(CASE WHEN s.status IN ('matched','multiple') THEN 1 ELSE 0 END) synced,
                  SUM(CASE WHEN s.status='ambiguous' THEN 1 ELSE 0 END) ambiguous,
                  SUM(CASE WHEN s.status='review' THEN 1 ELSE 0 END) review,
                  SUM(CASE WHEN s.status='unmatched' THEN 1 ELSE 0 END) unmatched,
                  SUM(CASE WHEN s.status='invalid' THEN 1 ELSE 0 END) invalid,
                  SUM(CASE WHEN s.status IN ('matched','multiple')
                                AND s.method='waveform' THEN 1 ELSE 0 END) waveform,
                  MAX(s.updated_at) updated_at
           FROM files f LEFT JOIN sync_status s ON s.camera_file_id=f.id
           WHERE f.card_id=? AND f.category='video'""",
        (card_id,),
    )
    return {name: row[name] or 0 for name in
            ("total", "synced", "ambiguous", "review", "unmatched", "invalid",
             "waveform")} \
        | {"updated_at": row["updated_at"]}

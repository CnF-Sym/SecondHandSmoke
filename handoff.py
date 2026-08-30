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

"""Resumable review dailies and a deterministic Resolve handoff package.

This is downstream editorial work.  It reads verified primary-drive masters,
existing ProRes proxies and accepted sync relationships; it never reads source
cards, changes a copy record or participates in SAFE TO FORMAT.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from collections import Counter
from fractions import Fraction
from pathlib import Path

import xxhash

import db
import media
import proxies
import storage
import sync

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # The ingest/verification safety path must still launch.
    Image = ImageDraw = ImageFont = None


FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
PROFILE = "h264_720p_burnin_v1"
PROFILE_LABEL = "H.264 review 720p"
FCPXML_VERSION = "1.10"
ESTIMATED_BYTES_PER_SECOND = 550_000
F_FULLFSYNC = 51

_lock = threading.Lock()
_current = None
_last_snapshot = None
_capability = None


class HandoffCancelled(Exception):
    pass


def capability(recheck=False):
    """Return whether this machine can build the optional review movies.

    A positive answer is cached; a negative one is re-probed, so installing
    ffmpeg or Pillow mid-shoot takes effect without restarting the tool.
    """
    global _capability, FFMPEG, Image, ImageDraw, ImageFont
    if _capability is not None and (_capability[0] or not recheck):
        return _capability
    FFMPEG = os.environ.get("DIT_FFMPEG") or shutil.which("ffmpeg")
    if Image is None:
        try:
            from PIL import Image as _I, ImageDraw as _D, ImageFont as _F
            Image, ImageDraw, ImageFont = _I, _D, _F
        except ImportError:
            pass
    if not FFMPEG:
        _capability = (False, "ffmpeg is not installed")
        return _capability
    if not media.FFPROBE:
        _capability = (False, "ffprobe is not installed")
        return _capability
    if Image is None:
        _capability = (False, "Pillow is not installed")
        return _capability
    try:
        encoders = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"], capture_output=True,
            text=True, timeout=10,
        )
        filters = subprocess.run(
            [FFMPEG, "-hide_banner", "-filters"], capture_output=True,
            text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _capability = (False, f"ffmpeg could not start: {exc}")
    else:
        required_encoders = ("libx264", "aac")
        required_filters = ("overlay", "scale", "amix")
        missing = [name for name in required_encoders if name not in encoders.stdout]
        missing += [name for name in required_filters if name not in filters.stdout]
        _capability = (not missing, "" if not missing else
                       f"ffmpeg is missing {', '.join(missing)}")
    return _capability


def _primary(day_id):
    return db.q1(
        """SELECT * FROM destinations WHERE day_id=? AND role='primary'
           ORDER BY id LIMIT 1""", (day_id,),
    )


def _camera_rows(day_id, destination_id):
    return db.q(
        """SELECT f.id file_id,f.rel_path,f.size source_size,f.src_hash,
                  c.id card_id,c.roll,c.label card_label,
                  m.duration_seconds,m.start_timecode,m.frame_rate,
                  m.timecode_rate,m.timecode_drop_frame,
                  m.audio_channels,m.scene camera_scene,
                  m.take_name camera_take,m.notes camera_notes,
                  cp.state copy_state,cp.dest_path original_path,
                  p.state proxy_state,p.profile proxy_profile,
                  p.source_hash proxy_source_hash,p.output_path proxy_path,
                  p.output_size proxy_size,p.output_hash proxy_hash,
                  p.duration_seconds proxy_duration,p.start_timecode proxy_timecode,
                  p.reel_name proxy_reel,
                  s.status sync_status,s.method sync_method,s.error sync_error,
                  w.score waveform_score,w.margin waveform_margin
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN media_metadata m ON m.file_id=f.id
           LEFT JOIN copies cp ON cp.file_id=f.id AND cp.destination_id=?
           LEFT JOIN proxies p ON p.file_id=f.id
           LEFT JOIN sync_status s ON s.camera_file_id=f.id
           LEFT JOIN waveform_results w ON w.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'
           ORDER BY c.roll,f.rel_path""", (destination_id, day_id),
    )


def _sound_links(camera_file_id, destination_id):
    return db.q(
        """SELECT l.sound_file_id,l.method,l.sound_start_seconds,
                  l.sound_end_seconds,l.offset_seconds,l.overlap_seconds,
                  sf.rel_path,sf.size source_size,sf.src_hash,
                  sc.id card_id,sc.roll card_roll,
                  cp.state copy_state,cp.dest_path input_path,
                  sm.duration_seconds,sm.start_timecode,sm.audio_channels,
                  sm.sample_rate,sm.scene,sm.take_name,sm.notes,sm.circled,
                  sm.sound_roll
           FROM sync_links l JOIN files sf ON sf.id=l.sound_file_id
           JOIN cards sc ON sc.id=sf.card_id
           LEFT JOIN copies cp ON cp.file_id=sf.id AND cp.destination_id=?
           LEFT JOIN media_metadata sm ON sm.file_id=sf.id
           WHERE l.camera_file_id=?
           ORDER BY l.sound_start_seconds,sf.rel_path""",
        (destination_id, camera_file_id),
    )


def _path_problem(path, expected_size, label):
    if not path or not os.path.isfile(path):
        return f"{label} is missing"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return f"{label} cannot be read: {exc}"
    if expected_size is not None and size != expected_size:
        return f"{label} changed size"
    return None


def bundle_path_for(destination_root, project, day_name):
    root = storage.canonical(destination_root)
    target = os.path.abspath(os.path.join(root, project, day_name, "HANDOFF"))
    if os.path.commonpath((root, target)) != root:
        raise ValueError("Handoff path would escape the primary destination")
    return target


def daily_path_for(bundle_root, roll, rel_path, disambiguator=None):
    stem = os.path.splitext(os.path.basename(rel_path))[0]
    name = f"{stem}_{disambiguator}_DAILY.mp4" if disambiguator \
        else f"{stem}_DAILY.mp4"
    root = storage.canonical(bundle_root)
    target = os.path.abspath(os.path.join(root, "DAILIES", roll, name))
    if os.path.commonpath((root, target)) != root:
        raise ValueError("Daily path would escape the handoff folder")
    return target


def _relevant_cards(cameras, links_by_camera):
    ids = {row["card_id"] for row in cameras}
    for links in links_by_camera.values():
        ids.update(link["card_id"] for link in links)
    return sorted(ids)


def preflight(day_id, rebuild=False):
    ok, reason = capability()
    if not ok:
        return reason
    day = db.q1("SELECT * FROM days WHERE id=?", (day_id,))
    if not day:
        return "Unknown shoot day"
    primary = _primary(day_id)
    if not primary:
        return "Mark one destination primary before building the handoff"
    try:
        current = storage.inspect_path(primary["path"])
    except storage.StorageError as exc:
        return str(exc)
    if primary["device_id"] and current["device_id"] != primary["device_id"]:
        return f"{primary['label']} is not the same physical drive registered for this day"
    if not os.access(current["path"], os.R_OK | os.W_OK):
        return f"No read/write access to {current['path']}"

    cameras = _camera_rows(day_id, primary["id"])
    if not cameras:
        return "No camera clips were catalogued on this day"
    analyzed = sum(1 for row in cameras if row["sync_status"])
    if analyzed != len(cameras):
        return "Run timecode sync before building the editorial handoff"
    links_by_camera = {row["file_id"]: _sound_links(row["file_id"], primary["id"])
                       for row in cameras}

    # A handoff is downstream of the verified-copy gate for every source it uses.
    import reports
    for card_id in _relevant_cards(cameras, links_by_camera):
        gate = reports.card_status(card_id)
        if not gate["safe"]:
            card = db.q1("SELECT roll,label FROM cards WHERE id=?", (card_id,))
            return f"Handoff waits for verified {card['roll'] or card['label']}: {gate['reason']}"

    total_seconds = 0.0
    for row in cameras:
        if row["copy_state"] != "verified":
            return f"Primary original is not verified for {row['rel_path']}"
        problem = _path_problem(row["original_path"], row["source_size"],
                                f"Primary original for {row['rel_path']}")
        if problem:
            return problem
        if row["proxy_state"] != "ready" or row["proxy_profile"] != proxies.PROFILE:
            return f"Build the current proxy for {row['rel_path']} before handoff"
        problem = _path_problem(row["proxy_path"], row["proxy_size"],
                                f"Proxy for {row['rel_path']}")
        if problem:
            return problem
        if not row["proxy_hash"]:
            return f"Proxy checksum is missing for {row['rel_path']}; check proxies"
        if not row["duration_seconds"] or not row["start_timecode"] \
                or not row["frame_rate"]:
            return f"Camera timing metadata is incomplete for {row['rel_path']}"
        total_seconds += float(row["duration_seconds"])
        for link in links_by_camera[row["file_id"]]:
            if link["copy_state"] != "verified":
                return f"Accepted sound is not verified for {link['rel_path']}"
            problem = _path_problem(link["input_path"], link["source_size"],
                                    f"Accepted sound for {link['rel_path']}")
            if problem:
                return problem
            if not link["duration_seconds"] or not link["sample_rate"]:
                return f"Sound metadata is incomplete for {link['rel_path']}"

    estimate = int(total_seconds * ESTIMATED_BYTES_PER_SECOND)
    reserve = max(256 * 1024 * 1024, int(estimate * 0.10))
    if current["free_bytes"] < estimate + reserve:
        return (f"{primary['label']} needs about {estimate + reserve:,} free bytes "
                f"for review dailies but has {current['free_bytes']:,}")
    return None


def _hash_file(path):
    digest = xxhash.xxh64()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def _atomic_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _safe_text(value):
    return " ".join(str(value or "").replace("\t", " ").splitlines()).strip()


def _fraction_time(value, rate=None):
    """Render seconds as an FCPXML time value.

    FCPXML expects every time to be a rational number that is a whole
    multiple of the sequence's frameDuration. Rounding to six decimals and
    taking whatever denominator fell out produced values like ``21/2s`` --
    251.7483 frames at 23.976 -- which Resolve either snaps or rejects.
    Given a rate, the value is quantized to whole frames and expressed
    against that rate's own denominator, so it is exact by construction.

    Negative values are clamped to zero: a source start before the head of
    its own asset is never valid, and a data fault upstream should not
    produce a file Resolve refuses to open.
    """
    seconds = max(0.0, float(value))
    if rate is None:
        fraction = Fraction(str(round(seconds, 6))).limit_denominator(1_000_000)
    else:
        parsed = rate if isinstance(rate, Fraction) else sync.parse_rate(rate)
        frames = int(round(seconds * float(parsed)))
        fraction = Fraction(frames * parsed.denominator, parsed.numerator)
    if fraction.denominator == 1:
        return f"{fraction.numerator}s"
    return f"{fraction.numerator}/{fraction.denominator}s"


def _frames(seconds, rate):
    """Whole frames at ``rate``, the unit the timeline is actually built in."""
    parsed = rate if isinstance(rate, Fraction) else sync.parse_rate(rate)
    return int(round(max(0.0, float(seconds)) * float(parsed)))


def _frame_duration(rate):
    parsed = sync.parse_rate(rate)
    return f"{parsed.denominator}/{parsed.numerator}s"


def frames_to_timecode(frame_number, rate, drop_frame=False):
    """Inverse of sync.timecode_to_frames, wrapping at timecode midnight."""
    nominal = sync.nominal_rate(rate)
    frame_number = int(frame_number)
    if drop_frame:
        if nominal not in (30, 60):
            raise ValueError("drop-frame output requires a 30/60-based rate")
        drop = 2 if nominal == 30 else 4
        frames_per_minute = nominal * 60 - drop
        frames_per_10_minutes = nominal * 600 - drop * 9
        frames_per_day = frames_per_10_minutes * 6 * 24
        number = frame_number % frames_per_day
        ten_minutes, remain = divmod(number, frames_per_10_minutes)
        number += drop * 9 * ten_minutes
        if remain >= drop:
            number += drop * ((remain - drop) // frames_per_minute)
        separator = ";"
    else:
        number = frame_number % (nominal * 24 * 3600)
        separator = ":"
    hours, number = divmod(number, nominal * 3600)
    minutes, number = divmod(number, nominal * 60)
    seconds, frames = divmod(number, nominal)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{frames:02d}"


def _timecode_out(row):
    rate = sync.camera_timecode_rate(row)
    drop = (bool(row["timecode_drop_frame"])
            if row["timecode_drop_frame"] is not None
            else ";" in row["start_timecode"])
    first = sync.timecode_to_frames(row["start_timecode"], rate, drop)
    duration_frames = max(1, int(round(float(row["duration_seconds"]) * float(rate))))
    return frames_to_timecode(first + duration_frames, rate, drop)


def _camera_signature(row, links):
    payload = {
        "file": row["file_id"], "source": row["src_hash"],
        "proxy": row["proxy_hash"], "duration": row["duration_seconds"],
        "timecode": row["start_timecode"], "rate": row["frame_rate"],
        "sync": row["sync_status"], "method": row["sync_method"],
        "links": [{"file": link["sound_file_id"], "hash": link["src_hash"],
                   "offset": link["offset_seconds"], "overlap": link["overlap_seconds"]}
                  for link in links],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _catalog_signature(day, cameras, links_by_camera):
    payload = {"project": day["project"], "day": day["name"],
               "shoot_date": day["shoot_date"],
               "cameras": [_camera_signature(row, links_by_camera[row["file_id"]])
                           for row in cameras]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _font(size):
    choices = [os.environ.get("DIT_SLATE_FONT"),
               "/System/Library/Fonts/SFNSMono.ttf",
               "/System/Library/Fonts/Menlo.ttc",
               "/Library/Fonts/Arial.ttf"]
    for path in choices:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _scene_take(row, links):
    scenes = []
    for link in links:
        value = " / ".join(value for value in
                           (_safe_text(link["scene"]), _safe_text(link["take_name"]))
                           if value)
        if value and value not in scenes:
            scenes.append(value)
    if not scenes:
        value = " / ".join(value for value in
                           (_safe_text(row["camera_scene"]),
                            _safe_text(row["camera_take"])) if value)
        if value:
            scenes.append(value)
    return " + ".join(scenes) or "SCENE / TAKE —"


def render_slate(path, row, links):
    """Write one transparent static burn-in overlay using only a system font."""
    image = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accepted = row["sync_status"] in ("matched", "multiple") and bool(links)
    accent = (52, 176, 123, 235) if accepted else (220, 147, 43, 240)
    draw.rectangle((0, 0, 1280, 54), fill=(6, 9, 13, 210))
    draw.rectangle((0, 654, 1280, 720), fill=(6, 9, 13, 220))
    draw.rectangle((0, 650, 1280, 654), fill=accent)
    small, medium = _font(22), _font(26)
    name = os.path.basename(row["rel_path"])
    roll = row["roll"] or row["card_label"]
    draw.text((22, 14), name, font=small, fill=(242, 245, 249, 255))
    roll_width = draw.textlength(roll, font=small)
    draw.text((1258 - roll_width, 14), roll, font=small, fill=accent)
    scene_take = _scene_take(row, links)
    draw.text((22, 672), scene_take, font=medium, fill=(242, 245, 249, 255))
    if accepted:
        method = (row["sync_method"] or "timecode").upper()
        sync_label = f"SYNC {method} · START {row['start_timecode']}"
    else:
        sync_label = f"SYNC REVIEW: {(row['sync_status'] or 'NOT RUN').upper()} · START {row['start_timecode']}"
    width = draw.textlength(sync_label, font=small)
    draw.text((1258 - width, 675), sync_label, font=small, fill=accent)
    image.save(path, format="PNG")


def transcode_daily(row, links, partial_path, slate_path, progress,
                    cancel_event, process_started=None):
    """Render a 720p H.264 review file, using accepted production sound."""
    duration = float(row["duration_seconds"])
    command = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-i", row["proxy_path"], "-loop", "1", "-i", slate_path]
    for link in links:
        command += ["-i", link["input_path"]]
    filters = [
        "[0:v:0]scale=w=1280:h=720:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black[base]",
        "[1:v:0]format=rgba[slate]",
        "[base][slate]overlay=0:0:format=auto:eof_action=repeat[v]",
    ]
    if links:
        audio_names = []
        for index, link in enumerate(links):
            trim = max(0.0, -float(link["offset_seconds"]))
            delay = max(0, round(max(0.0, float(link["offset_seconds"])) * 1000))
            usable = max(0.01, min(duration - max(0.0, float(link["offset_seconds"])),
                                   float(link["overlap_seconds"])))
            name = f"a{index}"
            filters.append(
                f"[{index + 2}:a:0]atrim=start={trim:.6f}:duration={usable:.6f},"
                f"asetpts=PTS-STARTPTS,adelay={delay}:all=1,aresample=48000,"
                f"aformat=channel_layouts=stereo[{name}]"
            )
            audio_names.append(f"[{name}]")
        # normalize=0: sequential takes inside one long camera clip do not
        # overlap, so dividing by the number of inputs just makes every one of
        # them quiet. alimiter catches the rare genuine overlap instead.
        filters.append("".join(audio_names) +
                       f"amix=inputs={len(audio_names)}:duration=longest:normalize=0,"
                       f"alimiter=limit=0.95:level=disabled,"
                       f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[aout]")
    elif row["audio_channels"]:
        filters.append("[0:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]")
    else:
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                       f"atrim=duration={duration:.6f}[aout]")
    command += [
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[aout]",
        "-t", f"{duration:.6f}", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-ar", "48000", "-movflags", "+faststart", "-timecode",
        row["start_timecode"], "-metadata:s:v:0",
        f"reel_name={row['roll'] or row['card_label']}",
        "-metadata", f"comment=Second Hand Smoke {row['sync_status']} {row['sync_method']}",
        "-stats_period", "0.5", "-progress", "pipe:1", partial_path,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
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
        raise HandoffCancelled()
    if returncode:
        raise RuntimeError(("\n".join(output[-12:]) or
                            f"ffmpeg exited with status {returncode}")[-2000:])
    progress(duration)


def inspect_daily(path):
    payload = media.probe_file(path)
    facts = media.parse_probe(payload)
    video = next((stream for stream in payload.get("streams", [])
                  if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in payload.get("streams", [])
                  if stream.get("codec_type") == "audio"), {})
    return {"codec_name": video.get("codec_name"), "width": video.get("width"),
            "height": video.get("height"), "audio_codec": audio.get("codec_name"),
            "duration_seconds": facts.get("duration_seconds")}


def _validated_daily(path, expected_duration):
    facts = inspect_daily(path)
    if facts["codec_name"] != "h264" or facts["audio_codec"] != "aac":
        raise ValueError("review file is not H.264 with AAC audio")
    if facts["width"] != 1280 or facts["height"] != 720:
        raise ValueError("review file is not 1280x720")
    if facts["duration_seconds"] is not None \
            and abs(float(facts["duration_seconds"]) - expected_duration) > 0.2:
        raise ValueError("review duration does not match the camera clip")
    return facts


def _save_daily(file_id, **values):
    columns = ("state", "profile", "source_signature", "input_path",
               "output_path", "output_size", "output_hash", "duration_seconds",
               "external_audio", "error")
    record = {name: values.get(name) for name in columns}
    record["profile"] = record["profile"] or PROFILE
    record["external_audio"] = record["external_audio"] or 0
    names = ",".join(columns)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{name}=excluded.{name}" for name in columns)
    db.ex(
        f"""INSERT INTO dailies(camera_file_id,{names},updated_at)
            VALUES (?,{placeholders},?)
            ON CONFLICT(camera_file_id) DO UPDATE SET {updates},updated_at=excluded.updated_at""",
        (file_id, *(record[name] for name in columns), db.now()),
    )


def _save_export(day_id, **values):
    columns = ("state", "catalog_signature", "bundle_path", "fcpxml_path",
               "ale_path", "json_path", "readme_path", "checksums_path",
               "total_camera", "ready_dailies", "unresolved", "error")
    record = {name: values.get(name) for name in columns}
    for name in ("total_camera", "ready_dailies", "unresolved"):
        record[name] = record[name] or 0
    names = ",".join(columns)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{name}=excluded.{name}" for name in columns)
    db.ex(
        f"""INSERT INTO handoff_exports(day_id,{names},updated_at)
            VALUES (?,{placeholders},?)
            ON CONFLICT(day_id) DO UPDATE SET {updates},updated_at=excluded.updated_at""",
        (day_id, *(record[name] for name in columns), db.now()),
    )


def _relative(path, root):
    try:
        return os.path.relpath(path, root)
    except (TypeError, ValueError):
        return path


def _format_resource(resources, ids, rate):
    key = str(rate)
    if key not in ids:
        resource_id = f"r{len(ids) + 1}"
        ids[key] = resource_id
        ET.SubElement(resources, "format", id=resource_id,
                      name=f"FFVideoFormat1080p{float(sync.parse_rate(rate)):.3f}",
                      frameDuration=_frame_duration(rate), width="1920", height="1080",
                      colorSpace="1-1-1 (Rec. 709)")
    return ids[key]


def _camera_start_seconds(row):
    # Timecode is numbered at the timecode rate, which is only the same as the
    # recording rate when the camera is not shooting off-speed.
    return sync.timecode_to_seconds(
        row["start_timecode"], sync.camera_timecode_rate(row),
        bool(row["timecode_drop_frame"]) if row["timecode_drop_frame"] is not None
        else ";" in row["start_timecode"],
    )


def build_fcpxml(day, cameras, links_by_camera):
    fcpxml = ET.Element("fcpxml", version=FCPXML_VERSION)
    resources = ET.SubElement(fcpxml, "resources")
    format_ids = {}
    camera_assets, sound_assets = {}, {}
    next_id = 1000

    # Everything on the timeline is measured in whole frames at the sequence
    # rate; per-clip source times use that clip's own rate. Nothing is written
    # as a raw float, so no value can land between two frames.
    sequence_rate = sync.parse_rate(cameras[0]["frame_rate"])

    for row in cameras:
        fmt = _format_resource(resources, format_ids, row["frame_rate"])
        asset_id = f"r{next_id}"; next_id += 1
        camera_assets[row["file_id"]] = asset_id
        rate = sync.parse_rate(row["frame_rate"])
        attrs = {"id": asset_id, "name": os.path.basename(row["proxy_path"]),
                 "start": _fraction_time(_camera_start_seconds(row), rate),
                 "duration": _fraction_time(row["duration_seconds"], rate),
                 "hasVideo": "1", "format": fmt, "hasAudio": "1",
                 "audioSources": "1", "audioChannels": "2", "audioRate": "48000"}
        asset = ET.SubElement(resources, "asset", attrs)
        ET.SubElement(asset, "media-rep", kind="original-media",
                      src=Path(row["proxy_path"]).resolve().as_uri())
    for links in links_by_camera.values():
        for link in links:
            if link["sound_file_id"] in sound_assets:
                continue
            asset_id = f"r{next_id}"; next_id += 1
            sound_assets[link["sound_file_id"]] = asset_id
            attrs = {"id": asset_id, "name": os.path.basename(link["input_path"]),
                     "start": _fraction_time(link["sound_start_seconds"], sequence_rate),
                     "duration": _fraction_time(link["duration_seconds"], sequence_rate),
                     "hasAudio": "1", "audioSources": "1",
                     "audioChannels": str(link["audio_channels"] or 1),
                     "audioRate": "48000"}
            asset = ET.SubElement(resources, "asset", attrs)
            ET.SubElement(asset, "media-rep", kind="original-media",
                          src=Path(link["input_path"]).resolve().as_uri())

    event = ET.SubElement(fcpxml, "event", name=f"{day['project']} {day['name']}")
    project = ET.SubElement(event, "project", name=f"{day['name']} DAILIES STRINGOUT")
    sequence_format = _format_resource(resources, format_ids, cameras[0]["frame_rate"])
    total_frames = sum(_frames(row["duration_seconds"], sequence_rate) for row in cameras)
    sequence = ET.SubElement(
        project, "sequence", format=sequence_format,
        duration=_fraction_time(total_frames / float(sequence_rate), sequence_rate),
        tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")
    offset_frames = 0
    for row in cameras:
        links = links_by_camera[row["file_id"]]
        rate = sync.parse_rate(row["frame_rate"])
        camera_start = _camera_start_seconds(row)
        accepted = row["sync_status"] in ("matched", "multiple") and bool(links)
        clip = ET.SubElement(
            spine, "asset-clip", name=os.path.splitext(os.path.basename(row["rel_path"]))[0],
            ref=camera_assets[row["file_id"]],
            offset=_fraction_time(offset_frames / float(sequence_rate), sequence_rate),
            start=_fraction_time(camera_start, rate),
            duration=_fraction_time(row["duration_seconds"], rate),
            srcEnable="video" if accepted else "all", audioRole="dialogue.camera",
        )
        for lane, link in enumerate(links, 1):
            trim = max(0.0, -float(link["offset_seconds"]))
            placement = camera_start + max(0.0, float(link["offset_seconds"]))
            duration = max(1.0 / float(rate), float(link["overlap_seconds"]))
            ET.SubElement(
                clip, "asset-clip", name=os.path.splitext(os.path.basename(link["rel_path"]))[0],
                ref=sound_assets[link["sound_file_id"]], lane=str(-lane),
                offset=_fraction_time(placement, rate),
                start=_fraction_time(float(link["sound_start_seconds"]) + trim, rate),
                duration=_fraction_time(duration, rate), srcEnable="audio",
                audioRole="dialogue.production",
            )
        marker_value = (f"{_scene_take(row, links)} · SYNC "
                        f"{(row['sync_method'] or 'timecode').upper()}" if accepted else
                        f"SYNC REVIEW · {(row['sync_status'] or 'NOT RUN').upper()}")
        ET.SubElement(clip, "marker", start=_fraction_time(camera_start, rate),
                      duration=_frame_duration(row["frame_rate"]), value=marker_value)
        offset_frames += _frames(row["duration_seconds"], sequence_rate)

    # Browser clips make every camera proxy easy to find outside the stringout.
    for row in cameras:
        rate = sync.parse_rate(row["frame_rate"])
        ET.SubElement(event, "asset-clip",
                      name=os.path.splitext(os.path.basename(row["rel_path"]))[0],
                      ref=camera_assets[row["file_id"]],
                      format=_format_resource(resources, format_ids, row["frame_rate"]),
                      start=_fraction_time(_camera_start_seconds(row), rate),
                      duration=_fraction_time(row["duration_seconds"], rate),
                      audioRole="dialogue.camera")
    ET.indent(fcpxml, space="  ")
    body = ET.tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def build_ale(day, cameras, links_by_camera):
    output = io.StringIO(newline="")
    fps_counts = Counter(round(float(sync.parse_rate(row["frame_rate"])), 3)
                         for row in cameras)
    heading_fps = fps_counts.most_common(1)[0][0]
    output.write("Heading\nFIELD_DELIM\tTABS\nVIDEO_FORMAT\t1080\n"
                 f"AUDIO_FORMAT\t48khz\nFPS\t{heading_fps:.3f}\n\nColumn\n")
    columns = ["Name", "Tape", "Start", "End", "FPS", "Tracks", "Scene", "Take",
               "SoundRoll", "Comments", "Good", "Source File", "Proxy Path",
               "Sync Status", "Sync Method", "Sync Offset"]
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(columns)
    output.write("Data\n")
    for row in cameras:
        links = links_by_camera[row["file_id"]]
        first = links[0] if links else None
        offsets = sorted({round(float(link["offset_seconds"]), 6) for link in links})
        writer.writerow([
            os.path.splitext(os.path.basename(row["rel_path"]))[0],
            row["roll"] or row["card_label"], row["start_timecode"],
            _timecode_out(row), f"{float(sync.camera_timecode_rate(row)):.3f}",
            "VA", _safe_text(first["scene"] if first else ""),
            _safe_text(first["take_name"] if first else ""),
            _safe_text(first["sound_roll"] if first else ""),
            _safe_text(first["notes"] if first else row["sync_error"]),
            "1" if first and first["circled"] else "0",
            row["original_path"], row["proxy_path"], row["sync_status"] or "not run",
            row["sync_method"] or "", ", ".join(f"{value:+.6f}" for value in offsets),
        ])
    return output.getvalue()


def build_manifest(day, primary, bundle, cameras, links_by_camera):
    day_root = os.path.join(primary["path"], day["project"], day["name"])
    items = []
    for row in cameras:
        links = links_by_camera[row["file_id"]]
        daily = db.q1("SELECT * FROM dailies WHERE camera_file_id=?", (row["file_id"],))
        items.append({
            "clip": os.path.splitext(os.path.basename(row["rel_path"]))[0],
            "roll": row["roll"] or row["card_label"],
            "duration_seconds": row["duration_seconds"],
            "start_timecode": row["start_timecode"], "frame_rate": row["frame_rate"],
            "original": {"path": _relative(row["original_path"], day_root),
                         "xxhash64": row["src_hash"]},
            "proxy": {"path": _relative(row["proxy_path"], day_root),
                      "xxhash64": row["proxy_hash"]},
            "daily": {"path": _relative(daily["output_path"], bundle),
                      "xxhash64": daily["output_hash"]},
            "sync": {"status": row["sync_status"], "method": row["sync_method"],
                     "waveform_score": row["waveform_score"],
                     "waveform_margin": row["waveform_margin"],
                     "error": row["sync_error"],
                     "links": [{"path": _relative(link["input_path"], day_root),
                                "xxhash64": link["src_hash"],
                                "scene": link["scene"], "take": link["take_name"],
                                "offset_seconds": link["offset_seconds"],
                                "overlap_seconds": link["overlap_seconds"]}
                               for link in links]},
        })
    return json.dumps({"schema": "cf-smoker-handoff-1", "generated_at": db.now(),
                       "project": day["project"], "day": day["name"],
                       "shoot_date": day["shoot_date"],
                       "primary_destination": primary["label"], "clips": items},
                      indent=2, sort_keys=True) + "\n"


def build_readme(day, cameras, links_by_camera):
    unresolved = [row for row in cameras
                  if row["sync_status"] not in ("matched", "multiple")]
    lines = [
        f"{day['project']} · {day['name']} EDITORIAL HANDOFF",
        "=" * 68, "",
        "1. In DaVinci Resolve, choose File > Import > Timeline and select",
        f"   {day['name']}_RESOLVE.fcpxml.",
        "2. Leave automatic source-clip import enabled. The timeline references the",
        "   ProRes proxies on the primary drive and accepted production WAV files.",
        "3. Import the ALE into the Media Pool if you want scene/take/notes columns.",
        "4. Before online, relink by reel + source timecode to CAMERA originals.",
        "   The JSON file records every original/proxy/sound path and xxHash64.",
        "5. CHECKSUMS.txt covers every handoff document and review movie.", "",
        "REVIEW DAILIES", "--------------",
        "DAILIES contains 720p H.264 viewing copies. The slate shows filename,",
        "roll, scene/take, source start TC and whether production sound was accepted.",
        "The movies are for review only; edit/conform from the ProRes proxies.", "",
        f"SYNC: {len(cameras) - len(unresolved)}/{len(cameras)} camera clips accepted",
    ]
    if unresolved:
        lines += ["", "NEEDS EDITORIAL REVIEW", "----------------------"]
        lines += [f"- {os.path.basename(row['rel_path'])}: "
                  f"{(row['sync_status'] or 'not run').upper()}"
                  + (f" — {_safe_text(row['sync_error'])}" if row["sync_error"] else "")
                  for row in unresolved]
    else:
        lines += ["No sync exceptions were present when this package was generated."]
    lines += ["", "This export never modifies originals, proxies or format clearance.",
              "", "Generated by Second Hand Smoke · A CnF Works Project", ""]
    return "\n".join(lines)


class Job:
    def __init__(self, day_id, rebuild=False):
        self.day_id = day_id
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
        percent = (min(100, self.seconds_done / self.seconds_total * 100)
                   if self.seconds_total else
                   (min(100, self.files_done / self.files_total * 100)
                    if self.files_total else (100 if self.state == "done" else 0)))
        return {"job_type": "handoff", "mode": "handoff", "day_id": self.day_id,
                "state": self.state, "phase": self.phase, "message": self.message,
                "current_file": self.current_file, "files_total": self.files_total,
                "files_done": self.files_done, "seconds_total": self.seconds_total,
                "seconds_done": self.seconds_done, "output_bytes": self.output_bytes,
                "bytes_total": 0, "bytes_done": self.output_bytes,
                "percent": round(percent, 1), "errors": self.errors[-20:],
                "warnings": self.warnings[-20:], "started_epoch": self.started_epoch,
                "finished_epoch": self.finished_epoch}

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
                self.message = (f"Handoff stopped with {len(self.errors)} problem(s) — "
                                "the prior phases are unaffected")
            elif self.warnings:
                self.message = f"Handoff ready with {len(self.warnings)} warning(s)"
            else:
                self.message = "Resolve handoff and review dailies ready"
        except HandoffCancelled:
            self.state = "cancelled"
            self.message = "Handoff stopped safely — media is unaffected"
            prior = db.q1("SELECT * FROM handoff_exports WHERE day_id=?", (self.day_id,))
            if prior:
                _save_export(self.day_id, state="cancelled",
                             catalog_signature=prior["catalog_signature"],
                             bundle_path=prior["bundle_path"], error="Stopped by operator")
        except Exception:
            self.state = "failed"
            self.message = "Handoff job crashed — media and format clearance are unaffected"
            self.errors.append(traceback.format_exc(limit=6))
            prior = db.q1("SELECT * FROM handoff_exports WHERE day_id=?", (self.day_id,))
            _save_export(self.day_id, state="failed",
                         catalog_signature=prior["catalog_signature"] if prior else None,
                         bundle_path=prior["bundle_path"] if prior else None,
                         error=self.errors[-1][-2000:])
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
        day = db.q1("SELECT * FROM days WHERE id=?", (self.day_id,))
        primary = _primary(self.day_id)
        if not day or not primary:
            raise RuntimeError("Mark one destination primary before building the handoff")
        cameras = _camera_rows(self.day_id, primary["id"])
        if not cameras:
            raise RuntimeError("No camera clips were catalogued on this day")
        links_by_camera = {row["file_id"]: _sound_links(row["file_id"], primary["id"])
                           for row in cameras}
        bundle = bundle_path_for(primary["path"], day["project"], day["name"])
        os.makedirs(bundle, exist_ok=True)
        incomplete = os.path.join(bundle, "EXPORT_INCOMPLETE.txt")
        _atomic_write(incomplete, "This handoff is incomplete. Run Phase 6 again.\n")
        signature = _catalog_signature(day, cameras, links_by_camera)
        unresolved = sum(row["sync_status"] not in ("matched", "multiple")
                         for row in cameras)
        _save_export(self.day_id, state="running", catalog_signature=signature,
                     bundle_path=bundle, total_camera=len(cameras), unresolved=unresolved)
        self.files_total = len(cameras)
        self.seconds_total = sum(float(row["duration_seconds"]) for row in cameras)
        self.job_row = db.ex(
            "INSERT INTO jobs(card_id,job_type,state,started_at) VALUES (?,'handoff','running',?)",
            (cameras[0]["card_id"], db.now()),
        )
        counts = Counter(os.path.splitext(os.path.basename(row["rel_path"]))[0].casefold()
                         for row in cameras)
        for row in cameras:
            if self.cancel.is_set():
                raise HandoffCancelled()
            duration = float(row["duration_seconds"])
            base_seconds = self.seconds_done
            self.current_file = os.path.basename(row["rel_path"])
            links = links_by_camera[row["file_id"]]
            stem = os.path.splitext(os.path.basename(row["rel_path"]))[0].casefold()
            disambiguator = (hashlib.sha1(row["rel_path"].encode()).hexdigest()[:8]
                             if counts[stem] > 1 else None)
            output = daily_path_for(bundle, row["roll"] or row["card_label"],
                                    row["rel_path"], disambiguator)
            source_signature = _camera_signature(row, links)
            prior = db.q1("SELECT * FROM dailies WHERE camera_file_id=?", (row["file_id"],))
            if not self.rebuild and prior and prior["state"] == "ready" \
                    and prior["profile"] == PROFILE \
                    and prior["source_signature"] == source_signature \
                    and prior["output_path"] == output and os.path.isfile(output) \
                    and os.path.getsize(output) == prior["output_size"] \
                    and prior["output_hash"]:
                try:
                    if _hash_file(row["proxy_path"]) != row["proxy_hash"]:
                        raise ValueError("source proxy checksum changed")
                    if _hash_file(output) != prior["output_hash"]:
                        raise ValueError("review checksum changed")
                    _validated_daily(output, duration)
                except Exception as exc:
                    self.warnings.append(
                        f"{self.current_file}: existing daily failed validation ({exc}); rebuilt"
                    )
                else:
                    self.output_bytes += prior["output_size"] or 0
                    self.seconds_done = base_seconds + duration
                    self.files_done += 1
                    continue
            partial = output[:-4] + ".partial.mp4"
            slate = output[:-4] + ".slate.partial.png"
            os.makedirs(os.path.dirname(output), exist_ok=True)
            for path in (partial, slate):
                if os.path.exists(path):
                    os.remove(path)
            _save_daily(row["file_id"], state="running", source_signature=source_signature,
                        input_path=row["proxy_path"], output_path=output,
                        duration_seconds=duration, external_audio=len(links))
            try:
                if _hash_file(row["proxy_path"]) != row["proxy_hash"]:
                    raise ValueError("source proxy checksum changed; check or rebuild proxies")
                self.phase = "Drawing review slate"
                render_slate(slate, row, links)
                self.phase = "Encoding review daily"
                transcode_daily(
                    row, links, partial, slate,
                    lambda seconds, base=base_seconds, length=duration: setattr(
                        self, "seconds_done", base + min(max(seconds, 0), length)),
                    self.cancel, self._set_process,
                )
                if self.cancel.is_set():
                    raise HandoffCancelled()
                self.phase = "Checking review daily"
                _validated_daily(partial, duration)
                _flush_file(partial)
                output_hash = _hash_file(partial)
                os.replace(partial, output)
                size = os.path.getsize(output)
                _save_daily(row["file_id"], state="ready", source_signature=source_signature,
                            input_path=row["proxy_path"], output_path=output,
                            output_size=size, output_hash=output_hash,
                            duration_seconds=duration, external_audio=len(links), error=None)
                self.output_bytes += size
            except HandoffCancelled:
                _save_daily(row["file_id"], state="cancelled",
                            source_signature=source_signature, input_path=row["proxy_path"],
                            output_path=output, duration_seconds=duration,
                            external_audio=len(links), error="Stopped by operator")
                raise
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                self.errors.append(f"{self.current_file}: {error}")
                _save_daily(row["file_id"], state="failed",
                            source_signature=source_signature, input_path=row["proxy_path"],
                            output_path=output, duration_seconds=duration,
                            external_audio=len(links), error=error)
            finally:
                for path in (partial, slate):
                    if os.path.exists(path):
                        os.remove(path)
            self.seconds_done = base_seconds + duration
            self.files_done += 1

        if self.errors:
            _save_export(self.day_id, state="failed", catalog_signature=signature,
                         bundle_path=bundle, total_camera=len(cameras),
                         ready_dailies=len(cameras) - len(self.errors), unresolved=unresolved,
                         error="; ".join(self.errors)[-2000:])
            return
        self.phase = "Writing Resolve package"
        paths = {
            "fcpxml": os.path.join(bundle, f"{day['name']}_RESOLVE.fcpxml"),
            "ale": os.path.join(bundle, f"{day['name']}_METADATA.ale"),
            "json": os.path.join(bundle, f"{day['name']}_MEDIA.json"),
            "readme": os.path.join(bundle, "README_HANDOFF.txt"),
            "checksums": os.path.join(bundle, "CHECKSUMS.txt"),
        }
        _atomic_write(paths["fcpxml"], build_fcpxml(day, cameras, links_by_camera))
        _atomic_write(paths["ale"], build_ale(day, cameras, links_by_camera))
        _atomic_write(paths["json"], build_manifest(
            day, primary, bundle, cameras, links_by_camera))
        _atomic_write(paths["readme"], build_readme(day, cameras, links_by_camera))
        checksum_paths = [paths[name] for name in ("fcpxml", "ale", "json", "readme")]
        checksum_paths += [db.q1("SELECT output_path FROM dailies WHERE camera_file_id=?",
                                 (row["file_id"],))["output_path"] for row in cameras]
        checksums = "".join(f"{_hash_file(path)}  {_relative(path, bundle)}\n"
                            for path in sorted(checksum_paths))
        _atomic_write(paths["checksums"], checksums)
        if os.path.exists(incomplete):
            os.remove(incomplete)
        _save_export(self.day_id, state="ready", catalog_signature=signature,
                     bundle_path=bundle, fcpxml_path=paths["fcpxml"],
                     ale_path=paths["ale"], json_path=paths["json"],
                     readme_path=paths["readme"], checksums_path=paths["checksums"],
                     total_camera=len(cameras), ready_dailies=len(cameras),
                     unresolved=unresolved, error=None)
        self.phase = "Finished"
        self.current_file = ""


def start(day_id, rebuild=False):
    global _current, _last_snapshot
    with _lock:
        if _current is not None:
            return None, "Another handoff job is already running"
        job = Job(day_id, rebuild=rebuild)
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
        """SELECT COUNT(f.id) total,
                  SUM(CASE WHEN d.state='ready' AND d.profile=? THEN 1 ELSE 0 END) ready,
                  SUM(CASE WHEN d.state IN ('failed','cancelled') THEN 1 ELSE 0 END) problems,
                  SUM(CASE WHEN d.state='running' THEN 1 ELSE 0 END) running,
                  SUM(CASE WHEN d.state='ready' THEN d.output_size ELSE 0 END) output_bytes,
                  MAX(d.updated_at) updated_at
           FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN dailies d ON d.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'""", (PROFILE, day_id),
    )
    export = db.q1("SELECT * FROM handoff_exports WHERE day_id=?", (day_id,))
    unresolved = db.q1(
        """SELECT COUNT(*) count FROM files f JOIN cards c ON c.id=f.card_id
           LEFT JOIN sync_status s ON s.camera_file_id=f.id
           WHERE c.day_id=? AND f.category='video'
             AND COALESCE(s.status,'') NOT IN ('matched','multiple')""", (day_id,),
    )["count"]
    state = export["state"] if export else "not_built"
    daily_files = db.q(
        """SELECT d.output_path,d.output_size FROM dailies d
           JOIN files f ON f.id=d.camera_file_id JOIN cards c ON c.id=f.card_id
           WHERE c.day_id=? AND f.category='video' AND d.state='ready'""", (day_id,),
    )
    files_present = all(item["output_path"] and os.path.isfile(item["output_path"])
                        and os.path.getsize(item["output_path"]) == item["output_size"]
                        for item in daily_files)
    documents_present = bool(export and all(
        export[name] and os.path.isfile(export[name]) for name in
        ("fcpxml_path", "ale_path", "json_path", "readme_path", "checksums_path")
    ))
    package_ready = bool(export and state == "ready" and
                         (row["ready"] or 0) == (row["total"] or 0)
                         and files_present and documents_present)
    if export and state == "ready" and not package_ready:
        state = "missing"
    return {"total": row["total"] or 0, "ready": row["ready"] or 0,
            "problems": (row["problems"] or 0) + (1 if state == "missing" else 0),
            "running": row["running"] or 0,
            "output_bytes": row["output_bytes"] or 0,
            "updated_at": export["updated_at"] if export else row["updated_at"],
            "state": state, "package_ready": package_ready,
            "bundle_path": export["bundle_path"] if export else None,
            "fcpxml_path": export["fcpxml_path"] if export else None,
            "unresolved": unresolved, "error": export["error"] if export else None,
            "available": ok, "unavailable_reason": reason,
            "profile": PROFILE_LABEL, "fcpxml_version": FCPXML_VERSION}

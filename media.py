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

"""Media metadata extraction and production sound-report ingestion.

ffprobe reads container/stream metadata without decoding or modifying media.
Sound-report CSV data is layered on top because scene, take and notes are often
more complete in the recorder report than in the BWF tags exposed by ffprobe.
Metadata is useful but never part of the byte-integrity format gate.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET

import db


FFPROBE = os.environ.get("DIT_FFPROBE") or shutil.which("ffprobe")

try:
    PROBE_TIMEOUT = max(5, int(os.environ.get("DIT_PROBE_TIMEOUT", "90")))
except ValueError:
    PROBE_TIMEOUT = 90


def refresh_tools():
    """Re-detect ffprobe. Lets a mid-shoot install take effect without a restart."""
    global FFPROBE
    FFPROBE = os.environ.get("DIT_FFPROBE") or shutil.which("ffprobe")
    return FFPROBE


def _number(value, kind=float):
    try:
        if value in (None, "", "N/A"):
            return None
        return kind(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _tags(section):
    return {str(k).casefold(): v for k, v in (section.get("tags") or {}).items()}


def _first(mapping, *names):
    for name in names:
        value = mapping.get(name.casefold())
        if value not in (None, ""):
            return str(value).strip()
    return None


def _duration(format_data, streams):
    value = _number(format_data.get("duration"))
    if value is not None:
        return value
    durations = [_number(stream.get("duration")) for stream in streams]
    durations = [value for value in durations if value is not None]
    return max(durations) if durations else None


def parse_probe(payload):
    """Normalize ffprobe JSON into the fields used by the catalog."""
    streams = payload.get("streams") or []
    format_data = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video and not audio:
        raise ValueError("ffprobe found no video or audio stream")
    primary = video or audio or (streams[0] if streams else {})

    combined_tags = {}
    for section in (format_data, *streams):
        combined_tags.update(_tags(section))

    frame_rate = None
    if video:
        for key in ("avg_frame_rate", "r_frame_rate"):
            candidate = video.get(key)
            if candidate and candidate not in ("0/0", "N/A"):
                frame_rate = candidate
                break

    sample_rate = _number((audio or {}).get("sample_rate"), int)
    bits = _number((audio or {}).get("bits_per_raw_sample"), int)
    if bits is None:
        bits = _number((audio or {}).get("bits_per_sample"), int)

    time_reference = _number(_first(
        combined_tags, "time_reference", "time reference", "bwf_time_reference"
    ), int)
    timecode = _first(combined_tags, "timecode", "start_timecode", "start tc")
    timecode_rate = _first(
        combined_tags, "timecode_rate", "timecode rate", "master_speed", "speed"
    ) or (frame_rate if video else None)
    timecode_flag = _first(
        combined_tags, "timecode_flag", "timecode flag", "timecode_format"
    )
    drop_frame = None
    if timecode and ";" in timecode:
        drop_frame = 1
    elif timecode_flag:
        normalized_flag = re.sub(r"[^a-z0-9]+", "", timecode_flag.casefold())
        drop_frame = 0 if "ndf" in normalized_flag else int("df" in normalized_flag)

    return {
        "status": "ready",
        "media_type": "video" if video else ("audio" if audio else None),
        "format_name": format_data.get("format_name"),
        "codec_name": primary.get("codec_name"),
        "codec_long_name": primary.get("codec_long_name"),
        "duration_seconds": _duration(format_data, streams),
        "start_timecode": timecode,
        "reel_name": _first(combined_tags, "reel_name", "reel", "tape", "folder"),
        "width": _number((video or {}).get("width"), int),
        "height": _number((video or {}).get("height"), int),
        "frame_rate": frame_rate,
        "timecode_rate": timecode_rate,
        "timecode_drop_frame": drop_frame,
        "audio_channels": _number((audio or {}).get("channels"), int),
        "sample_rate": sample_rate,
        "bit_depth": bits,
        "bwf_time_reference": time_reference,
        "bwf_sample_rate": sample_rate if time_reference is not None else None,
        "origination_date": _first(
            combined_tags, "origination_date", "origination date", "date"
        ),
        "origination_time": _first(
            combined_tags, "origination_time", "origination time", "creation_time"
        ),
        "scene": _first(combined_tags, "scene", "scene_name", "slate"),
        "take_name": _first(combined_tags, "take", "take_number", "take name"),
        "notes": _first(combined_tags, "notes", "note", "comment", "description"),
        "circled": None,
        "sound_roll": _first(combined_tags, "sound_roll", "roll", "tape", "folder"),
        "sound_report_path": None,
        "raw_probe_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "error": None,
    }


def probe_file(path):
    if not FFPROBE:
        raise FileNotFoundError("ffprobe is not installed")
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out after {PROBE_TIMEOUT}s") from exc
    except OSError as exc:
        raise RuntimeError(f"ffprobe could not start: {exc}") from exc
    if result.returncode:
        message = (result.stderr or "ffprobe could not read the file").strip()
        raise RuntimeError(message[-1000:])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc


# iXML is read straight off a card, and the standard library documents
# xml.etree as vulnerable to entity-expansion denial of service ("billion
# laughs"). That attack needs a DTD or an ENTITY declaration, and no recorder
# firmware writes either into an iXML chunk, so refusing them costs nothing
# and removes the attack rather than bounding it.
_DTD_MARKER = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def _ixml_values(payload):
    data = payload.rstrip(b"\x00")
    if _DTD_MARKER.search(data):
        return {}
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        return {}
    values = {}
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1].upper()
        text = (node.text or "").strip()
        if text and name not in values:
            values[name] = text
    rate = next((values[key] for key in
                 ("TIMECODE_RATE", "MASTER_SPEED", "CURRENT_SPEED")
                 if values.get(key)), None)
    flag = values.get("TIMECODE_FLAG") or values.get("TIMECODE_FORMAT")
    drop = None
    if flag:
        normalized = re.sub(r"[^a-z0-9]+", "", flag.casefold())
        drop = 0 if "ndf" in normalized else int("df" in normalized)
    return {"timecode_rate": rate, "timecode_drop_frame": drop}


def read_wave_metadata(path):
    """Read the small BEXT/iXML chunks needed for deterministic sound TC.

    ffprobe exposes these fields for many recorders, but not every firmware
    writes iXML in exactly the same way. This reader never touches audio data
    and gives the sync engine the recorder's explicit TC rate when available.
    """
    result = {}
    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as handle:
            header = handle.read(12)
            if len(header) != 12 or header[:4] not in (b"RIFF", b"RF64", b"BW64", b"RIFX") \
                    or header[8:12] != b"WAVE":
                return result
            endian = ">" if header[:4] == b"RIFX" else "<"
            while handle.tell() + 8 <= file_size:
                chunk_id = handle.read(4)
                raw_size = handle.read(4)
                if len(raw_size) != 4:
                    break
                chunk_size = struct.unpack(endian + "I", raw_size)[0]
                chunk_start = handle.tell()
                if chunk_id == b"bext":
                    payload = handle.read(min(chunk_size, 1024))
                    if len(payload) >= 346:
                        low, high = struct.unpack_from(endian + "II", payload, 338)
                        result["bwf_time_reference"] = low | (high << 32)
                        date = payload[320:330].decode("ascii", "ignore").strip(" \x00")
                        clock = payload[330:338].decode("ascii", "ignore").strip(" \x00")
                        if date:
                            result["origination_date"] = date
                        if clock:
                            result["origination_time"] = clock
                elif chunk_id.lower() == b"ixml" and chunk_size <= 4 * 1024 * 1024:
                    result.update({key: value for key, value in
                                   _ixml_values(handle.read(chunk_size)).items()
                                   if value is not None})
                if chunk_size == 0xFFFFFFFF:
                    break
                next_chunk = chunk_start + chunk_size + (chunk_size & 1)
                if next_chunk <= chunk_start or next_chunk > file_size:
                    break
                handle.seek(next_chunk)
    except OSError:
        return {}
    return result


METADATA_COLUMNS = (
    "status", "media_type", "format_name", "codec_name", "codec_long_name",
    "duration_seconds", "start_timecode", "reel_name", "width", "height",
    "frame_rate", "timecode_rate", "timecode_drop_frame",
    "audio_channels", "sample_rate", "bit_depth",
    "bwf_time_reference", "bwf_sample_rate", "origination_date",
    "origination_time", "scene", "take_name", "notes", "circled",
    "sound_roll", "sound_report_path", "raw_probe_json", "error",
)


def _save_metadata(file_id, values):
    values = {key: values.get(key) for key in METADATA_COLUMNS}
    columns = ",".join(METADATA_COLUMNS)
    placeholders = ",".join("?" for _ in METADATA_COLUMNS)
    updates = ",".join(f"{key}=excluded.{key}" for key in METADATA_COLUMNS)
    db.ex(
        f"""INSERT INTO media_metadata (file_id,{columns},updated_at)
            VALUES (?,{placeholders},?)
            ON CONFLICT(file_id) DO UPDATE SET {updates},updated_at=excluded.updated_at""",
        (file_id, *(values[key] for key in METADATA_COLUMNS), db.now()),
    )


def _normal_header(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


HEADER_ALIASES = {
    "filename": {"file", "file name", "filename", "clip", "clip name"},
    "scene": {"scene", "scene name", "slate"},
    "take_name": {"take", "take name", "take number", "take no", "take #"},
    "start_timecode": {"tc", "timecode", "time code", "start tc", "start timecode"},
    "notes": {"note", "notes", "comments", "comment"},
    "circled": {"circle", "circled", "good", "print"},
    "sound_roll": {"roll", "sound roll", "tape", "folder", "record folder"},
}


def _header_map(headers):
    normalized = {_normal_header(value): value for value in headers if value}
    result = {}
    for field, aliases in HEADER_ALIASES.items():
        match = next((normalized[name] for name in map(_normal_header, aliases)
                      if name in normalized), None)
        if match:
            result[field] = match
    return result


def _read_report(path):
    with open(path, "rb") as report_file:
        raw = report_file.read()
    # latin-1 decodes any byte sequence without raising, so it can never be
    # rejected on encoding grounds alone. Each candidate encoding is instead
    # judged by whether it produces a header row we recognise; the first one
    # that does is the right one.
    for encoding in ("utf-8-sig", "utf-16", "mac_roman", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        parsed = _parse_report_text(text)
        if parsed[0]:
            return parsed
    return None, []


def _parse_report_text(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for delimiter in (",", "\t", ";"):
            headers = next(csv.reader([line], delimiter=delimiter), [])
            mapping = _header_map(headers)
            if "filename" in mapping and any(
                    field in mapping for field in ("scene", "take_name", "start_timecode")):
                reader = csv.DictReader(lines[index:], delimiter=delimiter)
                return mapping, list(reader)
    return None, []


def _truthy(value):
    if value is None or str(value).strip() == "":
        return None
    return 1 if str(value).strip().casefold() in {
        "1", "true", "yes", "y", "circle", "circled", "good", "print", "*", "x"
    } else 0


def _match_audio(name, by_name, by_stem):
    base = os.path.basename((name or "").strip()).casefold()
    if not base:
        return None
    exact = by_name.get(base)
    if exact:
        return exact
    stem = os.path.splitext(base)[0]
    exact = by_stem.get(stem)
    if exact:
        return exact
    # Monophonic splits often append a track suffix to the take name.
    matches = [row for candidate, row in by_stem.items()
               if candidate.startswith(stem + "_") or candidate.startswith(stem + "-")]
    return matches[0] if len(matches) == 1 else None


def _primary_destination(day_id):
    return db.q1(
        """SELECT * FROM destinations WHERE day_id=? AND role='primary'
           ORDER BY id LIMIT 1""", (day_id,)
    )


def resolve_media_path(card, file_row):
    """Find a readable copy of this file: the mounted source card if it's
    still there, otherwise the verified primary-drive copy.

    Every other post-offload phase (proxies, waveform, handoff) reads from
    the verified primary destination rather than the card, which is exactly
    why they still work after a card has been cleared and pulled. Metadata
    and sound-report reads used to be the one place that insisted on the
    card itself, which meant re-running "Read metadata" after a card was
    safely unmounted silently produced nothing. This restores the same
    fallback everyone else already relies on. Returns None if neither a
    mounted card nor a verified copy is available; never touches source
    media except with a plain read.
    """
    if os.path.isdir(card["source_path"]):
        candidate = os.path.join(card["source_path"], file_row["rel_path"])
        if os.path.isfile(candidate):
            return candidate
    primary = _primary_destination(card["day_id"])
    if primary:
        copy = db.q1(
            """SELECT dest_path FROM copies
               WHERE file_id=? AND destination_id=? AND state='verified'""",
            (file_row["id"], primary["id"]),
        )
        if copy and copy["dest_path"] and os.path.isfile(copy["dest_path"]):
            return copy["dest_path"]
    return None


def import_sound_reports(card_id, card):
    audio = db.q("SELECT * FROM files WHERE card_id=? AND category='audio'", (card_id,))
    if not audio:
        return {"reports": 0, "rows": 0, "matched": 0}
    by_name = {os.path.basename(row["rel_path"]).casefold(): row for row in audio}
    by_stem = {os.path.splitext(name)[0]: row for name, row in by_name.items()}

    reports = db.q("""SELECT * FROM files
                       WHERE card_id=? AND category='sidecar' AND lower(rel_path) LIKE '%.csv'""",
                   (card_id,))
    report_count = row_count = matched_count = 0
    for report in reports:
        full_path = resolve_media_path(card, report)
        if not full_path:
            continue
        try:
            mapping, rows = _read_report(full_path)
        except OSError:
            continue
        if not mapping:
            continue
        report_count += 1
        for values in rows:
            row_count += 1
            target = _match_audio(values.get(mapping["filename"]), by_name, by_stem)
            if not target:
                continue
            current = db.q1("SELECT * FROM media_metadata WHERE file_id=?", (target["id"],))
            status = "ready" if current and current["status"] == "ready" else "partial"

            fields = {}
            for field in ("scene", "take_name", "start_timecode", "notes", "sound_roll"):
                header = mapping.get(field)
                value = str(values.get(header) or "").strip() if header else ""
                fields[field] = value or (current[field] if current else None)
            circle_header = mapping.get("circled")
            circled = _truthy(values.get(circle_header)) if circle_header else None
            if circled is None and current:
                circled = current["circled"]

            if current:
                metadata = {key: current[key] for key in METADATA_COLUMNS}
            else:
                metadata = {key: None for key in METADATA_COLUMNS}
            metadata.update(fields)
            metadata.update({
                "status": status,
                "media_type": metadata.get("media_type") or "audio",
                "circled": circled,
                "sound_report_path": report["rel_path"],
                "error": metadata.get("error"),
            })
            _save_metadata(target["id"], metadata)
            matched_count += 1
    return {"reports": report_count, "rows": row_count, "matched": matched_count}


def _analyze_card(card_id):
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return {"total": 0, "ready": 0, "errors": ["Unknown card"],
                "sound_reports": 0, "sound_rows_matched": 0}

    mounted = os.path.isdir(card["source_path"])
    if not mounted and not _primary_destination(card["day_id"]):
        return {"total": 0, "ready": 0,
                "errors": ["Source card is not mounted, and no primary destination "
                           "is configured to read a verified copy from"],
                "sound_reports": 0, "sound_rows_matched": 0}

    # Stored links must never outlive the metadata they were calculated from.
    # Import locally to keep the metadata reader independently usable.
    import sync
    sync.invalidate_card(card_id)

    files = db.q("""SELECT * FROM files
                     WHERE card_id=? AND category IN ('video','audio')
                     ORDER BY rel_path""", (card_id,))
    errors = []
    ready = 0
    if not FFPROBE:
        errors.append("ffprobe is not installed; install ffmpeg to read media metadata")

    for file_row in files:
        if not FFPROBE:
            metadata = {key: None for key in METADATA_COLUMNS}
            metadata.update({
                "status": "unavailable", "media_type": file_row["category"],
                "error": "ffprobe is not installed",
            })
            _save_metadata(file_row["id"], metadata)
            continue
        path = resolve_media_path(card, file_row)
        if not path:
            metadata = {key: None for key in METADATA_COLUMNS}
            metadata.update({
                "status": "unavailable", "media_type": file_row["category"],
                "error": "Source card is unmounted and no verified copy was found",
            })
            _save_metadata(file_row["id"], metadata)
            errors.append(
                f"{file_row['rel_path']}: source card is unmounted and no verified "
                "copy is available"
            )
            continue
        try:
            metadata = parse_probe(probe_file(path))
            if file_row["category"] == "audio" and path.lower().endswith((".wav", ".wave")):
                wave = read_wave_metadata(path)
                for key, value in wave.items():
                    if value is not None:
                        metadata[key] = value
                if metadata.get("bwf_time_reference") is not None:
                    metadata["bwf_sample_rate"] = metadata.get("sample_rate")
            _save_metadata(file_row["id"], metadata)
            ready += 1
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            metadata = {key: None for key in METADATA_COLUMNS}
            metadata.update({
                "status": "error", "media_type": file_row["category"],
                "error": message[-1000:],
            })
            _save_metadata(file_row["id"], metadata)
            errors.append(f"{file_row['rel_path']}: {message}")

    sound = import_sound_reports(card_id, card)
    return {
        "total": len(files), "ready": ready, "errors": errors,
        "sound_reports": sound["reports"],
        "sound_rows": sound["rows"],
        "sound_rows_matched": sound["matched"],
    }


def analyze_card(card_id):
    import sync
    with sync.METADATA_LOCK:
        return _analyze_card(card_id)


def card_summary(card_id):
    if not FFPROBE:
        refresh_tools()
    row = db.q1("""SELECT
        COUNT(f.id) total,
        SUM(CASE WHEN m.status='ready' THEN 1 ELSE 0 END) ready,
        SUM(CASE WHEN m.status='partial' THEN 1 ELSE 0 END) partial,
        SUM(CASE WHEN m.status IN ('error','unavailable') THEN 1 ELSE 0 END) problems,
        SUM(CASE WHEN f.category='video' THEN m.duration_seconds ELSE 0 END) video_seconds,
        SUM(CASE WHEN f.category='audio' THEN m.duration_seconds ELSE 0 END) audio_seconds,
        MAX(m.updated_at) updated_at
        FROM files f LEFT JOIN media_metadata m ON m.file_id=f.id
        WHERE f.card_id=? AND f.category IN ('video','audio')""", (card_id,))
    return {
        "total": row["total"] or 0,
        "ready": row["ready"] or 0,
        "partial": row["partial"] or 0,
        "problems": row["problems"] or 0,
        "video_seconds": row["video_seconds"] or 0,
        "audio_seconds": row["audio_seconds"] or 0,
        "updated_at": row["updated_at"],
        "ffprobe_available": bool(FFPROBE),
    }

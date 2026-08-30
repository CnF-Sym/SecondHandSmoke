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

"""Self-contained end-to-end exercise of the ingest safety contract."""

import atexit
import getpass
import json
import os
import shutil
import struct
import tempfile
import time
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np


RIG = os.path.realpath(tempfile.mkdtemp(prefix="cf-smoker-rig-"))
if os.environ.get("DIT_KEEP_TEST_RIG") == "1":
    print(f"RIG_ROOT={RIG}")
else:
    atexit.register(shutil.rmtree, RIG, ignore_errors=True)

CARDS = os.path.join(RIG, "cards")
DRIVE_A = os.path.join(RIG, "DRIVE_A")
DRIVE_A_ALIAS = os.path.join(RIG, "DRIVE_A_ALIAS")
DRIVE_B = os.path.join(RIG, "DRIVE_B")
for folder in (CARDS, DRIVE_A, DRIVE_A_ALIAS, DRIVE_B):
    os.makedirs(folder)

os.environ["DIT_DB"] = os.path.join(RIG, "catalog.db")
os.environ["DIT_SCAN_ROOT"] = CARDS
os.environ["DIT_MIN_COPIES"] = "1"  # Code must still enforce the safety floor of two.


def write_fixture(relative, seed, size=24 * 1024):
    path = os.path.join(CARDS, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    block = bytes(((seed + i) % 251) + 1 for i in range(1024))
    with open(path, "wb") as fh:
        remaining = size
        while remaining:
            chunk = block[:min(len(block), remaining)]
            fh.write(chunk)
            remaining -= len(chunk)
    return path


def write_text_fixture(relative, text):
    path = os.path.join(CARDS, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def write_bwf_fixture(relative, time_reference, rate="24000/1001", drop="NDF"):
    """A tiny valid WAVE carrying the BEXT/iXML timing fields Phase 4 uses."""
    path = os.path.join(CARDS, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bext = bytearray(602)
    bext[320:330] = b"2026-01-16"
    bext[330:338] = b"23:59:58"
    struct.pack_into("<II", bext, 338, time_reference & 0xFFFFFFFF,
                     time_reference >> 32)
    ixml = (f"<BWFXML><TIMECODE_RATE>{rate}</TIMECODE_RATE>"
            f"<TIMECODE_FLAG>{drop}</TIMECODE_FLAG></BWFXML>").encode()
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 144000, 3, 24)

    def chunk(name, payload):
        return name + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")

    body = chunk(b"fmt ", fmt) + chunk(b"bext", bytes(bext)) + chunk(b"iXML", ixml) + chunk(b"data", b"")
    with open(path, "wb") as handle:
        handle.write(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body)
    return path


CAMERA_FILES = [
    ("CONTENTS/CLIPS001/A001C001_260116AB_CANON/A001C001_260116AB_CANON.MXF", 1, 32 * 1024),
    ("CONTENTS/CLIPS001/A001C001_260116AB_CANON/A001C001_260116AB_CANON.XML", 2, 800),
    ("CONTENTS/CLIPS001/A001C002_260116AB_CANON/A001C002_260116AB_CANON.MXF", 3, 36 * 1024),
    ("CONTENTS/CLIPS001/A001C002_260116AB_CANON/A001C002_260116AB_CANON.XML", 4, 900),
    ("CONTENTS/CLIPS001/A001C003_260116AB_CANON/A001C003_260116AB_CANON.MXF", 5, 28 * 1024),
    ("CONTENTS/CLIPS001/A001C003_260116AB_CANON/A001C003_260116AB_CANON.XML", 6, 700),
    ("CONTENTS/CANON.MIF", 7, 512),
    ("INDEX.MIF", 8, 512),
]
for rel, seed, size in CAMERA_FILES:
    write_fixture(os.path.join("A001_CFEXPRESS", rel), seed, size)
write_fixture("A001_CFEXPRESS/.DS_Store", 9, 256)

for index in range(3):
    write_fixture(f"SOUND_01/CRACK_R01/CRACK_01_{index + 1:03}.WAV", 20 + index, 12 * 1024)
write_text_fixture(
    "SOUND_01/CRACK_R01/SOUND_REPORT.CSV",
    "Project,PROJECT_01\n"
    "Mixer,Test Mixer\n"
    "File Name,Scene,Take,Start TC,Notes,Circled,Sound Roll\n"
    "CRACK_01_001.WAV,12A,1,01:00:10:00,Good boom,Yes,CRACK_R01\n"
    "CRACK_01_002.WAV,12A,2,01:01:20:00,Plane overhead,No,CRACK_R01\n"
    "CRACK_01_003.WAV,12B,1,01:02:30:00,Print,Yes,CRACK_R01\n",
)


import app as application  # noqa: E402
import db  # noqa: E402
import handoff  # noqa: E402
import media  # noqa: E402
import offload  # noqa: E402
import proxies  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402
import sync  # noqa: E402
import toolpaths  # noqa: E402
import version  # noqa: E402
import waveform  # noqa: E402


# Temp folders share one real filesystem. Give the simulated mounts stable,
# distinct identities while still exercising all production validation logic.
real_inspect_path = storage.inspect_path


def fake_inspect_path(path):
    info = real_inspect_path(path)
    real = storage.canonical(path)
    identities = (
        (DRIVE_A_ALIAS, "test:drive-a"),
        (DRIVE_A, "test:drive-a"),
        (DRIVE_B, "test:drive-b"),
        (os.path.join(CARDS, "A001_CFEXPRESS"), "test:camera-a001"),
        (os.path.join(CARDS, "SOUND_01"), "test:sound-01"),
        (os.path.join(CARDS, "A001_DUPLICATE"), "test:camera-duplicate"),
    )
    for root, identity in identities:
        if storage.paths_overlap(storage.canonical(root), real):
            info["device_id"] = identity
            info["volume_uuid"] = identity
            break
    return info


storage.inspect_path = fake_inspect_path


def fake_probe_file(path):
    name = os.path.basename(path)
    if name.lower().endswith(".mxf"):
        clip = int(name[5:8])
        clip_tc = {1: "01:00:10:00", 2: "01:01:20:00", 3: "01:02:30:00"}[clip]
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "mpeg2video",
                 "codec_long_name": "MPEG-2 video", "width": 2048, "height": 1080,
                 "avg_frame_rate": "24000/1001",
                 "tags": {"timecode": clip_tc}},
                {"codec_type": "audio", "codec_name": "pcm_s24le", "channels": 2,
                 "sample_rate": "48000", "bits_per_sample": 24},
            ],
            "format": {"format_name": "mxf", "duration": "10.5"},
        }
    if name.lower().endswith(".wav"):
        take = int(os.path.splitext(name)[0].split("_")[-1])
        references = {1: 173453280, 2: 176816640, 3: 180180000}
        return {
            "streams": [
                {"codec_type": "audio", "codec_name": "pcm_s24le", "channels": 4,
                 "sample_rate": "48000", "bits_per_sample": 24},
            ],
            "format": {"format_name": "wav", "duration": "12.25",
                       "tags": {"time_reference": str(references[take]),
                                "timecode_rate": "24000/1001",
                                "timecode_flag": "NDF",
                                "origination_date": "2026-01-16"}},
        }
    raise RuntimeError("unexpected fixture type")


media.FFPROBE = "test-ffprobe"
media.probe_file = fake_probe_file
proxies.FFMPEG = "test-ffmpeg"
proxies._capability = (True, "")

proxy_encode_calls = []


def fake_transcode_file(input_path, partial_path, timecode, reel, duration,
                        progress, cancel_event, process_started=None):
    if cancel_event.is_set():
        raise proxies.ProxyCancelled()
    proxy_encode_calls.append((input_path, partial_path, timecode, reel))
    os.makedirs(os.path.dirname(partial_path), exist_ok=True)
    payload = f"PRORES_PROXY\n{os.path.basename(input_path)}\n{timecode}\n{reel}\n".encode()
    with open(partial_path, "wb") as handle:
        handle.write(payload * 128)
    progress(float(duration or 0) / 2)
    progress(float(duration or 0))


def fake_inspect_proxy(path):
    final_path = (path.removesuffix(".partial.mov") + ".mov"
                  if path.endswith(".partial.mov") else path)
    row = db.q1("""SELECT duration_seconds,start_timecode,reel_name
                    FROM proxies WHERE output_path=?""", (final_path,))
    if not row:
        raise RuntimeError("proxy is not catalogued")
    return {
        "codec_name": "prores", "profile_name": "Proxy",
        "width": 1920, "height": 1080,
        "duration_seconds": row["duration_seconds"],
        "start_timecode": row["start_timecode"], "reel_name": row["reel_name"],
    }


proxies.transcode_file = fake_transcode_file
proxies.inspect_proxy = fake_inspect_proxy
waveform._capability = (True, "")

waveform_signals = {}
for signal_index in range(1, 4):
    signal_rng = np.random.default_rng(9000 + signal_index)
    sound_signal = signal_rng.normal(size=int(12.25 * waveform.SAMPLE_RATE)).astype("f4")
    signal_start = waveform.SAMPLE_RATE if signal_index == 1 else 0
    camera_signal = (sound_signal[signal_start:
                                  signal_start + int(10.5 * waveform.SAMPLE_RATE)]
                     + signal_rng.normal(scale=0.025,
                                         size=int(10.5 * waveform.SAMPLE_RATE))).astype("f4")
    waveform_signals[f"CRACK_01_{signal_index:03}.WAV"] = sound_signal
    waveform_signals[f"A001C00{signal_index}_260116AB_CANON.MXF"] = camera_signal


def fake_extract_audio(path, cancel_event, process_started=None):
    if cancel_event.is_set():
        raise waveform.WaveformCancelled()
    return waveform_signals[os.path.basename(path)].copy()


waveform.extract_audio = fake_extract_audio
handoff.FFMPEG = "test-ffmpeg"
handoff._capability = (True, "")

daily_encode_calls = []
daily_slate_calls = []


def fake_render_slate(path, row, links):
    daily_slate_calls.append((os.path.basename(row["rel_path"]), row["sync_status"],
                              row["sync_method"], len(links)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"TEST SLATE")


def fake_transcode_daily(row, links, partial_path, slate_path, progress,
                         cancel_event, process_started=None):
    if cancel_event.is_set():
        raise handoff.HandoffCancelled()
    daily_encode_calls.append((row["file_id"], row["proxy_path"], len(links)))
    os.makedirs(os.path.dirname(partial_path), exist_ok=True)
    payload = (f"H264 DAILY\n{os.path.basename(row['rel_path'])}\n"
               f"{row['start_timecode']}\n{row['sync_status']}\n").encode()
    with open(partial_path, "wb") as handle:
        handle.write(payload * 128)
    progress(float(row["duration_seconds"] or 0) / 2)
    progress(float(row["duration_seconds"] or 0))


def fake_inspect_daily(path):
    final_path = (path.removesuffix(".partial.mp4") + ".mp4"
                  if path.endswith(".partial.mp4") else path)
    row = db.q1("SELECT duration_seconds FROM dailies WHERE output_path=?", (final_path,))
    if not row:
        raise RuntimeError("daily is not catalogued")
    return {"codec_name": "h264", "width": 1280, "height": 720,
            "audio_codec": "aac", "duration_seconds": row["duration_seconds"]}


handoff.render_slate = fake_render_slate
handoff.transcode_daily = fake_transcode_daily
handoff.inspect_daily = fake_inspect_daily
db.init()
c = application.app.test_client()


def post(url, **body):
    response = c.post(url, json=body)
    return response.status_code, response.get_json()


def get(url):
    response = c.get(url)
    return response.status_code, response.get_json()


def raw_post(url, **kwargs):
    """A POST that deliberately does NOT look like the app's own UI."""
    return c.post(url, **kwargs)


def wait_job():
    while offload.current() or proxies.current() or waveform.current() or handoff.current():
        time.sleep(0.02)


FAILED = False


def check(label, condition, extra=""):
    global FAILED
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" +
          (f"  — {extra}" if extra else ""))
    if not condition:
        FAILED = True


print("\n[1] launch + day + physical destinations")
home = c.get("/")
check("home screen renders", home.status_code == 200 and b"Second Hand Smoke" in home.data
      and b"Second Hand Smoke" in home.data and b"CnF Works" in home.data)
check("Phase 6 controls render",
      b"Sync day" in home.data and b"Waveform fallback" in home.data and
      b"Editorial handoff" in home.data and b"Build Resolve handoff" in home.data)
check("stylesheet is served", c.get("/static/app.css").status_code == 200)
s, j = post("/api/day", project="PROJECT_01", name="DAY_01", shoot_date="2026-01-16")
check("day created", s == 200, json.dumps(j))
day_id = j["day_id"]
s, j = post("/api/day", project="../escape", name="DAY_02", shoot_date="2026-01-16")
check("unsafe project path rejected", s == 400, j.get("error"))
s, j = post("/api/destination", path=DRIVE_A, label="DRIVE_A", role="primary")
check("first destination added", s == 200, j)
s, j = post("/api/destination", path=DRIVE_A_ALIAS, label="DRIVE_A_ALIAS")
check("second folder on same physical drive rejected", s == 400, j.get("error"))
s, j = post("/api/destination", path=DRIVE_B, label="DRIVE_B")
check("second physical destination added", s == 200, j)
check("environment cannot lower the two-copy floor", reports.MIN_VERIFIED_COPIES == 2)

print("\n[2] scan + register")
s, j = get("/api/scan")
found = {item["label"]: item for item in j["cards"]}
check("two cards found", len(found) == 2, list(found))
check("camera card classified", found["A001_CFEXPRESS"]["kind"] == "camera")
check("roll parsed from clip name", found["A001_CFEXPRESS"]["roll"] == "A001")
check("sound card classified", found["SOUND_01"]["kind"] == "sound")
check("macOS metadata excluded", found["A001_CFEXPRESS"]["file_count"] == 8)
s, j = post("/api/register", fingerprint=found["A001_CFEXPRESS"]["fingerprint"])
cam_id = j["card_id"]
check("card registered", s == 200, j)
s, j = post(f"/api/card/{cam_id}/manifest")
check("partial manifest is blocked", s == 400, j.get("error"))
s, j = post(f"/api/proxy/{cam_id}")
check("proxy generation waits for verified originals", s == 400, j.get("error"))

probe_example = media.parse_probe(fake_probe_file(
    os.path.join(CARDS, "A001_CFEXPRESS", CAMERA_FILES[0][0])))
check("ffprobe JSON normalization keeps rate and timecode",
      probe_example["frame_rate"] == "24000/1001" and
      probe_example["timecode_rate"] == "24000/1001" and
      probe_example["start_timecode"] == "01:00:10:00")
check("23.976 non-drop TC uses real elapsed seconds",
      abs(sync.timecode_to_seconds("01:00:00:00", "24000/1001") - 3603.6) < 0.0001)
check("29.97 drop-frame TC stays within one frame of clock time",
      abs(sync.timecode_to_seconds("01:00:00;00", "30000/1001") - 3600.0)
      < (1 / (30000 / 1001)))
try:
    sync.timecode_to_seconds("00:01:00;00", "30000/1001")
    invalid_drop_label_rejected = False
except ValueError:
    invalid_drop_label_rejected = True
check("skipped drop-frame labels are rejected", invalid_drop_label_rejected)
drop_frames = sync.timecode_to_frames("12:34:56;12", "30000/1001", True)
check("Phase 6 ALE inverts drop-frame counts without changing the TC label",
      handoff.frames_to_timecode(drop_frames, "30000/1001", True) == "12:34:56;12")
camera_near_midnight = sync.timecode_to_seconds("23:59:59:20", "24000/1001")
midnight_overlap = sync.overlap_with_rollover(
    camera_near_midnight, 4.0, 0.0, 2.0,
    sync.day_length_seconds("24000/1001"),
)
check("midnight rollover still matches overlapping sound", midnight_overlap[0] > 1.0)
reference_sound = np.random.default_rng(123).normal(size=7000).astype("f4")
reference_camera = reference_sound[1000:6000] + np.random.default_rng(456).normal(
    scale=0.02, size=5000)
waveform_match = waveform.correlate(reference_camera, reference_sound)
check("FFT waveform correlation recovers a sample-accurate offset",
      waveform_match["score"] > 0.99 and
      abs(waveform_match["offset_seconds"] + 2.0) < 0.0001,
      waveform_match)
unrelated_match = waveform.correlate(
    reference_camera, np.random.default_rng(789).normal(size=7000).astype("f4")
)
check("unrelated waveforms score below the review floor",
      unrelated_match["score"] < waveform.REVIEW_SCORE, unrelated_match)
try:
    waveform.correlate(np.zeros(2000, dtype="f4"), np.zeros(3000, dtype="f4"))
    silent_waveform_rejected = False
except ValueError:
    silent_waveform_rejected = True
check("silent audio is rejected instead of false-matched", silent_waveform_rejected)
bwf_path = write_bwf_fixture("WAVE_METADATA_TEST/reference.wav", 0x100000002)
bwf = media.read_wave_metadata(bwf_path)
check("BEXT 64-bit sample reference and iXML TC rate are read",
      bwf.get("bwf_time_reference") == 0x100000002 and
      bwf.get("timecode_rate") == "24000/1001" and
      bwf.get("timecode_drop_frame") == 0, bwf)
shutil.rmtree(os.path.dirname(bwf_path))

print("\n[3] changed source is blocked")
extra = write_fixture("A001_CFEXPRESS/LATE_CLIP.MXF", 44, 4096)
s, j = post(f"/api/offload/{cam_id}")
check("new file after registration blocks offload", s == 400, j.get("error"))
os.remove(extra)

print("\n[4] verified offload")
s, j = post(f"/api/offload/{cam_id}")
check("offload started", s == 200, j)
wait_job()
status = reports.card_status(cam_id)
check("card cleared for format", status["safe"], status["reason"])
check("all files verified", status["verified"] == 8, f"{status['verified']}/{status['total']}")
check("completed progress stops at 100%", offload.status_snapshot()["percent"] == 100)
camera_metadata = media.card_summary(cam_id)
check("camera clip metadata stored automatically",
      camera_metadata["ready"] == 3 and camera_metadata["video_seconds"] == 31.5,
      camera_metadata)
s, before_sound_sync = post(f"/api/day/{day_id}/sync")
check("camera clips remain explicitly unmatched before sound arrives",
      s == 200 and before_sound_sync["unmatched"] == 3 and
      before_sound_sync["matched"] == 0, before_sound_sync)
check("an unmatched sync never changes format clearance", reports.card_status(cam_id)["safe"])

print("\n[5] source untouched + destination layout")
source_root = os.path.join(CARDS, "A001_CFEXPRESS")
source_files = sorted(os.path.relpath(os.path.join(dp, name), source_root)
                      for dp, _, names in os.walk(source_root) for name in names)
check("source file count unchanged", len(source_files) == 9, len(source_files))
check("no partial file written to source", not any(name.endswith(".partial") for name in source_files))
for label, drive in (("DRIVE_A", DRIVE_A), ("DRIVE_B", DRIVE_B)):
    path = os.path.join(drive, "PROJECT_01", "DAY_01", "CAMERA", "A001")
    count = sum(len(names) for _, _, names in os.walk(path))
    check(f"{label} has 8 files", count == 8, count)

print("\n[6] complete manifest + no-op resume")
s, j = post(f"/api/card/{cam_id}/manifest")
check("MHL written to both drives", s == 200 and len(j["paths"]) == 2, j)
with open(j["paths"][0], encoding="utf-8") as fh:
    mhl = fh.read()
check("MHL contains all hashes", mhl.count("<hash>") == 8)
check("MHL is well-formed XML", ET.parse(j["paths"][0]).getroot().tag == "hashlist")
before = db.q1("SELECT COUNT(*) c FROM copies WHERE state='verified'")["c"]
s, j = post(f"/api/offload/{cam_id}")
wait_job()
after = db.q1("SELECT COUNT(*) c FROM copies WHERE state='verified'")["c"]
check("resume does not recopy verified data", before == after == 16, f"{before} -> {after}")

print("\n[7] corruption, truncation and deletion")
BASE = ("PROJECT_01/DAY_01/CAMERA/A001/CONTENTS/CLIPS001/"
        "A001C002_260116AB_CANON/A001C002_260116AB_CANON.MXF")
victim = os.path.join(DRIVE_B, BASE)
twin = os.path.join(DRIVE_A, BASE)
size = os.path.getsize(victim)
with open(victim, "r+b") as fh:
    fh.seek(size // 2)
    fh.write(b"\x00" * 4096)
db.ex("UPDATE copies SET state='copied' WHERE dest_path=?", (victim,))
s, j = post(f"/api/offload/{cam_id}")
wait_job()
row = db.q1("SELECT state,error FROM copies WHERE dest_path=?", (victim,))
check("same-size corruption detected", row["state"] == "failed", dict(row))
check("format gate closes after corruption", not reports.card_status(cam_id)["safe"])

shutil.copyfile(twin, victim)
with open(victim, "r+b") as fh:
    fh.truncate(size - 500)
db.ex("UPDATE copies SET state='copied' WHERE dest_path=?", (victim,))
s, j = post(f"/api/offload/{cam_id}")
wait_job()
row = db.q1("SELECT state FROM copies WHERE dest_path=?", (victim,))
check("truncated copy healed and verified", row["state"] == "verified")

os.remove(victim)
db.ex("UPDATE copies SET state='copied' WHERE dest_path=?", (victim,))
s, j = post(f"/api/offload/{cam_id}")
wait_job()
row = db.q1("SELECT state FROM copies WHERE dest_path=?", (victim,))
check("missing copy restored and verified", row["state"] == "verified")

print("\n[8] re-read catches rot in a previously verified copy")
with open(twin, "r+b") as fh:
    fh.seek(size // 3)
    fh.write(b"\xff" * 2048)
s, j = post(f"/api/offload/{cam_id}?mode=verify")
wait_job()
row = db.q1("SELECT state,error FROM copies WHERE dest_path=?", (twin,))
check("re-read caught silent rot", row["state"] == "failed", row["error"])
check("format gate closes after rot", not reports.card_status(cam_id)["safe"])
s, j = post(f"/api/offload/{cam_id}")
wait_job()
check("offload repairs the bad copy", reports.card_status(cam_id)["safe"])

print("\n[9] duplicate roll protection")
for rel, seed, size in CAMERA_FILES:
    write_fixture(os.path.join("A001_DUPLICATE", rel), seed + 80, size)
s, scan_result = get("/api/scan")
duplicate = next(item for item in scan_result["cards"] if item["label"] == "A001_DUPLICATE")
s, j = post("/api/register", fingerprint=duplicate["fingerprint"])
check("second card cannot overwrite the A001 output", s == 409, j.get("error"))

print("\n[10] sound card + day report")
sound = next(item for item in scan_result["cards"] if item["label"] == "SOUND_01")
s, j = post("/api/register", fingerprint=sound["fingerprint"])
sound_id = j["card_id"]
s, j = post(f"/api/offload/{sound_id}")
wait_job()
check("sound card clears", reports.card_status(sound_id)["safe"])
sound_metadata = media.card_summary(sound_id)
check("sound metadata stored automatically", sound_metadata["ready"] == 3,
      sound_metadata)
sound_row = db.q1("""SELECT m.scene,m.take_name,m.start_timecode,m.notes,m.circled,m.sound_roll
                     FROM files f JOIN media_metadata m ON m.file_id=f.id
                     WHERE f.card_id=? AND f.rel_path LIKE '%CRACK_01_001.WAV'""", (sound_id,))
check("sound report matched scene, take, TC and notes",
      dict(sound_row) == {"scene": "12A", "take_name": "1",
                          "start_timecode": "01:00:10:00", "notes": "Good boom",
                          "circled": 1, "sound_roll": "CRACK_R01"}, dict(sound_row))
sound_root = os.path.join(DRIVE_A, "PROJECT_01", "DAY_01", "SOUND", "CRACK_R01")
check("sound roll folder is not duplicated",
      os.path.isfile(os.path.join(sound_root, "CRACK_01_001.WAV")) and
      not os.path.isdir(os.path.join(sound_root, "CRACK_R01")))
s, state_after_sound = get("/api/state")
check("new sound metadata invalidates earlier sync relationships",
      s == 200 and state_after_sound["sync"]["analyzed"] == 0,
      state_after_sound.get("sync"))
s, sync_result = post(f"/api/day/{day_id}/sync")
check("BWF sample reference deterministically matches every camera clip",
      s == 200 and sync_result["matched"] == 3 and
      sync_result["ambiguous"] == 0 and sync_result["links"] == 3,
      sync_result)
sync_links = db.q("""SELECT l.offset_seconds,m.scene,m.take_name
                       FROM sync_links l JOIN media_metadata m ON m.file_id=l.sound_file_id
                       ORDER BY l.camera_file_id""")
check("sync links retain scene, take and exact per-clip offsets",
      [(row["scene"], row["take_name"]) for row in sync_links] ==
      [("12A", "1"), ("12A", "2"), ("12B", "1")] and
      all(abs(row["offset_seconds"]) < 0.0001 for row in sync_links),
      [dict(row) for row in sync_links])
check("successful sync never changes either card's format clearance",
      reports.card_status(cam_id)["safe"] and reports.card_status(sound_id)["safe"])
report = c.get(f"/api/day/{day_id}/report")
check("day report renders media metadata",
      report.status_code == 200 and b"SAFE TO FORMAT" in report.data and
      b"01:00:10:00" in report.data and b"Good boom" in report.data)
check("day report records timecode relationships and offsets",
      b"Camera / sound sync" in report.data and b"sync 3/3" in report.data and
      b"CRACK_01_001.WAV" in report.data and b"+0.000s" in report.data)
s, analysis = post(f"/api/card/{sound_id}/analyze")
check("manual metadata refresh works",
      s == 200 and analysis["ready"] == 3 and analysis["sound_rows_matched"] == 3,
      analysis)
check("manual metadata refresh clears stale sync rows",
      sync.day_summary(day_id)["analyzed"] == 0, sync.day_summary(day_id))
s, sync_result = post(f"/api/day/{day_id}/sync")
check("sync rerun is deterministic after metadata refresh",
      s == 200 and sync_result["matched"] == 3 and sync_result["links"] == 3,
      sync_result)

original_sound = db.q1("""SELECT f.*,m.* FROM files f JOIN media_metadata m ON m.file_id=f.id
                            WHERE f.card_id=? AND f.rel_path LIKE '%CRACK_01_001.WAV'""",
                         (sound_id,))
ambiguous_file = db.ex(
    """INSERT INTO files(card_id,rel_path,size,mtime,category,src_hash)
       VALUES (?,?,?,?,?,?)""",
    (sound_id, "CRACK_R01/AMBIGUOUS_001.WAV", original_sound["size"],
     time.time(), "audio", original_sound["src_hash"]),
)
ambiguous_metadata = {key: original_sound[key] for key in media.METADATA_COLUMNS}
ambiguous_metadata.update({"notes": "test mono split"})
media._save_metadata(ambiguous_file, ambiguous_metadata)
for destination in db.q("SELECT * FROM destinations WHERE day_id=?", (day_id,)):
    dest_path = os.path.join(destination["path"], "AMBIGUOUS_001.WAV")
    # The gate's live check stats every 'verified' row's dest_path on a
    # connected drive, so this synthetic take needs a real file on disk at
    # the right size, not just a database row.
    with open(dest_path, "wb") as fh:
        fh.write(b"\0" * original_sound["size"])
    db.ex("""INSERT INTO copies(file_id,destination_id,dest_path,state,read_back_hash,updated_at)
             VALUES (?,?,?,?,?,?)""",
          (ambiguous_file, destination["id"], dest_path,
           "verified", original_sound["src_hash"], db.now()))
s, split_sync = post(f"/api/day/{day_id}/sync")
first_camera_id = db.q1(
    "SELECT id FROM files WHERE card_id=? AND category='video' ORDER BY rel_path", (cam_id,)
)["id"]
split_status = db.q1("""SELECT candidate_groups,link_count,status FROM sync_status
                          WHERE camera_file_id=?""", (first_camera_id,))
check("matching mono splits are grouped as one recorder take",
      s == 200 and split_sync["matched"] == 3 and split_sync["links"] == 4 and
      dict(split_status) == {"candidate_groups": 1, "link_count": 2, "status": "matched"},
      {"result": split_sync, "status": dict(split_status)})
ambiguous_metadata.update({"scene": "12X", "take_name": "99", "notes": "test collision"})
media._save_metadata(ambiguous_file, ambiguous_metadata)
s, ambiguous_sync = post(f"/api/day/{day_id}/sync")
check("overlapping competing sound takes are never silently chosen",
      s == 200 and ambiguous_sync["ambiguous"] == 1 and
      ambiguous_sync["matched"] == 2, ambiguous_sync)
check("an ambiguous sync is visible but does not close the format gate",
      reports.card_status(sound_id)["safe"] and reports.card_status(cam_id)["safe"])
db.ex("DELETE FROM sync_links WHERE sound_file_id=?", (ambiguous_file,))
db.ex("DELETE FROM copies WHERE file_id=?", (ambiguous_file,))
db.ex("DELETE FROM media_metadata WHERE file_id=?", (ambiguous_file,))
db.ex("DELETE FROM files WHERE id=?", (ambiguous_file,))

db.ex("UPDATE media_metadata SET duration_seconds=4 WHERE file_id=?",
      (original_sound["file_id"],))
sequential_file = db.ex(
    """INSERT INTO files(card_id,rel_path,size,mtime,category,src_hash)
       VALUES (?,?,?,?,?,?)""",
    (sound_id, "CRACK_R01/SEQUENTIAL_001.WAV", original_sound["size"],
     time.time(), "audio", original_sound["src_hash"]),
)
sequential_metadata = {key: original_sound[key] for key in media.METADATA_COLUMNS}
sequential_metadata.update({
    "duration_seconds": 4.0, "start_timecode": None,
    "bwf_time_reference": original_sound["bwf_time_reference"] + (5 * 48000),
    "scene": "12A", "take_name": "1B", "notes": "test sequential take",
})
media._save_metadata(sequential_file, sequential_metadata)
for destination in db.q("SELECT * FROM destinations WHERE day_id=?", (day_id,)):
    dest_path = os.path.join(destination["path"], "SEQUENTIAL_001.WAV")
    with open(dest_path, "wb") as fh:
        fh.write(b"\0" * original_sound["size"])
    db.ex("""INSERT INTO copies(file_id,destination_id,dest_path,state,read_back_hash,updated_at)
             VALUES (?,?,?,?,?,?)""",
          (sequential_file, destination["id"], dest_path,
           "verified", original_sound["src_hash"], db.now()))
s, multiple_sync = post(f"/api/day/{day_id}/sync")
check("sequential sound takes inside one camera clip are both retained",
      s == 200 and multiple_sync["multiple"] == 1 and
      multiple_sync["matched"] == 2 and multiple_sync["links"] == 4,
      multiple_sync)
check("multiple sequential matches do not change either format gate",
      reports.card_status(sound_id)["safe"] and reports.card_status(cam_id)["safe"])
db.ex("DELETE FROM sync_links WHERE sound_file_id=?", (sequential_file,))
db.ex("DELETE FROM copies WHERE file_id=?", (sequential_file,))
db.ex("DELETE FROM media_metadata WHERE file_id=?", (sequential_file,))
db.ex("DELETE FROM files WHERE id=?", (sequential_file,))
db.ex("UPDATE media_metadata SET duration_seconds=? WHERE file_id=?",
      (original_sound["duration_seconds"], original_sound["file_id"]))

camera_file = db.q1("SELECT id FROM files WHERE card_id=? AND category='video' ORDER BY rel_path",
                     (cam_id,))
saved_camera_tc = db.q1("SELECT start_timecode FROM media_metadata WHERE file_id=?",
                        (camera_file["id"],))["start_timecode"]
db.ex("UPDATE media_metadata SET start_timecode=NULL WHERE file_id=?", (camera_file["id"],))
s, invalid_sync = post(f"/api/day/{day_id}/sync")
check("missing camera TC is reported as invalid instead of guessed",
      s == 200 and invalid_sync["invalid"] == 1 and invalid_sync["matched"] == 2,
      invalid_sync)
check("invalid sync metadata never changes the verified-copy gate",
      reports.card_status(cam_id)["safe"])
db.ex("UPDATE media_metadata SET start_timecode=? WHERE file_id=?",
      (saved_camera_tc, camera_file["id"]))
s, final_sync = post(f"/api/day/{day_id}/sync")
check("corrected timecode returns the day to a clean 3/3 match",
      s == 200 and final_sync["matched"] == 3 and final_sync["links"] == 3,
      final_sync)

print("\n[11] conservative waveform fallback")
s, clean_waveform = post(f"/api/day/{day_id}/waveform")
wait_job()
clean_waveform_summary = waveform.day_summary(day_id)
check("waveforms independently check otherwise-clean timecode matches",
      s == 200 and clean_waveform_summary["analyzed"] == 3 and
      clean_waveform_summary["confirmed"] == 2 and
      clean_waveform_summary["matched"] == 1,
      clean_waveform_summary)
corrected_tc = db.q1("""SELECT s.method,l.offset_seconds,w.prior_method
                           FROM sync_status s JOIN sync_links l
                             ON l.camera_file_id=s.camera_file_id
                           JOIN waveform_results w ON w.camera_file_id=s.camera_file_id
                           WHERE s.camera_file_id=?""", (camera_file["id"],))
check("a strong waveform can correct a lying TC offset on the same take",
      corrected_tc["method"] == "waveform" and corrected_tc["prior_method"] == "timecode" and
      abs(corrected_tc["offset_seconds"] + 1.0) < 0.01, dict(corrected_tc))
s, clean_waveform = post(f"/api/day/{day_id}/waveform")
check("completed waveform checks are resumable and skipped",
      s == 400 and "No clips need" in clean_waveform.get("error", ""), clean_waveform)

waveform_camera_name = "A001C001_260116AB_CANON.MXF"
original_camera_waveform = waveform_signals[waveform_camera_name].copy()
db.ex("UPDATE media_metadata SET start_timecode=NULL WHERE file_id=?", (camera_file["id"],))
s, invalid_sync = post(f"/api/day/{day_id}/sync")
check("waveform fallback starts from an explicit unresolved TC state",
      s == 200 and invalid_sync["invalid"] == 1, invalid_sync)

camera_source = os.path.join(CARDS, "A001_CFEXPRESS")
sound_source = os.path.join(CARDS, "SOUND_01")
camera_unmounted = camera_source + ".waveform-unmounted"
sound_unmounted = sound_source + ".waveform-unmounted"
os.rename(camera_source, camera_unmounted)
os.rename(sound_source, sound_unmounted)
s, waveform_started = post(f"/api/day/{day_id}/waveform")
wait_job()
os.rename(camera_unmounted, camera_source)
os.rename(sound_unmounted, sound_source)
check("waveform fallback reads verified masters without source cards mounted",
      s == 200, waveform_started)
waveform_row = db.q1("""SELECT w.state,w.score,w.margin,w.offset_seconds,
                                  s.status,s.method,s.link_count
                           FROM waveform_results w JOIN sync_status s
                             ON s.camera_file_id=w.camera_file_id
                           WHERE w.camera_file_id=?""", (camera_file["id"],))
check("a unique high-confidence waveform becomes an accepted relationship",
      waveform_row["state"] == "matched" and waveform_row["status"] == "matched" and
      waveform_row["method"] == "waveform" and waveform_row["score"] > 0.9 and
      waveform_row["margin"] > waveform.AUTO_MARGIN and
      abs(waveform_row["offset_seconds"] + 1.0) < 0.01 and
      waveform_row["link_count"] == 1, dict(waveform_row))
waveform_link = db.q1("""SELECT l.method,l.offset_seconds,f.rel_path
                           FROM sync_links l JOIN files f ON f.id=l.sound_file_id
                           WHERE l.camera_file_id=?""", (camera_file["id"],))
check("accepted waveform link names the right take and offset",
      waveform_link["method"] == "waveform" and
      waveform_link["rel_path"].endswith("CRACK_01_001.WAV") and
      abs(waveform_link["offset_seconds"] + 1.0) < 0.01, dict(waveform_link))
waveform_day = sync.day_summary(day_id)
check("the day summary distinguishes TC and waveform relationships",
      waveform_day["waveform"] == 1 and waveform_day["timecode"] == 2 and
      waveform_day["matched"] == 3, waveform_day)
check("accepted waveform metadata cannot change format clearance",
      reports.card_status(cam_id)["safe"] and reports.card_status(sound_id)["safe"])

s, reset_sync = post(f"/api/day/{day_id}/sync")
check("re-running timecode sync clears stale waveform analysis",
      s == 200 and reset_sync["invalid"] == 1 and
      waveform.day_summary(day_id)["analyzed"] == 0,
      {"sync": reset_sync, "waveform": waveform.day_summary(day_id)})
sound_two_name = "CRACK_01_002.WAV"
original_sound_two = waveform_signals[sound_two_name].copy()
waveform_signals[sound_two_name] = waveform_signals["CRACK_01_001.WAV"].copy()
s, review_started = post(f"/api/day/{day_id}/waveform")
wait_job()
review_row = db.q1("""SELECT w.state,w.score,w.margin,s.status,s.link_count
                         FROM waveform_results w JOIN sync_status s
                           ON s.camera_file_id=w.camera_file_id
                         WHERE w.camera_file_id=?""", (camera_file["id"],))
candidate_count = db.q1("SELECT COUNT(*) count FROM waveform_candidates WHERE camera_file_id=?",
                         (camera_file["id"],))["count"]
check("two equally strong takes are held for review instead of auto-selected",
      s == 200 and review_row["state"] == "review" and
      review_row["status"] == "review" and review_row["margin"] < waveform.AUTO_MARGIN and
      review_row["link_count"] == 0 and candidate_count == 3,
      {"row": dict(review_row), "candidates": candidate_count})
check("a waveform review state never changes format clearance",
      reports.card_status(cam_id)["safe"] and reports.card_status(sound_id)["safe"])
review_report = c.get(f"/api/day/{day_id}/report")
check("day report preserves review candidates and confidence",
      review_report.status_code == 200 and b"Waveform fallback" in review_report.data and
      b"review" in review_report.data and b"1.000" in review_report.data)
waveform_signals[sound_two_name] = original_sound_two

s, reset_sync = post(f"/api/day/{day_id}/sync")
waveform_signals[waveform_camera_name] = np.random.default_rng(2468).normal(
    size=int(10.5 * waveform.SAMPLE_RATE)).astype("f4")
s, unmatched_started = post(f"/api/day/{day_id}/waveform")
wait_job()
unmatched_row = db.q1("""SELECT w.state,w.score,s.status,s.link_count
                            FROM waveform_results w JOIN sync_status s
                              ON s.camera_file_id=w.camera_file_id
                            WHERE w.camera_file_id=?""", (camera_file["id"],))
check("unrelated audio remains unmatched",
      s == 200 and unmatched_row["state"] == "unmatched" and
      unmatched_row["score"] < waveform.REVIEW_SCORE and
      unmatched_row["status"] == "unmatched" and unmatched_row["link_count"] == 0,
      dict(unmatched_row))

s, reset_sync = post(f"/api/day/{day_id}/sync")
normal_waveform_extract = waveform.extract_audio


def failing_waveform_extract(path, cancel_event, process_started=None):
    if os.path.basename(path) == waveform_camera_name:
        raise RuntimeError("test decoder failure")
    return normal_waveform_extract(path, cancel_event, process_started)


waveform.extract_audio = failing_waveform_extract
s, failed_started = post(f"/api/day/{day_id}/waveform")
wait_job()
failed_result = db.q1("SELECT state,error FROM waveform_results WHERE camera_file_id=?",
                       (camera_file["id"],))
check("decoder failures stay visible and leave the TC failure unresolved",
      s == 200 and failed_result["state"] == "failed" and
      "test decoder failure" in failed_result["error"] and
      db.q1("SELECT status FROM sync_status WHERE camera_file_id=?",
            (camera_file["id"],))["status"] == "invalid",
      dict(failed_result))
check("waveform decoder failure cannot change format clearance",
      reports.card_status(cam_id)["safe"])
waveform.extract_audio = normal_waveform_extract

s, reset_sync = post(f"/api/day/{day_id}/sync")
waveform_job_started = False


def slow_waveform_extract(path, cancel_event, process_started=None):
    global waveform_job_started
    waveform_job_started = True
    while not cancel_event.is_set():
        time.sleep(0.01)
    raise waveform.WaveformCancelled()


waveform.extract_audio = slow_waveform_extract
s, cancel_waveform = post(f"/api/day/{day_id}/waveform")
deadline = time.time() + 3
while not waveform_job_started and time.time() < deadline:
    time.sleep(0.01)
busy_status, busy_body = post(f"/api/proxy/{cam_id}")
check("proxy work cannot race waveform analysis",
      busy_status == 409, busy_body.get("error"))
post("/api/cancel")
wait_job()
check("waveform cancellation stops safely",
      s == 200 and waveform.status_snapshot()["state"] == "cancelled",
      waveform.status_snapshot())
check("cancelled waveform work cannot change format clearance",
      reports.card_status(cam_id)["safe"])
waveform.extract_audio = normal_waveform_extract

waveform_signals[waveform_camera_name] = original_camera_waveform
s, reset_sync = post(f"/api/day/{day_id}/sync")
s, final_waveform = post(f"/api/day/{day_id}/waveform")
wait_job()
db.ex("UPDATE media_metadata SET start_timecode=? WHERE file_id=?",
      (saved_camera_tc, camera_file["id"]))
final_waveform_summary = waveform.day_summary(day_id)
check("waveform fallback resumes to a clean final relationship",
      s == 200 and final_waveform_summary["matched"] == 1 and
      sync.day_summary(day_id)["matched"] == 3,
      {"waveform": final_waveform_summary, "sync": sync.day_summary(day_id)})
final_waveform_report = c.get(f"/api/day/{day_id}/report")
check("day report records waveform score, method and offset",
      final_waveform_report.status_code == 200 and
      b"Waveform fallback" in final_waveform_report.data and
      "matched · waveform".encode() in final_waveform_report.data and
      b"-1.000s" in final_waveform_report.data)

print("\n[12] resumable, relink-safe proxies")
s, j = post(f"/api/proxy/{cam_id}")
check("proxy job started", s == 200, j)
wait_job()
proxy_summary = proxies.card_summary(cam_id)
check("all camera proxies are ready", proxy_summary["ready"] == 3, proxy_summary)
proxy_rows = db.q("""SELECT p.*, f.rel_path FROM proxies p JOIN files f ON f.id=p.file_id
                       WHERE f.card_id=? ORDER BY f.rel_path""", (cam_id,))
check("timecode and camera roll are embedded in every proxy",
      all(row["start_timecode"] and row["reel_name"] == "A001" and
          row["width"] == 1920 and row["height"] == 1080 and
          row["codec_name"] == "prores" for row in proxy_rows))
check("proxies are written only to the primary working drive",
      all(row["output_path"].startswith(DRIVE_A + os.sep) for row in proxy_rows) and
      not any("_PXY.mov" in name for _, _, names in os.walk(DRIVE_B) for name in names))
check("proxy filenames and folder are deterministic",
      all(os.path.isfile(row["output_path"]) and
          f"{os.sep}PROXY{os.sep}A001{os.sep}" in row["output_path"] and
          row["output_path"].endswith("_PXY.mov") for row in proxy_rows))
check("proxy work does not change the format gate", reports.card_status(cam_id)["safe"])

encoded_before = len(proxy_encode_calls)
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
check("resume inspects and skips complete proxies",
      s == 200 and len(proxy_encode_calls) == encoded_before,
      f"{encoded_before} -> {len(proxy_encode_calls)}")

damaged_proxy = proxy_rows[1]["output_path"]
with open(damaged_proxy, "r+b") as handle:
    handle.truncate(max(1, os.path.getsize(damaged_proxy) // 2))
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
check("a truncated proxy is regenerated without touching originals",
      s == 200 and len(proxy_encode_calls) == encoded_before + 1 and
      proxies.card_summary(cam_id)["ready"] == 3)

changed_proxy = proxy_rows[0]["output_path"]
with open(changed_proxy, "r+b") as handle:
    first = handle.read(1)
    handle.seek(0)
    handle.write(bytes([first[0] ^ 0xFF]))
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
check("same-size proxy corruption is checksum-detected and regenerated",
      s == 200 and len(proxy_encode_calls) == encoded_before + 2 and
      proxies.card_summary(cam_id)["ready"] == 3)

camera_source = os.path.join(CARDS, "A001_CFEXPRESS")
unmounted_source = camera_source + ".unmounted"
os.rename(camera_source, unmounted_source)
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
os.rename(unmounted_source, camera_source)
check("verified-drive proxies do not require the source card mounted", s == 200, j)

missing_tc = proxy_rows[1]["file_id"]
saved_tc = db.q1("SELECT start_timecode FROM media_metadata WHERE file_id=?",
                 (missing_tc,))["start_timecode"]
db.ex("UPDATE media_metadata SET start_timecode=NULL WHERE file_id=?", (missing_tc,))
s, j = post(f"/api/proxy/{cam_id}?rebuild=1")
wait_job()
check("missing camera timecode fails loudly but leaves originals safe",
      proxies.card_summary(cam_id)["problems"] == 1 and
      reports.card_status(cam_id)["safe"], proxies.status_snapshot()["message"])
db.ex("UPDATE media_metadata SET start_timecode=? WHERE file_id=?", (saved_tc, missing_tc))
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
check("a corrected metadata problem resumes to completion",
      proxies.card_summary(cam_id)["ready"] == 3)

report = c.get(f"/api/day/{day_id}/report")
check("day report records proxy completion",
      report.status_code == 200 and b"proxies 3/3" in report.data and
      b"not built" not in report.data.split(b"Card A001", 1)[1].split(b"File verification", 1)[0])

s, j = post(f"/api/proxy/{sound_id}")
check("sound-only cards cannot start a proxy job", s == 400, j.get("error"))

print("\n[13] checksummed Resolve handoff and review dailies")
s, handoff_started = post(f"/api/day/{day_id}/handoff")
wait_job()
handoff_summary = handoff.day_summary(day_id)
handoff_export = db.q1("SELECT * FROM handoff_exports WHERE day_id=?", (day_id,))
check("Phase 6 package and every review daily are ready",
      s == 200 and handoff_summary["package_ready"] and
      handoff_summary["ready"] == handoff_summary["total"] == 3 and
      handoff_summary["unresolved"] == 0, handoff_summary)
required_handoff = [handoff_export[name] for name in
                    ("fcpxml_path", "ale_path", "json_path", "readme_path",
                     "checksums_path")]
check("handoff writes all five durable documents",
      all(path and os.path.isfile(path) for path in required_handoff), required_handoff)

fcpxml_root = ET.parse(handoff_export["fcpxml_path"]).getroot()
fcpxml_text = open(handoff_export["fcpxml_path"], encoding="utf-8").read()
check("FCPXML 1.10 carries proxy, production-sound and sync decisions",
      fcpxml_root.tag == "fcpxml" and fcpxml_root.attrib["version"] == "1.10" and
      "_PXY.mov" in fcpxml_text and ".WAV" in fcpxml_text and
      "SYNC WAVEFORM" in fcpxml_text and "dialogue.production" in fcpxml_text)
ale_text = open(handoff_export["ale_path"], encoding="utf-8").read()
check("ALE exposes reel, TC, scene, take, notes and sync method",
      "FIELD_DELIM\tTABS" in ale_text and "A001" in ale_text and
      "01:00:10:00" in ale_text and "12A" in ale_text and
      "Good boom" in ale_text and "waveform" in ale_text)
with open(handoff_export["json_path"], encoding="utf-8") as handle:
    handoff_manifest = json.load(handle)
check("JSON manifest preserves original/proxy hashes and exact sound offsets",
      handoff_manifest["schema"] == "cf-smoker-handoff-1" and
      len(handoff_manifest["clips"]) == 3 and
      all(clip["original"]["xxhash64"] and clip["proxy"]["xxhash64"] and
          clip["daily"]["xxhash64"] for clip in handoff_manifest["clips"]) and
      abs(handoff_manifest["clips"][0]["sync"]["links"][0]["offset_seconds"] + 1.0) < 0.01)
checksum_lines = open(handoff_export["checksums_path"], encoding="utf-8").readlines()
check("handoff checksum list covers documents and all dailies",
      len(checksum_lines) == 7 and sum("DAILIES/" in line for line in checksum_lines) == 3,
      len(checksum_lines))
daily_rows = db.q("SELECT * FROM dailies ORDER BY camera_file_id")
check("review dailies are deterministic, checksummed and use accepted production sound",
      len(daily_rows) == 3 and all(row["state"] == "ready" and row["output_hash"] and
          row["external_audio"] == 1 and row["output_path"].endswith("_DAILY.mp4")
          for row in daily_rows))
check("slates visibly distinguish file, sync state and method",
      len(daily_slate_calls) == 3 and
      any(call[1] == "matched" and call[2] == "waveform" for call in daily_slate_calls),
      daily_slate_calls)
notes_response = c.get(f"/api/day/{day_id}/handoff/notes")
check("operator notes are available from the cart UI",
      notes_response.status_code == 200 and b"Editorial handoff notes" in notes_response.data and
      b"File &gt; Import &gt; Timeline" in notes_response.data)
check("handoff work never changes either format gate",
      reports.card_status(cam_id)["safe"] and reports.card_status(sound_id)["safe"])

encoded_dailies_before = len(daily_encode_calls)
camera_source = os.path.join(CARDS, "A001_CFEXPRESS")
sound_source = os.path.join(CARDS, "SOUND_01")
camera_unmounted = camera_source + ".handoff-unmounted"
sound_unmounted = sound_source + ".handoff-unmounted"
os.rename(camera_source, camera_unmounted)
os.rename(sound_source, sound_unmounted)
s, resumed_handoff = post(f"/api/day/{day_id}/handoff")
wait_job()
os.rename(camera_unmounted, camera_source)
os.rename(sound_unmounted, sound_source)
check("handoff resumes from verified drives with source cards unmounted",
      s == 200 and len(daily_encode_calls) == encoded_dailies_before,
      f"{encoded_dailies_before} -> {len(daily_encode_calls)}")

damaged_daily = daily_rows[1]["output_path"]
with open(damaged_daily, "r+b") as handle:
    first = handle.read(1)
    handle.seek(0)
    handle.write(bytes([first[0] ^ 0xFF]))
s, repaired_handoff = post(f"/api/day/{day_id}/handoff")
wait_job()
check("same-size daily corruption is checksum-detected and rebuilt",
      s == 200 and len(daily_encode_calls) == encoded_dailies_before + 1 and
      handoff.day_summary(day_id)["package_ready"])

first_sound_id = db.q1("""SELECT l.sound_file_id FROM sync_links l
                            WHERE l.camera_file_id=? ORDER BY l.sound_file_id LIMIT 1""",
                        (camera_file["id"],))["sound_file_id"]
saved_sound_reference = db.q1("SELECT bwf_time_reference FROM media_metadata WHERE file_id=?",
                              (first_sound_id,))["bwf_time_reference"]
db.ex("UPDATE media_metadata SET bwf_time_reference=? WHERE file_id=?",
      (saved_sound_reference + 100 * 48000, first_sound_id))
s, unresolved_sync = post(f"/api/day/{day_id}/sync")
check("new sync facts immediately mark the old handoff stale",
      s == 200 and handoff.day_summary(day_id)["state"] == "stale",
      handoff.day_summary(day_id))
s, unresolved_handoff = post(f"/api/day/{day_id}/handoff?rebuild=1")
wait_job()
unresolved_summary = handoff.day_summary(day_id)
unresolved_export = db.q1("SELECT * FROM handoff_exports WHERE day_id=?", (day_id,))
unresolved_xml = open(unresolved_export["fcpxml_path"], encoding="utf-8").read()
unresolved_readme = open(unresolved_export["readme_path"], encoding="utf-8").read()
check("unresolved sync is packaged but unmistakably held for editorial review",
      s == 200 and unresolved_summary["package_ready"] and
      unresolved_summary["unresolved"] == 1 and "SYNC REVIEW" in unresolved_xml and
      "NEEDS EDITORIAL REVIEW" in unresolved_readme, unresolved_summary)
check("unresolved daily falls back to camera audio instead of guessing a WAV",
      db.q1("SELECT external_audio FROM dailies WHERE camera_file_id=?",
            (camera_file["id"],))["external_audio"] == 0)
db.ex("UPDATE media_metadata SET bwf_time_reference=? WHERE file_id=?",
      (saved_sound_reference, first_sound_id))
s, restored_sync = post(f"/api/day/{day_id}/sync")
s, restored_handoff = post(f"/api/day/{day_id}/handoff")
wait_job()
check("corrected sync facts rebuild a clean package",
      s == 200 and handoff.day_summary(day_id)["package_ready"] and
      handoff.day_summary(day_id)["unresolved"] == 0,
      handoff.day_summary(day_id))

s, rebuilt_proxies = post(f"/api/proxy/{cam_id}?rebuild=1")
wait_job()
check("proxy replacement marks its downstream handoff stale",
      s == 200 and handoff.day_summary(day_id)["state"] == "stale",
      handoff.day_summary(day_id))
s, refreshed_handoff = post(f"/api/day/{day_id}/handoff")
wait_job()
check("stale handoff resumes after a proxy rebuild",
      s == 200 and handoff.day_summary(day_id)["package_ready"])
handoff_report = c.get(f"/api/day/{day_id}/report")
check("day report records handoff readiness and package path",
      handoff_report.status_code == 200 and b"Editorial handoff" in handoff_report.data and
      b"handoff 3/3" in handoff_report.data and b"DAY_01_RESOLVE.fcpxml" not in handoff_report.data)

print("\n[14] handoff cancellation is safe and resumable")
normal_daily_transcode = handoff.transcode_daily
daily_started = False


def slow_daily_transcode(row, links, partial_path, slate_path, progress,
                         cancel_event, process_started=None):
    global daily_started
    os.makedirs(os.path.dirname(partial_path), exist_ok=True)
    with open(partial_path, "wb") as handle:
        handle.write(b"PARTIAL DAILY")
    daily_started = True
    while not cancel_event.is_set():
        time.sleep(0.01)
    raise handoff.HandoffCancelled()


handoff.transcode_daily = slow_daily_transcode
s, cancelling_handoff = post(f"/api/day/{day_id}/handoff?rebuild=1")
deadline = time.time() + 3
while not daily_started and time.time() < deadline:
    time.sleep(0.01)
busy_status, busy_body = post(f"/api/proxy/{cam_id}")
check("proxy work cannot race the handoff encoder",
      busy_status == 409, busy_body.get("error"))
post("/api/cancel")
wait_job()
handoff.transcode_daily = normal_daily_transcode
handoff_partials = [os.path.join(dp, name)
                    for dp, _, names in os.walk(DRIVE_A)
                    for name in names if ".partial." in name]
check("cancelled handoff leaves no partial review files or slates",
      not handoff_partials, handoff_partials)
check("cancelled handoff remains marked incomplete",
      handoff.status_snapshot()["state"] == "cancelled" and
      os.path.isfile(os.path.join(handoff.day_summary(day_id)["bundle_path"],
                                  "EXPORT_INCOMPLETE.txt")))
check("cancelled handoff cannot change format clearance",
      reports.card_status(cam_id)["safe"] and reports.card_status(sound_id)["safe"])
s, resumed_after_cancel = post(f"/api/day/{day_id}/handoff")
wait_job()
check("cancelled handoff resumes to a complete checksummed package",
      s == 200 and handoff.day_summary(day_id)["package_ready"] and
      not os.path.exists(os.path.join(handoff.day_summary(day_id)["bundle_path"],
                                      "EXPORT_INCOMPLETE.txt")))

print("\n[15] proxy cancellation is safe and resumable")
normal_transcode = proxies.transcode_file
proxy_started = False


def slow_transcode(input_path, partial_path, timecode, reel, duration,
                   progress, cancel_event, process_started=None):
    global proxy_started
    os.makedirs(os.path.dirname(partial_path), exist_ok=True)
    with open(partial_path, "wb") as handle:
        handle.write(b"PARTIAL PROXY")
    proxy_started = True
    while not cancel_event.is_set():
        time.sleep(0.01)
    raise proxies.ProxyCancelled()


proxies.transcode_file = slow_transcode
s, j = post(f"/api/proxy/{cam_id}?rebuild=1")
deadline = time.time() + 3
while not proxy_started and time.time() < deadline:
    time.sleep(0.01)
second_status, second_body = post(f"/api/offload/{cam_id}")
check("offload cannot race a proxy job", second_status == 409, second_body.get("error"))
post("/api/cancel")
wait_job()
proxies.transcode_file = normal_transcode
proxy_snapshot = proxies.status_snapshot()
proxy_partials = [os.path.join(dp, name)
                  for dp, _, names in os.walk(DRIVE_A)
                  for name in names if name.endswith(".partial.mov")]
check("proxy job reports cancelled", proxy_snapshot["state"] == "cancelled",
      proxy_snapshot["state"])
check("cancelled proxy leaves no partial QuickTimes", not proxy_partials, proxy_partials)
check("cancelled proxy leaves the format gate green", reports.card_status(cam_id)["safe"])
s, j = post(f"/api/proxy/{cam_id}")
wait_job()
check("cancelled proxy work resumes", proxies.card_summary(cam_id)["ready"] == 3)

print("\n[16] offload cancellation is safe and leaves no partials")
write_fixture("CANCEL_CARD/CANCEL_R02/LONG_TAKE.WAV", 110, 32 * 1024 * 1024)
s, cancel_scan = get("/api/scan")
cancel_card = next(item for item in cancel_scan["cards"] if item["label"] == "CANCEL_CARD")
s, j = post("/api/register", fingerprint=cancel_card["fingerprint"])
cancel_id = j["card_id"]
original_chunk = offload.CHUNK
offload.CHUNK = 1024
s, j = post(f"/api/offload/{cancel_id}")
second_status, second_body = post(f"/api/offload/{cam_id}")
check("a second simultaneous job is rejected", second_status == 409,
      second_body.get("error"))
post("/api/cancel")
wait_job()
offload.CHUNK = original_chunk
snapshot = offload.status_snapshot()
check("job reports cancelled", snapshot["state"] == "cancelled", snapshot["state"])
partials = [os.path.join(dp, name)
            for drive in (DRIVE_A, DRIVE_B)
            for dp, _, names in os.walk(drive)
            for name in names if name.endswith(".partial")]
check("cancelled job leaves no partial files", not partials, partials)
check("cancelled card cannot clear", not reports.card_status(cancel_id)["safe"])

print("\n[17] metadata falls back to the verified copy once the card is gone")
card_row = db.q1("SELECT * FROM cards WHERE id=?", (cam_id,))
unmounted_path = card_row["source_path"] + "_UNMOUNTED"
os.rename(card_row["source_path"], unmounted_path)
try:
    s, fallback_analysis = post(f"/api/card/{cam_id}/analyze")
    check("metadata refresh works with the source card physically gone",
          s == 200 and fallback_analysis["total"] > 0 and
          fallback_analysis["ready"] == fallback_analysis["total"],
          fallback_analysis)
    stray = db.q1("""SELECT f.rel_path, mm.status, mm.error FROM media_metadata mm
                       JOIN files f ON f.id = mm.file_id
                       WHERE f.card_id=? AND mm.status != 'ready' LIMIT 1""", (cam_id,))
    check("no clip was left unavailable while a verified primary copy exists",
          stray is None, dict(stray) if stray else None)
finally:
    os.rename(unmounted_path, card_row["source_path"])

print("\n[18] the live format-gate check catches a corrupted copy without a re-read pass")
sample_file = db.q1(
    "SELECT * FROM files WHERE card_id=? ORDER BY rel_path LIMIT 1", (cam_id,)
)
sample_copy = db.q1(
    "SELECT * FROM copies WHERE file_id=? AND state='verified' LIMIT 1", (sample_file["id"],)
)
before_gate = reports.card_status(cam_id)
check("card reads safe before its on-disk copy is touched", before_gate["safe"], before_gate)
with open(sample_copy["dest_path"], "r+b") as fh:
    fh.truncate(max(0, sample_file["size"] - 100))
# The DB row still says 'verified' — nothing has re-read the drive yet. The
# live check in reports.card_status() is what catches this immediately,
# without waiting for someone to run 'Re-read drives'.
live_gate = reports.card_status(cam_id)
check("truncating a verified copy on a connected drive reopens the gate immediately",
      not live_gate["safe"] and live_gate["live_issues"], live_gate)
# Formal repair still follows the documented two-step path: a verify pass
# marks the bad copy 'failed' in the database, then a normal offload pass
# recopies and reverifies it (mirrors section [8]'s rot-recovery flow).
s, j = post(f"/api/offload/{cam_id}?mode=verify")
wait_job()
s, j = post(f"/api/offload/{cam_id}")
wait_job()
healed_gate = reports.card_status(cam_id)
check("re-reading then re-offloading heals the live-detected corruption",
      healed_gate["safe"], healed_gate)


# ---------------------------------------------------------------------------
# [19] Regressions for faults the stubbed rig could not previously see.
#
# Everything above runs against fake device identities, fake probes and fake
# transcoders. These checks go at the real functions instead, because each of
# them shipped a bug that every test above still passed.
# ---------------------------------------------------------------------------
print("\n[19] audit regressions")

# -- F-01: a disk image must fold to the identity of the drive underneath it,
#    with no prefix, or it counts as a second physical destination.
storage_source = open(storage.__file__, encoding="utf-8").read()
check("a folded disk image reports the backing drive's identity, unprefixed",
      'device_id = backing["device_id"]' in storage_source
      and 'f"img:{backing' not in storage_source,
      "an image on a drive must not read as a second drive")

# -- F-02: the gate must count physical drives, not destination rows.
dest_rows = db.q("SELECT id, device_id FROM destinations WHERE day_id=?", (day_id,))
saved_devices = [(row["id"], row["device_id"]) for row in dest_rows]
check("card is clear before the destinations are collapsed",
      reports.card_status(cam_id)["safe"])
for dest_id, _ in saved_devices:
    db.ex("UPDATE destinations SET device_id=? WHERE id=?", ("disk:same", dest_id))
reports.invalidate_gate_cache()
collapsed = reports.card_status(cam_id)
check("two destinations on one physical drive no longer clear the gate",
      not collapsed["safe"] and collapsed["shared_drives"], collapsed["reason"])
for dest_id, device_id in saved_devices:
    db.ex("UPDATE destinations SET device_id=? WHERE id=?", (device_id, dest_id))
reports.invalidate_gate_cache()
check("restoring separate drive identities reopens the gate",
      reports.card_status(cam_id)["safe"])

# -- F-03: a verification that finds no catalog row must create one, because
#    _copy()'s recovery path is written on the assumption that it does.
orphan_file = db.q1("SELECT * FROM files WHERE card_id=? ORDER BY rel_path LIMIT 1", (cam_id,))
orphan_copy = db.q1("SELECT * FROM copies WHERE file_id=? AND state='verified' LIMIT 1",
                    (orphan_file["id"],))
orphan_dest = db.q1("SELECT * FROM destinations WHERE id=?", (orphan_copy["destination_id"],))
db.ex("DELETE FROM copies WHERE file_id=? AND destination_id=?",
      (orphan_file["id"], orphan_dest["id"]))
job = offload.Job(cam_id, mode="verify")
recorded = job._verify(orphan_file, orphan_dest, orphan_copy["dest_path"],
                       orphan_file["src_hash"])
healed_row = db.q1("SELECT state, read_back_hash FROM copies WHERE file_id=? AND destination_id=?",
                   (orphan_file["id"], orphan_dest["id"]))
check("verifying a copy with no catalog row recreates the row",
      recorded and healed_row and healed_row["state"] == "verified" and
      healed_row["read_back_hash"] == orphan_file["src_hash"],
      dict(healed_row) if healed_row else "no row was written")
reports.invalidate_gate_cache()

# -- F-11: two source files that resolve to one destination path must be
#    refused before the copy starts, not discovered as a checksum mismatch.
collision_card = dict(db.q1("SELECT * FROM cards WHERE id=?", (cam_id,)))
collision_card["roll"] = "CRACK_R01"
collision_card["kind"] = "sound"
collision_day = db.q1("SELECT * FROM days WHERE id=?", (day_id,))
collision_dests = db.q("SELECT * FROM destinations WHERE day_id=? ORDER BY id", (day_id,))
collision_files = [{"rel_path": os.path.join("CRACK_R01", "take1.wav")},
                   {"rel_path": "take1.wav"}]
collision = offload._target_collision(collision_files, collision_dests,
                                      collision_day, collision_card)
check("two source files mapping to one destination path are refused up front",
      bool(collision) and "take1.wav" in collision, collision)
check("distinct source paths are not falsely flagged as colliding",
      offload._target_collision(
          [{"rel_path": os.path.join("CRACK_R01", "take1.wav")},
           {"rel_path": os.path.join("CRACK_R01", "take2.wav")}],
          collision_dests, collision_day, collision_card) is None)

# -- F-04: waveform-accepted links must be stored on the same absolute day
#    axis Phase 4 uses, or handoff writes a negative FCPXML source start.
wave_camera = db.q1("""SELECT f.id file_id FROM files f
                        WHERE f.card_id=? AND f.category='video' ORDER BY f.rel_path LIMIT 1""",
                    (cam_id,))
wave_sound = db.q1("""SELECT f.id file_id, m.duration_seconds FROM files f
                       JOIN media_metadata m ON m.file_id=f.id
                       WHERE f.card_id=? AND f.category='audio' LIMIT 1""", (sound_id,))
db.ex("""INSERT INTO sync_status (camera_file_id,status,method,camera_start_seconds,
         camera_end_seconds,candidate_groups,link_count,updated_at)
         VALUES (?,'unmatched','timecode',?,?,0,0,?)
         ON CONFLICT(camera_file_id) DO UPDATE SET camera_start_seconds=excluded.camera_start_seconds,
         status='unmatched', method='timecode'""",
      (wave_camera["file_id"], 3613.61, 3624.11, db.now()))
waveform._set_sync_result(
    {"file_id": wave_camera["file_id"]}, "matched",
    {"offset_seconds": -0.482, "overlap_seconds": 9.5},
    [{"file_id": wave_sound["file_id"],
      "duration_seconds": wave_sound["duration_seconds"] or 12.25}], 1, None)
wave_link = db.q1("SELECT * FROM sync_links WHERE camera_file_id=?", (wave_camera["file_id"],))
check("a waveform link stores an absolute day-axis start, not a bare offset",
      wave_link is not None and wave_link["sound_start_seconds"] >= 0
      and abs(wave_link["sound_start_seconds"] - (3613.61 - 0.482)) < 1e-6
      and abs(wave_link["offset_seconds"] + 0.482) < 1e-6,
      dict(wave_link) if wave_link else "no link written")
db.ex("DELETE FROM sync_links WHERE camera_file_id=?", (wave_camera["file_id"],))
db.ex("DELETE FROM sync_status WHERE camera_file_id=?", (wave_camera["file_id"],))

# -- F-05: every FCPXML time must be a whole multiple of the frame duration
#    and never negative, or Resolve snaps it or refuses the file.
fcpxml_camera = {
    "file_id": 1, "rel_path": "A001C001_260116AB_CANON.MXF", "roll": "A001",
    "card_label": "A001", "duration_seconds": 10.5, "start_timecode": "01:00:13:13",
    "frame_rate": "24000/1001", "timecode_rate": None, "timecode_drop_frame": 0,
    "proxy_path": os.path.join(RIG, "A001C001_PXY.mov"), "sync_status": "matched",
    "sync_method": "waveform", "sync_error": None, "camera_scene": "12A",
    "camera_take": "1", "audio_channels": 2,
}
fcpxml_link = {
    "sound_file_id": 9, "rel_path": "CRACK_01_001.WAV",
    "input_path": os.path.join(RIG, "CRACK_01_001.WAV"),
    "sound_start_seconds": 3613.128, "duration_seconds": 12.25,
    "offset_seconds": -0.482, "overlap_seconds": 9.5,
    "audio_channels": 4, "scene": "12A", "take_name": "1",
    "notes": None, "circled": 1, "sound_roll": "CRACK_R01",
}
fcpxml_text = handoff.build_fcpxml({"project": "PROJECT_01", "name": "DAY_01",
                                    "shoot_date": "2026-01-16"},
                                   [fcpxml_camera], {1: [fcpxml_link]})
fcpxml_root = ET.fromstring(fcpxml_text[fcpxml_text.index("<fcpxml"):])
frame_duration = Fraction(1001, 24000)
bad_times, negative_times = [], []
for node in fcpxml_root.iter():
    for attribute in ("start", "duration", "offset", "tcStart"):
        value = node.get(attribute)
        if not value or not value.endswith("s"):
            continue
        body = value[:-1]
        moment = Fraction(body)
        if moment < 0:
            negative_times.append(f"{node.tag}/{attribute}={value}")
        if moment % frame_duration != 0:
            bad_times.append(f"{node.tag}/{attribute}={value}")
check("no FCPXML time is negative", not negative_times, negative_times[:4])
check("every FCPXML time is a whole multiple of the frame duration",
      not bad_times, bad_times[:4])

# -- F-06 / F-07: the API must answer only this machine's own interface.
rebound = raw_post("/api/cancel", json={}, headers={"Host": "evil.example.com"})
check("a rebound hostname is refused", rebound.status_code == 403, rebound.status_code)
form_post = raw_post("/api/cancel", data="x=1",
                     content_type="application/x-www-form-urlencoded")
check("a cross-site form POST is refused", form_post.status_code == 415, form_post.status_code)
foreign = raw_post("/api/cancel", json={}, headers={"Origin": "https://evil.example.com"})
check("a POST from another origin is refused", foreign.status_code == 403, foreign.status_code)
own = raw_post("/api/cancel", json={},
               headers={"Origin": f"http://127.0.0.1:{application.PORT}"})
check("the app's own interface is still allowed", own.status_code == 200, own.status_code)
escaped = c.get("/api/browse?path=/etc")
check("the folder picker refuses paths outside the allowed roots",
      escaped.status_code == 403, escaped.status_code)

# -- F-13: unknown ids answer, rather than throwing a traceback at the operator.
check("an unknown shoot day answers 404 instead of 500",
      c.get("/api/day/999999/report").status_code == 404)

# -- F-14: the docstring is a docstring again.
check("validate_destination_set has its docstring back",
      bool(storage.validate_destination_set.__doc__))

# -- F-15: MHL creatorinfo fields must not duplicate the hostname.
mhl_written = reports.write_mhl(cam_id, saved_devices[0][0])
if mhl_written:
    mhl_text = open(mhl_written, encoding="utf-8").read()
    mhl_root = ET.fromstring(mhl_text)
    creator = mhl_root.find("creatorinfo")
    check("MHL username is the login account, not the hostname",
          creator is not None and
          creator.findtext("username") == getpass.getuser() and
          creator.findtext("username") != creator.findtext("hostname"),
          creator.findtext("username") if creator is not None else "no creatorinfo")
else:
    check("MHL username is the login account, not the hostname", False,
          "manifest was not written")

# -- F-22: the step tracker counts drives, not destination rows.
tracker_source = open("app.js", encoding="utf-8").read()
check("the step tracker counts distinct devices",
      "deviceKeys.size >= s.min_copies" in tracker_source)

print("\n[20] version and tool discovery")

# The VERSION file is the single source of truth. If Python and the macOS
# build scripts read different numbers, an MHL can claim to come from a build
# that never wrote it.
version_file = open(version.VERSION_FILE, encoding="utf-8").read().strip()
check("VERSION file is readable and not the unknown placeholder",
      version.VERSION == version_file and version.VERSION != "0.0.0-unknown",
      version.VERSION)

tool_mhl = reports.write_mhl(cam_id, saved_devices[0][0])
if tool_mhl:
    tool_field = ET.fromstring(open(tool_mhl, encoding="utf-8").read()) \
        .find("creatorinfo").findtext("tool")
    check("MHL tool field carries the VERSION file's number",
          tool_field is not None and version.VERSION in tool_field,
          tool_field)
else:
    check("MHL tool field carries the VERSION file's number", False,
          "manifest was not written")

# An explicit override is honoured verbatim. Substituting a different binary
# for the one the operator named would be worse than the error they asked for.
os.environ["DIT_FFMPEG"] = "/nowhere/ffmpeg"
check("an explicit DIT_FFMPEG override is used verbatim",
      toolpaths.ffmpeg() == "/nowhere/ffmpeg")
del os.environ["DIT_FFMPEG"]

# The case that breaks the packaged app: launchd gives a Finder-launched .app
# a minimal PATH with no Homebrew on it. Discovery must still find ffmpeg.
fake_bin = os.path.join(RIG, "fake_bin")
os.makedirs(fake_bin, exist_ok=True)
fake_ffmpeg = os.path.join(fake_bin, "ffmpeg")
with open(fake_ffmpeg, "w") as handle:
    handle.write("#!/bin/sh\nexit 0\n")
os.chmod(fake_ffmpeg, 0o755)

saved_path = os.environ.get("PATH", "")
saved_dirs = toolpaths.FALLBACK_DIRS
try:
    os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    toolpaths.FALLBACK_DIRS = (fake_bin,)
    check("ffmpeg is found off PATH, as when launched from Finder",
          toolpaths.ffmpeg() == fake_ffmpeg, str(toolpaths.ffmpeg()))
    toolpaths.FALLBACK_DIRS = (os.path.join(RIG, "does_not_exist"),)
    check("a genuinely absent ffmpeg still reports absent",
          toolpaths.ffmpeg() is None, str(toolpaths.ffmpeg()))
finally:
    toolpaths.FALLBACK_DIRS = saved_dirs
    os.environ["PATH"] = saved_path

# A non-executable file of the right name is not a usable tool.
os.chmod(fake_ffmpeg, 0o644)
try:
    os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    toolpaths.FALLBACK_DIRS = (fake_bin,)
    check("a non-executable file of the right name is not accepted",
          toolpaths.ffmpeg() is None, str(toolpaths.ffmpeg()))
finally:
    toolpaths.FALLBACK_DIRS = saved_dirs
    os.environ["PATH"] = saved_path

print("\n[21] the macOS application bundle")

import re
import subprocess
import sys

spec_source = open("SecondHandSmoke.spec", encoding="utf-8").read()
app_source = open("app.py", encoding="utf-8").read()

# Every file app.py serves off disk has to be inside the frozen bundle. Adding
# a stylesheet and forgetting the spec produces an app that is fine in
# development and broken for everyone who downloads it.
served = set(re.findall(r'send_from_directory\(BASE_DIR,\s*"([^"]+)"', app_source))
served |= set(re.findall(r'render_template\("([^"]+)"', app_source))
served.add("VERSION")
missing = sorted(name for name in served if f"('{name}', '.')" not in spec_source)
check("every file the server reads off disk is bundled by the spec",
      not missing, ", ".join(missing) or "")

# A denied removable-volume prompt makes cards look empty rather than raising.
# The usage description is the only thing that gives the operator a reason to
# say yes, so losing it is a silent, expensive regression.
plist_source = open("macos/Info.plist", encoding="utf-8").read()
check("Info.plist explains why the app needs removable volumes",
      "NSRemovableVolumesUsageDescription" in plist_source)
check("the app bundle is not sandboxed",
      "com.apple.security.app-sandbox" not in
      open("macos/entitlements.plist", encoding="utf-8").read())

build_source = open("macos/build_macos_app.sh", encoding="utf-8").read()
check("the build script takes the version from the VERSION file",
      'tr -d \'[:space:]\' < "$ROOT/VERSION"' in build_source and
      "CFBundleShortVersionString $VERSION" in build_source)

# Two servers on one catalog is a race next to the format gate. The lock is
# what stops it, so prove a second process is actually refused.
import server_main

held = server_main.claim_single_instance()
check("a server claims an exclusive lock beside the catalog",
      os.path.exists(server_main._lock_path()))

second = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, '.'); import server_main;"
     " server_main.claim_single_instance()"],
    cwd=os.getcwd(), env=dict(os.environ), capture_output=True, text=True)
check("a second server on the same catalog is refused",
      second.returncode == 3, second.stderr.strip().splitlines()[-1:] or second.returncode)

held.close()
second = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, '.'); import server_main;"
     " server_main.claim_single_instance()"],
    cwd=os.getcwd(), env=dict(os.environ), capture_output=True, text=True)
check("the lock is released when the server that held it exits",
      second.returncode == 0, second.stderr.strip())

print("\n[22] the support address reaches every surface")

# One definition, three places it has to appear. A DIT at 4am should find it on
# whatever is in front of them: the screen, the report a producer forwarded, or
# the notes inside the handoff package.
address = version.SUPPORT_EMAIL
check("there is a support address to publish", "@" in address, address)

home_page = c.get("/").data.decode("utf-8", "replace")
check("the interface footer carries the support address", address in home_page)
check("the interface footer carries the version", version.VERSION in home_page)

report_path, report_body = reports.day_report(day_id)
check("the day report carries the support address", address in report_body)
check("the day report says which build wrote it",
      version.TOOL_STRING in report_body)

notes_body = c.get(f"/api/day/{day_id}/handoff/notes").data.decode("utf-8", "replace")
check("the handoff notes carry the support address", address in notes_body)

readme_text = open("README.md", encoding="utf-8").read()
check("the README carries the support address", address in readme_text)

# The footer tells the operator what an absent ffmpeg costs. If that sentence
# goes missing, a missing ffmpeg reads as a broken program.
interface_source = open("app.js", encoding="utf-8").read()
check("the interface explains what an absent ffmpeg does and does not break",
      "the format gate are unaffected" in interface_source and
      "not found" in interface_source)
check("the interface reports tool discovery from the server",
      '"tools"' in open("app.py", encoding="utf-8").read())

print("\n" + ("SOME CHECKS FAILED" if FAILED else "ALL CHECKS PASSED") + "\n")
raise SystemExit(1 if FAILED else 0)

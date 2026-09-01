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
import csv
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
# The rig's fake drives live beside its fake cards, so the folder picker and
# the drive tools can reach them. /etc stays outside, which is what the
# browse-root check is actually about.
os.environ["DIT_BROWSE_ROOT"] = RIG


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
# Two drives, and both manifest formats on each: the 1.1 sidecar and the 2.0
# ascmhl folder. Section [37] is where the 2.0 side is picked apart.
check("both manifest formats written to both drives",
      s == 200 and len(j["paths"]) == 4, j)
legacy_paths = [path for path in j["paths"] if "ascmhl" not in path]
modern_paths = [path for path in j["paths"] if "ascmhl" in path]
check("one ASC MHL 1.1 file per drive", len(legacy_paths) == 2, legacy_paths)
check("one ASC MHL 2.0 manifest per drive", len(modern_paths) == 2, modern_paths)
check("and nothing was warned about on a first write", not j.get("warnings"),
      j.get("warnings"))
with open(legacy_paths[0], encoding="utf-8") as fh:
    mhl = fh.read()
check("MHL contains all hashes", mhl.count("<hash>") == 8)
check("MHL is well-formed XML",
      ET.parse(legacy_paths[0]).getroot().tag == "hashlist")
check("and the 2.0 manifest covers the same eight files",
      len(ET.parse(modern_paths[0]).getroot()
          .findall("{urn:ASC:MHL:v2.0}hashes/{urn:ASC:MHL:v2.0}hash")) == 8)
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

# -- F-22: the step tracker counts drives, not destination rows. Two folders on
# one disk are one place for a copy to die. Matched on the pieces rather than
# one expression, so the check survives the tracker being rewritten.
tracker_source = open("app.js", encoding="utf-8").read()
check("the step tracker counts distinct devices",
      "d.device_id" in tracker_source and "deviceKeys.size" in tracker_source
      and "s.min_copies" in tracker_source)

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

print("\n[23] the PDF report cannot disagree with the HTML report")

import pdfreport

data = reports.day_data(day_id)
check("day_data gathers the whole day in one pass",
      bool(data) and len(data["cards"]) == len(db.q(
          "SELECT id FROM cards WHERE day_id=?", (day_id,))))
check("day_data answers None for a shoot day that does not exist",
      reports.day_data(999999) is None)

# The gate verdict in the report data is the gate verdict, not a copy of it
# that has drifted.
gates_agree = all(
    entry["gate"]["safe"] == reports.card_status(entry["card"]["id"])["safe"]
    for entry in data["cards"])
check("every gate verdict in the report data is the live gate verdict", gates_agree)

# The HTML report renders from that same structure and nothing else.
check("the HTML report is rendered from day_data",
      reports.render_html(data).count("SAFE TO FORMAT")
      + reports.render_html(data).count("DO NOT FORMAT") == len(data["cards"]))

# Structural, and the important one: the PDF writer has no way to reach the
# catalog, so it cannot form a second opinion about anything.
pdf_source = open("pdfreport.py", encoding="utf-8").read()
check("the PDF writer never queries the catalog",
      "db.q(" not in pdf_source and "db.q1(" not in pdf_source)
check("the PDF writer never calls the format gate itself",
      "reports.card_status" not in pdf_source)
check("both renderers take the stamp wording from one place",
      "reports.gate_stamp(" in pdf_source and
      "gate_stamp(st)" in open("reports.py", encoding="utf-8").read())

check("the stamp wording matches the verdict",
      reports.gate_stamp({"safe": True}) == ("SAFE TO FORMAT", True, "ok") and
      reports.gate_stamp({"safe": False}) == ("DO NOT FORMAT", False, "bad"))

pdf_ok, pdf_reason = pdfreport.availability()
if pdf_ok:
    for layout in pdfreport.LAYOUTS:
        produced = pdfreport.build(day_id, layout,
                                   out_path=os.path.join(RIG, f"report_{layout}.pdf"))
        head = open(produced, "rb").read(5)
        check(f"the {layout} PDF is written and is a PDF",
              head == b"%PDF-" and os.path.getsize(produced) > 1000,
              f"{head!r} {os.path.getsize(produced)} bytes")

    response = c.get(f"/api/day/{day_id}/report.pdf?layout=table")
    check("the PDF route serves a PDF",
          response.status_code == 200 and response.data[:5] == b"%PDF-",
          response.status_code)
    bad_layout = c.get(f"/api/day/{day_id}/report.pdf?layout=nonsense")
    check("an unknown layout is refused rather than guessed",
          bad_layout.status_code == 400, bad_layout.status_code)
    check("an unknown shoot day answers 404 from the PDF route too",
          c.get("/api/day/999999/report.pdf").status_code == 404)

    # A logo is decoration. A missing one must never cost anyone their report.
    db.setting(pdfreport.SETTING_LOGO, os.path.join(RIG, "no_such_logo.png"))
    survived = pdfreport.build(day_id, "table",
                               out_path=os.path.join(RIG, "report_nologo.pdf"))
    check("a missing logo does not stop the report",
          os.path.getsize(survived) > 1000)
    db.setting(pdfreport.SETTING_LOGO, "")
else:
    print(f"  SKIP  reportlab is not installed — {pdf_reason}")
    check("without reportlab the PDF route explains itself instead of failing",
          c.get(f"/api/day/{day_id}/report.pdf").status_code == 503)
    check("without reportlab the HTML report still works",
          c.get(f"/api/day/{day_id}/report").status_code == 200)

# The HTML report is an addition's neighbour, not its casualty.
check("the HTML day report still renders after the split",
      c.get(f"/api/day/{day_id}/report").status_code == 200)

print("\n[24] branding assets")

import struct

for name in sorted(application.BRAND_ASSETS):
    check(f"{name} is present in assets/",
          os.path.isfile(os.path.join("assets", name)))
    response = c.get(f"/static/brand/{name}")
    check(f"{name} is served",
          response.status_code == 200 and response.data[:8] == b"\x89PNG\r\n\x1a\n",
          response.status_code)

# The route is an allow-list, not a folder. A web server on a machine full of
# other people's footage does not get to serve whatever it is asked for.
check("the brand route refuses anything not on the allow-list",
      c.get("/static/brand/app.py").status_code == 404)
check("the brand route cannot be walked out of",
      c.get("/static/brand/..%2fapp.py").status_code in (404, 400))

icns = "macos/AppIcon.icns"
check("the app icon is a real icns container",
      os.path.isfile(icns) and open(icns, "rb").read(4) == b"icns")
if os.path.isfile(icns):
    with open(icns, "rb") as handle:
        blob = handle.read()
    declared = struct.unpack(">I", blob[4:8])[0]
    check("the icns length header matches the file", declared == len(blob),
          f"{declared} vs {len(blob)}")
    # The Dock, the Finder and the DMG all pick different sizes out of this.
    present = set()
    offset = 8
    while offset + 8 <= len(blob):
        kind = blob[offset:offset + 4].decode("ascii", "replace")
        length = struct.unpack(">I", blob[offset + 4:offset + 8])[0]
        if length < 8:
            break
        present.add(kind)
        offset += length
    check("the icns carries the sizes macOS asks for",
          {"ic10", "ic09", "ic08", "ic07", "icp5", "icp4"} <= present,
          ", ".join(sorted(present)))

spec_text = open("SecondHandSmoke.spec", encoding="utf-8").read()
check("every served branding asset is bundled into the application",
      all(f"('assets/{name}', 'assets')" in spec_text
          for name in application.BRAND_ASSETS),
      ", ".join(sorted(application.BRAND_ASSETS)))
check("the source artwork is not shipped inside the application",
      "assets/source" not in spec_text)
check("the build script picks up the app icon",
      "AppIcon.icns" in open("macos/build_macos_app.sh", encoding="utf-8").read())

print("\n[25] the report logo")

import base64

try:
    from PIL import Image as _PILImage
    _pillow = True
except Exception:
    _pillow = False

if not _pillow:
    print("  SKIP  Pillow is not installed — logo handling cannot be exercised")
else:
    source_logo = os.path.join(RIG, "show_logo.png")
    _PILImage.new("RGBA", (64, 40), (255, 0, 128, 255)).save(source_logo)

    # A payload appended after the PNG's end. Whatever a file claims to be, the
    # copy this program keeps is one it wrote itself, so this must not survive.
    smuggled = b"<<not-part-of-any-image>>"
    with open(source_logo, "ab") as handle:
        handle.write(smuggled)
    original_bytes = open(source_logo, "rb").read()

    payload = base64.b64encode(original_bytes).decode("ascii")
    status, body = post("/api/report-branding", production="THE SHOW",
                        logo_data=f"data:image/png;base64,{payload}")
    check("a picked image is accepted", status == 200, body)
    stored = body.get("logo", "")
    check("the logo is kept in the program's own folder, not where it was picked",
          bool(stored) and stored != source_logo
          and stored == pdfreport.logo_store_path(), stored)
    check("the stored logo exists and is a PNG",
          os.path.isfile(stored) and open(stored, "rb").read(8) == b"\x89PNG\r\n\x1a\n")
    check("the stored logo is a re-encode, not the bytes that arrived",
          smuggled not in open(stored, "rb").read())
    check("the file the operator picked is untouched",
          open(source_logo, "rb").read() == original_bytes)
    check("the production name is stored alongside it",
          db.setting(pdfreport.SETTING_PRODUCTION) == "THE SHOW")

    preview = c.get("/api/report-branding/logo")
    check("the interface can show the stored logo",
          preview.status_code == 200 and preview.data[:8] == b"\x89PNG\r\n\x1a\n",
          preview.status_code)

    if pdf_ok:
        branded = pdfreport.build(day_id, "table",
                                  out_path=os.path.join(RIG, "report_branded.pdf"))
        check("a branded report is produced with the logo on it",
              os.path.getsize(branded) > 1000)

    # A typed path is copied too, for the same reason: a path into a show folder
    # stops resolving the moment that drive is unmounted.
    status, body = post("/api/report-branding", production="THE SHOW",
                        logo=source_logo)
    check("a typed path is copied rather than referenced",
          status == 200 and body.get("logo") != source_logo, body)

    status, body = post("/api/report-branding", production="THE SHOW",
                        logo=os.path.join(RIG, "does_not_exist.png"))
    check("a path that is not there is refused with a reason",
          status == 400 and "No file at" in body.get("error", ""), body)

    not_an_image = base64.b64encode(b"MZ\x90\x00 this is not an image").decode("ascii")
    status, body = post("/api/report-branding", production="THE SHOW",
                        logo_data=not_an_image)
    check("a file that is not an image is refused with a reason",
          status == 400 and "image" in body.get("error", "").lower(), body)

    oversize = base64.b64encode(b"\x00" * (pdfreport.MAX_LOGO_BYTES + 1)).decode("ascii")
    status, body = post("/api/report-branding", production="THE SHOW",
                        logo_data=oversize)
    check("an oversized image is refused before it is decoded as one",
          status == 400, status)

    kept = db.setting(pdfreport.SETTING_LOGO)
    status, body = post("/api/report-branding", production="THE SHOW", clear_logo=True)
    check("removing the logo clears the setting", status == 200 and not body.get("logo"))
    check("removing the logo removes only this program's copy",
          not os.path.isfile(kept) and os.path.isfile(source_logo))
    check("the preview says there is nothing to show",
          c.get("/api/report-branding/logo").status_code == 404)

    # And the report still builds with no logo at all.
    if pdf_ok:
        plain = pdfreport.build(day_id, "table",
                                out_path=os.path.join(RIG, "report_plain.pdf"))
        check("a report with no logo is still produced", os.path.getsize(plain) > 1000)

check("the interface offers a picker as well as a typed path",
      'type="file"' in open("index.html", encoding="utf-8").read() and
      "sendLogo" in open("app.js", encoding="utf-8").read())
server_source = open("app.py", encoding="utf-8").read()
check("a chosen image travels as JSON, so the cross-origin guard is untouched",
      "logo_data" in server_source
      and "request.files" not in server_source
      and 'request.is_json' in server_source
      and 'Requests must be sent as application/json' in server_source)

print("\n[26] the track")

ui_html = open("index.html", encoding="utf-8").read()
ui_js = open("app.js", encoding="utf-8").read()
ui_css = open("app.css", encoding="utf-8").read()

# It used to be six chips in the top-right corner of the header, which is the
# least-looked-at part of a window. If it goes back in there, nobody finds it.
check("the track is its own region, not tucked into the header",
      '<nav class="track"' in ui_html and
      ui_html.index('</header>') < ui_html.index('id="stepTracker"'))
check("the track is announced to assistive technology",
      'aria-label="Progress through this shoot day"' in ui_html)
check("the current step is marked as current",
      'aria-current="step"' in ui_js)
# Colour alone is not a status. Someone reading this with a screen reader, or
# on a cart monitor with the contrast turned down, still needs the words.
check("each step says its state out loud, not only in colour",
      'aria-label="${esc(st.label)}' in ui_js and ", ${state}" in ui_js
      and 'st.blocked ? "blocked"' in ui_js)

# A rail of lamps does not answer "which of these six is my problem right now".
check("the track carries a plain sentence about what to do next",
      'id="trackHint"' in ui_html and "Next — " in ui_js)
check("every step can explain itself", ui_js.count("hint:") >= 6)
check("steps carry counts, so the track is progress and not just lamps",
      "step-count" in ui_js and "step-count" in ui_css)

# The destinations step is the one that blocks, and it must say by how much.
check("a short destination count says how many more drives are needed",
      "Add ${plural(need - drives" in ui_js)

print("\n[27] reading order and what is reachable")

# The numbered panels read 1,2,3 down the left column and 4,5,6 down the right,
# while the eye goes left to right. Saying what each column is for is cheaper
# than fighting the layout.
check("each column says what it is for", ui_html.count('class="col-cap"') == 2)

# Dimmed, never hidden: a tool whose controls vanish mid-shoot reads as broken.
check("phases that cannot run yet are dimmed rather than removed",
      "setWaiting(" in ui_js and
      all(f'id="{name}"' in ui_html and f'id="{name}" hidden' not in ui_html
          for name in ("syncPanel", "proxyPanel", "handoffPanel")))
check("the accent stripe is status, not decoration",
      ".panel.waiting{--panel-accent" in ui_css)
check("sync, proxies and handoff wait on a cleared card",
      "anyCleared" in ui_js and ui_js.count("setWaiting(") >= 5)
check("a first run explains what a shoot day even is",
      'id="firstRun"' in ui_html and "firstRun" in ui_js)

# The wordmark was pulling the eye away from the thing that orients you.
check("the logo does not out-shout the track",
      ".brand-logo{height:34px" in ui_css)

print("\n[28] readable at 4am")

import re as _re


def _relative_luminance(hex_colour):
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    la, lb = _relative_luminance(a), _relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


tokens = dict(_re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})", ui_css))
surfaces = ["bg", "panel", "panel-2"]
check("the palette still defines its surfaces and text colours",
      all(name in tokens for name in surfaces + ["text", "dim", "dim-2"]),
      ", ".join(sorted(tokens)))

# This interface is read on a cart monitor, at night, by someone who has been
# awake for sixteen hours. Every colour that carries words has to clear WCAG AA
# for body text on every surface it is used on. Status colours included: a
# warning nobody can read is not a warning.
for name in ("text", "dim", "dim-2", "safe", "work", "danger", "cyan"):
    if name not in tokens:
        continue
    worst = min(contrast(tokens[name], tokens[surface]) for surface in surfaces)
    check(f"--{name} is readable on every surface ({worst:.2f}:1, AA needs 4.5)",
          worst >= 4.5, tokens[name])

# Secondary text must still read as secondary, or the hierarchy is gone.
if "dim-2" in tokens and "dim" in tokens:
    check("the quieter text colour is genuinely quieter than the main one",
          contrast(tokens["dim-2"], tokens["bg"]) < contrast(tokens["dim"], tokens["bg"]))

check("prose is set in a face made for prose",
      "--sans:" in ui_css and "font-family:var(--sans)" in ui_css)
check("paths, hashes and timecode keep the monospace face", "--mono:" in ui_css)

# A control that never stops asking for attention has stopped being a signal.
check("nothing on screen pulses forever",
      "infinite" not in ui_css, "an infinite animation is still defined")

print("\n[29] the interface calls only functions that exist")

# A rewrite of one part of app.js can quietly delete another. The browser only
# says so at runtime, in a panel nobody may look at during the rig, and this
# file is not run through a JavaScript engine — so check it here instead.
defined = set(_re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", ui_js,
                          _re.MULTILINE))
defined |= set(_re.findall(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", ui_js,
                           _re.MULTILINE))

refresh_body = ui_js.split("async function refresh() {", 1)[1].split("\nasync function", 1)[0]
called = set(_re.findall(r"\b([a-z][A-Za-z0-9_$]*)\s*\(", refresh_body))
builtins = {"if", "for", "while", "switch", "catch", "return", "typeof", "await",
            "some", "every", "filter", "map", "find", "reduce", "includes",
            "toggle", "esc", "api", "String", "Boolean", "Number",
            "setTimeout", "clearTimeout",
            # "var(" appears inside the CSS in a template string, not as a call.
            "var"}
missing = sorted(name for name in called - builtins
                 if name not in defined and f".{name}(" not in refresh_body)
check("every function the refresh loop calls is defined", not missing,
      ", ".join(missing))

# The two the interface would silently lose most easily, named outright.
for name in ("renderTools", "renderReports", "renderSteps", "renderCards",
             "renderDestSlots", "renderJob", "setWaiting", "computeSteps"):
    check(f"{name}() exists", name in defined)

print("\n[30] report thumbnails are cosmetic and never touch the record")

import thumbnails

gate_before = reports.card_status(cam_id)["safe"]

check("nothing is cached before anything asks", all(
    thumbnails.cached_frame(row["src_hash"]) is None
    for row in db.q("SELECT src_hash FROM files WHERE card_id=? AND category='video'",
                    (cam_id,))))

# The rig's camera clips are synthetic bytes, so a real decode of them fails.
# That is the first thing worth proving: a clip nobody can draw is a gap in a
# report, never an error and never a changed record.
real_row = db.q1("SELECT * FROM files WHERE card_id=? AND category='video' "
                 "ORDER BY rel_path LIMIT 1", (cam_id,))
check("an undecodable clip simply has no frame",
      thumbnails.frame_for(dict(real_row), day_id) is None)
check("a failed frame does not change format clearance",
      reports.card_status(cam_id)["safe"] == gate_before)

# Now with a decoder that works, so the rest of the behaviour can be seen.
extract_calls = []
real_extract = thumbnails._extract


# A real 48x27 JPEG, so the PDF path is exercised with something reportlab can
# actually place, and so this section does not depend on Pillow being present.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4p"
    "LSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09P"
    "T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAbADADASIA"
    "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB"
    "AQAAAAAAAAAAAAAAAAAAAAH/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCegKgA"
    "AAAAAAAAD//Z")


def fake_extract(input_path, output_path, seek):
    extract_calls.append({"input": input_path, "output": output_path, "seek": seek})
    with open(output_path, "wb") as handle:
        handle.write(TINY_JPEG)
    return True


thumbnails._extract = fake_extract
try:
    frame = thumbnails.frame_for(dict(real_row), day_id)
    check("a frame is made and cached under the file's content hash",
          frame == thumbnails.cache_path(real_row["src_hash"])
          and os.path.isfile(frame), frame)

    # The rule the proxy builder already follows, and the reason report frames
    # still work after a card has cleared and been pulled.
    primary = db.q1("SELECT id, path FROM destinations WHERE day_id=? AND role='primary'"
                    " ORDER BY id LIMIT 1", (day_id,))
    verified = db.q1("SELECT dest_path FROM copies WHERE file_id=? AND destination_id=?"
                     " AND state='verified'", (real_row["id"], primary["id"]))
    check("the frame is pulled from the verified primary copy, not the card",
          extract_calls and extract_calls[-1]["input"] == verified["dest_path"],
          extract_calls[-1]["input"] if extract_calls else "no call")
    card_row = db.q1("SELECT source_path FROM cards WHERE id=?", (cam_id,))
    check("no frame was ever read off the source card",
          not any(call["input"].startswith(card_row["source_path"])
                  for call in extract_calls))

    # Re-generating a report must not re-decode the day.
    before = len(extract_calls)
    thumbnails.frame_for(dict(real_row), day_id)
    check("a cached frame is reused rather than decoded again",
          len(extract_calls) == before)

    # A frame comes off a verified copy or it does not come at all. A picture
    # of a half-written file, in a document that also says SAFE TO FORMAT, is
    # exactly the wrong thing to hand a producer.
    db.ex("UPDATE copies SET state='copied' WHERE file_id=? AND destination_id=?",
          (real_row["id"], primary["id"]))
    reports.invalidate_gate_cache(cam_id)
    check("an unverified copy is never used as a source for a frame",
          thumbnails._verified_primary_copy(real_row["id"], day_id) is None)
    db.ex("UPDATE copies SET state='verified' WHERE file_id=? AND destination_id=?",
          (real_row["id"], primary["id"]))
    reports.invalidate_gate_cache(cam_id)

    summary = thumbnails.build_for_day(day_id)
    check("building a day reports what it made and what it reused",
          summary["available"] and (summary["made"] + summary["cached"]) > 0,
          json.dumps(summary))

    # The report gatherer must never decode. It reads the cache and moves on.
    calls_before = len(extract_calls)
    data = reports.day_data(day_id)
    check("gathering a day never decodes a frame", len(extract_calls) == calls_before)
    frames = [m for entry in data["cards"] for m in entry["media"]
              if m.get("category") == "video" and m.get("thumbnail")]
    check("the report data carries the frames it found", bool(frames))
    check("sound rows are never given a frame",
          all(m.get("thumbnail") is None for entry in data["cards"]
              for m in entry["media"] if m.get("category") != "video"))

    if pdf_ok:
        sheet = pdfreport.build(day_id, "thumbnail",
                                out_path=os.path.join(RIG, "report_thumbnail.pdf"))
        check("the thumbnail PDF is written and is a PDF",
              open(sheet, "rb").read(5) == b"%PDF-" and os.path.getsize(sheet) > 1000,
              f"{os.path.getsize(sheet)} bytes")
        route = c.get(f"/api/day/{day_id}/report.pdf?layout=thumbnail")
        check("the thumbnail layout is served", route.status_code == 200, route.status_code)
finally:
    thumbnails._extract = real_extract

# With every frame thrown away the report must still build, unchanged in every
# way that matters.
removed = thumbnails.clear_cache()
check("clearing the cache removes only this machine's frames",
      removed > 0 and os.path.isfile(db.q1(
          "SELECT dest_path FROM copies WHERE file_id=? AND state='verified' LIMIT 1",
          (real_row["id"],))["dest_path"]))
if pdf_ok:
    bare = pdfreport.build(day_id, "thumbnail",
                           out_path=os.path.join(RIG, "report_no_frames.pdf"))
    check("a report with no frames at all is still produced",
          os.path.getsize(bare) > 1000)

check("the whole thumbnail pass never moved the format gate",
      reports.card_status(cam_id)["safe"] == gate_before)
check("the source card is untouched by thumbnails",
      len(os.listdir(os.path.join(CARDS, "A001_CFEXPRESS"))) > 0)

# Structural: this module is cosmetic, and must have no way to say otherwise.
thumb_source = open("thumbnails.py", encoding="utf-8").read()
check("thumbnails never write to the catalog",
      "db.ex(" not in thumb_source and "INSERT" not in thumb_source
      and "UPDATE" not in thumb_source)
check("thumbnails never touch the format gate",
      "card_status" not in thumb_source and "import reports" not in thumb_source)

print("\n[31] presets cannot loosen anything")

import presets

folder_id, error = presets.create_folder("SEASON 2")
check("a preset folder is created", folder_id and not error, error)
_, duplicate_error = presets.create_folder("SEASON 2")
check("two folders cannot share a name", bool(duplicate_error), duplicate_error)

_, error = presets.save_from_day(day_id, "", folder_id)
check("a preset must be named", bool(error), error)
_, error = presets.save_from_day(999999, "GHOST", folder_id)
check("a preset cannot be saved from a day that does not exist", bool(error), error)

db.setting(pdfreport.SETTING_PRODUCTION, "SEASON TWO")
preset_id, error = presets.save_from_day(day_id, "THREE DRIVES", folder_id)
check("the day's setup is saved as a preset", preset_id and not error, error)

saved = [dict(row) for row in db.q(
    "SELECT * FROM preset_destinations WHERE preset_id=? ORDER BY position",
    (preset_id,))]
check("the preset remembers every drive and its role",
      len(saved) == 2 and saved[0]["role"] == "primary",
      json.dumps([{"label": r["label"], "role": r["role"]} for r in saved]))
check("the preset remembers which physical drive each path was",
      all(row["device_id"] for row in saved))

# Saving twice under one name replaces it, rather than making a second.
again_id, error = presets.save_from_day(day_id, "THREE DRIVES", folder_id)
check("saving over a preset replaces it instead of piling up",
      again_id == preset_id and not error
      and db.q1("SELECT COUNT(*) c FROM presets WHERE name='THREE DRIVES'")["c"] == 1)
check("replacing a preset does not duplicate its drives",
      db.q1("SELECT COUNT(*) c FROM preset_destinations WHERE preset_id=?",
            (preset_id,))["c"] == 2)

# A fresh day to apply it to.
status, body = post("/api/day", project="PROJECT_01", name="DAY_02",
                    shoot_date="2026-01-17")
check("a second shoot day is created for the preset", status == 200, body)
second_day = body["day_id"]

result, error = presets.apply(preset_id, second_day)
check("applying a preset attaches its drives",
      not error and len(result["attached"]) == 2, error or json.dumps(result))
check("the attached drives are on the new day, with their roles",
      db.q1("SELECT COUNT(*) c FROM destinations WHERE day_id=? AND role='primary'",
            (second_day,))["c"] == 1
      and db.q1("SELECT COUNT(*) c FROM destinations WHERE day_id=?",
                (second_day,))["c"] == 2)

# Applying again is not an error and does not double anything up.
result, error = presets.apply(preset_id, second_day)
check("applying a preset twice skips what is already there",
      not error and len(result["skipped"]) == 2 and not result["attached"],
      json.dumps(result))
check("a second apply did not add a third destination",
      db.q1("SELECT COUNT(*) c FROM destinations WHERE day_id=?",
            (second_day,))["c"] == 2)

# The whole point: a preset is not a second, slacker route to a destination.
alias_id, _ = presets.save_from_day(day_id, "ALIAS TEST", 0)
db.ex("DELETE FROM preset_destinations WHERE preset_id=?", (alias_id,))
db.ex("""INSERT INTO preset_destinations
         (preset_id, position, label, path, role, device_id, volume_uuid)
         VALUES (?,?,?,?,?,?,?)""",
      (alias_id, 0, "SNEAKY", DRIVE_A_ALIAS, "backup", None, None))
result, error = presets.apply(alias_id, second_day)
check("a preset cannot slip a second folder on an existing drive past the gate",
      not error and len(result["failed"]) == 1
      and "physical drive" in result["failed"][0]["reason"],
      json.dumps(result))
check("the refused destination really was not added",
      db.q1("SELECT COUNT(*) c FROM destinations WHERE day_id=?",
            (second_day,))["c"] == 2)

# Drives get rotated, cloned and relabelled. A preset that refused to run
# because the disk is different would be useless; one that says nothing would
# let "SHOW_A already has Monday on it" quietly stop being true.
status, body = post("/api/day", project="PROJECT_01", name="DAY_03",
                    shoot_date="2026-01-18")
third_day = body["day_id"]
db.ex("UPDATE preset_destinations SET device_id='disk99' WHERE preset_id=?",
      (preset_id,))
result, error = presets.apply(preset_id, third_day)
check("a different physical drive at a familiar path is attached anyway",
      not error and len(result["attached"]) == 2, json.dumps(result))
check("and it is said out loud",
      len(result["warnings"]) == 2
      and "different physical drive" in result["warnings"][0],
      json.dumps(result["warnings"]))

# Branding travels with the preset, including its own copy of a logo.
if _pillow:
    logo_source = os.path.join(RIG, "preset_logo.png")
    _PILImage.new("RGBA", (40, 20), (0, 200, 120, 255)).save(logo_source)
    pdfreport.store_logo(open(logo_source, "rb").read())
    db.setting(pdfreport.SETTING_PRODUCTION, "THE OTHER SHOW")
    branded_id, _ = presets.save_from_day(day_id, "BRANDED", folder_id)
    own_logo = presets.preset_logo_path(branded_id)
    check("a preset keeps its own copy of its logo, not a pointer to the slot",
          os.path.isfile(own_logo) and own_logo != pdfreport.logo_store_path())

    pdfreport.clear_logo()
    db.setting(pdfreport.SETTING_PRODUCTION, "SOMETHING ELSE")
    presets.apply(branded_id, third_day)
    check("applying a preset restores its production name",
          db.setting(pdfreport.SETTING_PRODUCTION) == "THE OTHER SHOW")
    check("applying a preset restores its logo",
          os.path.isfile(db.setting(pdfreport.SETTING_LOGO) or ""))

    presets.delete_preset(branded_id)
    check("deleting a preset takes its own logo copy and nothing else",
          not os.path.isfile(own_logo) and os.path.isfile(logo_source))

# A day's project name is not something a preset gets to change quietly: that
# day already has folders on three drives with that name in the path.
check("applying a preset never renames the day's project",
      db.q1("SELECT project FROM days WHERE id=?", (third_day,))["project"]
      == "PROJECT_01")

# Deleting a container should not delete its contents by surprise.
presets.delete_folder(folder_id)
check("deleting a folder keeps its presets", db.q1(
    "SELECT folder_id FROM presets WHERE id=?", (preset_id,))["folder_id"] == 0)
check("the folder itself is gone",
      db.q1("SELECT id FROM preset_folders WHERE id=?", (folder_id,)) is None)
check("Ungrouped is not a folder anyone can delete",
      bool(presets.delete_folder(0)))

presets.delete_preset(preset_id)
check("deleting a preset removes its drives too",
      db.q1("SELECT COUNT(*) c FROM preset_destinations WHERE preset_id=?",
            (preset_id,))["c"] == 0)

# The HTTP surface, and the one refusal that matters on it.
check("presets are listed over HTTP", c.get("/api/presets").status_code == 200)
status, body = post("/api/preset/999999/apply")
check("applying a preset that does not exist is refused with a reason",
      status == 400 and "no longer exists" in body.get("error", ""), body)

# Structural: there is one way to attach a drive, and presets use it.
preset_source = open("presets.py", encoding="utf-8").read()
check("presets never insert a destination themselves",
      "INSERT INTO destinations" not in preset_source
      and "destinations.attach(" in preset_source)
check("presets never touch the format gate or the copy records",
      "card_status" not in preset_source and "INSERT INTO copies" not in preset_source
      and "UPDATE copies" not in preset_source)

print("\n[32] renaming happens on the destination and nowhere else")

import naming

# --- the pure part, where the risk actually lives ------------------------
rename_card = {"roll": "R001", "label": "R001_CARD", "kind": "camera", "id": 0}
rename_day = {"project": "PROJECT_01", "name": "DAY_01", "shoot_date": "2026-01-16"}
sample_files = [
    {"id": 11, "rel_path": "CLIPS/R001C002.MXF", "category": "video",
     "scene": "9", "take_name": "2"},
    {"id": 12, "rel_path": "CLIPS/R001C002.XML", "category": "sidecar"},
    {"id": 13, "rel_path": "CLIPS/R001C001.MXF", "category": "video",
     "scene": "9", "take_name": "1"},
    {"id": 14, "rel_path": "CLIPS/R001C001.XML", "category": "sidecar"},
]

check("the default pattern renames nothing",
      all(not e["renamed"] for e in
          naming.plan(sample_files, rename_card, rename_day)))

planned = naming.plan(sample_files, rename_card, rename_day, "{roll}_{index}")
by_source = {e["rel_path"]: e["dest_name"] for e in planned}
check("a pattern renames every file on the card",
      by_source["CLIPS/R001C001.MXF"] == "R001_0001.MXF"
      and by_source["CLIPS/R001C002.MXF"] == "R001_0002.MXF", json.dumps(by_source))

# Cameras write CLIP.MXF beside CLIP.XML. Break that pair and no NLE finds the
# metadata again.
check("a sidecar is renamed with the clip it belongs to",
      by_source["CLIPS/R001C001.XML"] == "R001_0001.XML"
      and by_source["CLIPS/R001C002.XML"] == "R001_0002.XML")

# The one that stops a resumed offload copying a second file beside the first.
shuffled = naming.plan(list(reversed(sample_files)), rename_card, rename_day,
                       "{roll}_{index}")
check("the plan is identical whatever order the files arrive in",
      {e["rel_path"]: e["dest_name"] for e in shuffled} == by_source)

check("missing metadata falls back to the original name, never to an empty one",
      naming.plan([{"id": 1, "rel_path": "X/notes.txt", "category": "sidecar"}],
                  rename_card, rename_day, "{scene}_{take}")[0]["dest_name"]
      == "notes.txt")

check("the extension is carried over exactly",
      naming.plan([{"id": 1, "rel_path": "A.MXF", "category": "video"}],
                  rename_card, rename_day, "{roll}")[0]["dest_name"] == "R001.MXF")

check("a pattern cannot contain a path separator",
      bool(naming.validate("{roll}/{index}")))
check("an unknown token is refused with the list of real ones",
      "{project}" in (naming.validate("{nope}") or ""))
check("an empty pattern is refused", bool(naming.validate("")))
check("unsafe characters never reach the filesystem",
      naming.sanitise("A/B:C*D") == "A_B_C_D")
check("a Windows reserved name is defused",
      naming.sanitise("CON").lower().startswith("con") and naming.sanitise("CON") != "CON")

colliding = naming.plan(sample_files, rename_card, rename_day, "{roll}_{scene}")
clashes = naming.collisions(colliding)
check("two takes that would share a name are reported, not silently suffixed",
      bool(clashes) and all(len(sources) == 2 for _, sources in clashes)
      and any("R001C001.MXF" in sources[0] + sources[1] for _, sources in clashes),
      json.dumps(clashes))

# --- and now for real, through the copy path -----------------------------
write_fixture("R001_CARD/CLIPS/R001C001.MXF", 210, 12 * 1024)
write_text_fixture("R001_CARD/CLIPS/R001C001.XML", "<clip>one</clip>\n")
write_fixture("R001_CARD/CLIPS/R001C002.MXF", 211, 12 * 1024)

status, body = post("/api/day", project="PROJECT_01", name="DAY_RENAME",
                    shoot_date="2026-01-19")
rename_day_id = body["day_id"]
for path, role in ((DRIVE_A, "primary"), (DRIVE_B, "backup")):
    post("/api/destination", path=path, label=os.path.basename(path), role=role)

status, body = post(f"/api/day/{rename_day_id}/naming",
                    pattern_camera="{day}_{roll}_{index}")
check("a naming pattern is stored on the day", status == 200, body)
status, body = post(f"/api/day/{rename_day_id}/naming", pattern_camera="{nope}")
check("a pattern that cannot work is refused while someone is looking at it",
      status == 400, body)

status, found = get("/api/scan")
fingerprint = next((c_["fingerprint"] for c_ in found["cards"]
                    if "R001_CARD" in c_["path"]), None)
check("the renaming card is found by the scanner", bool(fingerprint))
status, body = post("/api/register", fingerprint=fingerprint)
check("the renaming card registers", status == 200, body)
rename_card_id = body["card_id"]

status, preview = get(f"/api/day/{rename_day_id}/naming/preview")
this_card = next(card for card in preview["cards"] if card["card_id"] == rename_card_id)
check("the preview says what will change before anything is copied",
      this_card["renamed"] == this_card["total"] and this_card["total"] == 3,
      json.dumps(this_card["sample"]))

source_listing_before = sorted(os.listdir(os.path.join(CARDS, "R001_CARD", "CLIPS")))
source_bytes_before = open(os.path.join(CARDS, "R001_CARD/CLIPS/R001C001.MXF"), "rb").read()

status, body = post(f"/api/offload/{rename_card_id}")
check("the renaming offload starts", status == 200, body)
wait_job()

gate = reports.card_status(rename_card_id)
check("a renamed card clears the gate exactly as any other card does",
      gate["safe"], gate["reason"])

written = sorted(os.listdir(os.path.join(
    DRIVE_A, "PROJECT_01", "DAY_RENAME", "CAMERA", "R001", "CLIPS")))
check("files landed under the new names",
      written == ["DAY_RENAME_R001_0001.MXF", "DAY_RENAME_R001_0001.XML",
                  "DAY_RENAME_R001_0002.MXF"], ", ".join(written))

check("the source card still has its original names",
      sorted(os.listdir(os.path.join(CARDS, "R001_CARD", "CLIPS")))
      == source_listing_before, ", ".join(source_listing_before))
check("the source card's bytes are untouched",
      open(os.path.join(CARDS, "R001_CARD/CLIPS/R001C001.MXF"), "rb").read()
      == source_bytes_before)
check("the catalog still knows every file by its name on the card",
      db.q1("SELECT COUNT(*) c FROM files WHERE card_id=? AND rel_path LIKE ?",
            (rename_card_id, "CLIPS/R001C%"))["c"] == 3)

# The manifest, and both names.
rename_dest = db.q1("SELECT id FROM destinations WHERE day_id=? AND role='primary'",
                    (rename_day_id,))
mhl_path = reports.write_mhl(rename_card_id, rename_dest["id"])
check("a manifest is written for a renamed card", bool(mhl_path), mhl_path)
mhl_body = open(mhl_path, encoding="utf-8").read()
check("the manifest is still well-formed XML",
      ET.fromstring(mhl_body) is not None)
check("the manifest lists the names the files were written under",
      "DAY_RENAME_R001_0001.MXF" in mhl_body)
check("the manifest also records what each file was called on the card",
      "<!-- source: R001C001.MXF -->" in mhl_body)
check("no extra element was added inside <hash>, which would break the schema",
      all(child.tag in ("file", "size", "creationdate", "lastmodificationdate",
                        "md5", "sha1", "xxhash", "xxhash64", "xxhash64be",
                        "null", "hashdate")
          for hash_el in ET.fromstring(mhl_body).findall("hash")
          for child in hash_el))

names_csv = os.path.splitext(mhl_path)[0] + "_NAMES.csv"
check("a machine-readable name map is written beside the manifest",
      os.path.isfile(names_csv))
if os.path.isfile(names_csv):
    mapping = list(csv.reader(open(names_csv, encoding="utf-8")))
    check("the name map has both names and the hash for every renamed file",
          mapping[0] == ["original", "renamed", "size", "xxhash64be"]
          and len(mapping) == 4, json.dumps(mapping[:2]))

# Half a card named one way and half the other is not a resumable state.
db.ex("UPDATE days SET pattern_camera=? WHERE id=?",
      ("{roll}_{index}", rename_day_id))
refusal = offload.preflight(rename_card_id, "offload", 2)
check("changing the pattern on a half-copied card is refused with both names",
      refusal and "was already copied as" in refusal, refusal)
db.ex("UPDATE days SET pattern_camera=? WHERE id=?",
      ("{day}_{roll}_{index}", rename_day_id))
check("putting the pattern back makes the card workable again",
      offload.preflight(rename_card_id, "offload", 2) is None)

# A colliding pattern must never reach the copy loop.
db.ex("UPDATE days SET pattern_camera=? WHERE id=?", ("{roll}", rename_day_id))
refusal = offload.preflight(rename_card_id, "offload", 2)
check("a colliding pattern is refused before a byte is copied",
      refusal and "would both be written" in refusal, refusal)
db.ex("UPDATE days SET pattern_camera=? WHERE id=?",
      ("{day}_{roll}_{index}", rename_day_id))

# The day report has to show both names too, since it is what gets forwarded.
rename_report = reports.render_html(reports.day_data(rename_day_id))
check("the day report shows what each file was called on the card",
      "R001C001.MXF" in rename_report)
check("the day report shows what it was written as",
      "DAY_RENAME_R001_0001.MXF" in rename_report)
check("the day report says renaming happened on the destination only",
      "The card was not touched" in rename_report)

# Renaming added a plan argument to the collision check. That check is also the
# one piece of the copy path exercisable with nothing but relative paths, and it
# has to stay that way — section [19] calls it exactly like this.
check("the collision check still works on rows with no catalog behind them",
      bool(offload._target_collision(
          [{"rel_path": os.path.join("CRACK_R01", "take1.wav")},
           {"rel_path": "take1.wav"}],
          collision_dests, collision_day, collision_card)))

# Structural: naming decides names and nothing else.
naming_source = open("naming.py", encoding="utf-8").read()
check("naming never touches the database or the filesystem",
      "import db" not in naming_source and "open(" not in naming_source
      and "card_status" not in naming_source)

print("\n[33] a filtered card can never read plain SAFE TO FORMAT")

# --- the pure part -------------------------------------------------------
check("an empty filter keeps everything",
      naming.is_included("anything.MXF", "") and naming.is_included("a/b.THM", "   "))
check("an exclude pattern matches by filename anywhere on the card",
      not naming.is_included("CLIPS/SUB/A001.THM", "-*.THM")
      and naming.is_included("CLIPS/SUB/A001.MXF", "-*.THM"))
check("an include pattern means only those files are copied",
      naming.is_included("A.MXF", "*.MXF") and not naming.is_included("A.THM", "*.MXF"))
check("a path pattern is matched against the whole path",
      not naming.is_included("X/PROXY/a.MXF", "-*/PROXY/*")
      and naming.is_included("X/CLIPS/a.MXF", "-*/PROXY/*"))
check("filters are described in words for the interface",
      "never" in naming.describe_filters("-*.THM"))
check("split_by_filter is pure and keeps order",
      naming.split_by_filter([{"rel_path": "a.MXF"}, {"rel_path": "b.THM"}],
                             "-*.THM")[0][0]["rel_path"] == "a.MXF")

# --- and now against the gate, which is the whole point ------------------
write_fixture("F001_CARD/CLIPS/F001C001.MXF", 220, 8 * 1024)
write_fixture("F001_CARD/CLIPS/F001C001.THM", 221, 1024)
write_fixture("F001_CARD/CLIPS/F001C002.MXF", 222, 8 * 1024)
write_fixture("F001_CARD/CLIPS/F001C002.THM", 223, 1024)

status, body = post("/api/day", project="PROJECT_01", name="DAY_FILTER",
                    shoot_date="2026-01-20")
filter_day_id = body["day_id"]
for path, role in ((DRIVE_A, "primary"), (DRIVE_B, "backup")):
    post("/api/destination", path=path, label=os.path.basename(path), role=role)
post(f"/api/day/{filter_day_id}/naming", pattern_camera="{original}",
     filter_camera="-*.THM")

status, found = get("/api/scan")
fingerprint = next((c_["fingerprint"] for c_ in found["cards"]
                    if "F001_CARD" in c_["path"]), None)
status, body = post("/api/register", fingerprint=fingerprint)
filter_card_id = body["card_id"]

# Previewing must not change anything about the card.
status, preview = get(f"/api/day/{filter_day_id}/naming/preview")
this_card = next(card for card in preview["cards"]
                 if card["card_id"] == filter_card_id)
check("the preview says which files a filter would skip, before anything runs",
      this_card["skipped_total"] == 2
      and all(name.endswith(".THM") for name in this_card["skipped"]),
      json.dumps(this_card["skipped"]))
check("previewing does not mark anything excluded",
      db.q1("SELECT COUNT(*) c FROM files WHERE card_id=? AND COALESCE(excluded,0)=1",
            (filter_card_id,))["c"] == 0)

status, body = post(f"/api/offload/{filter_card_id}")
check("the filtered offload starts", status == 200, body)
wait_job()

gate = reports.card_status(filter_card_id)
check("the copied files are verified on both drives", gate["verified"] == 2, gate)
check("the card is clear to format", gate["safe"], gate["reason"])

# The entire reason this feature was built carefully.
check("but it is NOT the plain green sentence",
      gate["stamp"]["label"] != "SAFE TO FORMAT"
      and "CLEARED WITH EXCLUSIONS" in gate["stamp"]["label"], gate["stamp"])
check("and it is amber, not green", gate["stamp"]["tone"] == "warn", gate["stamp"])
check("the skipped files are named, not just counted",
      len(gate["excluded"]) == 2
      and all(name.endswith(".THM") for name in gate["excluded"]), gate["excluded"])
check("the reason says how many were deliberately not copied",
      "deliberately not copied" in gate["reason"], gate["reason"])

filtered_dir = os.path.join(DRIVE_A, "PROJECT_01", "DAY_FILTER", "CAMERA",
                            "F001", "CLIPS")
written = sorted(os.listdir(filtered_dir))
check("the excluded files really were not written",
      written == ["F001C001.MXF", "F001C002.MXF"], ", ".join(written))
check("the excluded files are still on the card, untouched",
      os.path.isfile(os.path.join(CARDS, "F001_CARD/CLIPS/F001C001.THM")))

# Paperwork has to carry it too, or the amber on screen is the only warning.
filter_dest = db.q1("SELECT id FROM destinations WHERE day_id=? AND role='primary'",
                    (filter_day_id,))
filter_mhl = reports.write_mhl(filter_card_id, filter_dest["id"])
check("a manifest is still written for what was copied", bool(filter_mhl))
filter_mhl_body = open(filter_mhl, encoding="utf-8").read()
check("the manifest says out loud that it does not cover everything",
      "deliberately" in filter_mhl_body and "_EXCLUDED.csv" in filter_mhl_body)
check("the manifest is still well-formed XML",
      ET.fromstring(filter_mhl_body) is not None)
excluded_csv = os.path.splitext(filter_mhl)[0] + "_EXCLUDED.csv"
check("the skipped files are listed in a sidecar beside the manifest",
      os.path.isfile(excluded_csv)
      and len(list(csv.reader(open(excluded_csv, encoding="utf-8")))) == 3)

filter_report = reports.render_html(reports.day_data(filter_day_id))
check("the day report shows the amber verdict, not the green one",
      "CLEARED WITH EXCLUSIONS" in filter_report
      and "stamp warn" in filter_report)
check("the day report names the skipped files",
      "F001C001.THM" in filter_report and "Deliberately not copied" in filter_report)

if pdf_ok:
    filtered_pdf = pdfreport.build(filter_day_id, "table",
                                   out_path=os.path.join(RIG, "report_filtered.pdf"))
    check("the PDF is produced for a filtered card too",
          os.path.getsize(filtered_pdf) > 1000)

# A filter changed after the fact must not rewrite history. Two separate
# properties, and the difference between them matters.
db.ex("UPDATE days SET filter_camera=? WHERE id=?", ("-*.MXF", filter_day_id))
reports.invalidate_gate_cache(filter_card_id)
check("changing a filter does not touch a card that is not being offloaded",
      reports.card_status(filter_card_id)["stamp"]["tone"] == "warn"
      and len(reports.card_status(filter_card_id)["excluded"]) == 2,
      "the flags are written when an offload decides to skip, not when a "
      "setting changes")

# Now actually start deciding again, which is the only moment the flags move.
card_row = db.q1("SELECT * FROM cards WHERE id=?", (filter_card_id,))
day_row = db.q1("SELECT * FROM days WHERE id=?", (filter_day_id,))
offload.apply_filters(card_row, day_row)
check("a file that is already copied is never marked excluded afterwards",
      db.q1("SELECT COUNT(*) c FROM files WHERE card_id=? AND rel_path LIKE '%.MXF' "
            "AND COALESCE(excluded,0)=1", (filter_card_id,))["c"] == 0)

# And the other half, which is not history being rewritten but intent changing:
# the THMs are no longer filtered out, so they are files this card is supposed
# to have and does not. The gate must say so rather than staying green out of
# politeness.
reports.invalidate_gate_cache(filter_card_id)
reopened = reports.card_status(filter_card_id)
check("files a filter stops excluding become copyable again",
      db.q1("SELECT COUNT(*) c FROM files WHERE card_id=? AND rel_path LIKE '%.THM' "
            "AND COALESCE(excluded,0)=1", (filter_card_id,))["c"] == 0)
check("and the card stops being clear, because it is now genuinely incomplete",
      not reopened["safe"] and reopened["stamp"]["tone"] == "bad",
      reopened["reason"])

# Put it back the way it was and confirm it settles to amber again.
db.ex("UPDATE days SET filter_camera=? WHERE id=?", ("-*.THM", filter_day_id))
offload.apply_filters(db.q1("SELECT * FROM cards WHERE id=?", (filter_card_id,)),
                      db.q1("SELECT * FROM days WHERE id=?", (filter_day_id,)))
reports.invalidate_gate_cache(filter_card_id)
restored = reports.card_status(filter_card_id)
check("restoring the filter returns the card to cleared-with-exclusions",
      restored["safe"] and restored["stamp"]["tone"] == "warn", restored["reason"])

# Re-reading the drives must not move a single flag: it is checking what is
# there, not deciding what to copy.
verify_source = open("offload.py", encoding="utf-8").read()
check("only an offload applies filters — a verify run never does",
      'if self.mode == "offload":\n            apply_filters' in verify_source)

# A filter that would leave nothing to copy is refused rather than obeyed.
db.ex("UPDATE days SET filter_camera=? WHERE id=?", ("-*", filter_day_id))
db.ex("UPDATE files SET excluded=0 WHERE card_id=?", (filter_card_id,))
db.ex("DELETE FROM copies WHERE file_id IN "
      "(SELECT id FROM files WHERE card_id=?)", (filter_card_id,))
refusal = offload.preflight(filter_card_id, "offload", 2)
check("a filter that would skip every file is refused, not obeyed",
      refusal and "Nothing would be copied" in refusal, refusal)
db.ex("UPDATE days SET filter_camera=? WHERE id=?", ("-*.THM", filter_day_id))

# Structural: the interface no longer writes the verdict's words itself.
check("only one place decides what the stamp says",
      '"SAFE TO FORMAT"' not in open("app.js", encoding="utf-8").read()
      .split("function renderCards")[1].split("function renderDestSlots")[0]
      or "g.stamp.label" in open("app.js", encoding="utf-8").read())
check("the gate ships its stamp so every renderer reads the same words",
      "gate[\"stamp\"]" in open("reports.py", encoding="utf-8").read())

print("\n[34] drive tools")

import drivetools

# --- the registry, which is about drives rather than mount points --------
drive_row, error = drivetools.register(DRIVE_A, "SHOW_A_01")
check("a drive is registered by its identity, not its path",
      drive_row and not error and drive_row["identity"], error)
check("the label given is the label kept", drive_row["label"] == "SHOW_A_01")

again, _ = drivetools.register(DRIVE_A)
check("registering the same drive twice does not make a second row",
      again["id"] == drive_row["id"]
      and db.q1("SELECT COUNT(*) c FROM drives")["c"] == 1)
check("and re-registering without a label keeps the one it had",
      again["label"] == "SHOW_A_01")

# DRIVE_A_ALIAS is a second folder on the same physical drive. A registry that
# counted it as another drive would be the same lie the format gate refuses.
alias_row, _ = drivetools.register(DRIVE_A_ALIAS)
check("two folders on one physical drive are one drive in the registry",
      alias_row["id"] == drive_row["id"],
      f"{alias_row['id']} vs {drive_row['id']}")

# --- the speed test ------------------------------------------------------
job, error = drivetools.start("speed", DRIVE_A,
                              test_bytes=drivetools.MIN_TEST_BYTES)
check("a speed test starts", job and not error, error)
for _ in range(200):
    if drivetools.current() is None:
        break
    time.sleep(0.1)
snapshot = drivetools.status_snapshot()
check("the speed test finishes", snapshot["state"] == "done", snapshot["state"])
speed = snapshot["result"]
check("it reports a write speed and a read speed",
      speed["write_mb_s"] > 0 and speed["read_mb_s"] > 0, json.dumps(speed))
check("the read back matched what was written", speed["verified"], json.dumps(speed))
check("and it says whether the cache was bypassed, rather than assuming",
      "cache_bypassed" in speed and speed["note"], json.dumps(speed))
if sys.platform == "darwin":
    check("on macOS the read is measured with the page cache bypassed",
          speed["cache_bypassed"],
          "a read speed measured out of RAM is the lie this program exists to refuse")

check("the speed test leaves nothing behind on the drive",
      not os.path.isdir(os.path.join(DRIVE_A, drivetools.SCRATCH_DIR)),
      os.listdir(DRIVE_A))
check("and it did not disturb any card's verdict",
      reports.card_status(cam_id)["safe"])

# --- the index -----------------------------------------------------------
job, error = drivetools.start("index", DRIVE_A, label="SHOW_A_01")
check("an index starts", job and not error, error)
for _ in range(300):
    if drivetools.current() is None:
        break
    time.sleep(0.1)
snapshot = drivetools.status_snapshot()
check("the index finishes", snapshot["state"] == "done", snapshot["state"])
index = snapshot["result"]
check("it counted the files that are actually on the drive",
      index["files"] > 0 and index["bytes"] > 0, json.dumps(
          {k: v for k, v in index.items() if k != "manifests"}))

# The point of reading manifests: a drive that has ever been offloaded can say
# what it holds without re-hashing a terabyte.
check("it read the manifests already sitting on the drive",
      len(index["manifests"]) >= 1,
      json.dumps(index["manifests"]))
check("and a manifest tells it the day and roll",
      any(m["roll"] for m in index["manifests"]), json.dumps(index["manifests"]))

listed = drivetools.known_drives()
this_drive = next(d for d in listed if d["id"] == drive_row["id"])
check("the registry reports what the drive holds",
      this_drive["indexed_files"] == index["files"]
      and this_drive["online"], json.dumps(
          {k: this_drive[k] for k in ("indexed_files", "online", "label")}))

# Re-indexing replaces rather than accumulates.
before = this_drive["indexed_files"]
job, _ = drivetools.start("index", DRIVE_A)
for _ in range(300):
    if drivetools.current() is None:
        break
    time.sleep(0.1)
check("re-indexing replaces the snapshot instead of doubling it",
      next(d for d in drivetools.known_drives()
           if d["id"] == drive_row["id"])["indexed_files"] == before)

# An index is a snapshot and must never be mistaken for a verification.
tools_source = open("drivetools.py", encoding="utf-8").read()
check("drive tools never write to the copies table",
      "INSERT INTO copies" not in tools_source
      and "UPDATE copies" not in tools_source)
check("drive tools never touch the format gate",
      "card_status" not in tools_source)
check("the only thing removed from a drive is the test's own scratch file",
      tools_source.count("os.remove(") == 1 and "SCRATCH_DIR" in tools_source)

# --- the job gate --------------------------------------------------------
check("a drive job counts as a running job like any other",
      "drivetools.current()" in open("app.py", encoding="utf-8").read())
status, body = post("/api/drive/index", path="/etc")
check("a drive outside the allowed roots is refused",
      status == 403, body)
status, body = post("/api/drive/speed", path=os.path.join(RIG, "not_a_drive"))
check("a path that is not a mounted folder is refused with a reason",
      status in (400, 409) and "error" in body, body)

drivetools.forget(drive_row["id"])
check("forgetting a drive removes it and its index",
      db.q1("SELECT COUNT(*) c FROM drives")["c"] == 0
      and db.q1("SELECT COUNT(*) c FROM drive_index")["c"] == 0)
check("but the drive itself is untouched", os.path.isdir(DRIVE_A)
      and len(os.listdir(DRIVE_A)) > 0)

print("\n[35] the README's promises about this file")

# The README now points strangers at this file as the reason to trust the
# program. If it names a section that has moved, the argument collapses at the
# first person who checks — which is the only kind of person it is for.
rig_source = open("test_rig.py", encoding="utf-8").read()
readme_text = open("README.md", encoding="utf-8").read()

sections = set(_re.findall(r'print\("\\n\[(\d+)\]', rig_source))
promised = set(_re.findall(r"\*\*\[(\d+)\](?:, \[(\d+)\])?\*\*", readme_text))
promised = {number for pair in promised for number in pair if number}
missing = sorted(promised - sections, key=int)
check("every section the README points at exists", not missing,
      ", ".join(missing))

check("the README tells people to run it, with the real command",
      ".venv/bin/python test_rig.py" in readme_text
      and "Don't trust it" in readme_text)
check("and gives them somewhere to report a failure",
      "hello@cnfworks.com" in readme_text.split("Don't trust it")[1][:2000])

claimed = _re.search(r"\*\*(\d+) checks across (\d+) sections\*\*", readme_text)
if claimed:
    actual_sections = len(sections)
    check("the section count in the README is true",
          int(claimed.group(2)) == actual_sections,
          f"README says {claimed.group(2)}, there are {actual_sections}")
    check("the check count in the README is not an overclaim",
          rig_source.count("check(") >= int(claimed.group(1)),
          f"README says more than {claimed.group(1)}")
else:
    check("the README's count of checks and sections is stated in a checkable way",
          False, "the phrasing changed; update this test with it")

print("\n[36] the checksum is a choice, and the manifest never lies about it")

import hashing

check("xxHash64 is the default, so nothing changes for anyone who ignores this",
      hashing.DEFAULT == "xxh64" and hashing.normalise(None) == "xxh64")
check("rows written before this setting existed are read as xxHash64",
      hashing.normalise("") == "xxh64" and hashing.normalise("nonsense") == "xxh64")

# Every algorithm offered must have an element the MHL schema actually defines.
# One that does not would have to be written under a neighbouring name, which
# is a manifest that lies about what is in it. 1.1 names five digests and 2.0
# names six, so an algorithm may legitimately have no 1.1 name — but then the
# rule is that no 1.1 manifest gets written for it at all, which section [37]
# proves. What it may never have is a name from a schema that does not use it.
mhl_elements = {"md5", "sha1", "xxhash", "xxhash64", "xxhash64be", "null"}
mhl2_elements = {"md5", "sha1", "xxh64", "xxh3", "xxh128", "c4"}
check("every checksum offered can be named truthfully in an ASC MHL 1.1, "
      "or admits it cannot be named there at all",
      all(spec["mhl_element"] in mhl_elements or spec["mhl_element"] is None
          for spec in hashing.ALGORITHMS.values()),
      ", ".join(f"{k}:{v['mhl_element']}" for k, v in hashing.ALGORITHMS.items()))
check("and every one of them has a real ASC MHL 2.0 element name",
      all(spec["mhl2_element"] in mhl2_elements
          for spec in hashing.ALGORITHMS.values()),
      ", ".join(f"{k}:{v['mhl2_element']}" for k, v in hashing.ALGORITHMS.items()))
check("the interface can say which manifests a choice will produce",
      all(choice["manifests"] and "ASC MHL 2.0" in choice["manifests"]
          for choice in hashing.choices()),
      json.dumps({c["id"]: c["manifests"] for c in hashing.choices()}))

sample = os.path.join(RIG, "checksum_sample.bin")
with open(sample, "wb") as handle:
    handle.write(b"second hand smoke" * 1024)
digests = {algo: offload.hash_file(sample, algo=algo)[0] for algo in hashing.ALGORITHMS}
check("each algorithm produces a different digest of the same bytes",
      len(set(digests.values())) == len(digests), json.dumps(digests))
check("and each is stable across two reads",
      all(offload.hash_file(sample, algo=algo)[0] == digest
          for algo, digest in digests.items()))
check("the default digest is unchanged from what this program has always written",
      digests["xxh64"] == offload.hash_file(sample)[0])

# --- through a real offload ---------------------------------------------
write_fixture("H001_CARD/CLIPS/H001C001.MXF", 240, 8 * 1024)
write_fixture("H001_CARD/CLIPS/H001C002.MXF", 241, 8 * 1024)

status, body = post("/api/day", project="PROJECT_01", name="DAY_MD5",
                    shoot_date="2026-01-21")
md5_day = body["day_id"]
for path, role in ((DRIVE_A, "primary"), (DRIVE_B, "backup")):
    post("/api/destination", path=path, label=os.path.basename(path), role=role)

status, body = post(f"/api/day/{md5_day}/checksum", hash_algo="md5")
check("a shoot day can be set to another checksum", status == 200, body)
status, body = post(f"/api/day/{md5_day}/checksum", hash_algo="crc32")
check("an algorithm this program cannot name in a manifest is refused",
      status == 400, body)

status, found = get("/api/scan")
fingerprint = next((c_["fingerprint"] for c_ in found["cards"]
                    if "H001_CARD" in c_["path"]), None)
status, body = post("/api/register", fingerprint=fingerprint)
md5_card = body["card_id"]

status, body = post(f"/api/offload/{md5_card}")
check("an offload under a different checksum starts", status == 200, body)
wait_job()

gate = reports.card_status(md5_card)
check("the card clears, verified with the checksum that was chosen",
      gate["safe"], gate["reason"])
stored = db.q("SELECT src_hash, hash_algo FROM files WHERE card_id=?", (md5_card,))
check("every file records which checksum it was hashed with",
      all(row["hash_algo"] == "md5" for row in stored),
      json.dumps([dict(r) for r in stored]))
check("and the digests really are MD5",
      all(len(row["src_hash"]) == 32 for row in stored))

md5_dest = db.q1("SELECT id FROM destinations WHERE day_id=? AND role='primary'",
                 (md5_day,))
md5_mhl = reports.write_mhl(md5_card, md5_dest["id"])
check("a manifest is written", bool(md5_mhl))
md5_body = open(md5_mhl, encoding="utf-8").read()
check("the manifest names the digest it is actually carrying",
      "<md5>" in md5_body and "xxhash64be" not in md5_body)
check("the manifest is still well-formed XML",
      ET.fromstring(md5_body) is not None)
check("and every hash element is one the MHL schema defines",
      all(child.tag in ("file", "size", "creationdate", "lastmodificationdate",
                        "md5", "sha1", "xxhash", "xxhash64", "xxhash64be",
                        "null", "hashdate")
          for hash_el in ET.fromstring(md5_body).findall("hash")
          for child in hash_el))

# Half a card hashed one way and half the other is not a resumable state.
status, body = post(f"/api/day/{md5_day}/checksum", hash_algo="sha1")
check("changing the checksum on a card that is already copied is refused",
      status == 400 and "already copied" in body.get("error", ""), body)
check("and the day keeps the checksum it was actually using",
      db.q1("SELECT hash_algo FROM days WHERE id=?", (md5_day,))["hash_algo"] == "md5")

db.ex("UPDATE days SET hash_algo=? WHERE id=?", ("sha1", md5_day))
refusal = offload.preflight(md5_card, "offload", 2)
check("the offload refuses too, in case the setting was changed another way",
      refusal and "already copied" in refusal, refusal)
db.ex("UPDATE days SET hash_algo=? WHERE id=?", ("md5", md5_day))
check("putting it back makes the card workable again",
      offload.preflight(md5_card, "offload", 2) is None)

# The internal checks are deliberately not configurable.
for module in ("proxies.py", "handoff.py", "thumbnails.py"):
    source = open(module, encoding="utf-8").read()
    check(f"{module} does not take its checksum from the day's setting",
          "algo_for(" not in source and "hash_algo" not in source)

print("\n[37] ASC MHL 2.0 — the ascmhl folder, and the chain that covers it")

import mhl2

V2 = "{urn:ASC:MHL:v2.0}"
CHAIN_NS = "{urn:ASC:MHL:DIRECTORY:v2.0}"

# --- the C4 ID ------------------------------------------------------------
# The chain file identifies every manifest by its C4 ID, so a wrong C4 encoder
# makes an unreadable chain. These vectors were produced by the reference
# implementation (Pomfort's `ascmhl` package) and are pinned here so that a
# future edit to the encoder cannot quietly change what we write.
check("the C4 ID of empty input matches the reference implementation",
      hashing.new("c4").hexdigest() ==
      "c459dsjfscH38cYeXXYogktxf4Cd9ibshE3BHUo6a58hBXmRQdZrAkZzsWcbWtDg5oQ"
      "stpDuni4Hirj75GEmTc1sFT",
      hashing.new("c4").hexdigest())
c4_hello = hashing.new("c4")
c4_hello.update(b"hello world")
check("and of 'hello world'",
      c4_hello.hexdigest() ==
      "c41yP4cqy7jmaRDzC2bmcGNZkuQb3VdftMk6YH7ynQ2Qw4zktKsyA9fk52xghNQNAdkp"
      "F9iFmFkKh2bNVG4kDWhsok",
      c4_hello.hexdigest())
check("every C4 ID is 90 characters and starts with c4",
      all(len(d) == 90 and d.startswith("c4")
          for d in (hashing.new("c4").hexdigest(), c4_hello.hexdigest())))

# --- an offload under an algorithm 1.1 cannot name ------------------------
write_fixture("I001_CARD/CLIPS/I001C001.MXF", 250, 8 * 1024)
write_fixture("I001_CARD/CLIPS/I001C002.MXF", 251, 8 * 1024)

status, body = post("/api/day", project="PROJECT_01", name="DAY_MHL2",
                    shoot_date="2026-01-22")
v2_day = body["day_id"]
for path, role in ((DRIVE_A, "primary"), (DRIVE_B, "backup")):
    post("/api/destination", path=path, label=os.path.basename(path), role=role)

status, body = post(f"/api/day/{v2_day}/checksum", hash_algo="xxh128")
check("a shoot day can be set to a checksum only ASC MHL 2.0 can name",
      status == 200, body)

status, found = get("/api/scan")
fingerprint = next((c_["fingerprint"] for c_ in found["cards"]
                    if "I001_CARD" in c_["path"]), None)
status, body = post("/api/register", fingerprint=fingerprint)
v2_card = body["card_id"]
status, body = post(f"/api/offload/{v2_card}")
check("the offload runs", status == 200, body)
wait_job()

gate = reports.card_status(v2_card)
check("the card clears", gate["safe"], gate["reason"])
check("and the digests really are 128-bit",
      all(len(row["src_hash"]) == 32 for row in
          db.q("SELECT src_hash FROM files WHERE card_id=?", (v2_card,))))

v2_dest = db.q1("SELECT id FROM destinations WHERE day_id=? AND role='primary'",
                (v2_day,))
written = reports.write_manifests(v2_card, v2_dest["id"])
check("a manifest is written", bool(written["paths"]), json.dumps(written))
check("nothing is warned about on a fresh folder", not written["warnings"],
      json.dumps(written["warnings"]))
check("and no ASC MHL 1.1 file is written for a digest 1.1 cannot name",
      not any(p.endswith(".mhl") and "ascmhl" not in p for p in written["paths"]),
      json.dumps(written["paths"]))
check("the 1.1 renderer refuses on its own too, however it is called",
      reports.write_mhl(v2_card, v2_dest["id"]) is None)

v2_path = written["paths"][-1]
v2_root = os.path.dirname(os.path.dirname(v2_path))
check("the manifest is inside an ascmhl folder at the top of the day folder",
      os.path.basename(os.path.dirname(v2_path)) == "ascmhl", v2_path)
check("and is named <number>_<roll>_<date>_<time>Z.mhl",
      _re.match(r"^\d{4}_.+_\d{4}-\d{2}-\d{2}_\d{6}Z\.mhl$",
                os.path.basename(v2_path)) is not None,
      os.path.basename(v2_path))

v2_tree = ET.parse(v2_path).getroot()
check("it is a v2.0 hashlist in the ASC namespace",
      v2_tree.tag == V2 + "hashlist" and v2_tree.get("version") == "2.0",
      v2_tree.tag)
check("it names the tool and the version that wrote it",
      v2_tree.find(f"{V2}creatorinfo/{V2}tool").get("version") == version.VERSION)
check("it says the media was transferred, not hashed in place",
      v2_tree.findtext(f"{V2}processinfo/{V2}process") == "transfer")
check("it claims no root hash, because the day folder is still being written to",
      v2_tree.find(f"{V2}processinfo/{V2}roothash") is None)

v2_hashes = v2_tree.findall(f"{V2}hashes/{V2}hash")
check("every copied file is in it",
      len(v2_hashes) == db.q1("SELECT COUNT(*) c FROM files WHERE card_id=?",
                              (v2_card,))["c"],
      f"{len(v2_hashes)} entries")
check("each entry carries the digest under its own algorithm's element name",
      all(h.find(V2 + "xxh128") is not None for h in v2_hashes))
check("and says the hash was taken off the card, not off the destination",
      all(h.find(V2 + "xxh128").get("action") == "original" for h in v2_hashes))
check("each path carries its size and modification date",
      all(h.find(V2 + "path").get("size")
          and h.find(V2 + "path").get("lastmodificationdate")
          for h in v2_hashes))
check("paths are relative to the folder the ascmhl folder sits in, "
      "and resolve to files that are really there",
      all(os.path.isfile(os.path.join(v2_root, h.find(V2 + "path").text))
          for h in v2_hashes),
      json.dumps([h.find(V2 + "path").text for h in v2_hashes]))
check("every element in a hash entry is one the 2.0 schema defines",
      all(child.tag.split("}")[-1] in
          ("path", "previousPath", "metadata", "md5", "sha1",
           "xxh64", "xxh3", "xxh128", "c4")
          for h in v2_hashes for child in h))

# --- the chain ------------------------------------------------------------
chain_path = os.path.join(os.path.dirname(v2_path), "ascmhl_chain.xml")
check("a chain file sits beside the manifest", os.path.isfile(chain_path))
chain = ET.parse(chain_path).getroot()
generations = chain.findall(CHAIN_NS + "hashlist")
check("it records this manifest, by name", len(generations) == 1
      and generations[0].findtext(CHAIN_NS + "path") == os.path.basename(v2_path),
      ET.tostring(chain, encoding="unicode"))
check("and by the C4 ID of the file as it actually landed on disk",
      generations[0].findtext(CHAIN_NS + "c4") == mhl2.c4_of_file(v2_path))

# --- writing it twice -----------------------------------------------------
again = reports.write_manifests(v2_card, v2_dest["id"])
check("writing the manifest again adds nothing, because nothing changed",
      again["paths"] == written["paths"], json.dumps(again["paths"]))
check("so the chain still has exactly one generation",
      len(ET.parse(chain_path).getroot().findall(CHAIN_NS + "hashlist")) == 1)

# --- a manifest that was altered after the fact ---------------------------
# This is the whole point of a chain: catching a manifest that changed since
# it was written. Nothing is repaired and nothing is deleted — it is reported.
with open(v2_path, "a", encoding="utf-8") as handle:
    handle.write("<!-- not us -->\n")
db.ex("UPDATE files SET src_hash=? WHERE card_id=? AND rel_path LIKE '%I001C001%'",
      ("f" * 32, v2_card))
tampered = reports.write_manifests(v2_card, v2_dest["id"])
check("a manifest that no longer matches the chain is reported",
      any("no longer matches" in w for w in tampered["warnings"]),
      json.dumps(tampered["warnings"]))
check("the altered manifest is left exactly where it is",
      os.path.isfile(v2_path)
      and open(v2_path, encoding="utf-8").read().endswith("<!-- not us -->\n"))
check("and the new state is recorded as a second generation, not a rewrite",
      len(ET.parse(chain_path).getroot().findall(CHAIN_NS + "hashlist")) == 2)

# --- renaming, which 2.0 can say out loud --------------------------------
v2_renamed = [h for h in v2_hashes if h.find(V2 + "previousPath") is not None]
check("nothing claims a previous name when nothing was renamed",
      not v2_renamed, json.dumps([h.find(V2 + "path").text for h in v2_renamed]))

# --- the reference layout, read back by our own drive indexer ------------
read_back = drivetools.read_manifest(v2_path)
check("the drive indexer can read an ASC MHL 2.0 manifest",
      read_back is not None and read_back["file_count"] == len(v2_hashes),
      json.dumps(read_back and {k: v for k, v in read_back.items() if k != "files"}))
check("and gets the sizes and digests out of it, not just the names",
      read_back and all(f["size"] and f["hash"] for f in read_back["files"]),
      json.dumps(read_back and read_back["files"]))
project_guess, day_guess, roll_guess = \
    drivetools._day_from_manifest_path(DRIVE_A, v2_path)
check("and works out the day and roll from where a 2.0 manifest sits",
      (project_guess, day_guess) == ("PROJECT_01", "DAY_MHL2")
      and bool(roll_guess) and _re.match(r"^\d{4}_", roll_guess) is None
      and not roll_guess.endswith("Z"),
      f"{project_guess} / {day_guess} / {roll_guess}")

# --- and 1.1 is still written when 1.1 can say it ------------------------
both = reports.write_manifests(md5_card, md5_dest["id"])
check("a day on MD5 gets both manifest formats, from the same facts",
      len(both["paths"]) == 2
      and any("ascmhl" in p for p in both["paths"])
      and any("ascmhl" not in p for p in both["paths"]),
      json.dumps(both["paths"]))
legacy_path = next(p for p in both["paths"] if "ascmhl" not in p)
modern_path = next(p for p in both["paths"] if "ascmhl" in p)
legacy = ET.parse(legacy_path).getroot()
modern = ET.parse(modern_path).getroot()
legacy_facts = sorted((h.findtext("file"), h.findtext("size"), h.findtext("md5"))
                      for h in legacy.findall("hash"))
modern_facts = sorted((h.find(V2 + "path").text,
                       h.find(V2 + "path").get("size"),
                       h.findtext(V2 + "md5"))
                      for h in modern.findall(f"{V2}hashes/{V2}hash"))
check("and the two files say exactly the same thing about the same files",
      legacy_facts == modern_facts,
      json.dumps([legacy_facts, modern_facts]))


print("\n" + ("SOME CHECKS FAILED" if FAILED else "ALL CHECKS PASSED") + "\n")
raise SystemExit(1 if FAILED else 0)

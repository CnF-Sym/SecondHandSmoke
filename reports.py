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

"""Manifests, the format gate, and the paperwork.

The MHL sidecar is the part that outlives this tool. It is a plain XML
manifest of every file and its hash, readable by Silverstack, ShotPut,
Resolve's clone tool and anything else that speaks ASC MHL. If this
application disappears tomorrow, the MHL still proves the day is intact.
"""

import csv
import getpass
import html
import os
import socket
import threading
import time
from datetime import datetime, timezone

import db
import handoff
import sync
import thumbnails
import version
import waveform

try:
    MIN_VERIFIED_COPIES = max(2, int(os.environ.get("DIT_MIN_COPIES", "2")))
except ValueError:
    MIN_VERIFIED_COPIES = 2

# Written into the MHL <name> field. The manifest outlives this application,
# so it should name the person who ran the ingest, not just the login account.
OPERATOR_NAME = os.environ.get("DIT_OPERATOR") or getpass.getuser()


# --------------------------------------------------------------------------
# The gate

_GATE_TTL_SECONDS = 4.0
_gate_cache = {}
_gate_lock = threading.Lock()


def invalidate_gate_cache(card_id=None):
    """Drop cached gate answers. Called after anything that can change one."""
    with _gate_lock:
        if card_id is None:
            _gate_cache.clear()
        else:
            _gate_cache.pop(card_id, None)


def card_status_cached(card_id, ttl=_GATE_TTL_SECONDS):
    """Display-only wrapper around card_status().

    The live gate walks every verified copy of every file with a stat each.
    That is correct and cheap once; it is not cheap several times a second
    against the same drives an offload is writing to. Screens use this;
    manifests, proxy preflight, waveform preflight and the handoff all call
    card_status() so no decision is ever made on a stale answer.
    """
    now = time.monotonic()
    with _gate_lock:
        hit = _gate_cache.get(card_id)
        if hit and now - hit[0] < ttl:
            return hit[1]
    answer = card_status(card_id)
    with _gate_lock:
        _gate_cache[card_id] = (now, answer)
    return answer


def _device_map(card_id):
    """Physical-device identity for every destination on this card's day.

    The gate must count *drives*, not destination rows. Two folders, two
    partitions, an alias, or a disk image and the drive its backing file sits
    on are all one place for a copy to die, and all of them resolve to one
    ``device_id``. ``storage.inspect_path`` establishes that identity when a
    destination is added and ``validate_destination_set`` re-confirms it
    before every job; this reads what those recorded, so the gate counts the
    same physical drives the copy engine did.

    Returns {destination_id: {"key", "root", "label"}}.
    """
    rows = db.q(
        """SELECT d.id, d.label, d.path, d.device_id
           FROM destinations d JOIN cards c ON c.day_id = d.day_id
           WHERE c.id=?""", (card_id,))
    out = {}
    for row in rows:
        # A destination we never positively identified is keyed by its own id
        # rather than merged with the others, so a catalog written by an older
        # build is never made *less* safe than it was; it simply does not gain
        # the collapsing behaviour until the next job re-identifies its drive.
        key = row["device_id"] or f"unidentified:{row['id']}"
        out[row["id"]] = {"key": key, "root": row["path"], "label": row["label"]}
    return out


def card_status(card_id):
    """Can this card be formatted?

    Only if every single file on it has been read back and checksum-matched
    on at least MIN_VERIFIED_COPIES separate *physical drives*. Anything less
    and the answer is no, with the reason.
    """
    files = db.q("SELECT * FROM files WHERE card_id=?", (card_id,))
    if not files:
        return {"safe": False, "reason": "No files catalogued for this card",
                "verified": 0, "total": 0, "short": []}

    card = db.q1("SELECT status FROM cards WHERE id=?", (card_id,))
    if card and card["status"] in ("offloading", "verifying", "failed", "cancelled"):
        label = {
            "offloading": "Offload is still running",
            "verifying": "Drive verification is still running",
            "failed": "The most recent job failed",
            "cancelled": "The most recent job was cancelled",
        }[card["status"]]
        return {"safe": False, "reason": label,
                "verified": 0, "total": len(files), "short": []}

    devices = _device_map(card_id)
    short = []
    fully_verified = 0
    live_issues = []
    shared_drives = set()
    for f in files:
        rows = db.q(
            "SELECT destination_id, dest_path FROM copies WHERE file_id=? AND state='verified'",
            (f["id"],),
        )
        keys = set()
        for row in rows:
            info = devices.get(row["destination_id"])
            gap = _live_gap(f, row["dest_path"], info["root"] if info else None)
            if gap:
                live_issues.append(f"{f['rel_path']} on destination {row['destination_id']}: {gap}")
                continue
            key = info["key"] if info else f"unidentified:{row['destination_id']}"
            if key in keys:
                shared_drives.add(info["label"] if info else str(row["destination_id"]))
            keys.add(key)
        n = len(keys)
        if n >= MIN_VERIFIED_COPIES:
            fully_verified += 1
        else:
            short.append({"rel_path": f["rel_path"], "verified_copies": n})

    zero_byte = [f["rel_path"] for f in files if f["size"] == 0]
    if zero_byte:
        return {"safe": False, "reason": f"{len(zero_byte)} zero-byte file(s) on card",
                "verified": fully_verified, "total": len(files), "short": short[:50],
                "zero_byte": zero_byte[:20], "live_issues": live_issues[:20]}

    if short:
        reason = f"{len(short)} file(s) below {MIN_VERIFIED_COPIES} verified copies"
        if shared_drives:
            reason += (f" — copies on {', '.join(sorted(shared_drives))} share a "
                       "physical drive and count once")
        if live_issues:
            reason += f" — {len(live_issues)} changed on a connected drive since verification"
        return {"safe": False, "reason": reason,
                "verified": fully_verified, "total": len(files), "short": short[:50],
                "shared_drives": sorted(shared_drives),
                "live_issues": live_issues[:20]}

    return {"safe": True,
            "reason": f"All {len(files)} files verified on {MIN_VERIFIED_COPIES}+ separate drives",
            "verified": fully_verified, "total": len(files), "short": [],
            "shared_drives": [], "live_issues": []}


def _live_gap(f, dest_path, dest_root=None):
    """Cheap on-disk check for a destination that is currently reachable:
    does the file that made this copy 'verified' still exist at the size it
    was verified at? This is a stat, not a re-hash — 'Re-read drives' is
    still the only way to catch silent bit rot — but it closes the much more
    common gap where a drive is unplugged, a file gets deleted, or a copy is
    truncated after verification while the database row still says green.
    A destination that isn't currently reachable at all (pulled after wrap,
    which is normal) is not penalized here; its earlier verification still
    counts.

    Connectivity is decided from ``dest_root`` — the destination folder the
    operator chose — and never from the file's own parent directory. Deleting
    a whole roll folder removes that parent too, and reading its absence as
    "the drive is gone" would leave the gate green on media that no longer
    exists.
    """
    if not dest_path:
        return None
    root = dest_root or os.path.dirname(dest_path)
    if not os.path.isdir(root):
        return None  # drive genuinely not connected; earlier verification stands
    try:
        size = os.path.getsize(dest_path)
    except OSError:
        return "missing from a connected drive"
    if size != f["size"]:
        return f"size changed on disk ({size} vs {f['size']} verified)"
    return None


# --------------------------------------------------------------------------
# MHL

def write_mhl(card_id, destination_id):
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    day = db.q1("SELECT * FROM days WHERE id=?", (card["day_id"],))
    dest = db.q1("SELECT * FROM destinations WHERE id=?", (destination_id,))

    rows = db.q("""SELECT f.rel_path, f.size, f.mtime, f.src_hash, c.dest_path, c.state,
                          c.updated_at
                   FROM files f JOIN copies c ON c.file_id = f.id
                   WHERE f.card_id=? AND c.destination_id=? AND c.state='verified'
                   ORDER BY f.rel_path""", (card_id, destination_id))
    total = db.q1("SELECT COUNT(*) c FROM files WHERE card_id=?", (card_id,))["c"]
    if not rows or len(rows) != total:
        return None

    root = os.path.join(dest["path"], day["project"], day["name"])
    os.makedirs(root, exist_ok=True)
    out_path = os.path.join(root, f"{day['name']}_{card['roll'] or card['label']}.mhl")

    def esc(s):
        return html.escape(str(s), quote=True)

    stamp = datetime.now(timezone.utc).isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<hashlist version="1.1">',
        '  <creatorinfo>',
        f'    <name>{esc(OPERATOR_NAME)}</name>',
        f'    <username>{esc(getpass.getuser())}</username>',
        f'    <hostname>{esc(socket.gethostname())}</hostname>',
        f'    <tool>{esc(version.TOOL_STRING)}</tool>',
        f'    <startdate>{stamp}</startdate>',
        f'    <finishdate>{stamp}</finishdate>',
        '  </creatorinfo>',
    ]
    renamed = []
    for r in rows:
        rel = os.path.relpath(r["dest_path"], root)
        mod = datetime.fromtimestamp(r["mtime"], timezone.utc).isoformat()
        source_name = os.path.basename(r["rel_path"])
        if source_name != os.path.basename(rel):
            renamed.append((r["rel_path"], rel, r["size"], r["src_hash"]))
            # A comment, not an element. The ASC MHL 1.1 schema defines <hash>
            # as an xs:sequence with no xs:any, so an extra child would make
            # this manifest schema-invalid — and a manifest Silverstack or
            # ShotPut refuses to read is worse than no manifest. A comment is
            # legal anywhere, ignored by every parser, and still tells a human
            # reading the file what this thing used to be called.
            lines.append(f'  <!-- source: {esc(source_name)} -->')
        lines += [
            '  <hash>',
            f'    <file>{esc(rel)}</file>',
            f'    <size>{r["size"]}</size>',
            f'    <lastmodificationdate>{mod}</lastmodificationdate>',
            f'    <xxhash64be>{esc(r["src_hash"])}</xxhash64be>',
            f'    <hashdate>{esc(r["updated_at"])}</hashdate>',
            '  </hash>',
        ]
    lines.append('</hashlist>')

    partial = out_path + ".partial"
    with open(partial, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(partial, out_path)

    # The machine-readable half of "the manifest records both names". It sits
    # beside the MHL and travels with the day, rather than living only in this
    # program's catalog.
    names_path = os.path.splitext(out_path)[0] + "_NAMES.csv"
    if renamed:
        partial_names = names_path + ".partial"
        with open(partial_names, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["original", "renamed", "size", "xxhash64be"])
            for source_rel, dest_rel, size, digest in renamed:
                writer.writerow([source_rel, dest_rel, size, digest])
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(partial_names, names_path)
    elif os.path.isfile(names_path):
        # Nothing was renamed this time. A stale mapping from an earlier run
        # would be a lie sitting next to a manifest.
        try:
            os.remove(names_path)
        except OSError:
            pass
    return out_path


# --------------------------------------------------------------------------
# Day report

REPORT_CSS = """
body{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:#fff;color:#111;
margin:40px auto;max-width:1000px;padding:0 20px}
h1{font-size:22px;letter-spacing:.06em;text-transform:uppercase;margin:0 0 4px}
h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#666;
border-bottom:1px solid #ddd;padding-bottom:6px;margin:34px 0 12px}
.sub{color:#666;margin-bottom:28px}
table{border-collapse:collapse;width:100%;font-size:12px}
th{text-align:left;font-weight:600;color:#666;border-bottom:1px solid #ccc;padding:6px 8px;
font-size:11px;letter-spacing:.08em;text-transform:uppercase}
td{padding:5px 8px;border-bottom:1px solid #f0f0f0}
.media{table-layout:fixed}.media td{font-size:11px}.media td:first-child{overflow-wrap:anywhere}
.media th:nth-child(1){width:21%}.media th:nth-child(2){width:13%}
.media th:nth-child(3){width:7%}.media th:nth-child(4){width:11%}
.media th:nth-child(5){width:16%}.media th:nth-child(6){width:13%}
.media th:nth-child(7){width:8%}.media th:nth-child(8){width:11%}
.sync{table-layout:fixed;margin-top:12px}.sync td{font-size:11px}
.sync td:first-child,.sync td:nth-child(3){overflow-wrap:anywhere}
.sync th:nth-child(1){width:22%}.sync th:nth-child(2){width:12%}
.sync th:nth-child(3){width:22%}.sync th:nth-child(4){width:17%}
.sync th:nth-child(5){width:13%}.sync th:nth-child(6){width:14%}
.waveform{table-layout:fixed;margin-top:12px}.waveform td{font-size:11px}
.waveform td:first-child,.waveform td:nth-child(3){overflow-wrap:anywhere}
.waveform th:nth-child(1){width:22%}.waveform th:nth-child(2){width:12%}
.waveform th:nth-child(3){width:21%}.waveform th:nth-child(4){width:15%}
.waveform th:nth-child(5){width:9%}.waveform th:nth-child(6){width:9%}
.waveform th:nth-child(7){width:12%}
.foot{margin-top:40px;padding-top:14px;border-top:1px solid #ddd;color:#888;font-size:11px;line-height:1.8}
.foot a{color:#888}
.nowrap{white-space:nowrap}
.ok{color:#0a7c50;font-weight:600}.bad{color:#b3261e;font-weight:600}
.warn{color:#9a6500;font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.stamp{border:2px solid #0a7c50;color:#0a7c50;display:inline-block;padding:8px 16px;
letter-spacing:.16em;font-weight:700;margin:6px 0}
.stamp.bad{border-color:#b3261e;color:#b3261e}
.hash{color:#888;font-size:11px}
"""


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def human_duration(seconds):
    if seconds is None:
        return "—"
    total = max(0, round(float(seconds)))
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def display_rate(value):
    if not value:
        return ""
    try:
        numerator, denominator = value.split("/", 1)
        rate = float(numerator) / float(denominator)
        return f"{rate:.3f}".rstrip("0").rstrip(".")
    except (ValueError, ZeroDivisionError):
        return value


def display_codec(value):
    return {
        "mpeg2video": "MPEG-2",
        "pcm_s24le": "PCM",
        "pcm_s16le": "PCM",
        "prores": "ProRes",
        "h264": "H.264",
        "hevc": "HEVC",
    }.get(value, value or "")


# The report is gathered once and rendered more than once.
#
# The HTML report and the PDF report have to agree about whether a card is safe
# to format. The only way to guarantee that is for them not to be two paths at
# all: day_data() does every query, and each renderer is handed the result. A
# renderer cannot reach the database, so it cannot form its own opinion, and a
# PDF that says SAFE TO FORMAT is quoting the same card_status() call the HTML
# quoted, from the same dict, in the same call.

_MEDIA_SQL = """SELECT f.rel_path, f.category, f.src_hash, m.*,
                       p.state proxy_status, p.output_path proxy_path,
                       p.error proxy_error
                FROM files f LEFT JOIN media_metadata m ON m.file_id=f.id
                LEFT JOIN proxies p ON p.file_id=f.id
                WHERE f.card_id=? AND f.category IN ('video','audio')
                ORDER BY f.rel_path"""

_SYNC_SQL = """SELECT f.rel_path camera_path, s.status, s.method, s.error,
                      l.offset_seconds, l.overlap_seconds,
                      sf.rel_path sound_path, sm.scene, sm.take_name
               FROM files f
               LEFT JOIN sync_status s ON s.camera_file_id=f.id
               LEFT JOIN sync_links l ON l.camera_file_id=f.id
               LEFT JOIN files sf ON sf.id=l.sound_file_id
               LEFT JOIN media_metadata sm ON sm.file_id=sf.id
               WHERE f.card_id=? AND f.category='video'
               ORDER BY f.rel_path,l.sound_start_seconds,sf.rel_path"""

_WAVEFORM_SQL = """SELECT f.rel_path camera_path,w.state,w.score,w.margin,
                          w.offset_seconds,w.overlap_seconds,w.error,
                          sf.rel_path sound_path,sm.scene,sm.take_name
                   FROM waveform_results w JOIN files f ON f.id=w.camera_file_id
                   LEFT JOIN files sf ON sf.id=w.best_sound_file_id
                   LEFT JOIN media_metadata sm ON sm.file_id=sf.id
                   WHERE f.card_id=? ORDER BY f.rel_path"""


def gate_stamp(gate):
    """The words that go on the paperwork, and nothing else decides them.

    Both report renderers call this. A PDF that disagrees with the HTML about
    whether a card can be formatted is the one bug this whole program exists to
    prevent, so the sentence itself has one definition too, not just the data
    behind it.
    """
    return ("SAFE TO FORMAT", True) if gate["safe"] else ("DO NOT FORMAT", False)


def _dicts(result):
    return [dict(row) for row in result]


def _media_rows(card_id):
    """Media rows, each carrying the report frame that already exists for it.

    Cache lookup only. Gathering a shoot day never decodes anything: a report
    that wants pictures warms the cache before it asks, and every other reader
    simply finds None and prints no picture. Thumbnails are cosmetic and this
    is where that is enforced — nothing here can fail in a way that changes
    what the report says about a card.
    """
    rows = _dicts(db.q(_MEDIA_SQL, (card_id,)))
    for row in rows:
        row["thumbnail"] = (thumbnails.cached_frame(row.get("src_hash"))
                            if row.get("category") == "video" else None)
    return rows


def day_data(day_id):
    """Gather everything any report needs, in one pass, from one place.

    Returns None for a shoot day that does not exist. Every verification fact
    in here — the gate verdict, the per-destination copy state — is read once
    and handed to whichever renderer wants it. Nothing downstream queries the
    catalog again.
    """
    day = db.q1("SELECT * FROM days WHERE id=?", (day_id,))
    if not day:
        return None

    dests = _dicts(db.q("""SELECT * FROM destinations WHERE day_id=?
                           ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id""",
                        (day_id,)))

    duration_totals = dict(db.q1("""SELECT
        SUM(CASE WHEN c.kind IN ('camera','mixed') AND f.category='video'
                 THEN m.duration_seconds ELSE 0 END) camera_seconds,
        SUM(CASE WHEN c.kind IN ('sound','mixed') AND f.category='audio'
                 THEN m.duration_seconds ELSE 0 END) sound_seconds
        FROM cards c JOIN files f ON f.card_id=c.id
        LEFT JOIN media_metadata m ON m.file_id=f.id WHERE c.day_id=?""", (day_id,)))
    proxy_totals = dict(db.q1("""SELECT COUNT(*) total,
        SUM(CASE WHEN p.state='ready' THEN 1 ELSE 0 END) ready,
        SUM(CASE WHEN p.state IN ('failed','cancelled') THEN 1 ELSE 0 END) problems,
        SUM(CASE WHEN p.state='ready' THEN p.output_size ELSE 0 END) output_bytes
        FROM cards c JOIN files f ON f.card_id=c.id
        LEFT JOIN proxies p ON p.file_id=f.id
        WHERE c.day_id=? AND f.category='video'""", (day_id,)))

    cards = []
    grand_files = grand_bytes = 0
    for row in db.q("SELECT * FROM cards WHERE day_id=? ORDER BY label", (day_id,)):
        card = dict(row)
        files = _dicts(db.q("SELECT * FROM files WHERE card_id=? ORDER BY rel_path",
                            (card["id"],)))
        grand_files += len(files)
        grand_bytes += card["total_bytes"]

        copy_state = {}
        copy_path = {}
        for f in files:
            for d in dests:
                row = db.q1("SELECT state, dest_path FROM copies "
                            "WHERE file_id=? AND destination_id=?", (f["id"], d["id"]))
                copy_state[(f["id"], d["id"])] = row["state"] if row else "—"
                copy_path[(f["id"], d["id"])] = row["dest_path"] if row else None
            # What this file was actually written as, on the first destination
            # that has it. Renaming happens on the destination, so the source
            # name in files.rel_path and this are both true at once and the
            # paperwork has to show both.
            f["dest_name"] = next(
                (os.path.basename(copy_path[(f["id"], d["id"])])
                 for d in dests if copy_path.get((f["id"], d["id"]))), "")
            f["renamed"] = bool(f["dest_name"]
                                and f["dest_name"] != os.path.basename(f["rel_path"]))

        cards.append({
            "card": card,
            # The format gate, quoted once. No renderer decides this.
            "gate": card_status(card["id"]),
            "files": files,
            "copy_state": copy_state,
            "renamed_count": sum(1 for f in files if f["renamed"]),
            "media": _media_rows(card["id"]),
            "sync": _dicts(db.q(_SYNC_SQL, (card["id"],))),
            "waveform": _dicts(db.q(_WAVEFORM_SQL, (card["id"],))),
        })

    return {
        "day": dict(day),
        "destinations": dests,
        "cards": cards,
        "generated": db.now(),
        "tool": version.TOOL_STRING,
        "support_email": version.SUPPORT_EMAIL,
        "project_url": version.PROJECT_URL,
        "totals": {
            "files": grand_files,
            "bytes": grand_bytes,
            "duration": duration_totals,
            "proxy": proxy_totals,
            "sync": sync.day_summary(day_id),
            "waveform": waveform.day_summary(day_id),
            "handoff": handoff.day_summary(day_id),
        },
    }


def summary_line(data):
    """The one-line day summary, shared by every renderer."""
    totals = data["totals"]
    duration_totals = totals["duration"]
    proxy_totals = totals["proxy"]
    sync_totals = totals["sync"]
    waveform_totals = totals["waveform"]
    handoff_totals = totals["handoff"]

    proxy_status = f"proxies {proxy_totals['ready'] or 0}/{proxy_totals['total'] or 0}"
    if proxy_totals["output_bytes"]:
        proxy_status += f" ({human_bytes(proxy_totals['output_bytes'])})"
    if proxy_totals["problems"]:
        proxy_status += f" · {proxy_totals['problems']} problem(s)"

    if not sync_totals["total"]:
        sync_status = "sync no camera clips"
    elif not sync_totals["analyzed"]:
        sync_status = f"sync not run ({sync_totals['total']} camera clips)"
    else:
        synced = sync_totals["matched"] + sync_totals["multiple"]
        needs_attention = (sync_totals["ambiguous"] + sync_totals["review"]
                           + sync_totals["unmatched"] + sync_totals["invalid"])
        sync_status = f"sync {synced}/{sync_totals['total']}"
        if needs_attention:
            sync_status += f" · {needs_attention} need attention"

    handoff_status = (f"handoff {handoff_totals['ready']}/{handoff_totals['total']}"
                      if handoff_totals["state"] != "not_built" else "handoff not built")
    if handoff_totals["unresolved"]:
        handoff_status += f" · {handoff_totals['unresolved']} sync review"

    return [
        f"{len(data['cards'])} cards",
        f"{totals['files']} files",
        f"{human_bytes(totals['bytes'])} total",
        f"camera {human_duration(duration_totals['camera_seconds'])}",
        f"sound {human_duration(duration_totals['sound_seconds'])}",
        proxy_status,
        sync_status,
        f"waveform {waveform_totals['matched']}/{waveform_totals['analyzed']} accepted · "
        f"{waveform_totals['confirmed']} TC confirmed",
        handoff_status,
    ]


def render_html(data):
    """The day report as HTML. Reads only from day_data()."""
    day = data["day"]
    dests = data["destinations"]

    e = lambda s: html.escape(str(s if s is not None else ""))
    p = [f"<!doctype html><meta charset=utf-8><title>{e(day['name'])} ingest report</title>",
         f"<style>{REPORT_CSS}</style>",
         f"<h1>{e(day['project'])} &middot; {e(day['name'])}</h1>",
         f"<div class=sub>Shoot date {e(day['shoot_date'])} &nbsp;·&nbsp; "
         f"report generated {e(data['generated'])}</div>"]

    p.append("<h2>Destinations</h2><table><tr><th>Label</th><th>Role</th><th>Path</th></tr>")
    for d in dests:
        p.append(f"<tr><td>{e(d['label'])}</td><td>{e(d['role'])}</td><td>{e(d['path'])}</td></tr>")
    p.append("</table>")

    handoff_totals = data["totals"]["handoff"]
    p.append("<h2>Editorial handoff</h2>")
    handoff_class = ("ok" if handoff_totals["package_ready"] and
                     not handoff_totals["unresolved"] else
                     "warn" if handoff_totals["state"] in ("ready", "stale") else "bad")
    handoff_label = ("ready" if handoff_totals["package_ready"] else
                     handoff_totals["state"].replace("_", " "))
    handoff_path = handoff_totals["bundle_path"] or "—"
    p.append(f"<table><tr><th>State</th><th>Review dailies</th>"
             f"<th>Sync exceptions</th><th>Package</th></tr>"
             f"<tr><td class='{handoff_class}'>{e(handoff_label)}</td>"
             f"<td>{handoff_totals['ready']}/{handoff_totals['total']}</td>"
             f"<td>{handoff_totals['unresolved']}</td>"
             f"<td>{e(handoff_path)}</td></tr></table>")

    for entry in data["cards"]:
        c = entry["card"]
        st = entry["gate"]
        files = entry["files"]
        label, safe = gate_stamp(st)
        stamp = (f"<div class=stamp>{label}</div>" if safe
                 else f"<div class='stamp bad'>{label}</div>")
        p.append(f"<h2>Card {e(c['roll'] or c['label'])} &middot; {e(c['kind'])}</h2>")
        p.append(stamp + f"<div class=sub>{e(st['reason'])} &nbsp;·&nbsp; "
                         f"{len(files)} files &nbsp;·&nbsp; {human_bytes(c['total_bytes'])}</div>")

        media_rows = entry["media"]
        if media_rows:
            p.append("<table class=media><tr><th>Media</th><th>Scene / Take</th><th>Duration</th>"
                     "<th>Start TC</th><th>Format</th><th>Audio</th><th>Metadata</th>"
                     "<th>Proxy</th></tr>")
            for m in media_rows:
                media_name = os.path.basename(m["rel_path"])
                scene_take = " / ".join(value for value in
                                        (m["scene"], m["take_name"]) if value) or "—"
                if m["width"] and m["height"]:
                    rate = display_rate(m["frame_rate"])
                    format_bits = f"{m['width']}×{m['height']}"
                    if rate:
                        format_bits += f" @ {rate}"
                    if m["codec_name"]:
                        format_bits += f" · {display_codec(m['codec_name'])}"
                else:
                    format_bits = display_codec(m["codec_name"]) or "—"
                audio_bits = []
                if m["sample_rate"]:
                    rate = m["sample_rate"]
                    audio_bits.append(f"{rate // 1000}kHz" if rate % 1000 == 0
                                      else f"{rate:,}Hz")
                if m["audio_channels"]:
                    audio_bits.append(f"{m['audio_channels']}ch")
                if m["bit_depth"]:
                    audio_bits.append(f"{m['bit_depth']}b")
                start = m["start_timecode"]
                if not start and m["bwf_time_reference"] is not None:
                    start = f"BWF {m['bwf_time_reference']:,} samples"
                meta_status = m["status"] or "not analyzed"
                status_class = ("ok" if meta_status == "ready" else
                                "warn" if meta_status == "partial" else "bad")
                error_title = f" title='{e(m['error'])}'" if m["error"] else ""
                if m["category"] == "video":
                    proxy_status = m["proxy_status"] or "not built"
                    proxy_detail = m["proxy_error"] or m["proxy_path"]
                    proxy_class = ("ok" if proxy_status == "ready" else
                                   "bad" if proxy_status == "failed" else "warn")
                else:
                    proxy_status, proxy_detail, proxy_class = "—", None, ""
                proxy_title = f" title='{e(proxy_detail)}'" if proxy_detail else ""
                notes = f" · {e(m['notes'])}" if m["notes"] else ""
                p.append(
                    f"<tr><td title='{e(m['rel_path'])}'>{e(media_name)}</td>"
                    f"<td>{e(scene_take)}{notes}</td>"
                    f"<td class=nowrap>{human_duration(m['duration_seconds'])}</td>"
                    f"<td class=nowrap>{e(start or '—')}</td><td>{e(format_bits)}</td>"
                    f"<td class=nowrap>{e(' · '.join(audio_bits) or '—')}</td>"
                    f"<td class='{status_class} nowrap'{error_title}>{e(meta_status)}</td>"
                    f"<td class='{proxy_class} nowrap'{proxy_title}>{e(proxy_status)}</td></tr>"
                )
            p.append("</table>")

        sync_rows = entry["sync"]
        if sync_rows:
            p.append("<h2>Camera / sound sync</h2>")
            p.append("<table class=sync><tr><th>Camera</th><th>Status</th><th>Sound</th>"
                     "<th>Scene / Take</th><th>Offset</th><th>Overlap</th></tr>")
            for link in sync_rows:
                status = link["status"] or "not run"
                status_class = ("ok" if status in ("matched", "multiple") else
                                "warn" if status in ("not run", "unmatched", "review")
                                else "bad")
                status_label = (f"{status} · {link['method']}" if link["method"] else status)
                scene_take = " / ".join(value for value in
                                        (link["scene"], link["take_name"]) if value) or "—"
                offset = (f"{link['offset_seconds']:+.3f}s"
                          if link["offset_seconds"] is not None else "—")
                overlap = (f"{link['overlap_seconds']:.3f}s"
                           if link["overlap_seconds"] is not None else "—")
                title = f" title='{e(link['error'])}'" if link["error"] else ""
                p.append(
                    f"<tr><td title='{e(link['camera_path'])}'>"
                    f"{e(os.path.basename(link['camera_path']))}</td>"
                    f"<td class='{status_class}'{title}>{e(status_label)}</td>"
                    f"<td title='{e(link['sound_path'])}'>"
                    f"{e(os.path.basename(link['sound_path'])) if link['sound_path'] else '—'}</td>"
                    f"<td>{e(scene_take)}</td><td class=nowrap>{e(offset)}</td>"
                    f"<td class=nowrap>{e(overlap)}</td></tr>"
                )
            p.append("</table>")

        waveform_rows = entry["waveform"]
        if waveform_rows:
            p.append("<h2>Waveform fallback</h2>")
            p.append("<table class=waveform><tr><th>Camera</th><th>Result</th>"
                     "<th>Best sound</th><th>Scene / Take</th><th>Score</th>"
                     "<th>Margin</th><th>Offset / overlap</th></tr>")
            for result in waveform_rows:
                result_class = ("ok" if result["state"] in ("matched", "confirmed") else
                                "warn" if result["state"] in
                                ("review", "unmatched", "unverifiable")
                                else "bad")
                scene_take = " / ".join(value for value in
                                        (result["scene"], result["take_name"]) if value) or "—"
                score = f"{result['score']:.3f}" if result["score"] is not None else "—"
                margin = f"{result['margin']:.3f}" if result["margin"] is not None else "—"
                timing = (f"{result['offset_seconds']:+.3f}s / "
                          f"{result['overlap_seconds']:.3f}s"
                          if result["offset_seconds"] is not None else "—")
                title = f" title='{e(result['error'])}'" if result["error"] else ""
                p.append(
                    f"<tr><td title='{e(result['camera_path'])}'>"
                    f"{e(os.path.basename(result['camera_path']))}</td>"
                    f"<td class='{result_class}'{title}>{e(result['state'])}</td>"
                    f"<td title='{e(result['sound_path'])}'>"
                    f"{e(os.path.basename(result['sound_path'])) if result['sound_path'] else '—'}</td>"
                    f"<td>{e(scene_take)}</td><td>{e(score)}</td><td>{e(margin)}</td>"
                    f"<td class=nowrap>{e(timing)}</td></tr>"
                )
            p.append("</table>")

        p.append("<h2>File verification</h2>")
        renamed_here = entry.get("renamed_count", 0)
        if renamed_here:
            p.append(f"<div class=sub>{renamed_here} of {len(files)} files were "
                     f"renamed on the destination. The card was not touched. Both "
                     f"names are below, in the manifest, and in the "
                     f"<code>_NAMES.csv</code> beside it.</div>")
        p.append("<table><tr><th>File on the card</th>"
                 + ("<th>Written as</th>" if renamed_here else "")
                 + "<th class=num>Size</th><th>xxHash64</th>"
                 + "".join(f"<th>{e(d['label'])}</th>" for d in dests) + "</tr>")
        for f in files:
            cells = ""
            for d in dests:
                s = entry["copy_state"][(f["id"], d["id"])]
                cls = "ok" if s == "verified" else ("bad" if s in ("failed", "—") else "")
                cells += f"<td class='{cls}'>{e(s)}</td>"
            written = ""
            if renamed_here:
                written = (f"<td>{e(f['dest_name'])}</td>" if f["renamed"]
                           else "<td class=sub>unchanged</td>")
            p.append(f"<tr><td>{e(f['rel_path'])}</td>{written}"
                     f"<td class=num>{human_bytes(f['size'])}</td>"
                     f"<td class=hash>{e(f['src_hash'])}</td>{cells}</tr>")
        p.append("</table>")

    p.insert(4, "<div class=sub>" + " &nbsp;·&nbsp; ".join(summary_line(data)) + "</div>")

    # This document gets emailed to producers and post houses by people who
    # were not in the room when it was made. It has to say what wrote it and
    # who to ask about it.
    p.append(f"<div class=foot>Generated by {e(data['tool'])} "
             f"&nbsp;·&nbsp; free software under the GNU GPL v3.0 "
             f"&nbsp;·&nbsp; questions about this report: "
             f"<a href='mailto:{e(data['support_email'])}'>"
             f"{e(data['support_email'])}</a> "
             f"&nbsp;·&nbsp; <a href='{e(data['project_url'])}'>"
             f"{e(data['project_url'])}</a></div>")

    return "\n".join(p)


def report_dir(data):
    dests = data["destinations"]
    day = data["day"]
    folder = (os.path.join(dests[0]["path"], day["project"], day["name"], "REPORTS")
              if dests else os.path.expanduser("~/SecondHandSmoke/reports"))
    os.makedirs(folder, exist_ok=True)
    return folder


def day_report(day_id):
    """The HTML day report. Written to disk and returned."""
    data = day_data(day_id)
    body = render_html(data)
    out = os.path.join(report_dir(data), f"ingest_{data['day']['name']}.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    return out, body

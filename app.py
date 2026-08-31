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

"""Second Hand Smoke. Local web UI.

A CnF Works Project.

Runs on http://127.0.0.1:3838 by default. Bound to loopback only; this
never needs to be reachable from anywhere else.
"""

import base64
import binascii
import html
import os
import re
import sys
import threading
from datetime import date

from flask import Flask, jsonify, request, render_template, Response, send_from_directory

import cards
import db
import handoff
import media
import offload
import pdfreport
import proxies
import reports
import storage
import sync
import version
import waveform

# Inside the packaged application the interface files are unpacked beside the
# frozen modules rather than sitting next to this source file.
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
app = Flask(__name__, template_folder=BASE_DIR, static_folder=None)
# Nothing this interface sends is large except a chosen report logo, and
# that is capped well below this. An unbounded body is a way to spend a
# cart machine's memory for free.
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

try:
    PORT = int(os.environ.get("DIT_PORT", "3838"))
except ValueError:
    print("DIT_PORT is not a number; falling back to 3838", file=sys.stderr)
    PORT = 3838

_scan_cache = {}
_scan_lock = threading.Lock()
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

# Only ever one job at a time, across every job type. Each worker module has
# its own lock, but those only exclude another job of the same kind — two
# requests arriving together could pass the "is anything running?" check and
# then start an offload and a proxy job side by side. This gate makes the
# check and the start one atomic step.
_JOB_GATE = threading.Lock()

# The tool is bound to loopback, which stops other machines reaching it. It
# does not stop a page open in the browser on the same cart: a cross-origin
# form POST needs no permission, and a rebound hostname resolves to 127.0.0.1
# like any other. Both are closed below.
ALLOWED_HOSTS = frozenset(
    f"{name}:{PORT}" for name in ("127.0.0.1", "localhost", "[::1]")
) | {"127.0.0.1", "localhost"}
ALLOWED_ORIGINS = frozenset(
    f"http://{name}:{PORT}" for name in ("127.0.0.1", "localhost", "[::1]")
)
BROWSE_ROOTS = None  # built lazily; see _browse_roots()


# --------------------------------------------------------------------------
# Local-only request guard  (F-06 CSRF, F-07 DNS rebinding)

@app.before_request
def only_local_requests():
    """Reject anything that did not come from this machine's own UI.

    Two separate problems, one hook:

    * **Host** — a page anywhere on the internet can point a hostname it
      controls at 127.0.0.1 and then read this API as a same-origin resource
      ("DNS rebinding"). Browsers send the name they dialled in ``Host``, so
      requiring a loopback host closes it.
    * **Origin** — a cross-origin ``<form>`` can POST here without any
      permission from the browser, which was enough to cancel a running
      offload. Browsers always attach ``Origin`` to a cross-origin POST, and
      a form cannot send ``application/json``, so requiring both is enough.
    """
    if request.host not in ALLOWED_HOSTS:
        return jsonify(error="This tool only answers requests from this "
                             "computer's own interface."), 403
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify(error="Request came from another site and was refused."), 403
    if not request.is_json:
        return jsonify(error="Requests must be sent as application/json."), 415
    return None


@app.after_request
def drop_stale_gate_cache(response):
    """Anything that mutates state can change a format-gate answer."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        reports.invalidate_gate_cache()
    return response


@app.errorhandler(Exception)
def json_error(exc):
    """The UI reads `error` out of a JSON body; give it one for every failure.

    Without this, an unexpected exception reached the operator as a blank
    toast and an HTML error page in the network tab.
    """
    code = getattr(exc, "code", 500)
    if code == 500:
        app.logger.exception("Unhandled error on %s", request.path)
        message = "Something went wrong. Check the terminal window for details."
    else:
        message = getattr(exc, "description", str(exc))
    return jsonify(error=message), code


def _browse_roots():
    """The only places the folder picker will list.

    It used to accept any absolute path and list it, which made the whole
    filesystem layout readable through the API. Cards live under the scan
    root, cloud-sync folders live under home, and destinations live on
    mounted volumes — nothing else needs to be browsable.
    """
    global BROWSE_ROOTS
    if BROWSE_ROOTS is None:
        candidates = ["/Volumes", os.path.expanduser("~"), cards.SCAN_ROOT,
                      os.environ.get("DIT_BROWSE_ROOT")]
        BROWSE_ROOTS = tuple(sorted({storage.canonical(path) for path in candidates
                                     if path and os.path.isdir(path)}))
    return BROWSE_ROOTS


def _inside_browse_root(path):
    return any(storage.paths_overlap(root, path) and
               os.path.commonpath((root, path)) == root
               for root in _browse_roots())


def start_job(starter, *args, **kwargs):
    """Check "is anything running?" and start, as one atomic step."""
    with _JOB_GATE:
        if (offload.current() is not None or proxies.current() is not None
                or waveform.current() is not None or handoff.current() is not None):
            return None, "Another job is already running"
        return starter(*args, **kwargs)


def job_running():
    return (offload.current() is not None or proxies.current() is not None
            or waveform.current() is not None or handoff.current() is not None)


def component(value, label):
    value = (value or "").strip()
    if (not SAFE_COMPONENT.fullmatch(value) or value.endswith((".", " "))
            or value in (".", "..")):
        raise ValueError(
            f"{label} must be 1–64 letters, numbers, spaces, dots, dashes or underscores"
        )
    return value


# --------------------------------------------------------------------------
# Reading and writing preferences lives in db.py, where the report
# writers can reach it without importing the web layer.
setting = db.setting


def active_day():
    did = setting("active_day")
    return db.q1("SELECT * FROM days WHERE id=?", (did,)) if did else None


def rows(r):
    return [dict(x) for x in r]


def latest_job():
    if offload.current() is not None:
        return offload.status_snapshot()
    if proxies.current() is not None:
        return proxies.status_snapshot()
    if waveform.current() is not None:
        return waveform.status_snapshot()
    if handoff.current() is not None:
        return handoff.status_snapshot()
    snapshots = [snapshot for snapshot in
                 (offload.status_snapshot(), proxies.status_snapshot(),
                  waveform.status_snapshot(), handoff.status_snapshot()) if snapshot]
    if not snapshots:
        return None
    return max(snapshots, key=lambda snapshot:
               snapshot.get("finished_epoch") or snapshot.get("started_epoch") or 0)


@app.route("/")
def index():
    return render_template("index.html", port=PORT,
                           min_copies=reports.MIN_VERIFIED_COPIES,
                           version=version.VERSION,
                           support_email=version.SUPPORT_EMAIL,
                           project_url=version.PROJECT_URL)


@app.get("/static/app.css")
def app_css():
    return send_from_directory(BASE_DIR, "app.css", mimetype="text/css")


@app.get("/static/app.js")
def app_js():
    return send_from_directory(BASE_DIR, "app.js", mimetype="application/javascript")


# An allow-list rather than a folder: this is a web server on a machine full of
# other people's footage, and "serve whatever is named" is how that stops being
# true.
BRAND_ASSETS = {"logo.png", "icon.png", "icon-192.png"}


@app.get("/static/brand/<name>")
def brand_asset(name):
    if name not in BRAND_ASSETS:
        return Response("Unknown asset.\n", status=404, mimetype="text/plain")
    return send_from_directory(ASSETS_DIR, name, mimetype="image/png")


# --------------------------------------------------------------------------
# Days and destinations

@app.post("/api/day")
def create_day():
    d = request.get_json(silent=True) or {}
    try:
        project = component(d.get("project") or "PROJECT_01", "Project")
        name = component(d.get("name"), "Shoot day")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    shoot_date = (d.get("shoot_date") or date.today().isoformat()).strip()
    try:
        date.fromisoformat(shoot_date)
    except ValueError:
        return jsonify(error="Shoot date must be a valid date"), 400
    existing = db.q1("SELECT * FROM days WHERE project=? AND name=?", (project, name))
    did = existing["id"] if existing else db.ex(
        "INSERT INTO days (project,name,shoot_date,created_at) VALUES (?,?,?,?)",
        (project, name, shoot_date, db.now()))
    setting("active_day", str(did))
    return jsonify(ok=True, day_id=did)


@app.post("/api/day/<int:day_id>/activate")
def activate_day(day_id):
    if not db.q1("SELECT id FROM days WHERE id=?", (day_id,)):
        return jsonify(error="Unknown shoot day"), 404
    setting("active_day", str(day_id))
    return jsonify(ok=True)


@app.post("/api/destination")
def add_destination():
    day = active_day()
    if not day:
        return jsonify(error="Create a shoot day first"), 400
    d = request.get_json(silent=True) or {}
    raw_path = (d.get("path") or "").strip()
    if not raw_path:
        return jsonify(error="Choose a destination folder"), 400
    path = storage.canonical(raw_path)
    label = (d.get("label") or os.path.basename(path) or "Destination").strip()[:80]
    role = d.get("role") or "backup"
    if role not in ("primary", "backup", "offsite"):
        return jsonify(error="Destination role must be primary, backup or offsite"), 400
    try:
        info = storage.inspect_path(path)
    except storage.StorageError as exc:
        return jsonify(error=str(exc)), 400
    if not os.access(info["path"], os.W_OK | os.R_OK):
        return jsonify(error=f"No read/write access to {info['path']}"), 400

    existing_device = db.q1(
        "SELECT label FROM destinations WHERE day_id=? AND device_id=?",
        (day["id"], info["device_id"]),
    )
    if existing_device:
        return jsonify(error=f"{existing_device['label']} is already on this physical drive"), 400
    try:
        db.ex("""INSERT INTO destinations
              (day_id,label,path,canonical_path,device_id,volume_uuid,role)
              VALUES (?,?,?,?,?,?,?)""",
              (day["id"], label, info["path"], info["path"], info["device_id"],
               info["volume_uuid"], role))
    except Exception:
        return jsonify(error="That destination is already on this day"), 400
    return jsonify(ok=True)


@app.delete("/api/destination/<int:dest_id>")
def del_destination(dest_id):
    used = db.q1("SELECT COUNT(*) c FROM copies WHERE destination_id=?", (dest_id,))["c"]
    if used:
        return jsonify(error="Files have already been written here. "
                             "Remove it after the day wraps, not during."), 400
    db.ex("DELETE FROM destinations WHERE id=?", (dest_id,))
    return jsonify(ok=True)


@app.get("/api/browse")
def browse():
    """One-level folder listing so the UI can offer a click-through picker
    instead of a typed path. Read-only — lists directory names only, never
    file contents. 'volumes' jumps to mounted drives, 'home' jumps to the
    user's home folder (where cloud-sync folders like Dropbox or iCloud
    Drive normally live); anything else is treated as a literal path.
    """
    raw = (request.args.get("path") or "volumes").strip()
    if raw == "volumes":
        real = storage.canonical("/Volumes" if sys.platform == "darwin"
                                 else cards.SCAN_ROOT)
    elif raw == "home":
        real = storage.canonical("~")
    else:
        real = storage.canonical(raw)
    if not os.path.isdir(real):
        return jsonify(error=f"Not a folder: {real}"), 400
    if not _inside_browse_root(real):
        return jsonify(error="The folder picker only lists mounted drives and "
                             "folders inside your home folder."), 403
    try:
        names = sorted(os.listdir(real), key=str.casefold)
    except OSError as exc:
        return jsonify(error=f"Cannot list {real}: {exc}"), 400
    entries = []
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(real, name)
        try:
            if os.path.isdir(full):
                entries.append({"name": name, "path": full})
        except OSError:
            continue
    parent = os.path.dirname(real)
    if parent == real or not _inside_browse_root(parent):
        parent = None
    return jsonify(path=real, parent=parent, entries=entries,
                   roots=list(_browse_roots()))


# --------------------------------------------------------------------------
# Cards

@app.get("/api/scan")
def scan():
    destinations = db.q("SELECT path, device_id FROM destinations")
    found, problems = cards.scan(
        exclude_paths=[d["path"] for d in destinations],
        exclude_device_ids=[d["device_id"] for d in destinations if d["device_id"]],
    )
    # Rebuilt under a lock: Flask serves requests on threads, and a register
    # or ingest call reading the cache while a scan clears it would see a card
    # that is only half re-registered.
    fresh = {c["fingerprint"]: c for c in found}
    with _scan_lock:
        _scan_cache.clear()
        _scan_cache.update(fresh)
    out = []
    for c in found:
        known = db.q1("SELECT * FROM cards WHERE fingerprint=?", (c["fingerprint"],))
        item = {k: c[k] for k in
                ("label", "path", "kind", "roll", "fingerprint",
                 "file_count", "media_count", "total_bytes")}
        item["scan_errors"] = c["errors"]
        item["known"] = bool(known)
        item["card_id"] = known["id"] if known else None
        item["status"] = known["status"] if known else None
        out.append(item)
    return jsonify(cards=out, scan_root=cards.SCAN_ROOT, problems=problems)


def _register_card(day, d):
    """Shared registration logic used by both /api/register (advanced,
    inspect-before-you-commit flow) and /api/ingest (the one-button flow).
    Returns (card_id, reused, error, status_code) — on success error is None.
    """
    fp = d.get("fingerprint")
    with _scan_lock:
        scanned = _scan_cache.get(fp)
    if not scanned:
        return None, False, "Card no longer mounted — rescan", 400
    if scanned["errors"]:
        return None, False, "Card scan had read errors. Do not ingest or format this card.", 400
    try:
        fresh = cards.inspect_volume(scanned["path"])
    except OSError as exc:
        return None, False, f"Card could not be read: {exc}", 400
    if fresh["errors"]:
        return None, False, "Card contains unreadable files. Check the card and rescan.", 400
    if fresh["fingerprint"] != fp:
        return None, False, "Card contents changed after the scan. Scan it again.", 409

    existing = db.q1("SELECT * FROM cards WHERE fingerprint=?", (fp,))
    legacy = False
    if not existing:
        existing = db.q1(
            """SELECT * FROM cards
               WHERE fingerprint=? AND inventory_fingerprint IS NULL""",
            (scanned["inventory_fingerprint"],),
        )
        legacy = bool(existing)
    if existing:
        if existing["day_id"] != day["id"]:
            prior = db.q1("SELECT name FROM days WHERE id=?", (existing["day_id"],))
            return None, False, f"This exact card is already registered on {prior['name']}", 409
        if legacy:
            with db.transaction():
                db.ex("""UPDATE cards
                         SET fingerprint=?, inventory_fingerprint=?, source_path=?,
                             status='registered' WHERE id=?""",
                      (fp, scanned["inventory_fingerprint"], scanned["path"], existing["id"]))
                db.ex("UPDATE files SET src_hash=NULL WHERE card_id=?", (existing["id"],))
                db.ex("""UPDATE copies SET state='pending', read_back_hash=NULL,
                         error='Reverification required after identity upgrade'
                         WHERE file_id IN (SELECT id FROM files WHERE card_id=?)""",
                      (existing["id"],))
        else:
            db.ex("UPDATE cards SET source_path=? WHERE id=?",
                  (scanned["path"], existing["id"]))
        return existing["id"], True, None, None

    try:
        roll = component(
            (d.get("roll") or scanned["roll"] or scanned["label"]).upper(), "Roll"
        )
    except ValueError as exc:
        return None, False, str(exc), 400
    kind = d.get("kind") or scanned["kind"]
    if kind not in ("camera", "sound", "mixed"):
        return None, False, "Card does not contain recognized camera or sound media", 400
    collision = db.q1(
        "SELECT label FROM cards WHERE day_id=? AND kind=? AND roll=?",
        (day["id"], kind, roll),
    )
    if collision:
        return None, False, f"Roll {roll} is already registered on this day", 409

    with db.transaction():
        cid = db.ex("""INSERT INTO cards
            (fingerprint,inventory_fingerprint,label,kind,roll,day_id,source_path,
             file_count,total_bytes,registered_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (fp, scanned["inventory_fingerprint"], scanned["label"], kind, roll,
                     day["id"], scanned["path"], scanned["file_count"],
                     scanned["total_bytes"], db.now()))
        for f in scanned["files"]:
            db.ex("""INSERT INTO files (card_id,rel_path,size,mtime,category)
                     VALUES (?,?,?,?,?)""",
                  (cid, f["rel_path"], f["size"], f["mtime"], f["category"]))
    return cid, False, None, None


@app.post("/api/register")
def register():
    day = active_day()
    if not day:
        return jsonify(error="Create a shoot day first"), 400
    d = request.get_json(silent=True) or {}
    card_id, reused, error, status = _register_card(day, d)
    if error:
        return jsonify(error=error), status
    return jsonify(ok=True, card_id=card_id, reused=reused)


@app.post("/api/ingest")
def ingest():
    """Card goes in, one button: register (or re-link) the freshly scanned
    card and immediately start its verified offload to every configured
    destination. This is the on-set fast path; Register and Offload stay
    available separately for anyone who wants to inspect a card first."""
    day = active_day()
    if not day:
        return jsonify(error="Create a shoot day first"), 400
    if job_running():
        return jsonify(error="Another job is already running"), 409
    d = request.get_json(silent=True) or {}
    card_id, reused, error, status = _register_card(day, d)
    if error:
        return jsonify(error=error), status
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not os.path.isdir(card["source_path"]):
        return jsonify(error=f"Card is not mounted at {card['source_path']} — "
                             "rescan and try again", card_id=card_id), 400
    error = offload.preflight(card_id, "offload", reports.MIN_VERIFIED_COPIES)
    if error:
        return jsonify(error=error, card_id=card_id), 400
    job, err = start_job(offload.start, card_id, mode="offload",
                         min_copies=reports.MIN_VERIFIED_COPIES)
    if err:
        return jsonify(error=err, card_id=card_id), 409
    return jsonify(ok=True, card_id=card_id, reused=reused)


@app.post("/api/offload/<int:card_id>")
def start_offload(card_id):
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return jsonify(error="Unknown card"), 404
    mode = (request.args.get("mode") or "offload")
    if mode not in ("offload", "verify"):
        return jsonify(error="Unknown mode"), 400
    if job_running():
        return jsonify(error="Another job is already running"), 409
    if mode == "offload" and not os.path.isdir(card["source_path"]):
        return jsonify(error="Card is not mounted at "
                             f"{card['source_path']} — rescan and re-register"), 400
    error = offload.preflight(card_id, mode, reports.MIN_VERIFIED_COPIES)
    if error:
        return jsonify(error=error), 400
    job, err = start_job(offload.start, card_id, mode=mode,
                         min_copies=reports.MIN_VERIFIED_COPIES)
    if err:
        return jsonify(error=err), 409
    return jsonify(ok=True)


@app.post("/api/cancel")
def cancel():
    offload.cancel()
    proxies.cancel()
    waveform.cancel()
    handoff.cancel()
    return jsonify(ok=True)


@app.get("/api/card/<int:card_id>/status")
def card_status(card_id):
    return jsonify(reports.card_status(card_id))


@app.post("/api/card/<int:card_id>/analyze")
def analyze_card(card_id):
    if job_running():
        return jsonify(error="Wait for the active job to finish"), 409
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return jsonify(error="Unknown card"), 404
    if os.path.isdir(card["source_path"]):
        # The card is still here — hold it to the same freshness checks
        # offload and register use before trusting anything read from it.
        try:
            current = cards.inspect_volume(card["source_path"])
        except OSError as exc:
            return jsonify(error=f"Card could not be read: {exc}"), 400
        if current["errors"]:
            return jsonify(error="Source card has read errors; metadata analysis stopped"), 400
        if current["fingerprint"] != card["fingerprint"]:
            return jsonify(error="Source card contents changed; scan and register it again"), 409
    elif not db.q1("""SELECT 1 FROM copies c JOIN files f ON f.id = c.file_id
                       WHERE f.card_id=? AND c.state='verified' LIMIT 1""", (card_id,)):
        return jsonify(error="Mount the source card before reading metadata"), 400
    # else: the card has been safely unmounted after clearing — media.py
    # falls back to reading the verified primary-drive copy instead.
    result = media.analyze_card(card_id)
    return jsonify(ok=True, **result)


@app.post("/api/proxy/<int:card_id>")
def start_proxy(card_id):
    if job_running():
        return jsonify(error="Another job is already running"), 409
    rebuild = request.args.get("rebuild") == "1"
    error = proxies.preflight(card_id, rebuild=rebuild)
    if error:
        return jsonify(error=error), 400
    job, error = start_job(proxies.start, card_id, rebuild=rebuild)
    if error:
        return jsonify(error=error), 409
    return jsonify(ok=True)


@app.post("/api/day/<int:day_id>/proxies/next")
def proxies_next(day_id):
    """Start a proxy job for the next eligible camera/mixed card on this day
    that doesn't have complete proxies yet. The 'Build all proxies' button
    calls this once, then again each time a job finishes, so the operator
    doesn't have to click through every roll by hand. done:true (not an
    error) means there was nothing left to do."""
    if not db.q1("SELECT id FROM days WHERE id=?", (day_id,)):
        return jsonify(error="Unknown shoot day"), 404
    if job_running():
        return jsonify(error="Another job is already running"), 409
    candidates = db.q(
        """SELECT id FROM cards WHERE day_id=? AND kind IN ('camera','mixed')
           ORDER BY roll, label""", (day_id,))
    for row in candidates:
        summary = proxies.card_summary(row["id"])
        if not summary["total"] or summary["ready"] == summary["total"]:
            continue
        if not reports.card_status(row["id"])["safe"]:
            continue
        if proxies.preflight(row["id"], rebuild=False):
            continue
        job, error = start_job(proxies.start, row["id"], rebuild=False)
        if error:
            continue
        return jsonify(ok=True, done=False, card_id=row["id"])
    return jsonify(ok=True, done=True)


@app.post("/api/day/<int:day_id>/sync")
def sync_day(day_id):
    if job_running():
        return jsonify(error="Wait for the active job to finish"), 409
    if not db.q1("SELECT id FROM days WHERE id=?", (day_id,)):
        return jsonify(error="Unknown shoot day"), 404
    try:
        result = sync.run_day(day_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, **result)


@app.post("/api/day/<int:day_id>/waveform")
def waveform_day(day_id):
    if job_running():
        return jsonify(error="Another job is already running"), 409
    error = waveform.preflight(day_id)
    if error:
        return jsonify(error=error), 400
    job, error = start_job(waveform.start, day_id)
    if error:
        return jsonify(error=error), 409
    return jsonify(ok=True)


@app.post("/api/day/<int:day_id>/handoff")
def handoff_day(day_id):
    if job_running():
        return jsonify(error="Another job is already running"), 409
    rebuild = request.args.get("rebuild") == "1"
    error = handoff.preflight(day_id, rebuild=rebuild)
    if error:
        return jsonify(error=error), 400
    job, error = start_job(handoff.start, day_id, rebuild=rebuild)
    if error:
        return jsonify(error=error), 409
    return jsonify(ok=True)


@app.get("/api/day/<int:day_id>/handoff/notes")
def handoff_notes(day_id):
    export = db.q1("SELECT readme_path FROM handoff_exports WHERE day_id=?", (day_id,))
    if not export or not export["readme_path"] or not os.path.isfile(export["readme_path"]):
        return Response("Build the Phase 6 handoff first.\n", status=404,
                        mimetype="text/plain")
    with open(export["readme_path"], encoding="utf-8") as handle:
        body = handle.read()
    page = ("<!doctype html><meta charset=utf-8><title>Editorial handoff notes</title>"
            "<style>body{margin:0;background:#0e1116;color:#dce3ec;"
            "font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}"
            "main{max-width:900px;margin:40px auto;padding:0 24px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere}"
            "a{color:#7dd9de}</style><main><pre>" + html.escape(body) +
            "</pre></main>")
    return Response(page, mimetype="text/html")


@app.post("/api/card/<int:card_id>/manifest")
def manifest(card_id):
    card = db.q1("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return jsonify(error="Unknown card"), 404
    gate = reports.card_status(card_id)
    if not gate["safe"]:
        return jsonify(error=f"Manifest blocked: {gate['reason']}"), 400
    dests = db.q("SELECT * FROM destinations WHERE day_id=?", (card["day_id"],))
    written = [p for p in (reports.write_mhl(card_id, d["id"]) for d in dests) if p]
    if not written:
        return jsonify(error="Nothing verified yet — no manifest to write"), 400
    return jsonify(ok=True, paths=written)


@app.post("/api/report-branding")
def report_branding():
    """Store the production name, and the logo, however it was chosen.

    A picked file arrives base64-encoded inside the JSON body rather than as a
    multipart upload. That is not laziness: every state-changing request here
    must be application/json, because a cross-origin HTML form can post to this
    server but cannot send that content type. Accepting multipart would mean
    taking that guard off the whole API to add one convenience.

    A typed path and a picked file end in the same place — pdfreport.store_logo
    decodes, checks and re-encodes both, and stores the result inside this
    program's own folder.
    """
    body = request.get_json(silent=True) or {}
    production = str(body.get("production") or "").strip()[:120]
    db.setting(pdfreport.SETTING_PRODUCTION, production)

    if body.get("clear_logo"):
        pdfreport.clear_logo()
        return jsonify(ok=True, production=production, logo="")

    raw = None
    encoded = str(body.get("logo_data") or "")
    if encoded:
        if "," in encoded[:64] and encoded.lstrip().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return jsonify(error="That image did not arrive intact. Try again."), 400
    else:
        typed = str(body.get("logo") or "").strip()
        if typed:
            path = os.path.abspath(os.path.expanduser(typed))
            if not os.path.isfile(path):
                return jsonify(error=f"No file at {path}"), 400
            if os.path.getsize(path) > pdfreport.MAX_LOGO_BYTES:
                return jsonify(error="That image is too large for a report logo."), 400
            if path == db.setting(pdfreport.SETTING_LOGO):
                return jsonify(ok=True, production=production, logo=path)
            with open(path, "rb") as handle:
                raw = handle.read()

    if raw is None:
        return jsonify(ok=True, production=production,
                       logo=db.setting(pdfreport.SETTING_LOGO) or "")
    try:
        stored = pdfreport.store_logo(raw)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, production=production, logo=stored)


@app.get("/api/report-branding/logo")
def report_branding_logo():
    """The stored logo, for the preview in the interface."""
    stored = db.setting(pdfreport.SETTING_LOGO) or ""
    if not stored or not os.path.isfile(stored):
        return Response("No logo is set.\n", status=404, mimetype="text/plain")
    return send_from_directory(os.path.dirname(stored), os.path.basename(stored),
                               mimetype="image/png")


@app.get("/api/day/<int:day_id>/report.pdf")
def day_report_pdf(day_id):
    if not db.q1("SELECT id FROM days WHERE id=?", (day_id,)):
        return Response("Unknown shoot day.\n", status=404, mimetype="text/plain")
    layout = request.args.get("layout", pdfreport.DEFAULT_LAYOUT)
    try:
        path = pdfreport.build(day_id, layout)
    except RuntimeError as exc:
        # reportlab is optional. Say why, rather than throwing a 500 at someone
        # who only wanted a PDF of paperwork they can already read as HTML.
        return Response(f"{exc}\n", status=503, mimetype="text/plain")
    except ValueError as exc:
        return Response(f"{exc}\n", status=400, mimetype="text/plain")
    return send_from_directory(os.path.dirname(path), os.path.basename(path),
                               mimetype="application/pdf", as_attachment=True,
                               download_name=os.path.basename(path))


@app.get("/api/day/<int:day_id>/report")
def day_report(day_id):
    if not db.q1("SELECT id FROM days WHERE id=?", (day_id,)):
        return Response("Unknown shoot day.\n", status=404, mimetype="text/plain")
    path, body = reports.day_report(day_id)
    return Response(body, mimetype="text/html",
                    headers={"X-Report-Path": path})


# --------------------------------------------------------------------------
@app.get("/api/state")
def state():
    day = active_day()
    pdf_available, pdf_reason = pdfreport.availability()
    payload = {
        "days": rows(db.q("SELECT * FROM days ORDER BY shoot_date DESC, name DESC")),
        "day": dict(day) if day else None,
        "min_copies": reports.MIN_VERIFIED_COPIES,
        # Read, not re-detected: the interface polls this several times a
        # minute and probing the filesystem on every poll would cost more than
        # it tells anyone. A mid-shoot ffmpeg install is picked up by the
        # existing refresh in the metadata path.
        "tools": {"ffprobe": media.FFPROBE, "ffmpeg": proxies.FFMPEG},
        "pdf": {"available": pdf_available, "reason": pdf_reason,
                "layouts": list(pdfreport.LAYOUTS)},
        "branding": {"production": db.setting(pdfreport.SETTING_PRODUCTION) or "",
                     "logo": db.setting(pdfreport.SETTING_LOGO) or ""},
        "destinations": [], "cards": [],
        "job": latest_job(),
        "sync": None,
        "waveform": None,
        "handoff": None,
    }
    if day:
        payload["sync"] = sync.day_summary(day["id"])
        payload["waveform"] = waveform.day_summary(day["id"])
        payload["handoff"] = handoff.day_summary(day["id"])
        payload["destinations"] = rows(
            db.q("SELECT * FROM destinations WHERE day_id=? ORDER BY id", (day["id"],)))
        for c in db.q("SELECT * FROM cards WHERE day_id=? ORDER BY roll, label", (day["id"],)):
            item = dict(c)
            item["gate"] = reports.card_status_cached(c["id"])
            item["metadata"] = media.card_summary(c["id"])
            item["proxies"] = proxies.card_summary(c["id"])
            item["sync"] = sync.card_summary(c["id"])
            item["mounted"] = os.path.isdir(c["source_path"])
            payload["cards"].append(item)
    return jsonify(payload)


db.init()


if __name__ == "__main__":
    print(f"\n  SECOND HAND SMOKE → http://127.0.0.1:{PORT}")
    print("  A CnF Works Project")
    print(f"  Catalog: {db.DB_PATH}")
    print(f"  Scanning: {cards.SCAN_ROOT}")
    print(f"  Verified copies required before a card clears: "
          f"{reports.MIN_VERIFIED_COPIES}\n")
    app.run(host="127.0.0.1", port=PORT, threaded=True)

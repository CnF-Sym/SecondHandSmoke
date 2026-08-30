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

"""SQLite catalog for the Second Hand Smoke ingest station.

One database for the whole project. Days, cards, files, destinations and
per-destination copy state all live here. Nothing in this module ever
touches source media.
"""

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DIT_DB", os.path.expanduser("~/SecondHandSmoke/catalog.db"))

# Catalog locations used by earlier builds, newest first. If one of these still
# holds a database and the current location is empty, it is moved across on
# first use rather than silently starting the shoot with a blank catalog.
# Add a path here when the default catalog location changes in a future release.
LEGACY_DB_PATHS = ()

_local = threading.local()
_migrate_lock = threading.Lock()
_migrated = False

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS days (
    id          INTEGER PRIMARY KEY,
    project     TEXT NOT NULL,
    name        TEXT NOT NULL,
    shoot_date  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(project, name)
);

CREATE TABLE IF NOT EXISTS destinations (
    id             INTEGER PRIMARY KEY,
    day_id         INTEGER NOT NULL REFERENCES days(id),
    label          TEXT NOT NULL,
    path           TEXT NOT NULL,
    canonical_path TEXT,
    device_id      TEXT,
    volume_uuid    TEXT,
    role           TEXT NOT NULL DEFAULT 'backup',   -- primary | backup | offsite
    UNIQUE(day_id, path)
);

CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY,
    fingerprint   TEXT NOT NULL UNIQUE,
    inventory_fingerprint TEXT,
    label         TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- camera | sound | mixed
    roll          TEXT,
    day_id        INTEGER REFERENCES days(id),
    source_path   TEXT NOT NULL,
    file_count    INTEGER NOT NULL,
    total_bytes   INTEGER NOT NULL,
    registered_at TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'registered'
);

CREATE TABLE IF NOT EXISTS files (
    id        INTEGER PRIMARY KEY,
    card_id   INTEGER NOT NULL REFERENCES cards(id),
    rel_path  TEXT NOT NULL,
    size      INTEGER NOT NULL,
    mtime     REAL NOT NULL,
    category  TEXT NOT NULL,                 -- video | audio | sidecar | other
    src_hash  TEXT,
    UNIQUE(card_id, rel_path)
);

CREATE TABLE IF NOT EXISTS copies (
    id             INTEGER PRIMARY KEY,
    file_id        INTEGER NOT NULL REFERENCES files(id),
    destination_id INTEGER NOT NULL REFERENCES destinations(id),
    dest_path      TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending|copied|verified|failed
    read_back_hash TEXT,
    error          TEXT,
    updated_at     TEXT,
    UNIQUE(file_id, destination_id)
);

CREATE TABLE IF NOT EXISTS media_metadata (
    file_id             INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending|ready|partial|error|unavailable
    media_type          TEXT,                            -- video | audio
    format_name         TEXT,
    codec_name          TEXT,
    codec_long_name     TEXT,
    duration_seconds    REAL,
    start_timecode      TEXT,
    reel_name           TEXT,
    width               INTEGER,
    height              INTEGER,
    frame_rate          TEXT,
    timecode_rate       TEXT,
    timecode_drop_frame INTEGER,
    audio_channels      INTEGER,
    sample_rate         INTEGER,
    bit_depth           INTEGER,
    bwf_time_reference  INTEGER,
    bwf_sample_rate     INTEGER,
    origination_date    TEXT,
    origination_time    TEXT,
    scene               TEXT,
    take_name           TEXT,
    notes               TEXT,
    circled             INTEGER,
    sound_roll          TEXT,
    sound_report_path   TEXT,
    raw_probe_json      TEXT,
    error               TEXT,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
    file_id          INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    state            TEXT NOT NULL DEFAULT 'pending', -- pending|running|ready|failed|cancelled
    profile          TEXT NOT NULL,
    source_hash      TEXT,
    input_path       TEXT,
    output_path      TEXT,
    output_size      INTEGER,
    output_hash      TEXT,
    duration_seconds REAL,
    start_timecode   TEXT,
    reel_name        TEXT,
    width            INTEGER,
    height           INTEGER,
    codec_name       TEXT,
    error            TEXT,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_status (
    camera_file_id       INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    status               TEXT NOT NULL, -- matched|multiple|ambiguous|review|unmatched|invalid
    method               TEXT NOT NULL DEFAULT 'timecode',
    camera_start_seconds REAL,
    camera_end_seconds   REAL,
    frame_rate           TEXT,
    drop_frame           INTEGER,
    candidate_groups     INTEGER NOT NULL DEFAULT 0,
    link_count           INTEGER NOT NULL DEFAULT 0,
    error                TEXT,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_links (
    camera_file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sound_file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    method            TEXT NOT NULL DEFAULT 'timecode',
    sound_start_seconds REAL NOT NULL,
    sound_end_seconds   REAL NOT NULL,
    offset_seconds      REAL NOT NULL,
    overlap_seconds     REAL NOT NULL,
    day_shift_seconds   REAL NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY(camera_file_id, sound_file_id)
);

CREATE TABLE IF NOT EXISTS waveform_results (
    camera_file_id      INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    state               TEXT NOT NULL, -- confirmed|matched|review|unmatched|unverifiable|failed
    prior_status        TEXT,
    prior_method        TEXT,
    best_sound_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    score               REAL,
    runner_up_score     REAL,
    margin              REAL,
    offset_seconds      REAL,
    overlap_seconds     REAL,
    candidate_groups    INTEGER NOT NULL DEFAULT 0,
    camera_source_hash  TEXT,
    sound_source_hash   TEXT,
    error               TEXT,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS waveform_candidates (
    camera_file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sound_file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    group_key            TEXT NOT NULL,
    rank                 INTEGER NOT NULL,
    score                REAL NOT NULL,
    alternate_peak_score REAL,
    offset_seconds       REAL NOT NULL,
    overlap_seconds      REAL NOT NULL,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY(camera_file_id, sound_file_id)
);

CREATE TABLE IF NOT EXISTS dailies (
    camera_file_id  INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    state           TEXT NOT NULL DEFAULT 'pending', -- pending|running|ready|stale|failed|cancelled
    profile         TEXT NOT NULL,
    source_signature TEXT,
    input_path      TEXT,
    output_path     TEXT,
    output_size     INTEGER,
    output_hash     TEXT,
    duration_seconds REAL,
    external_audio INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoff_exports (
    day_id            INTEGER PRIMARY KEY REFERENCES days(id) ON DELETE CASCADE,
    state             TEXT NOT NULL DEFAULT 'pending', -- pending|running|ready|stale|failed|cancelled
    catalog_signature TEXT,
    bundle_path       TEXT,
    fcpxml_path       TEXT,
    ale_path          TEXT,
    json_path         TEXT,
    readme_path       TEXT,
    checksums_path    TEXT,
    total_camera      INTEGER NOT NULL DEFAULT 0,
    ready_dailies     INTEGER NOT NULL DEFAULT 0,
    unresolved        INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    card_id     INTEGER NOT NULL REFERENCES cards(id),
    job_type    TEXT NOT NULL DEFAULT 'offload',
    state       TEXT NOT NULL,               -- running | done | failed | cancelled
    message     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_card ON files(card_id);
CREATE INDEX IF NOT EXISTS idx_copies_file ON copies(file_id);
CREATE INDEX IF NOT EXISTS idx_copies_dest ON copies(destination_id);
CREATE INDEX IF NOT EXISTS idx_metadata_status ON media_metadata(status);
CREATE INDEX IF NOT EXISTS idx_proxies_state ON proxies(state);
CREATE INDEX IF NOT EXISTS idx_sync_status_state ON sync_status(status);
CREATE INDEX IF NOT EXISTS idx_sync_links_sound ON sync_links(sound_file_id);
CREATE INDEX IF NOT EXISTS idx_waveform_results_state ON waveform_results(state);
CREATE INDEX IF NOT EXISTS idx_waveform_candidates_rank
    ON waveform_candidates(camera_file_id, rank);
CREATE INDEX IF NOT EXISTS idx_dailies_state ON dailies(state);
CREATE INDEX IF NOT EXISTS idx_handoff_exports_state ON handoff_exports(state);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate_legacy_catalog():
    """Move a catalog written by an earlier build to the current location.

    Runs once per process, and only when DIT_DB was not set explicitly and the
    current path holds nothing. The newest surviving legacy location wins, so a
    machine that has been through both renames does not resurrect the oldest
    catalog. The write-ahead log and shared memory file travel with the
    database, so an unclean shutdown still recovers.
    """
    global _migrated
    with _migrate_lock:
        if _migrated:
            return
        _migrated = True
        if os.environ.get("DIT_DB") or os.path.exists(DB_PATH):
            return
        source_db = next((path for path in LEGACY_DB_PATHS if os.path.exists(path)), None)
        if not source_db:
            return
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        moved = []
        for suffix in ("", "-wal", "-shm"):
            source = source_db + suffix
            if os.path.exists(source):
                shutil.move(source, DB_PATH + suffix)
                moved.append(os.path.basename(source))
        if moved:
            print(f"  Catalog moved from {os.path.dirname(source_db)} "
                  f"to {DB_PATH} ({', '.join(moved)})")


def conn():
    """One connection per thread. Flask request threads and the offload
    worker each get their own."""
    c = getattr(_local, "conn", None)
    if c is None:
        _migrate_legacy_catalog()
        folder = os.path.dirname(DB_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def setting(key, value=None):
    """Read or write one persistent preference.

    Lives here rather than in app.py because the report writers need branding
    settings and must not import the web layer to get them.
    """
    if value is None:
        row = q1("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None
    ex("INSERT INTO settings (key,value) VALUES (?,?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return value


def init():
    conn().executescript(SCHEMA)
    _add_column("destinations", "canonical_path", "TEXT")
    _add_column("destinations", "device_id", "TEXT")
    _add_column("destinations", "volume_uuid", "TEXT")
    _add_column("cards", "inventory_fingerprint", "TEXT")
    _add_column("jobs", "job_type", "TEXT NOT NULL DEFAULT 'offload'")
    _add_column("proxies", "output_hash", "TEXT")
    _add_column("media_metadata", "timecode_rate", "TEXT")
    _add_column("media_metadata", "timecode_drop_frame", "INTEGER")


def _add_column(table, column, declaration):
    """Small forward-only migrations for catalogs created by earlier builds."""
    columns = {row["name"] for row in q(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn().execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def q(sql, args=()):
    return conn().execute(sql, args).fetchall()


def q1(sql, args=()):
    return conn().execute(sql, args).fetchone()


def ex(sql, args=()):
    cur = conn().execute(sql, args)
    return cur.lastrowid


@contextmanager
def transaction():
    connection = conn()
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")

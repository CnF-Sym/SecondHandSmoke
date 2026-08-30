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

"""Entry point for the server inside the macOS application bundle.

`start.command` runs `app.py` directly and always will — that path is how this
gets developed and how anyone can run it from source. This module is the same
server with the three things a bundled child process needs that a Terminal
session gets for free:

  * it exits when the application that launched it goes away, so a crashed
    window never leaves a server holding the catalog open;
  * it refuses to start if another copy already holds the catalog, because two
    servers sharing one SQLite file is a race nobody wants near a format gate;
  * everything it prints goes to stdout, which the wrapper captures to a log
    the operator can send to support.

It adds no behaviour of its own to the offload. The verification path, the
two-drive rule and the format gate are the same code either way.
"""

import fcntl
import os
import sys
import threading
import time


def _lock_path():
    from db import DB_PATH
    return os.path.join(os.path.dirname(DB_PATH), ".server.lock")


def claim_single_instance():
    """Hold an exclusive lock for as long as this process runs.

    Returns the open file object, which must stay referenced: closing it
    releases the lock. The lock is advisory and lives beside the catalog, so it
    covers exactly what needs covering — one server per catalog.
    """
    path = _lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print("Second Hand Smoke is already running.\n"
              f"Another server holds {path}.\n"
              "Quit the running copy before starting another.", file=sys.stderr)
        raise SystemExit(3)
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def watch_parent(interval=2.0):
    """Exit when the process that launched this one disappears.

    The wrapper terminates this server on quit. This is the backstop for the
    case where the wrapper never gets the chance — a crash, a force quit — so
    that the worst outcome is a window that vanished, not an invisible server
    still holding the catalog.
    """
    original = os.getppid()

    def loop():
        while True:
            time.sleep(interval)
            current = os.getppid()
            if current != original or current == 1:
                # Immediate. There is nothing to flush that matters more than
                # not lingering: every write in the offload path is already
                # fsynced and atomically renamed before it counts.
                os._exit(0)

    thread = threading.Thread(target=loop, name="parent-watchdog", daemon=True)
    thread.start()


def main():
    lock = claim_single_instance()  # noqa: F841 — the lock lives as long as we do

    import app
    import cards
    import db
    import reports
    import version

    watch_parent()

    print(f"Second Hand Smoke {version.VERSION} — A CnF Works Project")
    print(f"  Interface: http://127.0.0.1:{app.PORT}")
    print(f"  Catalog:   {db.DB_PATH}")
    print(f"  Scanning:  {cards.SCAN_ROOT}")
    print(f"  Verified copies required before a card clears: "
          f"{reports.MIN_VERIFIED_COPIES}")
    sys.stdout.flush()

    app.app.run(host="127.0.0.1", port=app.PORT, threaded=True)


if __name__ == "__main__":
    main()

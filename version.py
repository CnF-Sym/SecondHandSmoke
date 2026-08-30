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

"""The version number, read from one file.

The version appears in the MHL <tool> field, in the UI, and in the macOS app
bundle's Info.plist. Those have to agree: an MHL that claims to come from a
different build than the app that wrote it is a manifest nobody can trust.
The VERSION file at the repository root is the single source of truth. Python
reads it here; the macOS build scripts read the same file with PlistBuddy.
"""

import os
import sys

# Inside the packaged application the VERSION file is unpacked beside the
# frozen modules rather than sitting next to this source file.
ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")


def _read():
    try:
        with open(VERSION_FILE, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        value = ""
    # An unreadable VERSION file must not stop an offload. It marks the
    # manifest as coming from an unidentified build instead, which is honest
    # and obvious in the paperwork rather than silently wrong.
    return value or "0.0.0-unknown"


VERSION = _read()
NAME = "Second Hand Smoke"
VENDOR = "CnF Works"
TOOL_STRING = f"{NAME} {VERSION} ({VENDOR})"

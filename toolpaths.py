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

"""Finding ffmpeg and ffprobe, from a Terminal or from a double-clicked app.

ffmpeg is deliberately not bundled. It stays an external tool the user
installs, so this program never ships someone else's binaries and never has to
answer for their licences or their CVEs.

The catch is where it lives. `shutil.which` searches PATH, and PATH is not the
same in both launch modes. A shell inherits the login shell's PATH, which on
almost every Mac includes Homebrew's /opt/homebrew/bin. An application
launched from Finder or the Dock does not: launchd hands it a minimal PATH of
/usr/bin:/bin:/usr/sbin:/sbin, and Homebrew is not on it. So the same machine,
with ffmpeg plainly installed and working in Terminal, reports "ffmpeg not
found" the moment the same code runs inside a .app.

That is not a hypothetical. It is the default outcome for the packaged build,
and it silently disables metadata, proxies, waveform sync and dailies. So this
module searches PATH first, then the handful of places package managers
actually install to.

Nothing here touches the copy or verification path. If ffmpeg is genuinely
absent, every one of those features degrades to unavailable and the offload,
the checksums and the format gate carry on exactly as before.
"""

import os
import shutil

# Searched in order, only after PATH has come up empty. Homebrew on Apple
# Silicon, Homebrew on Intel, MacPorts, then the system locations, which are
# already on PATH but cost nothing to confirm.
FALLBACK_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
    "/bin",
)


def find(name, override_var):
    """Return a path to `name`, or None.

    An explicit override is returned verbatim, even if it does not exist. That
    is deliberate: someone who sets DIT_FFMPEG has said which binary to use,
    and quietly substituting a different one would be worse than the error
    they get when it fails to run.
    """
    override = os.environ.get(override_var)
    if override:
        return override

    found = shutil.which(name)
    if found:
        return found

    for folder in FALLBACK_DIRS:
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def ffmpeg():
    return find("ffmpeg", "DIT_FFMPEG")


def ffprobe():
    return find("ffprobe", "DIT_FFPROBE")

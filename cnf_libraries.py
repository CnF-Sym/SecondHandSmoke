"""CnF-Libraries-Installs — the Python twin of CnFLibraries.swift.

Every CnF Works app keeps ALL of its files in one visible place, never scattered through ~/Library:

    CnF-Libraries-Installs/
        README.txt
        <App Name>/
            Settings/      preferences
            Save Files/    history, anything the app remembers
            Caches/        safe to delete any time
            ...            whatever else the app needs, each in its own folder

Remove an app completely: delete the app, then delete its folder here. Nothing else is left behind —
a packaged app also removes what macOS itself drops in ~/Library under its bundle ID when it quits
(preferences plist, saved window state, caches, HTTP storage, WebKit data).

The folder is found in this order (same as the Swift block):
  1. $CNF_LIBRARIES_INSTALLS
  2. a "CnF-Libraries-Installs" folder beside the app, or in any folder above it on the same drive
     (put one at the top of the Mac mini's programs SSD and every app on that drive uses it)
  3. ~/CnF-Libraries-Installs (created on first use)

This file is identical in every CnF Works Python app. Copy it as-is into new apps. Standard library only.
"""
from __future__ import annotations

import atexit
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

FOLDER_NAME = "CnF-Libraries-Installs"


def app_location() -> Path:
    """The .app bundle when packaged (PyInstaller/py2app), else the folder holding the running code."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return exe
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    return Path(main_file).resolve() if main_file else Path(__file__).resolve()


def resolve_root(start: Optional[Path] = None, home: Optional[Path] = None,
                 environ: Optional[dict] = None) -> Path:
    environ = os.environ if environ is None else environ
    value = (environ.get("CNF_LIBRARIES_INSTALLS") or "").strip()
    if value:
        return Path(os.path.expanduser(value)).resolve()
    directory = (start or app_location()).resolve().parent
    while str(directory) not in ("/", "/Volumes", ""):
        candidate = directory / FOLDER_NAME
        if candidate.is_dir():
            return candidate
        if directory.parent == directory:
            break
        directory = directory.parent
    return (home or Path.home()) / FOLDER_NAME


_root: Optional[Path] = None


def root() -> Path:
    global _root
    if _root is None:
        _root = resolve_root()
    return _root


def folder(app_name: str, sub: Optional[str] = None, base: Optional[Path] = None) -> Path:
    """<root>/<app_name>/<sub>, created on demand. The README at the top is written once."""
    base = base or root()
    path = base / app_name
    if sub:
        path = path / sub
    path.mkdir(parents=True, exist_ok=True)
    write_readme(base)
    return path


def write_readme(base: Path) -> None:
    readme = base / "README.txt"
    if readme.exists():
        return
    text = (
        "CnF-Libraries-Installs\n"
        "======================\n\n"
        "Every CnF Works app keeps ALL of its files here — settings, save files, caches, logs, libraries —\n"
        "one folder per app, instead of scattering them through hidden system folders.\n\n"
        "To remove an app completely: delete the app, then delete its folder here. Nothing else is left behind.\n\n"
        "To keep these on another drive (e.g. the Mac mini's programs SSD): put a folder named\n"
        "\"CnF-Libraries-Installs\" at the top of that drive, next to the apps. Apps on that drive use it.\n"
    )
    try:
        readme.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _device(path: Path) -> Optional[int]:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return p.stat().st_dev
    except OSError:
        return None


def same_volume(a: Path, b: Path) -> bool:
    da, db = _device(a), _device(b)
    return da is not None and da == db


def migrate(old: Path, new: Path, discard: Iterable[str] = ()) -> bool:
    """Moves an app's old folder (e.g. ~/Library/Application Support/<App>) to its CnF folder, once.

    Only a same-volume rename — never a copy — and only if the new folder is missing or empty.
    Names in `discard` (rebuildable things such as a Python venv) are deleted from the old folder first,
    because they hold absolute paths and would not survive the move.
    """
    if not old.is_dir() or old.is_symlink():
        return False
    if new.exists():
        try:
            visible = [n for n in os.listdir(new) if not n.startswith(".")]
        except OSError:
            return False
        if visible:
            return False
        shutil.rmtree(new, ignore_errors=True)
    new.parent.mkdir(parents=True, exist_ok=True)
    if not same_volume(old, new.parent):
        return False
    for name in discard:
        target = old / name
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    try:
        os.rename(old, new)
    except OSError:
        return False
    return True


def repoint_sqlite_paths(db_path: Path, old_dir: Path, new_dir: Path) -> int:
    """After a data folder moves, rewrite stored absolute paths that pointed inside the old folder.

    Apps that save full paths in their database (receipt originals, report logos, ...) would otherwise point
    at a folder that no longer exists. Every text value in every table that starts with "<old_dir>/" gets
    that prefix swapped for "<new_dir>/". Nothing else is touched. Returns the number of values changed.
    """
    import sqlite3

    db_path = Path(db_path)
    old = str(old_dir).rstrip("/") + "/"
    new = str(new_dir).rstrip("/") + "/"
    if old == new or not db_path.is_file():
        return 0
    changed = 0
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            qt = '"' + table.replace('"', '""') + '"'
            try:
                columns = [r[1] for r in conn.execute(f"PRAGMA table_info({qt})")]
            except sqlite3.Error:
                continue
            for column in columns:
                qc = '"' + column.replace('"', '""') + '"'
                try:
                    cur = conn.execute(
                        f"UPDATE {qt} SET {qc} = ? || substr({qc}, ?) "
                        f"WHERE typeof({qc}) = 'text' AND substr({qc}, 1, ?) = ?",
                        (new, len(old) + 1, len(old), old))
                    changed += max(cur.rowcount, 0)
                except sqlite3.Error:
                    continue  # a read-only/virtual table or a guard trigger: leave it as it was
        conn.commit()
    finally:
        conn.close()
    return changed


def repoint_once(db_path: Path, old_dirs: Iterable[Path], new_dir: Path,
                 marker: str = ".cnf-paths-repointed") -> None:
    """repoint_sqlite_paths for each old folder, once per data folder (a marker file records it)."""
    new_dir = Path(new_dir)
    flag = new_dir / marker
    if flag.exists() or not Path(db_path).is_file():
        return
    for old in old_dirs:
        repoint_sqlite_paths(Path(db_path), Path(old), new_dir)
    try:
        flag.write_text("Stored file paths were updated after this folder moved into CnF-Libraries-Installs.\n",
                        encoding="utf-8")
    except OSError:
        pass


def bundle_id() -> Optional[str]:
    """CFBundleIdentifier of the packaged .app, or None when running from source."""
    bundle = app_location()
    if bundle.suffix != ".app":
        return None
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as fh:
            value = plistlib.load(fh).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return value if isinstance(value, str) else None


SYSTEM_LEFTOVERS = (
    "Preferences/{id}.plist",
    "Saved Application State/{id}.savedState",
    "Caches/{id}",
    "HTTPStorages/{id}",
    "HTTPStorages/{id}.binarycookies",
    "WebKit/{id}",
)


def remove_system_leftovers(bid: Optional[str] = None, home: Optional[Path] = None,
                            extra: Iterable[str] = ()) -> None:
    """What macOS writes under ~/Library for an app, by bundle ID. Called when the app quits.

    `extra` lists further paths relative to ~/Library that only this app makes (e.g. "Caches/CnF Works").
    """
    bid = bid if bid is not None else bundle_id()
    library = (home or Path.home()) / "Library"
    rels = list(extra)
    if bid and "." in bid and "/" not in bid:
        rels += [r.format(id=bid) for r in SYSTEM_LEFTOVERS]
    for rel in rels:
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            continue
        path = library / rel
        if path.is_symlink() or not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            pass


def clean_on_quit(extra: Iterable[str] = ()) -> None:
    """Register remove_system_leftovers to run when the program exits (packaged apps and extras)."""
    extra = tuple(extra)
    atexit.register(lambda: remove_system_leftovers(extra=extra))

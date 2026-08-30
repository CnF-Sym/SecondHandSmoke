# -*- mode: python ; coding: utf-8 -*-
#
# Builds the Flask server as a self-contained folder of binaries. The Swift
# wrapper in macos/ launches the result; this spec produces no .app of its own.
#
# One source of truth for the version. Never hand-edit a plist.

import pathlib

APP_VERSION = pathlib.Path('VERSION').read_text().strip()


a = Analysis(
    ['server_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # The interface. app.py serves these from BASE_DIR, which is the
        # unpack directory once frozen.
        ('index.html', '.'),
        ('app.css', '.'),
        ('app.js', '.'),
        # Read by version.py, and therefore present in every MHL manifest this
        # build writes.
        ('VERSION', '.'),
        # GPL-3.0 requires the licence text to travel with the binary.
        ('LICENSE', '.'),
    ],
    hiddenimports=[
        'xxhash',
        'numpy',
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont',
        'reportlab', 'reportlab.pdfgen', 'reportlab.platypus',
    ],
    hookspath=[],
    runtime_hooks=[],
    # Nothing here draws a native window or plots anything. Excluding these
    # keeps the bundle to the size of the thing it actually is.
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'pytest', 'setuptools'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SecondHandSmoke-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console binary: the wrapper captures stdout and stderr into the log it
    # points the operator at when something goes wrong.
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='server',
)

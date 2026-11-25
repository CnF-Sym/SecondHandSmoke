# Second Hand Smoke v0.4.1 — CnF Works 2026

Start a shoot day and the drives are right below it. The active project, day,
date and checksum stay in view. **New day…** opens the form, and the day
switcher is still there. Presets, Naming & Filtering and Reports open when
asked and remember your choice until you close the app.

## At the card reader

A card that shares a physical drive with a destination, or overlaps its
folder, stays out of ingest. The scan now names the destination that caused
it to be skipped.

A cleared card gets a quiet **Re-verify** action that reads the destination
copies. If a filter left files off the copy, the card is marked **Cleared with
exclusions**. Disabled buttons are easier to read, with tooltips for reasons
such as missing clips or sync. Longer panel explanations are under **More**.

## Catalog and support

The catalog keeps the original paths in a SQLite backup before an upgrade
repoints them. A failed backup defers that path rewrite. Error messages point
to the resolved server log and to **Reveal Server Log in Finder**. The header
loads a smaller copy of the logo.

## Release packaging

The DMG command packages the existing Developer ID signed and stapled app.
Release checks inspect the final Python entitlements. The DMG is signed,
notarized and stapled before its SHA-256 is written with the image filename.

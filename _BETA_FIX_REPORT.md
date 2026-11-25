# Second Hand Smoke 0.4.1 beta fix report

The macOS ARM64 test rig finished with **633 passed, zero failed, across 40
sections**. The baseline passed 607 checks. Both runs used the local
`.venv/bin/python` with cached ReportLab 5.1.0 because the declared 4.x package
was unavailable offline. The repository's dependency declaration still
specifies 4.x.

## Changes by file

| File | Change |
|---|---|
| `Build Second Hand Smoke.command` | Runs the shared app signer and verifies the resulting signatures and staple. A failed release returns a nonzero status. |
| `Make Second Hand Smoke DMG.command` | Keeps the double-click entry point and returns the packaging status after the pause. |
| `macos/verify_release_app.sh` | Checks Developer ID, hardened runtime, signatures and both Python entitlements on the server, Swift executable, nested libraries and app. Validates the app staple. |
| `macos/make_dmg.sh` | Packages the existing app in `dist/`. Keeps the version gate, refuses ad-hoc builds, signs/notarizes/staples the DMG, then writes one basename-only SHA-256. |
| `app.py` | Resolves the log path for HTTP 500s, serves the smaller logo, passes destination labels to the scan and exposes the existing display gate to card rows. Direct source launches acquire the catalog lock before initialization. |
| `db.py` | Takes the pre-upgrade backup before repointing legacy paths. A failed backup defers the repoint. Backup filenames distinguish snapshots taken within the same second. |
| `cards.py` | Explains device and path exclusions for volumes with media using the destination label. Both exclusions still block the card from scan results. |
| `app.js` | Preserves panel choices in memory, folds the new-day form, renders quiet verify-only actions for cleared cards and supplies disabled-button reasons. Cards cleared with exclusions keep an explicit warning. |
| `index.html` | Places Destinations and Card in the Reader directly below Shoot Day, adds the active-day display and More controls, and uses the smaller logo. |
| `app.css` | Styles the collapsed setup panels and More controls and raises disabled-button opacity to .55. |
| `assets/logo-header.png` | Adds a 320 × 180 transparent header image, about 110 KB. |
| `SecondHandSmoke.spec` | Bundles the new header asset. |
| `test_rig.py` | Adds release, catalog, scan, log, asset and UI checks. Isolates the ffmpeg fallback tests from host PATH and updates the temporary folder prefix. |
| `README.md` | Updates the rig count, release sequence and log location. |
| `MANUAL.md` | Updates the version, rig count, release sequence, catalog locking and log location. |
| `macos/README.md` | Documents entitlement discovery, signing and packaging order, installed-app verification and the resolved log. |
| `VERSION` | Bumps the app to 0.4.1. |
| `RELEASE_NOTES_v0.4.1.md` | Summarizes the fixes for beta users. |

Copying, hashing, byte read-back, format-gate decisions and the
`cf-smoker-handoff-1` handoff schema use their existing code. The shared
`release.command` was inspected and left untouched. It already discovers
`macos/entitlements.plist` from the repository above `dist/` and signs
nested code first.

## Verification pending

- This Mac reported zero valid code-signing identities. The rig exercised real
  macOS rejection of an ad-hoc app and a version mismatch. A full Developer ID
  build, notarized DMG, Gatekeeper assessment and signed-server launch still
  need verification on a signing-equipped Mac.
- The host has ffmpeg in Homebrew. The fallback tests use an empty temporary
  PATH, so system PATH cannot affect them. A separate run on a host with
  `/usr/bin/ffmpeg` was unavailable.
- The rig checked that Destinations directly follows Shoot Day. The headless
  browser did not return a rendered result for the 1440 × 900 layout check.

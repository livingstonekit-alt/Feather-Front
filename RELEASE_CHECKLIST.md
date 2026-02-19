# Release Checklist (Packaging/Distribution)

Use this checklist before publishing a FeatherFront release.

## 1. Security and secrets

- [ ] Confirm no local secrets are tracked (`settings.json`, old `config.json`, credentials, tokens).
- [ ] Verify sensitive values are redacted in API output (`/api/status` stream URL query secrets).
- [ ] Confirm auth settings behavior for `/settings` and protected `/api/*` routes.

## 2. Data/privacy

- [ ] Confirm local runtime data is excluded from git/package:
  - `birdnet-overlay/data/overlay.db*`
  - `birdnet-overlay/data/clips/`
  - `birdnet-overlay/data/monitor.wav`
  - any local logs
- [ ] Confirm no install-specific paths/usernames/ZIPs are in tracked files.

## 3. Licensing/compliance

- [ ] Review `THIRD_PARTY.md`.
- [ ] Review `LICENSES.md`.
- [ ] Verify redistribution terms for BirdNET Analyzer and FFmpeg.
- [ ] Include required license/notice files in packaged artifacts.

## 4. Build and runtime validation

- [ ] Run installer flow (`birdnet-overlay/install.sh`) on a clean environment.
- [ ] Validate configured port flow (no hardcoded `8002` assumptions in docs/UI).
- [ ] Validate LaunchAgent setup (macOS): `RunAtLoad` + `KeepAlive`.
- [ ] Verify core endpoints:
  - `/`
  - `/weather/`
  - `/settings` (auth)
  - `/api/status`
  - `/api/weather/settings`

## 5. Functional checks

- [ ] Confirm real-time detections continue to update counts.
- [ ] Confirm summary cache updates with new detections/deletes.
- [ ] Confirm icon mappings resolve from SQLite table (`species_icons`).
- [ ] Confirm weather settings save/load correctly.

## 6. Documentation and release metadata

- [ ] Update `README.md` and `birdnet-overlay/README.md` if behavior changed.
- [ ] Update version/tag and changelog notes.
- [ ] Create annotated git tag for release.
- [ ] Push commit + tag to remote.

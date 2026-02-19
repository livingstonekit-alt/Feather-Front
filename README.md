# Feather Front

Feather Front is a real-time bird activity system for streams and monitoring setups.
It combines live bird-call detection, rolling detection summaries, clip compilation, and weather context in a single interface.

## What it provides

- Real-time bird call detection from stream or local audio input
- Live activity feed and per-species detection counts
- Rolling call compilation metadata and best clip tracking
- Weather overlay and settings tied to the same runtime system
- Unified settings panel for audio, detection, location, weather, and admin controls

## Platform status

- Current end-to-end testing has been performed on a Mac mini (macOS).
- Other platforms may work, but have not been fully validated in this repository yet.

## Visuals

Feather Front in action:

![Feather Front in action](screenshots/Screenshot%202026-02-19%2015-20-31.png)

Detection overlay:

![Detection overlay](screenshots/Screenshot%202026-02-19%20151312.png)

Weather overlay:

![Weather overlay](screenshots/Screenshot%202026-02-19%20151322.png)

Settings interface:

![Settings interface](screenshots/Screenshot%202026-02-19%20151344.png)

Activity and detections panels:

![Activity and detections](screenshots/Screenshot%202026-02-19%20151400.png)

## Run it locally

Full Feather Front stack (recommended):

```bash
cd birdnet-overlay
./install.sh
```

Then start:

```bash
python3 server.py
```

Then open:

- `http://localhost:<PORT>/` (Feather Front overlay)
- `http://localhost:<PORT>/weather/` (weather overlay)
- `http://localhost:<PORT>/settings` (settings panel)

If settings auth is enabled, use your configured admin credentials.

## OBS Bird Cam Overlays

Use OBS Browser Sources for these endpoints:

- Bird activity overlay: `http://<HOST>:<PORT>/`
- Weather overlay: `http://<HOST>:<PORT>/weather/`

Recommended OBS Browser Source settings:

- Width/Height: match your scene design (for example `1920x1080`)
- `Shutdown source when not visible`: enabled
- `Refresh browser when scene becomes active`: enabled
- `Control audio via OBS`: disabled (unless intentionally using overlay audio)

## Live Example

- Backwoods Rustic (YouTube): https://www.youtube.com/@BackwoodsRustic

## Third-party references

- See `THIRD_PARTY.md` for external software/services and upstream project links.
- See `LICENSES.md` for distribution/licensing notes.
- See `RELEASE_CHECKLIST.md` for packaging/release validation steps.

## Customize

- Update interval lives in `app.js`.
- Visuals live in `styles.css`.
- Weather location/unit can be controlled from Feather Front settings (`http://localhost:<PORT>/settings`).
- The overlay reads weather settings from `/api/weather/settings` and falls back to `http://localhost:<PORT>/api/weather/settings`.

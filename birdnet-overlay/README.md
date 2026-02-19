# FeatherFront Detection Engine

The FeatherFront detection engine handles real-time bird-call analysis, live event logging, species-level summaries, best-clip tracking, and integrated weather settings for overlays.

## Platform status

- Current end-to-end testing has been performed on a Mac mini (macOS).
- Other platforms may work, but have not been fully validated yet.

## Requirements

- `ffmpeg`
- BirdNET Analyzer (Python package or app with a CLI)

## Quick start

1) Run the installer (checks dependencies, prompts for port/settings, and can configure a macOS LaunchAgent with keepalive):

```bash
./install.sh
```

2) Run the server:

```
python3 server.py
```

3) Open the overlay:

```
http://localhost:<PORT>
```

Settings panel:

```
http://localhost:<PORT>/settings
```

## OBS Bird Cam Overlays

Add OBS Browser Sources using:

- Detection overlay: `http://<HOST>:<PORT>/`
- Weather overlay: `http://<HOST>:<PORT>/weather/`
- (Optional) Settings/admin page in a browser: `http://<HOST>:<PORT>/settings`

Recommended OBS Browser Source options:

- Enable `Shutdown source when not visible`
- Enable `Refresh browser when scene becomes active`
- Set source dimensions explicitly (commonly `1920x1080`)

You can restart the capture pipeline from the settings panel or via:

- `POST http://localhost:<PORT>/api/restart`

Log management:

- `GET http://localhost:<PORT>/api/log`
- `POST http://localhost:<PORT>/api/log/delete` with JSON `{ \"id\": \"...\" }`

## Configuration

You can edit `birdnet-overlay/settings.json` or set env vars:

- `INPUT_MODE`: `stream` (RTMP/RTSP) or `avfoundation` (local audio)
- `INPUT_DEVICE`: audio device index for avfoundation input
- `RTMP_URL`: RTMP/RTSP input (example: `rtsp://user:pass@camera:554/stream`)
- `HTTP_PORT`: defaults to installer-selected port (commonly `8002`)
- `SEGMENT_SECONDS`: audio segment length, defaults to `3`
- `MIN_CONFIDENCE`: confidence threshold, defaults to `0.25`
- `LATITUDE`: latitude for BirdNET location filtering (default `-1`)
- `LONGITUDE`: longitude for BirdNET location filtering (default `-1`)
- `WEEK`: week of year (1-48) for BirdNET filtering (default `-1`)
- `WEATHER_LOCATION`: weather query for the weather overlay (ZIP/city, default `YOUR_ZIP`)
- `WEATHER_UNIT`: `fahrenheit` or `celsius` for the weather overlay
- `BIRDNET_TEMPLATE`: command template that consumes `{input}` and writes CSV results to `{output}`
- `BIRDNET_WORKDIR`: working directory for the BirdNET command (useful if it needs model files)

The default `BIRDNET_TEMPLATE` assumes the BirdNET Analyzer package is installed:

```
python3 -m birdnet_analyzer.analyze {input} -o {output} --rtype csv --min_conf {min_conf} --lat {lat} --lon {lon} --week {week}
```

If you install BirdNET into the local virtualenv at `birdnet-overlay/.venv`, point the template at that interpreter:

```
./.venv/bin/python -m birdnet_analyzer.analyze {input} -o {output} --rtype csv --min_conf {min_conf} --lat {lat} --lon {lon} --week {week}
```

If your BirdNET install uses a different command, update the template to match. It must:

- Read the wav file at `{input}`
- Write a BirdNET CSV result into `{output}` (a directory is expected)

Supported template variables: `{input}`, `{output}`, `{min_conf}`, `{segment_seconds}`, `{lat}`, `{lon}`, `{week}`.

The settings UI now uses `WEATHER_LOCATION` as the display location label for detections/overlay metadata.
BirdNET filtering still uses `LATITUDE`, `LONGITUDE`, and `WEEK`/`AUTO_WEEK`.

## Output

Live results are written to:

- `birdnet-overlay/data/latest.json`

The overlay (`index.html`) polls that file every 2 seconds.
Credentials in `RTMP_URL` are stripped from `stream_url` before writing output.
The JSON also includes `last_detection` and `last_heard` to keep the most recent call visible.

Log endpoint:

- `http://localhost:<PORT>/api/log`

The same JSON is available at:

- `http://localhost:<PORT>/api/status`
- `http://localhost:<PORT>/api/weather/settings`

## Third-party references

- See repository-level `THIRD_PARTY.md` for dependency/source attribution.

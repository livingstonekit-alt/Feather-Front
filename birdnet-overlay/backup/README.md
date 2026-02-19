# BirdNET Overlay

A lightweight RTMP/RTSP-to-BirdNET bridge plus a matching overlay UI.
The server listens on port 8002 and serves the overlay while updating `data/latest.json`.

## Requirements

- `ffmpeg`
- BirdNET Analyzer (Python package or app with a CLI)

## Quick start

1) Edit `config.json` or set environment variables.
2) Run the server:

```
python3 server.py
```

3) Open the overlay:

```
http://localhost:8002
```

Settings panel:

```
http://localhost:8002/settings
```

You can restart the capture pipeline from the settings panel or via:

- `POST http://localhost:8002/api/restart`

Log management:

- `GET http://localhost:8002/api/log`
- `POST http://localhost:8002/api/log/delete` with JSON `{ \"id\": \"...\" }`

## Configuration

You can edit `birdnet-overlay/config.json` or set env vars:

- `INPUT_MODE`: `stream` (RTMP/RTSP) or `avfoundation` (local audio)
- `INPUT_DEVICE`: audio device index for avfoundation input
- `RTMP_URL`: RTMP/RTSP input (example: `rtsp://user:pass@camera:554/stream`)
- `HTTP_PORT`: defaults to `8002`
- `SEGMENT_SECONDS`: audio segment length, defaults to `3`
- `MIN_CONFIDENCE`: confidence threshold, defaults to `0.25`
- `LOCATION_LABEL`: label shown in the badge
- `LATITUDE`: latitude for BirdNET location filtering (default `-1`)
- `LONGITUDE`: longitude for BirdNET location filtering (default `-1`)
- `WEEK`: week of year (1-48) for BirdNET filtering (default `-1`)
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

## Output

Live results are written to:

- `birdnet-overlay/data/latest.json`

The overlay (`index.html`) polls that file every 2 seconds.
Credentials in `RTMP_URL` are stripped from `stream_url` before writing output.
The JSON also includes `last_detection` and `last_heard` to keep the most recent call visible.

Log endpoint:

- `http://localhost:8002/api/log`

The same JSON is available at:

- `http://localhost:8002/api/status`

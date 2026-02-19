#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS_FILE="$SCRIPT_DIR/settings.json"
EXAMPLE_FILE="$SCRIPT_DIR/config.example.json"

choose_python() {
  local candidates=(
    "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
    "python3"
  )
  local py=""
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == /* ]]; then
      [[ -x "$candidate" ]] || continue
      py="$candidate"
    else
      py="$(command -v "$candidate" || true)"
      [[ -n "$py" ]] || continue
    fi
    if "$py" - <<'PY' >/dev/null 2>&1
import audioop
import json
import sqlite3
PY
    then
      echo "$py"
      return 0
    fi
  done
  return 1
}

prompt_yn() {
  local prompt="${1:-Continue?}"
  local default="${2:-Y}"
  local value=""
  while true; do
    read -r -p "$prompt " value || true
    value="${value:-$default}"
    case "$(echo "$value" | tr '[:upper:]' '[:lower:]')" in
      y|yes) return 0 ;;
      n|no) return 1 ;;
    esac
  done
}

echo "FeatherFront installer"
echo "Directory: $SCRIPT_DIR"

PYTHON_BIN="$(choose_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: unable to find a compatible Python interpreter (requires audioop)."
  echo "Tip: on macOS, install or use Python 3.9 from Command Line Tools."
  exit 1
fi
echo "Using Python: $PYTHON_BIN"

if command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg: OK ($(command -v ffmpeg))"
else
  echo "Warning: ffmpeg not found on PATH."
  echo "Install ffmpeg before running live capture."
fi

if [[ ! -f "$SETTINGS_FILE" ]]; then
  cp "$EXAMPLE_FILE" "$SETTINGS_FILE"
  echo "Created settings file: $SETTINGS_FILE"
else
  echo "Settings file already exists: $SETTINGS_FILE"
fi

CURRENT_PORT="$("$PYTHON_BIN" - "$SETTINGS_FILE" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    value = int(data.get("http_port", 8002))
except Exception:
    value = 8002
print(value)
PY
)"
read -r -p "HTTP port for FeatherFront [$CURRENT_PORT]: " PORT_INPUT || true
PORT_INPUT="${PORT_INPUT:-$CURRENT_PORT}"
if [[ ! "$PORT_INPUT" =~ ^[0-9]+$ ]] || (( PORT_INPUT < 1 || PORT_INPUT > 65535 )); then
  echo "Invalid port: $PORT_INPUT"
  exit 1
fi
SELECTED_PORT="$PORT_INPUT"
"$PYTHON_BIN" - "$SETTINGS_FILE" "$SELECTED_PORT" <<'PY'
import json, sys
path, port = sys.argv[1], int(sys.argv[2])
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
data["http_port"] = port
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=True, indent=2)
    handle.write("\n")
print(f"Saved http_port={port} to {path}")
PY

if prompt_yn "Create/update .venv and install Python tooling? [Y/n]" "Y"; then
  "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv"
  "$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    "$SCRIPT_DIR/.venv/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
  fi
  if prompt_yn "Install/upgrade birdnet-analyzer in .venv? [y/N]" "N"; then
    "$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade birdnet-analyzer
  fi
fi

if prompt_yn "Run interactive settings wizard now? [Y/n]" "Y"; then
  "$PYTHON_BIN" - "$SETTINGS_FILE" <<'PY'
import getpass
import hashlib
import json
import os
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    cfg = json.load(handle)

def ask_text(label, key, fallback=""):
    current = str(cfg.get(key, fallback))
    value = input(f"{label} [{current}]: ").strip()
    if value:
        cfg[key] = value

def ask_float(label, key, fallback):
    current = cfg.get(key, fallback)
    value = input(f"{label} [{current}]: ").strip()
    if value:
        try:
            cfg[key] = float(value)
        except ValueError:
            print(f"Invalid number for {label}; keeping {current}")

def ask_int(label, key, fallback):
    current = cfg.get(key, fallback)
    value = input(f"{label} [{current}]: ").strip()
    if value:
        try:
            cfg[key] = int(value)
        except ValueError:
            print(f"Invalid integer for {label}; keeping {current}")

def ask_bool(label, key, fallback=False):
    current = bool(cfg.get(key, fallback))
    default = "Y" if current else "N"
    value = input(f"{label} [y/n, default {default}]: ").strip().lower()
    if not value:
        return
    if value in {"y", "yes", "1", "true", "on"}:
        cfg[key] = True
    elif value in {"n", "no", "0", "false", "off"}:
        cfg[key] = False

def hash_password(text):
    salt = os.urandom(16).hex()
    iterations = 210000
    digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"

print("\nCore settings")
ask_text("Input mode (stream/avfoundation)", "input_mode", "stream")
ask_text("Stream URL (RTMP/RTSP)", "rtmp_url", "")
ask_text("Input device index", "input_device", "0")
ask_float("Segment seconds", "segment_seconds", 3.5)
ask_float("Min confidence (0-1)", "min_confidence", 0.45)
ask_float("Silence threshold dB", "silence_threshold_db", -44.0)
ask_float("Silence min seconds", "silence_min_seconds", 0.3)
ask_float("Overlay hold seconds", "overlay_hold_seconds", 5.0)
ask_bool("Overlay sticky", "overlay_sticky", False)

print("\nBirdNET filter settings")
ask_text("Location label", "location", "Stream")
ask_float("Latitude", "latitude", -1)
ask_float("Longitude", "longitude", -1)
ask_int("Week (-1 for year-round)", "week", -1)
ask_bool("Auto week", "auto_week", True)

print("\nWeather settings")
ask_text("Weather location (ZIP/city)", "weather_location", "YOUR_ZIP")
ask_text("Weather unit (fahrenheit/celsius)", "weather_unit", "fahrenheit")

print("\nBirdNET command settings")
ask_text("BirdNET template", "birdnet_template", cfg.get("birdnet_template", ""))
ask_text("BirdNET workdir", "birdnet_workdir", cfg.get("birdnet_workdir", "."))

print("\nSettings auth")
ask_bool("Enable settings auth", "settings_auth_enabled", False)
ask_text("Auth username", "settings_auth_user", "admin")
if cfg.get("settings_auth_enabled"):
    password = getpass.getpass("Set admin password (leave blank to keep existing hash): ").strip()
    if password:
        cfg["settings_auth_password_hash"] = hash_password(password)
    elif not cfg.get("settings_auth_password_hash"):
        print("No existing password hash found; disabling settings auth for safety.")
        cfg["settings_auth_enabled"] = False

with open(path, "w", encoding="utf-8") as handle:
    json.dump(cfg, handle, ensure_ascii=True, indent=2)
    handle.write("\n")
print(f"\nSaved settings to {path}")
PY
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  if prompt_yn "Install/update macOS LaunchAgent for auto-start + keepalive? [Y/n]" "Y"; then
    LAUNCH_DIR="$HOME/Library/LaunchAgents"
    LAUNCH_PLIST="$LAUNCH_DIR/com.featherfront.overlay.plist"
    mkdir -p "$LAUNCH_DIR"

    cat > "$LAUNCH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.featherfront.overlay</string>
    <key>ProgramArguments</key>
    <array>
      <string>$PYTHON_BIN</string>
      <string>$SCRIPT_DIR/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/server.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/server.log</string>
  </dict>
</plist>
PLIST

    uid="$(id -u)"
    launchctl bootout "gui/$uid/com.featherfront.overlay" >/dev/null 2>&1 || true
    launchctl enable "gui/$uid/com.featherfront.overlay" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$uid" "$LAUNCH_PLIST" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid/com.featherfront.overlay" >/dev/null 2>&1 || true
    echo "LaunchAgent installed: $LAUNCH_PLIST"

    if prompt_yn "Disable legacy com.weather.overlay LaunchAgent? [Y/n]" "Y"; then
      launchctl bootout "gui/$uid/com.weather.overlay" >/dev/null 2>&1 || true
      launchctl disable "gui/$uid/com.weather.overlay" >/dev/null 2>&1 || true
      echo "Disabled legacy com.weather.overlay"
    fi

    if prompt_yn "Disable legacy com.birdnet.overlay LaunchAgent? [Y/n]" "Y"; then
      launchctl bootout "gui/$uid/com.birdnet.overlay" >/dev/null 2>&1 || true
      launchctl disable "gui/$uid/com.birdnet.overlay" >/dev/null 2>&1 || true
      echo "Disabled legacy com.birdnet.overlay"
    fi
  fi
fi

echo
echo "Install complete."
echo "Start FeatherFront:"
echo "  cd \"$SCRIPT_DIR\""
echo "  \"$PYTHON_BIN\" server.py"
echo
echo "Open:"
echo "  http://localhost:$SELECTED_PORT/"
echo "  http://localhost:$SELECTED_PORT/settings"

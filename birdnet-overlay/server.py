#!/usr/bin/env python3
import audioop
import base64
import csv
import cgi
import hmac
import queue
import io
import math
import wave
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
WEATHER_ROOT = ROOT.parent
DATA_DIR = ROOT / "data"
TMP_DIR = ROOT / "tmp"
LATEST_PATH = DATA_DIR / "latest.json"
SETTINGS_PATH = ROOT / "settings.json"
LEGACY_CONFIG_PATH = ROOT / "config.json"
LOG_PATH = DATA_DIR / "detections.jsonl"
EVENTS_PATH = DATA_DIR / "events.jsonl"
DB_PATH = DATA_DIR / "overlay.db"
CLIPS_DIR = DATA_DIR / "clips"
CLIP_INDEX_PATH = DATA_DIR / "clips.json"
ICONS_DIR = DATA_DIR / "icons"
ICON_INDEX_PATH = DATA_DIR / "icons.json"
ANALYSIS_MIN_CONF = 0.01
MAX_QUEUE_SEGMENTS = 60
GATE_WORKERS = 1
ANALYSIS_WORKERS = 3
MAX_ANALYSIS_BACKLOG = 24
MAX_SEGMENT_AGE_SECONDS = 30.0
FALLBACK_FFMPEG_PATHS = (
  "/opt/homebrew/bin/ffmpeg",
  "/usr/local/bin/ffmpeg",
)

DEFAULT_CONFIG = {
  "http_port": 8002,
  "input_mode": "stream",
  "input_device": "",
  "rtmp_url": "",
  "segment_seconds": 3,
  "min_confidence": 0.25,
  "silence_threshold_db": -45.0,
  "silence_min_seconds": 0.2,
  "overlay_hold_seconds": 60,
  "overlay_sticky": False,
  "birdnet_template": "python3 -m birdnet_analyzer.analyze {input} -o {output} --rtype csv --min_conf {min_conf} --lat {lat} --lon {lon} --week {week}",
  "birdnet_workdir": "",
  "location": "Stream",
  "latitude": -1,
  "longitude": -1,
  "week": -1,
  "auto_week": False,
  "weather_location": "YOUR_ZIP",
  "weather_unit": "fahrenheit",
  "settings_auth_enabled": False,
  "settings_auth_user": "admin",
  "settings_auth_password_hash": "",
}

write_lock = threading.Lock()
analysis_error_lock = threading.Lock()
analysis_last_error = None
config_lock = threading.Lock()
log_lock = threading.Lock()
event_lock = threading.Lock()
restart_capture = threading.Event()
last_detection_lock = threading.Lock()
last_detection = None
log_revision_lock = threading.Lock()
log_revision = int(time.time() * 1000)
species_lock = threading.Lock()
species_set = set()
species_count_lock = threading.Lock()
species_counts = {}
capture_pid_lock = threading.Lock()
current_capture_pid = None


def restart_server_process():
  # Delay a bit so the HTTP response can flush before process replacement.
  time.sleep(0.2)
  python = sys.executable
  args = [python, *sys.argv]
  os.execv(python, args)


def now_iso():
  return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_week():
  now = datetime.now()
  day_of_year = now.timetuple().tm_yday
  week = int((day_of_year - 1) / 7) + 1
  return max(1, min(48, week))


def get_log_revision():
  with log_revision_lock:
    return log_revision


def bump_log_revision():
  global log_revision
  with log_revision_lock:
    candidate = int(time.time() * 1000)
    if candidate <= log_revision:
      candidate = log_revision + 1
    log_revision = candidate
    return log_revision


def rebuild_species_set():
  entries = read_log(None)
  species = set()
  for entry in entries:
    species.add(entry.get("species", "Unknown") or "Unknown")
  with species_lock:
    global species_set
    species_set = species


def rebuild_species_counts():
  entries = read_log(None)
  counts = {}
  for entry in entries:
    species = entry.get("species", "Unknown") or "Unknown"
    counts[species] = counts.get(species, 0) + 1
  with species_count_lock:
    global species_counts
    species_counts = counts


def update_species_set(entries):
  if isinstance(entries, dict):
    entries = [entries]
  with species_lock:
    for entry in entries:
      species_set.add(entry.get("species", "Unknown") or "Unknown")


def update_species_counts(entries):
  if isinstance(entries, dict):
    entries = [entries]
  with species_count_lock:
    for entry in entries:
      species = entry.get("species", "Unknown") or "Unknown"
      species_counts[species] = species_counts.get(species, 0) + 1


def get_species_count():
  with species_lock:
    return len(species_set)


def get_species_heard_count(species):
  if not species:
    return 0
  with species_count_lock:
    return species_counts.get(species, 0)


def get_species_rank(species):
  if not species:
    return None
  with species_count_lock:
    items = list(species_counts.items())
  if not items:
    return None
  items.sort(key=lambda item: (-item[1], item[0]))
  for index, (name, _count) in enumerate(items, start=1):
    if name == species:
      return index
  return None


def hash_password(password, salt=None, iterations=210000):
  text = str(password or "")
  if salt is None:
    salt = os.urandom(16).hex()
  else:
    salt = str(salt)
  digest = hashlib.pbkdf2_hmac(
    "sha256",
    text.encode("utf-8"),
    salt.encode("utf-8"),
    int(iterations),
  ).hex()
  return f"pbkdf2_sha256${int(iterations)}${salt}${digest}"


def verify_password(password, encoded_hash):
  if not encoded_hash:
    return False
  try:
    scheme, iterations_text, salt, expected = str(encoded_hash).split("$", 3)
    if scheme != "pbkdf2_sha256":
      return False
    iterations = int(iterations_text)
  except (ValueError, TypeError):
    return False
  computed = hash_password(password, salt=salt, iterations=iterations)
  return hmac.compare_digest(computed, str(encoded_hash))


def db_connect():
  connection = sqlite3.connect(str(DB_PATH), timeout=30)
  connection.row_factory = sqlite3.Row
  connection.execute("PRAGMA journal_mode=WAL")
  connection.execute("PRAGMA synchronous=NORMAL")
  return connection


def init_database():
  DATA_DIR.mkdir(parents=True, exist_ok=True)
  with db_connect() as connection:
    connection.execute(
      """
      CREATE TABLE IF NOT EXISTS detections (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        species TEXT,
        scientific_name TEXT,
        confidence REAL,
        location TEXT,
        raw_json TEXT NOT NULL
      )
      """
    )
    connection.execute(
      """
      CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        type TEXT,
        message TEXT,
        raw_json TEXT NOT NULL
      )
      """
    )
    connection.execute(
      """
      CREATE TABLE IF NOT EXISTS species_icons (
        species_key TEXT PRIMARY KEY,
        species_name TEXT,
        filename TEXT NOT NULL,
        updated_at TEXT NOT NULL
      )
      """
    )
    connection.execute(
      """
      CREATE TABLE IF NOT EXISTS summary_cache (
        cache_key TEXT PRIMARY KEY,
        log_revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
      )
      """
    )
    connection.execute(
      "CREATE INDEX IF NOT EXISTS idx_detections_timestamp ON detections(timestamp)"
    )
    connection.execute(
      "CREATE INDEX IF NOT EXISTS idx_detections_species ON detections(species)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_species_icons_filename ON species_icons(filename)")
    connection.commit()
  migrate_legacy_jsonl_if_needed()
  migrate_legacy_icon_index_if_needed()


def iter_jsonl_file(path):
  if not path.exists():
    return
  try:
    with path.open("r", encoding="utf-8") as handle:
      for line in handle:
        if not line.strip():
          continue
        try:
          parsed = json.loads(line)
        except json.JSONDecodeError:
          continue
        if isinstance(parsed, dict):
          yield parsed
  except OSError:
    return


def migrate_legacy_jsonl_if_needed():
  with db_connect() as connection:
    det_count = connection.execute("SELECT COUNT(1) AS count FROM detections").fetchone()["count"]
    evt_count = connection.execute("SELECT COUNT(1) AS count FROM events").fetchone()["count"]

    if det_count == 0:
      payload = []
      for row in iter_jsonl_file(LOG_PATH):
        row = dict(row)
        row["id"] = entry_id(row)
        payload.append(
          (
            row["id"],
            str(row.get("timestamp") or now_iso()),
            row.get("species", "Unknown"),
            row.get("scientific_name", ""),
            normalize_confidence(row.get("confidence")),
            row.get("location", ""),
            json.dumps(row, ensure_ascii=True),
          )
        )
        if len(payload) >= 1000:
          connection.executemany(
            """
            INSERT OR REPLACE INTO detections
            (id, timestamp, species, scientific_name, confidence, location, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
          )
          payload = []
      if payload:
        connection.executemany(
          """
          INSERT OR REPLACE INTO detections
          (id, timestamp, species, scientific_name, confidence, location, raw_json)
          VALUES (?, ?, ?, ?, ?, ?, ?)
          """,
          payload,
        )

    if evt_count == 0:
      payload = []
      for row in iter_jsonl_file(EVENTS_PATH):
        row = dict(row)
        row["id"] = event_id(row)
        payload.append(
          (
            row["id"],
            str(row.get("timestamp") or now_iso()),
            row.get("type", ""),
            row.get("message", ""),
            json.dumps(row, ensure_ascii=True),
          )
        )
        if len(payload) >= 1000:
          connection.executemany(
            """
            INSERT OR REPLACE INTO events
            (id, timestamp, type, message, raw_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            payload,
          )
          payload = []
      if payload:
        connection.executemany(
          """
          INSERT OR REPLACE INTO events
          (id, timestamp, type, message, raw_json)
          VALUES (?, ?, ?, ?, ?)
          """,
          payload,
        )
    connection.commit()


def parse_icon_index_file(path):
  if not path.exists():
    return {}
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}
  return data if isinstance(data, dict) else {}


def migrate_legacy_icon_index_if_needed():
  legacy_index = parse_icon_index_file(ICON_INDEX_PATH)
  if not legacy_index:
    return
  with db_connect() as connection:
    current_count = connection.execute("SELECT COUNT(1) AS count FROM species_icons").fetchone()["count"]
    if current_count > 0:
      return
    payload = []
    stamp = now_iso()
    for key, filename in legacy_index.items():
      species_key = normalize_species_key(key)
      filename_text = str(filename or "").strip()
      if not species_key or not filename_text:
        continue
      payload.append((species_key, str(key or ""), filename_text, stamp))
    if payload:
      connection.executemany(
        """
        INSERT OR REPLACE INTO species_icons
        (species_key, species_name, filename, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        payload,
      )
      connection.commit()


def load_config():
  config = dict(DEFAULT_CONFIG)
  source_path = SETTINGS_PATH if SETTINGS_PATH.exists() else LEGACY_CONFIG_PATH
  if source_path.exists():
    try:
      with source_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
      if isinstance(data, dict):
        for key in config:
          if key in data:
            config[key] = data[key]
    except (OSError, json.JSONDecodeError):
      pass

  def env_override(key, env_name, cast):
    value = os.getenv(env_name)
    if value is None or value == "":
      return
    try:
      config[key] = cast(value)
    except ValueError:
      return

  def normalize_weather_unit(value):
    unit = str(value or "").strip().lower()
    if unit in {"c", "celsius", "metric"}:
      return "celsius"
    return "fahrenheit"

  def normalize_bool(value):
    if isinstance(value, str):
      return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)

  env_override("http_port", "HTTP_PORT", int)
  env_override("input_mode", "INPUT_MODE", str)
  env_override("input_device", "INPUT_DEVICE", str)
  env_override("rtmp_url", "RTMP_URL", str)
  env_override("segment_seconds", "SEGMENT_SECONDS", float)
  env_override("min_confidence", "MIN_CONFIDENCE", float)
  env_override("silence_threshold_db", "SILENCE_THRESHOLD_DB", float)
  env_override("silence_min_seconds", "SILENCE_MIN_SECONDS", float)
  env_override("overlay_hold_seconds", "OVERLAY_HOLD_SECONDS", float)
  env_override("overlay_sticky", "OVERLAY_STICKY", lambda value: value.lower() in {"1", "true", "yes", "on"})
  env_override("birdnet_template", "BIRDNET_TEMPLATE", str)
  env_override("birdnet_workdir", "BIRDNET_WORKDIR", str)
  env_override("location", "LOCATION_LABEL", str)
  env_override("latitude", "LATITUDE", float)
  env_override("longitude", "LONGITUDE", float)
  env_override("week", "WEEK", int)
  env_override("auto_week", "AUTO_WEEK", lambda value: value.lower() in {"1", "true", "yes", "on"})
  env_override("weather_location", "WEATHER_LOCATION", str)
  env_override("weather_unit", "WEATHER_UNIT", normalize_weather_unit)
  env_override("settings_auth_enabled", "SETTINGS_AUTH_ENABLED", normalize_bool)
  env_override("settings_auth_user", "SETTINGS_AUTH_USER", str)
  env_override("settings_auth_password_hash", "SETTINGS_AUTH_PASSWORD_HASH", str)

  plain_password = os.getenv("SETTINGS_AUTH_PASSWORD")
  if plain_password:
    config["settings_auth_password_hash"] = hash_password(plain_password)
    config["settings_auth_enabled"] = True

  config["weather_unit"] = normalize_weather_unit(config.get("weather_unit"))
  config["weather_location"] = str(config.get("weather_location") or "YOUR_ZIP")
  config["settings_auth_enabled"] = normalize_bool(config.get("settings_auth_enabled"))
  config["settings_auth_user"] = str(config.get("settings_auth_user") or "admin").strip() or "admin"
  config["settings_auth_password_hash"] = str(config.get("settings_auth_password_hash") or "").strip()

  # Consolidate runtime settings into a single canonical file.
  if not SETTINGS_PATH.exists():
    write_config(config)

  return config


def get_config_snapshot(config):
  with config_lock:
    snapshot = dict(config)
  snapshot.pop("settings_auth_password_hash", None)
  snapshot["current_week"] = current_week()
  return snapshot


def write_config(config):
  encoded = json.dumps(config, ensure_ascii=True, indent=2)
  SETTINGS_PATH.write_text(encoded, encoding="utf-8")


def update_config(config, updates):
  changed = set()
  restart_fields = {"input_mode", "input_device", "rtmp_url", "segment_seconds"}
  allowed = {
    "http_port",
    "input_mode",
    "input_device",
    "rtmp_url",
    "segment_seconds",
    "min_confidence",
    "silence_threshold_db",
    "silence_min_seconds",
    "overlay_hold_seconds",
    "overlay_sticky",
    "birdnet_template",
    "birdnet_workdir",
    "location",
    "latitude",
    "longitude",
    "week",
    "auto_week",
    "weather_location",
    "weather_unit",
  }

  def normalize_input_mode(value):
    value = str(value or "").strip().lower()
    if value in {"rtmp", "rtsp", "stream"}:
      return "stream"
    if value in {"avfoundation", "device", "local"}:
      return "avfoundation"
    return "stream"

  def cast_value(key, value):
    if key in {
      "input_mode",
      "input_device",
      "rtmp_url",
      "birdnet_template",
      "birdnet_workdir",
      "location",
      "weather_location",
    }:
      return str(value)
    if key == "auto_week":
      if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
      return bool(value)
    if key == "segment_seconds":
      return max(0.1, float(value))
    if key == "http_port":
      port = int(value)
      return max(1, min(65535, port))
    if key == "min_confidence":
      return max(0.0, min(1.0, float(value)))
    if key == "silence_threshold_db":
      return max(-120.0, min(0.0, float(value)))
    if key == "silence_min_seconds":
      return max(0.0, float(value))
    if key == "overlay_hold_seconds":
      return max(1.0, float(value))
    if key == "overlay_sticky":
      if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
      return bool(value)
    if key in {"latitude", "longitude"}:
      return float(value)
    if key == "week":
      return int(value)
    if key == "weather_unit":
      unit = str(value or "").strip().lower()
      if unit in {"c", "celsius", "metric"}:
        return "celsius"
      return "fahrenheit"
    return value

  with config_lock:
    for key, value in updates.items():
      if key not in allowed:
        continue
      if key == "input_mode":
        value = normalize_input_mode(value)
      try:
        normalized = cast_value(key, value)
      except (TypeError, ValueError):
        continue
      if config.get(key) != normalized:
        config[key] = normalized
        changed.add(key)

    if changed:
      write_config(config)

  if restart_fields.intersection(changed):
    restart_capture.set()

  return changed


def ensure_latest_file(config):
  DATA_DIR.mkdir(parents=True, exist_ok=True)
  TMP_DIR.mkdir(parents=True, exist_ok=True)
  CLIPS_DIR.mkdir(parents=True, exist_ok=True)
  init_database()
  if not CLIP_INDEX_PATH.exists():
    CLIP_INDEX_PATH.write_text("{}", encoding="utf-8")
  rebuild_species_set()
  rebuild_species_counts()
  if not LATEST_PATH.exists():
    payload = build_payload(
      get_config_snapshot(config),
      status="idle",
      status_message="Waiting for BirdNET",
      predictions=[],
    )
    write_latest(payload)
  refresh_last_detection(config)


def write_latest(payload):
  payload = dict(payload)
  payload["timestamp"] = payload.get("timestamp") or now_iso()
  encoded = json.dumps(payload, ensure_ascii=True, indent=2)
  tmp_path = LATEST_PATH.with_suffix(".tmp")
  with write_lock:
    tmp_path.write_text(encoded, encoding="utf-8")
    tmp_path.replace(LATEST_PATH)


def build_payload(config, status, status_message, predictions):
  last = get_last_detection()
  icon_index = load_icon_index()
  if last:
    last = dict(last)
    last["times_heard"] = get_species_heard_count(last.get("species"))
    last["species_rank"] = get_species_rank(last.get("species"))
    last["icon_url"] = icon_url_for(last.get("species"), icon_index)
  top = predictions[0] if predictions else None
  species = top["species"] if top else "No detection"
  scientific_name = top.get("scientific_name", "") if top else ""
  confidence = top.get("confidence") if top else None
  return {
    "timestamp": now_iso(),
    "species": species,
    "scientific_name": scientific_name,
    "confidence": confidence,
    "status": status,
    "status_message": status_message,
    "stream_url": safe_stream_url(config.get("rtmp_url", "")),
    "clip_seconds": config.get("segment_seconds"),
    "model": "BirdNET",
    "times_heard": get_species_heard_count(species) if species != "No detection" else 0,
    "location": config.get("location", "Stream"),
    "latitude": config.get("latitude", -1),
    "longitude": config.get("longitude", -1),
    "week": config.get("week", -1),
    "top_predictions": predictions,
    "last_detection": last,
    "last_heard": last["timestamp"] if last else None,
    "icon_url": icon_url_for(species, icon_index),
    "log_revision": get_log_revision(),
    "species_count": get_species_count(),
    "species_rank": get_species_rank(species),
    "overlay_hold_seconds": config.get("overlay_hold_seconds", 60),
    "overlay_sticky": bool(config.get("overlay_sticky", False)),
  }


def safe_stream_url(url):
  if not url:
    return ""
  try:
    parts = urlsplit(url)
    netloc = parts.netloc
    if parts.username or parts.password:
      host = parts.hostname or ""
      if parts.port:
        host = f"{host}:{parts.port}"
      netloc = host
    sensitive_query_keys = {
      "password",
      "pass",
      "passwd",
      "pwd",
      "token",
      "api_key",
      "apikey",
      "auth",
      "authorization",
    }
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = []
    for key, value in query_pairs:
      lower_key = str(key).strip().lower()
      if (
        lower_key in sensitive_query_keys
        or "password" in lower_key
        or lower_key.endswith("_token")
        or lower_key.endswith("_key")
      ):
        redacted_pairs.append((key, "REDACTED"))
      else:
        redacted_pairs.append((key, value))
    query = urlencode(redacted_pairs, doseq=True)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
  except ValueError:
    return url


def get_last_detection():
  with last_detection_lock:
    if not last_detection:
      return None
    return deepcopy(last_detection)


def parse_timestamp(value):
  if not value:
    return None
  if not isinstance(value, str):
    return None
  try:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None


def invalidate_summary_cache():
  try:
    with db_connect() as connection:
      connection.execute("DELETE FROM summary_cache WHERE cache_key = 'log_summary'")
      connection.commit()
  except sqlite3.Error:
    return


def get_cached_summary():
  current_revision = get_log_revision()
  try:
    with db_connect() as connection:
      row = connection.execute(
        """
        SELECT payload_json
        FROM summary_cache
        WHERE cache_key = 'log_summary' AND log_revision = ?
        """,
        (current_revision,),
      ).fetchone()
  except sqlite3.Error:
    return None
  if not row:
    return None
  try:
    payload = json.loads(row["payload_json"])
  except (TypeError, json.JSONDecodeError):
    return None
  if isinstance(payload, dict):
    return payload
  return None


def set_cached_summary(payload):
  if not isinstance(payload, dict):
    return
  try:
    encoded = json.dumps(payload, ensure_ascii=True)
    with db_connect() as connection:
      connection.execute(
        """
        INSERT OR REPLACE INTO summary_cache
        (cache_key, log_revision, payload_json, updated_at)
        VALUES ('log_summary', ?, ?, ?)
        """,
        (get_log_revision(), encoded, now_iso()),
      )
      connection.commit()
  except sqlite3.Error:
    return


def derive_last_detection(entries, config):
  if not entries:
    return None
  icon_index = load_icon_index()
  latest = None
  latest_dt = None
  for entry in entries:
    stamp = entry.get("timestamp")
    dt = parse_timestamp(stamp)
    if dt is None:
      if latest_dt is None and stamp:
        if latest is None or stamp > latest:
          latest = stamp
      continue
    if latest_dt is None or dt > latest_dt:
      latest_dt = dt
      latest = stamp

  if not latest:
    return None

  grouped = [entry for entry in entries if entry.get("timestamp") == latest]
  if not grouped:
    return None

  def confidence_key(item):
    value = item.get("confidence")
    try:
      return float(value)
    except (TypeError, ValueError):
      return 0.0

  grouped.sort(key=confidence_key, reverse=True)
  top = grouped[0]
  predictions = []
  for entry in grouped[:3]:
    predictions.append({
      "species": entry.get("species", "Unknown"),
      "scientific_name": entry.get("scientific_name", ""),
      "confidence": entry.get("confidence"),
    })

  return {
    "timestamp": top.get("timestamp", now_iso()),
    "species": top.get("species", "Unknown"),
    "scientific_name": top.get("scientific_name", ""),
    "confidence": top.get("confidence"),
    "clip_seconds": config.get("segment_seconds"),
    "times_heard": get_species_heard_count(top.get("species", "Unknown")),
    "top_predictions": predictions,
    "location": top.get("location") or config.get("location", "Stream"),
    "icon_url": icon_url_for(top.get("species", "Unknown"), icon_index),
  }


def refresh_last_detection(config):
  entries = read_log(None)
  latest = derive_last_detection(entries, config)
  with last_detection_lock:
    global last_detection
    last_detection = latest
  payload = {}
  if LATEST_PATH.exists():
    try:
      payload = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      payload = {}
  payload["last_detection"] = latest
  payload["last_heard"] = latest["timestamp"] if latest else None
  payload["log_revision"] = get_log_revision()
  payload["species_count"] = get_species_count()
  if payload:
    write_latest(payload)


def record_last_detection(predictions, config):
  global last_detection
  if not predictions:
    return
  top = predictions[0]
  icon_index = load_icon_index()
  payload = {
    "timestamp": now_iso(),
    "species": top.get("species", "Unknown"),
    "scientific_name": top.get("scientific_name", ""),
    "confidence": top.get("confidence"),
    "clip_seconds": config.get("segment_seconds"),
    "top_predictions": predictions,
    "location": config.get("location", "Stream"),
    "icon_url": icon_url_for(top.get("species", "Unknown"), icon_index),
  }
  with last_detection_lock:
    last_detection = payload

  log_timestamp = payload["timestamp"]
  entries = []
  for prediction in predictions:
    entry = {
      "id": uuid.uuid4().hex,
      "timestamp": log_timestamp,
      "species": prediction.get("species", "Unknown"),
      "scientific_name": prediction.get("scientific_name", ""),
      "confidence": prediction.get("confidence"),
      "location": payload["location"],
    }
    entries.append(entry)
    confidence_label = format_confidence(entry.get("confidence"))
    message = f"Detected {entry['species']}"
    if confidence_label:
      message = f"{message} ({confidence_label})"
    log_event("detection", message, {
      "species": entry["species"],
      "scientific_name": entry["scientific_name"],
      "confidence": entry.get("confidence"),
    })
  append_log(entries)
  with last_detection_lock:
    if last_detection and last_detection.get("species") == payload.get("species"):
      last_detection["times_heard"] = get_species_heard_count(payload.get("species"))


def append_log(entries):
  if not entries:
    return
  if isinstance(entries, dict):
    entries = [entries]
  payload = []
  for entry in entries:
    if "id" not in entry:
      entry["id"] = entry_id(entry)
    payload.append(
      (
        entry["id"],
        str(entry.get("timestamp") or now_iso()),
        entry.get("species", "Unknown"),
        entry.get("scientific_name", ""),
        normalize_confidence(entry.get("confidence")),
        entry.get("location", ""),
        json.dumps(entry, ensure_ascii=True),
      )
    )
  with log_lock:
    with db_connect() as connection:
      connection.executemany(
        """
        INSERT OR REPLACE INTO detections
        (id, timestamp, species, scientific_name, confidence, location, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
      )
      connection.commit()
  update_species_set(entries)
  update_species_counts(entries)
  bump_log_revision()
  invalidate_summary_cache()


def entry_id(entry):
  if entry.get("id"):
    return str(entry["id"])
  base = f"{entry.get('timestamp', '')}|{entry.get('species', '')}|{entry.get('confidence', '')}"
  return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def read_log(limit=200):
  if limit is not None and limit <= 0:
    return []
  query = "SELECT id, raw_json FROM detections ORDER BY timestamp DESC, rowid DESC"
  params = []
  if limit is not None:
    query += " LIMIT ?"
    params.append(int(limit))
  try:
    with log_lock:
      with db_connect() as connection:
        rows = connection.execute(query, params).fetchall()
  except sqlite3.Error:
    return []
  output = []
  for row in reversed(rows):
    try:
      entry = json.loads(row["raw_json"])
    except (TypeError, json.JSONDecodeError):
      entry = {
        "id": row["id"],
        "timestamp": "",
      }
    entry["id"] = entry_id(entry)
    output.append(entry)
  return output


def build_activity_curve(days=10):
  try:
    days = int(days)
  except (TypeError, ValueError):
    days = 7
  days = max(1, min(30, days))
  now = datetime.now(timezone.utc)
  cutoff = now - timedelta(days=days)
  local_now = now.astimezone()
  today_local = local_now.date()
  bins_per_hour = 2
  total_bins = 24 * bins_per_hour
  counts = [0] * total_bins
  today_counts = [0] * total_bins
  cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")
  try:
    with log_lock:
      with db_connect() as connection:
        rows = connection.execute(
          "SELECT timestamp FROM detections WHERE timestamp >= ? ORDER BY timestamp ASC",
          (cutoff_iso,),
        ).fetchall()
  except sqlite3.Error:
    return {"points": counts, "today_points": today_counts, "days": days}
  for row in rows:
    dt = parse_timestamp(row["timestamp"])
    if not dt:
      continue
    local_dt = dt.astimezone()
    half_hour = local_dt.minute // 30
    bucket = (local_dt.hour * bins_per_hour) + half_hour
    counts[bucket] += 1
    if local_dt.date() == today_local:
      today_counts[bucket] += 1
  avg = [round(value / days, 2) for value in counts]
  current_hour = local_now.hour + (local_now.minute / 60.0) + (local_now.second / 3600.0)
  current_bin_index = int(current_hour * bins_per_hour)
  today_trimmed = []
  for idx, value in enumerate(today_counts):
    if idx > current_bin_index:
      today_trimmed.append(None)
    else:
      today_trimmed.append(value)
  return {
    "points": avg,
    "today_points": today_trimmed,
    "days": days,
  }


def smooth_series(values, window, wrap=True, ignore_none=False):
  if not values:
    return []
  size = max(1, int(window))
  if size <= 1:
    return list(values)
  half = size // 2
  total = len(values)
  smoothed = []
  for index in range(total):
    acc = 0.0
    count = 0
    for offset in range(-half, half + 1):
      target = index + offset
      if wrap:
        target = target % total
      elif target < 0 or target >= total:
        continue
      value = values[target]
      if ignore_none and value is None:
        continue
      acc += value
      count += 1
    if count == 0:
      smoothed.append(None if ignore_none else 0)
    else:
      smoothed.append(round(acc / count, 2))
  return smoothed


def build_log_csv(entries):
  output = io.StringIO()
  writer = csv.writer(output)
  writer.writerow(["timestamp", "species", "scientific_name", "confidence", "location", "id"])
  for entry in entries:
    writer.writerow([
      entry.get("timestamp", ""),
      entry.get("species", ""),
      entry.get("scientific_name", ""),
      entry.get("confidence", ""),
      entry.get("location", ""),
      entry.get("id", ""),
    ])
  return output.getvalue()


def count_pending_segments():
  try:
    if not TMP_DIR.exists():
      return 0
    return len(list(TMP_DIR.glob("segment_*.wav")))
  except OSError:
    return 0


def slugify(value):
  text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
  return text.strip("-") or "unknown"


def load_clip_index():
  if not CLIP_INDEX_PATH.exists():
    return {}
  try:
    data = json.loads(CLIP_INDEX_PATH.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}
  return data if isinstance(data, dict) else {}


def save_clip_index(index):
  CLIP_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=True, indent=2), encoding="utf-8")
  invalidate_summary_cache()


def normalize_species_key(value):
  return str(value or "").strip().lower()


def load_icon_index():
  try:
    with db_connect() as connection:
      rows = connection.execute("SELECT species_key, filename FROM species_icons").fetchall()
  except sqlite3.Error:
    return {}
  index = {}
  for row in rows:
    key = str(row["species_key"] or "").strip().lower()
    filename = str(row["filename"] or "").strip()
    if key and filename:
      index[key] = filename
  return index


def save_icon_index(index):
  payload = []
  stamp = now_iso()
  for key, filename in (index or {}).items():
    species_key = normalize_species_key(key)
    filename_text = str(filename or "").strip()
    if not species_key or not filename_text:
      continue
    payload.append((species_key, str(key or ""), filename_text, stamp))
  try:
    with db_connect() as connection:
      connection.execute("DELETE FROM species_icons")
      if payload:
        connection.executemany(
          """
          INSERT OR REPLACE INTO species_icons
          (species_key, species_name, filename, updated_at)
          VALUES (?, ?, ?, ?)
          """,
          payload,
        )
      connection.commit()
  except sqlite3.Error:
    return


def icon_url_for(species, icon_index=None):
  key = normalize_species_key(species)
  if not key:
    return ""
  if icon_index is None:
    icon_index = load_icon_index()
  filename = icon_index.get(key)
  if not filename:
    return ""
  if not (ICONS_DIR / filename).exists():
    return ""
  return f"/data/icons/{filename}"


def save_species_icon(species, payload):
  species_name = str(species or "").strip()
  if not species_name:
    return "", "Species is required"
  if not payload:
    return "", "Icon file missing"
  if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
    return "", "Icon must be a PNG file"
  ICONS_DIR.mkdir(parents=True, exist_ok=True)
  filename = f"{slugify(species_name)}.png"
  try:
    (ICONS_DIR / filename).write_bytes(payload)
  except OSError:
    return "", "Unable to save icon"
  try:
    with db_connect() as connection:
      connection.execute(
        """
        INSERT OR REPLACE INTO species_icons
        (species_key, species_name, filename, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (normalize_species_key(species_name), species_name, filename, now_iso()),
      )
      connection.commit()
  except sqlite3.Error:
    return "", "Unable to save icon mapping"
  return f"/data/icons/{filename}", ""


def remove_species_icon(species):
  species_name = str(species or "").strip()
  if not species_name:
    return False
  key = normalize_species_key(species_name)
  filename = ""
  try:
    with db_connect() as connection:
      row = connection.execute(
        "SELECT filename FROM species_icons WHERE species_key = ?",
        (key,),
      ).fetchone()
      if row:
        filename = str(row["filename"] or "").strip()
      connection.execute("DELETE FROM species_icons WHERE species_key = ?", (key,))
      connection.commit()
  except sqlite3.Error:
    return False
  if filename:
    try:
      (ICONS_DIR / filename).unlink(missing_ok=True)
    except OSError:
      pass
  return bool(filename)


def compute_clip_score(confidence_value, snr_db):
  try:
    confidence = float(confidence_value)
  except (TypeError, ValueError):
    confidence = -1.0
  try:
    snr_value = float(snr_db)
  except (TypeError, ValueError):
    snr_value = 0.0
  return (confidence * 100.0) + snr_value


def compute_snr_db(path):
  try:
    with wave.open(str(path), "rb") as wav:
      channels = wav.getnchannels()
      sample_width = wav.getsampwidth()
      rate = wav.getframerate()
      if rate <= 0:
        return None
      window_frames = max(1, int(rate * 0.2))
      rms_values = []
      while True:
        frames = wav.readframes(window_frames)
        if not frames:
          break
        if channels > 1:
          frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
        rms = audioop.rms(frames, sample_width)
        rms_values.append(rms)
  except (OSError, wave.Error):
    return None
  if not rms_values:
    return None
  rms_values.sort()
  noise_count = max(1, int(len(rms_values) * 0.1))
  noise_floor = sum(rms_values[:noise_count]) / noise_count
  signal_level = sum(rms_values) / len(rms_values)
  if noise_floor <= 0 or signal_level <= 0:
    return None
  snr = 20.0 * math.log10(signal_level / noise_floor)
  return round(snr, 2)


def update_best_clips(segment_path, predictions):
  if not predictions:
    return
  CLIPS_DIR.mkdir(parents=True, exist_ok=True)
  index = load_clip_index()
  updated = False
  for prediction in predictions:
    species = prediction.get("species", "Unknown") or "Unknown"
    confidence = prediction.get("confidence")
    normalized_confidence = normalize_confidence(confidence)
    confidence_value = normalized_confidence if normalized_confidence is not None else -1.0
    snr_db = compute_snr_db(segment_path)
    score = compute_clip_score(confidence_value, snr_db)
    entry = index.get(species, {})
    existing_normalized = normalize_confidence(entry.get("confidence"))
    existing_conf = existing_normalized if existing_normalized is not None else -1.0
    existing_snr = entry.get("snr_db")
    try:
      existing_score = float(entry.get("score"))
    except (TypeError, ValueError):
      existing_score = compute_clip_score(existing_conf, existing_snr)
    if confidence_value + 0.02 < existing_conf:
      continue
    if score <= existing_score:
      continue
    filename = f"{slugify(species)}.wav"
    target = CLIPS_DIR / filename
    try:
      shutil.copy2(segment_path, target)
    except OSError:
      continue
    index[species] = {
      "species": species,
      "scientific_name": prediction.get("scientific_name", ""),
      "confidence": confidence,
      "snr_db": snr_db,
      "score": round(score, 2),
      "timestamp": now_iso(),
      "filename": filename,
    }
    updated = True
  if updated:
    save_clip_index(index)


def summarize_log():
  cached = get_cached_summary()
  if cached is not None:
    return cached
  entries_all = read_log(None)
  if not entries_all:
    payload = {"entries": [], "species_count": 0, "total_detections": 0, "log_revision": get_log_revision()}
    set_cached_summary(payload)
    return payload
  days = 30
  local_now = datetime.now(timezone.utc).astimezone()
  start_date = local_now.date() - timedelta(days=days - 1)
  date_index = {start_date + timedelta(days=idx): idx for idx in range(days)}
  icon_index = load_icon_index()
  summary = {}
  total = len(entries_all)
  for entry in entries_all:
    species = entry.get("species", "Unknown") or "Unknown"
    item = summary.get(species)
    dt = parse_timestamp(entry.get("timestamp"))
    daily_index = None
    if dt:
      local_dt = dt.astimezone()
      daily_index = date_index.get(local_dt.date())
    current_time = dt.timestamp() if dt else None
    current_raw = entry.get("timestamp", "") or ""
    current_conf = normalize_confidence(entry.get("confidence")) or -1.0

    if not item:
      summary[species] = {
        "count": 1,
        "latest_entry": entry,
        "latest_time": current_time,
        "latest_raw": current_raw,
        "latest_conf": current_conf,
        "daily_counts": [0] * days,
      }
      if daily_index is not None:
        summary[species]["daily_counts"][daily_index] += 1
      continue

    item["count"] += 1
    if daily_index is not None:
      item["daily_counts"][daily_index] += 1
    latest_time = item["latest_time"]
    replace = False
    if current_time is not None and (latest_time is None or current_time > latest_time):
      replace = True
    elif current_time is None and latest_time is None:
      if current_raw > item["latest_raw"]:
        replace = True
    elif current_time is not None and latest_time is not None and current_time == latest_time:
      if current_conf > item["latest_conf"]:
        replace = True

    if replace:
      item["latest_entry"] = entry
      item["latest_time"] = current_time
      item["latest_raw"] = current_raw
      item["latest_conf"] = current_conf

  clip_index = load_clip_index()
  entries = []
  for species, item in summary.items():
    latest_entry = dict(item["latest_entry"])
    latest_entry["species"] = latest_entry.get("species", species) or species
    latest_entry["count"] = item["count"]
    latest_entry["id"] = entry_id(latest_entry)
    latest_entry["daily_counts"] = item.get("daily_counts", [])
    latest_entry["icon_url"] = icon_url_for(latest_entry.get("species"), icon_index)
    clip = clip_index.get(species)
    if clip and clip.get("filename"):
      latest_entry["clip_url"] = f"/api/clip?species={quote(species)}"
      latest_entry["clip_confidence"] = clip.get("confidence")
    entries.append(latest_entry)

  with species_count_lock:
    global species_counts
    species_counts = {species: item["count"] for species, item in summary.items()}
  with species_lock:
    global species_set
    species_set = set(summary.keys())

  payload = {
    "entries": entries,
    "species_count": len(entries),
    "total_detections": total,
    "log_revision": get_log_revision(),
  }
  set_cached_summary(payload)
  return payload


def delete_log_entry(entry_key):
  if not entry_key:
    return False
  with log_lock:
    try:
      with db_connect() as connection:
        cursor = connection.execute("DELETE FROM detections WHERE id = ?", (str(entry_key),))
        removed = cursor.rowcount > 0
        connection.commit()
    except sqlite3.Error:
      return False
  if removed:
    rebuild_species_set()
    rebuild_species_counts()
    bump_log_revision()
    invalidate_summary_cache()
  return removed


def append_event(entries):
  if not entries:
    return
  if isinstance(entries, dict):
    entries = [entries]
  payload = []
  for entry in entries:
    if "id" not in entry:
      entry["id"] = event_id(entry)
    payload.append(
      (
        entry["id"],
        str(entry.get("timestamp") or now_iso()),
        entry.get("type", ""),
        entry.get("message", ""),
        json.dumps(entry, ensure_ascii=True),
      )
    )
  with event_lock:
    with db_connect() as connection:
      connection.executemany(
        """
        INSERT OR REPLACE INTO events
        (id, timestamp, type, message, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        payload,
      )
      connection.commit()


def event_id(entry):
  if entry.get("id"):
    return str(entry["id"])
  base = f"{entry.get('timestamp', '')}|{entry.get('type', '')}|{entry.get('message', '')}"
  return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def read_events(limit=200):
  if limit is not None and limit <= 0:
    return []
  query = "SELECT id, raw_json FROM events ORDER BY timestamp DESC, rowid DESC"
  params = []
  if limit is not None:
    query += " LIMIT ?"
    params.append(int(limit))
  try:
    with event_lock:
      with db_connect() as connection:
        rows = connection.execute(query, params).fetchall()
  except sqlite3.Error:
    return []
  output = []
  for row in reversed(rows):
    try:
      entry = json.loads(row["raw_json"])
    except (TypeError, json.JSONDecodeError):
      entry = {"id": row["id"], "timestamp": "", "type": "", "message": ""}
    entry["id"] = event_id(entry)
    output.append(entry)
  return output


def log_event(event_type, message, extra=None):
  entry = {
    "id": uuid.uuid4().hex,
    "timestamp": now_iso(),
    "type": event_type,
    "message": message,
  }
  if extra:
    entry.update(extra)
  append_event(entry)


def format_confidence(value):
  if value is None:
    return ""
  try:
    numeric = float(value)
  except (TypeError, ValueError):
    return ""
  if numeric > 1:
    numeric = numeric / 100.0
  numeric = max(0.0, min(1.0, numeric))
  return f"{numeric * 100:.0f}%"


def normalize_confidence(value):
  if value is None:
    return None
  try:
    if isinstance(value, str):
      value = value.strip().replace("%", "")
    numeric = float(value)
  except (TypeError, ValueError):
    return None
  if numeric > 1:
    numeric = numeric / 100.0
  return max(0.0, min(1.0, numeric))


def normalize_timestamp(value):
  if not value:
    return now_iso()
  if not isinstance(value, str):
    return now_iso()
  text = value.strip()
  if not text:
    return now_iso()
  try:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
  except ValueError:
    return now_iso()
  return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def add_manual_log_entry(data, config):
  species = str(data.get("species", "")).strip()
  if not species:
    return None, "Species is required"
  scientific_name = str(data.get("scientific_name", "")).strip()
  confidence = normalize_confidence(data.get("confidence"))
  timestamp = normalize_timestamp(data.get("timestamp"))
  entry = {
    "id": uuid.uuid4().hex,
    "timestamp": timestamp,
    "species": species,
    "scientific_name": scientific_name,
    "confidence": confidence,
    "location": config.get("location", "Stream"),
  }
  append_log(entry)
  log_event("manual", f"Manual entry {species}", {
    "species": species,
    "scientific_name": scientific_name,
    "confidence": confidence,
  })
  return entry, None


def list_audio_inputs():
  ffmpeg_path = resolve_ffmpeg_path()
  if not ffmpeg_path:
    return [], "ffmpeg not found"
  command = [ffmpeg_path, "-f", "avfoundation", "-list_devices", "true", "-i", ""]
  result = subprocess.run(command, capture_output=True, text=True, check=False)
  lines = (result.stderr or "").splitlines()
  devices = []
  in_audio = False
  for line in lines:
    if "AVFoundation audio devices" in line:
      in_audio = True
      continue
    if "AVFoundation video devices" in line:
      in_audio = False
      continue
    if not in_audio:
      continue
    match = re.search(r"\[(\d+)\]\s(.+)$", line)
    if not match:
      continue
    index, name = match.groups()
    devices.append({"id": index, "name": name.strip()})
  return devices, None


def is_file_ready(path):
  try:
    age = time.time() - path.stat().st_mtime
  except OSError:
    return False
  return age > 0.4


def latest_segment_mtime():
  try:
    files = list(TMP_DIR.glob("segment_*.wav"))
  except OSError:
    return None
  if not files:
    return None
  try:
    return max(path.stat().st_mtime for path in files)
  except OSError:
    return None


def resolve_ffmpeg_path():
  resolved = shutil.which("ffmpeg")
  if resolved:
    return resolved
  for candidate in FALLBACK_FFMPEG_PATHS:
    if Path(candidate).exists():
      return candidate
  return None


def _list_ffmpeg_processes():
  processes = []
  try:
    result = subprocess.run(
      ["pgrep", "-fl", "ffmpeg"],
      check=False,
      capture_output=True,
      text=True,
    )
    lines = result.stdout.splitlines()
  except FileNotFoundError:
    result = subprocess.run(
      ["ps", "-ax", "-o", "pid=,command="],
      check=False,
      capture_output=True,
      text=True,
    )
    lines = result.stdout.splitlines()
  for line in lines:
    parts = line.strip().split(None, 1)
    if len(parts) != 2:
      continue
    pid_text, command = parts
    if "ffmpeg" not in command:
      continue
    try:
      pid = int(pid_text)
    except ValueError:
      continue
    processes.append((pid, command))
  return processes


def _pid_exists(pid):
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True


def get_current_capture_pid():
  with capture_pid_lock:
    return current_capture_pid


def set_current_capture_pid(pid):
  with capture_pid_lock:
    global current_capture_pid
    current_capture_pid = pid


def cleanup_capture_processes(reason, allowed_pids=None):
  marker = str(TMP_DIR / "segment_")
  candidates = []
  for pid, command in _list_ffmpeg_processes():
    if marker in command:
      if allowed_pids and pid in allowed_pids:
        continue
      candidates.append(pid)
  if not candidates:
    return 0
  for pid in candidates:
    try:
      os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
      continue
    except PermissionError:
      continue
  deadline = time.time() + 2.0
  remaining = candidates
  while remaining and time.time() < deadline:
    remaining = [pid for pid in remaining if _pid_exists(pid)]
    if remaining:
      time.sleep(0.1)
  for pid in remaining:
    try:
      os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
      continue
    except PermissionError:
      continue
  log_event("server", f"Cleaned {len(candidates)} orphan capture process(es) ({reason})")
  return len(candidates)


def clear_tmp_segments(reason):
  removed = 0
  for path in TMP_DIR.glob("segment_*.wav"):
    try:
      path.unlink()
      removed += 1
    except OSError:
      continue
  if removed:
    log_event("server", f"Cleared {removed} pending segments ({reason})")
  return removed


def analyze_audio_activity(path, threshold_db, min_active_seconds):
  if threshold_db is None:
    return True, None
  if min_active_seconds <= 0:
    return True, None
  try:
    with wave.open(str(path), "rb") as handle:
      sample_rate = handle.getframerate()
      sample_width = handle.getsampwidth()
      channels = handle.getnchannels()
      total_frames = handle.getnframes()
      if total_frames <= 0:
        return False, None

      chunk_frames = max(1, int(sample_rate * 0.05))
      active_frames = 0
      max_db = -120.0
      max_amp = float(1 << (8 * sample_width - 1))

      while True:
        chunk = handle.readframes(chunk_frames)
        if not chunk:
          break
        rms = audioop.rms(chunk, sample_width)
        if rms <= 0 or max_amp <= 0:
          db = -120.0
        else:
          db = 20.0 * math.log10(rms / max_amp)
        if db > max_db:
          max_db = db
        if db >= threshold_db:
          frames_in_chunk = len(chunk) // (sample_width * channels)
          active_frames += frames_in_chunk
          if (active_frames / sample_rate) >= min_active_seconds:
            return True, max_db

      return False, max_db
  except (wave.Error, OSError, EOFError):
    return True, None


def normalize_header(value):
  return value.strip().lower().replace("_", " ")


def extract_predictions(csv_path):
  predictions = []
  with csv_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
      return predictions
    header_map = {normalize_header(name): name for name in reader.fieldnames}

    def pick(*options):
      for option in options:
        key = normalize_header(option)
        if key in header_map:
          return header_map[key]
      return None

    common_key = pick("common name", "common_name", "species")
    scientific_key = pick("scientific name", "scientific_name")
    confidence_key = pick("confidence", "score", "probability")

    for row in reader:
      if not row:
        continue
      try:
        confidence = float(row.get(confidence_key, ""))
      except (TypeError, ValueError):
        continue
      predictions.append({
        "species": (row.get(common_key) or "").strip() or "Unknown",
        "scientific_name": (row.get(scientific_key) or "").strip(),
        "confidence": confidence,
      })

  predictions.sort(key=lambda item: item.get("confidence", 0), reverse=True)
  return predictions


def build_birdnet_command(template, input_path, output_path, min_confidence, segment_seconds, latitude, longitude, week):
  if "{input}" not in template or "{output}" not in template:
    raise ValueError("BIRDNET_TEMPLATE must include {input} and {output}.")
  command = template.format(
    input=shlex.quote(str(input_path)),
    output=shlex.quote(str(output_path)),
    min_conf=min_confidence,
    segment=segment_seconds,
    segment_seconds=segment_seconds,
    lat=latitude,
    latitude=latitude,
    lon=longitude,
    longitude=longitude,
    week=week,
  )
  return shlex.split(command)


def resolve_output_paths(output_target, input_path):
  output_target = Path(output_target)
  if output_target.suffix.lower() == ".csv":
    return output_target, output_target
  output_target.mkdir(parents=True, exist_ok=True)
  run_dir = output_target / f"birdnet_{uuid.uuid4().hex}"
  run_dir.mkdir(parents=True, exist_ok=True)
  expected = run_dir / f"{input_path.stem}.BirdNET.results.csv"
  return run_dir, expected


def run_birdnet(template, workdir, input_path, output_target, min_confidence, segment_seconds, latitude, longitude, week):
  try:
    output_arg, output_file = resolve_output_paths(output_target, input_path)
    command = build_birdnet_command(
      template,
      input_path,
      output_arg,
      min_confidence,
      segment_seconds,
      latitude,
      longitude,
      week,
    )
  except ValueError as error:
    return [], str(error)

  try:
    result = subprocess.run(
      command,
      cwd=workdir or None,
      capture_output=True,
      text=True,
      check=False,
      timeout=60,
    )
  except FileNotFoundError:
    return [], "BirdNET command not found. Set BIRDNET_TEMPLATE."
  except subprocess.TimeoutExpired:
    return [], "BirdNET timed out."

  if result.returncode != 0:
    message = result.stderr.strip() or "BirdNET failed."
    return [], message

  if not output_file.exists():
    return [], None

  try:
    predictions = extract_predictions(output_file)
  except (OSError, csv.Error):
    return [], "Unable to read BirdNET output."
  finally:
    try:
      output_file.unlink(missing_ok=True)
    except OSError:
      pass
    if output_file.parent != Path(output_target):
      try:
        shutil.rmtree(output_file.parent, ignore_errors=True)
      except OSError:
        pass

  return predictions, None


def build_ffmpeg_command(config, ffmpeg_path):
  input_mode = config.get("input_mode", "stream")
  if input_mode == "avfoundation":
    device = str(config.get("input_device", "")).strip()
    if not device:
      return None, "Audio input not set"
    input_args = ["-f", "avfoundation", "-i", f":{device}"]
  else:
    stream_url = config.get("rtmp_url", "")
    if not stream_url:
      return None, "Stream URL not set"
    scheme = ""
    try:
      scheme = urlsplit(stream_url).scheme.lower()
    except ValueError:
      scheme = ""
    input_args = []
    if scheme == "rtsp":
      input_args.extend(["-rtsp_transport", "tcp"])
    input_args = [
      *input_args,
      "-i",
      stream_url,
      "-map",
      "0:a:0",
    ]

  command = [
    ffmpeg_path,
    "-loglevel",
    "warning",
    "-hide_banner",
    "-y",
    *input_args,
    "-vn",
    "-ac",
    "1",
    "-ar",
    "48000",
    "-f",
    "segment",
    "-segment_time",
    str(config.get("segment_seconds", 3)),
    "-reset_timestamps",
    "1",
    str(TMP_DIR / "segment_%06d.wav"),
  ]
  return command, None


def analyze_segment(path, config):
  global analysis_last_error
  snapshot = get_config_snapshot(config)

  if not snapshot.get("birdnet_template"):
    status_message = "BIRDNET_TEMPLATE not set"
    payload = build_payload(
      snapshot,
      status="idle",
      status_message=status_message,
      predictions=[],
    )
    with analysis_error_lock:
      if status_message != analysis_last_error:
        log_event("error", status_message)
        analysis_last_error = status_message
    write_latest(payload)
    path.unlink(missing_ok=True)
    return

  log_event("analysis", "Analyzing segment")
  effective_week = snapshot.get("week", -1)
  if snapshot.get("auto_week"):
    effective_week = current_week()

  predictions, error = run_birdnet(
    snapshot["birdnet_template"],
    snapshot.get("birdnet_workdir"),
    path,
    TMP_DIR,
    ANALYSIS_MIN_CONF,
    snapshot["segment_seconds"],
    snapshot["latitude"],
    snapshot["longitude"],
    effective_week,
  )

  if error:
    payload = build_payload(
      snapshot,
      status="error",
      status_message=error,
      predictions=[],
    )
    with analysis_error_lock:
      if error != analysis_last_error:
        log_event("error", error)
        analysis_last_error = error
  else:
    report_threshold = snapshot.get("min_confidence", 0.0)
    above = [item for item in predictions if item.get("confidence", 0) >= report_threshold]
    below = [item for item in predictions if item.get("confidence", 0) < report_threshold]
    above = above[:3]
    below = below[:3]
    status_message = "Detected" if above else "No detections"
    if above:
      record_last_detection(above, snapshot)
      update_best_clips(path, above)
    else:
      threshold_label = format_confidence(report_threshold)
      if threshold_label:
        log_event("analysis", f"No detections above {threshold_label}")
      else:
        log_event("analysis", "No detections above threshold")
    payload = build_payload(
      dict(snapshot, week=effective_week),
      status="listening",
      status_message=status_message,
      predictions=above,
    )
    if below:
      summaries = []
      for item in below:
        species = item.get("species", "Unknown")
        confidence_label = format_confidence(item.get("confidence"))
        if confidence_label:
          summaries.append(f"{species} ({confidence_label})")
        else:
          summaries.append(species)
      log_event("detection", "Below threshold: " + ", ".join(summaries), {"below_threshold": True})
    with analysis_error_lock:
      analysis_last_error = None

  write_latest(payload)
  path.unlink(missing_ok=True)


def process_loop(config, stop_event):
  gate_queue = queue.Queue()
  analysis_queue = queue.Queue()
  gate_paths = set()
  analysis_paths = set()
  paths_lock = threading.Lock()
  last_status_report = 0.0
  last_status_payload = None
  last_worker_check = 0.0
  drop_log_state = {"backlog": 0.0, "stale": 0.0}

  def gate_worker(worker_id):
    while not stop_event.is_set():
      try:
        path = gate_queue.get(timeout=0.5)
      except queue.Empty:
        continue
      try:
        with paths_lock:
          gate_paths.discard(path)
        if not path.exists():
          gate_queue.task_done()
          continue
        if not is_file_ready(path):
          gate_queue.task_done()
          continue

        snapshot = get_config_snapshot(config)
        try:
          threshold_db = float(snapshot.get("silence_threshold_db", -45.0))
        except (TypeError, ValueError):
          threshold_db = -45.0
        try:
          min_active_seconds = float(snapshot.get("silence_min_seconds", 0.2))
        except (TypeError, ValueError):
          min_active_seconds = 0.2
        is_active, peak_db = analyze_audio_activity(path, threshold_db, min_active_seconds)
        if not is_active:
          threshold_label = f"{threshold_db:.1f} dBFS"
          if peak_db is None:
            log_event("analysis", f"Skipped silent segment (below {threshold_label})")
          else:
            log_event("analysis", f"Skipped silent segment (peak {peak_db:.1f} dBFS)")
          path.unlink(missing_ok=True)
          gate_queue.task_done()
          continue

        backlog = analysis_queue.qsize()
        with paths_lock:
          backlog += len(analysis_paths)
        if backlog >= MAX_ANALYSIS_BACKLOG:
          now = time.time()
          if now - drop_log_state["backlog"] > 10:
            log_event("analysis", f"Dropped active segment due to backlog ({backlog})")
            drop_log_state["backlog"] = now
          path.unlink(missing_ok=True)
          gate_queue.task_done()
          continue

        with paths_lock:
          analysis_paths.add(path)
        analysis_queue.put(path)
        gate_queue.task_done()
      except Exception as error:
        log_event("error", f"Gate worker {worker_id} error: {error}")
        with paths_lock:
          gate_paths.discard(path)
        gate_queue.task_done()

  def start_gate_worker(worker_id):
    thread = threading.Thread(target=gate_worker, args=(worker_id,), daemon=True)
    thread.start()
    return thread

  gate_threads = [start_gate_worker(index + 1) for index in range(GATE_WORKERS)]

  def analysis_worker(worker_id):
    while not stop_event.is_set():
      try:
        path = analysis_queue.get(timeout=0.5)
      except queue.Empty:
        continue
      try:
        log_event("analysis", f"Worker {worker_id} analyzing segment")
        if not path.exists():
          with paths_lock:
            analysis_paths.discard(path)
          analysis_queue.task_done()
          continue
        analyze_segment(path, config)
      except Exception as error:
        log_event("error", f"Analysis worker {worker_id} error: {error}")
      finally:
        with paths_lock:
          analysis_paths.discard(path)
        analysis_queue.task_done()

  def start_analysis_worker(worker_id):
    thread = threading.Thread(target=analysis_worker, args=(worker_id,), daemon=True)
    thread.start()
    return thread

  analysis_threads = [start_analysis_worker(index + 1) for index in range(ANALYSIS_WORKERS)]

  while not stop_event.is_set():
    now = time.time()
    files_with_time = []
    for path in TMP_DIR.glob("segment_*.wav"):
      try:
        mtime = path.stat().st_mtime
      except OSError:
        continue
      if now - mtime > MAX_SEGMENT_AGE_SECONDS:
        with paths_lock:
          if path in gate_paths or path in analysis_paths:
            continue
        path.unlink(missing_ok=True)
        if now - drop_log_state["stale"] > 10:
          log_event("analysis", f"Dropped stale segment (> {int(MAX_SEGMENT_AGE_SECONDS)}s old)")
          drop_log_state["stale"] = now
        continue
      files_with_time.append((mtime, path))
    files_with_time.sort(key=lambda item: item[0])
    files = [path for _, path in files_with_time]

    if now - last_worker_check >= 5:
      for index, thread in enumerate(list(gate_threads)):
        if not thread.is_alive():
          log_event("error", f"Gate worker {index + 1} stopped, restarting")
          gate_threads[index] = start_gate_worker(index + 1)
      for index, thread in enumerate(list(analysis_threads)):
        if not thread.is_alive():
          log_event("error", f"Analysis worker {index + 1} stopped, restarting")
          analysis_threads[index] = start_analysis_worker(index + 1)
      last_worker_check = now
    if len(files) > MAX_QUEUE_SEGMENTS:
      drop_count = len(files) - MAX_QUEUE_SEGMENTS
      dropped = 0
      for path in files:
        if dropped >= drop_count:
          break
        with paths_lock:
          if path in gate_paths or path in analysis_paths:
            continue
          gate_paths.discard(path)
          analysis_paths.discard(path)
        path.unlink(missing_ok=True)
        dropped += 1
      if dropped:
        log_event("analysis", f"Dropped {dropped} queued segments to cap queue at {MAX_QUEUE_SEGMENTS}")
      files = [path for path in files if path.exists()]

    gate_size = gate_queue.qsize()
    analysis_size = analysis_queue.qsize()
    with paths_lock:
      active_count = len(analysis_paths)
      gate_pending = len(gate_paths)
    tmp_count = len(files)
    oldest_age = 0
    if files_with_time:
      oldest_age = max(0, now - files_with_time[0][0])
    status_payload = (tmp_count, gate_pending, gate_size, analysis_size, active_count, int(oldest_age))
    if status_payload != last_status_payload:
      if now - last_status_report >= 5:
        log_event(
          "analysis",
          f"Status: tmp {tmp_count}, gate {gate_pending + gate_size}, analysis {analysis_size}, active {active_count}, oldest {int(oldest_age)}s",
        )
        last_status_report = now
        last_status_payload = status_payload
    if not files:
      time.sleep(0.2)
    else:
      for path in files:
        if stop_event.is_set():
          break
        if not is_file_ready(path):
          continue
        with paths_lock:
          if path in gate_paths or path in analysis_paths:
            continue
          gate_paths.add(path)
        gate_queue.put(path)

  return


def capture_loop(config, stop_event):
  last_status = None
  last_restart_log = 0.0
  stall_count = 0
  cleanup_capture_processes("startup")

  def capture_watchdog():
    while not stop_event.is_set():
      pid = get_current_capture_pid()
      if pid:
        cleanup_capture_processes("watchdog", {pid})
      time.sleep(5)

  threading.Thread(target=capture_watchdog, daemon=True).start()
  while not stop_event.is_set():
    snapshot = get_config_snapshot(config)
    ffmpeg_path = resolve_ffmpeg_path()
    if not ffmpeg_path:
      status_message = "ffmpeg not found"
      payload = build_payload(
        snapshot,
        status="error",
        status_message=status_message,
        predictions=[],
      )
      if status_message != last_status:
        log_event("error", status_message)
        last_status = status_message
      write_latest(payload)
      return

    command, error = build_ffmpeg_command(snapshot, ffmpeg_path)
    if error:
      payload = build_payload(
        snapshot,
        status="idle",
        status_message=error,
        predictions=[],
      )
      if error != last_status:
        log_event("server", error)
        last_status = error
      write_latest(payload)
      if restart_capture.is_set():
        restart_capture.clear()
      time.sleep(1)
      continue

    payload = build_payload(
      snapshot,
      status="listening",
      status_message="Listening",
      predictions=[],
    )
    if payload["status_message"] != last_status:
      log_event("server", payload["status_message"])
      last_status = payload["status_message"]
    write_latest(payload)

    process = subprocess.Popen(command)
    set_current_capture_pid(process.pid)
    restart_requested = False
    last_segment_time = latest_segment_mtime() or time.time()
    try:
      segment_seconds = float(snapshot.get("segment_seconds", 3))
    except (TypeError, ValueError):
      segment_seconds = 3
    stall_timeout = max(10.0, segment_seconds * 5.0)
    while process.poll() is None and not stop_event.is_set():
      if restart_capture.is_set():
        restart_requested = True
        restart_capture.clear()
        process.terminate()
        break
      latest = latest_segment_mtime()
      if latest and latest > last_segment_time:
        last_segment_time = latest
        stall_count = 0
      if time.time() - last_segment_time > stall_timeout:
        now = time.time()
        if now - last_restart_log > 15:
          log_event("server", f"No new audio segments for {int(stall_timeout)}s, restarting capture")
          last_restart_log = now
        clear_tmp_segments("stall")
        stall_count += 1
        if stall_count >= 3:
          log_event("server", "Repeated stalls detected, forcing capture reset")
          cleanup_capture_processes("stall reset")
          stall_count = 0
        restart_requested = True
        process.terminate()
        break
      time.sleep(0.5)

    if process.poll() is None:
      try:
        process.wait(timeout=2)
      except subprocess.TimeoutExpired:
        process.kill()
        try:
          process.wait(timeout=2)
        except subprocess.TimeoutExpired:
          pass
    set_current_capture_pid(None)

    if stop_event.is_set():
      process.terminate()
      break

    if restart_requested:
      continue

    payload = build_payload(
      snapshot,
      status="idle",
      status_message="Input disconnected, retrying",
      predictions=[],
    )
    if payload["status_message"] != last_status:
      log_event("server", payload["status_message"])
      last_status = payload["status_message"]
    write_latest(payload)
    time.sleep(2)


def make_handler(config):
  class NoCacheHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
      super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
      path = self.path.split("?", 1)[0]
      if self.path.endswith(".json"):
        self.send_header("Cache-Control", "no-store, max-age=0")
      if path.startswith("/api/weather/settings"):
        self.send_header("Access-Control-Allow-Origin", "*")
      super().end_headers()

    def _send_json(self, status_code, payload):
      body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
      self.send_response(status_code)
      self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Cache-Control", "no-store, max-age=0")
      self.end_headers()
      self.wfile.write(body)

    def _send_csv(self, filename, text):
      body = text.encode("utf-8")
      self.send_response(200)
      self.send_header("Content-Type", "text/csv; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
      self.send_header("Cache-Control", "no-store, max-age=0")
      self.end_headers()
      self.wfile.write(body)

    def _send_file(self, path, content_type, download_name=None):
      try:
        body = Path(path).read_bytes()
      except OSError:
        self.send_error(404, "File not found")
        return
      self.send_response(200)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(body)))
      if download_name:
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
      self.send_header("Cache-Control", "no-store, max-age=0")
      self.end_headers()
      self.wfile.write(body)

    def _read_json(self):
      try:
        length = int(self.headers.get("Content-Length", "0"))
      except ValueError:
        return None
      if length <= 0:
        return None
      try:
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    def _auth_settings(self):
      with config_lock:
        enabled = bool(config.get("settings_auth_enabled"))
        user = str(config.get("settings_auth_user") or "admin").strip() or "admin"
        password_hash = str(config.get("settings_auth_password_hash") or "").strip()
      if not enabled or not password_hash:
        return {"enabled": False, "user": user, "password_hash": password_hash}
      return {"enabled": True, "user": user, "password_hash": password_hash}

    def _requires_auth(self, path):
      if path.startswith("/api/status") or path.startswith("/api/weather/settings"):
        return False
      if path.startswith("/weather/") or path in {"/weather", "/weather/"}:
        return False
      if path.startswith("/data/icons/"):
        return False
      if path in {"/", "/index.html", "/app.js", "/styles.css"}:
        return False
      if path in {"/settings", "/settings/", "/settings.html", "/settings.js", "/settings.css"}:
        return True
      if path.startswith("/api/"):
        return True
      return False

    def _is_authorized(self):
      settings = self._auth_settings()
      if not settings.get("enabled"):
        return True
      header = self.headers.get("Authorization", "")
      if not header.startswith("Basic "):
        return False
      token = header.split(" ", 1)[1].strip()
      try:
        decoded = base64.b64decode(token).decode("utf-8")
      except (ValueError, UnicodeDecodeError):
        return False
      if ":" not in decoded:
        return False
      user, password = decoded.split(":", 1)
      if not hmac.compare_digest(str(user), settings["user"]):
        return False
      return verify_password(password, settings["password_hash"])

    def _require_auth(self):
      self.send_response(401)
      self.send_header("WWW-Authenticate", 'Basic realm="Feather Front Settings"')
      self.send_header("Cache-Control", "no-store, max-age=0")
      self.send_header("Content-Length", "0")
      self.end_headers()

    def do_GET(self):
      path = self.path.split("?", 1)[0]
      if self._requires_auth(path) and not self._is_authorized():
        return self._require_auth()
      if path in {"/settings", "/settings/"}:
        self.path = "/settings.html"
        return super().do_GET()
      if path in {"/weather", "/weather/", "/weather/index.html"}:
        return self._send_file(WEATHER_ROOT / "index.html", "text/html; charset=utf-8")
      if path == "/weather/app.js":
        return self._send_file(WEATHER_ROOT / "app.js", "application/javascript; charset=utf-8")
      if path == "/weather/styles.css":
        return self._send_file(WEATHER_ROOT / "styles.css", "text/css; charset=utf-8")
      if path.startswith("/api/status"):
        payload = {}
        if LATEST_PATH.exists():
          try:
            payload = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
          except (OSError, json.JSONDecodeError):
            payload = {}
        return self._send_json(200, payload)
      if path.startswith("/api/settings"):
        return self._send_json(200, get_config_snapshot(config))
      if path.startswith("/api/weather/settings"):
        snapshot = get_config_snapshot(config)
        payload = {
          "weather_location": str(snapshot.get("weather_location") or "YOUR_ZIP"),
          "weather_unit": str(snapshot.get("weather_unit") or "fahrenheit"),
        }
        return self._send_json(200, payload)
      if path.startswith("/api/inputs"):
        devices, error = list_audio_inputs()
        response = {"devices": devices, "error": error}
        return self._send_json(200, response)
      if path.startswith("/api/queue"):
        return self._send_json(200, {"pending": count_pending_segments()})
      if path.startswith("/api/log/summary"):
        return self._send_json(200, summarize_log())
      if path.startswith("/api/log/activity"):
        query = parse_qs(urlsplit(self.path).query)
        days = query.get("days", ["7"])[0]
        return self._send_json(200, build_activity_curve(days))
      if path.startswith("/api/clip"):
        query = parse_qs(urlsplit(self.path).query)
        species = (query.get("species") or [""])[0]
        species = str(species).strip()
        if not species:
          return self.send_error(400, "Missing species")
        index = load_clip_index()
        clip = index.get(species)
        if not clip or not clip.get("filename"):
          return self.send_error(404, "Clip not found")
        path = CLIPS_DIR / clip["filename"]
        download = (query.get("download") or [""])[0]
        download_name = None
        if str(download).strip() == "1":
          download_name = clip["filename"]
        return self._send_file(path, "audio/wav", download_name)
      if path.startswith("/api/log/csv"):
        entries = read_log(None)
        payload = build_log_csv(entries)
        return self._send_csv("birdnet_detections.csv", payload)
      if path.startswith("/api/log"):
        query = parse_qs(urlsplit(self.path).query)
        try:
          limit = int(query.get("limit", ["200"])[0])
        except ValueError:
          limit = 200
        limit = max(0, min(1000, limit))
        return self._send_json(200, {"entries": read_log(limit)})
      if path.startswith("/api/events"):
        query = parse_qs(urlsplit(self.path).query)
        try:
          limit = int(query.get("limit", ["200"])[0])
        except ValueError:
          limit = 200
        limit = max(0, min(1000, limit))
        return self._send_json(200, {"entries": read_events(limit)})
      super().do_GET()

    def do_POST(self):
      path = self.path.split("?", 1)[0]
      if self._requires_auth(path) and not self._is_authorized():
        return self._require_auth()
      if path.startswith("/api/icon/upload"):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
          return self._send_json(400, {"ok": False, "error": "Expected multipart form"})
        form = cgi.FieldStorage(
          fp=self.rfile,
          headers=self.headers,
          environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
          },
        )
        species = str(form.getvalue("species", "")).strip()
        if not species:
          return self._send_json(400, {"ok": False, "error": "Species is required"})
        file_item = form["icon"] if "icon" in form else None
        if isinstance(file_item, list):
          file_item = file_item[0] if file_item else None
        if file_item is None or not hasattr(file_item, "file") or file_item.file is None:
          return self._send_json(400, {"ok": False, "error": "Icon file missing"})
        payload = file_item.file.read()
        icon_url, error = save_species_icon(species, payload)
        if error:
          return self._send_json(400, {"ok": False, "error": error})
        bump_log_revision()
        refresh_last_detection(config)
        return self._send_json(200, {"ok": True, "icon_url": icon_url})
      if path.startswith("/api/icon/delete"):
        data = self._read_json()
        if data is None or not isinstance(data, dict):
          return self._send_json(400, {"ok": False, "error": "Invalid JSON"})
        species = str(data.get("species", "")).strip()
        if not species:
          return self._send_json(400, {"ok": False, "error": "Species is required"})
        removed = remove_species_icon(species)
        if removed:
          bump_log_revision()
          refresh_last_detection(config)
        return self._send_json(200, {"ok": removed})
      if path.startswith("/api/settings"):
        data = self._read_json()
        if data is None or not isinstance(data, dict):
          return self._send_json(400, {"ok": False, "error": "Invalid JSON"})
        changed = update_config(config, data)
        return self._send_json(200, {"ok": True, "changed": sorted(changed)})
      if path.startswith("/api/restart/server"):
        log_event("server", "Server restart requested")
        self._send_json(200, {"ok": True})
        threading.Thread(target=restart_server_process, daemon=True).start()
        return
      if path.startswith("/api/restart"):
        restart_capture.set()
        log_event("server", "Capture restart requested")
        return self._send_json(200, {"ok": True})
      if path.startswith("/api/log/delete"):
        data = self._read_json()
        if data is None or not isinstance(data, dict):
          return self._send_json(400, {"ok": False, "error": "Invalid JSON"})
        entry_id_value = str(data.get("id", "")).strip()
        if not entry_id_value:
          return self._send_json(400, {"ok": False, "error": "Missing id"})
        removed = delete_log_entry(entry_id_value)
        if removed:
          refresh_last_detection(config)
        return self._send_json(200, {"ok": removed})
      if path.startswith("/api/log/add"):
        data = self._read_json()
        if data is None or not isinstance(data, dict):
          return self._send_json(400, {"ok": False, "error": "Invalid JSON"})
        entry, error = add_manual_log_entry(data, config)
        if error:
          return self._send_json(400, {"ok": False, "error": error})
        return self._send_json(200, {"ok": True, "entry": entry})
      return super().do_POST()

  return NoCacheHandler


def run_server(config, stop_event):
  handler = make_handler(config)
  server = ThreadingHTTPServer(("", config["http_port"]), handler)

  def shutdown(*_):
    stop_event.set()
    server.shutdown()

  signal.signal(signal.SIGINT, shutdown)
  signal.signal(signal.SIGTERM, shutdown)

  server.serve_forever(poll_interval=0.5)


def main():
  config = load_config()
  ensure_latest_file(config)

  stop_event = threading.Event()
  capture_thread = threading.Thread(target=capture_loop, args=(config, stop_event), daemon=True)
  process_thread = threading.Thread(target=process_loop, args=(config, stop_event), daemon=True)

  capture_thread.start()
  process_thread.start()

  run_server(config, stop_event)


if __name__ == "__main__":
  main()

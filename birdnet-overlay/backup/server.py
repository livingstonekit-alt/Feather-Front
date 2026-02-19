#!/usr/bin/env python3
import audioop
import csv
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
import subprocess
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TMP_DIR = ROOT / "tmp"
LATEST_PATH = DATA_DIR / "latest.json"
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = DATA_DIR / "detections.jsonl"
EVENTS_PATH = DATA_DIR / "events.jsonl"
CLIPS_DIR = DATA_DIR / "clips"
CLIP_INDEX_PATH = DATA_DIR / "clips.json"
ANALYSIS_MIN_CONF = 0.01
MAX_QUEUE_SEGMENTS = 60
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
}

write_lock = threading.Lock()
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


def load_config():
  config = dict(DEFAULT_CONFIG)
  if CONFIG_PATH.exists():
    try:
      with CONFIG_PATH.open("r", encoding="utf-8") as handle:
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

  return config


def get_config_snapshot(config):
  with config_lock:
    snapshot = dict(config)
  snapshot["current_week"] = current_week()
  return snapshot


def write_config(config):
  encoded = json.dumps(config, ensure_ascii=True, indent=2)
  CONFIG_PATH.write_text(encoded, encoding="utf-8")


def update_config(config, updates):
  changed = set()
  restart_fields = {"input_mode", "input_device", "rtmp_url", "segment_seconds"}
  allowed = {
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
  }

  def normalize_input_mode(value):
    value = str(value or "").strip().lower()
    if value in {"rtmp", "rtsp", "stream"}:
      return "stream"
    if value in {"avfoundation", "device", "local"}:
      return "avfoundation"
    return "stream"

  def cast_value(key, value):
    if key in {"input_mode", "input_device", "rtmp_url", "birdnet_template", "birdnet_workdir", "location"}:
      return str(value)
    if key == "auto_week":
      if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
      return bool(value)
    if key == "segment_seconds":
      return max(0.1, float(value))
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
  if not LOG_PATH.exists():
    LOG_PATH.write_text("", encoding="utf-8")
  if not EVENTS_PATH.exists():
    EVENTS_PATH.write_text("", encoding="utf-8")
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
    "log_revision": get_log_revision(),
    "species_count": get_species_count(),
    "overlay_hold_seconds": config.get("overlay_hold_seconds", 60),
    "overlay_sticky": bool(config.get("overlay_sticky", False)),
  }


def safe_stream_url(url):
  if not url:
    return ""
  try:
    parts = urlsplit(url)
    if not parts.username and not parts.password:
      return url
    host = parts.hostname or ""
    if parts.port:
      host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
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


def derive_last_detection(entries, config):
  if not entries:
    return None
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
  payload = {
    "timestamp": now_iso(),
    "species": top.get("species", "Unknown"),
    "scientific_name": top.get("scientific_name", ""),
    "confidence": top.get("confidence"),
    "clip_seconds": config.get("segment_seconds"),
    "top_predictions": predictions,
    "location": config.get("location", "Stream"),
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
  LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
  with log_lock:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
      for entry in entries:
        if "id" not in entry:
          entry["id"] = entry_id(entry)
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
  update_species_set(entries)
  update_species_counts(entries)
  bump_log_revision()


def entry_id(entry):
  if entry.get("id"):
    return str(entry["id"])
  base = f"{entry.get('timestamp', '')}|{entry.get('species', '')}|{entry.get('confidence', '')}"
  return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def read_log(limit=200):
  if not LOG_PATH.exists():
    return []
  try:
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
  except OSError:
    return []
  if limit is not None:
    if limit <= 0:
      return []
    lines = lines[-limit:]
  output = []
  for line in lines:
    if not line.strip():
      continue
    try:
      entry = json.loads(line)
    except json.JSONDecodeError:
      continue
    entry["id"] = entry_id(entry)
    output.append(entry)
  return output


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


def update_best_clips(segment_path, predictions):
  if not predictions:
    return
  CLIPS_DIR.mkdir(parents=True, exist_ok=True)
  index = load_clip_index()
  updated = False
  for prediction in predictions:
    species = prediction.get("species", "Unknown") or "Unknown"
    confidence = prediction.get("confidence")
    try:
      confidence_value = float(confidence)
    except (TypeError, ValueError):
      confidence_value = -1.0
    entry = index.get(species, {})
    try:
      existing_conf = float(entry.get("confidence", -1))
    except (TypeError, ValueError):
      existing_conf = -1.0
    if confidence_value <= existing_conf:
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
      "timestamp": now_iso(),
      "filename": filename,
    }
    updated = True
  if updated:
    save_clip_index(index)


def summarize_log():
  if not LOG_PATH.exists():
    return {"entries": [], "species_count": 0, "total_detections": 0}
  summary = {}
  total = 0
  try:
    handle = LOG_PATH.open("r", encoding="utf-8")
  except OSError:
    return {"entries": [], "species_count": 0, "total_detections": 0}
  with handle:
    for line in handle:
      if not line.strip():
        continue
      try:
        entry = json.loads(line)
      except json.JSONDecodeError:
        continue
      total += 1
      species = entry.get("species", "Unknown") or "Unknown"
      item = summary.get(species)
      dt = parse_timestamp(entry.get("timestamp"))
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
        }
        continue

      item["count"] += 1
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
    clip = clip_index.get(species)
    if clip and clip.get("filename"):
      latest_entry["clip_url"] = f"/api/clip?species={quote(species)}"
      latest_entry["clip_confidence"] = clip.get("confidence")
    entries.append(latest_entry)

  return {
    "entries": entries,
    "species_count": len(entries),
    "total_detections": total,
  }


def delete_log_entry(entry_key):
  if not entry_key or not LOG_PATH.exists():
    return False
  removed = False
  with log_lock:
    try:
      lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
      return False
    kept = []
    for line in lines:
      if not line.strip():
        continue
      try:
        entry = json.loads(line)
      except json.JSONDecodeError:
        continue
      current_id = entry_id(entry)
      if current_id == entry_key:
        removed = True
        continue
      kept.append(json.dumps(entry, ensure_ascii=True))
    tmp_path = LOG_PATH.with_suffix(".tmp")
    tmp_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp_path.replace(LOG_PATH)
  if removed:
    rebuild_species_set()
    rebuild_species_counts()
    bump_log_revision()
  return removed


def append_event(entries):
  if not entries:
    return
  if isinstance(entries, dict):
    entries = [entries]
  EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
  with event_lock:
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
      for entry in entries:
        if "id" not in entry:
          entry["id"] = event_id(entry)
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def event_id(entry):
  if entry.get("id"):
    return str(entry["id"])
  base = f"{entry.get('timestamp', '')}|{entry.get('type', '')}|{entry.get('message', '')}"
  return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def read_events(limit=200):
  if not EVENTS_PATH.exists():
    return []
  try:
    lines = EVENTS_PATH.read_text(encoding="utf-8").splitlines()
  except OSError:
    return []
  if limit:
    lines = lines[-limit:]
  output = []
  for line in lines:
    if not line.strip():
      continue
    try:
      entry = json.loads(line)
    except json.JSONDecodeError:
      continue
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
  expected = output_target / f"{input_path.stem}.BirdNET.results.csv"
  return output_target, expected


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
    return [], "BirdNET output missing."

  try:
    predictions = extract_predictions(output_file)
  except (OSError, csv.Error):
    return [], "Unable to read BirdNET output."
  finally:
    output_file.unlink(missing_ok=True)

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


def process_loop(config, stop_event):
  last_error = None
  while not stop_event.is_set():
    files = sorted(TMP_DIR.glob("segment_*.wav"), key=lambda path: path.stat().st_mtime)
    if len(files) > MAX_QUEUE_SEGMENTS:
      drop_count = len(files) - MAX_QUEUE_SEGMENTS
      for path in files[:drop_count]:
        path.unlink(missing_ok=True)
      log_event("analysis", f"Dropped {drop_count} queued segments to cap queue at {MAX_QUEUE_SEGMENTS}")
      files = files[drop_count:]
    if not files:
      time.sleep(0.4)
      continue

    for path in files:
      if stop_event.is_set():
        break
      if not is_file_ready(path):
        continue

      snapshot = get_config_snapshot(config)

      if not snapshot.get("birdnet_template"):
        status_message = "BIRDNET_TEMPLATE not set"
        payload = build_payload(
          snapshot,
          status="idle",
          status_message=status_message,
          predictions=[],
        )
        if status_message != last_error:
          log_event("error", status_message)
          last_error = status_message
        write_latest(payload)
        path.unlink(missing_ok=True)
        continue

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
        continue

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
        if error != last_error:
          log_event("error", error)
          last_error = error
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
          log_event("analysis", "Below threshold: " + ", ".join(summaries))
        last_error = None

      write_latest(payload)
      path.unlink(missing_ok=True)

  return


def capture_loop(config, stop_event):
  last_status = None
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
      if time.time() - last_segment_time > stall_timeout:
        log_event("server", f"No new audio segments for {int(stall_timeout)}s, restarting capture")
        restart_requested = True
        process.terminate()
        break
      time.sleep(0.5)

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
      if self.path.endswith(".json"):
        self.send_header("Cache-Control", "no-store, max-age=0")
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

    def do_GET(self):
      path = self.path.split("?", 1)[0]
      if path in {"/settings", "/settings/"}:
        self.path = "/settings.html"
        return super().do_GET()
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
      if path.startswith("/api/inputs"):
        devices, error = list_audio_inputs()
        response = {"devices": devices, "error": error}
        return self._send_json(200, response)
      if path.startswith("/api/queue"):
        return self._send_json(200, {"pending": count_pending_segments()})
      if path.startswith("/api/log/summary"):
        return self._send_json(200, summarize_log())
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
      if path.startswith("/api/settings"):
        data = self._read_json()
        if data is None or not isinstance(data, dict):
          return self._send_json(400, {"ok": False, "error": "Invalid JSON"})
        changed = update_config(config, data)
        return self._send_json(200, {"ok": True, "changed": sorted(changed)})
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

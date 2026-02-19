const elements = {
  inputMode: document.querySelectorAll("input[name='input_mode']"),
  streamField: document.getElementById("stream-field"),
  streamUrl: document.getElementById("stream-url"),
  deviceField: document.getElementById("device-field"),
  deviceSelect: document.getElementById("device-select"),
  deviceManual: document.getElementById("device-manual"),
  deviceWarning: document.getElementById("device-warning"),
  refreshDevices: document.getElementById("refresh-devices"),
  minConfidence: document.getElementById("min-confidence"),
  minConfidenceValue: document.getElementById("min-confidence-value"),
  segmentSeconds: document.getElementById("segment-seconds"),
  locationLabel: document.getElementById("location-label"),
  coords: document.getElementById("coords"),
  week: document.getElementById("week"),
  autoWeek: document.getElementById("auto-week"),
  weekHint: document.getElementById("week-hint"),
  silenceThreshold: document.getElementById("silence-threshold"),
  silenceMinSeconds: document.getElementById("silence-min-seconds"),
  overlayHold: document.getElementById("overlay-hold"),
  overlaySticky: document.getElementById("overlay-sticky"),
  saveSettings: document.getElementById("save-settings"),
  restartCapture: document.getElementById("restart-capture"),
  saveStatus: document.getElementById("save-status"),
  logBody: document.getElementById("log-body"),
  refreshLog: document.getElementById("refresh-log"),
  exportLog: document.getElementById("export-log"),
  speciesCount: document.getElementById("species-count"),
  logSortButtons: Array.from(document.querySelectorAll(".sort-header")),
  activityBody: document.getElementById("activity-body"),
  queueCount: document.getElementById("queue-count"),
  activityFilters: Array.from(document.querySelectorAll(".activity-filter")),
  controlGrid: document.querySelector(".panel-grid"),
  controlPanels: Array.from(document.querySelectorAll(".block-collapsible[data-panel]")),
};

let currentDevice = "";
let logEntries = [];
let activityEntries = [];
let logSortMode = "time";
let logSortOrder = "desc";
let currentWeek = null;
const PANEL_STATE_KEY = "birdnet_panel_state";
const PANEL_ORDER_KEY = "birdnet_panel_order";
let audioPlayer = null;
let currentClipUrl = null;
let activityFilterSet = new Set(["all"]);

function formatWeekHint() {
  if (elements.autoWeek.checked && Number.isFinite(currentWeek)) {
    return `Auto week is set to ${currentWeek}.`;
  }
  return "Set week to -1 for year-round species filtering.";
}

function updateWeekMode() {
  if (elements.autoWeek.checked) {
    elements.week.disabled = true;
  } else {
    elements.week.disabled = false;
  }
  elements.weekHint.textContent = formatWeekHint();
}

function getPanelState() {
  try {
    const raw = localStorage.getItem(PANEL_STATE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};
  }
}

function getPanelOrder() {
  try {
    const raw = localStorage.getItem(PANEL_ORDER_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function applyPanelPreferences() {
  const order = getPanelOrder();
  if (order.length && elements.controlGrid) {
    order.forEach((panelId) => {
      const panel = elements.controlPanels.find((item) => item.dataset.panel === panelId);
      if (panel) {
        elements.controlGrid.appendChild(panel);
      }
    });
  }
  if (elements.controlGrid) {
    elements.controlPanels = Array.from(
      elements.controlGrid.querySelectorAll(".block-collapsible[data-panel]")
    );
  }

  const state = getPanelState();
  elements.controlPanels.forEach((panel) => {
    const key = panel.dataset.panel;
    if (!key || !(key in state)) {
      return;
    }
    panel.open = Boolean(state[key]);
  });
}

function persistPanelPreferences() {
  const order = elements.controlPanels.map((panel) => panel.dataset.panel);
  const state = {};
  elements.controlPanels.forEach((panel) => {
    if (panel.dataset.panel) {
      state[panel.dataset.panel] = panel.open;
    }
  });
  try {
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(order));
    localStorage.setItem(PANEL_STATE_KEY, JSON.stringify(state));
  } catch (error) {
    return;
  }
}


function setStatus(message) {
  elements.saveStatus.textContent = message;
}

function getSelectedMode() {
  const selected = Array.from(elements.inputMode).find((input) => input.checked);
  return selected ? selected.value : "stream";
}

function updateModeFields() {
  const mode = getSelectedMode();
  elements.streamField.style.display = mode === "stream" ? "block" : "none";
  elements.deviceField.style.display = mode === "avfoundation" ? "block" : "none";
}

function formatTimeParts(value) {
  if (!value) {
    return { dateText: "--", timeText: "--" };
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { dateText: "--", timeText: "--" };
  }
  return {
    dateText: date.toLocaleDateString([], { month: "short", day: "2-digit" }),
    timeText: date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
  };
}

function formatConfidence(value) {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "--";
  }
  const percent = numeric > 1 ? numeric : numeric * 100;
  return Math.round(percent) + "%";
}



function numericConfidence(value) {
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    return -1;
  }
  return parsed > 1 ? parsed / 100 : parsed;
}

function numericTime(value) {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function slugify(value) {
  return String(value || "unknown")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function updateSortIndicators() {
  elements.logSortButtons.forEach((button) => {
    const active = button.dataset.sort === logSortMode;
    button.dataset.active = active ? "true" : "false";
    if (active) {
      button.dataset.order = logSortOrder;
    } else {
      delete button.dataset.order;
    }
  });
}

function buildSpeciesCounts(entries) {
  const counts = new Map();
  entries.forEach((entry) => {
    const key = entry.species || "Unknown";
    const countValue = Number(entry.count) || 0;
    counts.set(key, countValue);
  });
  return counts;
}

function updateActivityFilterButtons() {
  elements.activityFilters.forEach((button) => {
    const key = button.dataset.filter || "all";
    const active = activityFilterSet.has("all") ? key === "all" : activityFilterSet.has(key);
    button.dataset.active = active ? "true" : "false";
  });
}

function getFilteredActivity(entries) {
  if (activityFilterSet.has("all")) {
    return entries;
  }
  return entries.filter((entry) => activityFilterSet.has(entry.type || "server"));
}

function sortSummary(items) {
  const direction = logSortOrder === "asc" ? 1 : -1;
  return items.slice().sort((a, b) => {
    let result = 0;
    if (logSortMode === "species") {
      const nameA = (a.species || "Unknown").toLowerCase();
      const nameB = (b.species || "Unknown").toLowerCase();
      result = nameA.localeCompare(nameB);
    } else if (logSortMode === "count") {
      result = a.count - b.count;
    } else {
      result = a.time - b.time;
    }

    if (result === 0) {
      result = a.time - b.time;
    }
    if (result === 0) {
      result = numericConfidence(a.entry.confidence) - numericConfidence(b.entry.confidence);
    }
    return result * direction;
  });
}

function formatCoords(lat, lon) {
  if (typeof lat !== "number" || typeof lon !== "number") {
    return "";
  }
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return "";
  }
  if (lat === -1 || lon === -1) {
    return "";
  }
  const latDir = lat >= 0 ? "N" : "S";
  const lonDir = lon >= 0 ? "E" : "W";
  const latText = Math.abs(lat).toFixed(4);
  const lonText = Math.abs(lon).toFixed(4);
  return `${latText}° ${latDir}, ${lonText}° ${lonDir}`;
}

function parseCoords(text) {
  if (!text || !text.trim()) {
    return { lat: -1, lon: -1 };
  }
  const directional = text.match(/([0-9.]+)\s*°?\s*([NS])\s*,?\s*([0-9.]+)\s*°?\s*([EW])/i);
  if (directional) {
    const lat = Number(directional[1]);
    const lon = Number(directional[3]);
    if (!Number.isNaN(lat) && !Number.isNaN(lon)) {
      const latDir = directional[2].toUpperCase();
      const lonDir = directional[4].toUpperCase();
      return {
        lat: latDir === "S" ? -lat : lat,
        lon: lonDir === "W" ? -lon : lon,
      };
    }
  }
  const numbers = text.match(/-?\d+(?:\.\d+)?/g);
  if (numbers && numbers.length >= 2) {
    const lat = Number(numbers[0]);
    const lon = Number(numbers[1]);
    if (!Number.isNaN(lat) && !Number.isNaN(lon)) {
      return { lat, lon };
    }
  }
  return null;
}

function setDeviceOptions(devices) {
  elements.deviceSelect.innerHTML = "";
  if (!devices || devices.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No audio devices found";
    elements.deviceSelect.appendChild(option);
    return;
  }
  devices.forEach((device) => {
    const option = document.createElement("option");
    option.value = device.id;
    option.textContent = `[${device.id}] ${device.name}`;
    elements.deviceSelect.appendChild(option);
  });
  if (currentDevice) {
    const optionMatch = Array.from(elements.deviceSelect.options).some(
      (option) => option.value === currentDevice
    );
    if (optionMatch) {
      elements.deviceSelect.value = currentDevice;
    } else {
      elements.deviceManual.value = currentDevice;
    }
  }
}

async function loadInputs() {
  try {
    const response = await fetch("/api/inputs", { cache: "no-store" });
    const data = await response.json();
    if (data.error) {
      elements.deviceWarning.textContent = data.error;
      setDeviceOptions([]);
      return;
    }
    setDeviceOptions(data.devices || []);
    if (!data.devices || data.devices.length === 0) {
      elements.deviceWarning.textContent = "No devices found. Check microphone permissions.";
    } else {
      elements.deviceWarning.textContent = "";
    }
  } catch (error) {
    elements.deviceWarning.textContent = "Unable to load devices.";
    setDeviceOptions([]);
  }
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    const data = await response.json();
    elements.streamUrl.value = data.rtmp_url || "";
    elements.segmentSeconds.value = data.segment_seconds || 3;
    elements.locationLabel.value = data.location || "";
    elements.week.value = data.week ?? -1;
    elements.autoWeek.checked = Boolean(data.auto_week);
    currentWeek = Number.isFinite(Number(data.current_week)) ? Number(data.current_week) : null;
    updateWeekMode();
    const thresholdValue = Number(data.silence_threshold_db ?? -45);
    elements.silenceThreshold.value = Number.isFinite(thresholdValue) ? thresholdValue : -45;
    const minActiveValue = Number(data.silence_min_seconds ?? 0.2);
    elements.silenceMinSeconds.value = Number.isFinite(minActiveValue) ? minActiveValue : 0.2;
    const overlayHoldValue = Number(data.overlay_hold_seconds ?? 60);
    elements.overlayHold.value = Number.isFinite(overlayHoldValue) ? overlayHoldValue : 60;
    if (elements.overlaySticky) {
      elements.overlaySticky.checked = Boolean(data.overlay_sticky);
    }

    currentDevice = data.input_device || "";
    elements.deviceManual.value = "";

    const formatted = formatCoords(Number(data.latitude), Number(data.longitude));
    elements.coords.value = formatted;

    const minConfidence = Number(data.min_confidence ?? 0.25);
    elements.minConfidence.value = minConfidence;
    elements.minConfidenceValue.textContent = minConfidence.toFixed(2);

    Array.from(elements.inputMode).forEach((input) => {
      input.checked = input.value === (data.input_mode || "stream");
    });
    updateModeFields();
    if (currentDevice) {
      const optionMatch = Array.from(elements.deviceSelect.options).some(
        (option) => option.value === currentDevice
      );
      if (optionMatch) {
        elements.deviceSelect.value = currentDevice;
      } else {
        elements.deviceManual.value = currentDevice;
      }
    }
    setStatus("Loaded");
  } catch (error) {
    setStatus("Failed to load");
  }
}

function renderLog(entries) {
  elements.logBody.innerHTML = "";
  if (!entries || entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "log-row";
    empty.innerHTML = "<span>--</span><span>No detections yet</span><span>--</span><span>--</span><span></span>";
    elements.logBody.appendChild(empty);
    if (elements.speciesCount) {
      elements.speciesCount.textContent = "0 species";
    }
    return;
  }
  const counts = buildSpeciesCounts(entries);
  const summary = entries.map((entry) => ({
    species: entry.species || "Unknown",
    entry,
    time: numericTime(entry.timestamp),
    count: Number(entry.count) || 0,
  }));
  const sorted = sortSummary(summary);
  if (elements.speciesCount) {
    elements.speciesCount.textContent = `${entries.length} species`;
  }

  sorted.forEach((item) => {
    const entry = item.entry;
    const row = document.createElement("div");
    row.className = "log-row";

    const time = document.createElement("div");
    time.className = "log-time";
    const parts = formatTimeParts(entry.timestamp);
    const dateLine = document.createElement("span");
    dateLine.className = "time-date";
    dateLine.textContent = parts.dateText;
    const timeLine = document.createElement("span");
    timeLine.className = "time-clock";
    timeLine.textContent = parts.timeText;
    time.appendChild(dateLine);
    time.appendChild(timeLine);

    const species = document.createElement("div");
    species.className = "log-species";
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "species-link";
    nameButton.textContent = entry.species || "Unknown";
    nameButton.dataset.species = entry.species || "Unknown";
    const latin = document.createElement("span");
    latin.className = "log-latin";
    latin.textContent = entry.scientific_name || "";
    species.appendChild(nameButton);
    species.appendChild(latin);
    if (entry.clip_url) {
      const clipActions = document.createElement("div");
      clipActions.className = "clip-actions";
      const playButton = document.createElement("button");
      playButton.type = "button";
      playButton.className = "clip-play";
      playButton.setAttribute("aria-label", "Play clip");
      playButton.title = "Play clip";
      playButton.dataset.clipUrl = entry.clip_url;
      const download = document.createElement("a");
      download.className = "clip-download";
      download.setAttribute("aria-label", "Download clip");
      download.title = "Download clip";
      download.href = `${entry.clip_url}${entry.clip_url.includes("?") ? "&" : "?"}download=1`;
      download.download = `${slugify(entry.species || "bird-call")}.wav`;
      const clipConfidence = document.createElement("span");
      clipConfidence.className = "clip-confidence";
      clipConfidence.textContent = formatConfidence(entry.clip_confidence ?? entry.confidence);
      clipActions.appendChild(playButton);
      clipActions.appendChild(download);
      clipActions.appendChild(clipConfidence);
      species.appendChild(clipActions);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "log-delete";
    remove.textContent = "×";
    remove.title = "Remove";
    remove.dataset.id = entry.id || "";
    const countCell = document.createElement("span");
    countCell.className = "log-count-cell";
    countCell.textContent = `${item.count}x`;
    const confidence = document.createElement("span");
    confidence.className = "log-confidence";
    confidence.textContent = formatConfidence(entry.confidence);

    row.appendChild(time);
    row.appendChild(species);
    row.appendChild(countCell);
    row.appendChild(confidence);
    row.appendChild(remove);

    elements.logBody.appendChild(row);
  });
}

function renderActivity(entries) {
  elements.activityBody.innerHTML = "";
  const filtered = getFilteredActivity(entries || []);
  if (!filtered || filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "activity-row";
    empty.innerHTML = "<span>--</span><span>--</span><span>No activity yet</span>";
    elements.activityBody.appendChild(empty);
    return;
  }

  filtered.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "activity-row";
    row.dataset.type = entry.type || "server";

    const time = document.createElement("div");
    time.className = "activity-time";
    const parts = formatTimeParts(entry.timestamp);
    const dateLine = document.createElement("span");
    dateLine.className = "time-date";
    dateLine.textContent = parts.dateText;
    const timeLine = document.createElement("span");
    timeLine.className = "time-clock";
    timeLine.textContent = parts.timeText;
    time.appendChild(dateLine);
    time.appendChild(timeLine);

    const type = document.createElement("span");
    type.className = "activity-type";
    type.textContent = entry.type || "server";

    const message = document.createElement("span");
    message.className = "activity-message";
    if (entry.message) {
      message.textContent = entry.message;
    } else if (entry.species) {
      const confidence = formatConfidence(entry.confidence);
      message.textContent = confidence === "--"
        ? `Detected ${entry.species}`
        : `Detected ${entry.species} (${confidence})`;
    } else {
      message.textContent = "Event";
    }

    row.appendChild(time);
    row.appendChild(type);
    row.appendChild(message);
    elements.activityBody.appendChild(row);
  });
}

async function loadLog() {
  try {
    const response = await fetch("/api/log/summary", { cache: "no-store" });
    const data = await response.json();
    logEntries = Array.isArray(data.entries) ? data.entries.slice() : [];
    if (elements.speciesCount && Number.isFinite(Number(data.species_count))) {
      elements.speciesCount.textContent = `${Number(data.species_count)} species`;
    }
    renderLog(logEntries);
  } catch (error) {
    setStatus("Log unavailable");
  }
}

async function loadActivity() {
  try {
    const response = await fetch("/api/events?limit=100", { cache: "no-store" });
    const data = await response.json();
    activityEntries = Array.isArray(data.entries) ? data.entries.slice() : [];
    activityEntries.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    renderActivity(activityEntries);
  } catch (error) {
    setStatus("Activity unavailable");
  }
}

async function loadQueue() {
  try {
    const response = await fetch("/api/queue", { cache: "no-store" });
    const data = await response.json();
    const count = Number(data.pending) || 0;
    elements.queueCount.textContent = `Queue ${count}`;
  } catch (error) {
    elements.queueCount.textContent = "Queue --";
  }
}


async function deleteLogEntry(entryId) {
  if (!entryId) {
    return;
  }
  setStatus("Removing...");
  try {
    const response = await fetch("/api/log/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: entryId }),
    });
    const result = await response.json();
    if (result.ok) {
      setStatus("Removed");
      loadLog();
    } else {
      setStatus("Remove failed");
    }
  } catch (error) {
    setStatus("Remove failed");
  }
}

function openSpeciesSearch(species) {
  if (!species || species === "Unknown") {
    return;
  }
  const query = encodeURIComponent(`${species} bird`);
  window.open(`https://www.google.com/search?tbm=isch&q=${query}`, "_blank", "noopener");
}

function playClip(url) {
  if (!url) {
    return;
  }
  if (!audioPlayer) {
    audioPlayer = new Audio();
  }
  if (currentClipUrl === url && !audioPlayer.paused) {
    audioPlayer.pause();
    return;
  }
  currentClipUrl = url;
  audioPlayer.src = url;
  audioPlayer.play().catch(() => {});
}

function exportDetectionsCsv() {
  window.location.href = "/api/log/csv";
}

async function saveSettings() {
  setStatus("Saving...");
  const coords = parseCoords(elements.coords.value);
  if (!coords) {
    setStatus("Invalid coordinates");
    return;
  }
  const manualDevice = elements.deviceManual.value.trim();
  const deviceValue = manualDevice || elements.deviceSelect.value;
  const weekValue = elements.week.value === "" ? -1 : Number(elements.week.value);
  const thresholdValue = elements.silenceThreshold.value === "" ? -45 : Number(elements.silenceThreshold.value);
  const minActiveValue = elements.silenceMinSeconds.value === "" ? 0.2 : Number(elements.silenceMinSeconds.value);
  const overlayHoldValue = elements.overlayHold.value === "" ? 60 : Number(elements.overlayHold.value);

  const payload = {
    input_mode: getSelectedMode(),
    rtmp_url: elements.streamUrl.value.trim(),
    input_device: deviceValue,
    min_confidence: Number(elements.minConfidence.value),
    segment_seconds: Number(elements.segmentSeconds.value),
    silence_threshold_db: Number.isFinite(thresholdValue) ? thresholdValue : -45,
    silence_min_seconds: Number.isFinite(minActiveValue) ? minActiveValue : 0.2,
    overlay_hold_seconds: Number.isFinite(overlayHoldValue) ? overlayHoldValue : 60,
    overlay_sticky: Boolean(elements.overlaySticky && elements.overlaySticky.checked),
    location: elements.locationLabel.value.trim(),
    latitude: coords.lat,
    longitude: coords.lon,
    week: weekValue,
    auto_week: elements.autoWeek.checked,
  };

  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (result.ok) {
      persistPanelPreferences();
      setStatus("Saved");
    } else {
      setStatus("Save failed");
    }
  } catch (error) {
    setStatus("Save failed");
  }
}

async function restartCapture() {
  setStatus("Restarting...");
  try {
    const response = await fetch("/api/restart", { method: "POST" });
    const result = await response.json();
    if (result.ok) {
      setStatus("Capture restarted");
    } else {
      setStatus("Restart failed");
    }
  } catch (error) {
    setStatus("Restart failed");
  }
}

function bindEvents() {
  Array.from(elements.inputMode).forEach((input) => {
    input.addEventListener("change", updateModeFields);
  });
  elements.minConfidence.addEventListener("input", () => {
    elements.minConfidenceValue.textContent = Number(elements.minConfidence.value).toFixed(2);
  });
  elements.autoWeek.addEventListener("change", updateWeekMode);
  elements.refreshDevices.addEventListener("click", loadInputs);
  elements.restartCapture.addEventListener("click", restartCapture);
  elements.saveSettings.addEventListener("click", saveSettings);
  elements.refreshLog.addEventListener("click", loadLog);
  elements.exportLog.addEventListener("click", exportDetectionsCsv);
  elements.logSortButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.sort || "time";
      if (logSortMode === mode) {
        logSortOrder = logSortOrder === "asc" ? "desc" : "asc";
      } else {
        logSortMode = mode;
        logSortOrder = "desc";
      }
      updateSortIndicators();
      renderLog(logEntries);
    });
  });
  elements.activityFilters.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.filter || "all";
      if (key === "all") {
        activityFilterSet = new Set(["all"]);
      } else {
        if (activityFilterSet.has("all")) {
          activityFilterSet = new Set();
        }
        if (activityFilterSet.has(key)) {
          activityFilterSet.delete(key);
        } else {
          activityFilterSet.add(key);
        }
        if (activityFilterSet.size === 0) {
          activityFilterSet.add("all");
        }
      }
      updateActivityFilterButtons();
      renderActivity(activityEntries);
    });
  });
  elements.logBody.addEventListener("click", (event) => {
    const speciesTarget = event.target.closest(".species-link");
    if (speciesTarget) {
      openSpeciesSearch(speciesTarget.dataset.species);
      return;
    }
    const clipTarget = event.target.closest(".clip-play");
    if (clipTarget) {
      playClip(clipTarget.dataset.clipUrl);
      return;
    }
    const target = event.target.closest(".log-delete");
    if (target) {
      deleteLogEntry(target.dataset.id);
    }
  });

  if (elements.controlGrid) {
    let draggedPanel = null;

    elements.controlGrid.addEventListener("click", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (handle) {
        event.preventDefault();
        event.stopPropagation();
      }
    });

    elements.controlGrid.addEventListener("dragstart", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (!handle) {
        return;
      }
      const panel = handle.closest(".block-collapsible[data-panel]");
      if (!panel) {
        return;
      }
      draggedPanel = panel;
      panel.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", panel.dataset.panel || "");
    });

    elements.controlGrid.addEventListener("dragend", () => {
      if (draggedPanel) {
        draggedPanel.classList.remove("is-dragging");
      }
      draggedPanel = null;
    });

    elements.controlGrid.addEventListener("dragover", (event) => {
      if (!draggedPanel) {
        return;
      }
      event.preventDefault();
      const target = event.target.closest(".block-collapsible[data-panel]");
      if (!target || target === draggedPanel) {
        return;
      }
      const rect = target.getBoundingClientRect();
      const before = event.clientY < rect.top + rect.height / 2;
      if (before) {
        elements.controlGrid.insertBefore(draggedPanel, target);
      } else {
        elements.controlGrid.insertBefore(draggedPanel, target.nextSibling);
      }
      elements.controlPanels = Array.from(
        elements.controlGrid.querySelectorAll(".block-collapsible[data-panel]")
      );
    });

    elements.controlGrid.addEventListener("drop", (event) => {
      if (!draggedPanel) {
        return;
      }
      event.preventDefault();
    });
  }
}

applyPanelPreferences();
bindEvents();
loadInputs().then(loadSettings);
updateSortIndicators();
updateActivityFilterButtons();
loadLog();
loadActivity();
loadQueue();
setInterval(loadLog, 2000);
setInterval(loadActivity, 2000);
setInterval(loadQueue, 2000);

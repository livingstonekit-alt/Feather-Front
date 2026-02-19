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
  coords: document.getElementById("coords"),
  week: document.getElementById("week"),
  autoWeek: document.getElementById("auto-week"),
  weekHint: document.getElementById("week-hint"),
  silenceThreshold: document.getElementById("silence-threshold"),
  silenceMinSeconds: document.getElementById("silence-min-seconds"),
  overlayHold: document.getElementById("overlay-hold"),
  overlaySticky: document.getElementById("overlay-sticky"),
  httpPort: document.getElementById("http-port"),
  overlayUrl: document.getElementById("overlay-url"),
  settingsUrl: document.getElementById("settings-url"),
  weatherOverlayUrl: document.getElementById("weather-overlay-url"),
  copyOverlayUrl: document.getElementById("copy-overlay-url"),
  copySettingsUrl: document.getElementById("copy-settings-url"),
  copyWeatherOverlayUrl: document.getElementById("copy-weather-overlay-url"),
  restartServer: document.getElementById("restart-server"),
  logoutSettings: document.getElementById("logout-settings"),
  weatherLocation: document.getElementById("weather-location"),
  weatherUnit: document.getElementById("weather-unit"),
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
  activityCurve: document.getElementById("activity-curve"),
  controlGrid: document.querySelector(".panel-grid"),
  controlPanels: Array.from(document.querySelectorAll(".block-collapsible[data-panel]")),
};

let currentDevice = "";
let logEntries = [];
let activityEntries = [];
let logSortMode = "time";
let logSortOrder = "desc";
let logSuspendUntil = 0;
let lastLogRevision = null;
let uiSuspendUntil = 0;
let activityCurveData = [];
let activityTodayData = [];
let currentWeek = null;
let currentHttpPort = 8002;
const PANEL_STATE_KEY = "birdnet_panel_state";
const PANEL_ORDER_KEY = "birdnet_panel_order";
let audioPlayer = null;
let currentClipUrl = null;
let activityFilterSet = new Set(["all"]);

function normalizePort(value, fallback = 8002) {
  if (value === null || value === undefined) {
    return fallback;
  }
  const text = String(value).trim();
  if (!text) {
    return fallback;
  }
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(1, Math.min(65535, Math.round(parsed)));
}

function baseUrlForPort(port) {
  const protocol = window.location.protocol || "http:";
  const host = window.location.hostname || "localhost";
  return `${protocol}//${host}:${port}`;
}

function renderOverlayUrls() {
  const port = normalizePort(
    elements.httpPort ? elements.httpPort.value : currentHttpPort,
    currentHttpPort || 8002
  );
  currentHttpPort = port;
  if (elements.httpPort) {
    elements.httpPort.value = String(port);
  }
  const baseUrl = baseUrlForPort(port);
  if (elements.overlayUrl) {
    elements.overlayUrl.value = `${baseUrl}/`;
  }
  if (elements.settingsUrl) {
    elements.settingsUrl.value = `${baseUrl}/settings`;
  }
  if (elements.weatherOverlayUrl) {
    elements.weatherOverlayUrl.value = `${baseUrl}/weather/`;
  }
}

async function copyToClipboard(text) {
  if (!text) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(input);
    return Boolean(ok);
  }
}

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

function isUiSuspended() {
  return Date.now() < uiSuspendUntil;
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
    currentHttpPort = normalizePort(data.http_port ?? 8002);
    if (elements.httpPort) {
      elements.httpPort.value = String(currentHttpPort);
    }
    renderOverlayUrls();
    elements.streamUrl.value = data.rtmp_url || "";
    elements.segmentSeconds.value = data.segment_seconds || 3;
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
    if (elements.weatherLocation) {
      elements.weatherLocation.value = (data.weather_location || "YOUR_ZIP").trim();
    }
    if (elements.weatherUnit) {
      const configuredUnit = String(data.weather_unit || "fahrenheit").toLowerCase();
      elements.weatherUnit.value = configuredUnit === "celsius" ? "celsius" : "fahrenheit";
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
    empty.innerHTML = "<span>--</span><span>No detections yet</span><span>--</span><span></span>";
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

    const iconCell = document.createElement("div");
    iconCell.className = "log-icon-cell";
    const iconWrap = document.createElement("div");
    iconWrap.className = "species-icon";
    iconWrap.title = entry.icon_url ? "Replace icon" : "Upload icon";
    const iconImg = document.createElement("img");
    iconImg.className = "species-icon-img";
    const iconInput = document.createElement("input");
    iconInput.type = "file";
    iconInput.accept = "image/png";
    iconInput.className = "icon-input";
    iconInput.dataset.species = entry.species || "Unknown";
    const iconRemove = document.createElement("button");
    iconRemove.type = "button";
    iconRemove.className = "icon-remove";
    iconRemove.textContent = "×";
    iconRemove.title = "Remove icon";
    iconRemove.dataset.species = entry.species || "Unknown";
    const hasIcon = Boolean(entry.icon_url);
    iconWrap.classList.toggle("has-icon", hasIcon);
    iconWrap.classList.toggle("is-empty", !hasIcon);
    iconRemove.classList.toggle("is-hidden", !hasIcon);
    if (hasIcon) {
      iconImg.src = `${entry.icon_url}?t=${Date.now()}`;
      iconImg.alt = `${entry.species || "Bird"} icon`;
    } else {
      iconImg.removeAttribute("src");
      iconImg.alt = "";
    }
    iconWrap.appendChild(iconImg);
    iconWrap.appendChild(iconInput);
    iconWrap.appendChild(iconRemove);
    iconWrap.addEventListener("dragover", (event) => {
      event.preventDefault();
      iconWrap.classList.add("is-dragover");
    });
    iconWrap.addEventListener("dragleave", () => {
      iconWrap.classList.remove("is-dragover");
    });
    iconWrap.addEventListener("drop", (event) => {
      event.preventDefault();
      iconWrap.classList.remove("is-dragover");
      const file = event.dataTransfer && event.dataTransfer.files
        ? event.dataTransfer.files[0]
        : null;
      if (!file) {
        return;
      }
      if (!file.type || file.type !== "image/png") {
        setStatus("Icon must be a PNG file");
        return;
      }
      uploadIcon(iconInput.dataset.species || "", file);
    });
    iconWrap.addEventListener("click", (event) => {
      if (event.target === iconRemove) {
        return;
      }
      if (event.target === iconInput) {
        return;
      }
      logSuspendUntil = Math.max(logSuspendUntil, Date.now() + 30000);
      uiSuspendUntil = Math.max(uiSuspendUntil, Date.now() + 30000);
      iconInput.click();
    });
    iconInput.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    iconRemove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeIcon(iconRemove.dataset.species || "");
    });
    iconInput.addEventListener("change", () => {
      if (!iconInput.files || iconInput.files.length === 0) {
        uiSuspendUntil = 0;
        return;
      }
      uploadIcon(iconInput.dataset.species || "", iconInput.files[0]);
      iconInput.value = "";
    });
    iconCell.appendChild(iconWrap);

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
    const actionRow = document.createElement("div");
    actionRow.className = "species-actions";
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
      actionRow.appendChild(clipActions);
    }
    if (actionRow.childElementCount > 0) {
      species.appendChild(actionRow);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "log-delete";
    remove.textContent = "×";
    remove.title = "Remove";
    remove.dataset.id = entry.id || "";

    const stats = document.createElement("div");
    stats.className = "log-stats";
    const countStat = document.createElement("div");
    countStat.className = "log-stat";
    const countLabel = document.createElement("span");
    countLabel.className = "log-stat-label";
    countLabel.textContent = "X Heard";
    const countValue = document.createElement("span");
    countValue.className = "log-stat-value";
    countValue.textContent = `${item.count}`;
    countStat.appendChild(countLabel);
    countStat.appendChild(countValue);
    const confidenceStat = document.createElement("div");
    confidenceStat.className = "log-stat";
    const confidenceLabel = document.createElement("span");
    confidenceLabel.className = "log-stat-label";
    confidenceLabel.textContent = "Confidence";
    const confidenceValue = document.createElement("span");
    confidenceValue.className = "log-stat-value";
    confidenceValue.textContent = formatConfidence(entry.confidence);
    confidenceStat.appendChild(confidenceLabel);
    confidenceStat.appendChild(confidenceValue);
    stats.appendChild(countStat);
    stats.appendChild(confidenceStat);

    const trendStat = document.createElement("div");
    trendStat.className = "log-stat log-trend";
    const trendLabel = document.createElement("span");
    trendLabel.className = "log-stat-label";
    const trendText = formatTrendPercent(entry.daily_counts);
    trendLabel.textContent = trendText ? `30-Day Trend (${trendText})` : "30-Day Trend";
    const trendCanvas = document.createElement("canvas");
    trendCanvas.className = "log-trend-canvas";
    trendCanvas.width = 150;
    trendCanvas.height = 28;
    trendStat.appendChild(trendLabel);
    trendStat.appendChild(trendCanvas);
    stats.appendChild(trendStat);

    const detection = document.createElement("div");
    detection.className = "log-detection";
    detection.appendChild(time);
    detection.appendChild(species);

    row.appendChild(iconCell);
    row.appendChild(detection);
    row.appendChild(stats);
    row.appendChild(remove);

    elements.logBody.appendChild(row);

    drawTrend(trendCanvas, entry.daily_counts);
  });
}

function formatTrendPercent(counts) {
  if (!Array.isArray(counts) || counts.length < 14) {
    return "";
  }
  const windowSize = 7;
  const recent = counts.slice(-windowSize);
  const previous = counts.slice(-windowSize * 2, -windowSize);
  const sumRecent = recent.reduce((acc, value) => acc + (Number(value) || 0), 0);
  const sumPrev = previous.reduce((acc, value) => acc + (Number(value) || 0), 0);
  if (sumPrev <= 0) {
    if (sumRecent <= 0) {
      return "0%";
    }
    return "+100%";
  }
  const change = ((sumRecent - sumPrev) / sumPrev) * 100;
  const rounded = Math.round(change);
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded}%`;
}

function drawTrend(canvas, counts) {
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  const values = Array.isArray(counts) && counts.length > 0 ? counts : [];
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.clientHeight || canvas.height;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(width * ratio));
  canvas.height = Math.max(1, Math.floor(height * ratio));
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (values.length === 0) {
    return;
  }
  const padding = { top: 0, bottom: 0, left: 0, right: 0 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const maxValue = Math.max(1, ...values);
  const step = innerWidth / Math.max(1, values.length - 1);

  ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top + innerHeight);
  ctx.lineTo(padding.left + innerWidth, padding.top + innerHeight);
  ctx.stroke();

  ctx.strokeStyle = "rgba(255, 138, 76, 0.85)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = padding.left + step * index;
    const y = padding.top + innerHeight - (value / maxValue) * innerHeight;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
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

function drawActivityCurve(points, todayPoints) {
  if (!elements.activityCurve) {
    return;
  }
  const canvas = elements.activityCurve;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.clientHeight || canvas.height;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(width * ratio));
  canvas.height = Math.max(1, Math.floor(height * ratio));
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);

  const padding = { top: 10, right: 12, bottom: 18, left: 12 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;

  const binCount = 48;
  const values = Array.isArray(points) && points.length === binCount ? points : new Array(binCount).fill(0);
  const todayValues = Array.isArray(todayPoints) && todayPoints.length === binCount ? todayPoints : new Array(binCount).fill(null);
  const maxValue = Math.max(1, ...values, ...todayValues);
  const now = new Date();
  const hourFraction = now.getHours() + now.getMinutes() / 60 + now.getSeconds() / 3600;
  const windowHours = 5;
  const halfWindow = windowHours / 2;
  const stepX = innerWidth / 240;

  const sampleAt = (hourValue, series) => {
    const normalized = ((hourValue % 24) + 24) % 24;
    const bin = normalized * 2;
    const base = Math.floor(bin);
    const next = (base + 1) % binCount;
    const mix = bin - base;
    const baseValue = series[base];
    const nextValue = series[next];
    if (baseValue == null || nextValue == null) {
      return null;
    }
    return baseValue * (1 - mix) + nextValue * mix;
  };
  const sampleAtToday = (hourValue, series, cutoffHour) => {
    if (hourValue > cutoffHour) {
      return null;
    }
    const normalized = ((hourValue % 24) + 24) % 24;
    const bin = normalized * 2;
    const base = Math.floor(bin);
    const next = (base + 1) % binCount;
    const mix = bin - base;
    const baseValue = series[base];
    const nextValue = series[next];
    if (baseValue == null) {
      return null;
    }
    if (nextValue == null) {
      return baseValue;
    }
    return baseValue * (1 - mix) + nextValue * mix;
  };

  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top + innerHeight);
  ctx.lineTo(padding.left + innerWidth, padding.top + innerHeight);
  ctx.stroke();

  const tickCount = 5;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= tickCount; i += 1) {
    const x = padding.left + (innerWidth / tickCount) * i;
    const tickHeight = i % 2 === 0 ? 10 : 6;
    ctx.beginPath();
    ctx.moveTo(x, padding.top + innerHeight);
    ctx.lineTo(x, padding.top + innerHeight + tickHeight);
    ctx.stroke();
  }


  ctx.strokeStyle = "rgba(255, 138, 76, 0.85)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  const samples = 240;
  for (let i = 0; i <= samples; i += 1) {
    const t = i / samples;
    const hour = hourFraction - halfWindow + t * windowHours;
    const value = sampleAt(hour, values);
    const x = padding.left + stepX * i;
    const y = padding.top + innerHeight - (value / maxValue) * innerHeight;
    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      const prevHour = hourFraction - halfWindow + (i - 1) / samples * windowHours;
      const prevValue = sampleAt(prevHour, values);
      const prevX = padding.left + stepX * (i - 1);
      const prevY = padding.top + innerHeight - (prevValue / maxValue) * innerHeight;
      const controlX = (prevX + x) / 2;
      ctx.quadraticCurveTo(controlX, prevY, x, y);
    }
  }
  ctx.stroke();

  ctx.fillStyle = "rgba(255, 138, 76, 0.15)";
  ctx.lineTo(padding.left + innerWidth, padding.top + innerHeight);
  ctx.lineTo(padding.left, padding.top + innerHeight);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = "rgba(76, 201, 240, 0.85)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i <= samples; i += 1) {
    const t = i / samples;
    const hour = hourFraction - halfWindow + t * windowHours;
    if (hour > hourFraction) {
      break;
    }
    const value = sampleAtToday(hour, todayValues, hourFraction);
    const x = padding.left + stepX * i;
    if (value == null) {
      continue;
    }
    const y = padding.top + innerHeight - (value / maxValue) * innerHeight;
    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      const prevHour = hourFraction - halfWindow + (i - 1) / samples * windowHours;
      const prevValue = sampleAtToday(prevHour, todayValues, hourFraction);
      if (prevValue == null) {
        ctx.moveTo(x, y);
      } else {
        const prevX = padding.left + stepX * (i - 1);
        const prevY = padding.top + innerHeight - (prevValue / maxValue) * innerHeight;
        const controlX = (prevX + x) / 2;
        ctx.quadraticCurveTo(controlX, prevY, x, y);
      }
    }
  }
  ctx.stroke();

  const markerX = padding.left + innerWidth / 2;
  const markerValue = sampleAt(hourFraction, values);
  const markerY = padding.top + innerHeight - (markerValue / maxValue) * innerHeight;
  ctx.strokeStyle = "rgba(255, 209, 102, 0.8)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(markerX, padding.top);
  ctx.lineTo(markerX, padding.top + innerHeight);
  ctx.stroke();
  ctx.fillStyle = "rgba(255, 209, 102, 0.9)";
  ctx.beginPath();
  ctx.arc(markerX, markerY, 4, 0, Math.PI * 2);
  ctx.fill();
}

async function loadActivityCurve() {
  if (isUiSuspended()) {
    return;
  }
  try {
    const response = await fetch("/api/log/activity?days=10", { cache: "no-store" });
    const data = await response.json();
    activityCurveData = Array.isArray(data.points) ? data.points : [];
    activityTodayData = Array.isArray(data.today_points) ? data.today_points : [];
    drawActivityCurve(activityCurveData, activityTodayData);
  } catch (error) {
    return;
  }
}
async function loadLog() {
  if (Date.now() < logSuspendUntil) {
    return;
  }
  if (isUiSuspended()) {
    return;
  }
  try {
    const response = await fetch("/api/log/summary", { cache: "no-store" });
    const data = await response.json();
    if (typeof data.log_revision === "number") {
      if (data.log_revision === lastLogRevision) {
        return;
      }
      lastLogRevision = data.log_revision;
    }
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
  if (isUiSuspended()) {
    return;
  }
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
  if (isUiSuspended()) {
    return;
  }
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

async function uploadIcon(species, file) {
  if (!species || !file) {
    return;
  }
  logSuspendUntil = Math.max(logSuspendUntil, Date.now() + 15000);
  setStatus("Uploading icon...");
  const form = new FormData();
  form.append("species", species);
  form.append("icon", file);
  try {
    const response = await fetch("/api/icon/upload", {
      method: "POST",
      body: form,
    });
    const result = await response.json();
    if (result.ok) {
      setStatus("Icon updated");
      logSuspendUntil = 0;
      loadLog();
    } else {
      setStatus(result.error || "Icon upload failed");
    }
  } catch (error) {
    setStatus("Icon upload failed");
  }
}

async function removeIcon(species) {
  if (!species) {
    return;
  }
  logSuspendUntil = Math.max(logSuspendUntil, Date.now() + 8000);
  setStatus("Removing icon...");
  try {
    const response = await fetch("/api/icon/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ species }),
    });
    const result = await response.json();
    if (result.ok) {
      setStatus("Icon removed");
      logSuspendUntil = 0;
      loadLog();
    } else {
      setStatus(result.error || "Remove failed");
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

async function verifyWeatherSettings(expectedLocation, expectedUnit) {
  try {
    const response = await fetch("/api/weather/settings", { cache: "no-store" });
    if (!response.ok) {
      return { ok: false, reason: "weather endpoint unavailable" };
    }
    const data = await response.json();
    const savedLocation = String(data.weather_location || "").trim();
    const savedUnit = String(data.weather_unit || "").trim().toLowerCase();
    const locationMatches = savedLocation === expectedLocation;
    const unitMatches = savedUnit === expectedUnit;
    return { ok: locationMatches && unitMatches, reason: "mismatch" };
  } catch (error) {
    return { ok: false, reason: "weather endpoint unavailable" };
  }
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
  const weatherLocationValue = elements.weatherLocation
    ? elements.weatherLocation.value.trim()
    : "";
  const weatherUnitValue = elements.weatherUnit
    ? String(elements.weatherUnit.value || "fahrenheit").toLowerCase()
    : "fahrenheit";
  const httpPortValue = normalizePort(elements.httpPort ? elements.httpPort.value : currentHttpPort);

  const payload = {
    http_port: httpPortValue,
    input_mode: getSelectedMode(),
    rtmp_url: elements.streamUrl.value.trim(),
    input_device: deviceValue,
    min_confidence: Number(elements.minConfidence.value),
    segment_seconds: Number(elements.segmentSeconds.value),
    silence_threshold_db: Number.isFinite(thresholdValue) ? thresholdValue : -45,
    silence_min_seconds: Number.isFinite(minActiveValue) ? minActiveValue : 0.2,
    overlay_hold_seconds: Number.isFinite(overlayHoldValue) ? overlayHoldValue : 60,
    overlay_sticky: Boolean(elements.overlaySticky && elements.overlaySticky.checked),
    location: weatherLocationValue || "Stream",
    latitude: coords.lat,
    longitude: coords.lon,
    week: weekValue,
    auto_week: elements.autoWeek.checked,
    weather_location: weatherLocationValue || "YOUR_ZIP",
    weather_unit: weatherUnitValue === "celsius" ? "celsius" : "fahrenheit",
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
      if (Array.isArray(result.changed) && result.changed.includes("http_port")) {
        setStatus("Saved. Restart server to apply the new port.");
        renderOverlayUrls();
        return;
      }
      const verification = await verifyWeatherSettings(
        payload.weather_location,
        payload.weather_unit
      );
      if (verification.ok) {
        setStatus("Saved");
      } else {
        setStatus("Saved, but weather settings were not applied by server (restart required)");
      }
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

async function restartServer() {
  setStatus("Restarting server...");
  const targetPort = normalizePort(elements.httpPort ? elements.httpPort.value : currentHttpPort);
  try {
    const response = await fetch("/api/restart/server", { method: "POST" });
    const result = await response.json();
    if (!result.ok) {
      setStatus("Server restart failed");
      return;
    }
    setStatus("Server restarting...");
    const nextUrl = `${baseUrlForPort(targetPort)}/settings`;
    setTimeout(() => {
      window.location.href = nextUrl;
    }, 1500);
  } catch (error) {
    setStatus("Server restart failed");
  }
}

function logoutSettings() {
  setStatus("Logging out...");
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", "/api/settings", true, "logout", "logout");
    xhr.withCredentials = true;
    xhr.onreadystatechange = () => {
      if (xhr.readyState !== 4) {
        return;
      }
      setTimeout(() => {
        window.location.href = "/settings";
      }, 100);
    };
    xhr.onerror = () => {
      setTimeout(() => {
        window.location.href = "/settings";
      }, 100);
    };
    xhr.send();
  } catch (error) {
    window.location.href = "/settings";
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
  if (elements.httpPort) {
    elements.httpPort.addEventListener("input", renderOverlayUrls);
    elements.httpPort.addEventListener("change", renderOverlayUrls);
  }
  if (elements.copyOverlayUrl) {
    elements.copyOverlayUrl.addEventListener("click", async () => {
      const ok = await copyToClipboard(elements.overlayUrl ? elements.overlayUrl.value : "");
      setStatus(ok ? "Overlay URL copied" : "Copy failed");
    });
  }
  if (elements.copySettingsUrl) {
    elements.copySettingsUrl.addEventListener("click", async () => {
      const ok = await copyToClipboard(elements.settingsUrl ? elements.settingsUrl.value : "");
      setStatus(ok ? "Settings URL copied" : "Copy failed");
    });
  }
  if (elements.copyWeatherOverlayUrl) {
    elements.copyWeatherOverlayUrl.addEventListener("click", async () => {
      const ok = await copyToClipboard(elements.weatherOverlayUrl ? elements.weatherOverlayUrl.value : "");
      setStatus(ok ? "Weather overlay URL copied" : "Copy failed");
    });
  }
  elements.refreshDevices.addEventListener("click", loadInputs);
  elements.restartCapture.addEventListener("click", restartCapture);
  if (elements.restartServer) {
    elements.restartServer.addEventListener("click", restartServer);
  }
  if (elements.logoutSettings) {
    elements.logoutSettings.addEventListener("click", logoutSettings);
  }
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

window.addEventListener("focus", () => {
  uiSuspendUntil = 0;
});

applyPanelPreferences();
bindEvents();
renderOverlayUrls();
loadInputs().then(loadSettings);
updateSortIndicators();
updateActivityFilterButtons();
loadLog();
loadActivity();
loadQueue();
loadActivityCurve();
setInterval(loadLog, 2000);
setInterval(loadActivity, 2000);
setInterval(loadQueue, 2000);
setInterval(loadActivityCurve, 30000);
setInterval(() => drawActivityCurve(activityCurveData, activityTodayData), 1000);
window.addEventListener("resize", () => drawActivityCurve(activityCurveData, activityTodayData));

const DATA_URL = "data/latest.json";
const POLL_MS = 2000;
const DEFAULT_OPEN_MS = 60000;
const ANIMATION_MS = 1000;
const BETWEEN_MS = 1000;

const elements = {
  overlay: document.getElementById("overlay"),
  dockSpeciesText: document.getElementById("dock-species-text"),
  dockLatin: document.getElementById("dock-latin"),
  dockConfidence: document.getElementById("dock-confidence"),
  dockTimes: document.getElementById("dock-times"),
  dockTime: document.getElementById("dock-time"),
  dockCount: document.getElementById("dock-count"),
  speciesText: document.getElementById("species-text"),
  latin: document.getElementById("latin"),
  updated: document.getElementById("updated"),
  confidence: document.getElementById("confidence"),
  status: document.getElementById("status"),
  clip: document.getElementById("clip"),
  timesHeard: document.getElementById("times-heard"),
  confidenceLabel: document.getElementById("confidence-label"),
};

let lastDetection = loadPersistedDetection();
let currentDisplay = lastDetection;
let lastRevision = lastDetection?.log_revision ?? null;
let lastQueuedRevision = lastRevision;
let openDurationMs = DEFAULT_OPEN_MS;
let lastSpeciesRevision = null;
const detectionQueue = [];
let openStartAt = 0;
let closeStartAt = 0;
let closeEndAt = 0;
let nextReadyAt = 0;
let overlaySticky = false;

function formatTime(isoString) {
  if (!isoString) {
    return "--";
  }
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatConfidence(value) {
  if (value === null || value === undefined) {
    return "--";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "--";
  }
  const percent = numeric > 1 ? numeric : numeric * 100;
  return Math.round(percent);
}

function loadPersistedDetection() {
  try {
    const raw = localStorage.getItem("birdnet_last_detection");
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    return hasDetection(parsed) ? parsed : null;
  } catch (error) {
    return null;
  }
}

function persistDetection(data) {
  try {
    localStorage.setItem("birdnet_last_detection", JSON.stringify(data));
  } catch (error) {
    return;
  }
}

function clearPersistedDetection() {
  try {
    localStorage.removeItem("birdnet_last_detection");
  } catch (error) {
    return;
  }
}

function sourceLabel(data) {
  if (!data || !data.stream_url) {
    return "Audio input not set";
  }
  if (data.status === "error") {
    return "Audio input error";
  }
  if (data.status === "listening") {
    return "Audio input active";
  }
  return "Audio input idle";
}

function setOverlayDocked(isDocked) {
  elements.overlay.classList.toggle("docked", isDocked);
}

function setListeningState(isListening) {
  elements.overlay.classList.toggle("is-listening", isListening);
}

function hasDetection(data) {
  return Array.isArray(data?.top_predictions) && data.top_predictions.length > 0;
}

function updateDock(detection, fallback) {
  const persisted = fallback?.last_detection || null;
  const active = detection || persisted || null;
  const species = active?.species || "Listening";
  const confidenceValue = formatConfidence(active?.confidence);
  const confidenceText =
    active && confidenceValue !== "--" ? confidenceValue + "%" : "--";
  const scientific = active?.scientific_name || "--";
  const lastHeard = fallback?.last_heard || active?.timestamp || null;
  elements.dockSpeciesText.textContent = species;
  if (elements.dockLatin) {
    elements.dockLatin.textContent = scientific;
  }
  elements.dockConfidence.textContent = confidenceText;
  if (elements.dockTimes) {
    const timesHeard = Number(active?.times_heard ?? fallback?.times_heard ?? 0);
    elements.dockTimes.textContent = Number.isFinite(timesHeard)
      ? `${timesHeard}x heard`
      : "0x heard";
  }
  elements.dockTime.textContent = lastHeard ? formatTime(lastHeard) : "--";
}

function updateSpeciesCount(data) {
  if (!elements.dockCount) {
    return;
  }
  const count = Number(data?.species_count);
  if (!Number.isFinite(count)) {
    elements.dockCount.textContent = "(-- species)";
    return;
  }
  const label = count === 1 ? "1 species" : `${count} species`;
  elements.dockCount.textContent = `(${label})`;
}

async function refreshSpeciesCount() {
  try {
    const response = await fetch("/api/log/summary", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const count = Number(data?.species_count);
    if (Number.isFinite(count)) {
      updateSpeciesCount({ species_count: count });
    }
  } catch (error) {
    return;
  }
}

function setIdleState(message) {
  elements.speciesText.textContent = "Listening";
  elements.latin.textContent = "--";
  elements.updated.textContent = "--";
  elements.confidence.textContent = "--";
  elements.status.textContent = "Detection Confidence";
  elements.clip.textContent = "--s";
  elements.timesHeard.textContent = "0";
  if (elements.confidenceLabel) {
    elements.confidenceLabel.textContent = "--";
  }
  setOverlayDocked(true);
  setListeningState(true);
  updateDock(currentDisplay || lastDetection, null);
}

function scheduleDisplay(detection, now) {
  currentDisplay = detection;
  openStartAt = now;
  if (overlaySticky) {
    closeStartAt = Number.POSITIVE_INFINITY;
    closeEndAt = Number.POSITIVE_INFINITY;
    nextReadyAt = now + BETWEEN_MS;
  } else {
    closeStartAt = openStartAt + openDurationMs;
    closeEndAt = closeStartAt + ANIMATION_MS;
    nextReadyAt = closeEndAt + BETWEEN_MS;
  }
  persistDetection(currentDisplay);
  setOverlayDocked(false);
  setListeningState(false);
}

function applyDetection(detectionData, sourceData) {
  const confidenceValue = formatConfidence(detectionData.confidence);
  elements.speciesText.textContent = detectionData.species || "No detection";
  elements.latin.textContent = detectionData.scientific_name || "--";
  const lastHeard = sourceData?.last_heard || detectionData.timestamp;
  elements.updated.textContent = formatTime(lastHeard);
  elements.confidence.textContent = confidenceValue === "--" ? "--" : confidenceValue;
  elements.status.textContent = "Detection Confidence";
  elements.clip.textContent = detectionData.clip_seconds ? detectionData.clip_seconds + "s" : "--s";
  const timesHeard = Number(detectionData.times_heard ?? sourceData?.times_heard ?? 0);
  elements.timesHeard.textContent = Number.isFinite(timesHeard) ? String(timesHeard) : "0";
  if (elements.confidenceLabel) {
    elements.confidenceLabel.textContent =
      confidenceValue === "--" ? "--" : confidenceValue + "%";
  }
}

async function refresh() {
  try {
    const response = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!response.ok) {
      throw new Error("No data");
    }
    const data = await response.json();
    const now = Date.now();
    const serverLast = data.last_detection || null;
    const serverRevision = data.log_revision ?? null;
    const holdSeconds = Number(data.overlay_hold_seconds);
    if (Number.isFinite(holdSeconds) && holdSeconds > 0) {
      openDurationMs = holdSeconds * 1000;
    } else {
      openDurationMs = DEFAULT_OPEN_MS;
    }
    overlaySticky = Boolean(data.overlay_sticky);
    const revisionChanged = serverRevision !== null && serverRevision !== lastRevision;
    const serverLastWithRevision = serverLast
      ? { ...serverLast, log_revision: serverRevision }
      : null;
    if (serverRevision !== null && serverRevision !== lastSpeciesRevision) {
      lastSpeciesRevision = serverRevision;
      refreshSpeciesCount();
    }

    if (hasDetection(data)) {
      const detectionWithRevision = {
        ...data,
        log_revision: serverRevision ?? data.log_revision ?? null,
      };
      if (
        currentDisplay
        && detectionWithRevision.species
        && detectionWithRevision.species === currentDisplay.species
      ) {
        scheduleDisplay(detectionWithRevision, now);
      } else if (serverRevision !== null) {
        if (serverRevision !== lastQueuedRevision) {
          detectionQueue.push(detectionWithRevision);
          lastQueuedRevision = serverRevision;
        }
      } else if (
        !currentDisplay
        || new Date(detectionWithRevision.timestamp) > new Date(currentDisplay.timestamp || 0)
      ) {
        detectionQueue.push(detectionWithRevision);
      }
      lastDetection = detectionWithRevision;
      lastRevision = detectionWithRevision.log_revision ?? lastRevision;
    }

    if (serverLastWithRevision) {
      if (
        !lastDetection
        || new Date(serverLastWithRevision.timestamp) > new Date(lastDetection.timestamp || 0)
      ) {
        lastDetection = serverLastWithRevision;
        lastRevision = serverRevision;
      }
    } else if (revisionChanged && !hasDetection(data)) {
      lastDetection = null;
      lastRevision = serverRevision;
    }

    if (!currentDisplay && detectionQueue.length > 0 && (!overlaySticky && now >= nextReadyAt)) {
      scheduleDisplay(detectionQueue.shift(), now);
    }

    if (overlaySticky && detectionQueue.length > 0 && now >= nextReadyAt) {
      scheduleDisplay(detectionQueue.shift(), now);
    }

    if (currentDisplay && (overlaySticky || now < closeStartAt)) {
      applyDetection(currentDisplay, data);
      setOverlayDocked(false);
      setListeningState(false);
      updateSpeciesCount(data);
      updateDock(currentDisplay, data);
      return;
    }

    if (currentDisplay && !overlaySticky && now < closeEndAt) {
      applyDetection(currentDisplay, data);
      updateSpeciesCount(data);
      updateDock(currentDisplay, data);
      setListeningState(false);
      setOverlayDocked(true);
      return;
    }

    if (currentDisplay && !overlaySticky && now >= closeEndAt) {
      currentDisplay = null;
    }

    if (!currentDisplay && data.last_detection) {
      lastDetection = { ...data.last_detection, log_revision: data.log_revision ?? data.last_detection.log_revision };
    }
    if (lastDetection) {
      applyDetection(lastDetection, data);
      updateSpeciesCount(data);
      updateDock(lastDetection, data);
      setListeningState(false);
      setOverlayDocked(true);
    } else {
      setIdleState("Awaiting detections");
      updateSpeciesCount(data);
      updateDock(null, data);
    }
  } catch (error) {
    if (currentDisplay || lastDetection) {
      updateDock(currentDisplay || lastDetection, null);
      setOverlayDocked(true);
      setListeningState(false);
    } else {
      setIdleState("Waiting for BirdNET");
      updateDock(null, null);
    }
  }
}

setIdleState("Waiting for BirdNET");
refresh();
setInterval(refresh, POLL_MS);

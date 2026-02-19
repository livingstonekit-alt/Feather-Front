const DATA_URL = "data/latest.json";
const POLL_MS = 2000;
const DEFAULT_OPEN_MS = 60000;
const BETWEEN_MS = 1000;

const elements = {
  overlay: document.getElementById("overlay"),
  slides: Array.from(document.querySelectorAll(".slide")),
};

const activitySparks = Array.from(document.querySelectorAll(".activity-spark"));
const activityCanvases = Array.from(document.querySelectorAll(".activity-canvas"));

let activityPoints = [];

const slideFields = elements.slides.map((slide) => ({
  iconWrap: slide.querySelector(".species-icon"),
  iconImage: slide.querySelector(".species-icon-img"),
  species: slide.querySelector(".slide-species-text"),
  latin: slide.querySelector(".slide-latin-text"),
  speciesCardValue: slide.querySelector(".species-card-value"),
  time: slide.querySelector(".time-pill"),
  heard: slide.querySelector(".heard-pill"),
  confidence: slide.querySelector(".confidence-value"),
}));

let activeSlideIndex = 0;
let openDurationMs = DEFAULT_OPEN_MS;
let overlaySticky = false;
let pendingDetection = null;
let nextReadyAt = 0;
let displayedRevision = null;
let isAnimating = false;
let lastSpeciesRank = null;
let lastSpeciesCount = null;

function formatRank(rankValue, totalValue) {
  const rank = Number(rankValue);
  const total = Number(totalValue);
  if (!Number.isFinite(rank) || !Number.isFinite(total) || rank <= 0 || total <= 0) {
    return "--";
  }
  return `${Math.round(rank)}/${Math.round(total)}`;
}

function formatAgo(value) {
  if (!value) {
    return "--:--:--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function formatConfidence(value) {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "--";
  }
  const percent = numeric > 1 ? numeric : numeric * 100;
  return Math.round(percent) + "%";
}

function updateConfidenceDisplay(fields, value) {
  if (!fields || !fields.confidence) {
    return;
  }
  const numeric = formatConfidence(value);
  fields.confidence.textContent = numeric === "--" ? "--" : numeric.replace("%", "");
}

function renderSlide(index, detection) {
  const fields = slideFields[index];
  if (!fields) {
    return;
  }
  const iconUrl = detection.icon_url || "";
  if (fields.iconWrap && fields.iconImage) {
    if (iconUrl) {
      const cacheBust = `${iconUrl}${iconUrl.includes("?") ? "&" : "?"}t=${Date.now()}`;
      fields.iconImage.src = cacheBust;
      fields.iconImage.alt = detection.species || "Bird icon";
      fields.iconWrap.classList.remove("is-hidden");
    } else {
      fields.iconImage.removeAttribute("src");
      fields.iconImage.alt = "";
      fields.iconWrap.classList.add("is-hidden");
    }
  }
  fields.species.textContent = detection.species || "Listening";
  fields.latin.textContent = detection.scientific_name || "--";
  updateTimeDisplay(fields, detection);
  const heardValue = Number(detection.times_heard ?? 0);
  fields.heard.textContent = Number.isFinite(heardValue) ? `${heardValue}x heard` : "0x heard";
  if (fields.speciesCardValue) {
    const rankValue = detection.species_rank ?? lastSpeciesRank;
    const countValue = detection.species_count ?? lastSpeciesCount;
    fields.speciesCardValue.textContent = formatRank(rankValue, countValue);
  }
  updateConfidenceDisplay(fields, detection.confidence);
}

function updateTimeDisplay(fields, detection) {
  if (!fields || !fields.time) {
    return;
  }
  const ago = formatAgo(detection.timestamp);
  fields.time.textContent = `Last call ${ago}`;
}

function updateActiveAgo() {
  if (!currentDisplay) {
    return;
  }
  const fields = slideFields[activeSlideIndex];
  if (!fields) {
    return;
  }
  updateTimeDisplay(fields, currentDisplay);
}

function updateActivityPills() {
  if (!activitySparks.length) {
    return;
  }
  const now = new Date();
  const seconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
  const progress = Math.min(1, Math.max(0, seconds / 86400));
  const min = 2;
  const max = 98;
  const percent = `${(min + (max - min) * progress).toFixed(2)}%`;
  activitySparks.forEach((spark) => spark.style.setProperty("--activity-progress", percent));
}

function drawActivitySpark() {
  if (!activityCanvases.length) {
    return;
  }
  const binCount = 24;
  const values = Array.isArray(activityPoints) && activityPoints.length === binCount
    ? activityPoints
    : new Array(binCount).fill(0);
  const maxValue = Math.max(1, ...values);
  activityCanvases.forEach((canvas) => {
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

    const padding = { top: 4, bottom: 4, left: 4, right: 4 };
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const step = innerWidth / (values.length - 1);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
    ctx.lineWidth = 1;
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

    ctx.strokeStyle = "rgba(255, 138, 76, 0.6)";
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
  });
}

async function loadActivity() {
  try {
    const response = await fetch("/api/log/activity?days=10", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const rawPoints = Array.isArray(data.points) ? data.points : [];
    if (rawPoints.length === 48) {
      const hourly = [];
      for (let idx = 0; idx < rawPoints.length; idx += 2) {
        const first = rawPoints[idx] ?? 0;
        const second = rawPoints[idx + 1] ?? 0;
        hourly.push(Math.round(((first + second) / 2) * 100) / 100);
      }
      activityPoints = hourly;
    } else if (rawPoints.length === 24) {
      activityPoints = rawPoints;
    } else {
      activityPoints = [];
    }
    drawActivitySpark();
  } catch (error) {
    return;
  }
}

function animateSlide(targetIndex) {
  if (isAnimating) {
    return;
  }
  isAnimating = true;
  const outgoing = elements.slides[activeSlideIndex];
  const incoming = elements.slides[targetIndex];
  if (!outgoing || !incoming || outgoing === incoming) {
    isAnimating = false;
    return;
  }
  incoming.classList.remove("is-visible", "is-exiting");
  incoming.classList.add("is-reset");
  incoming.offsetHeight;
  incoming.classList.remove("is-reset");

  outgoing.classList.remove("is-visible");
  outgoing.classList.add("is-exiting");
  const finish = () => {
    outgoing.classList.remove("is-exiting");
    outgoing.classList.add("is-reset");
    outgoing.offsetHeight;
    outgoing.classList.remove("is-reset");
    incoming.classList.add("is-visible");
    activeSlideIndex = targetIndex;
    isAnimating = false;
  };
  outgoing.addEventListener("transitionend", finish, { once: true });
  setTimeout(() => {
    if (isAnimating) {
      finish();
    }
  }, 1100);
}

function scheduleDisplay(detection, now) {
  if (!detection) {
    return;
  }
  const currentSpecies = currentDisplay?.species;
  if (currentSpecies && currentSpecies === detection.species) {
    renderSlide(activeSlideIndex, detection);
    displayedRevision = detection.log_revision ?? displayedRevision;
    currentDisplay = detection;
    nextReadyAt = now + openDurationMs + BETWEEN_MS;
    return;
  }
  const nextIndex = (activeSlideIndex + 1) % elements.slides.length;
  renderSlide(nextIndex, detection);
  animateSlide(nextIndex);
  displayedRevision = detection.log_revision ?? displayedRevision;
  currentDisplay = detection;
  if (overlaySticky) {
    nextReadyAt = now + BETWEEN_MS;
  } else {
    nextReadyAt = now + openDurationMs + BETWEEN_MS;
  }
}

function queueDetection(detection, revision) {
  if (!detection || revision == null) {
    return;
  }
  if (displayedRevision === revision) {
    return;
  }
  pendingDetection = { ...detection, log_revision: revision };
}

function tryAdvance(now) {
  if (!pendingDetection) {
    return;
  }
  if (overlaySticky || now >= nextReadyAt) {
    const next = pendingDetection;
    pendingDetection = null;
    scheduleDisplay(next, now);
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
    const rawHold = Number(data.overlay_hold_seconds);
    const configuredHold = Number.isFinite(rawHold) && rawHold > 0 ? rawHold : DEFAULT_OPEN_MS / 1000;
    openDurationMs = configuredHold * 1000;
    overlaySticky = Boolean(data.overlay_sticky);
    const serverDetection = data.last_detection;
    const serverRevision = data.log_revision ?? serverDetection?.log_revision ?? null;
    const speciesCountValue = Number.isFinite(Number(data.species_count))
      ? Number(data.species_count)
      : serverDetection?.species_count;
    const speciesRankValue = Number.isFinite(Number(serverDetection?.species_rank))
      ? Number(serverDetection.species_rank)
      : Number.isFinite(Number(data.species_rank))
        ? Number(data.species_rank)
        : null;
    if (Number.isFinite(Number(speciesCountValue))) {
      lastSpeciesCount = Number(speciesCountValue);
    }
    if (Number.isFinite(Number(speciesRankValue))) {
      lastSpeciesRank = Number(speciesRankValue);
    }
    if (serverDetection && serverRevision != null) {
      const detectionWithCount = {
        ...serverDetection,
        species_count: speciesCountValue,
        species_rank: speciesRankValue,
      };
      queueDetection(detectionWithCount, serverRevision);
    }
    tryAdvance(now);
    if (serverDetection && (!currentDisplay || currentDisplay.species === "Listening")) {
      scheduleDisplay(
        {
          ...serverDetection,
          species_count: speciesCountValue,
          species_rank: speciesRankValue,
        },
        now
      );
    }
  } catch (error) {
    /* swallow */
  }
}

let currentDisplay = null;

scheduleDisplay({ species: "Listening", scientific_name: "--", timestamp: null, times_heard: 0, confidence: null }, Date.now());
refresh();
setInterval(refresh, POLL_MS);
updateActivityPills();
setInterval(updateActivityPills, 1000);
loadActivity();
setInterval(loadActivity, 60000);
window.addEventListener("resize", drawActivitySpark);
setInterval(updateActiveAgo, 1000);

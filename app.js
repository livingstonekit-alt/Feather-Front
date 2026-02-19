const REFRESH_MINUTES = 1;
const SETTINGS_REFRESH_MS = 60 * 1000;
const DEFAULT_WEATHER_LOCATION = "YOUR_ZIP";
const DEFAULT_WEATHER_UNIT = "fahrenheit";
const SETTINGS_URL_PARAM = new URLSearchParams(window.location.search).get("settings_url");
const HOST_BASE_URL = window.location.hostname
  ? `${window.location.protocol}//${window.location.hostname}:8002/api/weather/settings`
  : "";
const WEATHER_SETTINGS_URLS = SETTINGS_URL_PARAM
  ? [SETTINGS_URL_PARAM]
  : ["/api/weather/settings", HOST_BASE_URL, "http://localhost:8002/api/weather/settings"].filter(Boolean);

const elements = {
  location: document.getElementById("location"),
  locationTag: document.getElementById("location-tag"),
  clock: document.getElementById("clock"),
  date: document.getElementById("date"),
  sunrise: document.getElementById("sunrise"),
  sunset: document.getElementById("sunset"),
  temp: document.getElementById("temp"),
  tempUnit: document.getElementById("temp-unit"),
  condition: document.getElementById("condition"),
  feels: document.getElementById("feels"),
  precip: document.getElementById("precip"),
  humidity: document.getElementById("humidity"),
  wind: document.getElementById("wind"),
  nextHour: document.getElementById("next-hour"),
  tomorrow: document.getElementById("tomorrow"),
};

const WEATHER_CODES = {
  0: "Clear",
  1: "Mainly clear",
  2: "Partly cloudy",
  3: "Overcast",
  45: "Fog",
  48: "Rime fog",
  51: "Light drizzle",
  53: "Drizzle",
  55: "Dense drizzle",
  56: "Freezing drizzle",
  57: "Freezing drizzle",
  61: "Light rain",
  63: "Rain",
  65: "Heavy rain",
  66: "Freezing rain",
  67: "Freezing rain",
  71: "Light snow",
  73: "Snow",
  75: "Heavy snow",
  77: "Snow grains",
  80: "Rain showers",
  81: "Rain showers",
  82: "Violent rain showers",
  85: "Snow showers",
  86: "Snow showers",
  95: "Thunderstorm",
  96: "Thunderstorm w/ hail",
  99: "Thunderstorm w/ hail",
};

let cachedCoords = null;
let lastSettingsFetch = 0;
let weatherConfig = {
  location: DEFAULT_WEATHER_LOCATION,
  unit: DEFAULT_WEATHER_UNIT,
};

function normalizeWeatherUnit(value) {
  const unit = String(value || "").trim().toLowerCase();
  if (unit === "c" || unit === "celsius" || unit === "metric") {
    return "celsius";
  }
  return "fahrenheit";
}

function normalizeWeatherLocation(value) {
  const text = String(value || "").trim();
  return text || DEFAULT_WEATHER_LOCATION;
}

function getTemperatureSymbol() {
  return weatherConfig.unit === "celsius" ? "C" : "F";
}

function getWindDisplayUnit() {
  return weatherConfig.unit === "celsius" ? "km/h" : "mph";
}

function getWindApiUnit() {
  return weatherConfig.unit === "celsius" ? "kmh" : "mph";
}

function updateLocationTag() {
  if (!elements.locationTag) {
    return;
  }
  const query = normalizeWeatherLocation(weatherConfig.location);
  if (/^\d{5}$/.test(query)) {
    elements.locationTag.textContent = "ZIP " + query;
    return;
  }
  elements.locationTag.textContent = query;
}

function updateTemperatureUnitLabel() {
  if (!elements.tempUnit) {
    return;
  }
  elements.tempUnit.textContent = "\u00b0" + getTemperatureSymbol();
}

function applyWeatherSettings(settings) {
  if (!settings || typeof settings !== "object") {
    return;
  }
  const nextLocation = normalizeWeatherLocation(
    settings.weather_location !== undefined ? settings.weather_location : settings.location
  );
  const nextUnit = normalizeWeatherUnit(
    settings.weather_unit !== undefined ? settings.weather_unit : settings.unit
  );
  const locationChanged = nextLocation !== weatherConfig.location;
  weatherConfig = {
    location: nextLocation,
    unit: nextUnit,
  };
  if (locationChanged) {
    cachedCoords = null;
    elements.location.textContent = "Loading " + weatherConfig.location + "...";
  }
  updateLocationTag();
  updateTemperatureUnitLabel();
}

async function refreshWeatherSettings(force = false) {
  const now = Date.now();
  if (!force && now - lastSettingsFetch < SETTINGS_REFRESH_MS) {
    return;
  }
  lastSettingsFetch = now;
  for (const baseUrl of WEATHER_SETTINGS_URLS) {
    try {
      const response = await fetch(baseUrl + "?t=" + now, { cache: "no-store" });
      if (!response.ok) {
        continue;
      }
      const data = await response.json();
      applyWeatherSettings(data);
      return;
    } catch (error) {
      continue;
    }
  }
}

function formatTime(date) {
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDate(date) {
  return date.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function formatShortTime(date) {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function mmToInches(mm) {
  if (mm === undefined || mm === null) return null;
  return mm / 25.4;
}

function windDirection(degrees) {
  const directions = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
  ];
  const index = Math.round(((degrees % 360) / 22.5)) % 16;
  return directions[index];
}

function formatForecastLabel(condition, precipitation) {
  const chance = precipitation === null || precipitation === undefined
    ? ""
    : " " + Math.round(precipitation) + "%";
  return condition + chance;
}

async function getCoordinates() {
  if (cachedCoords) {
    return cachedCoords;
  }

  const query = normalizeWeatherLocation(weatherConfig.location);
  const geoUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
  geoUrl.searchParams.set("name", query);
  geoUrl.searchParams.set("count", "1");
  geoUrl.searchParams.set("language", "en");
  geoUrl.searchParams.set("format", "json");

  const response = await fetch(geoUrl);
  if (!response.ok) {
    throw new Error("Unable to reach geocoding service.");
  }

  const data = await response.json();
  if (!data.results || data.results.length === 0) {
    throw new Error("No location found for " + query + ".");
  }

  const result = data.results[0];
  const state = result.admin1 === "Tennessee" ? "TN" : result.admin1;
  const locationBits = [result.name, state, result.country_code].filter(Boolean);
  elements.location.textContent = locationBits.join(", ");

  cachedCoords = {
    latitude: result.latitude,
    longitude: result.longitude,
  };
  return cachedCoords;
}

async function updateWeather() {
  try {
    await refreshWeatherSettings(false);
    const coords = await getCoordinates();
    const weatherUrl = new URL("https://api.open-meteo.com/v1/forecast");
    weatherUrl.searchParams.set("latitude", coords.latitude);
    weatherUrl.searchParams.set("longitude", coords.longitude);
    weatherUrl.searchParams.set(
      "current",
      "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,relative_humidity_2m"
    );
    weatherUrl.searchParams.set(
      "hourly",
      "temperature_2m,weather_code,precipitation_probability"
    );
    weatherUrl.searchParams.set(
      "daily",
      "temperature_2m_max,temperature_2m_min,weather_code,sunrise,sunset,precipitation_sum"
    );
    weatherUrl.searchParams.set("temperature_unit", weatherConfig.unit);
    weatherUrl.searchParams.set("wind_speed_unit", getWindApiUnit());
    weatherUrl.searchParams.set("timezone", "auto");

    const response = await fetch(weatherUrl);
    if (!response.ok) {
      throw new Error("Unable to reach weather service.");
    }

    const data = await response.json();
    const current = data.current;
    if (!current) {
      throw new Error("Weather data unavailable.");
    }

    const temp = Math.round(current.temperature_2m);
    const feels = Math.round(current.apparent_temperature);
    const humidity = Math.round(current.relative_humidity_2m);
    const wind = Math.round(current.wind_speed_10m);
    const windDir = windDirection(current.wind_direction_10m ?? 0);
    const condition = WEATHER_CODES[current.weather_code] || "Weather code " + current.weather_code;

    const tempSymbol = getTemperatureSymbol();
    const windUnit = getWindDisplayUnit();

    elements.temp.textContent = temp;
    elements.condition.textContent = condition;
    elements.feels.textContent = "Feels like " + feels + " " + tempSymbol;
    elements.humidity.textContent = humidity + "%";
    elements.wind.textContent = wind + " " + windUnit + " " + windDir;

    const hourly = data.hourly;
    if (hourly && Array.isArray(hourly.time)) {
      const now = new Date();
      const nextIndex = hourly.time.findIndex((time) => new Date(time) >= now);
      const baseIndex = nextIndex === -1 ? hourly.time.length - 1 : nextIndex;
      const nextHourCode = hourly.weather_code?.[baseIndex];
      const nextHourPrecip = hourly.precipitation_probability?.[baseIndex];
      const nextHourCondition = WEATHER_CODES[nextHourCode] || "Unknown";

      elements.nextHour.textContent = formatForecastLabel(nextHourCondition, nextHourPrecip);
    }

    const daily = data.daily;
    if (daily && Array.isArray(daily.time) && daily.time.length > 1) {
      const todayIndex = 0;
      const precip = daily.precipitation_sum?.[todayIndex];
      if (precip !== undefined && precip !== null) {
        if (weatherConfig.unit === "celsius") {
          elements.precip.textContent = precip.toFixed(1) + " mm";
        } else {
          const inches = mmToInches(precip);
          elements.precip.textContent = inches.toFixed(2) + " in";
        }
      }
      const sunrise = daily.sunrise?.[todayIndex];
      const sunset = daily.sunset?.[todayIndex];
      if (sunrise && sunset) {
        elements.sunrise.textContent = formatShortTime(new Date(sunrise));
        elements.sunset.textContent = formatShortTime(new Date(sunset));
      }

      const tomorrowIndex = 1;
      const maxTemp = Math.round(daily.temperature_2m_max?.[tomorrowIndex]);
      const minTemp = Math.round(daily.temperature_2m_min?.[tomorrowIndex]);
      const code = daily.weather_code?.[tomorrowIndex];
      const tomorrowCondition = WEATHER_CODES[code] || "Unknown";
      elements.tomorrow.textContent = maxTemp + "/" + minTemp + " " + tomorrowCondition;
    }

    elements.clock.textContent = formatTime(new Date());
    elements.date.textContent = formatDate(new Date());
  } catch (error) {
    elements.condition.textContent = "Weather refresh failed";
    elements.feels.textContent = error.message;
  }
}

function updateClock() {
  const now = new Date();
  elements.clock.textContent = formatTime(now);
  elements.date.textContent = formatDate(now);
}

async function bootstrap() {
  updateLocationTag();
  updateTemperatureUnitLabel();
  await refreshWeatherSettings(true);
  await updateWeather();
  setInterval(updateWeather, REFRESH_MINUTES * 60 * 1000);
  updateClock();
  setInterval(updateClock, 1000);
}

bootstrap();

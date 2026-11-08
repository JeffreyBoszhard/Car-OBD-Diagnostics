// The dashboard is fed by /api/stream (server-sent events). These intervals
// are only used by the fallback path: no EventSource, a dropped stream, or a
// browser whose unit selection differs from the dashboard default.
const FALLBACK_POLL_MS = 1000;
const GAUGE_POLL_MS = 35;
const DISCONNECTED_POLL_MS = 1500;
const STREAM_RETRY_MS = 2500;
const TACH_MIN_DEG = 135;
const TACH_MAX_DEG = 405;
const TACH_MAX_RPM = 8000;
let SPEED_MAX = window.APP_UNITS === "imperial" ? 160 : 260;
const STARTUP_MAX_WAIT_MS = 2200;
const FETCH_TIMEOUT_MS = 4000;
const CHART_SAMPLE_MS = 50;
const PORT_POLL_MS = 3000;

let pollTimer = null;
let liveStream = null;
let streamConnected = false;
let streamFallbackActive = false;
let streamRetryTimer = null;
let gaugePollTimer = null;
let portPollTimer = null;
let isConnected = false;
let currentRpm = 0;
let targetRpm = 0;
let currentSpeed = 0;
let targetSpeed = 0;
let lastGaugeFrameAt = 0;
let vinRefreshPending = false;
let activePage = "dashboard";
let pageTransitionTimer = null;
let isFrozen = false;
let frozenPayload = null;
let startupDismissed = false;
let lastLivePayload = null;
let lastVehicleProfile = {};
let lastRenderedHealthSignature = "";
let lastRenderedChecklistSignature = "";
let lastRenderedErrorSignature = "";
let lastRenderedLiveDataSignature = "";
let lastRenderedVinExtraSignature = "";
let lastRenderedCodes = {
    "stored-codes": "",
    "pending-codes": "",
    "permanent-codes": ""
};
let codeScanPending = false;
let lastDtcStatus = {};
let lastConnectionQualitySignature = "";
let startupFallbackTimer = null;
let graphRecordingActive = false;
let graphRecordingStartedAt = 0;
let graphRecordingSamples = [];
let lastReadinessSignature = "";
let lastFreezeFrameSignature = "";
let lastReportSignature = "";
let lastRenderedPortSignature = "";
let demoMode = false;
let demoPreset = "idle";
let demoPresetRequestPending = false;
let limitedMode = false;
let pollProfile = "balanced";
let simpleMode = false;
let garageSearchTimer = null;
let editingGarageNoteId = null;
const rpmChartPoints = [];
const speedChartPoints = [];
const coolantChartPoints = [];
const voltageChartPoints = [];
const engineLoadChartPoints = [];
const throttleChartPoints = [];
const o2ChartPoints = [];
const CHART_POINT_LIMIT = 60;
const VEHICLE_LOOKUP_HISTORY_KEY = "obd_vehicle_lookup_history";
const VEHICLE_LOOKUP_HISTORY_LIMIT = 10;
const POLL_PROFILE_STORAGE_KEY = "obd_poll_profile";
const demoPresetMeta = new Map();
let lastChartSampleAt = 0;
const RPM_DIRECT_SNAP = 0.4;
const SPEED_DIRECT_SNAP = 0.05;
const RPM_SMOOTH_RESPONSE = 18;
const SPEED_SMOOTH_RESPONSE = 16;
let updateCheckOffline = false;
const chartHoverState = {};
let currentWarningThresholds = {};

const DEFAULT_WARNING_THRESHOLDS = {
    coolant_temp_c: 100,
    oil_temp_c: 125,
    intake_temp_c: 70,
    ecu_voltage_low_v: 11.8,
    ecu_voltage_high_v: 15.0,
    engine_load_pct: 90,
    throttle_pct: 90,
    fuel_trim_abs_pct: 12,
};


function byId(id) {
    return document.getElementById(id);
}

function setText(id, value) {
    const element = byId(id);
    if (!element) return;
    const next = String(value);
    if (element.textContent !== next) {
        element.textContent = next;
    }
}

function tr(key, fallback, vars = {}) {
    const source = window.APP_I18N || {};
    let text = Object.prototype.hasOwnProperty.call(source, key) ? source[key] : fallback;
    if (typeof text !== "string") {
        text = fallback;
    }
    return text.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? `{${name}}`));
}

function setClassName(id, className) {
    const element = byId(id);
    if (!element) return;
    if (element.className !== className) {
        element.className = className;
    }
}

function numberFromValue(value) {
    if (value === null || value === undefined) return 0;

    const match = String(value).replace(",", ".").match(/-?\d+(\.\d+)?/);
    return match ? Number(match[0]) : 0;
}

function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

function mapRange(value, min, max, targetMin, targetMax) {
    const ratio = clamp((value - min) / (max - min), 0, 1);
    return targetMin + ratio * (targetMax - targetMin);
}

function displayValue(value, fallback = "--") {
    if (value === null || value === undefined || value === "") {
        return fallback;
    }

    return String(value);
}

function isUnavailableValue(value) {
    const normalized = String(value ?? "").trim().toLowerCase();
    return normalized === "" || normalized === "--" || normalized === "n/a" || normalized === "null" || normalized === "none";
}

function displayLiveValue(value, fallback = tr("quick_no_data", "No Data")) {
    return isUnavailableValue(value) ? fallback : String(value);
}

function setOfflineIndicator(active) {
    updateCheckOffline = Boolean(active);
    const box = byId("offline-indicator");
    const pill = byId("offline-status-pill");
    if (box) box.hidden = !updateCheckOffline;
    if (pill) {
        pill.textContent = updateCheckOffline ? "Offline mode: update check unavailable" : "Online check: OK";
        pill.className = updateCheckOffline ? "status-pill is-warning" : "status-pill status-info";
    }
}

function updateBusModeIndicator(payload) {
    const pill = byId("obd-bus-mode");
    if (!pill) return;
    const mode = payload?.obd_bus_mode?.mode || "Unknown";
    const confidence = payload?.obd_bus_mode?.confidence || "low";
    pill.textContent = `Bus: ${mode} (${confidence})`;
    pill.title = payload?.obd_bus_mode?.detail || "";
}

function warningThresholdValue(thresholds, key) {
    const value = Number(thresholds?.[key] ?? DEFAULT_WARNING_THRESHOLDS[key]);
    return Number.isFinite(value) ? value : DEFAULT_WARNING_THRESHOLDS[key];
}

function warningMessage(label, value, detail) {
    const cleanLabel = label || tr("sensor", "Sensor");
    return tr("warning_limit_triggered", "{label} warning: {value} triggered {detail}.", {
        label: cleanLabel,
        value: formatLiveValue(value, "warning_value", String(value)),
        detail
    });
}

function getSensorWarning(sensorKey, rawValue, thresholds = currentWarningThresholds, label = "") {
    const key = String(sensorKey || "").toLowerCase();
    const value = numberFromValue(rawValue);
    if (!Number.isFinite(value)) return null;

    if (key === "coolant_temp") {
        const limit = warningThresholdValue(thresholds, "coolant_temp_c");
        return value >= limit ? { detail: `>= ${limit} C`, limit, value, label } : null;
    }
    if (key === "oil_temp") {
        const limit = warningThresholdValue(thresholds, "oil_temp_c");
        return value >= limit ? { detail: `>= ${limit} C`, limit, value, label } : null;
    }
    if (key === "intake_temp") {
        const limit = warningThresholdValue(thresholds, "intake_temp_c");
        return value >= limit ? { detail: `>= ${limit} C`, limit, value, label } : null;
    }
    if (key === "control_voltage" || key === "voltage") {
        const low = warningThresholdValue(thresholds, "ecu_voltage_low_v");
        const high = warningThresholdValue(thresholds, "ecu_voltage_high_v");
        if (value <= low) return { detail: `<= ${low} V`, limit: low, value, label };
        if (value >= high) return { detail: `>= ${high} V`, limit: high, value, label };
        return null;
    }
    if (key === "engine_load") {
        const limit = warningThresholdValue(thresholds, "engine_load_pct");
        return value >= limit ? { detail: `>= ${limit}%`, limit, value, label } : null;
    }
    if (key === "throttle") {
        const limit = warningThresholdValue(thresholds, "throttle_pct");
        return value >= limit ? { detail: `>= ${limit}%`, limit, value, label } : null;
    }
    if (key === "short_fuel_trim_1" || key === "long_fuel_trim_1") {
        const limit = warningThresholdValue(thresholds, "fuel_trim_abs_pct");
        return Math.abs(value) >= limit ? { detail: `outside +/-${limit}%`, limit, value, label } : null;
    }

    return null;
}

function applyWarningStateToMetric(id, warning) {
    const valueElement = byId(id);
    if (!valueElement) return;
    const card = valueElement.closest(".quick-metric");
    const active = Boolean(warning);
    valueElement.classList.toggle("is-warning", active);
    if (card) {
        card.classList.toggle("is-warning", active);
        card.title = active ? warningMessage(warning.label, warning.value, warning.detail) : "";
    }
    valueElement.title = active ? warningMessage(warning.label, warning.value, warning.detail) : "";
}

function applyWarningThresholds(payload) {
    currentWarningThresholds = payload?.runtime_state?.warning_thresholds || {};
    const vehicle = payload?.vehicle || {};
    applyWarningStateToMetric("quick-coolant", getSensorWarning("coolant_temp", vehicle.coolant_temp?.value, currentWarningThresholds, vehicle.coolant_temp?.label || "Coolant temperature"));
    applyWarningStateToMetric("quick-voltage", getSensorWarning("control_voltage", vehicle.control_voltage?.value || vehicle.voltage?.value, currentWarningThresholds, vehicle.control_voltage?.label || vehicle.voltage?.label || "ECU voltage"));
    applyWarningStateToMetric("quick-fuel-trim", getSensorWarning("long_fuel_trim_1", vehicle.long_fuel_trim_1?.value, currentWarningThresholds, vehicle.long_fuel_trim_1?.label || "Long fuel trim"));
    applyWarningStateToMetric("quick-engine-load", getSensorWarning("engine_load", vehicle.engine_load?.value, currentWarningThresholds, vehicle.engine_load?.label || "Engine load"));
}

function formatLiveValue(value, sensorKey, fallback = tr("quick_no_data", "No Data")) {
    if (isUnavailableValue(value)) {
        return fallback;
    }

    const raw = String(value).trim();
    const normalized = raw.replace(/_/g, " ").trim();
    const numericValue = (() => {
        const match = normalized.replace(/,/g, ".").match(/-?\d+(?:\.\d+)?/);
        return match ? Number(match[0]) : null;
    })();
    const lower = normalized.toLowerCase();

    const tupleMatches = [...normalized.matchAll(/['"]([^'"]+)['"]/g)].map((m) => m[1].trim());
    if (sensorKey === "fuel_system" && tupleMatches.length > 0) {
        const unique = [...new Set(tupleMatches)];
        return unique.join(" / ");
    }

    if (sensorKey === "status") {
        if (/^<obd\.OBDResponse\.Status object/.test(raw)) {
            return tr("status_report", "Status report");
        }
        if (/^<.*object.*>$/.test(raw)) {
            return tr("status_object", "Status object");
        }
    }

    if (sensorKey === "runtime" && Number.isFinite(numericValue)) {
        const totalSeconds = Math.max(0, Math.round(numericValue));
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
    }

    if (/\bengine load\b/.test(lower) || sensorKey === "engine_load") {
        if (Number.isFinite(numericValue)) {
            return `${Math.round(numericValue)}%`;
        }
        return normalized.replace(/percent/gi, "%");
    }

    if (/\b(degree\s+celsius|degree\s*\s*celsius|celsius)\b/.test(lower)) {
        return Number.isFinite(numericValue) ? `${Math.round(numericValue)} C` : normalized.replace(/degree[\s_]*celcius/gi, "C").replace(/degree[\s_]*celsius/gi, "C");
    }

    if (/\bvolt\b/.test(lower)) {
        return Number.isFinite(numericValue) ? `${numericValue.toFixed(1)} V` : normalized.replace(/volt/gi, "V");
    }

    if (/\bpercent\b/.test(lower) || lower.includes("%")) {
        return Number.isFinite(numericValue) ? `${numericValue.toFixed(2)}%` : normalized.replace(/percent/gi, "%");
    }

    if (/\bkilopascal\b/.test(lower)) {
        return Number.isFinite(numericValue) ? `${numericValue.toFixed(0)} kPa` : normalized.replace(/kilopascal/gi, "kPa");
    }

    if (/\b(second|seconds)\b/.test(lower)) {
        return Number.isFinite(numericValue) ? `${numericValue.toFixed(0)} s` : normalized.replace(/seconds/gi, "s").replace(/second/gi, "s");
    }

    return normalized;
}

function formatCompactDate(value) {
    const raw = String(value || "").trim();
    if (!/^\d{8}$/.test(raw)) {
        return displayValue(value);
    }
    return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

function buildYearFromDate(value) {
    const raw = String(value || "").trim();
    return /^\d{8}$/.test(raw) ? raw.slice(0, 4) : "";
}

function normalizeVinCandidate(value) {
    const compact = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    const match = compact.match(/[A-HJ-NPR-Z0-9]{17}/);
    return match ? match[0] : "";
}

function normalizePlateCandidate(value) {
    const compact = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (compact.length < 5 || compact.length > 8) {
        return "";
    }
    return compact;
}

function pulseElement(element, className = "pulse") {
    if (!element) return;
    element.classList.remove(className);
    void element.offsetWidth;
    element.classList.add(className);
}

function animateTextSwap(element) {
    if (!element) return;
    element.classList.remove("text-swap");
    void element.offsetWidth;
    element.classList.add("text-swap");
}

function setLiveMetricText(id, nextText) {
    const element = byId(id);
    if (!element) return;

    const currentText = element.textContent || "";
    if (currentText === nextText) {
        return;
    }

    element.textContent = nextText;
}

// The dial numbers ring the scale marks from the outside, and where they
// belong follows from the dial's own geometry rather than from a fixed
// pixel radius:
//
//   .tach-face::before  inset 7%, ring masked to 73-88% of its radius
//                       -> the scale marks end at 0.378 of the face
//                          diameter from the centre
//
// A number is placed so its inner edge clears that, which also keeps the
// bottom numbers off the centre readout. The old code used a fixed 146px:
// correct at exactly one dial size, and pushed onto - or over - the rim at
// every smaller one, which is what made the scale look crooked.
const TICK_RING_OUTER_RATIO = 0.378;
const TICK_LABEL_GAP = 4;

function positionGaugeTicks() {
    const gauges = [
        { selector: ".tach-face", gauge: "rpm" },
        { selector: ".speed-face", gauge: "speed" }
    ];

    gauges.forEach(({ selector, gauge }) => {
        const face = document.querySelector(selector);
        if (!face) return;

        const faceRect = face.getBoundingClientRect();
        const size = Math.min(faceRect.width, faceRect.height);
        if (!size) return;

        const centerX = faceRect.width / 2;
        const centerY = faceRect.height / 2;

        face.querySelectorAll(`.tick[data-gauge="${gauge}"]`).forEach((tick) => {
            const angle = Number(tick.dataset.angle || 0) * (Math.PI / 180);
            const cos = Math.cos(angle);
            const sin = Math.sin(angle);

            // How far the label box reaches along its own radius. A number
            // is wider than it is tall, so one at the side of the dial needs
            // more clearance than one at the top - using half the height for
            // both is what let the side numbers hang over the rim.
            const reach = Math.abs(cos) * ((tick.offsetWidth || 40) / 2)
                + Math.abs(sin) * ((tick.offsetHeight || 20) / 2);

            const radius = Math.min(
                size * TICK_RING_OUTER_RATIO + reach + TICK_LABEL_GAP,
                size * 0.5 - reach - 3
            );

            tick.style.left = `${centerX + cos * radius}px`;
            tick.style.top = `${centerY + sin * radius}px`;
            tick.style.transform = "translate(-50%, -50%)";
        });
    });
}

// The dial is fluid, so the numbers have to be re-placed whenever its size
// changes - a window resize, the sidebar collapsing, or the panel reflowing.
let gaugeResizeObserved = false;

function observeGaugeResize() {
    // Attach once: this runs on every page switch, and a fresh observer per
    // call would pile up watchers on the same two elements.
    if (gaugeResizeObserved) return;
    gaugeResizeObserved = true;

    if (!("ResizeObserver" in window)) {
        window.addEventListener("resize", positionGaugeTicks);
        return;
    }
    const observer = new ResizeObserver(() => positionGaugeTicks());
    document.querySelectorAll(".tach-face, .speed-face").forEach((face) => observer.observe(face));
}

function sanitizeKey(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[char]));
}

function clearContainerEmptyState(container) {
    container.querySelectorAll(".empty").forEach((node) => node.remove());
}

function ensureEmptyState(container, text) {
    const existing = container.querySelector(".empty");
    if (existing) {
        if (existing.textContent !== text) {
            existing.textContent = text;
        }
        return existing;
    }

    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = text;
    container.appendChild(empty);
    return empty;
}

function setPage(pageName) {
    if (!pageName || pageName === activePage) {
        return;
    }

    document.querySelectorAll(".module-button").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.page === pageName);
    });

    document.querySelectorAll(".page-screen").forEach((screen) => {
        const isTarget = screen.dataset.page === pageName;
        const isCurrent = screen.dataset.page === activePage;

        if (isCurrent && !isTarget) {
            screen.classList.remove("is-active");
            screen.classList.add("is-leaving");
        } else if (isTarget) {
            screen.classList.remove("is-leaving");
            screen.classList.add("is-active");
        } else {
            screen.classList.remove("is-active", "is-leaving");
        }
    });

    window.clearTimeout(pageTransitionTimer);
    pageTransitionTimer = window.setTimeout(() => {
        document.querySelectorAll(".page-screen.is-leaving").forEach((screen) => {
            screen.classList.remove("is-leaving");
        });
    }, 320);

    activePage = pageName;
    updateShellMode();
    if (pageName === "live") {
        refreshGaugeLayout();
    }
}

function initNavigation() {
    const nav = document.querySelector(".module-nav");

    if (!nav) return;

    nav.addEventListener("click", (event) => {
        const button = event.target.closest(".module-button");
        if (!button) return;
        event.preventDefault();
        setPage(button.dataset.page);
        window.location.hash = button.dataset.page;
    });
}

function initDashboardLauncher() {
    document.querySelectorAll("[data-page-target]").forEach((button) => {
        button.addEventListener("click", () => {
            const page = button.dataset.pageTarget;
            if (!page) return;
            setPage(page);
            window.location.hash = page;
        });
    });
}

function initPageFromHash() {
    const hashPage = window.location.hash.replace("#", "").trim() || "dashboard";

    document.querySelectorAll(".module-button").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.page === hashPage);
    });

    document.querySelectorAll(".page-screen").forEach((screen) => {
        const isTarget = screen.dataset.page === hashPage;
        screen.classList.toggle("is-active", isTarget);
        screen.classList.remove("is-leaving");
    });

    activePage = hashPage;
    updateShellMode();
    if (hashPage === "live") {
        refreshGaugeLayout();
    }
}

function updateShellMode() {
    const screen = document.querySelector(".tablet-screen");
    if (!screen) return;
    screen.classList.toggle("is-home-only", activePage === "dashboard");
}

function refreshGaugeLayout() {
    observeGaugeResize();
    window.requestAnimationFrame(() => {
        positionGaugeTicks();
        window.requestAnimationFrame(positionGaugeTicks);
    });
}

window.addEventListener("hashchange", initPageFromHash);

function currentDisplayPayload(livePayload) {
    if (isFrozen && frozenPayload) {
        return frozenPayload;
    }
    return livePayload;
}

function storeDemoPresets(presets) {
    demoPresetMeta.clear();
    (presets || []).forEach((preset) => {
        if (!preset?.id) return;
        demoPresetMeta.set(String(preset.id), preset);
    });
}

function dismissStartup(statusMessage = tr("scanner_ready", "Scanner ready.")) {
    if (startupDismissed) return;
    const overlay = byId("startup-overlay");
    setText("startup-message", statusMessage);
    if (!overlay) return;
    window.clearTimeout(startupFallbackTimer);
    window.setTimeout(() => {
        overlay.classList.add("is-hidden");
        startupDismissed = true;
    }, 700);
}

function armStartupFallback() {
    window.clearTimeout(startupFallbackTimer);
    startupFallbackTimer = window.setTimeout(() => {
        dismissStartup(tr("dashboard_loaded", "Dashboard loaded."));
    }, STARTUP_MAX_WAIT_MS);
}

async function fetchData() {
    try {
        await fetchStatusOnly(true);
    } catch (error) {
        console.error(error);
        isConnected = false;
        updateStatus({
            connected: false,
            current_port: "",
            error: tr("frontend_adapter_error", "Cannot connect to the ECU. Check whether the USB OBD adapter is plugged in."),
            user_message: tr("frontend_adapter_message", "Cannot connect to the ECU. The USB OBD adapter may not be connected.")
        });
        updateErrorLog([{
            time: "--",
            source: "Frontend",
            message: error.message,
            technical_message: error.message
        }]);
        dismissStartup(tr("frontend_adapter_short", "Cannot connect to the ECU. Check the USB OBD adapter."));
    } finally {
        scheduleNextPoll();
    }
}

async function fetchStatusOnly(shouldHydrateFullData = false) {
    const response = await fetch("/api/status", {
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
    });
    if (!response.ok) throw new Error(`Server returned status ${response.status}`);

    const payload = await response.json();
    const sessionState = payload.session_state || {};

    isConnected = Boolean(payload.connected);
    demoMode = Boolean(payload.demo_mode);
    limitedMode = Boolean(payload.limited_mode);
    pollProfile = String(payload.poll_profile?.id || pollProfile);

    updateStatus(payload, sessionState);
    updateConnectionQuality(payload.connection_quality || {});
    updateServiceModeUi();
    updateLimitedModeUi();
    updatePollProfileUi(payload.poll_profile || { id: pollProfile });
    updateErrorLog(payload.recent_errors || []);
    updateDemoModeUi();
    updateDemoPresetUi();
        dismissStartup(payload.user_message || tr("scanner_ready", "Scanner ready."));

    if (shouldHydrateFullData && (isConnected || demoMode)) {
        await fetchFullData();
    }
}

async function fetchFullData() {
    const response = await fetch("/api/data", {
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
    });
    if (!response.ok) throw new Error(`Server returned status ${response.status}`);

    applyFullPayload(await response.json());
}

// Renders one complete live-data payload. Called with a frame pushed over
// /api/stream, or with the answer from /api/data on the fallback path - the
// rendering is identical either way, only the transport differs.
function applyFullPayload(payload) {
    lastLivePayload = payload;
    const status = payload.status || {};
    const vehicleProfile = payload.vehicle_profile || {};
    const displayPayload = currentDisplayPayload(payload);
    const sessionState = displayPayload.session_state || payload.session_state || {};

    if (displayPayload.demo?.presets || payload.demo?.presets) {
        storeDemoPresets(displayPayload.demo?.presets || payload.demo?.presets || []);
    }
    demoPreset = String(
        displayPayload.demo?.preset ||
        payload.demo?.preset ||
        sessionState.demo_preset ||
        demoPreset
    );

    isConnected = Boolean(status.connected);
    demoMode = Boolean(status.demo_mode);
    limitedMode = Boolean(status.limited_mode);

    updateStatus(status, sessionState);
    updateBusModeIndicator(payload);
    applyWarningThresholds(displayPayload);
    updateConnectionQuality(displayPayload.connection_quality || payload.connection_quality || {});
    updateServiceModeUi();
    updateLimitedModeUi();
    updateErrorLog(status.recent_errors || []);
    updateVehicleProfileView(displayPayload.vehicle_profile || vehicleProfile);
    updateHealth(displayPayload.health || {});
    updateQuickMetrics(displayPayload.vehicle || {});
    updateTripComputer(displayPayload.trip || payload.trip || {});
    updateReadiness(displayPayload.readiness || payload.readiness || {});
    updateFreezeFrame(displayPayload.freeze_frame || payload.freeze_frame || {});
    updateReport(displayPayload.report || payload.report || {});
    updateBatteryCheck(displayPayload.battery_check || payload.battery_check || {});
    updateSimpleSummary(displayPayload.simple_summary || payload.simple_summary || {});
    updatePidSupportSummary(displayPayload.pid_support || payload.pid_support || {});
    renderExtendedData(displayPayload.extended || payload.extended || {});
    updateDtcStatus(displayPayload.dtc_status || payload.dtc_status || {});

    if (!isFrozen) {
        updateGaugeTargets(displayPayload.vehicle || {});
        updateCharts(displayPayload.vehicle || {});
        updateLiveData(displayPayload.vehicle || {});
        updateCodes("stored-codes", displayPayload.dtc?.stored || []);
        updateCodes("pending-codes", displayPayload.dtc?.pending || []);
        updateCodes("permanent-codes", displayPayload.dtc?.permanent || []);
        checkForNewFaultCodes(displayPayload.dtc || payload.dtc || {});
    }

    if (
        status.connected &&
        !status.connecting &&
        !vinRefreshPending &&
        !vehicleProfile.vin &&
        vehicleProfile.vin_status === "idle"
    ) {
        fetchVehicleProfile();
    }

    updateDemoModeUi();
    updateDemoPresetUi();
        dismissStartup(status.user_message || tr("scanner_ready", "Scanner ready."));
}

async function loadConfig() {
    try {
        pollProfile = loadLocalPollProfile();
        updatePollProfileUi({ id: pollProfile });
        const response = await fetch("/api/config");
        if (!response.ok) throw new Error(`Server returned status ${response.status}`);
        const config = await response.json();
        demoMode = Boolean(config.demo_mode);
        limitedMode = Boolean(config.limited_mode);
        pollProfile = String(config.poll_profile?.id || pollProfile || "balanced");
        saveLocalPollProfile(pollProfile);
        demoPreset = String(config.demo_preset || "idle");
        storeDemoPresets(config.demo_presets || []);
        const portInput = byId("port-input");
        if (portInput) {
            portInput.value = config.obd_port || "";
        }
        renderPortOptions(config.detected_ports || [], config.obd_port || "");
        updateDemoModeUi();
        updateLimitedModeUi();
        updatePollProfileUi(config.poll_profile || { id: pollProfile });
        updateDemoPresetUi();
        setText("scope-badge", tr("scope_standard_obd", "Standard OBD Only"));
    } catch (error) {
        console.error(error);
        setText("port-result", tr("port_config_failed", "Could not load COM configuration."));
    }
}

function renderPortOptions(ports, selectedPort = "") {
    const portInput = byId("port-input");
    if (!portInput) return;

    const normalizedPorts = Array.isArray(ports) ? ports : [];
    const nextSignature = JSON.stringify({
        selected: selectedPort || "",
        ports: normalizedPorts.map((port) => ({
            device: String(port?.device || ""),
            description: String(port?.description || "")
        }))
    });
    if (nextSignature === lastRenderedPortSignature) {
        return;
    }
    lastRenderedPortSignature = nextSignature;

    const previousValue = document.activeElement === portInput
        ? portInput.value
        : (selectedPort || portInput.value || "");
    const existingOptions = new Map(Array.from(portInput.options).map((option) => [option.value, option]));

    let emptyOption = existingOptions.get("");
    if (!emptyOption) {
        emptyOption = document.createElement("option");
        emptyOption.value = "";
    }
    emptyOption.textContent = tr("port_dropdown_empty", "No COM port selected");
    portInput.appendChild(emptyOption);

    normalizedPorts.forEach((port) => {
        const device = String(port?.device || "");
        if (!device) {
            return;
        }

        const label = port.description ? `${device} - ${port.description}` : device;
        let option = existingOptions.get(device);
        if (!option) {
            option = document.createElement("option");
            option.value = device;
        }
        option.textContent = label;
        portInput.appendChild(option);
    });

    Array.from(portInput.options).forEach((option) => {
        if (!option.value) {
            return;
        }
        const stillExists = normalizedPorts.some((port) => String(port?.device || "") === option.value);
        if (!stillExists) {
            option.remove();
        }
    });

    portInput.value = previousValue;
    if (portInput.value !== previousValue) {
        portInput.value = "";
    }

    syncPortDropdownUi();
}

function syncPortDropdownUi() {
    const portInput = byId("port-input");
    const trigger = byId("port-dropdown-trigger");
    const valueElement = byId("port-dropdown-value");
    const menu = byId("port-dropdown-menu");
    if (!portInput || !trigger || !valueElement || !menu) return;

    const options = Array.from(portInput.options).map((option) => ({
        value: option.value,
        label: option.textContent || "",
        selected: option.selected
    }));
    const selectedOption = options.find((option) => option.selected) || options[0];

    valueElement.textContent = selectedOption?.label || tr("port_dropdown_empty", "No COM port selected");
    trigger.classList.toggle("is-empty", !selectedOption?.value);

    const fragment = document.createDocumentFragment();
    options.forEach((option) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "port-dropdown-option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", option.selected ? "true" : "false");
        if (option.selected) {
            item.classList.add("is-selected");
        }

        const primary = document.createElement("span");
        primary.className = "port-dropdown-option-primary";
        primary.textContent = option.value || tr("port_dropdown_empty", "No COM port selected");

        const secondary = document.createElement("span");
        secondary.className = "port-dropdown-option-secondary";
        secondary.textContent = option.value ? option.label : tr("port_dropdown_helper", "Stay unlocked and do not force a COM port.");

        item.append(primary, secondary);
        item.addEventListener("click", () => {
            if (portInput.value === option.value) {
                setPortDropdownOpen(false);
                return;
            }
            portInput.value = option.value;
            syncPortDropdownUi();
            setPortDropdownOpen(false);
            portInput.dispatchEvent(new Event("change", { bubbles: true }));
        });
        fragment.appendChild(item);
    });

    menu.replaceChildren(fragment);
}

function setPortDropdownOpen(isOpen) {
    const shell = document.querySelector("[data-port-dropdown]");
    const trigger = byId("port-dropdown-trigger");
    const menu = byId("port-dropdown-menu");
    if (!shell || !trigger || !menu) return;

    shell.classList.toggle("is-open", isOpen);
    trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    menu.hidden = !isOpen;
}

function initPortDropdown() {
    const shell = document.querySelector("[data-port-dropdown]");
    const trigger = byId("port-dropdown-trigger");
    const menu = byId("port-dropdown-menu");
    const portInput = byId("port-input");
    if (!shell || !trigger || !menu || !portInput) return;

    trigger.addEventListener("click", () => {
        setPortDropdownOpen(!shell.classList.contains("is-open"));
    });

    document.addEventListener("click", (event) => {
        if (!shell.contains(event.target)) {
            setPortDropdownOpen(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setPortDropdownOpen(false);
        }
    });

    syncPortDropdownUi();
    setPortDropdownOpen(false);
}

function schedulePortPoll() {
    window.clearTimeout(portPollTimer);
    portPollTimer = window.setTimeout(fetchPortOptionsLive, PORT_POLL_MS);
}

async function fetchPortOptionsLive() {
    try {
        const response = await fetch("/api/config/ports", {
            signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
        });
        if (!response.ok) throw new Error(`Server returned status ${response.status}`);
        const result = await response.json();
        renderPortOptions(result.ports || [], result.selected || "");
    } catch (error) {
        console.error(error);
    } finally {
        schedulePortPoll();
    }
}

function scheduleNextPoll() {
    window.clearTimeout(pollTimer);
    // While the stream is feeding us, this only runs as a slow safety net.
    const delay = streamConnected && !streamFallbackActive
        ? 15000
        : (isConnected ? FALLBACK_POLL_MS : DISCONNECTED_POLL_MS);
    pollTimer = window.setTimeout(fetchData, delay);
}

// ---------------------------------------------------------------------------
// Live data stream
// ---------------------------------------------------------------------------
// The server builds each frame once and pushes it to every open tab, instead
// of every tab asking for its own copy ten times a second. The browser's own
// unit selection has to match the one the stream publishes in; when it does
// not (a per-browser override), the stream is kept for the gauges and the
// full payload is polled in this browser's units instead.
function streamUnitsMatch(frameUnits) {
    if (!frameUnits) return true;
    const mine = window.APP_UNITS || "metric";
    return String(frameUnits) === String(mine);
}

function setStreamIndicator(state) {
    const indicator = byId("stream-indicator");
    if (!indicator) return;
    indicator.dataset.state = state;
    indicator.classList.toggle("is-live", state === "live");
    indicator.classList.toggle("is-fallback", state === "fallback");
    indicator.title = state === "live"
        ? tr("stream_live", "Live stream connected")
        : state === "fallback"
            ? tr("stream_fallback", "Stream unavailable - polling instead")
            : tr("stream_connecting", "Connecting to live stream...");
}

function connectLiveStream() {
    if (!("EventSource" in window)) {
        streamFallbackActive = true;
        setStreamIndicator("fallback");
        return;
    }

    window.clearTimeout(streamRetryTimer);
    try {
        liveStream = new EventSource("/api/stream");
    } catch (error) {
        console.error(error);
        scheduleStreamRetry();
        return;
    }

    liveStream.addEventListener("open", () => {
        streamConnected = true;
        setStreamIndicator("live");
    });

    liveStream.addEventListener("gauges", (event) => {
        try {
            const frame = JSON.parse(event.data);
            isConnected = Boolean(frame.connected);
            demoMode = Boolean(frame.demo_mode);
            if (!isFrozen && streamUnitsMatch(frame.units)) {
                updateGaugeTargets(frame.vehicle || {});
            }
        } catch (error) {
            console.error(error);
        }
    });

    liveStream.addEventListener("live", (event) => {
        try {
            const frame = JSON.parse(event.data);
            if (!streamUnitsMatch(frame.units)) {
                if (!streamFallbackActive) {
                    streamFallbackActive = true;
                    setStreamIndicator("fallback");
                    scheduleNextPoll();
                }
                return;
            }
            streamFallbackActive = false;
            streamConnected = true;
            setStreamIndicator("live");
            applyFullPayload(frame);
        } catch (error) {
            console.error(error);
        }
    });

    liveStream.addEventListener("status", (event) => {
        try {
            const frame = JSON.parse(event.data);
            updateStatus(frame, frame.session_state || {});
        } catch (error) {
            console.error(error);
        }
    });

    liveStream.addEventListener("error", () => {
        // EventSource reconnects by itself, but a server that went away
        // entirely must not leave the dashboard frozen and silent.
        streamConnected = false;
        setStreamIndicator("connecting");
        if (liveStream && liveStream.readyState === EventSource.CLOSED) {
            scheduleStreamRetry();
        }
        scheduleNextPoll();
    });
}

function scheduleStreamRetry() {
    window.clearTimeout(streamRetryTimer);
    streamRetryTimer = window.setTimeout(() => {
        closeLiveStream();
        connectLiveStream();
    }, STREAM_RETRY_MS);
}

function closeLiveStream() {
    if (liveStream) {
        try {
            liveStream.close();
        } catch (error) {
            console.error(error);
        }
    }
    liveStream = null;
    streamConnected = false;
}

function scheduleGaugePoll() {
    window.clearTimeout(gaugePollTimer);
    if (streamConnected) {
        // The stream already carries the gauge channel.
        gaugePollTimer = window.setTimeout(scheduleGaugePoll, 2000);
        return;
    }
    gaugePollTimer = window.setTimeout(fetchGaugeData, isConnected || demoMode ? GAUGE_POLL_MS : DISCONNECTED_POLL_MS);
}

async function fetchGaugeData() {
    try {
        if (!isConnected && !demoMode) {
            return;
        }

        const response = await fetch("/api/gauges", {
            signal: AbortSignal.timeout(1200)
        });
        if (!response.ok) throw new Error(`Server returned status ${response.status}`);

        const payload = await response.json();
        if (!isFrozen) {
            updateGaugeTargets(payload.vehicle || {});
        }
    } catch (error) {
        console.error(error);
    } finally {
        scheduleGaugePoll();
    }
}

function updateStatus(status, sessionState = {}) {
    const dot = byId("status-dot");
    const reconnectButton = byId("reconnect-button");

    const portLabel = sessionState.port_label || status.current_port || tr("port_none_selected", "No COM port selected");
    demoMode = Boolean(status.demo_mode);

    if (dot && status.connecting) {
        dot.classList.remove("online");
    } else if (dot && status.connected) {
        dot.classList.add("online");
    } else if (dot) {
        dot.classList.remove("online");
    }

    const statusText = sessionState.status_label || (status.connecting ? tr("status_searching_adapter", "Searching for adapter...") : demoMode ? tr("status_demo_active", "Demo session active") : status.connected ? tr("status_connected", "Vehicle connected") : tr("status_offline", "Connection offline"));
    const adapterStateText = sessionState.adapter_label || (status.connecting ? tr("adapter_searching", "Searching") : demoMode ? tr("adapter_demo", "Demo") : status.connected ? tr("adapter_online", "Online") : tr("adapter_offline", "Offline"));
    const systemStatusText = sessionState.system_label || (status.connecting ? tr("system_searching", "Searching") : demoMode ? tr("system_demo", "Demo") : status.connected ? tr("system_connected", "Connected") : tr("system_offline", "Offline"));
    const liveStateText = demoMode ? tr("live_simulator", "Simulator") : status.connected ? tr("live_streaming", "Streaming") : status.connecting ? tr("live_waiting", "Waiting") : tr("live_no_data", "No Data");
    const connectionHintText = sessionState.detail || (
        status.connection_hint?.label
            ? `${status.connection_hint.label}. ${status.connection_hint.detail || ""}`
            : tr("waiting_for_adapter", "Waiting for adapter detection.")
    );

    if (byId("status-text")?.textContent !== statusText) animateTextSwap(byId("status-text"));
    if (byId("adapter-state")?.textContent !== adapterStateText) animateTextSwap(byId("adapter-state"));
    if (byId("system-status")?.textContent !== systemStatusText) animateTextSwap(byId("system-status"));
    if (byId("connection-hint")?.textContent !== connectionHintText) animateTextSwap(byId("connection-hint"));

    setText("status-text", statusText);
    setText("adapter-state", adapterStateText);
    setText("system-status", systemStatusText);
    setText("protocol", `${tr("protocol_prefix", "Protocol")}: ${status.protocol || "Unknown"}`);
    setText("status-message", status.user_message || status.error || tr("status_no_connection", "No connection"));
    setText("last-update", `${tr("update_prefix", "Update")}: ${status.last_update || "--"}`);
    setText("last-successful-update", `${tr("last_live_prefix", "Last live data")}: ${status.last_successful_update || "--"}`);
    setText("current-port", portLabel);
    setText("system-protocol", status.protocol || tr("unknown", "Unknown"));
    setText("system-port", portLabel);
    setText("scope-badge", tr("scope_standard_obd", "Standard OBD Only"));
    setText("live-state", liveStateText);
    // Drives the pulsing LED on the live-data chip.
    document.body.dataset.live = (status.connected || demoMode) && !status.connecting ? "1" : "0";
    setText("connection-hint", connectionHintText);
    updateDemoModeUi();

    if (reconnectButton) {
        reconnectButton.disabled = Boolean(status.connecting);
        reconnectButton.classList.toggle("connecting", Boolean(status.connecting));
        reconnectButton.innerText = status.connecting ? tr("reconnecting", "Reconnecting...") : tr("retry_connection", "Retry Connection");
    }
}

function updateConnectionQuality(quality) {
    const items = [
        { id: "quality-adapter", label: tr("quality_adapter", "Adapter"), online: Boolean(quality.adapter_connected) },
        { id: "quality-port", label: tr("quality_port", "OBD Port"), online: Boolean(quality.port_powered) },
        { id: "quality-car", label: tr("quality_car", "Car"), online: Boolean(quality.car_connected) },
        { id: "quality-live", label: tr("quality_live", "Live"), online: Boolean(quality.live_data_active) }
    ];

    const signature = items.map((item) => `${item.id}:${item.online ? "1" : "0"}`).join("|");
    if (signature === lastConnectionQualitySignature) {
        return;
    }
    lastConnectionQualitySignature = signature;

    items.forEach((item) => {
        const element = byId(item.id);
        if (!element) return;
        const previousState = element.dataset.online;
        const nextState = item.online ? "1" : "0";
        element.classList.toggle("is-online", item.online);
        element.classList.toggle("is-offline", !item.online);
        if (previousState !== nextState) {
            element.dataset.online = nextState;
        }
        element.innerHTML = `<span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.online ? tr("quality_ok", "Online") : tr("quality_waiting", "Waiting"))}</strong>`;
    });

    const summary = quality.quality || {};
    const summaryElement = byId("adapter-quality-summary");
    if (summaryElement) {
        summaryElement.className = `adapter-quality-summary status-${summary.level || "info"}`;
        summaryElement.innerHTML = `<strong>${escapeHtml(summary.label || tr("unknown", "Unknown"))}</strong><p>${escapeHtml(summary.detail || "")}</p>`;
    }
}

function updateDemoModeUi() {
    const button = byId("demo-mode-button");
    const copy = byId("demo-mode-copy");
    if (button) {
        button.innerText = demoMode ? tr("demo_disable", "Disable Demo Mode") : tr("demo_enable", "Enable Demo Mode");
        button.classList.toggle("is-active", demoMode);
    }
    if (copy) {
        const presetLabel = demoPresetMeta.get(demoPreset)?.label || tr("idle", "Idle");
        copy.innerText = demoMode
            ? tr("demo_mode_live", "Demo mode is live on the {preset} preset and keeps updating automatically.", { preset: presetLabel })
            : tr("demo_mode_off", "Use demo mode to test the UI without a USB adapter or car.");
    }
}

function updateLimitedModeUi() {
    const button = byId("limited-mode-button");
    const copy = byId("limited-mode-copy");
    const topbarState = byId("limited-mode-state");
    if (button) {
        button.innerText = limitedMode
            ? tr("limited_mode_disable", "Disable Limited Mode")
            : tr("limited_mode_enable", "Enable Limited Mode");
        button.classList.toggle("is-active", limitedMode);
    }
    if (copy) {
        copy.innerText = limitedMode
            ? tr("limited_mode_live", "Limited Mode is on. Only RPM, speed, coolant temp, ECU voltage, engine load and long fuel trim are polled.")
            : tr("limited_mode_off_copy", "Limited Mode is off. The scanner polls the full live ECU data set.");
    }
    if (topbarState) {
        topbarState.innerText = limitedMode
            ? tr("limited_mode_on_short", "On")
            : tr("limited_mode_off_short", "Off");
    }
}

function updatePollProfileUi(profile = {}) {
    const active = String(profile.id || pollProfile || loadLocalPollProfile() || "balanced");
    pollProfile = active;
    document.querySelectorAll("[data-poll-profile]").forEach((button) => {
        const isActive = button.dataset.pollProfile === active;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-pressed", String(isActive));
    });

    const copy = byId("poll-profile-copy");
    if (copy) {
        copy.innerText = tr(
            `poll_profile_${active}_copy`,
            profile.description || tr("poll_profile_copy", "Balanced is recommended. Performance feels faster, Safe reduces ECU and adapter load.")
        );
    }
    const topbarState = byId("profile-state");
    if (topbarState) {
        topbarState.innerText = active.charAt(0).toUpperCase() + active.slice(1);
    }
}

function loadLocalPollProfile() {
    try {
        const stored = window.localStorage.getItem(POLL_PROFILE_STORAGE_KEY);
        return ["performance", "balanced", "safe", "debug"].includes(stored) ? stored : "balanced";
    } catch {
        return "balanced";
    }
}

function saveLocalPollProfile(profile) {
    try {
        window.localStorage.setItem(POLL_PROFILE_STORAGE_KEY, profile);
    } catch {
        // Local storage can be unavailable in strict browser modes.
    }
}

function updateDemoPresetUi() {
    const copy = byId("demo-preset-copy");
    const preset = demoPresetMeta.get(demoPreset);

    document.querySelectorAll("[data-demo-preset]").forEach((button) => {
        const isActive = button.dataset.demoPreset === demoPreset;
        button.classList.toggle("is-active", isActive);
        button.disabled = demoPresetRequestPending;
    });

    if (copy) {
    copy.innerText = preset?.description || tr("demo_preset_calm", "Pick a calm simulator preset before driving the demo gauges.");
    }
}

function updateErrorLog(errors) {
    const errorLog = byId("error-log");
    if (!errorLog) return;

    const signature = JSON.stringify(errors || []);
    if (signature === lastRenderedErrorSignature) {
        return;
    }
    lastRenderedErrorSignature = signature;

    errorLog.replaceChildren();

    if (!errors || errors.length === 0) {
        const empty = document.createElement("p");
        empty.textContent = tr("errors_empty", "No errors logged.");
        errorLog.appendChild(empty);
        return;
    }

    errors.forEach((item, index) => {
        const row = document.createElement("div");
        row.className = "error-row";
        row.style.animationDelay = `${Math.min(index * 28, 220)}ms`;

        const meta = document.createElement("span");
        meta.textContent = `${item.time || "--"} - ${item.source || tr("unknown", "Unknown")}`;

        const message = document.createElement("strong");
        message.textContent = item.message || tr("unknown_error", "Unknown error");

        row.append(meta, message);

        if (item.technical_message) {
            const details = document.createElement("details");
            details.className = "technical-details";
            const summary = document.createElement("summary");
        summary.textContent = tr("technical_details", "Technical details");
            const pre = document.createElement("pre");
            pre.textContent = item.technical_message;
            details.append(summary, pre);
            row.appendChild(details);
        }

        errorLog.appendChild(row);
    });
}

function updateGaugeTargets(vehicle) {
    const nextRpm = numberFromValue(vehicle.rpm?.value);
    const nextSpeed = numberFromValue(vehicle.speed?.value);

    targetRpm = nextRpm;
    targetSpeed = nextSpeed;

    updateAccelTimer(nextSpeed);
    updateHomeMiniDashboard(vehicle);
}

const accelTimerState = { armed: true, running: false, startTs: null, best: null };

function accelTargetSpeed() {
    return window.APP_UNITS === "imperial" ? 60 : 100;
}

function initAccelTimerTitle() {
    const titleEl = byId("accel-timer-title");
    if (!titleEl) return;
    titleEl.textContent = window.APP_UNITS === "imperial"
        ? tr("accel_title_imperial", "0-60 mph")
        : tr("accel_title", "0-100 km/h");
}

function resetAccelTimer() {
    accelTimerState.armed = true;
    accelTimerState.running = false;
    accelTimerState.startTs = null;
    accelTimerState.best = null;
    setText("accel-timer-value", "--");
    setText("accel-timer-status", tr("accel_waiting", "Waiting for launch..."));
    setText("accel-best-time", tr("accel_best_none", "No run recorded yet."));
}

function updateAccelTimer(speed) {
    const valueEl = byId("accel-timer-value");
    const statusEl = byId("accel-timer-status");
    if (!valueEl || !statusEl || !Number.isFinite(speed)) return;

    const target = accelTargetSpeed();

    if (!accelTimerState.running) {
        if (speed <= 1 && !accelTimerState.armed) {
            accelTimerState.armed = true;
        }
        if (speed > 2 && accelTimerState.armed) {
            accelTimerState.running = true;
            accelTimerState.armed = false;
            accelTimerState.startTs = performance.now();
        }
    }

    if (accelTimerState.running) {
        const elapsed = (performance.now() - accelTimerState.startTs) / 1000;
        valueEl.textContent = `${elapsed.toFixed(2)}s`;
        statusEl.textContent = tr("accel_running", "Timing...");
        if (speed >= target) {
            accelTimerState.running = false;
            if (accelTimerState.best === null || elapsed < accelTimerState.best) {
                accelTimerState.best = elapsed;
                setText("accel-best-time", tr("accel_best", "Best this session: {time}s", { time: elapsed.toFixed(2) }));
            }
            statusEl.textContent = tr("accel_done", "Done! Reset to run again.");
        }
    } else if (!accelTimerState.armed) {
        statusEl.textContent = tr("accel_waiting_stop", "Come to a stop to arm the timer.");
    } else {
        statusEl.textContent = tr("accel_waiting", "Waiting for launch...");
    }
}

function updateHomeMiniDashboard(vehicle) {
    if (!byId("home-speed")) return;
    // The fast gauge poll (/api/gauges) only ever sends speed/rpm, for smooth
    // needle animation between full data polls. Only touch coolant/voltage
    // when this update actually carries that data (the full /api/data poll),
    // so the fast poll doesn't blank them back to "No Data" every tick.
    if ("speed" in vehicle) setText("home-speed", displayLiveValue(vehicle.speed?.value));
    if ("rpm" in vehicle) setText("home-rpm", displayLiveValue(vehicle.rpm?.value));
    if ("coolant_temp" in vehicle) setText("home-coolant", displayLiveValue(vehicle.coolant_temp?.value));
    if ("control_voltage" in vehicle || "voltage" in vehicle) {
        setText("home-voltage", displayLiveValue(vehicle.control_voltage?.value || vehicle.voltage?.value));
    }
}

function formatTripDuration(totalSeconds) {
    const seconds = Math.max(0, Math.round(totalSeconds || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) {
        return `${hours}h ${String(minutes).padStart(2, "0")}m`;
    }
    return `${minutes}m ${String(secs).padStart(2, "0")}s`;
}

function updateTripComputer(trip) {
    if (!trip || !byId("trip-distance")) return;

    setText("trip-distance", `${Number(trip.distance ?? 0).toFixed(1)} ${trip.distance_unit || "km"}`);
    setText("trip-avg-speed", `${Number(trip.avg_speed ?? 0).toFixed(0)} ${trip.speed_unit || "km/h"}`);
    setText("trip-max-speed", `${Number(trip.max_speed ?? 0).toFixed(0)} ${trip.speed_unit || "km/h"}`);
    setText("trip-max-rpm", trip.max_rpm ? Math.round(trip.max_rpm) : "--");
    setText("trip-driving-time", formatTripDuration(trip.driving_seconds));
    setText("trip-idle-time", formatTripDuration(trip.idle_seconds));
    setText("trip-avg-coolant", trip.avg_coolant !== null && trip.avg_coolant !== undefined
        ? `${Number(trip.avg_coolant).toFixed(0)} ${trip.temp_unit || "C"}`
        : "--");
    setText("trip-avg-consumption", trip.avg_consumption
        ? `${trip.avg_consumption.value} ${trip.avg_consumption.unit}`
        : "--");

    const liveConsumption = trip.live_consumption;
    if (liveConsumption) {
        setLiveMetricText(
            "quick-fuel-economy",
            liveConsumption.idle
                ? `${liveConsumption.value} L/h`
                : `${liveConsumption.value} ${liveConsumption.unit}`
        );
    } else {
        setLiveMetricText("quick-fuel-economy", tr("quick_no_data", "No Data"));
    }
}

async function resetTripComputer() {
    const button = byId("trip-reset-button");
    if (button) button.disabled = true;
    try {
        const response = await fetch("/api/trip/reset", { method: "POST" });
        const result = await response.json();
        if (result.trip) updateTripComputer(result.trip);
    } catch (error) {
        console.error(error);
    } finally {
        if (button) button.disabled = false;
    }
}

function liveGaugeValue(current, target, snapThreshold, dt = 1 / 60, response = 16) {
    if (!Number.isFinite(target)) return Number.isFinite(current) ? current : 0;
    if (!Number.isFinite(current)) return target;

    const difference = target - current;
    if (Math.abs(difference) <= snapThreshold) {
        return target;
    }

    // Smooth live interpolation: prevents the gauge from jumping from poll-to-poll
    // while still following fast ECU/demo changes quickly.
    const alpha = clamp(1 - Math.exp(-response * dt), 0.08, 0.62);
    return current + difference * alpha;
}

// Briefly highlights a readout whose value just changed, the way a real
// scan tool blips a field on update. Only fires on a real change, so a
// steady value does not strobe.
function flashOnChange(elementId, nextValue) {
    const element = byId(elementId);
    if (!element) return;
    const host = element.closest(".quick-metric, .detail-row, .metric-panel") || element;
    if (element.dataset.lastValue !== undefined && element.dataset.lastValue !== String(nextValue)) {
        host.classList.remove("is-updated");
        // Reading offsetWidth restarts the CSS animation.
        void host.offsetWidth;
        host.classList.add("is-updated");
        window.setTimeout(() => host.classList.remove("is-updated"), 500);
    }
    element.dataset.lastValue = String(nextValue);
}

function updateQuickMetrics(vehicle) {
    const metrics = {
        "quick-coolant": formatLiveValue(vehicle.coolant_temp?.value, "coolant_temp"),
        "quick-voltage": formatLiveValue(vehicle.control_voltage?.value || vehicle.voltage?.value, "control_voltage"),
        "quick-fuel-trim": formatLiveValue(vehicle.long_fuel_trim_1?.value, "long_fuel_trim_1"),
        "quick-engine-load": formatLiveValue(vehicle.engine_load?.value, "engine_load")
    };
    Object.entries(metrics).forEach(([id, value]) => {
        flashOnChange(id, value);
        setLiveMetricText(id, value);
    });
}

function pushChartPoint(buffer, value) {
    buffer.push(value);
    if (buffer.length > CHART_POINT_LIMIT) {
        buffer.shift();
    }
}

function installChartHover(canvas, canvasId) {
    if (canvas.dataset.hoverReady) return;
    canvas.dataset.hoverReady = "1";
    canvas.addEventListener("mousemove", (event) => {
        const rect = canvas.getBoundingClientRect();
        chartHoverState[canvasId] = ((event.clientX - rect.left) / Math.max(rect.width, 1)) * canvas.width;
    });
    canvas.addEventListener("mouseleave", () => {
        chartHoverState[canvasId] = null;
    });
}

function drawLineChart(canvasId, values, color, maxValueHint = 100, label = "") {
    const canvas = byId(canvasId);
    if (!canvas) return;
    installChartHover(canvas, canvasId);

    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = "#f6f9fc";
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = "#d6dee8";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i += 1) {
        const y = (height / 4) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    const cleanValues = (values && values.length ? values : [0, 0]).map((value) => (
        Number.isFinite(Number(value)) ? Number(value) : 0
    ));
    const maxValue = Math.max(maxValueHint, ...cleanValues, 1);
    const stepX = cleanValues.length > 1 ? width / (cleanValues.length - 1) : width;

    const points = cleanValues.map((value, index) => ({
        value,
        index,
        x: stepX * index,
        y: height - (Math.max(value, 0) / maxValue) * (height - 12) - 6
    }));

    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);

    if (points.length === 1) {
        ctx.lineTo(width, points[0].y);
    } else {
        for (let index = 0; index < points.length - 1; index += 1) {
            const current = points[index];
            const next = points[index + 1];
            const midX = (current.x + next.x) / 2;
            const midY = (current.y + next.y) / 2;
            ctx.quadraticCurveTo(current.x, current.y, midX, midY);
        }

        const lastPoint = points[points.length - 1];
        ctx.lineTo(lastPoint.x, lastPoint.y);
    }

    ctx.stroke();

    const hoverX = chartHoverState[canvasId];
    if (Number.isFinite(hoverX) && points.length) {
        const nearest = points.reduce((best, point) => Math.abs(point.x - hoverX) < Math.abs(best.x - hoverX) ? point : best, points[0]);
        const text = `${label || canvasId}: ${Math.round(nearest.value * 10) / 10}`;
        ctx.strokeStyle = "rgba(23, 32, 51, .25)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(nearest.x, 4);
        ctx.lineTo(nearest.x, height - 4);
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(nearest.x, nearest.y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = "700 12px Inter, Segoe UI, Arial, sans-serif";
        const labelWidth = ctx.measureText(text).width + 16;
        const labelX = Math.min(Math.max(nearest.x - labelWidth / 2, 6), width - labelWidth - 6);
        const labelY = Math.max(8, nearest.y - 34);
        ctx.fillStyle = "rgba(23, 32, 51, .92)";
        ctx.fillRect(labelX, labelY, labelWidth, 24);
        ctx.fillStyle = "#fff";
        ctx.fillText(text, labelX + 8, labelY + 16);
        canvas.title = text;
    }
}

function updateCharts(vehicle) {
    if (!rpmChartPoints.length) {
        pushChartPoint(rpmChartPoints, numberFromValue(vehicle.rpm?.value));
    }
    if (!speedChartPoints.length) {
        pushChartPoint(speedChartPoints, numberFromValue(vehicle.speed?.value));
    }
    if (!coolantChartPoints.length) {
        pushChartPoint(coolantChartPoints, numberFromValue(vehicle.coolant_temp?.value));
    }
    if (!voltageChartPoints.length) {
        pushChartPoint(voltageChartPoints, numberFromValue(vehicle.control_voltage?.value || vehicle.voltage?.value));
    }
    if (!engineLoadChartPoints.length) {
        pushChartPoint(engineLoadChartPoints, numberFromValue(vehicle.engine_load?.value));
    }
    if (!throttleChartPoints.length) {
        pushChartPoint(throttleChartPoints, numberFromValue(vehicle.throttle?.value));
    }
    if (!o2ChartPoints.length) {
        pushChartPoint(o2ChartPoints, numberFromValue(vehicle.o2_b1s1?.value || vehicle.o2_b1s2?.value));
    }
}

function renderCharts(now) {
    if (!lastChartSampleAt || now - lastChartSampleAt >= CHART_SAMPLE_MS) {
        pushChartPoint(rpmChartPoints, currentRpm);
        pushChartPoint(speedChartPoints, currentSpeed);
        const vehicle = currentDisplayPayload(lastLivePayload || {})?.vehicle || {};
        pushChartPoint(coolantChartPoints, numberFromValue(vehicle.coolant_temp?.value));
        pushChartPoint(voltageChartPoints, numberFromValue(vehicle.control_voltage?.value || vehicle.voltage?.value));
        pushChartPoint(engineLoadChartPoints, numberFromValue(vehicle.engine_load?.value));
        pushChartPoint(throttleChartPoints, numberFromValue(vehicle.throttle?.value));
        pushChartPoint(o2ChartPoints, numberFromValue(vehicle.o2_b1s1?.value || vehicle.o2_b1s2?.value));
        recordGraphSample(now);
        lastChartSampleAt = now;
    }

    drawLineChart("rpm-chart", rpmChartPoints, "#e14d4d", 5000, "RPM");
    drawLineChart("speed-chart", speedChartPoints, "#0d6efd", 160, "Speed");
    drawLineChart("coolant-chart", coolantChartPoints, "#0ca678", 130, "Coolant");
    drawLineChart("voltage-chart", voltageChartPoints, "#f59f00", 16, "ECU Voltage");
    drawLineChart("engine-load-chart", engineLoadChartPoints, "#845ef7", 100, "Engine Load");
    drawLineChart("throttle-chart", throttleChartPoints, "#12b886", 100, "Throttle");
    drawLineChart("o2-chart", o2ChartPoints, "#495057", 1.2);
}


function currentGraphSample(now = performance.now()) {
    const vehicle = currentDisplayPayload(lastLivePayload || {})?.vehicle || {};
    return {
        t: graphRecordingStartedAt ? Math.round(now - graphRecordingStartedAt) : 0,
        rpm: Math.round(currentRpm),
        speed: Math.round(currentSpeed),
        coolant: numberFromValue(vehicle.coolant_temp?.value),
        voltage: numberFromValue(vehicle.control_voltage?.value || vehicle.voltage?.value),
        engineLoad: numberFromValue(vehicle.engine_load?.value),
        throttle: numberFromValue(vehicle.throttle?.value)
    };
}

function recordGraphSample(now) {
    if (!graphRecordingActive) return;
    graphRecordingSamples.push(currentGraphSample(now));
    if (graphRecordingSamples.length > 24000) {
        graphRecordingSamples.shift();
    }
    updateRecordingStatus();
}

function updateRecordingStatus() {
    const button = byId("record-graphs-button");
    const status = byId("recording-status");
    if (button) {
        button.textContent = graphRecordingActive ? "Stop Recording" : "Start Recording";
        button.classList.toggle("danger-action", graphRecordingActive);
        button.classList.toggle("secondary-action", !graphRecordingActive);
    }
    if (status) {
        status.classList.toggle("is-recording", graphRecordingActive);
        if (graphRecordingActive) {
            const duration = graphRecordingStartedAt ? Math.round((performance.now() - graphRecordingStartedAt) / 1000) : 0;
            status.textContent = `Recording graphs... ${duration}s / ${graphRecordingSamples.length} samples`;
        } else if (graphRecordingSamples.length) {
            status.textContent = `${graphRecordingSamples.length} graph samples recorded`;
        } else {
            status.textContent = "No recording active";
        }
    }
}

function escapeRecordingJson(data) {
    return JSON.stringify(data).replace(/</g, "\\u003c");
}

function buildRecordingPlaybackHtml(samples) {
    const exportedAt = new Date().toLocaleString();
    const payload = {
        appVersion: window.APP_VERSION || "",
        exportedAt,
        samples,
        series: [
            { key: "rpm", label: "RPM", color: "#e14d4d", max: 5000 },
            { key: "speed", label: "Speed", color: "#0d6efd", max: 160 },
            { key: "coolant", label: "Coolant", color: "#0ca678", max: 130 },
            { key: "voltage", label: "ECU Voltage", color: "#f59f00", max: 16 },
            { key: "engineLoad", label: "Engine Load", color: "#845ef7", max: 100 },
            { key: "throttle", label: "Throttle", color: "#12b886", max: 100 }
        ]
    };

    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OBD Graph Recording</title>
<style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#172033;background:#edf3f8}body{margin:0;padding:24px}.shell{max-width:1280px;margin:0 auto}.hero{background:#fff;border:1px solid #d5e0ea;border-radius:22px;padding:22px 24px;box-shadow:0 18px 44px rgba(34,62,96,.12);display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.eyebrow{color:#0d6efd;font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;margin:0 0 8px}h1{font-size:30px;margin:0 0 8px}.muted{color:#62748d;margin:0}.controls{display:flex;gap:10px;flex-wrap:wrap}button{border:1px solid #bdd0e5;background:linear-gradient(180deg,#fff,#eef5fc);border-radius:14px;color:#162237;cursor:pointer;font-weight:900;padding:12px 16px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:18px}.card{background:#fff;border:1px solid #d5e0ea;border-radius:20px;padding:16px;box-shadow:0 12px 30px rgba(34,62,96,.08)}.card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.card strong{font-size:17px}.value{color:#62748d;font-weight:900}canvas{background:#f6f9fc;border:1px solid #e0e8f1;border-radius:14px;width:100%;height:220px}@media(max-width:800px){.hero,.grid{grid-template-columns:1fr;display:grid}.controls{width:100%}button{width:100%}}</style>
</head>
<body>
<div class="shell">
<section class="hero">
<div><p class="eyebrow">OBD Graph Playback</p><h1>Recorded Live Graphs</h1><p class="muted">Exported ${exportedAt}. ${samples.length} samples recorded.</p></div>
<div class="controls"><button id="play">Play</button><button id="pause">Pause</button><button id="restart">Restart</button></div>
</section>
<div id="grid" class="grid"></div>
</div>
<script>
const recording = ${escapeRecordingJson(payload)};
let playing = true;
let startedAt = performance.now();
let pausedAt = 0;
let cursorMs = 0;
const duration = Math.max(...recording.samples.map(sample => sample.t), 1);
const grid = document.getElementById('grid');
const hover = {};
recording.series.forEach(series => {
  const card = document.createElement('section');
  card.className = 'card';
  card.innerHTML = '<div class="card-head"><strong>' + series.label + '</strong><span class="value" id="value-' + series.key + '">--</span></div><canvas id="chart-' + series.key + '" width="760" height="240"></canvas>';
  grid.appendChild(card);
  const canvas = document.getElementById('chart-' + series.key);
  canvas.addEventListener('mousemove', event => { const rect = canvas.getBoundingClientRect(); hover[series.key] = ((event.clientX - rect.left) / Math.max(rect.width, 1)) * canvas.width; });
  canvas.addEventListener('mouseleave', () => { hover[series.key] = null; });
});
function draw(canvas, values, color, maxHint, label, hoverX) {
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = '#f6f9fc'; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle = '#d6dee8'; ctx.lineWidth = 1;
  for (let i=1;i<4;i++){const y=(height/4)*i;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
  const clean = values.length ? values.map(v => Number.isFinite(Number(v)) ? Number(v) : 0) : [0,0];
  const max = Math.max(maxHint, ...clean, 1);
  const stepX = clean.length > 1 ? width / (clean.length - 1) : width;
  ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.beginPath();
  const points = clean.map((value,index)=>({value,index,x:stepX*index,y:height-(Math.max(value,0)/max)*(height-16)-8}));
  points.forEach((point,index)=>{if(index===0)ctx.moveTo(point.x,point.y); else ctx.lineTo(point.x,point.y);});
  ctx.stroke();
  if(Number.isFinite(hoverX) && points.length){const nearest=points.reduce((best,point)=>Math.abs(point.x-hoverX)<Math.abs(best.x-hoverX)?point:best,points[0]);const text=label + ': ' + (Math.round(nearest.value*10)/10);ctx.strokeStyle='rgba(23,32,51,.25)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(nearest.x,4);ctx.lineTo(nearest.x,height-4);ctx.stroke();ctx.fillStyle=color;ctx.beginPath();ctx.arc(nearest.x,nearest.y,4,0,Math.PI*2);ctx.fill();ctx.font='700 12px Inter,Segoe UI,Arial,sans-serif';const w=ctx.measureText(text).width+16;const x=Math.min(Math.max(nearest.x-w/2,6),width-w-6);const y=Math.max(8,nearest.y-34);ctx.fillStyle='rgba(23,32,51,.92)';ctx.fillRect(x,y,w,24);ctx.fillStyle='#fff';ctx.fillText(text,x+8,y+16);canvas.title=text;}
}
function frame(now){
  if(playing){cursorMs = Math.min(now - startedAt, duration);} 
  const visible = recording.samples.filter(sample => sample.t <= cursorMs);
  recording.series.forEach(series => {
    const values = visible.map(sample => sample[series.key]);
    const latest = values.length ? values[values.length - 1] : 0;
    document.getElementById('value-' + series.key).textContent = Math.round(latest * 10) / 10;
    draw(document.getElementById('chart-' + series.key), values, series.color, series.max, series.label, hover[series.key]);
  });
  if(cursorMs >= duration) playing = false;
  requestAnimationFrame(frame);
}
document.getElementById('play').onclick=()=>{if(!playing){playing=true;startedAt=performance.now()-cursorMs;}};
document.getElementById('pause').onclick=()=>{playing=false;pausedAt=cursorMs;};
document.getElementById('restart').onclick=()=>{cursorMs=0;playing=true;startedAt=performance.now();};
requestAnimationFrame(frame);
</script>
</body>
</html>`;
}

function downloadGraphRecording() {
    if (!graphRecordingSamples.length) {
        updateRecordingStatus();
        return;
    }

    const html = buildRecordingPlaybackHtml(graphRecordingSamples.slice());
    const blob = new Blob([html], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `obd-graph-recording-${new Date().toISOString().replace(/[:.]/g, "-")}.html`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function toggleGraphRecording() {
    if (graphRecordingActive) {
        graphRecordingActive = false;
        updateRecordingStatus();
        downloadGraphRecording();
        return;
    }

    graphRecordingSamples = [];
    graphRecordingStartedAt = performance.now();
    graphRecordingActive = true;
    updateRecordingStatus();
}

async function checkForUpdates() {
    if (!document.body?.dataset?.dashboardPage) return;
    try {
        const response = await fetch("/api/update-check", { signal: AbortSignal.timeout(3500) });
        if (!response.ok) { setOfflineIndicator(true); return; }
        const result = await response.json();
        setOfflineIndicator(false);
        if (!result.update_available) return;

        const box = byId("update-notification");
        if (!box) return;
        setText("update-title", "Update available");
        setText("update-message", `You are using ${result.current_version}. New version ${result.latest_version} is available.`);
        const link = byId("update-link");
        if (link) {
            link.href = result.download_url || "#";
        }
        box.hidden = false;
    } catch (error) {
        setOfflineIndicator(true);
    }
}

function updateHealth(health) {
    const score = health.score ?? "--";
    const status = health.status || "good";
    const counts = health.counts || { stored: 0, pending: 0, permanent: 0 };
    const healthSignature = JSON.stringify({
        score,
        status,
        headline: health.headline || tr("health_unavailable", "Health report unavailable."),
        counts
    });

    if (healthSignature !== lastRenderedHealthSignature) {
        lastRenderedHealthSignature = healthSignature;
        setText("health-score", score);
    setText("health-headline", health.headline || tr("health_unavailable", "Health report unavailable."));
        setClassName("health-status-badge", `health-status-badge status-${status}`);
    setText("health-status-badge", status === "danger" ? tr("health_attention", "Attention") : status === "warning" ? tr("health_check", "Check") : tr("health_good", "Good"));
        setClassName("stored-status-pill", `status-pill ${counts.stored ? "status-danger" : "status-good"}`);
        setClassName("pending-status-pill", `status-pill ${counts.pending ? "status-warning" : "status-good"}`);
        setClassName("permanent-status-pill", `status-pill ${counts.permanent ? "status-warning" : "status-info"}`);
    setText("stored-status-pill", `${counts.stored || 0} ${tr("codes_stored", "Stored")}`);
    setText("pending-status-pill", `${counts.pending || 0} ${tr("codes_pending", "Pending")}`);
    setText("permanent-status-pill", `${counts.permanent || 0} ${tr("codes_permanent", "Permanent")}`);
    }

    updateCountDisplays("stored-count", counts.stored || 0);
    updateCountDisplays("pending-count", counts.pending || 0);
    updateCountDisplays("permanent-count", counts.permanent || 0);

    const checklist = byId("purchase-checklist");
    if (!checklist) return;

    const items = health.checklist || [];
    const checklistSignature = JSON.stringify(items);
    if (checklistSignature === lastRenderedChecklistSignature) {
        return;
    }
    lastRenderedChecklistSignature = checklistSignature;

    if (items.length === 0) {
        let empty = checklist.querySelector('[data-key="health-empty"]');
        if (!empty) {
            checklist.replaceChildren();
            empty = document.createElement("div");
            empty.className = "checklist-item level-good";
            empty.dataset.key = "health-empty";
            empty.innerHTML = `<strong>${tr("health_no_red_flags_title", "No obvious red flags from current scan.")}</strong><p>${tr("health_no_red_flags_copy", "Still verify with a road test and visual inspection.")}</p>`;
            checklist.appendChild(empty);
        }
        return;
    }

    const nextKeys = new Set(items.map((item, index) => sanitizeKey(`${item.level || "info"}-${item.title || `item-${index}`}`)));

    Array.from(checklist.children).forEach((child) => {
        if (!nextKeys.has(child.dataset.key || "")) {
            child.remove();
        }
    });

    items.forEach((item, index) => {
        const rowKey = sanitizeKey(`${item.level || "info"}-${item.title || `item-${index}`}`);
        let row = checklist.querySelector(`[data-key="${rowKey}"]`);

        if (!row) {
            row = document.createElement("div");
            row.dataset.key = rowKey;
            row.innerHTML = "<strong></strong><p></p>";
            checklist.appendChild(row);
        }

        row.className = `checklist-item level-${item.level || "info"}`;
        row.querySelector("strong").textContent = item.title || tr("notice", "Notice");
        row.querySelector("p").textContent = item.detail || "";
    });
}

function updateReadiness(readiness) {
    const container = byId("readiness-grid");
    if (!container) return;

    const signature = JSON.stringify(readiness || {});
    if (signature === lastReadinessSignature) {
        return;
    }
    lastReadinessSignature = signature;

    if (!readiness.available) {
        Array.from(container.children).forEach((child) => {
            if (!child.classList.contains("empty")) {
                child.remove();
            }
        });
        ensureEmptyState(container, tr("readiness_empty", "Readiness monitor data is not available yet."));
        return;
    }

    clearContainerEmptyState(container);

    const nextKeys = new Set(["mil", ...(readiness.monitors || []).map((item) => `monitor-${sanitizeKey(item.name)}`)]);
    Array.from(container.children).forEach((child) => {
        if (!nextKeys.has(child.dataset.key || "")) {
            child.remove();
        }
    });

    let milRow = container.querySelector('[data-key="mil"]');
    if (!milRow) {
        milRow = document.createElement("div");
        milRow.className = "support-row";
        milRow.dataset.key = "mil";
        milRow.innerHTML = `<div><strong>MIL</strong><p></p></div>`;
        container.appendChild(milRow);
    }
    milRow.querySelector("p").textContent = `${readiness.mil ? tr("readiness_on", "On") : tr("readiness_off", "Off")} | ${tr("readiness_dtc_count", "DTC count")}: ${readiness.dtc_count ?? "--"} | ${displayValue(readiness.ignition_type, tr("readiness_unknown_ignition", "Unknown ignition"))}`;

    (readiness.monitors || []).forEach((item, index) => {
        const rowKey = `monitor-${sanitizeKey(item.name)}`;
        let row = container.querySelector(`[data-key="${rowKey}"]`);
        if (!row) {
            row = document.createElement("div");
            row.dataset.key = rowKey;
            row.innerHTML = `<div><strong></strong><p></p></div><span class="support-badge"></span>`;
            container.appendChild(row);
        }
        row.className = `support-row ${item.complete ? "is-supported" : "is-unsupported"}`;
        row.style.animationDelay = `${Math.min(index * 18, 200)}ms`;
        row.querySelector("strong").textContent = item.name;
        row.querySelector("p").textContent = item.available ? tr("support_yes", "Supported") : tr("support_no", "Unavailable");
        const badge = row.querySelector(".support-badge");
        badge.className = `support-badge ${item.complete ? "status-good" : "status-warning"}`;
        badge.textContent = item.complete ? tr("readiness_ready", "Ready") : tr("readiness_incomplete", "Incomplete");
    });
}

function freezeFrameWarningKey(key) {
    const normalized = String(key || "").toLowerCase();
    if (normalized === "coolant_temp") return "coolant_temp";
    if (normalized === "oil_temp") return "oil_temp";
    if (normalized === "intake_temp") return "intake_temp";
    if (normalized === "control_voltage" || normalized === "voltage") return "control_voltage";
    if (normalized === "engine_load") return "engine_load";
    if (normalized === "throttle") return "throttle";
    if (normalized === "short_fuel_trim_1") return "short_fuel_trim_1";
    if (normalized === "long_fuel_trim_1") return "long_fuel_trim_1";
    return normalized;
}

function updateFreezeFrame(freezeFrame) {
    const container = byId("freeze-frame-grid");
    if (!container) return;

    const signature = JSON.stringify(freezeFrame || {}) + JSON.stringify(currentWarningThresholds || {});
    if (signature === lastFreezeFrameSignature) {
        return;
    }
    lastFreezeFrameSignature = signature;

    if (!freezeFrame.available || !freezeFrame.values || Object.keys(freezeFrame.values).length === 0) {
        Array.from(container.children).forEach((child) => {
            if (!child.classList.contains("empty")) {
                child.remove();
            }
        });
        ensureEmptyState(container, tr("freeze_frame_empty", "No freeze-frame snapshot available."));
        return;
    }

    clearContainerEmptyState(container);
    const entries = Object.entries(freezeFrame.values);
    const nextKeys = new Set(entries.map(([key]) => key));
    Array.from(container.children).forEach((child) => {
        if (!nextKeys.has(child.dataset.key || "")) {
            child.remove();
        }
    });

    entries.forEach(([key, value], index) => {
        let row = container.querySelector(`[data-key="${key}"]`);
        const label = key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
        const warning = getSensorWarning(freezeFrameWarningKey(key), value, currentWarningThresholds, label);
        const warningTitle = warning ? warningMessage(label, warning.value, warning.detail) : "";
        if (!row) {
            row = document.createElement("div");
            row.className = "sensor-row freeze-frame-row";
            row.dataset.key = key;
            row.innerHTML = `<span class="sensor-label"></span><span class="sensor-warning-badge" hidden>WARN</span><strong class="sensor-value"></strong>`;
            container.appendChild(row);
        }
        row.className = "sensor-row freeze-frame-row";
        row.classList.toggle("is-warning", Boolean(warning));
        row.title = warningTitle || label;
        row.style.animationDelay = `${Math.min(index * 18, 220)}ms`;
        row.querySelector(".sensor-label").textContent = label;
        row.querySelector(".sensor-label").title = label;
        const badge = row.querySelector(".sensor-warning-badge");
        if (badge) {
            badge.hidden = !warning;
            badge.title = warningTitle;
        }
        row.querySelector(".sensor-value").textContent = value;
        row.querySelector(".sensor-value").title = String(value ?? "");
    });
}

function updateReport(report) {
    const summary = byId("report-summary");
    const sections = byId("report-sections");
    if (!summary || !sections) return;

    const signature = JSON.stringify(report || {});
    if (signature === lastReportSignature) {
        return;
    }
    lastReportSignature = signature;

    setText("report-headline", report.headline || tr("report_fallback_headline", "Pre-purchase summary"));
    const summaryItems = report.summary || [];
    const detailSections = report.sections || [];

    if (summaryItems.length === 0) {
        Array.from(summary.children).forEach((child) => {
            if (!child.classList.contains("empty")) {
                child.remove();
            }
        });
        ensureEmptyState(summary, tr("report_summary_empty", "No report summary available yet."));
    } else {
        clearContainerEmptyState(summary);
        const nextKeys = new Set(summaryItems.map((_, index) => `summary-${index}`));
        Array.from(summary.children).forEach((child) => {
            if (!nextKeys.has(child.dataset.key || "")) {
                child.remove();
            }
        });
        summaryItems.forEach((item, index) => {
            const rowKey = `summary-${index}`;
            let row = summary.querySelector(`[data-key="${rowKey}"]`);
            if (!row) {
                row = document.createElement("div");
                row.className = "history-row";
                row.dataset.key = rowKey;
                row.innerHTML = `<div><strong></strong></div>`;
                summary.appendChild(row);
            }
            row.style.animationDelay = `${Math.min(index * 20, 220)}ms`;
            row.querySelector("strong").textContent = item;
        });
    }

    if (detailSections.length === 0) {
        Array.from(sections.children).forEach((child) => {
            if (!child.classList.contains("empty")) {
                child.remove();
            }
        });
        ensureEmptyState(sections, tr("report_details_empty", "No report details available yet."));
    } else {
        clearContainerEmptyState(sections);
        const nextKeys = new Set(detailSections.map((section, index) => `section-${sanitizeKey(section.title || index)}`));
        Array.from(sections.children).forEach((child) => {
            if (!nextKeys.has(child.dataset.key || "")) {
                child.remove();
            }
        });
        detailSections.forEach((section, index) => {
            const rowKey = `section-${sanitizeKey(section.title || index)}`;
            let row = sections.querySelector(`[data-key="${rowKey}"]`);
            if (!row) {
                row = document.createElement("div");
                row.className = "history-row";
                row.dataset.key = rowKey;
                row.innerHTML = `<div><strong></strong><p></p></div>`;
                sections.appendChild(row);
            }
            row.style.animationDelay = `${Math.min(index * 20, 220)}ms`;
            row.querySelector("strong").textContent = section.title;
            row.querySelector("p").textContent = (section.items || []).join(" | ");
        });
    }
}

function updateBatteryCheck(battery) {
    const badge = byId("battery-status-badge");
    const status = battery.status || "unknown";

    setText("battery-headline", battery.headline || tr("battery_unavailable", "Battery check unavailable"));
    setText("battery-detail", battery.detail || tr("battery_empty", "Voltage data is not available yet."));

    if (badge) {
        badge.className = `health-status-badge status-${status === "unknown" ? "info" : status}`;
        badge.textContent = status === "good"
            ? tr("health_good", "Good")
            : status === "warning"
                ? tr("health_check", "Check")
                : status === "danger"
                    ? tr("health_attention", "Attention")
                    : tr("unknown", "Unknown");
    }
}

function updateSimpleSummary(summary) {
    const panel = byId("simple-summary-panel");
    const list = byId("simple-summary-list");
    const button = byId("simple-mode-button");
    if (!panel || !list) return;

    panel.hidden = !simpleMode;
    if (button) {
        button.classList.toggle("is-active", simpleMode);
        button.setAttribute("aria-pressed", String(simpleMode));
        button.textContent = simpleMode ? tr("simple_mode_hide", "Hide Simple Mode") : tr("simple_mode_show", "Show Simple Mode");
    }
    if (!simpleMode) return;

    setText("simple-summary-headline", summary.headline || tr("simple_summary_unavailable", "Simple summary unavailable."));
    list.replaceChildren();
    (summary.items || []).forEach((item, index) => {
        const row = document.createElement("div");
        row.className = `checklist-item level-${summary.level || "info"}`;
        row.style.animationDelay = `${Math.min(index * 20, 180)}ms`;
        row.innerHTML = `<strong>${summary.headline || tr("simple_summary", "Simple summary")}</strong><p></p>`;
        row.querySelector("p").textContent = item;
        list.appendChild(row);
    });
}

function updatePidSupportSummary(support) {
    const container = byId("pid-support-summary");
    if (!container) return;

    const supported = support.supported_count ?? 0;
    const unsupported = support.unsupported_count ?? 0;
    const total = support.total ?? supported + unsupported;

    const cards = [
        { label: tr("pid_supported", "Supported"), value: supported },
        { label: tr("pid_unsupported", "Unsupported"), value: unsupported },
        { label: tr("pid_total", "Total checked"), value: total }
    ];

    container.replaceChildren();
    cards.forEach((card) => {
        const element = document.createElement("div");
        element.innerHTML = `<span>${card.value}</span><p>${card.label}</p>`;
        container.appendChild(element);
    });
}

function toggleSimpleMode() {
    simpleMode = !simpleMode;
    updateSimpleSummary(lastLivePayload?.simple_summary || {});
}

function exportScanReport() {
    const modal = byId("export-modal");
    if (modal) {
        modal.hidden = false;
        return;
    }
    window.location.href = "/api/report/export";
}

function closeExportModal() {
    const modal = byId("export-modal");
    if (modal) modal.hidden = true;
}

function currentExportPreset() {
    const select = byId("report-export-preset");
    return select && select.value ? select.value : "full";
}

function exportScanReportFormat(format) {
    closeExportModal();
    const preset = encodeURIComponent(currentExportPreset());
    window.location.href = `/api/report/export?format=${encodeURIComponent(format)}&preset=${preset}`;
}

function formatEngine(decoded) {
    if (decoded.engine_summary) {
        return decoded.engine_summary;
    }

    const parts = [];
    if (decoded.engine_cylinders) parts.push(`${decoded.engine_cylinders} cyl`);
    if (decoded.engine_displacement_l) parts.push(`${decoded.engine_displacement_l} L`);
    if (decoded.engine_model) parts.push(decoded.engine_model);
    if (decoded.engine_power_hp) parts.push(`${decoded.engine_power_hp} hp`);
    return parts.length > 0 ? parts.join(" / ") : "--";
}

function renderVinExtraDetails(decoded) {
    const container = byId("vin-extra-details");
    if (!container) return;

    const details = Array.isArray(decoded.extra_details) ? decoded.extra_details : [];
    const signature = JSON.stringify(
        details.map((detail, index) => ({
            key: String(detail?.key || `detail-${index}`),
        label: String(detail?.label || tr("detail", "Detail")),
            value: displayValue(detail?.value),
        }))
    );

    if (signature === lastRenderedVinExtraSignature) {
        return;
    }
    lastRenderedVinExtraSignature = signature;

    const expectedKeys = new Set();

    if (!details.length) {
        clearContainerEmptyState(container);
        container.querySelectorAll(".spec-card").forEach((card) => card.remove());
        ensureEmptyState(container, tr("vin_extra_empty", "No additional VIN details were returned by the decoder for this vehicle."));
        return;
    }

    clearContainerEmptyState(container);

    details.forEach((detail, index) => {
        const cardKey = String(detail.key || `detail-${index}`);
        expectedKeys.add(cardKey);

        let card = container.querySelector(`.spec-card[data-key="${cardKey}"]`);
        if (!card) {
            card = document.createElement("div");
            card.className = "spec-card";
            card.dataset.key = cardKey;
            card.innerHTML = "<span></span><strong></strong>";
            container.appendChild(card);
        }

        const label = detail.label || tr("detail", "Detail");
        const value = displayValue(detail.value);
        const labelNode = card.querySelector("span");
        const valueNode = card.querySelector("strong");

        if (labelNode && labelNode.textContent !== label) {
            labelNode.textContent = label;
        }
        if (valueNode && valueNode.textContent !== value) {
            valueNode.textContent = value;
        }
    });

    container.querySelectorAll(".spec-card").forEach((card) => {
        if (!expectedKeys.has(card.dataset.key || "")) {
            card.remove();
        }
    });
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}
function updateVehicleProfileView(profile) {
    lastVehicleProfile = {
        ...lastVehicleProfile,
        ...profile,
        decoded: profile.decoded !== undefined ? profile.decoded : (lastVehicleProfile.decoded || {}),
        rdw: profile.rdw !== undefined ? profile.rdw : (lastVehicleProfile.rdw || {})
    };

    const vin = lastVehicleProfile.vin || "";
    const decoded = lastVehicleProfile.decoded || {};
    const rdw = lastVehicleProfile.rdw || {};

    setText("vin-value", vin || tr("vin_not_loaded_short", "Not loaded"));
    setText("vin-message", lastVehicleProfile.vin_message || tr("vin_not_loaded", "VIN not loaded yet."));
    const manualVinInput = byId("manual-vin-input");
    if (manualVinInput && vin && document.activeElement !== manualVinInput) {
        manualVinInput.value = vin;
    }
    setText("manual-vin-result", lastVehicleProfile.vin_message || tr("vin_manual_default", "Enter a manual VIN when the ECU does not return one automatically."));
    setText("vin-make", displayValue(decoded.make));
    setText("vin-model", displayValue(decoded.model));
    setText("vin-year", displayValue(decoded.model_year));
    setText("vin-fuel", displayValue(decoded.fuel_type));
    setText("vin-body", displayValue(decoded.body_class || decoded.vehicle_type));
    setText("vin-engine", formatEngine(decoded));
    setText("vin-drive", displayValue(decoded.drive_type || decoded.transmission_style));
    setText("vin-plant", displayValue(decoded.plant_location || decoded.plant_company || decoded.plant_country));
    renderVinExtraDetails(decoded);

    const plateInput = byId("plate-input");
    if (plateInput && lastVehicleProfile.plate_query) {
        plateInput.value = lastVehicleProfile.plate_query;
    }
    setText("plate-result", lastVehicleProfile.plate_message || tr("plate_default", "Enter a Dutch license plate manually to load RDW data."));
    setText("rdw-plate", displayValue(rdw.plate));
    setText("rdw-plate-mini", displayValue(rdw.plate));
    setText("rdw-brand", displayValue(rdw.brand));
    setText("rdw-brand-mini", displayValue(rdw.brand));
    setText("rdw-model", displayValue(rdw.model));
    setText("rdw-model-mini", displayValue(rdw.model));
    setText("rdw-vehicle-type", displayValue(rdw.vehicle_type));
    setText("rdw-fuel", displayValue(rdw.fuel));
    setText("rdw-fuel-mini", displayValue(rdw.fuel));
    setText("rdw-color", displayValue(rdw.color));
    setText("rdw-apk", formatCompactDate(rdw.apk_expiry));
    setText("rdw-first-registration", formatCompactDate(rdw.first_registration));
    setText("rdw-build-year", displayValue(buildYearFromDate(rdw.first_registration)));
    setText("rdw-engine-cc", rdw.engine_cc ? `${rdw.engine_cc} cc` : "--");
    setText("rdw-weight", rdw.weight_empty ? `${rdw.weight_empty} kg` : "--");

    const refreshButton = byId("refresh-vin-button");
    if (refreshButton) {
        refreshButton.disabled = vinRefreshPending || lastVehicleProfile.vin_status === "loading";
    refreshButton.innerText = (vinRefreshPending || lastVehicleProfile.vin_status === "loading") ? tr("reading", "Reading...") : tr("read_vin", "Read VIN");
    }

    const manualVinButton = byId("manual-vin-button");
    if (manualVinButton) {
        manualVinButton.disabled = vinRefreshPending || lastVehicleProfile.vin_status === "loading";
        manualVinButton.innerText = (vinRefreshPending || lastVehicleProfile.vin_status === "loading") ? tr("adapter_searching", "Searching") + "..." : tr("search_vin", "Search VIN");
    }

    const clearVinButton = byId("clear-vin-button");
    if (clearVinButton) {
        clearVinButton.disabled = vinRefreshPending || lastVehicleProfile.vin_status === "loading";
    }
}

function renderGauges(now = performance.now()) {
    const rpmNeedle = document.getElementById("rpm-needle");
    const speedNeedle = document.getElementById("speed-needle");
    const rpmValue = document.getElementById("rpm-value");
    const speedValue = document.getElementById("speed-value");
    const tachFace = document.querySelector(".tach-face");
    const speedFace = document.querySelector(".speed-face");

    if (!rpmNeedle || !rpmValue || !tachFace) {
        requestAnimationFrame(renderGauges);
        return;
    }

    const dt = clamp(lastGaugeFrameAt ? (now - lastGaugeFrameAt) / 1000 : 1 / 60, 1 / 240, 0.05);
    lastGaugeFrameAt = now;

    currentRpm = liveGaugeValue(currentRpm, targetRpm, RPM_DIRECT_SNAP, dt, RPM_SMOOTH_RESPONSE);
    currentSpeed = liveGaugeValue(currentSpeed, targetSpeed, SPEED_DIRECT_SNAP, dt, SPEED_SMOOTH_RESPONSE);

    const rpmAngle = mapRange(currentRpm, 0, TACH_MAX_RPM, TACH_MIN_DEG, TACH_MAX_DEG);
    const speedAngle = mapRange(currentSpeed, 0, SPEED_MAX, TACH_MIN_DEG, TACH_MAX_DEG);
    const rpmIntensity = clamp(currentRpm / TACH_MAX_RPM, 0, 1);
    const speedIntensity = clamp(currentSpeed / SPEED_MAX, 0, 1);

    rpmNeedle.style.transform = `rotate(${rpmAngle}deg)`;
    if (speedNeedle) {
        speedNeedle.style.transform = `rotate(${speedAngle}deg)`;
    }
    rpmValue.textContent = currentRpm / 1000 < 0.05 ? "0" : (currentRpm / 1000).toFixed(1);
    speedValue.textContent = currentSpeed < 0.05 ? "0" : String(Math.round(currentSpeed));
    tachFace.style.setProperty("--rpm-glow", `${0.12 + rpmIntensity * 0.5}`);
    tachFace.style.setProperty("--rpm-scale", `${1 + rpmIntensity * 0.01}`);
    if (speedFace) {
        speedFace.style.setProperty("--rpm-glow", `${0.12 + speedIntensity * 0.45}`);
        speedFace.style.setProperty("--rpm-scale", `${1 + speedIntensity * 0.008}`);
    }

    renderCharts(now);
    requestAnimationFrame(renderGauges);
}

function updateLiveData(vehicle) {
    const grid = byId("live-grid");
    if (!grid) return;

    const signature = JSON.stringify(vehicle || {});
    if (signature === lastRenderedLiveDataSignature) {
        return;
    }
    lastRenderedLiveDataSignature = signature;

    if (!vehicle || Object.keys(vehicle).length === 0) {
        Array.from(grid.children).forEach((child) => {
            if (!child.classList.contains("empty")) {
                child.remove();
            }
        });
        ensureEmptyState(grid, tr("no_live_data", "No live data available yet."));
        return;
    }

    clearContainerEmptyState(grid);
    const liveKeys = Object.keys(vehicle).filter((key) => key !== "rpm" && key !== "speed");
    const nextKeys = new Set(liveKeys);
    Array.from(grid.children).forEach((child) => {
        if (!nextKeys.has(child.dataset.key || "")) {
            child.remove();
        }
    });

    liveKeys.forEach((key) => {
        if (key === "rpm" || key === "speed") return;

        const item = vehicle[key];
        const hasNoData = isUnavailableValue(item.value);
        let row = grid.querySelector(`[data-key="${key}"]`);
        if (!row) {
            row = document.createElement("div");
            row.dataset.key = key;
            row.innerHTML = `<div class="sensor-row-main"><span class="sensor-label"></span><span class="sensor-row-meta"></span><canvas class="sensor-sparkline" width="140" height="20" hidden></canvas></div><div class="sensor-row-badges"><span class="sensor-warning-badge" hidden>WARN</span><span class="sensor-state-badge">SNSR</span></div><strong class="sensor-value"></strong>`;
            grid.appendChild(row);
        }
        row.className = "sensor-row";
        row.classList.toggle("is-stale", Boolean(item.stale));
        row.classList.toggle("is-no-data", hasNoData);
        const warning = hasNoData ? null : getSensorWarning(key, item.value, currentWarningThresholds, item.label);
        const warningBadge = row.querySelector(".sensor-warning-badge");
        const warningTitle = warning ? warningMessage(item.label, warning.value, warning.detail) : "";
        row.classList.toggle("is-warning", Boolean(warning));
        row.title = warningTitle;
        if (warningBadge) {
            warningBadge.hidden = !warning;
            warningBadge.title = warningTitle;
        }
        row.querySelector(".sensor-label").textContent = item.label;
        row.querySelector(".sensor-label").title = item.label;
        row.querySelector(".sensor-row-meta").textContent = hasNoData
            ? tr("sensor_unavailable", "Sensor unavailable right now")
            : item.stale
                ? tr("stale_at", "Stale | last ok {time}", { time: item.updated_at || "--" })
                : tr("updated_at", "Updated {time}", { time: item.updated_at || "--" });
        row.querySelector(".sensor-value").textContent = hasNoData ? tr("sensor_no_data", "No Data") : formatLiveValue(item.value, key);
        updateSensorSparkline(row.querySelector(".sensor-sparkline"), key, hasNoData ? null : item.value);
    });
}

const sensorSparklineHistory = new Map();
const SENSOR_SPARKLINE_POINTS = 40;

function updateSensorSparkline(canvas, key, rawValue) {
    if (!canvas) return;
    const looksNumeric = rawValue !== null && rawValue !== undefined && /-?\d+(?:[.,]\d+)?/.test(String(rawValue));
    if (!looksNumeric) {
        canvas.hidden = true;
        return;
    }
    const value = numberFromValue(rawValue);

    let history = sensorSparklineHistory.get(key);
    if (!history) {
        history = [];
        sensorSparklineHistory.set(key, history);
    }
    history.push(value);
    if (history.length > SENSOR_SPARKLINE_POINTS) {
        history.shift();
    }

    if (history.length < 2) {
        canvas.hidden = true;
        return;
    }

    canvas.hidden = false;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const min = Math.min(...history);
    const max = Math.max(...history);
    const range = max - min || 1;
    const padding = 3;

    ctx.beginPath();
    ctx.strokeStyle = "#0d6efd";
    ctx.lineWidth = 1.4;
    history.forEach((point, index) => {
        const x = (index / (history.length - 1)) * width;
        const y = height - padding - ((point - min) / range) * (height - padding * 2);
        if (index === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });
    ctx.stroke();
}

function updateCountDisplays(baseId, value) {
    const primary = document.getElementById(baseId);
    if (primary) primary.textContent = value;

    const secondary = document.getElementById(`${baseId}-codes`);
    if (secondary) secondary.textContent = value;
}

function updateDtcStatus(status) {
    lastDtcStatus = { ...lastDtcStatus, ...status };

    const scanButton = byId("scan-codes-button");
    const statusMessage = lastDtcStatus.message || tr("fault_codes_status", "No fault code scan run yet.");

    setText("codes-status-message", statusMessage);

    if (scanButton) {
        const isScanning = codeScanPending || Boolean(lastDtcStatus.scanning);
        scanButton.disabled = isScanning;
        scanButton.innerText = isScanning ? tr("scanning", "Scanning...") : tr("scan_codes", "Scan Codes");
    }
}

const SEEN_FAULT_CODES_KEY = "obdSeenFaultCodes";

function loadSeenFaultCodes() {
    try {
        const stored = JSON.parse(window.localStorage.getItem(SEEN_FAULT_CODES_KEY) || "[]");
        return Array.isArray(stored) ? stored : [];
    } catch {
        return [];
    }
}

const seenFaultCodes = new Set(loadSeenFaultCodes());
let faultCodesBaselineReady = seenFaultCodes.size > 0;

function saveSeenFaultCodes() {
    try {
        window.localStorage.setItem(SEEN_FAULT_CODES_KEY, JSON.stringify([...seenFaultCodes]));
    } catch {
        // Ignore storage failures (private browsing, quota, etc.).
    }
}

function notifyNewFaultCode(code, description) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
        new Notification(tr("fault_notification_title", "New fault code detected"), {
            body: description ? `${code} - ${description}` : code,
            tag: `obd-dtc-${code}`,
        });
    } catch (error) {
        console.error(error);
    }
}

function checkForNewFaultCodes(dtc) {
    if (!dtc) return;
    const allCodes = [...(dtc.stored || []), ...(dtc.pending || []), ...(dtc.permanent || [])];
    if (!allCodes.length) return;

    if (!faultCodesBaselineReady) {
        allCodes.forEach((item) => {
            if (item.code) seenFaultCodes.add(item.code);
        });
        faultCodesBaselineReady = true;
        saveSeenFaultCodes();
        return;
    }

    let hasNew = false;
    allCodes.forEach((item) => {
        const code = item.code;
        if (!code || seenFaultCodes.has(code)) return;
        seenFaultCodes.add(code);
        hasNew = true;
        notifyNewFaultCode(code, item.description_en || item.description || "");
    });
    if (hasNew) saveSeenFaultCodes();
}

function updateFaultAlertsUi() {
    const button = byId("fault-alerts-button");
    const copy = byId("fault-alerts-copy");
    if (!button) return;

    if (!("Notification" in window)) {
        button.hidden = true;
        if (copy) copy.textContent = tr("fault_alerts_unsupported", "Your browser does not support notifications.");
        return;
    }

    const permission = Notification.permission;
    if (permission === "granted") {
        button.textContent = tr("fault_alerts_enabled", "Fault Alerts On");
        button.disabled = true;
        if (copy) copy.textContent = tr("fault_alerts_on_copy", "You will get a browser notification when a new fault code appears.");
    } else if (permission === "denied") {
        button.textContent = tr("fault_alerts_blocked", "Notifications Blocked");
        button.disabled = true;
        if (copy) copy.textContent = tr("fault_alerts_blocked_copy", "Notifications are blocked in your browser settings for this site.");
    } else {
        button.textContent = tr("fault_alerts_enable", "Enable Fault Alerts");
        button.disabled = false;
        if (copy) copy.textContent = tr("fault_alerts_copy", "Get a browser notification when a new fault code is detected while this tab is open.");
    }
}

function requestFaultAlerts() {
    if (!("Notification" in window)) return;
    Notification.requestPermission().then(() => updateFaultAlertsUi());
}

function updateCodes(elementId, codes) {
    const container = byId(elementId);
    if (!container) return;

    const signature = JSON.stringify({
        has_scan: Boolean(lastDtcStatus.has_scan),
        codes: codes || []
    });
    if (signature === lastRenderedCodes[elementId]) {
        updateCountDisplays(elementId.replace("-codes", "-count"), codes ? codes.length : 0);
        return;
    }
    lastRenderedCodes[elementId] = signature;

    const countId = elementId.replace("-codes", "-count");
    const total = codes ? codes.length : 0;

    updateCountDisplays(countId, total);
    container.replaceChildren();

    if (!codes || codes.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = lastDtcStatus.has_scan ? tr("fault_codes_empty", "No fault codes found.") : tr("fault_codes_scan_first", "Run a manual fault code scan first.");
        container.appendChild(empty);
        return;
    }

    codes.forEach((item, index) => {
        const isMisfire = item.misfire !== null && item.misfire !== undefined;
        const code = document.createElement("div");
        code.className = `code severity-${item.severity || "unknown"}${isMisfire ? " misfire" : ""}`;
        code.style.animationDelay = `${Math.min(index * 24, 260)}ms`;

        const title = document.createElement("strong");
        title.textContent = item.code;

        const description = document.createElement("p");
    description.textContent = item.description_en || item.description || tr("no_description", "No description available");

        const meta = document.createElement("span");
        meta.className = "code-meta";
    meta.textContent = `${item.system || tr("unknown_system", "Unknown system")} - ${severityLabel(item.severity)}`;

        const action = document.createElement("p");
        action.className = "code-action";
    action.textContent = item.action_hint || tr("inspect_code_hint", "Inspect this code before making a decision.");

        code.append(title, meta, description, action);

        if (item.code_type && String(item.code_type).toLowerCase() !== "generic") {
            const typeBadge = document.createElement("span");
            typeBadge.className = "code-type-badge";
            typeBadge.textContent = item.code_type;
            code.appendChild(typeBadge);
        }

        if (isMisfire) {
            const misfire = document.createElement("p");
            misfire.className = "misfire-note";
            misfire.textContent = `Misfire detection: ${item.misfire}`;
            code.appendChild(misfire);
        }

        if (item.possible_causes && item.possible_causes.length > 0) {
            const causes = document.createElement("ul");
            causes.className = "cause-list";
            item.possible_causes.slice(0, 5).forEach((cause) => {
                const causeItem = document.createElement("li");
                causeItem.textContent = cause;
                causes.appendChild(causeItem);
            });
            code.appendChild(causes);
        }

        container.appendChild(code);
    });
}

function severityLabel(severity) {
    if (severity === "high") return "High";
    if (severity === "medium") return "Medium";
    if (severity === "low") return "Low";
    return tr("unknown", "Unknown");
}

function updateServiceModeUi() {
    // Reading and clearing codes are always available; clearing is guarded by
    // the confirmation dialog in clearDTC() rather than by a mode switch.
    const ready = isConnected || demoMode;
    document.querySelectorAll("#clear-button, #clear-button-codes").forEach((button) => {
        button.disabled = !ready;
        button.title = ready
            ? tr("clear_codes_hint", "Asks for confirmation before clearing")
            : tr("clear_codes_offline", "Connect to a vehicle first");
    });

    setText("system-mode", ready
        ? tr("system_mode_service", "Service ready")
        : tr("system_mode_offline", "Offline"));
}

function updateFreezeUi() {
    const button = document.getElementById("freeze-button");
    const indicator = document.getElementById("freeze-indicator");

    button.classList.toggle("is-active", isFrozen);
    button.innerText = isFrozen ? tr("freeze_resume", "Resume Stream") : tr("freeze_pause", "Freeze Stream");
    indicator.innerText = isFrozen ? tr("freeze_frozen", "Live stream frozen for review") : tr("freeze_live", "Live stream active");
}

function toggleFreeze() {
    if (isFrozen) {
        isFrozen = false;
        frozenPayload = null;
    } else {
        const status = { connected: isConnected };
        const rpmText = document.getElementById("rpm-value")?.innerText || "0";
        const speedText = document.getElementById("speed-value")?.innerText || "0";
        frozenPayload = {
            status,
            vehicle: {
                rpm: { value: rpmText },
                speed: { value: speedText }
            }
        };
        if (lastLivePayload) {
            frozenPayload = lastLivePayload;
        }
        isFrozen = true;
    }

    updateFreezeUi();
}

async function reconnectObd() {
    const button = document.getElementById("reconnect-button");
    const resultElement = document.getElementById("port-result");

    button.disabled = true;
    button.classList.add("connecting");
        button.innerText = tr("reconnecting", "Reconnecting...");
    resultElement.innerText = tr("manual_reconnect_running", "Reconnecting to OBD adapter...");

    try {
        const response = await fetch("/api/reconnect", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);

        vinRefreshPending = false;
        isFrozen = false;
        frozenPayload = null;
        updateFreezeUi();
    resultElement.innerText = result.message || tr("manual_reconnect_started", "Reconnect started.");
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
        resultElement.innerText = error.message || tr("manual_reconnect_failed", "Reconnect failed.");
        button.disabled = false;
        button.classList.remove("connecting");
        button.innerText = tr("retry_connection", "Retry Connection");
    }
}

async function fetchVehicleProfile(force = false) {
    if (vinRefreshPending && !force) return;

    vinRefreshPending = true;
    updateVehicleProfileView({ vin_status: "loading", vin_message: tr("vin_reading_vehicle", "Reading VIN from vehicle...") });

    try {
        const response = await fetch("/api/vehicle/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        const result = await response.json();
        updateVehicleProfileView(result.vehicle_profile || {});
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
    } catch (error) {
        console.error(error);
        document.getElementById("vin-message").innerText = error.message || tr("vin_lookup_failed", "VIN lookup failed.");
    } finally {
        vinRefreshPending = false;
    }
}

async function savePlateLookup(event) {
    event.preventDefault();

    const plateInput = document.getElementById("plate-input");
    const resultElement = document.getElementById("plate-result");

    resultElement.innerText = tr("rdw_lookup_loading", "Looking up RDW vehicle data...");

    try {
        const response = await fetch("/api/vehicle/plate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ plate: plateInput.value.trim() })
        });
        const result = await response.json();
        updateVehicleProfileView(result.vehicle_profile || {});
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        saveVehicleLookupHistory({
            type: "plate",
            value: plateInput.value.trim().toUpperCase(),
            label: tr("history_type_plate", "Plate")
        });
    } catch (error) {
        console.error(error);
        resultElement.innerText = error.message || tr("rdw_lookup_failed", "RDW lookup failed.");
    }
}

async function saveManualVin(event) {
    event.preventDefault();

    if (vinRefreshPending) return;

    const vinInput = document.getElementById("manual-vin-input");
    const resultElement = document.getElementById("manual-vin-result");

    vinRefreshPending = true;
    resultElement.innerText = tr("vin_lookup_loading", "Looking up VIN...");
    updateVehicleProfileView({
        vin: vinInput.value.trim().toUpperCase(),
        vin_status: "loading",
        vin_message: tr("vin_processing", "Processing manual VIN...")
    });

    try {
        const response = await fetch("/api/vehicle/manual", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ vin: vinInput.value.trim() })
        });
        const result = await response.json();
        updateVehicleProfileView(result.vehicle_profile || {});
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        saveVehicleLookupHistory({
            type: "vin",
            value: vinInput.value.trim().toUpperCase(),
            label: tr("history_type_vin", "VIN")
        });
        resultElement.innerText = result.message || tr("vin_found", "VIN found.");
    } catch (error) {
        console.error(error);
        resultElement.innerText = error.message || tr("vin_lookup_failed", "VIN lookup failed.");
    } finally {
        vinRefreshPending = false;
        updateVehicleProfileView({});
    }
}

function clearManualVinInput() {
    const vinInput = document.getElementById("manual-vin-input");
    const resultElement = document.getElementById("manual-vin-result");

    if (vinRefreshPending || !vinInput) return;

    vinInput.value = "";
    if (resultElement) {
        resultElement.innerText = tr("vin_input_cleared", "VIN input cleared.");
    }
}

function loadVehicleLookupHistory() {
    try {
        const stored = window.localStorage.getItem(VEHICLE_LOOKUP_HISTORY_KEY);
        const items = stored ? JSON.parse(stored) : [];
        return Array.isArray(items) ? items : [];
    } catch {
        return [];
    }
}

function saveVehicleLookupHistory(entry) {
    const value = String(entry.value || "").trim();
    if (!value) return;

    const history = loadVehicleLookupHistory();
    const normalizedValue = value.toUpperCase();
    const existingIndex = history.findIndex((item) => item.type === entry.type && item.value === normalizedValue);
    if (existingIndex !== -1) {
        history.splice(existingIndex, 1);
    }

    history.unshift({
        type: entry.type,
        value: normalizedValue,
        label: entry.label,
        created_at: new Date().toISOString()
    });

    window.localStorage.setItem(VEHICLE_LOOKUP_HISTORY_KEY, JSON.stringify(history.slice(0, VEHICLE_LOOKUP_HISTORY_LIMIT)));
    renderVehicleLookupHistory();
}

function formatHistoryTimestamp(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch {
        return String(isoString || "");
    }
}

function renderVehicleLookupHistory() {
    const historyElement = document.getElementById("vehicle-history");
    if (!historyElement) return;

    const historyItems = loadVehicleLookupHistory();
    historyElement.replaceChildren();

    if (!historyItems.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("history_empty", "No lookup history yet.");
        historyElement.appendChild(empty);
        return;
    }

    historyItems.forEach((item) => {
        const row = document.createElement("div");
        row.className = "history-row";
        row.style.cursor = "pointer";
        row.innerHTML = `
            <div>
                <strong>${item.value}</strong>
                <p>${item.label}</p>
                <span>${formatHistoryTimestamp(item.created_at)}</span>
            </div>
        `;

        row.addEventListener("click", () => {
            if (item.type === "vin") {
                const vinInput = document.getElementById("manual-vin-input");
                if (vinInput) {
                    vinInput.value = item.value;
                    saveManualVin({ preventDefault: () => {} });
                }
                return;
            }

            const plateInput = document.getElementById("plate-input");
            if (plateInput) {
                plateInput.value = item.value;
                savePlateLookup({ preventDefault: () => {} });
            }
        });

        historyElement.appendChild(row);
    });
}

async function savePort(event) {
    event.preventDefault();

    const portInput = document.getElementById("port-input");
    const resultElement = document.getElementById("port-result");
    const port = portInput.value.trim();

    resultElement.innerText = tr("port_save_loading", "Saving COM port...");

    try {
        const response = await fetch("/api/config/port", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ port })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);

        portInput.value = result.obd_port || "";
        renderPortOptions(result.detected_ports || [], result.obd_port || "");
        resultElement.innerText = result.message || tr("port_saved", "COM port saved.");
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
        resultElement.innerText = error.message || tr("port_save_failed", "Could not save COM port.");
    }
}

let selectedBluetoothMac = "";

async function initBluetoothPanel() {
    const scanBox = byId("bluetooth-scan-box");
    const unsupportedNote = byId("bluetooth-unsupported-note");
    if (!scanBox || !unsupportedNote) return;

    try {
        const response = await fetch("/api/bluetooth/devices");
        const data = await response.json();
        if (data.supported) {
            scanBox.hidden = false;
        } else {
            unsupportedNote.hidden = false;
        }
    } catch (error) {
        console.error(error);
    }
}

function renderBluetoothDevices(devices) {
    const list = byId("bluetooth-device-list");
    const bindButton = byId("bluetooth-bind-button");
    if (!list) return;
    list.replaceChildren();
    selectedBluetoothMac = "";
    if (bindButton) bindButton.hidden = true;

    if (!devices.length) {
        const empty = document.createElement("p");
        empty.className = "panel-note";
        empty.textContent = tr("bluetooth_no_devices", "No paired Bluetooth devices found. Pair the adapter in your OS Bluetooth settings first.");
        list.appendChild(empty);
        return;
    }

    devices.forEach((device) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "bluetooth-device-option";
        option.dataset.mac = device.mac;
        option.innerHTML = `
            <span class="bluetooth-device-option-info">
                <strong>${escapeHtml(device.name)}</strong>
                <span>${escapeHtml(device.mac)}</span>
            </span>
        `;
        option.addEventListener("click", () => {
            selectedBluetoothMac = device.mac;
            list.querySelectorAll(".bluetooth-device-option").forEach((el) => el.classList.remove("is-selected"));
            option.classList.add("is-selected");
            if (bindButton) bindButton.hidden = false;
        });
        list.appendChild(option);
    });
}

async function scanBluetoothDevices() {
    const resultElement = byId("bluetooth-result");
    if (resultElement) resultElement.innerText = tr("bluetooth_scanning", "Scanning for paired Bluetooth devices...");

    try {
        const response = await fetch("/api/bluetooth/devices");
        const data = await response.json();
        renderBluetoothDevices(data.devices || []);
        if (resultElement) resultElement.innerText = "";
    } catch (error) {
        console.error(error);
        if (resultElement) resultElement.innerText = tr("bluetooth_bind_failed", "Adapter could not be bound.");
    }
}

async function bindBluetoothDevice() {
    const resultElement = byId("bluetooth-result");
    if (!selectedBluetoothMac) return;
    if (resultElement) resultElement.innerText = tr("bluetooth_binding", "Binding adapter...");

    try {
        const response = await fetch("/api/bluetooth/bind", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mac: selectedBluetoothMac })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);

        if (resultElement) {
            resultElement.innerText = tr("bluetooth_bind_success", "Adapter bound to {device}. Select it above and test the connection.", { device: result.device });
        }
        renderPortOptions(result.ports || [], result.device || "");
        const portInput = byId("port-input");
        if (portInput) portInput.value = result.device || "";
    } catch (error) {
        console.error(error);
        if (resultElement) resultElement.innerText = error.message || tr("bluetooth_bind_failed", "Adapter could not be bound.");
    }
}

async function toggleDemoMode() {
    const button = byId("demo-mode-button");
    if (button) {
        button.disabled = true;
    }

    try {
        const response = await fetch("/api/demo-mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: !demoMode })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || `Server returned status ${response.status}`);
        }

        demoMode = Boolean(result.demo_mode);
        updateDemoModeUi();
        await loadConfig();
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
    } finally {
        if (button) {
            button.disabled = false;
        }
    }
}

async function toggleLimitedMode() {
    const button = byId("limited-mode-button");
    if (button) {
        button.disabled = true;
    }

    limitedMode = !limitedMode;
    updateLimitedModeUi();

    try {
        const response = await fetch("/api/limited-mode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: limitedMode })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || `Server returned status ${response.status}`);
        }

        limitedMode = Boolean(result.limited_mode);
        updateLimitedModeUi();
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
        limitedMode = !limitedMode;
        updateLimitedModeUi();
    } finally {
        if (button) {
            button.disabled = false;
        }
    }
}

async function setPollProfile(profile) {
    pollProfile = String(profile || "balanced");
    saveLocalPollProfile(pollProfile);
    updatePollProfileUi({ id: pollProfile });

    try {
        const response = await fetch("/api/poll-profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile: pollProfile })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || `Server returned status ${response.status}`);
        }

        pollProfile = String(result.poll_profile?.id || pollProfile);
        limitedMode = Boolean(result.limited_mode);
        updateLimitedModeUi();
        updatePollProfileUi(result.poll_profile || { id: pollProfile });
        fetchSupportedSensors();
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
        updatePollProfileUi({ id: pollProfile });
    }
}

async function setDemoPreset(preset) {
    if (demoPresetRequestPending) return;

    demoPresetRequestPending = true;
    updateDemoPresetUi();

    try {
        const response = await fetch("/api/demo-mode/preset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ preset })
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || `Server returned status ${response.status}`);
        }

        demoPreset = String(result.demo_preset || preset || "idle");
        storeDemoPresets(result.demo_presets || []);
        updateDemoModeUi();
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
    } finally {
        demoPresetRequestPending = false;
        updateDemoPresetUi();
    }
}

async function testConnection() {
    const button = byId("test-connection-button");
    const resultElement = byId("test-connection-result");
    const stepsContainer = byId("test-connection-steps");

    if (!button || !resultElement || !stepsContainer) return;

    button.disabled = true;
    button.innerText = tr("testing", "Testing...");
    resultElement.innerText = tr("connection_test_running", "Testing adapter and ECU connection...");
    stepsContainer.replaceChildren();

    try {
        const response = await fetch("/api/connection/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        const result = await response.json();

        resultElement.innerText = result.phase || (result.success ? tr("connection_test_passed", "Connection test passed.") : tr("connection_test_failed", "Connection test failed."));

        const steps = result.steps || [];
        if (steps.length === 0) {
            const empty = document.createElement("div");
            empty.className = "empty";
            empty.textContent = tr("connection_test_empty", "No diagnostic steps returned.");
            stepsContainer.appendChild(empty);
        }

        steps.forEach((step, index) => {
            const row = document.createElement("div");
            row.className = `support-row ${step.ok ? "is-supported" : "is-unsupported"}`;
            row.style.animationDelay = `${Math.min(index * 18, 220)}ms`;
            row.innerHTML = `<div><strong>${escapeHtml(step.name || "")}</strong><p>${escapeHtml(step.detail || "")}</p></div><span class="support-badge ${step.ok ? "status-good" : "status-warning"}">${step.ok ? "OK" : "Check"}</span>`;
            stepsContainer.appendChild(row);
        });
    } catch (error) {
        console.error(error);
        resultElement.innerText = tr("connection_test_failed", "Connection test failed.");
    } finally {
        button.disabled = false;
        button.innerText = tr("test_connection", "Test Connection");
    }
}

let vehicleScanPollTimer = null;

const SCAN_STATUS_LABELS = {
    waiting: "Waiting",
    scanning: "Scanning...",
    complete: "Complete",
    no_faults: "No Faults Found",
    faults_found: "Faults Found",
    warning: "Warning",
    not_supported: "Not Supported",
    no_data: "No Data",
    error: "Communication Error",
    cancelled: "Cancelled",
};

const SCAN_STATUS_ICONS = {
    waiting: "o",
    scanning: ">",
    complete: "OK",
    no_faults: "OK",
    faults_found: "!",
    warning: "!",
    not_supported: "-",
    no_data: "-",
    error: "x",
    cancelled: "x",
};

function renderVehicleScanProgress(scan) {
    const container = byId("vehicle-scan-progress");
    if (!container || !scan || !Array.isArray(scan.parts)) return;

    container.hidden = false;

    const untouched = scan.parts.every((p) => p.status === "waiting");
    if (!scan.active && scan.connection_error && untouched) {
        const list = byId("scan-parts-list");
        if (list) list.innerHTML = "";
        const fractionEl = byId("scan-progress-fraction");
        if (fractionEl) fractionEl.textContent = "Not started";
        const fillEl = byId("scan-progress-bar-fill");
        if (fillEl) fillEl.style.width = "0%";
        const cancelButton = byId("cancel-scan-button");
        if (cancelButton) cancelButton.hidden = true;
        const summaryEl = byId("scan-summary");
        if (summaryEl) {
            summaryEl.hidden = false;
            summaryEl.className = "scan-summary attention";
            summaryEl.innerHTML = `<strong>SCAN NOT STARTED</strong><br>${escapeHtml(scan.connection_error || "")}`;
        }
        return;
    }

    const total = scan.parts.length;
    const doneCount = scan.parts.filter((p) => p.status !== "waiting" && p.status !== "scanning").length;
    const activeIndex = Math.max(scan.current_index ?? -1, 0);

    const fractionEl = byId("scan-progress-fraction");
    if (fractionEl) {
        fractionEl.textContent = scan.active
            ? `Part ${activeIndex + 1} of ${total}`
            : `${doneCount} of ${total} complete`;
    }

    const fillEl = byId("scan-progress-bar-fill");
    if (fillEl) {
        const pct = total ? Math.round((doneCount / total) * 100) : 0;
        fillEl.style.width = `${pct}%`;
    }

    const list = byId("scan-parts-list");
    if (list) {
        list.innerHTML = "";
        scan.parts.forEach((part) => {
            const row = document.createElement("li");
            row.className = "scan-part-row";
            row.dataset.status = part.status;

            const icon = document.createElement("span");
            icon.className = "scan-part-icon";
            icon.textContent = SCAN_STATUS_ICONS[part.status] || "o";

            const name = document.createElement("span");
            name.className = "scan-part-name";
            name.textContent = part.name;

            const result = document.createElement("span");
            result.className = "scan-part-result";
            result.textContent = part.status === "scanning"
                ? (part.detail || "Scanning...")
                : (part.result || SCAN_STATUS_LABELS[part.status] || "");

            row.append(icon, name, result);
            list.appendChild(row);
        });
    }

    const cancelButton = byId("cancel-scan-button");
    if (cancelButton) cancelButton.hidden = !scan.active;

    const summaryEl = byId("scan-summary");
    if (summaryEl) {
        if (!scan.active && scan.summary) {
            const s = scan.summary;
            summaryEl.hidden = false;
            summaryEl.className = `scan-summary ${s.overall_status === "attention_required" ? "attention" : "all-clear"}`;
            summaryEl.innerHTML = `
                <strong>Diagnostic Scan Complete</strong><br>
                VIN: ${escapeHtml(s.vin || "Unknown")}<br>
                Stored DTCs: ${escapeHtml(s.stored_dtc_count ?? 0)} &middot; Pending: ${escapeHtml(s.pending_dtc_count ?? 0)} &middot; Permanent: ${escapeHtml(s.permanent_dtc_count ?? 0)}<br>
                Readiness: ${escapeHtml(s.readiness_ready ?? 0)} Ready / ${escapeHtml(s.readiness_incomplete ?? 0)} Incomplete<br>
                Overall Status: ${s.overall_status === "attention_required" ? "ATTENTION REQUIRED" : "ALL CLEAR"}
            `;
        } else if (!scan.active && scan.interrupted) {
            summaryEl.hidden = false;
            summaryEl.className = "scan-summary attention";
            summaryEl.innerHTML = `<strong>SCAN INTERRUPTED</strong><br>${escapeHtml(scan.connection_error || "Vehicle communication was lost during the scan.")}`;
        } else {
            summaryEl.hidden = true;
        }
    }
}

async function pollVehicleScanStatus() {
    try {
        const response = await fetch("/api/scan/vehicle/status");
        const result = await response.json();
        const scan = result.scan || {};

        renderVehicleScanProgress(scan);
        updateDtcStatus(result.dtc_status || {});
        updateCodes("stored-codes", result.dtc?.stored || []);
        updateCodes("pending-codes", result.dtc?.pending || []);
        updateCodes("permanent-codes", result.dtc?.permanent || []);
        checkForNewFaultCodes(result.dtc || {});
        updateHealth(result.health || {});
        updateFreezeFrame(result.freeze_frame || {});
        updateReadiness(result.readiness || {});
        updateReport(result.report || {});

        if (scan.active) {
            vehicleScanPollTimer = setTimeout(pollVehicleScanStatus, 400);
        } else {
            codeScanPending = false;
            const scanButton = byId("scan-codes-button");
            if (scanButton) scanButton.disabled = false;
        }
    } catch (error) {
        console.error(error);
        codeScanPending = false;
        const scanButton = byId("scan-codes-button");
        if (scanButton) scanButton.disabled = false;
    }
}

async function scanCodes() {
    if (codeScanPending) return;
    codeScanPending = true;

    const scanButton = byId("scan-codes-button");
    if (scanButton) scanButton.disabled = true;

    updateDtcStatus({
        ...lastDtcStatus,
        scanning: true,
        message: tr("scanning_fault_codes", "Scanning fault codes...")
    });

    try {
        const response = await fetch("/api/scan/vehicle/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || `Server returned status ${response.status}`);
        }
        renderVehicleScanProgress(result.scan || {});
        clearTimeout(vehicleScanPollTimer);
        pollVehicleScanStatus();
    } catch (error) {
        console.error(error);
        updateDtcStatus({
            ...lastDtcStatus,
            has_scan: false,
            scanning: false,
            message: error.message || tr("fault_code_scan_failed", "Fault code scan failed.")
        });
        codeScanPending = false;
        if (scanButton) scanButton.disabled = false;
    }
}

async function cancelVehicleScan() {
    const cancelButton = byId("cancel-scan-button");
    if (cancelButton) cancelButton.disabled = true;

    try {
        const response = await fetch("/api/scan/vehicle/cancel", { method: "POST" });
        const result = await response.json().catch(() => ({}));
        if (result.scan) {
            renderVehicleScanProgress(result.scan);
        }
        updateDtcStatus({
            ...lastDtcStatus,
            scanning: false,
            message: result.message || tr("scan_cancelled", "Vehicle diagnostic scan cancelled.")
        });
        codeScanPending = false;
        const scanButton = byId("scan-codes-button");
        if (scanButton) scanButton.disabled = false;
        clearTimeout(vehicleScanPollTimer);
        pollVehicleScanStatus();
    } catch (error) {
        console.error(error);
    } finally {
        if (cancelButton) cancelButton.disabled = false;
    }
}

async function clearDTC() {
    const clearButton = this instanceof HTMLElement ? this : byId("clear-button");
    const resultTarget = clearButton?.dataset.resultTarget || "clear-result";
    const resultElement = byId(resultTarget);

    const confirmed = await openConfirmDialog(
        tr("clear_codes_confirm_title", "Clear fault codes?"),
        tr("clear_codes_confirm_message", "This erases stored fault codes and freeze-frame data from the ECU and resets the readiness monitors. The fault itself is not repaired: if it is still present, the code comes back. Continue?")
    );
    if (!confirmed) {
        if (resultElement) {
        resultElement.innerText = tr("clear_cancelled", "Clear cancelled.");
        }
        return;
    }

    if (resultElement) {
    resultElement.innerText = tr("clear_sending", "Sending clear command...");
    }
    if (clearButton) {
        clearButton.disabled = true;
    }

    try {
        const response = await fetch("/api/clear", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: "YES" })
        });
        const result = await response.json();
        if (resultElement) {
        resultElement.innerText = result.message || tr("command_processed", "Command processed.");
        }
        updateDtcStatus({
            has_scan: lastDtcStatus.has_scan,
            scanning: false,
            last_scan: lastDtcStatus.last_scan || null,
            message: tr("clear_codes_sent_verify", "Clear command sent. Run a new scan to verify the ECU is clean.")
        });
        fetchData();
checkForUpdates();
    } catch (error) {
        console.error(error);
        if (resultElement) {
        resultElement.innerText = tr("clear_command_failed", "Clear command failed. Check the connection.");
        }
    } finally {
        updateServiceModeUi();
    }
}

async function fetchSupportedSensors() {
    const supportedContainer = document.getElementById("supported-sensors");
    const unsupportedContainer = document.getElementById("unsupported-sensors");

    supportedContainer.innerHTML = `<p>${tr("supported_loading", "Loading supported sensors...")}</p>`;
    unsupportedContainer.innerHTML = `<p>${tr("unsupported_loading", "Loading unsupported sensors...")}</p>`;

    try {
        const response = await fetch("/api/supported");
        const result = await response.json();
        setText(
            "standard-obd-note",
            result.standard_obd_only
            ? tr("standard_obd_note", "Standard OBD only: engine and emission related ECU codes. ABS, airbag and body modules may require a brand-specific scanner.")
            : tr("enhanced_module_active", "Enhanced module support active.")
        );
        updatePidSupportSummary(result);
        renderSensorSupportList(supportedContainer, result.supported || [], true);
        renderSensorSupportList(unsupportedContainer, result.unsupported || [], false);
    } catch (error) {
        console.error(error);
        supportedContainer.innerHTML = `<p>${tr("supported_failed", "Could not load supported sensor list.")}</p>`;
        unsupportedContainer.innerHTML = `<p>${tr("unsupported_failed", "Could not load unsupported sensor list.")}</p>`;
    }
}

function renderSensorSupportList(container, items, supported) {
    container.replaceChildren();

    if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = supported ? tr("supported_empty", "No supported sensors detected yet.") : tr("unsupported_empty", "No unsupported sensor info yet.");
        container.appendChild(empty);
        return;
    }

    items.forEach((item, index) => {
        const row = document.createElement("div");
        row.className = `support-row ${supported ? "is-supported" : "is-unsupported"}`;
        row.style.animationDelay = `${Math.min(index * 18, 220)}ms`;

        const left = document.createElement("div");
        left.innerHTML = `<strong>${item.label}</strong><p>${item.command}</p>`;

        const badge = document.createElement("span");
        badge.className = `support-badge ${supported ? "status-good" : "status-warning"}`;
        badge.textContent = supported ? tr("available", "Available") : tr("not_supported", "Not supported");

        row.append(left, badge);
        container.appendChild(row);
    });
}

async function checkChangelog() {
    try {
        const response = await fetch("/api/changelog", { signal: AbortSignal.timeout(3500) });
        if (!response.ok) return;
        const result = await response.json();
        if (!result.success || !result.should_show) return;
        showChangelogDialog(result.current_version, result.changelog || []);
    } catch (error) {
        console.error(error);
    }
}

function showChangelogDialog(version, entries) {
    const overlay = byId("changelog-overlay");
    const versionEl = byId("changelog-version");
    const entriesEl = byId("changelog-entries");
    const closeButton = byId("changelog-close-button");
    if (!overlay || !entriesEl || !closeButton) return;

    if (versionEl) versionEl.textContent = version;

    entriesEl.innerHTML = "";
    entries.forEach((entry) => {
        const block = document.createElement("div");
        block.className = "changelog-entry";

        const header = document.createElement("div");
        header.className = "changelog-entry-header";
        header.innerHTML = `<span>${escapeHtml(entry.version || "")}</span><span class="changelog-entry-date">${escapeHtml(entry.date || "")}</span>`;

        const list = document.createElement("ul");
        (entry.changes || []).forEach((change) => {
            const li = document.createElement("li");
            li.textContent = change;
            list.appendChild(li);
        });

        block.append(header, list);
        entriesEl.appendChild(block);
    });

    overlay.hidden = false;
    overlay.classList.remove("is-closing");
    overlay.classList.add("is-open");

    const closeDialog = async () => {
        overlay.classList.remove("is-open");
        overlay.classList.add("is-closing");
        window.setTimeout(() => {
            overlay.classList.remove("is-closing");
            overlay.hidden = true;
        }, 220);
        closeButton.removeEventListener("click", closeDialog);
        overlay.removeEventListener("click", handleBackdrop);
        try {
            await fetch("/api/changelog/ack", { method: "POST" });
        } catch (error) {
            console.error(error);
        }
    };
    const handleBackdrop = (event) => {
        if (event.target === overlay) closeDialog();
    };

    closeButton.addEventListener("click", closeDialog);
    overlay.addEventListener("click", handleBackdrop);
}

function openConfirmDialog(title, message) {
    const overlay = byId("confirm-overlay");
    const titleElement = byId("confirm-title");
    const messageElement = byId("confirm-message");
    const yesButton = byId("confirm-yes-button");
    const noButton = byId("confirm-no-button");

    if (!overlay || !titleElement || !messageElement || !yesButton || !noButton) {
        return Promise.resolve(window.confirm(message));
    }

    titleElement.innerText = title;
    messageElement.innerText = message;
    overlay.hidden = false;
    overlay.classList.remove("is-closing");
    overlay.classList.add("is-open");

    return new Promise((resolve) => {
        let settled = false;
        const cleanup = (result) => {
            if (settled) return;
            settled = true;
            overlay.classList.remove("is-open");
            overlay.classList.add("is-closing");
            window.setTimeout(() => {
                overlay.classList.remove("is-closing");
                overlay.hidden = true;
            }, 220);
            yesButton.removeEventListener("click", handleYes);
            noButton.removeEventListener("click", handleNo);
            overlay.removeEventListener("click", handleBackdrop);
            resolve(result);
        };

        const handleYes = () => cleanup(true);
        const handleNo = () => cleanup(false);
        const handleBackdrop = (event) => {
            if (event.target === overlay) {
                cleanup(false);
            }
        };

        yesButton.addEventListener("click", handleYes);
        noButton.addEventListener("click", handleNo);
        overlay.addEventListener("click", handleBackdrop);
    });
}

async function fetchScanHistory() {
    const history = document.getElementById("scan-history");
    history.innerHTML = `<p>${tr("loading_saved_scans", "Loading saved scans...")}</p>`;

    try {
        const response = await fetch("/api/scans");
        const scans = await response.json();
        renderScanHistory(scans || []);
    } catch (error) {
        console.error(error);
        history.innerHTML = `<p>${tr("saved_scans_failed", "Could not load saved scan history.")}</p>`;
    }
}

function renderScanHistory(scans) {
    const history = document.getElementById("scan-history");
    history.replaceChildren();

    if (!scans.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("no_scans_saved", "No scans saved yet.");
        history.appendChild(empty);
        return;
    }

    scans.forEach((scan, index) => {
        const row = document.createElement("div");
        row.className = "history-row";
        row.style.animationDelay = `${Math.min(index * 24, 260)}ms`;

        const meta = document.createElement("div");
        const scanStatus = scan.payload?.status || {};
        const connectionLabel = scanStatus.connected ? tr("history_connection_connected", "Connected") : tr("history_connection_offline", "Offline");
        meta.innerHTML = `<strong>${escapeHtml(scan.label || "")}</strong><p>${escapeHtml(scan.created_at || "")}</p><span>${escapeHtml(scan.summary || "")}</span><p>${escapeHtml(connectionLabel)} | ${escapeHtml(scanStatus.protocol || "Unknown")} | ${escapeHtml(scanStatus.current_port || tr("port_none_selected", "No COM port selected"))}</p>`;

        const payload = scan.payload || {};
        const detail = document.createElement("div");
        detail.className = "history-health";
        detail.innerHTML = `<strong>${displayValue(payload.health?.score, "--")}</strong><p>${tr("history_health_score", "Health score")}</p>`;

        const actions = document.createElement("div");
        actions.className = "history-row-actions";
        const deleteButton = document.createElement("button");
        deleteButton.className = "history-delete-button";
        deleteButton.type = "button";
        deleteButton.textContent = tr("delete", "Delete");
        deleteButton.title = tr("scan_delete_title", "Delete saved scan");
        deleteButton.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteSavedScan(scan.id);
        });

        const shareButton = document.createElement("button");
        shareButton.className = "history-delete-button";
        shareButton.type = "button";
        shareButton.textContent = scan.share_token ? tr("scan_copy_link", "Copy Link") : tr("scan_share", "Share");
        shareButton.title = tr("scan_share_title", "Copy a read-only report link");
        shareButton.addEventListener("click", (event) => {
            event.stopPropagation();
            if (scan.share_token) {
                copyShareLink(`${window.location.origin}/share/${scan.share_token}`, shareButton);
            } else {
                shareScan(scan.id, shareButton);
            }
        });
        actions.append(shareButton);

        if (scan.share_token) {
            const unshareButton = document.createElement("button");
            unshareButton.className = "history-delete-button";
            unshareButton.type = "button";
            unshareButton.textContent = tr("scan_unshare", "Unshare");
            unshareButton.title = tr("scan_unshare_title", "Revoke this share link");
            unshareButton.addEventListener("click", (event) => unshareScan(scan.id, event));
            actions.append(unshareButton);
        }

        actions.append(deleteButton);

        row.append(meta, detail, actions);
        row.addEventListener("click", () => {
            history.querySelectorAll(".history-row").forEach((other) => other.classList.remove("is-selected"));
            row.classList.add("is-selected");
            renderScanHistoryDetail(scan);
        });
        history.appendChild(row);
    });
}

async function copyShareLink(url, button) {
    try {
        await navigator.clipboard.writeText(url);
        const original = button.textContent;
        button.textContent = tr("scan_share_copied", "Link copied!");
        window.setTimeout(() => {
            button.textContent = original;
        }, 1800);
    } catch {
        window.prompt(tr("scan_share_copy_manual", "Copy this link:"), url);
    }
}

async function shareScan(scanId, button) {
    button.disabled = true;
    try {
        const response = await fetch(`/api/scans/${encodeURIComponent(scanId)}/share`, { method: "POST" });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        await copyShareLink(result.share_url, button);
        fetchScanHistory();
    } catch (error) {
        console.error(error);
        window.alert(error.message || tr("scan_share_failed", "Share link could not be created."));
    } finally {
        button.disabled = false;
    }
}

async function unshareScan(scanId, event) {
    event.stopPropagation();
    const confirmed = window.confirm(tr("scan_unshare_confirm", "Revoke this share link? The old link will stop working."));
    if (!confirmed) return;
    try {
        await fetch(`/api/scans/${encodeURIComponent(scanId)}/share`, { method: "DELETE" });
        fetchScanHistory();
    } catch (error) {
        console.error(error);
    }
}

async function deleteSavedScan(scanId) {
    if (!scanId) return;
    const confirmed = window.confirm(tr("scan_delete_message", "This saved scan will be permanently removed from the database."));
    if (!confirmed) return;
    try {
        const response = await fetch(`/api/scans/${encodeURIComponent(scanId)}`, { method: "DELETE" });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        renderScanHistory(result.scans || []);
        const detail = byId("scan-history-detail");
        if (detail) {
            detail.className = "history-detail-empty";
            detail.textContent = tr("history_detail_empty", "Click a saved scan to open its snapshot.");
        }
    } catch (error) {
        console.error(error);
        window.alert(error.message || tr("scan_delete_failed", "Saved scan could not be deleted."));
    }
}
function renderScanHistoryDetail(scan) {
    const container = byId("scan-history-detail");
    if (!container) return;

    const payload = scan?.payload || {};
    const status = payload.status || {};
    const dtc = payload.dtc || {};
    const profile = payload.vehicle_profile || {};

    container.className = "detail-grid";
    container.replaceChildren();

    const rows = [
        [tr("history_label", "Label"), scan?.label || "--"],
        [tr("history_saved_at", "Saved at"), scan?.created_at || "--"],
        [tr("history_health_score", "Health score"), displayValue(payload.health?.score, "--")],
        [tr("history_vin", "VIN"), profile.vin || "--"],
        [tr("history_protocol", "Protocol"), status.protocol || tr("unknown", "Unknown")],
        [tr("history_port", "Port"), status.current_port || tr("port_none_selected", "No COM port selected")],
        [tr("codes_stored", "Stored codes"), String((dtc.stored || []).length)],
        [tr("codes_pending", "Pending codes"), String((dtc.pending || []).length)],
        [tr("codes_permanent", "Permanent codes"), String((dtc.permanent || []).length)],
    ];

    rows.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "detail-row";
        const key = document.createElement("span");
        key.textContent = label;
        const val = document.createElement("strong");
        val.textContent = value;
        row.append(key, val);
        container.appendChild(row);
    });

    const allCodes = [...(dtc.stored || []), ...(dtc.pending || []), ...(dtc.permanent || [])];
    if (allCodes.length) {
        const codeWrap = document.createElement("div");
        codeWrap.className = "codes";
        codeWrap.style.gridColumn = "1 / -1";
        allCodes.forEach((item) => {
            const code = document.createElement("div");
            code.className = `code severity-${item.severity || "unknown"}`;
            const strong = document.createElement("strong");
            strong.textContent = item.code || "--";
            const desc = document.createElement("p");
            desc.textContent = item.description || tr("dtc_no_description", "Description unavailable");
            code.append(strong, desc);
            codeWrap.appendChild(code);
        });
        container.appendChild(codeWrap);
    }
}

async function saveScanToDatabase() {
    const resultElement = document.getElementById("save-scan-result") || document.getElementById("report-action-result");
    await saveScanSnapshotToResult(resultElement, tr("save_scan_label", "Manual dashboard snapshot"));
}

async function saveReportSnapshot() {
    const resultElement = document.getElementById("report-action-result");
    await saveScanSnapshotToResult(resultElement, tr("report_snapshot_label", "Manual report snapshot"));
}

async function saveScanSnapshotToResult(resultElement, label) {
    if (!resultElement) return;
    resultElement.innerText = tr("save_scan_saving", "Saving current scan to database...");

    try {
        const response = await fetch("/api/scans/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);

        resultElement.innerText = tr("scan_saved_at", "{label} saved at {time}.", { label: result.scan.label, time: result.scan.created_at });
        renderScanHistory(result.scans || []);
    } catch (error) {
        console.error(error);
        resultElement.innerText = error.message || tr("save_scan_failed", "Could not save the scan.");
    }
}

function garageFilterParams() {
    const params = new URLSearchParams();
    const query = (byId("garage-filter-search")?.value || "").trim();
    if (query) params.set("q", query);
    const vehicleId = byId("garage-vehicle-filter")?.value || "";
    if (vehicleId) params.set("vehicle_id", vehicleId);
    return params;
}

let allVehiclesCache = [];
const vehiclePickerInstances = {};

function updateVehiclePickerCache(vehicles) {
    allVehiclesCache = vehicles || [];
    Object.values(vehiclePickerInstances).forEach((picker) => picker && picker.refresh());
}

function vehicleSearchText(vehicle) {
    return [vehicle.nickname, vehicle.plate, vehicle.vin, vehicle.brand, vehicle.model, vehicle.year]
        .map((part) => String(part || "").toLowerCase())
        .join(" ");
}

function vehicleLabel(vehicle) {
    return vehicle.nickname || [vehicle.brand, vehicle.model].filter(Boolean).join(" ") || tr("vehicle_picker_placeholder", "Select vehicle");
}

function vehicleMeta(vehicle) {
    return [vehicle.brand, vehicle.model, vehicle.year, vehicle.plate].filter(Boolean).join(" · ") || "--";
}

function initVehiclePicker(wrapperId, onChange) {
    const wrapper = byId(wrapperId);
    if (!wrapper) return null;

    const trigger = wrapper.querySelector("[data-vehicle-picker-trigger]");
    const triggerText = wrapper.querySelector("[data-vehicle-picker-trigger-text]");
    const valueInput = wrapper.querySelector("[data-vehicle-picker-value]");
    const panel = wrapper.querySelector("[data-vehicle-picker-panel]");
    const search = wrapper.querySelector("[data-vehicle-picker-search]");
    const optionsBox = wrapper.querySelector("[data-vehicle-picker-options]");
    const clearButton = wrapper.querySelector("[data-vehicle-picker-clear]");
    if (!trigger || !triggerText || !valueInput || !panel || !search || !optionsBox) return null;

    let selectedId = "";
    let closeTimer = null;

    function renderOptions(query) {
        const q = String(query || "").trim().toLowerCase();
        const matches = allVehiclesCache.filter((vehicle) => !q || vehicleSearchText(vehicle).includes(q));
        optionsBox.replaceChildren();
        if (!matches.length) {
            const empty = document.createElement("div");
            empty.className = "vehicle-picker-empty";
            empty.textContent = tr("vehicle_picker_empty", "No matching vehicles.");
            optionsBox.appendChild(empty);
            return;
        }
        matches.forEach((vehicle) => {
            const option = document.createElement("button");
            option.type = "button";
            option.className = "vehicle-picker-option";
            option.dataset.vehicleId = vehicle.id;
            option.innerHTML = `
                <span class="vehicle-picker-option-main">
                    <strong>${escapeHtml(vehicleLabel(vehicle))}</strong>
                    <span>${escapeHtml(vehicleMeta(vehicle))}</span>
                </span>
                <span class="vehicle-picker-option-meta">${escapeHtml(vehicle.vin || "")}</span>
            `;
            option.addEventListener("click", () => selectVehicle(vehicle));
            optionsBox.appendChild(option);
        });
    }

    function selectVehicle(vehicle) {
        selectedId = String(vehicle.id);
        valueInput.value = selectedId;
        triggerText.textContent = vehicleLabel(vehicle);
        triggerText.classList.remove("is-placeholder");
        closePanel();
        if (onChange) onChange(vehicle);
    }

    function clearSelection() {
        const hadSelection = Boolean(selectedId);
        selectedId = "";
        valueInput.value = "";
        triggerText.textContent = tr("vehicle_picker_placeholder", "Select vehicle");
        triggerText.classList.add("is-placeholder");
        closePanel();
        if (onChange && hadSelection) onChange(null);
    }

    function handleOutsideClick(event) {
        if (!wrapper.contains(event.target)) closePanel();
    }

    function openPanel() {
        if (closeTimer) window.clearTimeout(closeTimer);
        panel.hidden = false;
        requestAnimationFrame(() => panel.classList.add("is-open"));
        trigger.setAttribute("aria-expanded", "true");
        search.value = "";
        renderOptions("");
        search.focus();
        document.addEventListener("click", handleOutsideClick, true);
    }

    function closePanel() {
        panel.classList.remove("is-open");
        trigger.setAttribute("aria-expanded", "false");
        document.removeEventListener("click", handleOutsideClick, true);
        if (closeTimer) window.clearTimeout(closeTimer);
        closeTimer = window.setTimeout(() => {
            if (!panel.classList.contains("is-open")) panel.hidden = true;
        }, 160);
    }

    trigger.addEventListener("click", () => {
        if (panel.classList.contains("is-open")) closePanel();
        else openPanel();
    });
    search.addEventListener("input", () => renderOptions(search.value));
    if (clearButton) clearButton.addEventListener("click", clearSelection);

    return {
        refresh() {
            if (selectedId && !allVehiclesCache.some((item) => String(item.id) === selectedId)) {
                clearSelection();
            }
            if (panel.classList.contains("is-open")) renderOptions(search.value);
        },
        reset: clearSelection,
        getValue() {
            return selectedId;
        },
    };
}

function populateVehicleFilters(vehicles) {
    ["garage-vehicle-filter", "fuel-log-vehicle-filter"].forEach((id) => {
        const select = byId(id);
        if (!select) return;
        const currentValue = select.value;
        select.innerHTML = `<option value="">${escapeHtml(tr("vehicle_filter_all", "All vehicles"))}</option>` +
            vehicles.map((vehicle) => `<option value="${vehicle.id}">${escapeHtml(vehicle.nickname)}</option>`).join("");
        select.value = currentValue;
    });
}

function renderVehicleList(vehicles, activeId) {
    const list = byId("vehicle-list");
    if (!list) return;
    list.replaceChildren();

    if (!vehicles.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("vehicle_result", "No vehicle profiles yet.");
        list.appendChild(empty);
        return;
    }

    vehicles.forEach((vehicle, index) => {
        const chip = document.createElement("div");
        chip.className = `garage-vehicle-chip${vehicle.id === activeId ? " is-active" : ""}`;
        chip.style.animationDelay = `${Math.min(index * 24, 260)}ms`;
        const isActive = vehicle.id === activeId;
        const meta = [vehicle.vin ? `VIN ${vehicle.vin}` : "", vehicle.plate || ""].filter(Boolean).join(" - ") || "--";
        chip.innerHTML = `
            <div class="garage-vehicle-chip-info">
                <strong>${escapeHtml(vehicle.nickname)}</strong>
                <span>${isActive ? escapeHtml(tr("vehicle_active", "Active")) : escapeHtml(meta)}</span>
            </div>
            <div class="garage-vehicle-chip-actions">
                ${isActive ? "" : `<button class="icon-action" type="button" data-activate-vehicle="${vehicle.id}" title="${escapeHtml(tr("vehicle_activate", "Set Active"))}" aria-label="${escapeHtml(tr("vehicle_activate", "Set Active"))}">&#10003;</button>`}
                <button class="icon-action danger-icon" type="button" data-delete-vehicle="${vehicle.id}" aria-label="${escapeHtml(tr("vehicle_delete", "Delete"))}">&#128465;</button>
            </div>
        `;
        list.appendChild(chip);
    });

    list.querySelectorAll("[data-activate-vehicle]").forEach((button) => {
        button.addEventListener("click", () => activateVehicleProfile(button.dataset.activateVehicle));
    });
    list.querySelectorAll("[data-delete-vehicle]").forEach((button) => {
        button.addEventListener("click", () => deleteVehicleProfile(button.dataset.deleteVehicle));
    });
}

async function fetchVehicles() {
    const list = byId("vehicle-list");
    if (!list) return;
    try {
        const response = await fetch("/api/vehicles");
        const data = await response.json();
        renderVehicleList(data.vehicles || [], data.active_vehicle_id);
        populateVehicleFilters(data.vehicles || []);
        updateVehiclePickerCache(data.vehicles || []);
    } catch (error) {
        console.error(error);
        list.innerHTML = `<p>${tr("vehicle_load_failed", "Vehicles could not be loaded.")}</p>`;
    }
}

async function saveVehicleProfile(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setText("vehicle-result", tr("vehicle_saving", "Saving vehicle..."));
    try {
        const response = await fetch("/api/vehicles", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                nickname: byId("vehicle-nickname")?.value || "",
                vin: byId("vehicle-vin")?.value || "",
                plate: byId("vehicle-plate")?.value || "",
                brand: byId("vehicle-brand")?.value || "",
                model: byId("vehicle-model")?.value || "",
                year: byId("vehicle-year")?.value || "",
                tank: byId("vehicle-tank")?.value || "",
            }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        setText("vehicle-result", tr("vehicle_saved", "Vehicle added."));
        form.reset();
        renderVehicleList(result.vehicles || [], result.active_vehicle_id);
        populateVehicleFilters(result.vehicles || []);
        updateVehiclePickerCache(result.vehicles || []);
    } catch (error) {
        console.error(error);
        setText("vehicle-result", error.message || tr("vehicle_save_failed", "Vehicle could not be saved."));
    }
}

async function activateVehicleProfile(vehicleId) {
    try {
        await fetch(`/api/vehicles/${vehicleId}/activate`, { method: "POST" });
        fetchVehicles();
    } catch (error) {
        console.error(error);
    }
}

async function deleteVehicleProfile(vehicleId) {
    if (!window.confirm(tr("vehicle_delete_confirm", "Delete this vehicle profile? Notes and fuel log entries already tagged with it are kept, just unlinked."))) return;
    try {
        const response = await fetch(`/api/vehicles/${vehicleId}`, { method: "DELETE" });
        const result = await response.json();
        renderVehicleList(result.vehicles || [], result.active_vehicle_id);
        populateVehicleFilters(result.vehicles || []);
        updateVehiclePickerCache(result.vehicles || []);
    } catch (error) {
        console.error(error);
    }
}

async function fetchMaintenanceReminder() {
    const banner = byId("maintenance-reminder-banner");
    if (!banner) return;
    try {
        const response = await fetch("/api/maintenance-reminder");
        const reminder = await response.json();
        if (!reminder || !reminder.available) {
            banner.hidden = true;
            return;
        }
        const vars = {
            last: reminder.last_service_at,
            due: reminder.due_at,
            current: reminder.current,
            distance: Math.abs(reminder.remaining),
            unit: reminder.distance_unit,
            interval: window.APP_UNITS === "imperial" ? 6214 : 10000
        };
        banner.textContent = reminder.overdue
            ? tr("maintenance_reminder_overdue", "Service overdue: last logged at {last} {unit}, due at {due} {unit} (about {distance} {unit} ago).", vars)
            : tr("maintenance_reminder_upcoming", "Next service due around {due} {unit} - about {distance} {unit} from the last known {current} {unit}.", vars);
        banner.className = `panel-note${reminder.overdue ? " is-overdue" : ""}`;
        banner.hidden = false;
    } catch (error) {
        console.error(error);
        banner.hidden = true;
    }
}

function renderFuelLogTrendChart(trend) {
    const canvas = byId("fuel-log-chart");
    if (!canvas) return;
    const maxHint = window.APP_UNITS === "imperial" ? 45 : 18;
    drawLineChart("fuel-log-chart", (trend || []).map((point) => point.economy), "#0ca678", maxHint, "Economy");
}

function renderFuelLogList(entries) {
    const list = byId("fuel-log-list");
    if (!list) return;
    list.replaceChildren();

    if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("fuel_log_empty", "No fuel log entries yet. Add your first fill-up.");
        list.appendChild(empty);
        return;
    }

    entries.forEach((entry, index) => {
        const row = document.createElement("div");
        row.className = "history-row garage-note-row";
        row.style.animationDelay = `${Math.min(index * 24, 260)}ms`;
        const economyText = entry.economy
            ? `${entry.economy.value} ${entry.economy.unit}`
            : tr("fuel_log_first_fill_note", "First fill - economy calculated from the next one.");
        const chips = [
            entry.created_at,
            `${entry.mileage} ${entry.distance_unit}`,
            `${entry.volume} ${entry.volume_unit}`,
            entry.cost !== null && entry.cost !== undefined ? String(entry.cost) : ""
        ].filter(Boolean).map((value) => `<span>${escapeHtml(String(value))}</span>`).join("");
        const vehicle = entry.vehicle_id ? allVehiclesCache.find((item) => String(item.id) === String(entry.vehicle_id)) : null;
        const tankBar = vehicle && vehicle.tank
            ? `<div class="fuel-log-tank-bar"><div class="fuel-log-tank-bar-fill" style="width:${Math.min(100, Math.round((entry.volume / vehicle.tank) * 100))}%"></div></div>`
            : "";
        row.innerHTML = `
            <div class="garage-note-content">
                <strong>${escapeHtml(economyText)}</strong>
                <div class="garage-note-meta">${chips}</div>
                ${tankBar}
                ${entry.notes ? `<p>${escapeHtml(entry.notes)}</p>` : ""}
            </div>
            <div class="garage-note-actions">
                <button class="icon-action danger-icon fuel-log-delete" type="button" data-fuel-log-id="${entry.id}" aria-label="${escapeHtml(tr("fuel_log_delete", "Delete"))}">&#128465;</button>
            </div>
        `;
        list.appendChild(row);
    });
    list.querySelectorAll("[data-fuel-log-id]").forEach((button) => {
        button.addEventListener("click", deleteFuelLogEntry);
    });
}

function renderFuelLogHistory(data) {
    renderFuelLogList(data.entries || []);
    renderFuelLogTrendChart(data.trend || []);
}

async function fetchFuelLogs() {
    const list = byId("fuel-log-list");
    if (!list) return;
    list.innerHTML = `<p>${tr("fuel_log_loading", "Loading fuel log...")}</p>`;
    try {
        const vehicleId = byId("fuel-log-vehicle-filter")?.value || "";
        const url = vehicleId ? `/api/fuel-logs?vehicle_id=${encodeURIComponent(vehicleId)}` : "/api/fuel-logs";
        const response = await fetch(url);
        const data = await response.json();
        renderFuelLogHistory(data || { entries: [], trend: [] });
        fetchMaintenanceReminder();
    } catch (error) {
        console.error(error);
        list.innerHTML = `<p>${tr("fuel_log_failed", "Could not load fuel log entries.")}</p>`;
    }
}

async function saveFuelLogEntry(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setText("fuel-log-result", tr("fuel_log_saving", "Saving fill-up..."));

    try {
        const response = await fetch("/api/fuel-logs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                mileage: byId("fuel-log-mileage")?.value || "",
                liters: byId("fuel-log-liters")?.value || "",
                cost: byId("fuel-log-cost")?.value || null,
                notes: byId("fuel-log-notes")?.value || "",
                vehicle_id: vehiclePickerInstances["fuel-log-vehicle-picker"]?.getValue() || ""
            })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        setText("fuel-log-result", tr("fuel_log_saved", "Fill-up saved."));
        form.reset();
        vehiclePickerInstances["fuel-log-vehicle-picker"]?.reset();
        renderFuelLogHistory(result.fuel_log || { entries: [], trend: [] });
    } catch (error) {
        console.error(error);
        setText("fuel-log-result", error.message || tr("fuel_log_save_failed", "Fuel log entry could not be saved."));
    }
}

async function deleteFuelLogEntry(event) {
    const logId = event.currentTarget.dataset.fuelLogId;
    if (!logId) return;
    if (!window.confirm(tr("fuel_log_delete_confirm", "Delete this fuel log entry?"))) return;
    try {
        const response = await fetch(`/api/fuel-logs/${logId}`, { method: "DELETE" });
        const result = await response.json();
        renderFuelLogHistory(result.fuel_log || { entries: [], trend: [] });
    } catch (error) {
        console.error(error);
    }
}

async function fetchGarageNotes() {
    const list = byId("garage-notes-list");
    if (!list) return;
    list.innerHTML = `<p>${tr("garage_loading", "Loading garage notes...")}</p>`;

    try {
        const params = garageFilterParams();
        const response = await fetch(`/api/garage-notes${params.toString() ? `?${params.toString()}` : ""}`);
        const notes = await response.json();
        renderGarageNotes(notes || []);
        fetchMaintenanceReminder();
    } catch (error) {
        console.error(error);
        list.innerHTML = `<p>${tr("garage_failed", "Could not load garage notes.")}</p>`;
    }
}

function renderGarageNotes(notes) {
    const list = byId("garage-notes-list");
    if (!list) return;
    list.replaceChildren();

    if (!notes.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("garage_filter_empty", "No garage notes match this VIN or plate.");
        list.appendChild(empty);
        return;
    }

    notes.forEach((note, index) => {
        const row = document.createElement("div");
        row.className = "history-row garage-note-row";
        row.style.animationDelay = `${Math.min(index * 24, 260)}ms`;
        const noteId = Number(note.id || 0);
        const editPayload = encodeURIComponent(JSON.stringify({
            id: noteId,
            vin: note.vin || "",
            plate: note.plate || "",
            title: note.title || "",
            mileage: note.mileage || "",
            note: note.note || ""
        }));
        const identityChips = [
            note.created_at,
            note.vin ? `VIN ${note.vin}` : "",
            note.plate ? `${tr("history_type_plate", "Plate")} ${note.plate}` : "",
            note.mileage || ""
        ].filter(Boolean).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
        row.innerHTML = `
            <div class="garage-note-content">
                <strong>${escapeHtml(note.title || tr("garage_note", "Garage note"))}</strong>
                <div class="garage-note-meta">${identityChips || "<span>--</span>"}</div>
                <p>${escapeHtml(note.note || "")}</p>
                ${garageNoteAttachmentMarkup(note)}
            </div>
            <div class="garage-note-actions">
                <button class="icon-action garage-note-edit" type="button" data-garage-note="${editPayload}" aria-label="${escapeHtml(tr("garage_edit", "Edit garage note"))}">&#9998;</button>
                <button class="icon-action danger-icon garage-note-delete" type="button" data-garage-note-id="${noteId}" aria-label="${escapeHtml(tr("garage_delete_title", "Delete garage note?"))}">&#128465;</button>
            </div>
        `;
        list.appendChild(row);
    });
    list.querySelectorAll("[data-garage-note]").forEach((button) => {
        button.addEventListener("click", editGarageNote);
    });
    list.querySelectorAll("[data-garage-note-id]").forEach((button) => {
        button.addEventListener("click", deleteGarageNote);
    });
}

// Photos are files on the server now, not base64 inside the note payload.
// The name is generated server-side and validated there, and it is still
// escaped here: nothing from a stored record is ever written into markup raw.
function garageNoteAttachmentMarkup(note) {
    const name = note.attachment_name;
    if (!name || !/^[0-9a-f]{32}\.(?:jpg|png|webp|gif)$/.test(String(name))) {
        return "";
    }
    const src = `/uploads/${encodeURIComponent(String(name))}`;
    return `
        <a class="garage-note-photo" href="${escapeHtml(src)}" target="_blank" rel="noopener">
            <img src="${escapeHtml(src)}" alt="${escapeHtml(tr("garage_photo_alt", "Photo attached to this note"))}" loading="lazy">
            <span>${escapeHtml(tr("garage_open_photo", "Open photo"))}</span>
        </a>
    `;
}

function clearGarageFilter() {
    const search = byId("garage-filter-search");
    if (search) search.value = "";
    fetchGarageNotes();
}

function scheduleGarageSearch() {
    if (garageSearchTimer) {
        window.clearTimeout(garageSearchTimer);
    }
    garageSearchTimer = window.setTimeout(() => {
        garageSearchTimer = null;
        fetchGarageNotes();
    }, 180);
}

function exportGarageNotes() {
    const params = garageFilterParams();
    window.location.href = `/api/garage-notes/export${params.toString() ? `?${params.toString()}` : ""}`;
}

function normalizeGarageVinInput(value) {
    return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function normalizeGaragePlateInput(value) {
    return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function setGarageFieldError(input, hasError) {
    if (input) {
        input.classList.toggle("input-error", Boolean(hasError));
    }
}

function setGarageEditMode(note = null) {
    editingGarageNoteId = note?.id ? Number(note.id) : null;
    const submit = byId("garage-note-submit");
    const cancel = byId("garage-note-cancel-edit");
    if (submit) {
        submit.textContent = editingGarageNoteId
            ? tr("garage_update", "Update Note")
            : tr("garage_save", "Save Note");
    }
    if (cancel) {
        cancel.hidden = !editingGarageNoteId;
    }
    if (!editingGarageNoteId) {
        setGarageFieldError(byId("garage-note-vin"), false);
        setGarageFieldError(byId("garage-note-plate"), false);
        setGarageFieldError(byId("garage-note-text"), false);
    }
}

function editGarageNote(event) {
    event.preventDefault();
    event.stopPropagation();
    const raw = event.currentTarget?.dataset?.garageNote;
    if (!raw) return;
    let note = null;
    try {
        note = JSON.parse(decodeURIComponent(raw));
    } catch (error) {
        console.error(error);
        return;
    }
    byId("garage-note-vin").value = note.vin || "";
    byId("garage-note-plate").value = note.plate || "";
    byId("garage-note-title").value = note.title || "";
    byId("garage-note-mileage").value = note.mileage || "";
    byId("garage-note-text").value = note.note || "";
    setGarageEditMode(note);
    setText("garage-note-result", tr("garage_editing", "Editing garage note. Update or cancel when you are done."));
    byId("garage-note-vin")?.focus();
}

function cancelGarageEdit() {
    const form = byId("garage-note-form");
    if (form) form.reset();
    setGarageEditMode(null);
    updateVehicleProfileView(lastVehicleProfile);
    setText("garage-note-result", tr("garage_result", "No garage note saved yet."));
}

function validateGarageNoteForm() {
    const vinInput = byId("garage-note-vin");
    const plateInput = byId("garage-note-plate");
    const noteInput = byId("garage-note-text");
    const vin = normalizeGarageVinInput(vinInput?.value);
    const plate = normalizeGaragePlateInput(plateInput?.value);
    const noteText = noteInput?.value || "";

    setGarageFieldError(vinInput, false);
    setGarageFieldError(plateInput, false);
    setGarageFieldError(noteInput, false);

    if (!plate) {
        setGarageFieldError(plateInput, true);
        return { ok: false, message: tr("garage_plate_missing", "Enter a license plate before saving this garage note.") };
    }
    if (plate.length < 4) {
        setGarageFieldError(plateInput, true);
        return { ok: false, message: tr("garage_plate_too_short", "License plate looks too short. Check the plate and try again.") };
    }
    if (vin && vin.length !== 17) {
        setGarageFieldError(vinInput, true);
        return {
            ok: false,
            message: tr("garage_vin_invalid_length", "VIN must be exactly 17 characters. You entered {count}.", { count: vin.length })
        };
    }
    if (vin && /[IOQ]/.test(vin)) {
        setGarageFieldError(vinInput, true);
        return { ok: false, message: tr("garage_vin_invalid_chars", "VIN cannot contain I, O or Q. Check the VIN and try again.") };
    }
    if (!noteText.trim()) {
        setGarageFieldError(noteInput, true);
        return { ok: false, message: tr("garage_note_required", "Write a note before saving.") };
    }

    return { ok: true, vin, plate, noteText };
}

async function deleteGarageNote(event) {
    event.preventDefault();
    event.stopPropagation();
    const noteId = Number(event.currentTarget?.dataset?.garageNoteId || 0);
    if (!noteId) return;

    const confirmed = await openConfirmDialog(
        tr("garage_delete_title", "Delete garage note?"),
        tr("garage_delete_message", "This garage note will be permanently removed from the local database.")
    );
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/garage-notes/${noteId}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: "YES" })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        setText("garage-note-result", tr("garage_deleted", "Garage note deleted."));
        fetchGarageNotes();
    } catch (error) {
        console.error(error);
        setText("garage-note-result", error.message || tr("garage_delete_failed", "Garage note could not be deleted."));
    }
}

const PHOTO_MAX_BYTES = 6 * 1024 * 1024;
const PHOTO_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];

// Checked here for a quick, clear message; the server validates the bytes
// again and does not trust any of this.
function validateGaragePhoto(file) {
    if (!file) return { ok: true };
    if (!PHOTO_ALLOWED_TYPES.includes(file.type)) {
        return { ok: false, message: tr("garage_photo_type", "Only JPEG, PNG, WebP or GIF photos can be attached.") };
    }
    if (file.size > PHOTO_MAX_BYTES) {
        return { ok: false, message: tr("garage_photo_size", "That photo is larger than the 6 MB limit.") };
    }
    return { ok: true };
}

function selectedGaragePhoto() {
    return byId("garage-note-photo")?.files?.[0] || null;
}

async function saveGarageNote(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const resultElement = byId("garage-note-result");
    const validation = validateGarageNoteForm();

    if (!validation.ok) {
        setText("garage-note-result", validation.message);
        return;
    }

    setText("garage-note-result", editingGarageNoteId
        ? tr("garage_updating", "Updating garage note...")
        : tr("garage_saving", "Saving garage note..."));

    try {
        const photo = editingGarageNoteId ? null : selectedGaragePhoto();
        const photoCheck = validateGaragePhoto(photo);
        if (!photoCheck.ok) {
            setText("garage-note-result", photoCheck.message);
            return;
        }

        const fields = {
            vin: validation.vin,
            plate: validation.plate,
            title: byId("garage-note-title")?.value || "",
            mileage: byId("garage-note-mileage")?.value || "",
            note: validation.noteText,
            vehicle_id: vehiclePickerInstances["garage-note-vehicle-picker"]?.getValue() || ""
        };

        const url = editingGarageNoteId ? `/api/garage-notes/${editingGarageNoteId}` : "/api/garage-notes";
        let requestInit;
        if (photo) {
            // multipart: the photo is uploaded as a file, so nothing has to be
            // base64-encoded into the note itself.
            const form = new FormData();
            Object.entries(fields).forEach(([key, value]) => form.append(key, value));
            form.append("photo", photo, photo.name);
            requestInit = { method: "POST", body: form };
        } else {
            requestInit = {
                method: editingGarageNoteId ? "PUT" : "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(fields)
            };
        }

        const response = await fetch(url, requestInit);
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        if (resultElement) {
            resultElement.innerText = editingGarageNoteId
                ? tr("garage_updated", "Garage note updated.")
                : tr("garage_saved", "Garage note saved at {time}.", { time: result.note?.created_at || "--" });
        }
        form.reset();
        vehiclePickerInstances["garage-note-vehicle-picker"]?.reset();
        setGarageEditMode(null);
        renderGarageNotes(result.notes || []);
        fetchGarageNotes();
    } catch (error) {
        console.error(error);
        setText("garage-note-result", error.message || (editingGarageNoteId
            ? tr("garage_update_failed", "Garage note could not be updated.")
            : tr("garage_save_failed", "Garage note could not be saved.")));
    }
}

function setLanguageCookie(languageCode) {
    document.cookie = `obd_lang=${encodeURIComponent(languageCode)}; path=/; max-age=31536000; SameSite=Lax`;
}

function initLanguageSwitcher() {
    document.querySelectorAll("[data-language-code]").forEach((button) => {
        button.addEventListener("click", () => {
            const nextLanguage = String(button.dataset.languageCode || "en").toLowerCase();
            if (nextLanguage === String(window.APP_LANG || "en").toLowerCase()) {
                return;
            }
            setLanguageCookie(nextLanguage);
            window.location.reload();
        });
    });
}

function setUnitsCookie(unitsCode) {
    document.cookie = `obd_units=${encodeURIComponent(unitsCode)}; path=/; max-age=31536000; SameSite=Lax`;
}

function initUnitsSwitcher() {
    document.querySelectorAll("[data-unit-code]").forEach((button) => {
        button.addEventListener("click", () => {
            const nextUnits = String(button.dataset.unitCode || "metric").toLowerCase();
            if (nextUnits === String(window.APP_UNITS || "metric").toLowerCase()) {
                return;
            }
            setUnitsCookie(nextUnits);
            window.location.reload();
        });
    });
}

function setThemeCookie(themeCode) {
    document.cookie = `obd_theme=${encodeURIComponent(themeCode)}; path=/; max-age=31536000; SameSite=Lax`;
}

function initThemeSwitcher() {
    document.querySelectorAll("[data-theme-code]").forEach((button) => {
        button.addEventListener("click", () => {
            const nextTheme = String(button.dataset.themeCode || "auto").toLowerCase();
            if (nextTheme === String(window.APP_THEME || "auto").toLowerCase()) {
                return;
            }
            setThemeCookie(nextTheme);
            window.location.reload();
        });
    });
}

function resetUiCache() {
    try {
        window.localStorage.removeItem(POLL_PROFILE_STORAGE_KEY);
        window.localStorage.removeItem(VEHICLE_LOOKUP_HISTORY_KEY);
    } catch {
        // Ignore strict browser storage failures.
    }
    document.cookie = "obd_lang=; path=/; max-age=0; SameSite=Lax";
    setText("save-scan-result", tr("ui_cache_reset", "UI cache cleared. The page will reload."));
    window.setTimeout(() => window.location.reload(), 350);
}

function initPollProfileButtons() {
    pollProfile = loadLocalPollProfile();
    updatePollProfileUi({ id: pollProfile });
    document.querySelectorAll("[data-poll-profile]").forEach((button) => {
        button.disabled = false;
        button.addEventListener("click", (event) => {
            event.preventDefault();
            setPollProfile(button.dataset.pollProfile || "balanced");
        });
    });
}

// Storage is a single local .db file; the only first-run choices left are
// language and units.
function hydrateDashboardSetup(setup = {}) {
    const language = byId("setup-language");
    if (language && setup.language) language.value = setup.language;
    const units = byId("setup-units");
    if (units && setup.units) units.value = setup.units;
}

async function initDashboardSetup() {
    const modal = byId("setup-modal");
    if (!modal || !document.body?.dataset?.dashboardPage) return;
    try {
        const response = await fetch("/api/setup");
        const setup = await response.json();
        hydrateDashboardSetup(setup);
        modal.hidden = Boolean(setup.completed);
    } catch {
        modal.hidden = false;
    }
}

async function saveDashboardSetup() {
    const language = byId("setup-language")?.value || "en";
    const units = byId("setup-units")?.value || "metric";
    const resultElement = byId("setup-result");
    const button = byId("setup-save-button");
    const payload = { language, units };
    if (button) button.disabled = true;
    if (resultElement) resultElement.textContent = tr("setup_saving", "Saving setup...");
    try {
        const response = await fetch("/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        setLanguageCookie(language);
        setUnitsCookie(units);
        if (resultElement) resultElement.textContent = tr("setup_saved", "Setup saved.");
        const modal = byId("setup-modal");
        if (modal) modal.hidden = true;
        window.location.reload();
    } catch (error) {
        console.error(error);
        if (resultElement) resultElement.textContent = error.message || tr("setup_failed", "Setup could not be saved.");
    } finally {
        if (button) button.disabled = false;
    }
}



// ---------------------------------------------------------------------------
// Extended PIDs: odometer, DPF, hybrid/EV battery pack
// ---------------------------------------------------------------------------
// These are read from the vehicle with standard service modes, but support is
// far from universal, so the panel states plainly which values the car
// actually answered instead of showing a confident-looking zero.
function renderExtendedData(data) {
    const grid = byId("extended-grid");
    if (!grid) return;

    const cards = [];
    const odometer = data?.odometer;
    if (odometer && odometer.km !== null && odometer.km !== undefined) {
        const km = Number(odometer.km);
        const imperial = (window.APP_UNITS || "metric") === "imperial";
        const value = imperial ? km * 0.621371 : km;
        const unit = imperial ? "mi" : "km";
        cards.push({
            label: tr("odometer_reading", "Odometer read from the ECU"),
            value: `${value.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${unit}`,
            note: odometer.source === "demo"
                ? tr("demo_value", "Demo value")
                : tr("odometer_pid", "Service mode 01, PID A6"),
            classes: "is-odometer"
        });
    } else {
        cards.push({
            label: tr("odometer_reading", "Odometer read from the ECU"),
            value: tr("odometer_unsupported", "This vehicle does not answer the odometer PID."),
            note: "",
            classes: "is-unsupported"
        });
    }

    const dpf = data?.dpf;
    if (dpf?.available) {
        if (dpf.temperature_c !== null && dpf.temperature_c !== undefined) {
            cards.push({
                label: tr("dpf_temperature", "DPF temperature"),
                value: `${dpf.temperature_c} C`,
                note: ""
            });
        }
        if (dpf.pressure_raw) {
            cards.push({
                label: tr("dpf_pressure", "DPF pressure (raw)"),
                value: String(dpf.pressure_raw),
                note: tr("dpf_raw_note", "Layout is manufacturer-specific")
            });
        }
    }

    const pack = data?.battery_pack;
    if (pack?.available && pack.remaining_pct !== null && pack.remaining_pct !== undefined) {
        cards.push({
            label: tr("battery_pack_remaining", "Battery pack remaining"),
            value: `${pack.remaining_pct}%`,
            note: tr("battery_pack_pid", "Service mode 01, PID 5B")
        });
    }

    grid.replaceChildren();
    cards.forEach((card, index) => {
        const element = document.createElement("div");
        element.className = `extended-card ${card.classes || ""}`.trim();
        element.style.animationDelay = `${Math.min(index * 40, 200)}ms`;
        const label = document.createElement("span");
        label.textContent = card.label;
        const value = document.createElement("strong");
        value.textContent = card.value;
        element.append(label, value);
        if (card.note) {
            const note = document.createElement("small");
            note.textContent = card.note;
            element.appendChild(note);
        }
        grid.appendChild(element);
    });

    const checked = byId("extended-result");
    if (checked) {
        checked.textContent = data?.checked_at
            ? tr("extended_checked", "Last read: {time}", { time: data.checked_at })
            : "";
    }
}

async function fetchExtendedData() {
    try {
        const response = await fetch("/api/extended");
        if (!response.ok) throw new Error(`Server returned status ${response.status}`);
        renderExtendedData(await response.json());
    } catch (error) {
        console.error(error);
    }
}

async function refreshExtendedData() {
    const button = byId("extended-refresh");
    const result = byId("extended-result");
    if (button) button.disabled = true;
    if (result) result.textContent = tr("extended_reading", "Reading extended PIDs...");
    try {
        const response = await fetch("/api/extended/refresh", { method: "POST" });
        const payload = await response.json();
        if (!response.ok || !payload.success) {
            throw new Error(payload.message || `Server returned status ${response.status}`);
        }
        renderExtendedData(payload.extended);
    } catch (error) {
        console.error(error);
        if (result) result.textContent = error.message || tr("extended_failed", "Extended PIDs could not be read.");
    } finally {
        if (button) button.disabled = false;
    }
}


// ---------------------------------------------------------------------------
// Navigation rail
// ---------------------------------------------------------------------------
// The rail collapses to an icon strip, which is what you want on a tablet
// propped on a wing. The choice is remembered per browser; a failure to read
// or write that is not worth breaking the page over, so it is swallowed.
const RAIL_COLLAPSED_KEY = "obd_rail_collapsed";

function applyRailCollapsed(collapsed) {
    const rail = byId("scanner-rail");
    const toggle = byId("rail-toggle");
    if (!rail) return;

    rail.classList.toggle("is-collapsed", collapsed);
    if (toggle) toggle.setAttribute("aria-expanded", String(!collapsed));

    // The dial numbers are placed from the measured dial size, so they have
    // to be re-placed once the rail has finished changing width.
    window.setTimeout(positionGaugeTicks, 60);
    window.setTimeout(positionGaugeTicks, 320);
}

function initNavigationRail() {
    const rail = byId("scanner-rail");
    const toggle = byId("rail-toggle");
    if (!rail) return;

    let collapsed = false;
    try {
        collapsed = window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1";
    } catch (error) {
        collapsed = false;
    }
    applyRailCollapsed(collapsed);

    if (!toggle) return;
    toggle.addEventListener("click", () => {
        collapsed = !rail.classList.contains("is-collapsed");
        applyRailCollapsed(collapsed);
        try {
            window.localStorage.setItem(RAIL_COLLAPSED_KEY, collapsed ? "1" : "0");
        } catch (error) {
            /* a browser with storage disabled simply does not remember it */
        }
    });
}

function on(id, eventName, handler) {
    const element = byId(id);
    if (element) {
        element.addEventListener(eventName, handler);
    }
}

initPollProfileButtons();
on("freeze-button", "click", toggleFreeze);
on("extended-refresh", "click", refreshExtendedData);
on("clear-button", "click", clearDTC);
on("clear-button-codes", "click", clearDTC);
on("port-form", "submit", savePort);
on("port-input", "change", savePort);
on("bluetooth-scan-button", "click", scanBluetoothDevices);
on("bluetooth-bind-button", "click", bindBluetoothDevice);
initBluetoothPanel();
on("reconnect-button", "click", reconnectObd);
on("refresh-vin-button", "click", () => fetchVehicleProfile(true));
on("manual-vin-form", "submit", saveManualVin);
on("clear-vin-button", "click", clearManualVinInput);
on("plate-form", "submit", savePlateLookup);
on("scan-codes-button", "click", scanCodes);
on("cancel-scan-button", "click", cancelVehicleScan);
on("refresh-supported-button", "click", fetchSupportedSensors);
on("save-scan-button", "click", saveScanToDatabase);
on("record-graphs-button", "click", toggleGraphRecording);
on("update-close", "click", () => { const box = byId("update-notification"); if (box) box.hidden = true; });
on("setup-save-button", "click", saveDashboardSetup);
on("export-html-button", "click", () => exportScanReportFormat("html"));
on("export-csv-button", "click", () => exportScanReportFormat("csv"));
on("export-cancel-button", "click", closeExportModal);
on("reset-ui-cache-button", "click", resetUiCache);
on("garage-clear-filter-button", "click", clearGarageFilter);
on("trip-reset-button", "click", resetTripComputer);
on("fault-alerts-button", "click", requestFaultAlerts);
updateFaultAlertsUi();
on("accel-reset-button", "click", resetAccelTimer);
initAccelTimerTitle();
resetAccelTimer();
on("garage-export-button", "click", exportGarageNotes);
on("garage-note-cancel-edit", "click", cancelGarageEdit);
const reportExportButton = byId("report-export-button");
if (reportExportButton) {
    reportExportButton.addEventListener("click", exportScanReport);
}
const reportSaveSnapshotButton = byId("report-save-snapshot-button");
if (reportSaveSnapshotButton) {
    reportSaveSnapshotButton.addEventListener("click", saveReportSnapshot);
}
const simpleModeButton = byId("simple-mode-button");
if (simpleModeButton) {
    simpleModeButton.addEventListener("click", toggleSimpleMode);
}
const demoModeButton = byId("demo-mode-button");
if (demoModeButton) {
    demoModeButton.addEventListener("click", toggleDemoMode);
}
const limitedModeButton = byId("limited-mode-button");
if (limitedModeButton) {
    limitedModeButton.addEventListener("click", toggleLimitedMode);
}
document.querySelectorAll("[data-demo-preset]").forEach((button) => {
    button.addEventListener("click", () => {
        setDemoPreset(button.dataset.demoPreset || "idle");
    });
});
const testConnectionButton = byId("test-connection-button");
if (testConnectionButton) {
    testConnectionButton.addEventListener("click", testConnection);
}
const garageNoteForm = byId("garage-note-form");
if (garageNoteForm) {
    garageNoteForm.addEventListener("submit", saveGarageNote);
}
const garageSearchInput = byId("garage-filter-search");
if (garageSearchInput) {
    garageSearchInput.addEventListener("input", scheduleGarageSearch);
    garageSearchInput.addEventListener("search", fetchGarageNotes);
    garageSearchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
        }
    });
}

initDashboardSetup();
initLanguageSwitcher();
initUnitsSwitcher();
initThemeSwitcher();
initPortDropdown();
initNavigation();
initNavigationRail();
initDashboardLauncher();
initPageFromHash();
updateFreezeUi();
requestAnimationFrame(renderGauges);
armStartupFallback();
loadConfig();
schedulePortPoll();
scheduleGaugePoll();
setStreamIndicator("connecting");
connectLiveStream();
window.addEventListener("beforeunload", closeLiveStream);
fetchSupportedSensors();
fetchScanHistory();
fetchExtendedData();
on("vehicle-form", "submit", saveVehicleProfile);
on("garage-vehicle-filter", "change", fetchGarageNotes);
on("fuel-log-vehicle-filter", "change", fetchFuelLogs);
function applyGarageNoteVehicleSelection(vehicle) {
    const plateInput = byId("garage-note-plate");
    const vinInput = byId("garage-note-vin");
    const note = byId("garage-note-vehicle-note");
    if (!plateInput || !vinInput) return;

    if (vehicle) {
        plateInput.value = vehicle.plate || "";
        vinInput.value = vehicle.vin || "";
        plateInput.readOnly = true;
        vinInput.readOnly = true;
        plateInput.classList.add("is-picker-linked");
        vinInput.classList.add("is-picker-linked");
        if (note) note.hidden = false;
    } else {
        plateInput.readOnly = false;
        vinInput.readOnly = false;
        plateInput.classList.remove("is-picker-linked");
        vinInput.classList.remove("is-picker-linked");
        plateInput.value = "";
        vinInput.value = "";
        if (note) note.hidden = true;
    }
}

function applyFuelLogVehicleSelection(vehicle) {
    const note = byId("fuel-log-tank-note");
    if (!note) return;

    if (vehicle && vehicle.tank) {
        note.hidden = false;
        note.textContent = tr("vehicle_tank_note", "Tank size: {size} {unit}", { size: vehicle.tank, unit: vehicle.tank_unit || "L" });
    } else {
        note.hidden = true;
        note.textContent = "";
    }
}

function appendUnitToLabelNode(id, unit) {
    // Reuses the server-rendered (already localized) label text as the base
    // instead of a hardcoded English tr() fallback, so the unit suffix gets
    // appended without breaking non-English languages.
    const el = byId(id);
    const textNode = el?.firstChild;
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) return;
    if (el.dataset.baseLabel === undefined) el.dataset.baseLabel = textNode.textContent.trim();
    textNode.textContent = `${el.dataset.baseLabel} (${unit}) `;
}

function applyDynamicUnitLabels() {
    const imperial = window.APP_UNITS === "imperial";
    const distanceUnit = imperial ? "mi" : "km";
    const volumeUnit = imperial ? "gal" : "L";
    const distanceBadge = imperial ? "MI" : "KM";
    const volumeBadge = imperial ? "GAL" : "L";

    appendUnitToLabelNode("garage-note-mileage-label", distanceUnit);
    appendUnitToLabelNode("fuel-log-mileage-label", distanceUnit);
    appendUnitToLabelNode("fuel-log-liters-label", volumeUnit);
    appendUnitToLabelNode("trip-log-start-label", distanceUnit);
    appendUnitToLabelNode("trip-log-end-label", distanceUnit);

    setText("fuel-log-mileage-badge", distanceBadge);
    setText("fuel-log-liters-badge", volumeBadge);
    setText("trip-log-start-badge", distanceBadge);
    setText("trip-log-end-badge", distanceBadge);
    setText("vehicle-tank-unit", volumeUnit);
}

function initYearStepper() {
    const stepper = byId("vehicle-year-stepper");
    const input = byId("vehicle-year");
    if (!stepper || !input) return;

    stepper.querySelectorAll("[data-year-step]").forEach((button) => {
        button.addEventListener("click", () => {
            const step = Number(button.dataset.yearStep) || 0;
            const current = Number(input.value) || new Date().getFullYear();
            const next = Math.min(2100, Math.max(1900, current + step));
            input.value = String(next);
        });
    });
    input.addEventListener("blur", () => {
        if (!input.value) return;
        const clamped = Math.min(2100, Math.max(1900, Number(input.value) || 0));
        input.value = clamped ? String(clamped) : "";
    });
}

function renderTripLogList(entries) {
    const list = byId("trip-log-list");
    if (!list) return;
    list.replaceChildren();

    if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = tr("trip_log_empty", "No trips logged yet. Add your first trip.");
        list.appendChild(empty);
        return;
    }

    entries.forEach((entry, index) => {
        const row = document.createElement("div");
        row.className = "history-row garage-note-row";
        row.style.animationDelay = `${Math.min(index * 24, 260)}ms`;
        const distanceText = `${entry.distance} ${entry.distance_unit}`;
        const chips = [
            entry.created_at,
            `${entry.start_mileage} ${entry.distance_unit} -> ${entry.end_mileage} ${entry.distance_unit}`,
        ].filter(Boolean).map((value) => `<span>${escapeHtml(String(value))}</span>`).join("");
        row.innerHTML = `
            <div class="garage-note-content">
                <strong>${escapeHtml(entry.purpose || distanceText)}</strong>
                <div class="garage-note-meta">${chips}</div>
                ${entry.purpose && entry.notes ? `<p>${escapeHtml(entry.notes)}</p>` : entry.notes ? `<p>${escapeHtml(entry.notes)}</p>` : ""}
            </div>
            <div class="garage-note-actions">
                <button class="icon-action danger-icon trip-log-delete" type="button" data-trip-log-id="${entry.id}" aria-label="${escapeHtml(tr("trip_log_delete", "Delete"))}">&#128465;</button>
            </div>
        `;
        list.appendChild(row);
    });
    list.querySelectorAll("[data-trip-log-id]").forEach((button) => {
        button.addEventListener("click", deleteTripLogEntry);
    });
}

async function fetchTripLog() {
    const list = byId("trip-log-list");
    if (!list) return;
    list.innerHTML = `<p>${tr("trip_log_loading", "Loading trips...")}</p>`;
    try {
        const vehicleId = byId("trip-log-vehicle-filter")?.value || "";
        const url = vehicleId ? `/api/trip-log?vehicle_id=${encodeURIComponent(vehicleId)}` : "/api/trip-log";
        const response = await fetch(url);
        const data = await response.json();
        renderTripLogList(data.entries || []);
    } catch (error) {
        console.error(error);
        list.innerHTML = `<p>${tr("trip_log_load_failed", "Could not load trips.")}</p>`;
    }
}

async function saveTripLogEntry(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setText("trip-log-result", tr("trip_log_saving", "Saving trip..."));

    try {
        const response = await fetch("/api/trip-log", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                start_mileage: byId("trip-log-start")?.value || "",
                end_mileage: byId("trip-log-end")?.value || "",
                purpose: byId("trip-log-purpose")?.value || "",
                notes: byId("trip-log-notes")?.value || "",
                vehicle_id: vehiclePickerInstances["trip-log-vehicle-picker"]?.getValue() || ""
            })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || `Server returned status ${response.status}`);
        setText("trip-log-result", tr("trip_log_saved", "Trip saved."));
        form.reset();
        vehiclePickerInstances["trip-log-vehicle-picker"]?.reset();
        renderTripLogList(result.trip_log || []);
    } catch (error) {
        console.error(error);
        setText("trip-log-result", error.message || tr("trip_log_save_failed", "Trip could not be saved."));
    }
}

async function deleteTripLogEntry(event) {
    const tripId = event.currentTarget.dataset.tripLogId;
    if (!tripId) return;
    if (!window.confirm(tr("trip_log_delete_confirm", "Delete this trip?"))) return;
    try {
        const response = await fetch(`/api/trip-log/${tripId}`, { method: "DELETE" });
        const result = await response.json();
        renderTripLogList(result.trip_log || []);
    } catch (error) {
        console.error(error);
    }
}

vehiclePickerInstances["garage-note-vehicle-picker"] = initVehiclePicker("garage-note-vehicle-picker", applyGarageNoteVehicleSelection);
vehiclePickerInstances["fuel-log-vehicle-picker"] = initVehiclePicker("fuel-log-vehicle-picker", applyFuelLogVehicleSelection);
vehiclePickerInstances["trip-log-vehicle-picker"] = initVehiclePicker("trip-log-vehicle-picker");
initYearStepper();
applyDynamicUnitLabels();
on("trip-log-form", "submit", saveTripLogEntry);
on("trip-log-vehicle-filter", "change", fetchTripLog);
fetchTripLog();
fetchVehicles().then(() => {
    fetchGarageNotes();
    fetchFuelLogs();
});
on("fuel-log-form", "submit", saveFuelLogEntry);
renderVehicleLookupHistory();
fetchData();
checkForUpdates();
checkChangelog();
window.addEventListener("load", () => {
    positionGaugeTicks();
    window.requestAnimationFrame(positionGaugeTicks);
});
window.addEventListener("resize", positionGaugeTicks);














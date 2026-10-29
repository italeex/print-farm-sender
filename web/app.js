"use strict";

const POLL_IDLE_MS = 2000;
const POLL_JOB_MS = 1000;
const FETCH_TIMEOUT_MS = 5000;
const API_TIMEOUT_MS = 8000;
const FS_TIMEOUT_MS = 15000;
const BROWSE_TIMEOUT_MS = 300000;
const TOAST_MS = 5000;
const HEARTBEAT_MS = 10000;
const COMPACT_KEY = "farm.compact";
const GROUPED_KEY = "farm.grouped";
const CAMERA_PORT = 8000;
const DEFAULT_HELP_URL = "https://wiki.creality.com/en/k2-flagship-series/k2-plus/error-code-summary";
const EXPECTED_SERVER_VERSION = "2.6.2";
const STATE_LABELS = {
  printing: "Печатает",
  paused: "Пауза",
  idle: "Свободен",
  error: "Ошибка",
  offline: "Не в сети",
};
const SUMMARY_KEYS = ["printing", "paused", "idle", "error", "offline"];
const FILTER_KEYS = ["all", "printing", "paused", "idle", "error", "offline"];
const GROUP_ORDER = ["printing", "paused", "error", "idle", "offline"];
const GROUP_LABELS = {
  printing: "Печатают",
  paused: "Пауза",
  error: "Ошибки",
  idle: "Свободны",
  offline: "Не в сети",
};
const ACTION_LABELS = {
  pause: "Пауза включена",
  resume: "Печать продолжена",
  cancel: "Печать отменена",
};
const UI_LABELS = {
  mainsail: "Mainsail",
  fluidd: "Fluidd",
  unknown: "Интерфейс",
};
const SCAN_TIMEOUT_MS = 60000;
const PLAN_POLL_MS = 30000;
const PLAN_MONTHS = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];
const PLAN_MONTHS_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];
const PLAN_WEEKDAYS = [
  "воскресенье", "понедельник", "вторник", "среда",
  "четверг", "пятница", "суббота",
];
const HISTORY_RANGES = { "24h": 86400, "7d": 604800, "30d": 2592000 };
const HISTORY_LIMIT = 500;
const HISTORY_EVENT_LIMIT = 200;

const state = {
  printers: [],
  summary: {},
  job: {},
  config: {},
  selected: new Set(),
  files: [],
  filter: "all",
  query: "",
  compact: false,
  grouped: false,
  lastDir: "",
};

const cards = new Map();
const actionBusy = new Set();
const modalStack = [];
const journalIndexes = new Set();
let templateEl = null;
let cardsEl = null;
let summaryEl = null;
let searchEl = null;
let filtersEl = null;
let toastsEl = null;
let connEl = null;
let connTextEl = null;
let printerCountEl = null;
let refreshSecondsEl = null;
let sendEstimateEl = null;
let cancelJobEl = null;
let filesCountEl = null;
let filesNamesEl = null;
let modalFiles = null;
let modalPrinters = null;
let modalScan = null;
let scanSubnetEl = null;
let scanPortEl = null;
let scanUiPortEl = null;
let scanRunEl = null;
let scanStatusEl = null;
let scanResultsEl = null;
let scanAddEl = null;
let scanBusy = false;
let scanFound = [];
let scanSelected = new Set();
let modalError = null;
let modalConfirm = null;
let modalRename = null;
let renameTitleEl = null;
let renameHintEl = null;
let renameInputEl = null;
let renamePreviewEl = null;
let renameConfirmEl = null;
let fsBrowseEl = null;
let confirmTextEl = null;
let errorTitleEl = null;
let errorLabelEl = null;
let errorCodeEl = null;
let errorDetailEl = null;
let errorHintEl = null;
let errorWikiEl = null;
let fsListEl = null;
let fsCrumbsEl = null;
let fsUpEl = null;
let fsPreviewImage = null;
let fsPreviewEmpty = null;
let fsPreviewNameEl = null;
let fsSelectedCountEl = null;
let fsConfirmEl = null;
let printerListEl = null;
let printerForm = null;
let printerKeyEl = null;
let printerNameEl = null;
let printerHostEl = null;
let printerPortEl = null;
let printerMainsailEl = null;
let printerDeleteEl = null;
let printerResetEl = null;
let printerSubmitEl = null;
let journalPanel = null;
let journalEl = null;
let journalCountEl = null;
let journalToggleEl = null;
let journalCloseEl = null;
let lastErrorToast = 0;
let pollTimer = 0;
let inFlight = false;
let refreshRequested = false;
let sendBusy = false;
let lastJobRunning = false;
let confirmResolve = null;
let lastFocus = null;
let journalBusy = false;
let journalCursor = -1;
let journalCount = 0;
let fsPath = "";
let fsParent = null;
let fsEntries = [];
let fsSelected = new Set();
let fsActive = "";
let fsRequest = 0;
let fsPreviewSrc = "";
let lastFsPath = "";
let printerItems = [];
let layoutSignature = "";
let bulkBusy = false;
let renameMode = "single";
let renameKey = "";
let renameBusy = false;
let browseBusy = false;
let staleServerWarned = false;
let modalHistory = null;
let historyTitleEl = null;
let historyPillEl = null;
let historyStateLabelEl = null;
let historyLastEl = null;
let historyTimelineEl = null;
let historyEventsEl = null;
let historySummaryEl = null;
let historyRangesEl = null;
let historyKey = "";
let historyRange = "24h";
let historyRequest = 0;
let modalPlan = null;
let planButtonEl = null;
let planBadgeEl = null;
let planStatusEl = null;
let planData = null;
let planBusy = false;
let planTableEl = null;
let planTableBodyEl = null;
let planEmptyEl = null;
let planImportEl = null;
let planPrevEl = null;
let planNextEl = null;
let planTodayEl = null;
let planMonthEl = null;
let planWeeksEl = null;
let planSelectionEl = null;
let planSelectionTitleEl = null;
let planSelectionSummaryEl = null;
let planCalendar = null;
let planCalendarMonth = "";
let planCalendarRequest = 0;
let planSelection = null;
const planCalendarCache = new Map();
let modalPrinting = null;
let printingButtonEl = null;
let printingCountEl = null;
let printingSummaryEl = null;
let printingPartsSection = null;
let printingPartsBodyEl = null;
let printingPrintersSection = null;
let printingPrintersBodyEl = null;
let printingEmptyEl = null;
function pick(root, selectors) {
  for (const selector of selectors) {
    const found = root.querySelector(selector);
    if (found) return found;
  }
  return null;
}

function field(root, name) {
  return (
    root.querySelector('[data-field="' + name + '"]') ||
    root.querySelector("[data-" + name + "]") ||
    root.querySelector("." + name)
  );
}

function setText(node, value) {
  if (!node) return;
  const text = value === null || value === undefined ? "" : String(value);
  if (node.textContent !== text) node.textContent = text;
}

function clamp(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.min(max, Math.max(min, number));
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(number) / Math.log(1024)));
  const scaled = number / Math.pow(1024, index);
  const text = index === 0 ? String(Math.round(scaled)) : scaled.toFixed(1).replace(/\.0$/, "").replace(".", ",");
  return text + " " + units[index];
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return "меньше минуты";
  let hours = Math.floor(total / 3600);
  let minutes = Math.round((total % 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  if (hours > 0 && minutes > 0) return hours + " ч " + minutes + " мин";
  if (hours > 0) return hours + " ч";
  return minutes + " мин";
}

function parseFinish(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() !== "") {
    return numeric < 1000000000000 ? numeric : numeric / 1000;
  }
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date.getTime() / 1000;
}

function clockShort(seconds) {
  const date = new Date(seconds * 1000);
  return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
}

function clockFinish(seconds) {
  const date = new Date(seconds * 1000);
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) return clockShort(seconds);
  const day = String(date.getDate()).padStart(2, "0") + "." + String(date.getMonth() + 1).padStart(2, "0");
  return day + " " + clockShort(seconds);
}

function formatEta(status, seconds, finishAt) {
  if (status === "paused") return "на паузе";
  if (status !== "printing") return "—";
  const finish = parseFinish(finishAt);
  const now = Date.now() / 1000;
  if (finish !== null && finish <= now) return "завершается";
  let remaining = Number(seconds);
  if (finish !== null) remaining = finish - now;
  if (!Number.isFinite(remaining) || remaining <= 0) {
    return finish !== null ? "до " + clockFinish(finish) : "—";
  }
  if (finish !== null) return "осталось " + formatDuration(remaining) + " · до " + clockFinish(finish);
  return "осталось " + formatDuration(remaining);
}

function formatTemp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return Math.round(number) + "°";
}

function formatAgo(timestamp) {
  const number = Number(timestamp);
  if (!Number.isFinite(number) || number <= 0) return "нет данных";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - number));
  if (seconds < 60) return "только что";
  if (seconds < 3600) return Math.round(seconds / 60) + " мин назад";
  if (seconds < 86400) return Math.round(seconds / 3600) + " ч назад";
  return Math.round(seconds / 86400) + " дн назад";
}

function formatClock(value) {
  if (value === null || value === undefined || value === "") return "";
  let date = null;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() !== "") {
    date = new Date(numeric < 1000000000000 ? numeric * 1000 : numeric);
  } else {
    date = new Date(String(value));
  }
  if (!date || Number.isNaN(date.getTime())) return "";
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return hours + ":" + minutes + ":" + seconds;
}

function lastSeenText(timestamp) {
  const number = Number(timestamp);
  if (!Number.isFinite(number) || number <= 0) return "нет связи";
  return "последний ответ " + formatAgo(number);
}

function plural(count, one, few, many) {
  const number = Math.abs(Number(count) || 0) % 100;
  const last = number % 10;
  if (number > 10 && number < 20) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

function baseName(path) {
  const parts = String(path || "").split(/[\\/]/);
  const name = parts[parts.length - 1];
  return name || String(path || "");
}

function parentPath(path) {
  const text = String(path || "").replace(/[\\/]+$/, "");
  if (!text) return null;
  if (/^[A-Za-z]:$/.test(text)) return "";
  const index = Math.max(text.lastIndexOf("\\"), text.lastIndexOf("/"));
  if (index < 0) return null;
  if (index === 2 && /^[A-Za-z]:/.test(text)) return text.slice(0, 2) + "\\";
  return text.slice(0, index);
}

function joinPath(base, name) {
  const text = String(name || "");
  if (/^[A-Za-z]:[\\/]?$/.test(text)) return text.slice(0, 2) + "\\";
  if (!base) return text;
  const separator = /[\\/]$/.test(base) ? "" : "\\";
  return base + separator + text;
}

function crumbParts(path) {
  const text = String(path || "");
  const parts = [];
  if (!text) return parts;
  if (/^[A-Za-z]:[\\/]/.test(text)) {
    const drive = text.slice(0, 2);
    parts.push({ label: drive, path: drive + "\\" });
    let current = drive + "\\";
    for (const segment of text.slice(2).split(/[\\/]+/).filter(Boolean)) {
      current = current + segment;
      parts.push({ label: segment, path: current });
      current = current + "\\";
    }
    return parts;
  }
  if (text.charAt(0) === "/") {
    parts.push({ label: "/", path: "/" });
    let current = "";
    for (const segment of text.slice(1).split("/").filter(Boolean)) {
      current = current + "/" + segment;
      parts.push({ label: segment, path: current });
    }
    return parts;
  }
  let current = "";
  for (const segment of text.split(/[\\/]+/).filter(Boolean)) {
    current = current ? current + "\\" + segment : segment;
    parts.push({ label: segment, path: current });
  }
  return parts;
}

function errorText(error, fallback) {
  if (error && error.message) return String(error.message);
  return fallback || "Неизвестная ошибка";
}

function toast(text, kind) {
  if (!toastsEl) return;
  const node = document.createElement("div");
  node.className = "toast " + (kind || "info");
  node.setAttribute("role", "status");
  node.textContent = text;
  toastsEl.appendChild(node);
  window.setTimeout(function () {
    node.classList.add("hide");
    window.setTimeout(function () {
      node.remove();
    }, 300);
  }, TOAST_MS);
}

function notifyServerError() {
  const now = Date.now();
  if (now - lastErrorToast < 10000) return;
  lastErrorToast = now;
  toast("Нет связи с сервером", "error");
}

function setConnection(online) {
  if (connEl) connEl.classList.toggle("offline", !online);
  setText(connTextEl, online ? "Сервер работает" : "Нет связи");
}

async function requestJSON(path, options) {
  const settings = options || {};
  const controller = new AbortController();
  const timeoutMs = settings.timeout || API_TIMEOUT_MS;
  const timeout = window.setTimeout(function () {
    controller.abort();
  }, timeoutMs);
  const method = settings.method || "GET";
  const init = {
    method: method,
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: controller.signal,
  };
  if (settings.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(settings.body);
  } else if (method !== "GET") {
    init.headers["Content-Type"] = "application/json";
    init.body = "{}";
  }
  try {
    const response = await fetch(path, init);
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    if (!response.ok) {
      const message =
        data && (data.error || data.message)
          ? String(data.error || data.message)
          : "Ошибка сервера (HTTP " + response.status + ")";
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error && error.name === "AbortError") throw new Error("Сервер не ответил вовремя.");
    if (error instanceof TypeError) throw new Error("Нет связи с сервером.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function fetchState() {
  if (inFlight) return;
  inFlight = true;
  window.clearTimeout(pollTimer);
  const controller = new AbortController();
  const timer = window.setTimeout(function () {
    controller.abort();
  }, FETCH_TIMEOUT_MS);
  try {
    const response = await fetch("/api/state", {
      signal: controller.signal,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    state.printers = Array.isArray(data.printers) ? data.printers : [];
    state.summary = data.summary || {};
    state.job = data.job || {};
    state.config = data.config || {};
    if (!staleServerWarned && data.version !== EXPECTED_SERVER_VERSION) {
      staleServerWarned = true;
      toast("Сервер запущен со старой версией. Перезапустите приложение.", "warn");
    }
    state.lastDir = typeof state.config.last_dir === "string" ? state.config.last_dir : "";
    const refreshSeconds = Number(state.config.refresh_seconds);
    if (Number.isFinite(refreshSeconds) && refreshSeconds > 0) {
      setText(refreshSecondsEl, refreshSeconds);
    }
    lastErrorToast = 0;
    setConnection(true);
    noteJobExit();
    render();
    if (journalPanel && !journalPanel.hidden) loadJournal();
  } catch (error) {
    setConnection(false);
    notifyServerError();
  } finally {
    window.clearTimeout(timer);
    inFlight = false;
    if (refreshRequested) {
      refreshRequested = false;
      fetchState();
      return;
    }
    const delay = state.job && state.job.running ? POLL_JOB_MS : POLL_IDLE_MS;
    pollTimer = window.setTimeout(fetchState, delay);
  }
}

async function refreshState() {
  if (inFlight) {
    refreshRequested = true;
    return;
  }
  await fetchState();
}

function noteJobExit() {
  const job = state.job || {};
  const running = Boolean(job.running);
  if (lastJobRunning && !running && job.finished) {
    const ok = Number(job.ok) || 0;
    const failed = Number(job.failed) || 0;
    if (job.cancelled) toast("Отправка отменена.", "warn");
    else if (failed > 0) toast("Отправка завершена. Успешно: " + ok + ", ошибок: " + failed + ".", "error");
    else toast("Отправка завершена. Успешно: " + ok + ".");
  }
  lastJobRunning = running;
}

function render() {
  renderSummary();
  renderPrinting();
  renderCards();
  renderJob();
  applyFilters();
  updateSelectionUI();
}

function summaryTile(key) {
  if (!summaryEl) return null;
  return (
    summaryEl.querySelector('[data-stat="' + key + '"]') ||
    summaryEl.querySelector('[data-summary="' + key + '"]') ||
    summaryEl.querySelector("#summary-" + key)
  );
}

function summaryValue(tile) {
  return pick(tile, ["[data-stat-value]", "[data-value]", ".value", ".tile-value"]);
}

function renderSummary() {
  setText(printerCountEl, state.printers.length);
  if (!summaryEl) return;
  const counts = { printing: 0, paused: 0, idle: 0, error: 0, offline: 0 };
  for (const view of state.printers) {
    if (Object.prototype.hasOwnProperty.call(counts, view.state)) counts[view.state] += 1;
  }
  for (const key of SUMMARY_KEYS) {
    const tile = summaryTile(key);
    if (!tile) continue;
    const fromServer = state.summary ? state.summary[key] : undefined;
    const value = Number.isFinite(Number(fromServer)) ? Number(fromServer) : counts[key];
    const valueNode = summaryValue(tile);
    if (valueNode) setText(valueNode, value);
    else setText(tile, value);
  }
  const nearest = summaryTile("farm") || summaryTile("nearest_finish") || summaryTile("nearest");
  if (nearest) {
    const valueNode = summaryValue(nearest);
    const text = formatEta("printing", state.summary ? state.summary.nearest_finish : null);
    if (valueNode) setText(valueNode, text);
    else setText(nearest, text);
  }
}

function renderCards() {
  if (!cardsEl) return;
  const alive = new Set();
  for (const view of state.printers) {
    if (!view || !view.key) continue;
    alive.add(view.key);
    upsertCard(view);
  }
  for (const [key, record] of cards) {
    if (alive.has(key)) continue;
    record.el.remove();
    cards.delete(key);
    state.selected.delete(key);
  }
  layoutCards();
  updateEmptyState();
}

function updateEmptyState() {
  const empty = document.getElementById("cards-empty") || (cardsEl ? cardsEl.querySelector("[data-empty]") : null);
  if (!empty) return;
  const visible = countVisible();
  const searching = state.query.trim() !== "" || state.filter !== "all";
  empty.hidden = visible > 0;
  const text = empty.querySelector("p");
  if (text) setText(text, searching ? "Ничего не найдено." : "Принтеров нет — добавьте принтер.");
}

function countVisible() {
  let visible = 0;
  for (const record of cards.values()) {
    if (matches(record.view)) visible += 1;
  }
  return visible;
}

function statusOf(view) {
  const status = view && view.state ? view.state : "offline";
  return GROUP_ORDER.indexOf(status) === -1 ? "offline" : status;
}

function groupCounts() {
  const counts = {};
  for (const key of GROUP_ORDER) counts[key] = 0;
  for (const record of cards.values()) counts[statusOf(record.view)] += 1;
  return counts;
}

function layoutCards() {
  if (!cardsEl) return;
  const ordered = [];
  if (state.grouped) {
    for (const record of cards.values()) ordered.push(record);
    ordered.sort(function (left, right) {
      const rank = GROUP_ORDER.indexOf(statusOf(left.view)) - GROUP_ORDER.indexOf(statusOf(right.view));
      if (rank !== 0) return rank;
      return String((left.view && left.view.name) || "").localeCompare(String((right.view && right.view.name) || ""), "ru");
    });
  } else {
    for (const view of state.printers) {
      const record = cards.get(view.key);
      if (record && ordered.indexOf(record) === -1) ordered.push(record);
    }
    for (const record of cards.values()) {
      if (ordered.indexOf(record) === -1) ordered.push(record);
    }
  }
  const signature =
    (state.grouped ? "g" : "f") +
    "|" +
    ordered
      .map(function (record) {
        const key = String(record.el.dataset.key);
        if (!state.grouped) return key;
        const name = String((record.view && record.view.name) || "");
        return key + ":" + statusOf(record.view) + ":" + name;
      })
      .join(",");
  if (signature === layoutSignature) return;
  layoutSignature = signature;
  const focus = document.activeElement;
  const keepFocus = focus instanceof HTMLElement && cardsEl.contains(focus) ? focus : null;
  for (const head of Array.from(cardsEl.querySelectorAll(".group-head"))) head.remove();
  if (state.grouped) {
    const counts = groupCounts();
    let current = "";
    for (const record of ordered) {
      const status = statusOf(record.view);
      if (status !== current) {
        current = status;
        const head = document.createElement("div");
        head.className = "group-head";
        head.dataset.group = status;
        const label = document.createElement("span");
        label.textContent = GROUP_LABELS[status] || status;
        const count = document.createElement("b");
        count.textContent = String(counts[status] || 0);
        head.appendChild(label);
        head.appendChild(count);
        cardsEl.appendChild(head);
      }
      cardsEl.appendChild(record.el);
    }
  } else {
    for (const record of ordered) cardsEl.appendChild(record.el);
  }
  if (keepFocus && keepFocus.isConnected && document.activeElement !== keepFocus) {
    keepFocus.focus({ preventScroll: true });
  }
}

function thumbEmptyMessage(status) {
  if (status === "offline") return "Не в сети";
  if (status === "error") return "Ошибка";
  if (status === "idle") return "Готов к загрузке";
  return "Превью нет";
}

function cameraUrl(view) {
  const host = view && typeof view.host === "string" ? view.host.trim() : "";
  return "http://" + host + ":" + CAMERA_PORT + "/";
}

function uiUrl(view) {
  const url = view && typeof view.mainsail_url === "string" ? view.mainsail_url.trim() : "";
  return /^https?:\/\//i.test(url) ? url : "";
}

function uiLabel(view) {
  const kind = view && typeof view.ui_kind === "string" ? view.ui_kind.toLowerCase() : "";
  return UI_LABELS[kind] || UI_LABELS.unknown;
}

function createCard(view) {
  if (!templateEl || !templateEl.content) return null;
  const node = templateEl.content.firstElementChild;
  if (!node) return null;
  const el = node.cloneNode(true);
  el.dataset.key = view.key;
  const refs = {
    select: field(el, "selected") || field(el, "select") || el.querySelector('input[type="checkbox"]'),
    name: field(el, "name"),
    host: field(el, "host"),
    pill: field(el, "state") || el.querySelector(".pill"),
    stateLabel: field(el, "state-label"),
    thumb: field(el, "thumb") || el.querySelector("img"),
    thumbEmpty: field(el, "thumb-empty") || el.querySelector(".empty"),
    thumbEmptyText: field(el, "thumb-empty-text"),
    tag: field(el, "thumb-tag"),
    file: field(el, "file"),
    progressTrack: field(el, "progress"),
    progressBar: field(el, "progress-bar") || el.querySelector(".progress-bar"),
    progressPct: field(el, "progress-pct") || field(el, "progress-text") || field(el, "percent"),
    eta: field(el, "eta"),
    layer: field(el, "layer") || field(el, "layers"),
    nozzle: field(el, "nozzle"),
    bed: field(el, "bed"),
    filament: field(el, "filament"),
    lastSeen: field(el, "last-seen") || field(el, "seen") || field(el, "thumb-tag"),
    error: field(el, "error") || el.querySelector(".error-banner"),
    errorLabel: field(el, "error-label"),
    errorCode: field(el, "error-code"),
    upload: field(el, "upload"),
    uploadLabel: field(el, "upload-label"),
    uploadBar: field(el, "upload-bar"),
    uploadPct: field(el, "upload-pct"),
    mainsail: field(el, "mainsail"),
    mainsailLabel: field(el, "mainsail-label"),
    mainsailMenu: field(el, "mainsail-menu"),
    camera: field(el, "camera"),
    pause: field(el, "pause"),
    resume: field(el, "resume"),
    menu: field(el, "menu"),
    cardMenu: field(el, "card-menu"),
    cancelPrint: field(el, "cancel-print") || el.querySelector('[data-action="cancel-print"]'),
  };
  if (!refs.stateLabel) refs.stateLabel = refs.pill;
  refs.parts = field(el, "parts");
  if (!refs.parts && refs.file) {
    refs.parts = document.createElement("p");
    refs.parts.className = "fname muted parts";
    refs.parts.dataset.field = "parts";
    refs.parts.style.fontSize = "11.5px";
    refs.file.insertAdjacentElement("afterend", refs.parts);
  }
  const record = {
    el: el,
    refs: refs,
    view: view,
    thumbVersion: null,
    tagText: refs.lastSeen ? refs.lastSeen.textContent : "",
  };
  if (refs.select) {
    refs.select.setAttribute("aria-label", "Выбрать принтер " + (view.name || view.key));
    refs.select.addEventListener("change", function () {
      toggleSelect(view.key, refs.select.checked);
    });
  }
  if (refs.error) {
    refs.error.addEventListener("click", function () {
      openErrorModal(cards.has(view.key) ? cards.get(view.key).view : view);
    });
  }
  cardsEl.appendChild(el);
  cards.set(view.key, record);
  return record;
}

function upsertCard(view) {
  let record = cards.get(view.key);
  if (!record) record = createCard(view);
  if (!record) return;
  record.view = view;
  const el = record.el;
  const refs = record.refs;
  const selected = state.selected.has(view.key);
  const status = view.state || "offline";

  setText(refs.name, view.name || view.key);
  setText(refs.host, view.host ? view.host + ":" + view.port : "");
  if (refs.pill) refs.pill.className = "pill " + status;
  setText(refs.stateLabel, STATE_LABELS[status] || status);
  el.classList.toggle("offline", status === "offline");
  el.classList.toggle("selected", selected);
  if (refs.select) refs.select.checked = selected;

  setText(refs.file, view.file || "—");
  const parts = Array.isArray(view.parts) ? view.parts : [];
  const partsText = parts
    .filter(function (part) {
      return part && part.name;
    })
    .map(function (part) {
      return String(part.name) + " × " + (Number(part.count) || 0);
    })
    .join(", ");
  setText(refs.parts, partsText ? "Детали: " + partsText : "");
  if (refs.parts) refs.parts.hidden = !partsText;
  const percent = Math.round(clamp(view.progress, 0, 1) * 100);
  if (refs.progressTrack) {
    refs.progressTrack.classList.toggle("paused", status === "paused");
    refs.progressTrack.classList.toggle("error", status === "error");
  }
  if (refs.progressBar) refs.progressBar.style.width = percent + "%";
  setText(refs.progressPct, percent + "%");
  setText(refs.eta, formatEta(status, view.eta_seconds, view.finish_at));
  setText(refs.layer, view.layer_total ? view.layer + " / " + view.layer_total : "—");
  setText(refs.nozzle, formatTemp(view.nozzle));
  setText(refs.bed, formatTemp(view.bed));
  setText(refs.filament, view.filament || "");
  if (refs.filament) refs.filament.hidden = !view.filament;

  if (refs.lastSeen) {
    const offline = status === "offline";
    setText(refs.lastSeen, offline ? lastSeenText(view.last_seen) : record.tagText);
    refs.lastSeen.hidden = !offline;
  }

  if (refs.error) {
    const error = view.error;
    refs.error.hidden = !error;
    setText(refs.errorLabel, error ? error.label || "Ошибка принтера" : "");
    if (refs.errorCode) {
      setText(refs.errorCode, error && error.code ? error.code : "");
      refs.errorCode.hidden = !(error && error.code);
    }
  }

  const url = uiUrl(view);
  setText(refs.mainsailLabel, uiLabel(view));
  if (refs.mainsail) {
    if (url) {
      refs.mainsail.href = url;
      refs.mainsail.removeAttribute("aria-disabled");
      refs.mainsail.classList.remove("disabled");
      refs.mainsail.title = url;
    } else {
      refs.mainsail.removeAttribute("href");
      refs.mainsail.setAttribute("aria-disabled", "true");
      refs.mainsail.classList.add("disabled");
      refs.mainsail.title = "Адрес интерфейса неизвестен";
    }
  }
  if (refs.mainsailMenu) {
    if (url) {
      refs.mainsailMenu.setAttribute("href", url);
      refs.mainsailMenu.removeAttribute("aria-disabled");
      refs.mainsailMenu.classList.remove("disabled");
      refs.mainsailMenu.title = url;
    } else {
      refs.mainsailMenu.removeAttribute("href");
      refs.mainsailMenu.setAttribute("aria-disabled", "true");
      refs.mainsailMenu.classList.add("disabled");
      refs.mainsailMenu.title = "Адрес интерфейса неизвестен";
    }
  }

  if (refs.camera) {
    const cameraHost = typeof view.host === "string" ? view.host.trim() : "";
    if (cameraHost) {
      const cameraHref = cameraUrl(view);
      refs.camera.setAttribute("href", cameraHref);
      refs.camera.setAttribute("rel", "noopener noreferrer");
      refs.camera.removeAttribute("aria-disabled");
      refs.camera.classList.remove("disabled");
      refs.camera.title = cameraHref;
    } else {
      refs.camera.removeAttribute("href");
      refs.camera.setAttribute("aria-disabled", "true");
      refs.camera.classList.add("disabled");
      refs.camera.title = "Адрес камеры неизвестен";
    }
  }

  const controllable = status === "printing" || status === "paused";
  if (refs.pause) refs.pause.hidden = status !== "printing";
  if (refs.resume) refs.resume.hidden = status !== "paused";
  if (refs.menu) refs.menu.hidden = false;
  if (refs.cancelPrint) refs.cancelPrint.hidden = !controllable;

  updateThumb(record, view);
}

function updateThumb(record, view) {
  const version = view.thumb_version || "";
  if (record.thumbVersion === version) {
    if (record.refs.thumbEmptyText && !version) {
      setText(record.refs.thumbEmptyText, thumbEmptyMessage(view.state));
    }
    return;
  }
  record.thumbVersion = version;
  const image = record.refs.thumb;
  if (image) {
    if (version) {
      image.src = "/api/thumb/" + encodeURIComponent(view.key) + "?v=" + encodeURIComponent(version);
      image.hidden = false;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
    }
  }
  if (record.refs.thumbEmpty) record.refs.thumbEmpty.hidden = Boolean(version);
  if (record.refs.thumbEmptyText && !version) {
    setText(record.refs.thumbEmptyText, thumbEmptyMessage(view.state));
  }
}

function matches(view) {
  if (!view) return false;
  if (state.filter !== "all" && view.state !== state.filter) return false;
  const query = state.query.trim().toLowerCase();
  if (!query) return true;
  const haystack = [view.name, view.host, view.file].filter(Boolean).join(" ").toLowerCase();
  return haystack.indexOf(query) !== -1;
}

function applyFiltersTo(record) {
  const visible = matches(record.view);
  record.el.hidden = !visible;
  record.el.classList.toggle("hidden", !visible);
}

function syncSummaryTiles() {
  if (!summaryEl) return;
  for (const tile of summaryEl.querySelectorAll("[data-filter]")) {
    const active = tile.dataset.filter === state.filter;
    tile.classList.toggle("active", active);
    tile.setAttribute("aria-pressed", String(active));
  }
}

function setFilter(key) {
  state.filter = FILTER_KEYS.indexOf(key) === -1 ? "all" : key;
  applyFilters();
}

function applyFilters() {
  for (const record of cards.values()) applyFiltersTo(record);
  if (filtersEl) {
    const counts = { all: state.printers.length, printing: 0, paused: 0, idle: 0, error: 0, offline: 0 };
    for (const view of state.printers) {
      if (Object.prototype.hasOwnProperty.call(counts, view.state)) counts[view.state] += 1;
    }
    for (const chip of filtersEl.querySelectorAll("[data-filter]")) {
      const key = chip.dataset.filter;
      const active = key === state.filter;
      chip.classList.toggle("active", active);
      chip.setAttribute("aria-pressed", String(active));
      const counter = pick(chip, ["[data-count]", ".count"]);
      if (counter) setText(counter, counts[key] === undefined ? "" : counts[key]);
    }
  }
  syncSummaryTiles();
  if (cardsEl) {
    for (const head of cardsEl.querySelectorAll(".group-head")) {
      let visible = 0;
      for (const record of cards.values()) {
        if (statusOf(record.view) !== head.dataset.group) continue;
        if (matches(record.view)) visible += 1;
      }
      const counter = head.querySelector("b");
      if (counter) setText(counter, visible);
      head.hidden = visible === 0;
    }
  }
  updateEmptyState();
}

function toggleSelect(key, value) {
  const next = typeof value === "boolean" ? value : !state.selected.has(key);
  if (next) state.selected.add(key);
  else state.selected.delete(key);
  updateSelectionUI();
}

function updateSelectionUI() {
  for (const [key, record] of cards) {
    const selected = state.selected.has(key);
    if (record.refs.select) record.refs.select.checked = selected;
    record.el.classList.toggle("selected", selected);
  }
  const countNode = pick(document, [
    "#selected-count",
    '[data-action="send"] [data-count]',
    "#send-count",
    "#selection-count",
    "#send .count",
    "#send-button .count",
  ]);
  if (countNode) setText(countNode, state.selected.size);
  const sendButton = document.getElementById("send") || document.getElementById("send-button");
  if (sendButton) {
    sendButton.disabled =
      state.selected.size === 0 || sendBusy || Boolean(state.job && state.job.running);
  }
  for (const id of ["bulk-pause", "bulk-resume", "bulk-cancel", "bulk-rename"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = bulkBusy || state.selected.size === 0;
  }
  updateSendEstimate();
}

function updateSendEstimate() {
  if (!sendEstimateEl) return;
  const printers = state.selected.size;
  const sends = printers * state.files.length;
  setText(
    sendEstimateEl,
    "Выбрано " +
      printers +
      " " +
      plural(printers, "принтер", "принтера", "принтеров") +
      " · " +
      sends +
      " " +
      plural(sends, "отправка", "отправки", "отправок")
  );
}

function renderFilesStrip() {
  const count = state.files.length;
  if (filesCountEl) setText(filesCountEl, count + " " + plural(count, "файл", "файла", "файлов"));
  if (filesNamesEl) {
    if (!count) {
      setText(filesNamesEl, "Файлы не выбраны");
      filesNamesEl.title = "";
    } else {
      const names = state.files.slice(0, 3).map(baseName);
      const suffix = count > 3 ? " и ещё " + (count - 3) : "";
      setText(filesNamesEl, names.join(", ") + suffix);
      filesNamesEl.title = state.files.join("\n");
    }
  }
  updateSendEstimate();
}

function renderJob() {
  const job = state.job && typeof state.job === "object" ? state.job : {};
  const running = Boolean(job.running);
  const rows = job.per_printer && typeof job.per_printer === "object" ? job.per_printer : {};
  for (const [key, record] of cards) {
    const refs = record.refs;
    if (!refs.upload) continue;
    const row = rows[key];
    const visible = Boolean(row) && running;
    refs.upload.hidden = !visible;
    if (!visible) continue;
    const progress = clamp(row.progress, 0, 100);
    if (refs.uploadBar) refs.uploadBar.style.width = progress + "%";
    setText(refs.uploadPct, Math.round(progress) + " %");
    const error = row.last_error ? String(row.last_error) : "";
    let label = row.status ? String(row.status) : "Отправка";
    if (error) label = error.length > 60 ? error.slice(0, 57) + "…" : error;
    setText(refs.uploadLabel, label);
    if (refs.uploadLabel) refs.uploadLabel.title = error || label;
  }
  if (cancelJobEl) {
    cancelJobEl.disabled = !running || Boolean(job.cancelling);
    setText(cancelJobEl, job.cancelling ? "Отмена…" : "Отмена");
  }
}

function applyCompact(value, persist) {
  state.compact = Boolean(value);
  document.body.classList.toggle("compact", state.compact);
  const button = document.getElementById("compact-toggle");
  if (button) button.setAttribute("aria-pressed", String(state.compact));
  if (persist) {
    try {
      window.localStorage.setItem(COMPACT_KEY, state.compact ? "1" : "0");
    } catch {}
  }
}

function restoreCompact() {
  let stored = null;
  try {
    stored = window.localStorage.getItem(COMPACT_KEY);
  } catch {
    stored = null;
  }
  applyCompact(stored === "1", false);
}

function bindFilterClicks() {
  if (!filtersEl) return;
  filtersEl.addEventListener("click", function (event) {
    const target = event.target;
    const chip = target instanceof Element ? target.closest("[data-filter]") : null;
    if (!chip) return;
    const key = chip.dataset.filter || "all";
    setFilter(key);
  });
}

function bindSummaryClicks() {
  if (!summaryEl) return;
  summaryEl.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target : null;
    const tile = target ? target.closest("[data-filter]") : null;
    if (!tile) return;
    setFilter(tile.dataset.filter || "all");
  });
}

function applyGrouped(value, persist) {
  state.grouped = Boolean(value);
  const button = document.getElementById("group-toggle");
  if (button) button.setAttribute("aria-pressed", String(state.grouped));
  if (persist) {
    try {
      window.localStorage.setItem(GROUPED_KEY, state.grouped ? "1" : "0");
    } catch {}
  }
  layoutSignature = "";
  layoutCards();
  applyFilters();
}

function restoreGrouped() {
  let stored = null;
  try {
    stored = window.localStorage.getItem(GROUPED_KEY);
  } catch {
    stored = null;
  }
  applyGrouped(stored === "1", false);
}

function openModal(modal) {
  if (!modal) return;
  const index = modalStack.indexOf(modal);
  if (index === -1) {
    if (!modalStack.length) {
      lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    modalStack.push(modal);
  } else {
    modalStack.splice(index, 1);
    modalStack.push(modal);
  }
  modal.hidden = false;
}

function closeModal(modal) {
  if (!modal) return;
  modal.hidden = true;
  const index = modalStack.indexOf(modal);
  if (index !== -1) modalStack.splice(index, 1);
  if (modal === modalConfirm && confirmResolve) {
    const resolve = confirmResolve;
    confirmResolve = null;
    resolve(false);
  }
  if (!modalStack.length && lastFocus && typeof lastFocus.focus === "function") {
    lastFocus.focus();
    lastFocus = null;
  }
}

function closeTopModal() {
  const modal = modalStack[modalStack.length - 1];
  if (modal) closeModal(modal);
}

function finishConfirm(value) {
  const resolve = confirmResolve;
  confirmResolve = null;
  closeModal(modalConfirm);
  if (resolve) resolve(value);
}

function confirmDialog(text) {
  if (!modalConfirm) return Promise.resolve(false);
  if (confirmResolve) {
    const previous = confirmResolve;
    confirmResolve = null;
    previous(false);
  }
  setText(confirmTextEl, text || "Вы уверены?");
  openModal(modalConfirm);
  const cancel = modalConfirm.querySelector("[data-close]");
  if (cancel) cancel.focus();
  return new Promise(function (resolve) {
    confirmResolve = resolve;
  });
}

function closeCardMenus(except) {
  for (const record of cards.values()) {
    const refs = record.refs;
    if (!refs.cardMenu || refs.cardMenu === except) continue;
    refs.cardMenu.hidden = true;
    if (refs.menu) refs.menu.setAttribute("aria-expanded", "false");
  }
}

function hasOpenCardMenu() {
  for (const record of cards.values()) {
    if (record.refs.cardMenu && !record.refs.cardMenu.hidden) return true;
  }
  return false;
}

function toggleCardMenu(key) {
  const record = cards.get(key);
  if (!record || !record.refs.cardMenu) return;
  const open = record.refs.cardMenu.hidden;
  closeCardMenus(open ? record.refs.cardMenu : null);
  record.refs.cardMenu.hidden = !open;
  if (record.refs.menu) record.refs.menu.setAttribute("aria-expanded", String(open));
}

async function printAction(key, action) {
  const busyKey = key + ":" + action;
  if (actionBusy.has(busyKey)) return;
  actionBusy.add(busyKey);
  const record = cards.get(key);
  const button = record ? record.refs[action === "cancel" ? null : action] : null;
  if (button) button.disabled = true;
  try {
    await requestJSON("/api/print/" + encodeURIComponent(key) + "/" + action, {
      method: "POST",
      body: {},
    });
    const name = record && record.view ? record.view.name || record.view.key : key;
    toast((ACTION_LABELS[action] || "Готово") + ": " + name);
    await refreshState();
  } catch (error) {
    toast(errorText(error, "Не удалось выполнить действие."), "error");
  } finally {
    actionBusy.delete(busyKey);
    if (button) button.disabled = false;
  }
}

async function bulkPrintAction(action) {
  const selected = Array.from(state.selected);
  if (!selected.length) {
    toast("Выберите принтеры.", "warn");
    return;
  }
  const views = new Map(
    state.printers.map(function (view) {
      return [view.key, view];
    })
  );
  const applicable = function (key) {
    const view = views.get(key);
    const status = view && view.state ? view.state : "";
    if (action === "pause") return status === "printing";
    if (action === "resume") return status === "paused";
    return true;
  };
  const keys = selected.filter(applicable);
  const skipped = selected.length - keys.length;
  if (!keys.length) {
    toast("Нет принтеров в подходящем состоянии.", "warn");
    return;
  }
  if (bulkBusy) return;
  if (action === "cancel") {
    const confirmed = await confirmDialog("Отменить печать на выбранных принтерах (" + keys.length + ")?");
    if (!confirmed) return;
  }
  bulkBusy = true;
  updateSelectionUI();
  toast("Массовое действие выполняется…");
  const results = await Promise.allSettled(
    keys.map(function (key) {
      return requestJSON("/api/print/" + encodeURIComponent(key) + "/" + action, {
        method: "POST",
        body: {},
      });
    })
  );
  let ok = 0;
  const failed = [];
  for (let index = 0; index < results.length; index += 1) {
    const result = results[index];
    if (result.status === "fulfilled") {
      ok += 1;
      continue;
    }
    failed.push(
      ((views.get(keys[index]) && views.get(keys[index]).name) || keys[index]) +
        ": " +
        errorText(result.reason, "Ошибка")
    );
  }
  const label = ACTION_LABELS[action] || "Готово";
  const skippedText = skipped ? " Пропущено: " + skipped + "." : "";
  if (failed.length) {
    toast(
      label + ". Успешно: " + ok + ", ошибок: " + failed.length + "." + skippedText + " " + failed.join("; "),
      ok ? "warn" : "error"
    );
  } else {
    toast(label + ": " + ok + "." + skippedText);
  }
  bulkBusy = false;
  updateSelectionUI();
  await refreshState();
}

function renameTargets() {
  if (renameMode === "single") {
    const record = cards.get(renameKey);
    if (record && record.view) return [record.view];
    const view = state.printers.find(function (item) {
      return item.key === renameKey;
    });
    return view ? [view] : [];
  }
  const ordered = [];
  const seen = new Set();
  if (cardsEl) {
    for (const el of cardsEl.children) {
      if (!el.classList || !el.classList.contains("card")) continue;
      const key = el.dataset.key;
      const record = cards.get(key);
      if (!record || !record.view || !state.selected.has(key)) continue;
      ordered.push(record.view);
      seen.add(key);
    }
  }
  for (const view of state.printers) {
    if (!state.selected.has(view.key) || seen.has(view.key)) continue;
    ordered.push(view);
    seen.add(view.key);
  }
  return ordered;
}

function resolveName(pattern, view, index) {
  const host = String((view && view.host) || "");
  const octet = host.split(".").pop() || host;
  return String(pattern || "")
    .replace(/\{n\}/g, String(index + 1))
    .replace(/\{ip\}/g, octet)
    .replace(/\{name\}/g, String((view && view.name) || (view && view.key) || ""))
    .replace(/\{host\}/g, host)
    .trim();
}

function renderRenamePreview() {
  if (!renamePreviewEl) return;
  renamePreviewEl.textContent = "";
  if (renameMode !== "bulk") {
    renamePreviewEl.hidden = true;
    return;
  }
  renamePreviewEl.hidden = false;
  const pattern = renameInputEl ? renameInputEl.value : "";
  const targets = renameTargets();
  if (!targets.length) {
    const line = document.createElement("div");
    line.textContent = "Нет выбранных принтеров";
    renamePreviewEl.appendChild(line);
    return;
  }
  const limit = Math.min(3, targets.length);
  for (let index = 0; index < limit; index += 1) {
    const line = document.createElement("div");
    line.appendChild(document.createTextNode(String(index + 1) + ". "));
    const strong = document.createElement("b");
    strong.textContent = resolveName(pattern, targets[index], index) || "—";
    line.appendChild(strong);
    renamePreviewEl.appendChild(line);
  }
}

function openRenameModal(mode, key) {
  if (!modalRename) return;
  renameMode = mode === "bulk" ? "bulk" : "single";
  renameKey = key || "";
  if (renameMode === "single") {
    const record = cards.get(renameKey);
    const view = record ? record.view : null;
    setText(renameTitleEl, "Переименовать принтер");
    setText(renameHintEl, view ? view.host + ":" + view.port : "");
    if (renameInputEl) {
      renameInputEl.value = view ? view.name || "" : "";
      renameInputEl.placeholder = "Имя принтера";
    }
  } else {
    setText(renameTitleEl, "Переименовать выбранные");
    setText(renameHintEl, "Шаблон: {n} — номер, {ip} — последний октет, {name} — текущее имя, {host} — адрес.");
    if (renameInputEl) {
      renameInputEl.value = "{name}";
      renameInputEl.placeholder = "Принтер {n}";
    }
  }
  renderRenamePreview();
  openModal(modalRename);
  if (renameInputEl) {
    window.setTimeout(function () {
      if (!modalRename || modalRename.hidden) return;
      renameInputEl.focus();
      renameInputEl.select();
    }, 0);
  }
}

async function confirmRename() {
  if (renameBusy) return;
  const pattern = renameInputEl ? renameInputEl.value.trim() : "";
  const targets = renameTargets();
  if (!targets.length) {
    toast("Нечего переименовывать.", "warn");
    return;
  }
  if (!pattern) {
    toast("Введите имя.", "warn");
    return;
  }
  const names = targets.map(function (view, index) {
    return renameMode === "bulk" ? resolveName(pattern, view, index) : pattern;
  });
  const empty = names.some(function (name) {
    return !name;
  });
  if (empty) {
    toast("Имя не может быть пустым.", "warn");
    return;
  }
  renameBusy = true;
  if (renameConfirmEl) renameConfirmEl.disabled = true;
  let ok = 0;
  let errors = [];
  if (renameMode === "bulk") {
    toast("Переименование выполняется…");
    try {
      const data = await requestJSON("/api/printers/rename", {
        method: "POST",
        body: {
          updates: targets.map(function (view, index) {
            return { key: view.key, name: names[index] };
          }),
        },
      });
      ok = Number(data && data.renamed) || 0;
      errors = data && Array.isArray(data.errors) ? data.errors.map(String) : [];
    } catch (error) {
      errors = [errorText(error, "Не удалось переименовать.")];
    }
  } else {
    const target = targets[0];
    try {
      await requestJSON("/api/printers/" + encodeURIComponent(target.key), {
        method: "PUT",
        body: { name: names[0] },
      });
      ok = 1;
    } catch (error) {
      const label = target.name || target.key;
      errors = ["«" + label + "»: " + errorText(error, "Не удалось переименовать.")];
    }
  }
  renameBusy = false;
  if (renameConfirmEl) renameConfirmEl.disabled = false;
  if (!errors.length) closeModal(modalRename);
  await refreshState();
  if (errors.length) {
    toast(
      "Переименовано: " + ok + ", ошибок: " + errors.length + ". " + errors.join("; "),
      ok ? "warn" : "error"
    );
    return;
  }
  toast("Переименовано " + ok + " " + plural(ok, "принтер", "принтера", "принтеров") + ".");
}

function gcodePaths(data) {
  const lists = [];
  if (Array.isArray(data)) lists.push(data);
  if (data && typeof data === "object") {
    for (const name of ["files", "paths", "selected", "entries", "items"]) {
      if (Array.isArray(data[name])) lists.push(data[name]);
    }
  }
  const seen = new Set();
  const paths = [];
  for (const list of lists) {
    for (const item of list) {
      let path = "";
      if (typeof item === "string") {
        path = item;
      } else if (item && typeof item === "object") {
        if (typeof item.path === "string") path = item.path;
        else if (typeof item.name === "string") path = item.name;
      }
      if (!path || seen.has(path) || !/\.gcode$/i.test(path)) continue;
      seen.add(path);
      paths.push(path);
    }
  }
  return paths;
}

async function browseFiles() {
  if (!fsBrowseEl || browseBusy) return;
  browseBusy = true;
  fsBrowseEl.disabled = true;
  try {
    const data = await requestJSON("/api/fs/browse", {
      method: "POST",
      body: { initial_dir: state.lastDir || "" },
      timeout: BROWSE_TIMEOUT_MS,
    });
    const dir =
      data && typeof data.dir === "string"
        ? data.dir
        : data && typeof data.path === "string"
          ? data.path
          : "";
    if (dir) {
      state.lastDir = dir;
      lastFsPath = dir;
    }
    const merged = new Set(state.files);
    let added = 0;
    for (const path of gcodePaths(data)) {
      if (merged.has(path)) continue;
      merged.add(path);
      added += 1;
    }
    if (!added) {
      if (data && data.cancelled) {
        toast("Диалог закрыт без выбора.", "warn");
      } else if (data && Number(data.rejected) > 0) {
        toast("Файлы .gcode не выбраны. Отклонено файлов: " + Number(data.rejected) + ".", "warn");
      } else {
        toast("Файлы .gcode не выбраны.", "warn");
      }
      return;
    }
    state.files = Array.from(merged).sort();
    renderFilesStrip();
    updateSelectionUI();
    if (modalFiles && !modalFiles.hidden) closeModal(modalFiles);
    toast("Добавлено файлов: " + added + ".");
  } catch (error) {
    toast(errorText(error, "Не удалось открыть проводник."), "error");
  } finally {
    browseBusy = false;
    fsBrowseEl.disabled = false;
  }
}

function openErrorModal(view) {
  const error = view && view.error ? view.error : null;
  setText(errorTitleEl, view && view.name ? "Ошибка · " + view.name : "Ошибка принтера");
  setText(errorLabelEl, error && error.label ? error.label : "Ошибка принтера");
  setText(errorCodeEl, error && error.code ? error.code : "—");
  setText(errorDetailEl, error && error.detail ? error.detail : "—");
  setText(errorHintEl, error && error.hint ? error.hint : "Откройте Mainsail и проверьте состояние.");
  const url = error && error.help_url ? String(error.help_url) : "";
  if (errorWikiEl) {
    if (/^https?:\/\//i.test(url)) errorWikiEl.href = url;
    else errorWikiEl.setAttribute("href", DEFAULT_HELP_URL);
  }
  openModal(modalError);
}

async function loadFs(path) {
  if (!fsListEl) return;
  const token = fsRequest + 1;
  fsRequest = token;
  renderFsMessage("Загрузка…");
  try {
    const data = await requestJSON("/api/fs?path=" + encodeURIComponent(path || ""), {
      timeout: FS_TIMEOUT_MS,
    });
    if (token !== fsRequest) return;
    const current = data && typeof data.path === "string" ? data.path : path || "";
    fsPath = current;
    fsEntries = data && Array.isArray(data.entries) ? data.entries.filter(Boolean) : [];
    if (data && data.parent === null) fsParent = null;
    else if (data && typeof data.parent === "string") fsParent = data.parent;
    else fsParent = parentPath(fsPath);
    lastFsPath = fsPath;
    renderCrumbs();
    renderFsList();
    updateFsFooter();
    updateFsPreview();
    focusFirstFsEntry();
  } catch (error) {
    if (token !== fsRequest) return;
    renderFsMessage("Не удалось открыть папку.");
    toast(errorText(error, "Не удалось открыть папку."), "error");
  }
}

function fsIsDir(entry) {
  return Boolean(entry.is_dir) || entry.type === "dir" || entry.kind === "dir";
}

function renderFsMessage(text) {
  if (!fsListEl) return;
  fsListEl.textContent = "";
  const item = document.createElement("li");
  const label = document.createElement("span");
  label.className = "meta";
  label.textContent = text;
  item.appendChild(label);
  fsListEl.appendChild(item);
}

function renderFsList() {
  if (!fsListEl) return;
  fsListEl.textContent = "";
  if (!fsEntries.length) {
    renderFsMessage("Пусто");
    return;
  }
  for (const entry of fsEntries) {
    const name = entry && entry.name !== undefined ? String(entry.name) : "";
    if (!name) continue;
    const isDir = fsIsDir(entry);
    const path = typeof entry.path === "string" && entry.path ? entry.path : joinPath(fsPath, name);
    const item = document.createElement("li");
    item.dataset.path = path;
    if (isDir) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dir";
      button.dataset.nav = path;
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "▸";
      const label = document.createElement("span");
      label.textContent = name;
      button.appendChild(icon);
      button.appendChild(label);
      item.appendChild(button);
    } else {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.className = "check";
      box.checked = fsSelected.has(path);
      box.setAttribute("aria-label", "Выбрать " + name);
      box.addEventListener("change", function () {
        fsSetSelected(path, box.checked);
      });
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.open = path;
      const label = document.createElement("span");
      label.textContent = name;
      button.appendChild(label);
      const size = Number(entry.size);
      if (Number.isFinite(size) && size > 0) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = formatBytes(size);
        button.appendChild(meta);
      }
      item.appendChild(box);
      item.appendChild(button);
    }
    item.classList.toggle("active", !isDir && fsActive === path);
    item.classList.toggle("selected", !isDir && fsSelected.has(path));
    fsListEl.appendChild(item);
  }
}

function renderCrumbs() {
  if (!fsCrumbsEl) return;
  fsCrumbsEl.textContent = "";
  const parts = crumbParts(fsPath);
  if (!parts.length) {
    const label = document.createElement("span");
    label.textContent = "Диски";
    fsCrumbsEl.appendChild(label);
  } else {
    parts.forEach(function (part, index) {
      if (index > 0) {
        const separator = document.createElement("span");
        separator.setAttribute("aria-hidden", "true");
        separator.textContent = "\\";
        fsCrumbsEl.appendChild(separator);
      }
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.crumb = part.path;
      button.textContent = part.label;
      fsCrumbsEl.appendChild(button);
    });
  }
  if (fsUpEl) fsUpEl.disabled = fsParent === null || fsParent === undefined;
}

function focusFirstFsEntry() {
  if (!fsListEl || !modalFiles || modalFiles.hidden) return;
  const button = fsListEl.querySelector("button");
  if (!button) return;
  window.setTimeout(function () {
    if (modalFiles && !modalFiles.hidden) button.focus({ preventScroll: true });
  }, 0);
}

function lastSelectedPath() {
  const values = Array.from(fsSelected);
  return values.length ? values[values.length - 1] : "";
}

function fsSetSelected(path, value) {
  if (value) {
    fsSelected.add(path);
    fsActive = path;
  } else {
    fsSelected.delete(path);
    if (fsActive === path) fsActive = lastSelectedPath();
  }
  if (fsListEl) {
    for (const item of fsListEl.children) {
      const itemPath = item.dataset ? item.dataset.path : "";
      const selected = fsSelected.has(itemPath);
      item.classList.toggle("selected", selected);
      item.classList.toggle("active", itemPath === fsActive);
      const box = item.querySelector('input[type="checkbox"]');
      if (box) box.checked = selected;
    }
  }
  updateFsFooter();
  updateFsPreview();
}

function updateFsFooter() {
  if (fsConfirmEl) fsConfirmEl.disabled = fsSelected.size === 0;
  setText(fsSelectedCountEl, "Выбрано файлов: " + fsSelected.size);
}

function updateFsPreview() {
  if (!fsPreviewImage) return;
  if (!fsActive) {
    fsPreviewSrc = "";
    fsPreviewImage.removeAttribute("src");
    fsPreviewImage.hidden = true;
    if (fsPreviewEmpty) {
      fsPreviewEmpty.hidden = false;
      setText(fsPreviewEmpty, "Файл не выбран");
    }
    setText(fsPreviewNameEl, "Выберите файл .gcode");
    return;
  }
  const name = baseName(fsActive);
  setText(fsPreviewNameEl, name);
  const src = "/api/files/thumb?path=" + encodeURIComponent(fsActive);
  if (fsPreviewSrc === src) {
    if (fsPreviewImage.naturalWidth > 0) {
      fsPreviewImage.hidden = false;
      if (fsPreviewEmpty) fsPreviewEmpty.hidden = true;
    }
    return;
  }
  fsPreviewSrc = src;
  fsPreviewImage.alt = "Превью " + name;
  fsPreviewImage.hidden = true;
  if (fsPreviewEmpty) {
    fsPreviewEmpty.hidden = false;
    setText(fsPreviewEmpty, "Загрузка…");
  }
  fsPreviewImage.onload = function () {
    fsPreviewImage.hidden = false;
    if (fsPreviewEmpty) fsPreviewEmpty.hidden = true;
  };
  fsPreviewImage.onerror = function () {
    fsPreviewImage.hidden = true;
    if (fsPreviewEmpty) {
      fsPreviewEmpty.hidden = false;
      setText(fsPreviewEmpty, "Превью нет");
    }
  };
  fsPreviewImage.src = src;
}

function openFilesModal() {
  if (!modalFiles) return;
  fsSelected = new Set(state.files);
  fsActive = state.files.length ? state.files[0] : "";
  let start = lastFsPath || state.config.last_dir || "";
  if (!start && state.files.length) {
    const parent = parentPath(state.files[0]);
    start = parent || "";
  }
  fsPath = start || "";
  fsEntries = [];
  fsParent = parentPath(fsPath);
  fsPreviewSrc = "";
  openModal(modalFiles);
  renderCrumbs();
  updateFsFooter();
  updateFsPreview();
  loadFs(fsPath);
}

function confirmFiles() {
  if (!fsSelected.size) return;
  state.files = Array.from(fsSelected).sort();
  lastFsPath = fsPath;
  renderFilesStrip();
  updateSelectionUI();
  closeModal(modalFiles);
  toast("Выбрано файлов: " + state.files.length);
}

function normalizePrinter(item) {
  if (!item || typeof item !== "object") return null;
  let key = typeof item.key === "string" ? item.key : "";
  let host = typeof item.host === "string" ? item.host : "";
  let port = Number(item.port);
  if (!Number.isFinite(port) || port <= 0) {
    const match = key.match(/:(\d+)$/);
    port = match ? Number(match[1]) : 7125;
  }
  if (!host && key) {
    const index = key.lastIndexOf(":");
    if (index > 0) host = key.slice(0, index);
  }
  if (!key) key = host + ":" + port;
  let mainsail = Number(item.mainsail_port);
  if (!Number.isFinite(mainsail) || mainsail <= 0) {
    const match = String(item.mainsail_url || "").match(/:(\d+)\/?$/);
    mainsail = match ? Number(match[1]) : 4408;
  }
  return {
    key: key,
    name: String(item.name || key),
    host: host,
    port: port,
    mainsail_port: mainsail,
  };
}

function markActivePrinter() {
  if (!printerListEl) return;
  const activeKey = printerKeyEl ? printerKeyEl.value : "";
  for (const item of printerListEl.children) {
    item.classList.toggle("active", Boolean(activeKey) && item.dataset.key === activeKey);
  }
}

function renderPrinterList() {
  if (!printerListEl) return;
  printerListEl.textContent = "";
  for (const printer of printerItems) {
    const item = document.createElement("li");
    item.dataset.key = printer.key;
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("b");
    name.textContent = printer.name;
    const details = document.createElement("small");
    details.textContent = printer.host + ":" + printer.port + " · Mainsail " + printer.mainsail_port;
    who.appendChild(name);
    who.appendChild(details);
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn ghost";
    edit.dataset.key = printer.key;
    edit.textContent = "Изменить";
    item.appendChild(who);
    item.appendChild(edit);
    printerListEl.appendChild(item);
  }
  markActivePrinter();
}

async function refreshPrinterList(quiet) {
  if (!printerListEl) return;
  try {
    const data = await requestJSON("/api/printers", { timeout: 6000 });
    const list = Array.isArray(data)
      ? data
      : data && Array.isArray(data.printers)
        ? data.printers
        : [];
    printerItems = list.map(normalizePrinter).filter(Boolean);
    renderPrinterList();
  } catch (error) {
    printerItems = state.printers.map(normalizePrinter).filter(Boolean);
    renderPrinterList();
    if (!quiet) toast(errorText(error, "Не удалось загрузить принтеры."), "error");
  }
}

function resetPrinterForm() {
  if (!printerForm) return;
  printerForm.reset();
  if (printerKeyEl) printerKeyEl.value = "";
  if (printerDeleteEl) printerDeleteEl.hidden = true;
  markActivePrinter();
}

function fillPrinterForm(printer) {
  if (!printer || !printerForm) return;
  if (printerKeyEl) printerKeyEl.value = printer.key;
  if (printerNameEl) printerNameEl.value = printer.name;
  if (printerHostEl) printerHostEl.value = printer.host;
  if (printerPortEl) printerPortEl.value = String(printer.port);
  if (printerMainsailEl) printerMainsailEl.value = String(printer.mainsail_port);
  if (printerDeleteEl) printerDeleteEl.hidden = false;
  markActivePrinter();
  if (printerNameEl) printerNameEl.focus();
}

function openPrintersModal() {
  if (!modalPrinters) return;
  resetPrinterForm();
  openModal(modalPrinters);
  refreshPrinterList(false);
  if (printerNameEl) {
    window.setTimeout(function () {
      if (modalPrinters && !modalPrinters.hidden) printerNameEl.focus();
    }, 0);
  }
}

function validPort(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 1 && number <= 65535;
}

async function savePrinter(event) {
  if (event) event.preventDefault();
  if (!printerForm) return;
  if (!printerForm.reportValidity()) return;
  const name = printerNameEl ? printerNameEl.value.trim() : "";
  const host = printerHostEl ? printerHostEl.value.trim() : "";
  const port = printerPortEl ? Number(printerPortEl.value) : 0;
  const mainsail = printerMainsailEl ? Number(printerMainsailEl.value) : 0;
  if (!name || !host) {
    toast("Укажите имя и адрес принтера.", "warn");
    return;
  }
  if (!validPort(port) || !validPort(mainsail)) {
    toast("Порт должен быть от 1 до 65535.", "warn");
    return;
  }
  const oldKey = printerKeyEl ? printerKeyEl.value.trim() : "";
  const body = { name: name, host: host, port: port, mainsail_port: mainsail };
  const path = oldKey ? "/api/printers/" + encodeURIComponent(oldKey) : "/api/printers";
  const method = oldKey ? "PUT" : "POST";
  if (printerSubmitEl) printerSubmitEl.disabled = true;
  try {
    await requestJSON(path, { method: method, body: body });
    const newKey = host + ":" + port;
    if (oldKey && oldKey !== newKey && state.selected.has(oldKey)) {
      state.selected.delete(oldKey);
      state.selected.add(newKey);
      updateSelectionUI();
    }
    toast(oldKey ? "Принтер сохранён." : "Принтер добавлен.");
    resetPrinterForm();
    await refreshPrinterList(true);
    await refreshState();
  } catch (error) {
    toast(errorText(error, "Не удалось сохранить принтер."), "error");
  } finally {
    if (printerSubmitEl) printerSubmitEl.disabled = false;
  }
}

async function deletePrinter() {
  if (!printerKeyEl) return;
  const key = printerKeyEl.value.trim();
  if (!key) return;
  const known = printerItems.find(function (item) {
    return item.key === key;
  });
  const name = known ? known.name : key;
  const confirmed = await confirmDialog("Удалить принтер «" + name + "»?");
  if (!confirmed) return;
  if (printerDeleteEl) printerDeleteEl.disabled = true;
  try {
    await requestJSON("/api/printers/" + encodeURIComponent(key), { method: "DELETE", body: {} });
    state.selected.delete(key);
    updateSelectionUI();
    toast("Принтер удалён.");
    resetPrinterForm();
    await refreshPrinterList(true);
    await refreshState();
  } catch (error) {
    toast(errorText(error, "Не удалось удалить принтер."), "error");
  } finally {
    if (printerDeleteEl) printerDeleteEl.disabled = false;
  }
}

function scanAddress(item) {
  const host = item && typeof item.host === "string" ? item.host.trim() : "";
  const port = Number(item && item.port);
  return host + ":" + (Number.isFinite(port) && port > 0 ? port : 7125);
}

function existingAddresses() {
  const known = new Set();
  for (const view of state.printers) {
    if (!view || !view.host) continue;
    known.add(scanAddress(view));
  }
  return known;
}

function updateScanFooter() {
  if (scanAddEl) scanAddEl.disabled = scanBusy || scanSelected.size === 0;
}

function renderScanResults() {
  if (!scanResultsEl) return;
  scanResultsEl.textContent = "";
  const known = existingAddresses();
  for (const item of scanFound) {
    const key = scanAddress(item);
    const added = known.has(key);
    const row = document.createElement("li");
    row.className = "scan-row" + (added ? " added" : "");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "check";
    box.disabled = added;
    box.checked = added ? false : scanSelected.has(key);
    box.setAttribute("aria-label", "Добавить " + (item.name || key));
    box.addEventListener("change", function () {
      if (box.checked) scanSelected.add(key);
      else scanSelected.delete(key);
      updateScanFooter();
    });
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("b");
    name.textContent = String(item.name || key);
    const address = document.createElement("small");
    address.textContent = key + (added ? " · уже добавлен" : "");
    who.appendChild(name);
    who.appendChild(address);
    const kind = document.createElement("span");
    kind.className = "badge";
    kind.textContent = uiLabel(item);
    row.appendChild(box);
    row.appendChild(who);
    row.appendChild(kind);
    scanResultsEl.appendChild(row);
  }
  updateScanFooter();
}

function openScanModal() {
  if (!modalScan) return;
  openModal(modalScan);
  renderScanResults();
  if (scanRunEl) {
    window.setTimeout(function () {
      if (modalScan && !modalScan.hidden) scanRunEl.focus();
    }, 0);
  }
}

async function runScan() {
  if (scanBusy) return;
  const subnet = scanSubnetEl ? scanSubnetEl.value.trim() : "";
  const port = scanPortEl ? Number(scanPortEl.value) : 7125;
  const uiPort = scanUiPortEl ? Number(scanUiPortEl.value) : 4408;
  if (!validPort(port) || !validPort(uiPort)) {
    toast("Порт должен быть от 1 до 65535.", "warn");
    return;
  }
  scanBusy = true;
  scanFound = [];
  scanSelected = new Set();
  if (scanRunEl) scanRunEl.disabled = true;
  setText(scanStatusEl, "Сканирование… это может занять до минуты");
  renderScanResults();
  const body = { moonraker_port: port, ui_port: uiPort };
  if (subnet) body.subnet = subnet;
  try {
    const data = await requestJSON("/api/scan", {
      method: "POST",
      body: body,
      timeout: SCAN_TIMEOUT_MS,
    });
    scanFound =
      data && Array.isArray(data.found)
        ? data.found.filter(function (item) {
            return item && typeof item.host === "string" && item.host.trim();
          })
        : [];
    const resolved = data && typeof data.subnet === "string" ? data.subnet : "";
    if (resolved && scanSubnetEl) scanSubnetEl.value = resolved;
    renderScanResults();
    const scanned = Number(data && data.scanned);
    const scannedText = Number.isFinite(scanned) && scanned > 0 ? "Проверено адресов: " + scanned + ". " : "";
    setText(scanStatusEl, scannedText + "Найдено: " + scanFound.length + ".");
  } catch (error) {
    scanFound = [];
    renderScanResults();
    setText(scanStatusEl, errorText(error, "Не удалось просканировать сеть."));
    toast(errorText(error, "Не удалось просканировать сеть."), "error");
  } finally {
    scanBusy = false;
    if (scanRunEl) scanRunEl.disabled = false;
    updateScanFooter();
  }
}

async function importScanResults() {
  if (scanBusy || !scanSelected.size) {
    if (!scanSelected.size) toast("Выберите принтеры.", "warn");
    return;
  }
  const known = existingAddresses();
  const chosen = scanFound.filter(function (item) {
    const key = scanAddress(item);
    return scanSelected.has(key) && !known.has(key);
  });
  if (!chosen.length) {
    toast("Выберите принтеры.", "warn");
    return;
  }
  const fallbackUi = scanUiPortEl && validPort(Number(scanUiPortEl.value)) ? Number(scanUiPortEl.value) : 4408;
  const printers = chosen.map(function (item) {
    const uiPort = Number(item.ui_port);
    const port = Number(item.port);
    return {
      name: String(item.name || scanAddress(item)),
      host: String(item.host || "").trim(),
      port: Number.isFinite(port) && port > 0 ? port : 7125,
      mainsail_port: validPort(uiPort) ? uiPort : fallbackUi,
    };
  });
  scanBusy = true;
  if (scanRunEl) scanRunEl.disabled = true;
  if (scanAddEl) scanAddEl.disabled = true;
  setText(scanStatusEl, "Добавление принтеров…");
  try {
    const data = await requestJSON("/api/printers/import", {
      method: "POST",
      body: { printers: printers },
      timeout: SCAN_TIMEOUT_MS,
    });
    const added = Number(data && data.added) || 0;
    const skipped = Number(data && data.skipped) || 0;
    const skippedText = skipped ? " Пропущено: " + skipped + "." : "";
    toast("Добавлено: " + added + "." + skippedText, added ? "info" : "warn");
    scanSelected.clear();
    closeModal(modalScan);
    await refreshState();
  } catch (error) {
    setText(scanStatusEl, errorText(error, "Не удалось добавить принтеры."));
    toast(errorText(error, "Не удалось добавить принтеры."), "error");
  } finally {
    scanBusy = false;
    if (scanRunEl) scanRunEl.disabled = false;
    updateScanFooter();
  }
}

async function sendFiles() {
  if (sendBusy) return;
  if (!state.selected.size) {
    toast("Выберите принтеры.", "warn");
    return;
  }
  if (!state.files.length) {
    toast("Сначала выберите файлы .gcode.", "warn");
    openFilesModal();
    return;
  }
  if (state.job && state.job.running) {
    toast("Отправка уже идёт.", "warn");
    return;
  }
  sendBusy = true;
  updateSelectionUI();
  try {
    await requestJSON("/api/send", {
      method: "POST",
      body: { printers: Array.from(state.selected), files: state.files.slice() },
    });
    toast(
      "Отправка запущена: " +
        state.files.length +
        " " +
        plural(state.files.length, "файл", "файла", "файлов") +
        " × " +
        state.selected.size
    );
    await refreshState();
  } catch (error) {
    toast(errorText(error, "Не удалось начать отправку."), "error");
  } finally {
    sendBusy = false;
    updateSelectionUI();
  }
}

async function cancelJob() {
  if (!state.job || !state.job.running) {
    toast("Нет активной отправки.", "warn");
    return;
  }
  if (cancelJobEl) cancelJobEl.disabled = true;
  try {
    await requestJSON("/api/job/cancel", { method: "POST", body: {} });
    toast("Отмена запрошена.", "warn");
    await refreshState();
  } catch (error) {
    toast(errorText(error, "Не удалось отменить отправку."), "error");
  } finally {
    renderJob();
  }
}

function journalPayload(data) {
  if (Array.isArray(data)) return { entries: data, cursor: null };
  if (!data || typeof data !== "object") return { entries: [], cursor: null };
  const entries = Array.isArray(data.events)
    ? data.events
    : Array.isArray(data.items)
      ? data.items
      : Array.isArray(data.entries)
        ? data.entries
        : [];
  let cursor = null;
  for (const name of ["next", "last_index", "lastIndex", "cursor", "since"]) {
    const value = Number(data[name]);
    if (Number.isFinite(value)) {
      cursor = value;
      break;
    }
  }
  return { entries: entries, cursor: cursor };
}

function journalIndex(entry) {
  if (!entry || typeof entry !== "object") return null;
  for (const name of ["index", "id", "seq", "n", "number"]) {
    const value = Number(entry[name]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function journalText(entry) {
  if (typeof entry === "string") return entry;
  if (!entry || typeof entry !== "object") return "Событие";
  for (const name of ["text", "message", "msg", "detail", "event"]) {
    const value = entry[name];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "Событие";
}

function journalLevel(entry) {
  const raw =
    entry && typeof entry === "object"
      ? String(entry.level || entry.kind || entry.status || entry.type || "").toLowerCase()
      : "";
  const text = journalText(entry).toLowerCase();
  if (/error|failed|ошибка|сбой/.test(raw) || /ошибка|сбой|failed|error/.test(text)) return "error";
  if (/warn|warning|предупр/.test(raw) || /предупр/.test(text)) return "warn";
  if (/ok|success|done|готово|успешн/.test(raw) || /готово|успешн/.test(text)) return "ok";
  return "";
}

function journalTime(entry) {
  if (!entry || typeof entry !== "object") return "";
  return formatClock(entry.time ?? entry.ts ?? entry.timestamp ?? entry.at);
}

async function loadJournal() {
  if (!journalEl || journalBusy) return;
  journalBusy = true;
  const atBottom = journalEl.scrollHeight - journalEl.scrollTop - journalEl.clientHeight <= 48;
  try {
    const data = await requestJSON("/api/journal?since=" + journalCursor, { timeout: 6000 });
    const payload = journalPayload(data);
    let added = 0;
    let unindexed = 0;
    for (const entry of payload.entries) {
      const index = journalIndex(entry);
      if (index !== null) {
        if (journalIndexes.has(index)) continue;
        journalIndexes.add(index);
        if (index > journalCursor) journalCursor = index;
      } else {
        unindexed += 1;
      }
      const item = document.createElement("li");
      if (index !== null) item.dataset.journalIndex = String(index);
      const stamp = journalTime(entry);
      if (stamp) {
        const time = document.createElement("time");
        time.textContent = stamp;
        item.appendChild(time);
      }
      const label = document.createElement("span");
      const level = journalLevel(entry);
      if (level) label.className = level;
      label.textContent = journalText(entry);
      item.appendChild(label);
      journalEl.appendChild(item);
      added += 1;
    }
    if (payload.cursor !== null && payload.cursor > journalCursor) journalCursor = payload.cursor;
    else if (payload.cursor === null && unindexed) journalCursor += unindexed;
    while (journalEl.children.length > 200) {
      const first = journalEl.firstElementChild;
      if (!first) break;
      const stale = Number(first.dataset.journalIndex);
      if (Number.isFinite(stale)) journalIndexes.delete(stale);
      first.remove();
    }
    journalCount += added;
    setText(journalCountEl, journalCount);
    if (added && !journalPanel.hidden && atBottom) journalEl.scrollTop = journalEl.scrollHeight;
  } catch (error) {
    journalBusy = false;
    return;
  }
  journalBusy = false;
}

function setJournalOpen(value) {
  if (!journalPanel) return;
  const open = typeof value === "boolean" ? value : journalPanel.hidden;
  journalPanel.hidden = !open;
  if (journalToggleEl) journalToggleEl.setAttribute("aria-expanded", String(open));
  if (open) {
    loadJournal();
    if (journalEl) journalEl.scrollTop = journalEl.scrollHeight;
  }
}

function historyBounds(range) {
  const span = HISTORY_RANGES[range] || HISTORY_RANGES["24h"];
  const until = Math.floor(Date.now() / 1000);
  return { since: until - span, until: until };
}

function historyPartsText(parts) {
  if (Array.isArray(parts)) {
    return parts
      .filter(function (part) {
        return part && part.name;
      })
      .map(function (part) {
        return String(part.name) + " × " + (Number(part.count) || 0);
      })
      .join(", ");
  }
  const count = Number(parts);
  if (!Number.isFinite(count) || count <= 0) return "";
  return count + " " + plural(count, "деталь", "детали", "деталей");
}

function historyView(key) {
  const record = cards.get(key);
  return record && record.view ? record.view : null;
}

function updateHistoryState(state) {
  const known = state && STATE_LABELS[state] ? state : "";
  if (historyPillEl) historyPillEl.className = "pill " + (known || "idle");
  setText(historyStateLabelEl, known ? STATE_LABELS[known] : "—");
}

function syncHistoryRange() {
  if (!historyRangesEl) return;
  for (const button of historyRangesEl.querySelectorAll("[data-range]")) {
    const active = button.dataset.range === historyRange;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function renderHistoryMessage(text) {
  if (historyTimelineEl) {
    historyTimelineEl.textContent = "";
    const empty = document.createElement("span");
    empty.className = "timeline-empty";
    empty.textContent = text;
    historyTimelineEl.appendChild(empty);
  }
  if (historyEventsEl) {
    historyEventsEl.textContent = "";
    const item = document.createElement("li");
    item.className = "history-event empty";
    item.textContent = text;
    historyEventsEl.appendChild(item);
  }
}

function renderHistoryLast(last) {
  if (!last || typeof last !== "object") {
    setText(historyLastEl, "ещё не завершалась");
    return;
  }
  const chunks = [];
  if (last.file) chunks.push(baseName(last.file));
  const stamp = Number(last.ts ?? last.finished_at ?? last.end ?? last.time);
  if (Number.isFinite(stamp) && stamp > 0) chunks.push(clockFinish(stamp));
  const partsText = historyPartsText(last.parts);
  if (partsText) chunks.push(partsText);
  const duration = Number(last.duration);
  if (Number.isFinite(duration) && duration > 0) chunks.push(formatDuration(duration));
  setText(historyLastEl, chunks.length ? "Последняя: " + chunks.join(" · ") : "ещё не завершалась");
}

function renderTimeline(segments) {
  if (!historyTimelineEl) return;
  historyTimelineEl.textContent = "";
  const bounds = historyBounds(historyRange);
  const items = [];
  for (const segment of segments) {
    if (!segment) continue;
    const state = segment.state && STATE_LABELS[segment.state] ? segment.state : "idle";
    let start = Number(segment.start);
    let end = Number(segment.end);
    if (!Number.isFinite(start)) start = bounds.since;
    if (!Number.isFinite(end) || end <= start) end = start + 1;
    const from = Math.max(bounds.since, start);
    const to = Math.min(bounds.until, end);
    if (to <= from) continue;
    items.push({ state: state, from: from, to: to, file: segment.file ? baseName(segment.file) : "" });
  }
  const spans = [];
  let cursor = bounds.since;
  for (const item of items) {
    if (item.from > cursor) spans.push({ filler: true, from: cursor, to: item.from });
    spans.push(item);
    cursor = Math.max(cursor, item.to);
  }
  if (items.length && cursor < bounds.until) {
    spans.push({ filler: true, from: cursor, to: bounds.until });
  }
  for (const span of spans) {
    const node = document.createElement("span");
    const range = clockFinish(span.from) + " – " + clockFinish(span.to);
    if (span.filler) {
      node.className = "timeline-segment filler";
      node.title = "Нет данных\n" + range;
    } else {
      node.className = "timeline-segment " + span.state;
      const label = STATE_LABELS[span.state] || span.state;
      node.title = label + "\n" + range + (span.file ? "\n" + span.file : "");
    }
    node.style.flexGrow = String(span.to - span.from);
    node.style.flexBasis = "0";
    historyTimelineEl.appendChild(node);
  }
  const visible = items.length;
  if (!visible) {
    const empty = document.createElement("span");
    empty.className = "timeline-empty";
    empty.textContent = "Нет данных за период";
    historyTimelineEl.appendChild(empty);
  }
  historyTimelineEl.setAttribute("aria-label", "Лента состояний: участков " + visible);
}

function renderHistoryEvents(events) {
  if (!historyEventsEl) return;
  historyEventsEl.textContent = "";
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "history-event empty";
    item.textContent = "Событий нет";
    historyEventsEl.appendChild(item);
    return;
  }
  const rows = events.slice(-HISTORY_EVENT_LIMIT).reverse();
  for (const event of rows) {
    if (!event || typeof event !== "object") continue;
    const item = document.createElement("li");
    item.className = "history-event";
    const stamp = Number(event.ts);
    const time = document.createElement("time");
    time.textContent = Number.isFinite(stamp) && stamp > 0 ? clockFinish(stamp) : "—";
    const state = event.state && STATE_LABELS[event.state] ? event.state : "";
    const label = document.createElement("span");
    label.className = "history-event-state" + (state ? " " + state : "");
    label.textContent = state ? STATE_LABELS[state] : "—";
    const file = document.createElement("span");
    file.className = "history-event-file";
    file.textContent = event.file ? baseName(event.file) : "";
    item.appendChild(time);
    item.appendChild(label);
    item.appendChild(file);
    const partsText = historyPartsText(event.parts);
    if (partsText) {
      const parts = document.createElement("span");
      parts.className = "history-event-parts";
      parts.textContent = partsText;
      item.appendChild(parts);
    }
    if (event.message) {
      const message = document.createElement("span");
      message.className = "history-event-message";
      message.textContent = String(event.message);
      item.appendChild(message);
    }
    historyEventsEl.appendChild(item);
  }
}

function renderHistory(data) {
  const printer = data && data.printer && typeof data.printer === "object" ? data.printer : {};
  const fallback = historyView(historyKey);
  updateHistoryState(printer.state || (fallback ? fallback.state : ""));
  renderHistoryLast(data ? data.last_completion : null);
  const segments = data && Array.isArray(data.segments) ? data.segments : [];
  const events = data && Array.isArray(data.events) ? data.events : [];
  renderTimeline(segments);
  renderHistoryEvents(events);
  const shown = Math.min(events.length, HISTORY_EVENT_LIMIT);
  setText(historySummaryEl, "Событий: " + shown + " · участков: " + segments.length);
}

async function loadHistory() {
  if (!historyKey) return;
  const token = historyRequest + 1;
  historyRequest = token;
  const bounds = historyBounds(historyRange);
  if (historySummaryEl) setText(historySummaryEl, "Загрузка…");
  try {
    const data = await requestJSON(
      "/api/history/" +
        encodeURIComponent(historyKey) +
        "?since=" +
        bounds.since +
        "&until=" +
        bounds.until +
        "&limit=" +
        HISTORY_LIMIT,
      { timeout: 6000 }
    );
    if (token !== historyRequest || !modalHistory || modalHistory.hidden) return;
    renderHistory(data || {});
  } catch (error) {
    if (token !== historyRequest) return;
    renderHistoryMessage("Не удалось загрузить историю.");
    setText(historySummaryEl, "Ошибка");
    toast(errorText(error, "Не удалось загрузить историю."), "error");
  }
}

function openHistoryModal(key) {
  if (!modalHistory || !key) return;
  const view = historyView(key);
  historyKey = key;
  historyRange = "24h";
  setText(historyTitleEl, view && view.name ? view.name : key);
  updateHistoryState(view ? view.state : "");
  setText(historyLastEl, "ещё не завершалась");
  renderHistoryMessage("Загрузка…");
  syncHistoryRange();
  openModal(modalHistory);
  loadHistory();
}

function formatPlanNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  if (Number.isInteger(number)) return String(number);
  return number.toFixed(1).replace(/\.0$/, "").replace(".", ",");
}

function planDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

function planShortDate(date) {
  return planPad(date.getDate()) + "." + planPad(date.getMonth() + 1) + "." + date.getFullYear();
}

function planItemPercent(item) {
  const provided = item ? item.percent : null;
  if (provided !== null && provided !== undefined && provided !== "") {
    const percent = Number(provided);
    if (Number.isFinite(percent)) return Math.max(0, Math.min(100, Math.round(percent)));
  }
  const target = Number(item ? item.target : 0);
  const actual = Number(item ? item.actual : 0);
  if (!Number.isFinite(target) || target <= 0) return 0;
  if (!Number.isFinite(actual) || actual <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((actual / target) * 100)));
}

function planPad(value) {
  return String(value).padStart(2, "0");
}

function planDateKey(date) {
  return date.getFullYear() + "-" + planPad(date.getMonth() + 1) + "-" + planPad(date.getDate());
}

function planMonthKey(date) {
  return date.getFullYear() + "-" + planPad(date.getMonth() + 1);
}

function planMonthDate(key) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(key || ""));
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, 1);
  return Number.isNaN(date.getTime()) ? null : date;
}

function planMonthTitle(key) {
  const date = planMonthDate(key);
  if (!date) return "—";
  const month = PLAN_MONTHS[date.getMonth()];
  return month.charAt(0).toUpperCase() + month.slice(1) + " " + date.getFullYear();
}

function planDayLong(date) {
  return date.getDate() + " " + PLAN_MONTHS_GEN[date.getMonth()] + ", " + PLAN_WEEKDAYS[date.getDay()];
}

function planLongRange(start, end) {
  if (!start || !end) return "";
  let left = start.getDate() + " " + PLAN_MONTHS_GEN[start.getMonth()];
  let right = end.getDate() + " " + PLAN_MONTHS_GEN[end.getMonth()];
  if (start.getFullYear() !== end.getFullYear()) {
    left += " " + start.getFullYear();
    right += " " + end.getFullYear();
  }
  return left + " – " + right;
}

function planIsoWeek(date) {
  const copy = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = copy.getUTCDay() || 7;
  copy.setUTCDate(copy.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(copy.getUTCFullYear(), 0, 1));
  return {
    year: copy.getUTCFullYear(),
    week: Math.ceil(((copy.getTime() - yearStart.getTime()) / 86400000 + 1) / 7),
  };
}

function planDayEntry(key) {
  const days = planCalendar && Array.isArray(planCalendar.days) ? planCalendar.days : [];
  for (const day of days) {
    if (day && String(day.date || "").slice(0, 10) === key) return day;
  }
  return null;
}

function planWeekFound(startKey) {
  const weeks = planCalendar && Array.isArray(planCalendar.weeks) ? planCalendar.weeks : [];
  for (const week of weeks) {
    if (week && String(week.start || "").slice(0, 10) === startKey) return week;
  }
  const start = planDay(startKey);
  if (!start) return null;
  const iso = planIsoWeek(start);
  for (const week of weeks) {
    if (week && Number(week.week) === iso.week && Number(week.year) === iso.year) return week;
  }
  return null;
}

function planAggregateItems(entries) {
  const seen = new Map();
  const rows = [];
  for (const entry of entries) {
    const items = entry && Array.isArray(entry.items) ? entry.items : [];
    for (const item of items) {
      if (!item) continue;
      const key = String(item.key || item.code || item.name || rows.length);
      if (!seen.has(key)) {
        const row = {
          key: key,
          name: item.name || "",
          code: item.code || "",
          target: 0,
          actual: 0,
          percent: null,
        };
        seen.set(key, row);
        rows.push(row);
      }
      const row = seen.get(key);
      row.target += Number(item.target) || 0;
      row.actual += Number(item.actual) || 0;
    }
  }
  return rows;
}

function planWeekInfo(startKey) {
  const start = planDay(startKey);
  if (!start) return null;
  const found = planWeekFound(startKey);
  if (found) {
    const end = planDay(found.end) || new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
    return {
      start: planDay(found.start) || start,
      end: end,
      week: Number(found.week) || planIsoWeek(start).week,
      target: Number(found.target) || 0,
      actual: Number(found.actual) || 0,
      items: Array.isArray(found.items) ? found.items.filter(Boolean) : [],
    };
  }
  const entries = [];
  for (let index = 0; index < 7; index += 1) {
    const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    const entry = planDayEntry(planDateKey(date));
    if (entry) entries.push(entry);
  }
  let target = 0;
  let actual = 0;
  for (const entry of entries) {
    target += Number(entry.target) || 0;
    actual += Number(entry.actual) || 0;
  }
  return {
    start: start,
    end: new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6),
    week: planIsoWeek(start).week,
    target: target,
    actual: actual,
    items: planAggregateItems(entries),
  };
}

function planStatusClass(target, actual) {
  const plan = Number(target) || 0;
  const fact = Number(actual) || 0;
  if (plan > 0 && fact >= plan) return "done";
  if (fact > 0 && fact < plan) return "partial";
  if (plan <= 0 && fact <= 0) return "empty";
  return plan > 0 ? "has-plan" : "";
}

function planDayStatus(entry) {
  return planStatusClass(entry ? entry.target : 0, entry ? entry.actual : 0);
}

function planCountLabel(target, actual) {
  return formatPlanNumber(target) + " / " + formatPlanNumber(actual);
}

function planDefaultSelection(key) {
  const now = new Date();
  if (planMonthKey(now) === key) return { kind: "day", date: planDateKey(now) };
  return { kind: "day", date: key + "-01" };
}

function planSelectionMonthKey() {
  if (!planSelection) return "";
  return planSelection.kind === "week"
    ? String(planSelection.start || "").slice(0, 7)
    : String(planSelection.date || "").slice(0, 7);
}

function planCalendarRow(dayButton, entry, date, monthIndex, todayKey) {
  const key = planDateKey(date);
  dayButton.type = "button";
  dayButton.className = "plan-day";
  dayButton.dataset.date = key;
  if (date.getMonth() !== monthIndex) dayButton.classList.add("other-month");
  if (key === todayKey) dayButton.classList.add("today");
  if (planSelection && planSelection.kind === "day" && planSelection.date === key) {
    dayButton.classList.add("selected");
  }
  const status = planDayStatus(entry);
  if (status) dayButton.classList.add(status);
  const number = document.createElement("span");
  number.className = "plan-day-num";
  number.textContent = String(date.getDate());
  const count = document.createElement("span");
  count.className = "plan-day-count";
  const target = entry ? Number(entry.target) || 0 : 0;
  const actual = entry ? Number(entry.actual) || 0 : 0;
  count.textContent = target > 0 || actual > 0 ? planCountLabel(target, actual) : "";
  dayButton.appendChild(number);
  dayButton.appendChild(count);
  const title = planDayLong(date) + " · план " + formatPlanNumber(target) + ", факт " + formatPlanNumber(actual);
  dayButton.title = title;
  dayButton.setAttribute("aria-label", title);
  return key;
}

function planWeekButton(startKey, isoWeek) {
  const info = planWeekInfo(startKey);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "plan-week";
  button.dataset.weekStart = startKey;
  if (planSelection && planSelection.kind === "week" && planSelection.start === startKey) {
    button.classList.add("selected");
  }
  const status = planStatusClass(info ? info.target : 0, info ? info.actual : 0);
  if (status) button.classList.add(status);
  const number = document.createElement("span");
  number.className = "plan-week-num";
  number.textContent = "Нед. " + (info ? info.week : isoWeek);
  const count = document.createElement("span");
  count.className = "plan-week-count";
  count.textContent = planCountLabel(info ? info.target : 0, info ? info.actual : 0);
  button.appendChild(number);
  button.appendChild(count);
  const start = planDay(startKey);
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  const title = "Неделя " + (info ? info.week : isoWeek) + " · " + planLongRange(start, end);
  button.title = title;
  button.setAttribute("aria-label", title);
  return button;
}

function renderPlanCalendar() {
  if (!planWeeksEl) return;
  planWeeksEl.textContent = "";
  setText(planMonthEl, planCalendarMonth ? planMonthTitle(planCalendarMonth) : "—");
  const month = planMonthDate(planCalendarMonth);
  if (!month) return;
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const first = new Date(year, monthIndex, 1);
  const offset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
  const rowCount = Math.max(5, Math.ceil((offset + daysInMonth) / 7));
  const base = new Date(year, monthIndex, 1 - offset);
  const todayKey = planDateKey(new Date());
  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    const row = document.createElement("div");
    row.className = "plan-week-row";
    for (let column = 0; column < 7; column += 1) {
      const date = new Date(base.getFullYear(), base.getMonth(), base.getDate() + rowIndex * 7 + column);
      const entry = planDayEntry(planDateKey(date));
      const button = document.createElement("button");
      planCalendarRow(button, entry, date, monthIndex, todayKey);
      row.appendChild(button);
    }
    const start = new Date(base.getFullYear(), base.getMonth(), base.getDate() + rowIndex * 7);
    row.appendChild(planWeekButton(planDateKey(start), planIsoWeek(start).week));
    planWeeksEl.appendChild(row);
  }
  renderPlanSelection();
}

function renderPlanTable(items) {
  if (!planTableBodyEl) return;
  planTableBodyEl.textContent = "";
  for (const item of items) {
    const target = Number(item.target) || 0;
    const actual = Number(item.actual) || 0;
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.className = "plan-name";
    const name = document.createElement("b");
    name.textContent = String(item.name || item.code || "—");
    nameCell.appendChild(name);
    const code = String(item.code || "");
    if (code && code !== name.textContent) {
      const small = document.createElement("small");
      small.textContent = code;
      nameCell.appendChild(small);
    }
    row.appendChild(nameCell);
    for (const value of [target, actual, Math.max(0, target - actual)]) {
      const cell = document.createElement("td");
      cell.className = "plan-num";
      cell.textContent = formatPlanNumber(value);
      row.appendChild(cell);
    }
    const percent = planItemPercent(item);
    const percentCell = document.createElement("td");
    percentCell.className = "plan-percent";
    const track = document.createElement("div");
    track.className = "plan-mini";
    const bar = document.createElement("i");
    bar.style.width = percent + "%";
    track.appendChild(bar);
    const label = document.createElement("span");
    label.textContent = percent + " %";
    percentCell.appendChild(track);
    percentCell.appendChild(label);
    row.appendChild(percentCell);
    planTableBodyEl.appendChild(row);
  }
  if (planTableEl) planTableEl.hidden = items.length === 0;
  if (planEmptyEl) planEmptyEl.hidden = items.length > 0;
  setText(document.getElementById("plan-parts-badge"), items.length);
}

function renderPlanSelection() {
  if (!planSelectionTitleEl) return;
  let title = "—";
  let target = 0;
  let actual = 0;
  let items = [];
  if (planCalendar && planSelection) {
    if (planSelection.kind === "week") {
      const info = planWeekInfo(planSelection.start);
      if (info) {
        title = "Неделя " + info.week + " · " + planLongRange(info.start, info.end);
        target = info.target;
        actual = info.actual;
        items = info.items;
      }
    } else {
      const date = planDay(planSelection.date);
      if (date) title = planDayLong(date);
      const entry = planDayEntry(planSelection.date);
      if (entry) {
        target = Number(entry.target) || 0;
        actual = Number(entry.actual) || 0;
        items = Array.isArray(entry.items) ? entry.items.filter(Boolean) : [];
      }
    }
  }
  setText(planSelectionTitleEl, title);
  setText(
    planSelectionSummaryEl,
    "План " + formatPlanNumber(target) + " · Факт " + formatPlanNumber(actual) +
      " · Остаток " + formatPlanNumber(Math.max(0, target - actual))
  );
  renderPlanTable(items);
}

function renderPlan() {
  let badgeText = "неделя —";
  if (planData && planData.week) {
    const week = planData.week;
    badgeText = "неделя " + formatPlanNumber(week.parts_done) + "/" + formatPlanNumber(week.parts_target);
  }
  if (planBadgeEl) setText(planBadgeEl, badgeText);
  if (planButtonEl) {
    const label = "План выпуска · " + badgeText;
    planButtonEl.title = label;
    planButtonEl.setAttribute("aria-label", label);
  }
}

async function loadPlan() {
  try {
    const data = await requestJSON("/api/plan", { timeout: 6000 });
    if (!data || typeof data !== "object") return;
    planData = data;
    renderPlan();
  } catch {}
}

async function loadPlanMonth(key, force) {
  if (!key) return;
  planCalendarMonth = key;
  const cached = force ? null : planCalendarCache.get(key);
  if (cached) {
    planCalendar = cached;
    renderPlanCalendar();
    return;
  }
  const token = planCalendarRequest + 1;
  planCalendarRequest = token;
  setText(planStatusEl, "Загрузка…");
  try {
    const data = await requestJSON("/api/plan/calendar?month=" + encodeURIComponent(key), { timeout: 6000 });
    if (token !== planCalendarRequest) return;
    if (!data || typeof data !== "object") throw new Error("Пустой ответ");
    planCalendarCache.set(key, data);
    if (planCalendarMonth !== key) return;
    planCalendar = data;
    renderPlanCalendar();
  } catch (error) {
    if (token !== planCalendarRequest) return;
    if (planCalendarMonth === key) {
      planCalendar = planCalendarCache.get(key) || null;
      renderPlanCalendar();
    }
    toast(errorText(error, "Не удалось загрузить план месяца."), "error");
  } finally {
    if (token === planCalendarRequest) setText(planStatusEl, "");
  }
}

function planApplyMonth(key, keepSelection) {
  planCalendarMonth = key;
  if (!keepSelection) planSelection = planDefaultSelection(key);
  planCalendar = planCalendarCache.get(key) || null;
  renderPlanCalendar();
  loadPlanMonth(key);
}

function planGoMonth(offset) {
  const current = planMonthDate(planCalendarMonth) || planMonthDate(planMonthKey(new Date()));
  if (!current) return;
  const next = new Date(current.getFullYear(), current.getMonth() + offset, 1);
  planApplyMonth(planMonthKey(next), false);
}

function planGoToday() {
  const now = new Date();
  planSelection = { kind: "day", date: planDateKey(now) };
  planApplyMonth(planMonthKey(now), true);
}

function planOpenDay(key) {
  const date = planDay(key);
  if (!date) return;
  planSelection = { kind: "day", date: key };
  const monthKey = planMonthKey(date);
  if (monthKey !== planCalendarMonth) {
    planApplyMonth(monthKey, true);
  } else {
    renderPlanCalendar();
  }
}

function planOpenWeek(startKey) {
  if (!planDay(startKey)) return;
  planSelection = { kind: "week", start: startKey };
  renderPlanCalendar();
}

async function refreshPlanMonth() {
  const key = planCalendarMonth || planMonthKey(new Date());
  planCalendarCache.delete(key);
  planCalendar = null;
  renderPlanCalendar();
  await loadPlanMonth(key, true);
}

function openPlanModal() {
  if (!modalPlan) return;
  setText(planStatusEl, "");
  if (!planCalendarMonth) planCalendarMonth = planMonthKey(new Date());
  if (!planSelection || planSelectionMonthKey() !== planCalendarMonth) {
    planSelection = planDefaultSelection(planCalendarMonth);
  }
  planCalendar = planCalendarCache.get(planCalendarMonth) || null;
  renderPlanCalendar();
  openModal(modalPlan);
  loadPlan();
  loadPlanMonth(planCalendarMonth);
}

function activePrinting() {
  return state.printers.filter(function (view) {
    return view && (view.state === "printing" || view.state === "paused");
  });
}

function viewParts(view) {
  const parts = Array.isArray(view.parts) ? view.parts : [];
  return parts.filter(function (part) {
    return part && part.name;
  });
}

function partsSummary(parts) {
  return parts
    .map(function (part) {
      return String(part.name) + " × " + (Number(part.count) || 0);
    })
    .join(", ");
}

function partsCount(parts) {
  return parts.reduce(function (sum, part) {
    return sum + (Number(part.count) || 0);
  }, 0);
}

function syncPrintingCount(count) {
  setText(printingCountEl, count);
  if (printingButtonEl) {
    const label = "Сейчас печатается: " + count;
    printingButtonEl.title = label;
    printingButtonEl.setAttribute("aria-label", label);
  }
}

function printingGroups(active) {
  const groups = new Map();
  for (const view of active) {
    const printerName = view.name || view.key || "";
    for (const part of viewParts(view)) {
      const name = String(part.name);
      if (!groups.has(name)) groups.set(name, { name: name, count: 0, printers: [] });
      const group = groups.get(name);
      group.count += Number(part.count) || 0;
      if (printerName && group.printers.indexOf(printerName) === -1) group.printers.push(printerName);
    }
  }
  return Array.from(groups.values()).sort(function (a, b) {
    if (b.count !== a.count) return b.count - a.count;
    return a.name.localeCompare(b.name, "ru");
  });
}

function printingCell(row, text, className) {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = text;
  row.appendChild(cell);
  return cell;
}

function renderPrintingModal(active) {
  if (!printingSummaryEl || !printingPartsBodyEl || !printingPrintersBodyEl) return;
  const empty = active.length === 0;
  let total = 0;
  for (const view of active) total += partsCount(viewParts(view));
  const summary = empty
    ? ""
    : "В работе " + active.length + " " + plural(active.length, "принтер", "принтера", "принтеров") +
      " · " + total + " " + plural(total, "деталь", "детали", "деталей");
  setText(printingSummaryEl, summary);
  printingSummaryEl.hidden = empty;
  if (printingEmptyEl) printingEmptyEl.hidden = !empty;

  const groups = printingGroups(active);
  printingPartsBodyEl.textContent = "";
  for (const group of groups) {
    const row = document.createElement("tr");
    printingCell(row, group.name, "printing-name");
    printingCell(row, String(group.count), "printing-num");
    const printers = printingCell(row, group.printers.join(", "), "printing-printers");
    printers.title = printers.textContent;
    printingPartsBodyEl.appendChild(row);
  }
  if (printingPartsSection) printingPartsSection.hidden = empty || groups.length === 0;

  printingPrintersBodyEl.textContent = "";
  for (const view of active) {
    const parts = viewParts(view);
    const row = document.createElement("tr");
    printingCell(row, view.name || view.key || "—", "printing-name");
    const status = view.state === "paused" ? "paused" : "printing";
    const stateCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "pill " + status;
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = STATE_LABELS[status] || status;
    pill.appendChild(dot);
    pill.appendChild(label);
    stateCell.appendChild(pill);
    row.appendChild(stateCell);
    const file = printingCell(row, view.file || "—", "printing-file");
    file.title = file.textContent;
    const position = printingCell(row, parts.length ? partsSummary(parts) : "—", "printing-parts");
    position.title = position.textContent;
    printingCell(row, parts.length ? String(partsCount(parts)) : "—", "printing-num");
    printingPrintersBodyEl.appendChild(row);
  }
  if (printingPrintersSection) printingPrintersSection.hidden = empty;
}

function renderPrinting() {
  const active = activePrinting();
  syncPrintingCount(active.length);
  if (!modalPrinting || modalPrinting.hidden) return;
  renderPrintingModal(active);
}

function openPrintingModal() {
  if (!modalPrinting) return;
  openModal(modalPrinting);
  renderPrintingModal(activePrinting());
}

function planFilePaths(data) {
  const lists = [];
  if (Array.isArray(data)) lists.push(data);
  if (data && typeof data === "object") {
    for (const name of ["paths", "files", "selected", "entries", "items"]) {
      if (Array.isArray(data[name])) lists.push(data[name]);
    }
    if (typeof data.path === "string" && /\.(xlsx|json)$/i.test(data.path)) lists.push([data.path]);
  }
  const seen = new Set();
  const paths = [];
  for (const list of lists) {
    for (const item of list) {
      let path = "";
      if (typeof item === "string") path = item;
      else if (item && typeof item === "object" && typeof item.path === "string") path = item.path;
      if (!path || seen.has(path) || !/\.(xlsx|json)$/i.test(path)) continue;
      seen.add(path);
      paths.push(path);
    }
  }
  return paths;
}

async function importPlan() {
  if (!planImportEl || planBusy || browseBusy) return;
  planBusy = true;
  planImportEl.disabled = true;
  setText(planStatusEl, "Импорт…");
  try {
    const picked = await requestJSON("/api/fs/browse", {
      method: "POST",
      body: { initial_dir: state.lastDir || "", kind: "plan" },
      timeout: BROWSE_TIMEOUT_MS,
    });
    const dir = picked && typeof picked.dir === "string" ? picked.dir : "";
    if (dir) state.lastDir = dir;
    const paths = planFilePaths(picked);
    if (!paths.length) {
      toast("Файл плана не выбран.", "warn");
      setText(planStatusEl, "");
      return;
    }
    const result = await requestJSON("/api/plan/import", {
      method: "POST",
      body: { path: paths[0] },
      timeout: BROWSE_TIMEOUT_MS,
    });
    const days = Number(result && result.days) || 0;
    const items = Array.isArray(result && result.items) ? result.items.length : Number(result && result.imported) || 0;
    const from = planDay(result && (result.start_date || result.week_start));
    const to = planDay(result && (result.end_date || result.start_date || result.week_start));
    const span = from && to ? planShortDate(from) + " — " + planShortDate(to) + ": " : "";
    toast("Импортировано " + span + "дней " + days + ", деталей " + items + ".");
    setText(planStatusEl, "Импортировано");
    await loadPlan();
    await refreshPlanMonth();
  } catch (error) {
    setText(planStatusEl, "Ошибка импорта");
    toast(errorText(error, "Не удалось импортировать план."), "error");
  } finally {
    planBusy = false;
    planImportEl.disabled = false;
  }
}

async function heartbeat() {
  try {
    await requestJSON("/api/heartbeat", { method: "POST", body: {}, timeout: 4000 });
  } catch {}
}

function handleKeyboard(event) {
  const target = event.target;
  const typing =
    target instanceof Element &&
    Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  if (event.key === "Escape") {
    if (modalStack.length) {
      event.preventDefault();
      closeTopModal();
      return;
    }
    if (hasOpenCardMenu()) {
      event.preventDefault();
      closeCardMenus();
      return;
    }
    if (journalPanel && !journalPanel.hidden) {
      event.preventDefault();
      setJournalOpen(false);
    }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    if (!modalStack.length) sendFiles();
    return;
  }
  if (typing || modalStack.length) return;
  if (event.key === "/" || event.code === "Slash") {
    event.preventDefault();
    if (searchEl) searchEl.focus();
    return;
  }
  if (event.code === "KeyC" && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    applyCompact(!state.compact, true);
    return;
  }
  if (event.code === "KeyJ" && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    setJournalOpen(journalPanel ? !journalPanel.hidden : true);
  }
}

function bindCards() {
  if (!cardsEl) return;
  cardsEl.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const card = target.closest(".card");
    const key = card ? card.dataset.key : "";
    if (!key) return;
    const actionEl = target.closest("[data-action]");
    if (actionEl) {
      const action = actionEl.dataset.action;
      if (action === "menu") {
        event.preventDefault();
        toggleCardMenu(key);
        return;
      }
      if (action === "pause") {
        printAction(key, "pause");
        return;
      }
      if (action === "resume") {
        printAction(key, "resume");
        return;
      }
      if (action === "ui") {
        closeCardMenus();
        if (actionEl.getAttribute("aria-disabled") === "true") event.preventDefault();
        return;
      }
      if (action === "camera") {
        const record = cards.get(key);
        closeCardMenus();
        if (record && record.view && record.view.host) {
          window.open(cameraUrl(record.view), "_blank", "noopener,noreferrer");
        }
        return;
      }
      if (action === "rename") {
        closeCardMenus();
        openRenameModal("single", key);
        return;
      }
      if (action === "history") {
        closeCardMenus();
        openHistoryModal(key);
        return;
      }
      if (action === "cancel-print") {
        const record = cards.get(key);
        const name = record && record.view ? record.view.name || record.view.key : key;
        closeCardMenus();
        confirmDialog("Отменить печать на " + name + "? Текущий файл будет остановлен.").then(function (ok) {
          if (ok) printAction(key, "cancel");
        });
        return;
      }
    }
    const link = target.closest('a[data-field="mainsail"], a[data-field="camera"]');
    if (link && link.getAttribute("aria-disabled") === "true") event.preventDefault();
  });
}

function bindDocument() {
  document.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const closer = target.closest("[data-close]");
    if (closer) {
      const modal = closer.closest(".modal");
      if (modal === modalConfirm) finishConfirm(false);
      else if (modal) closeModal(modal);
      return;
    }
    if (target.classList.contains("modal")) {
      if (target === modalConfirm) finishConfirm(false);
      else closeModal(target);
      return;
    }
    const emptyAction = target.closest('#cards-empty [data-action="open-printers"]');
    if (emptyAction) {
      openPrintersModal();
      return;
    }
    if (!target.closest('[data-action="menu"]') && !target.closest(".card-menu")) closeCardMenus();
  });
}

function init() {
  templateEl = document.getElementById("card-template");
  cardsEl = document.getElementById("cards");
  summaryEl = document.getElementById("summary");
  searchEl = document.getElementById("search");
  filtersEl = document.getElementById("filters");
  toastsEl = document.getElementById("toasts");
  connEl = document.getElementById("conn");
  connTextEl = document.getElementById("conn-text");
  printerCountEl = document.getElementById("printer-count");
  refreshSecondsEl = document.getElementById("refresh-seconds");
  sendEstimateEl = document.getElementById("send-estimate");
  cancelJobEl = document.getElementById("cancel-job");
  filesCountEl = document.getElementById("files-count");
  filesNamesEl = document.getElementById("files-names");
  modalFiles = document.getElementById("modal-files");
  modalPrinters = document.getElementById("modal-printers");
  modalScan = document.getElementById("modal-scan");
  scanSubnetEl = document.getElementById("scan-subnet");
  scanPortEl = document.getElementById("scan-port");
  scanUiPortEl = document.getElementById("scan-ui-port");
  scanRunEl = document.getElementById("scan-run");
  scanStatusEl = document.getElementById("scan-status");
  scanResultsEl = document.getElementById("scan-results");
  scanAddEl = document.getElementById("scan-add");
  modalError = document.getElementById("modal-error");
  modalConfirm = document.getElementById("modal-confirm");
  modalRename = document.getElementById("modal-rename");
  renameTitleEl = document.getElementById("modal-rename-title");
  renameHintEl = document.getElementById("rename-hint");
  renameInputEl = document.getElementById("rename-input");
  renamePreviewEl = document.getElementById("rename-preview");
  renameConfirmEl = document.getElementById("rename-confirm");
  fsBrowseEl = document.getElementById("fs-browse");
  confirmTextEl = document.getElementById("confirm-text");
  errorTitleEl = document.getElementById("modal-error-title");
  errorLabelEl = document.getElementById("error-label");
  errorCodeEl = document.getElementById("error-code");
  errorDetailEl = document.getElementById("error-detail");
  errorHintEl = document.getElementById("error-hint");
  errorWikiEl = document.getElementById("error-wiki");
  fsListEl = document.getElementById("fs-list");
  fsCrumbsEl = document.getElementById("fs-crumbs");
  fsUpEl = document.getElementById("fs-up");
  fsPreviewImage = document.getElementById("fs-preview-image");
  fsPreviewEmpty = document.getElementById("fs-preview-empty");
  fsPreviewNameEl = document.getElementById("fs-preview-name");
  fsSelectedCountEl = document.getElementById("fs-selected-count");
  fsConfirmEl = document.getElementById("fs-confirm");
  printerListEl = document.getElementById("printer-list");
  printerForm = document.getElementById("printer-form");
  printerKeyEl = document.getElementById("printer-key");
  printerNameEl = document.getElementById("printer-name");
  printerHostEl = document.getElementById("printer-host");
  printerPortEl = document.getElementById("printer-port");
  printerMainsailEl = document.getElementById("printer-mainsail-port");
  printerDeleteEl = document.getElementById("printer-delete");
  printerResetEl = document.getElementById("printer-reset");
  printerSubmitEl = printerForm ? printerForm.querySelector('button[type="submit"]') : null;
  journalPanel = document.getElementById("journal-panel");
  journalEl = document.getElementById("journal");
  journalCountEl = document.getElementById("journal-count");
  journalToggleEl = document.getElementById("journal-toggle");
  journalCloseEl = document.getElementById("journal-close");
  modalHistory = document.getElementById("modal-history");
  historyTitleEl = document.getElementById("history-title");
  historyPillEl = document.getElementById("history-state-pill");
  historyStateLabelEl = document.getElementById("history-state-label");
  historyLastEl = document.getElementById("history-last");
  historyTimelineEl = document.getElementById("history-timeline");
  historyEventsEl = document.getElementById("history-events");
  historySummaryEl = document.getElementById("history-summary");
  historyRangesEl = document.getElementById("history-ranges");
  modalPlan = document.getElementById("modal-plan");
  planButtonEl = document.getElementById("plan");
  planBadgeEl = document.getElementById("plan-badge");
  planStatusEl = document.getElementById("plan-status");
  planTableEl = document.getElementById("plan-table");
  planTableBodyEl = document.getElementById("plan-table-body");
  planEmptyEl = document.getElementById("plan-empty");
  planImportEl = document.getElementById("plan-import");
  planPrevEl = document.getElementById("plan-prev");
  planNextEl = document.getElementById("plan-next");
  planTodayEl = document.getElementById("plan-today");
  planMonthEl = document.getElementById("plan-month");
  planWeeksEl = document.getElementById("plan-weeks");
  planSelectionEl = document.getElementById("plan-selection");
  planSelectionTitleEl = document.getElementById("plan-selection-title");
  planSelectionSummaryEl = document.getElementById("plan-selection-summary");
  modalPrinting = document.getElementById("modal-printing");
  printingButtonEl = document.getElementById("printing");
  printingCountEl = document.getElementById("printing-count");
  printingSummaryEl = document.getElementById("printing-summary");
  printingPartsSection = document.getElementById("printing-parts-section");
  printingPartsBodyEl = document.getElementById("printing-parts-body");
  printingPrintersSection = document.getElementById("printing-printers-section");
  printingPrintersBodyEl = document.getElementById("printing-printers-body");
  printingEmptyEl = document.getElementById("printing-empty");

  if (searchEl) {
    searchEl.addEventListener("input", function () {
      state.query = searchEl.value || "";
      applyFilters();
    });
  }
  bindFilterClicks();
  bindSummaryClicks();
  bindCards();
  bindDocument();

  const compactButton = document.getElementById("compact-toggle");
  if (compactButton) {
    compactButton.addEventListener("click", function () {
      applyCompact(!state.compact, true);
    });
  }
  const refreshButton = document.getElementById("refresh");
  if (refreshButton) {
    refreshButton.addEventListener("click", function () {
      fetchState();
    });
  }
  const selectOnlineButton = document.getElementById("select-online");
  if (selectOnlineButton) {
    selectOnlineButton.addEventListener("click", function () {
      for (const view of state.printers) {
        if (view && view.key && view.state !== "offline") state.selected.add(view.key);
      }
      updateSelectionUI();
    });
  }
  const clearSelectionButton = document.getElementById("clear-selection");
  if (clearSelectionButton) {
    clearSelectionButton.addEventListener("click", function () {
      state.selected.clear();
      updateSelectionUI();
    });
  }
  const selectVisibleButton = document.getElementById("select-visible");
  if (selectVisibleButton) {
    selectVisibleButton.addEventListener("click", function () {
      for (const record of cards.values()) {
        if (matches(record.view)) state.selected.add(record.view.key);
      }
      updateSelectionUI();
    });
  }
  const bulkPauseButton = document.getElementById("bulk-pause");
  if (bulkPauseButton) {
    bulkPauseButton.addEventListener("click", function () {
      bulkPrintAction("pause");
    });
  }
  const bulkResumeButton = document.getElementById("bulk-resume");
  if (bulkResumeButton) {
    bulkResumeButton.addEventListener("click", function () {
      bulkPrintAction("resume");
    });
  }
  const bulkCancelButton = document.getElementById("bulk-cancel");
  if (bulkCancelButton) {
    bulkCancelButton.addEventListener("click", function () {
      bulkPrintAction("cancel");
    });
  }
  const bulkRenameButton = document.getElementById("bulk-rename");
  if (bulkRenameButton) {
    bulkRenameButton.addEventListener("click", function () {
      if (!state.selected.size) return;
      openRenameModal("bulk");
    });
  }
  const groupButton = document.getElementById("group-toggle");
  if (groupButton) {
    groupButton.addEventListener("click", function () {
      applyGrouped(!state.grouped, true);
    });
  }
  if (renameInputEl) {
    renameInputEl.addEventListener("input", renderRenamePreview);
    renameInputEl.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      confirmRename();
    });
  }
  if (renameConfirmEl) renameConfirmEl.addEventListener("click", confirmRename);
  if (fsBrowseEl) fsBrowseEl.addEventListener("click", browseFiles);
  const sendButton = document.getElementById("send");
  if (sendButton) sendButton.addEventListener("click", sendFiles);
  if (cancelJobEl) cancelJobEl.addEventListener("click", cancelJob);
  const openFilesButton = document.getElementById("open-files");
  if (openFilesButton) openFilesButton.addEventListener("click", openFilesModal);
  const openPrintersButton = document.getElementById("open-printers");
  if (openPrintersButton) openPrintersButton.addEventListener("click", openPrintersModal);
  const scanNetworkButton = document.getElementById("scan-network");
  if (scanNetworkButton) scanNetworkButton.addEventListener("click", openScanModal);
  if (scanRunEl) scanRunEl.addEventListener("click", runScan);
  if (scanAddEl) scanAddEl.addEventListener("click", importScanResults);
  if (scanSubnetEl) {
    scanSubnetEl.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      runScan();
    });
  }

  if (fsUpEl) {
    fsUpEl.addEventListener("click", function () {
      loadFs(fsParent === null || fsParent === undefined ? "" : fsParent);
    });
  }
  if (fsCrumbsEl) {
    fsCrumbsEl.addEventListener("click", function (event) {
      const target = event.target instanceof Element ? event.target : null;
      const button = target ? target.closest("button[data-crumb]") : null;
      if (button) loadFs(button.dataset.crumb || "");
    });
  }
  if (fsListEl) {
    fsListEl.addEventListener("click", function (event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const nav = target.closest("button[data-nav]");
      if (nav) {
        loadFs(nav.dataset.nav || "");
        return;
      }
      const open = target.closest("button[data-open]");
      if (open) {
        const path = open.dataset.open || "";
        fsSetSelected(path, !fsSelected.has(path));
      }
    });
  }
  if (fsConfirmEl) fsConfirmEl.addEventListener("click", confirmFiles);

  if (printerForm) printerForm.addEventListener("submit", savePrinter);
  if (printerResetEl) printerResetEl.addEventListener("click", resetPrinterForm);
  if (printerDeleteEl) printerDeleteEl.addEventListener("click", deletePrinter);
  if (printerListEl) {
    printerListEl.addEventListener("click", function (event) {
      const target = event.target instanceof Element ? event.target : null;
      const button = target ? target.closest("button[data-key]") : null;
      if (!button) return;
      const printer = printerItems.find(function (item) {
        return item.key === button.dataset.key;
      });
      if (printer) fillPrinterForm(printer);
    });
  }

  if (journalToggleEl) {
    journalToggleEl.addEventListener("click", function () {
      setJournalOpen(journalPanel ? journalPanel.hidden : true);
    });
  }
  if (journalCloseEl) journalCloseEl.addEventListener("click", function () {
    setJournalOpen(false);
  });

  if (planButtonEl) planButtonEl.addEventListener("click", openPlanModal);
  if (printingButtonEl) printingButtonEl.addEventListener("click", openPrintingModal);
  if (planImportEl) planImportEl.addEventListener("click", importPlan);
  if (planPrevEl) planPrevEl.addEventListener("click", function () {
    planGoMonth(-1);
  });
  if (planNextEl) planNextEl.addEventListener("click", function () {
    planGoMonth(1);
  });
  if (planTodayEl) planTodayEl.addEventListener("click", planGoToday);
  if (planWeeksEl) {
    planWeeksEl.addEventListener("click", function (event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const week = target.closest("[data-week-start]");
      if (week) {
        planOpenWeek(week.dataset.weekStart || "");
        return;
      }
      const day = target.closest("[data-date]");
      if (day) planOpenDay(day.dataset.date || "");
    });
  }
  if (historyRangesEl) {
    historyRangesEl.addEventListener("click", function (event) {
      const target = event.target instanceof Element ? event.target : null;
      const button = target ? target.closest("[data-range]") : null;
      if (!button) return;
      const range = button.dataset.range || "24h";
      if (!HISTORY_RANGES[range] || range === historyRange) return;
      historyRange = range;
      syncHistoryRange();
      loadHistory();
    });
  }

  const confirmOk = document.getElementById("confirm-ok");
  if (confirmOk) confirmOk.addEventListener("click", function () {
    finishConfirm(true);
  });

  document.addEventListener("keydown", handleKeyboard);

  restoreCompact();
  restoreGrouped();
  renderFilesStrip();
  fetchState();
  loadJournal();
  loadPlan();
  window.setInterval(loadPlan, PLAN_POLL_MS);
  heartbeat();
  window.setInterval(heartbeat, HEARTBEAT_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}




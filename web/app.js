"use strict";

const POLL_IDLE_MS = 2000;
const POLL_JOB_MS = 1000;
const FETCH_TIMEOUT_MS = 5000;
const TOAST_MS = 5000;
const COMPACT_KEY = "farm.compact";
const STATE_LABELS = {
  printing: "Печатает",
  paused: "Пауза",
  idle: "Свободен",
  error: "Ошибка",
  offline: "Не в сети",
};
const SUMMARY_KEYS = ["printing", "paused", "idle", "error", "offline"];
const FILTER_KEYS = ["all", "printing", "paused", "idle", "error", "offline"];

const state = {
  printers: [],
  summary: {},
  job: {},
  selected: new Set(),
  files: [],
  filter: "all",
  query: "",
  compact: false,
};

const cards = new Map();
let templateEl = null;
let cardsEl = null;
let summaryEl = null;
let searchEl = null;
let filtersEl = null;
let toastsEl = null;
let connEl = null;
let connTextEl = null;
let printerCountEl = null;
let lastErrorToast = 0;
let pollTimer = 0;
let inFlight = false;

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

function formatEta(status, seconds) {
  if (status === "paused") return "на паузе";
  if (status !== "printing") return "—";
  const number = Number(seconds);
  if (!Number.isFinite(number) || number <= 0) return "—";
  return formatDuration(number);
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

function lastSeenText(timestamp) {
  const number = Number(timestamp);
  if (!Number.isFinite(number) || number <= 0) return "нет связи";
  return "последний ответ " + formatAgo(number);
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
    lastErrorToast = 0;
    setConnection(true);
    render();
  } catch (error) {
    setConnection(false);
    notifyServerError();
  } finally {
    window.clearTimeout(timer);
    inFlight = false;
    const delay = state.job && state.job.running ? POLL_JOB_MS : POLL_IDLE_MS;
    pollTimer = window.setTimeout(fetchState, delay);
  }
}

function render() {
  renderSummary();
  renderCards();
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

function thumbEmptyMessage(status) {
  if (status === "offline") return "Не в сети";
  if (status === "error") return "Ошибка";
  if (status === "idle") return "Готов к загрузке";
  return "Превью нет";
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
  };
  if (!refs.stateLabel) refs.stateLabel = refs.pill;
  const record = {
    el: el,
    refs: refs,
    view: view,
    thumbVersion: null,
    tagText: refs.lastSeen ? refs.lastSeen.textContent : "",
  };
  if (refs.select) {
    refs.select.addEventListener("change", function () {
      toggleSelect(view.key, refs.select.checked);
    });
  }
  if (refs.error) {
    refs.error.addEventListener("click", function () {
      if (typeof openErrorModal === "function") {
        openErrorModal(cards.has(view.key) ? cards.get(view.key).view : view);
      }
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
  const percent = Math.round(clamp(view.progress, 0, 1) * 100);
  if (refs.progressTrack) {
    refs.progressTrack.classList.toggle("paused", status === "paused");
    refs.progressTrack.classList.toggle("error", status === "error");
  }
  if (refs.progressBar) refs.progressBar.style.width = percent + "%";
  setText(refs.progressPct, percent + "%");
  setText(refs.eta, formatEta(status, view.eta_seconds));
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
    sendButton.disabled = state.selected.size === 0 || Boolean(state.job && state.job.running);
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
    state.filter = FILTER_KEYS.indexOf(key) === -1 ? "all" : key;
    applyFilters();
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

  if (searchEl) {
    searchEl.addEventListener("input", function () {
      state.query = searchEl.value || "";
      applyFilters();
    });
  }
  bindFilterClicks();

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
  restoreCompact();
  fetchState();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

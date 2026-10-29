"use strict";

const MODES = { cool: "קירור", heat: "חימום", fan: "מאוורר", dry: "ייבוש" };
const MODE_ICONS = { cool: "❄️", heat: "☀️", fan: "🌀", dry: "💧" };
const FANS = { auto: "אוטו", mute: "שקט", low: "נמוך", med: "בינוני", max: "גבוה" };
const DAYS = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"];

const S = { devices: [], rules: [], settings: {}, cities: [], shabbat: null, tab: "devices" };
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------- API
function storedPin() {
  try { return localStorage.getItem("ac_pin") || ""; } catch (e) { return ""; }
}

async function api(method, path, body) {
  const res = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-PIN": storedPin() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    const pin = prompt("קוד גישה:");
    if (pin !== null) {
      try { localStorage.setItem("ac_pin", pin); } catch (e) { /* ignore */ }
      return api(method, path, body);
    }
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "שגיאה " + res.status);
  return data;
}

let toastTimer;
function toast(msg, isError) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 5000 : 2200);
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" });
}
function fmtDateTime(iso) {
  const d = new Date(iso);
  return DAYS[d.getDay()] + " " + d.toLocaleDateString("he-IL", { day: "numeric", month: "numeric" }) + " " + fmtTime(iso);
}

// ---------------------------------------------------------------- tabs
function showTab(tab) {
  S.tab = tab;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + tab));
  if (tab === "rules") loadRules();
  if (tab === "shabbat") { loadRules(); loadShabbat(); }
  if (tab === "settings") { loadSettings(); loadLog(); renderDeviceAdmin(); }
  try { localStorage.setItem("ac_tab", tab); } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- devices
async function loadDevices() {
  try {
    S.devices = await api("GET", "devices");
    renderDevices();
  } catch (e) { toast(e.message, true); }
}

function renderDevices() {
  const box = $("#devices");
  $("#no-devices").hidden = S.devices.length > 0;
  // Keep cards that are being edited (pending temp change) untouched.
  box.innerHTML = S.devices.map(deviceCard).join("");
}

function deviceCard(d) {
  const st = d.state || {};
  const on = !!st.power;
  const pending = pendingTemp[d.id];
  const temp = pending ?? st.target_temp ?? "--";
  const online = d.online;
  const statusText = online === false ? "לא מגיב" : online ? (on ? MODES[st.mode] || "פועל" : "כבוי") : "בודק…";
  const dot = online === false ? "err" : on ? "on" : "";
  const chip = (group, val, label, cls = "") =>
    `<button class="chip ${cls} ${st[group] === val ? "sel" : ""}" data-act="${group}" data-val="${val}">${label}</button>`;
  const toggle = (key, label) =>
    `<button class="chip ${st[key] ? "sel" : ""}" data-act="${key}" data-val="${!st[key]}">${label}</button>`;
  return `
  <article class="card ${on ? "" : "off"} ${online === false ? "offline" : ""}" data-id="${d.id}">
    <div class="card-head">
      <div>
        <div class="name">${esc(d.name)}</div>
        <div class="meta"><span class="dot ${dot}"></span>${statusText}${st.room_temp != null ? " · בחדר " + st.room_temp + "°" : ""}</div>
      </div>
      <button class="power ${on ? "on" : ""}" data-act="power" data-val="${!on}" aria-label="הפעלה/כיבוי">⏻</button>
    </div>
    <div class="temp-row">
      <button data-act="temp-down" aria-label="הורדה">−</button>
      <div class="temp-value">${temp}<small>°C</small></div>
      <button data-act="temp-up" aria-label="העלאה">+</button>
    </div>
    <div class="group"><div class="chips">
      ${Object.entries(MODES).map(([k, v]) => chip("mode", k, MODE_ICONS[k] + " " + v, k)).join("")}
    </div></div>
    <div class="group"><div class="group-label">מאוורר</div><div class="chips">
      ${Object.entries(FANS).map(([k, v]) => chip("fan", k, v)).join("")}
    </div></div>
    <div class="group"><div class="group-label">אפשרויות</div><div class="chips">
      ${toggle("turbo", "טורבו")}${toggle("eco", "חסכון")}${toggle("swing_v", "נדנוד ↕")}${toggle("swing_h", "נדנוד ↔")}
      ${toggle("display", "תצוגה")}
      <button class="chip ${st.sleep ? "sel" : ""}" data-act="sleep" data-val="${st.sleep ? 0 : 1}">שינה</button>
    </div></div>
    <div class="group"><div class="group-label">כיבוי אוטומטי בעוד</div><div class="chips">
      ${[30, 60, 120, 180].map((m) => `<button class="chip" data-act="offin" data-val="${m}">${m < 60 ? m + " דק׳" : m / 60 + " שע׳"}</button>`).join("")}
    </div></div>
    ${d.error && online === false ? `<div class="err-text">${esc(d.error)}</div>` : ""}
  </article>`;
}

const pendingTemp = {};
const tempTimers = {};

async function control(id, action) {
  const card = $(`.card[data-id="${id}"]`);
  card && card.classList.add("busy");
  try {
    const st = await api("POST", `devices/${id}/control`, action);
    const d = S.devices.find((x) => x.id === id);
    Object.assign(d, st);
  } catch (e) {
    toast(e.message, true);
  } finally {
    renderDevices();
  }
}

function onDeviceClick(ev) {
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const id = btn.closest(".card").dataset.id;
  const d = S.devices.find((x) => x.id === id);
  const act = btn.dataset.act;
  const val = btn.dataset.val;
  if (act === "power") return control(id, val === "true" ? { power: true } : { power: false });
  if (act === "mode") return control(id, { power: true, mode: val });
  if (act === "fan") return control(id, { fan: val });
  if (act === "sleep") return control(id, { sleep: Number(val) });
  if (["turbo", "eco", "swing_v", "swing_h", "display"].includes(act)) return control(id, { [act]: val === "true" });
  if (act === "temp-up" || act === "temp-down") {
    const base = pendingTemp[id] ?? d.state?.target_temp ?? 24;
    const next = Math.max(16, Math.min(32, base + (act === "temp-up" ? 1 : -1)));
    pendingTemp[id] = next;
    $(".temp-value", btn.closest(".card")).innerHTML = next + "<small>°C</small>";
    clearTimeout(tempTimers[id]);
    tempTimers[id] = setTimeout(async () => {
      const t = pendingTemp[id];
      await control(id, { temp: t });
      delete pendingTemp[id];
      renderDevices();
    }, 700);
    return;
  }
  if (act === "offin") return offIn([id], Number(val));
}

function localIso(date) {
  const p = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
}

async function offIn(ids, minutes) {
  const at = new Date(Date.now() + minutes * 60000);
  try {
    await api("POST", "rules", {
      name: "כיבוי בעוד " + minutes + " דק׳",
      devices: ids,
      trigger: { type: "once", at: localIso(at) },
      action: { power: false },
      delete_after: true,
    });
    toast("יכבה ב-" + fmtTime(at.toISOString()));
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- rules
async function loadRules() {
  try {
    S.rules = await api("GET", "rules");
    renderRules();
  } catch (e) { toast(e.message, true); }
}

function describeTrigger(t) {
  if (t.type === "weekly") {
    const days = t.days.length === 7 ? "כל יום" : t.days.map((d) => DAYS[d]).join(" ");
    return `${t.time} · ${days}`;
  }
  if (t.type === "once") return "חד פעמי · " + fmtDateTime(t.at);
  const base = t.type === "shabbat_start" ? "כניסת שבת" : "יציאת שבת";
  if (!t.offset) return base;
  return `${Math.abs(t.offset)} דק׳ ${t.offset < 0 ? "לפני" : "אחרי"} ${base}`;
}

function describeAction(a) {
  if (a.power === false) return "כיבוי";
  const parts = ["הדלקה"];
  if (a.mode) parts.push(MODES[a.mode]);
  if (a.temp) parts.push(a.temp + "°");
  if (a.fan) parts.push("מאוורר " + FANS[a.fan]);
  return parts.join(" · ");
}

function ruleItem(r) {
  const names = r.devices.map((id) => S.devices.find((d) => d.id === id)?.name).filter(Boolean).join(", ");
  return `
  <div class="item ${r.enabled ? "" : "disabled"}" data-rule="${r.id}">
    <div>
      <div class="title">${esc(r.name)}</div>
      <div class="sub">${esc(describeTrigger(r.trigger))} → ${esc(describeAction(r.action))}</div>
      <div class="sub">${esc(names)}${r.next ? " · הבא: " + fmtDateTime(r.next) : ""}</div>
    </div>
    <div class="actions">
      <input type="checkbox" data-ract="toggle" ${r.enabled ? "checked" : ""} aria-label="פעיל">
      <button class="btn ghost small" data-ract="edit">עריכה</button>
      <button class="btn ghost small" data-ract="run">הפעל עכשיו</button>
      <button class="btn danger small" data-ract="delete">מחיקה</button>
    </div>
  </div>`;
}

function renderRules() {
  const normal = S.rules.filter((r) => !r.shabbat);
  const shabbat = S.rules.filter((r) => r.shabbat).sort((a, b) => (a.next || "").localeCompare(b.next || ""));
  $("#rules").innerHTML = normal.length ? normal.map(ruleItem).join("") : `<div class="empty">אין תזמונים. לחצו "תזמון חדש".</div>`;
  $("#shabbat-rules").innerHTML = shabbat.length ? shabbat.map(ruleItem).join("") : `<div class="empty">אין תוכנית שבת. לחצו "יצירת תוכנית".</div>`;
}

async function onRuleClick(ev) {
  const el = ev.target.closest("[data-ract]");
  if (!el) return;
  const id = el.closest("[data-rule]").dataset.rule;
  const rule = S.rules.find((r) => r.id === id);
  const act = el.dataset.ract;
  try {
    if (act === "toggle") {
      await api("PUT", "rules/" + id, { ...rule, enabled: el.checked });
    } else if (act === "edit") {
      return openRuleDialog(rule);
    } else if (act === "run") {
      await api("POST", `rules/${id}/run`);
      toast("בוצע");
      loadDevices();
    } else if (act === "delete") {
      if (!confirm(`למחוק את "${rule.name}"?`)) return;
      await api("DELETE", "rules/" + id);
    }
    loadRules();
  } catch (e) { toast(e.message, true); }
}

function deviceChips(container, selected) {
  container.innerHTML = S.devices.map((d) =>
    `<label class="chip"><input type="checkbox" value="${d.id}" ${selected.includes(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")
    || `<span class="hint">אין מזגנים מוגדרים</span>`;
}

let editingRule = null;
function openRuleDialog(rule, preset) {
  editingRule = rule || null;
  const f = $("#rule-form");
  f.reset();
  const r = rule || preset || {};
  $("#rule-title").textContent = rule ? "עריכת תזמון" : "תזמון חדש";
  f.name.value = r.name || "";
  deviceChips($("#rule-devices"), r.devices || S.devices.map((d) => d.id));
  const t = r.trigger || { type: "weekly", time: "07:00", days: [0, 1, 2, 3, 4] };
  f.type.value = t.type;
  f.time.value = t.time || "07:00";
  f.offset.value = t.offset || 0;
  f.at.value = t.at || localIso(new Date(Date.now() + 3600000));
  const days = t.days || [0, 1, 2, 3, 4];
  $("#rule-days").innerHTML = DAYS.map((n, i) =>
    `<label class="chip"><input type="checkbox" value="${i}" ${days.includes(i) ? "checked" : ""}> ${n}</label>`).join("");
  const a = r.action || { power: true };
  f.power.value = a.power === false ? "off" : "on";
  f.mode.value = a.mode || "";
  f.temp.value = a.temp || "";
  f.fan.value = a.fan || "";
  f.shabbat.checked = !!r.shabbat;
  syncRuleForm();
  $("#rule-dialog").showModal();
}

function syncRuleForm() {
  const f = $("#rule-form");
  const type = f.type.value;
  $$("[data-when]", f).forEach((el) => {
    const w = el.dataset.when;
    el.hidden = !(w === type || (w === "shabbat" && type.startsWith("shabbat")));
  });
  $("[data-power]", f).hidden = f.power.value !== "on";
}

async function saveRule(ev) {
  const f = $("#rule-form");
  if (ev.submitter?.value !== "save") return;
  ev.preventDefault();
  const type = f.type.value;
  const trigger = { type };
  if (type === "weekly") {
    trigger.time = f.time.value;
    trigger.days = $$("#rule-days input:checked").map((i) => Number(i.value));
  } else if (type === "once") {
    trigger.at = f.at.value;
  } else {
    trigger.offset = Number(f.offset.value || 0);
  }
  const action = { power: f.power.value === "on" };
  if (action.power) {
    if (f.mode.value) action.mode = f.mode.value;
    if (f.temp.value) action.temp = Number(f.temp.value);
    if (f.fan.value) action.fan = f.fan.value;
  }
  const body = {
    name: f.name.value,
    devices: $$("#rule-devices input:checked").map((i) => i.value),
    trigger, action,
    shabbat: f.shabbat.checked,
    enabled: editingRule ? editingRule.enabled : true,
    delete_after: false,
  };
  try {
    if (editingRule) await api("PUT", "rules/" + editingRule.id, body);
    else await api("POST", "rules", body);
    $("#rule-dialog").close();
    toast("נשמר");
    loadRules();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- shabbat
async function loadShabbat() {
  try {
    S.shabbat = await api("GET", "shabbat");
    const sh = S.shabbat;
    $("#shabbat-times").innerHTML = `
      <div class="times">
        <div><span class="lbl">כניסת שבת · ${new Date(sh.start).toLocaleDateString("he-IL", { day: "numeric", month: "numeric" })}</span><b>${fmtTime(sh.start)}</b></div>
        <div><span class="lbl">יציאת שבת</span><b>${fmtTime(sh.end)}</b></div>
      </div>
      <div class="status-line">${sh.active ? "🕯️ שבת עכשיו" + (sh.enforce ? " · התוכנית נשמרת" : "") : "לפי " + esc(S.settings.city || "ההגדרות")}</div>`;
    $("#enforce").checked = sh.enforce;
    updateBadge();
  } catch (e) { toast(e.message, true); }
}

function updateBadge() {
  const b = $("#shabbat-badge");
  const sh = S.shabbat;
  b.hidden = !(sh && sh.active);
  if (sh && sh.active) b.textContent = "🕯️ שבת · יוצאת " + fmtTime(sh.end);
}

const WIZ_KINDS = {
  shabbat_start: "כניסת שבת ±דק׳",
  fri: "שישי בשעה",
  sat: "שבת בשעה",
  shabbat_end: "יציאת שבת ±דק׳",
};
const DEFAULT_STEPS = [
  { kind: "shabbat_start", value: -30, power: "on" },
  { kind: "sat", value: "02:00", power: "off" },
  { kind: "sat", value: "11:00", power: "on" },
  { kind: "sat", value: "15:00", power: "off" },
  { kind: "sat", value: "16:00", power: "on" },
  { kind: "shabbat_end", value: 10, power: "off" },
];

function stepRow(s) {
  const isTime = s.kind === "fri" || s.kind === "sat";
  return `<div class="step">
    <label class="kind">מתי<select data-f="kind">${Object.entries(WIZ_KINDS).map(([k, v]) => `<option value="${k}" ${k === s.kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <button type="button" class="btn danger small" data-f="del" aria-label="הסרה">✕</button>
    <label>${isTime ? "שעה" : "דקות"}<input data-f="value" type="${isTime ? "time" : "number"}" value="${esc(s.value)}"></label>
    <label>פעולה<select data-f="power"><option value="on" ${s.power === "on" ? "selected" : ""}>הדלקה</option><option value="off" ${s.power === "off" ? "selected" : ""}>כיבוי</option></select></label>
  </div>`;
}

function readSteps() {
  return $$("#wizard-steps .step").map((row) => ({
    kind: $("[data-f=kind]", row).value,
    value: $("[data-f=value]", row).value,
    power: $("[data-f=power]", row).value,
  }));
}

function renderSteps(steps) {
  $("#wizard-steps").innerHTML = steps.map(stepRow).join("");
}

function openWizard() {
  deviceChips($("#wizard-devices"), S.devices.map((d) => d.id));
  renderSteps(DEFAULT_STEPS);
  $("#wizard-dialog").showModal();
}

async function saveWizard(ev) {
  if (ev.submitter?.value !== "save") return;
  ev.preventDefault();
  const f = $("#wizard-form");
  const devices = $$("#wizard-devices input:checked").map((i) => i.value);
  const on = { power: true, mode: f.mode.value, temp: Number(f.temp.value), fan: f.fan.value };
  const steps = readSteps();
  try {
    for (const s of steps) {
      const trigger = s.kind === "fri" || s.kind === "sat"
        ? { type: "weekly", time: s.value || "00:00", days: [s.kind === "fri" ? 5 : 6] }
        : { type: s.kind, offset: Number(s.value || 0) };
      await api("POST", "rules", {
        name: `שבת: ${s.power === "on" ? "הדלקה" : "כיבוי"}`,
        devices, trigger,
        action: s.power === "on" ? on : { power: false },
        shabbat: true,
      });
    }
    $("#wizard-dialog").close();
    toast("תוכנית השבת נוצרה");
    loadRules();
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- settings
async function loadSettings() {
  try {
    const r = await api("GET", "settings");
    S.settings = r.settings;
    S.cities = r.cities;
    const f = $("#settings-form");
    $("#city").innerHTML = S.cities.map((c) => `<option ${c === S.settings.city ? "selected" : ""}>${esc(c)}</option>`).join("");
    for (const k of ["candle_minutes", "havdalah_mode", "havdalah_minutes", "enforce_interval_min", "poll_interval_sec"]) {
      f[k].value = S.settings[k];
    }
  } catch (e) { toast(e.message, true); }
}

async function saveSettings(ev) {
  ev.preventDefault();
  const f = ev.target;
  const body = { city: f.city.value, havdalah_mode: f.havdalah_mode.value };
  for (const k of ["candle_minutes", "havdalah_minutes", "enforce_interval_min", "poll_interval_sec"]) body[k] = Number(f[k].value);
  if (body.city !== S.settings.city) delete body.candle_minutes; // take the city's custom
  try {
    const r = await api("PUT", "settings", body);
    S.settings = r.settings;
    loadSettings();
    toast("נשמר");
  } catch (e) { toast(e.message, true); }
}

function renderDeviceAdmin() {
  $("#device-admin").innerHTML = S.devices.map((d) => `
    <div class="item" data-dev="${d.id}">
      <div>
        <div class="title">${esc(d.name)}</div>
        <div class="sub">${esc(d.host)}${d.port !== 8888 ? ":" + d.port : ""} · ${d.online ? "מחובר" : d.online === false ? "לא מגיב" : "?"}</div>
      </div>
      <div class="actions">
        <button class="btn ghost small" data-dact="rename">שינוי שם</button>
        <button class="btn ghost small" data-dact="ip">שינוי IP</button>
        <button class="btn danger small" data-dact="delete">הסרה</button>
      </div>
    </div>`).join("");
}

async function onDeviceAdmin(ev) {
  const el = ev.target.closest("[data-dact]");
  if (!el) return;
  const id = el.closest("[data-dev]").dataset.dev;
  const d = S.devices.find((x) => x.id === id);
  try {
    if (el.dataset.dact === "rename") {
      const name = prompt("שם חדש:", d.name);
      if (!name) return;
      await api("PUT", "devices/" + id, { name });
    } else if (el.dataset.dact === "ip") {
      const host = prompt("כתובת IP:", d.host);
      if (!host) return;
      await api("PUT", "devices/" + id, { host });
    } else {
      if (!confirm(`להסיר את "${d.name}"? גם התזמונים שלו יעודכנו.`)) return;
      await api("DELETE", "devices/" + id);
    }
    await loadDevices();
    renderDeviceAdmin();
  } catch (e) { toast(e.message, true); }
}

async function scan() {
  const btn = $("#scan");
  const out = $("#scan-result");
  btn.disabled = true;
  btn.textContent = "מחפש… (עד חצי דקה)";
  out.innerHTML = "";
  try {
    const r = await api("POST", "scan", {});
    if (!r.found.length) {
      out.innerHTML = `<p class="hint">לא נמצאו מזגנים ברשת ${esc(r.networks.join(", "))}.
        ודאו שהמזגנים מחוברים לרשת ה-Wi-Fi של הבית (ולא משדרים רשת משלהם), ושהשרת מחובר לאותה רשת.</p>`;
    } else {
      out.innerHTML = r.found.map((f) => `
        <div class="item">
          <div><div class="title">${esc(f.host)}</div><div class="sub">גרסת מודול ${esc(f.version)}</div></div>
          <div class="actions">${f.known ? `<span class="sub">כבר קיים</span>`
            : `<button class="btn primary small" data-add="${esc(f.host)}" data-port="${f.port}">הוספה</button>`}</div>
        </div>`).join("");
    }
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
  btn.textContent = "🔍 חיפוש מזגנים ברשת";
}

async function addDevice(host, port, name) {
  try {
    await api("POST", "devices", { host, port, name: name || "מזגן " + (S.devices.length + 1) });
    toast("נוסף");
    await loadDevices();
    renderDeviceAdmin();
  } catch (e) { toast(e.message, true); }
}

async function loadLog() {
  try {
    const log = await api("GET", "log");
    $("#log").innerHTML = log.map((l) =>
      `<div class="${l.level}"><time>${new Date(l.ts * 1000).toLocaleString("he-IL", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</time>${esc(l.msg)}</div>`).join("")
      || `<span class="hint">ריק</span>`;
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- init
function init() {
  $(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]");
    if (g) showTab(g.dataset.goto);
    const add = e.target.closest("[data-add]");
    if (add) {
      const name = prompt("איך לקרוא למזגן? (למשל סלון)", "") || "";
      addDevice(add.dataset.add, Number(add.dataset.port), name);
      add.closest(".item").remove();
    }
  });
  $("#devices").addEventListener("click", onDeviceClick);
  $("#rules").addEventListener("click", onRuleClick);
  $("#shabbat-rules").addEventListener("click", onRuleClick);
  $("#device-admin").addEventListener("click", onDeviceAdmin);
  $("[data-bulk=off]").addEventListener("click", async () => {
    try {
      await api("POST", "control", { action: { power: false } });
      toast("כל המזגנים כובו");
      loadDevices();
    } catch (e) { toast(e.message, true); }
  });
  $("#refresh-all").addEventListener("click", async () => {
    await Promise.all(S.devices.map((d) => api("POST", `devices/${d.id}/refresh`).then((st) => Object.assign(d, st)).catch(() => {})));
    renderDevices();
  });
  $("#new-rule").addEventListener("click", () => openRuleDialog(null));
  $("#rule-form").addEventListener("change", syncRuleForm);
  $("#rule-form").addEventListener("submit", saveRule);
  $("#shabbat-wizard").addEventListener("click", openWizard);
  $("#wizard-form").addEventListener("submit", saveWizard);
  $("#wizard-add").addEventListener("click", () => renderSteps([...readSteps(), { kind: "sat", value: "12:00", power: "on" }]));
  $("#wizard-steps").addEventListener("click", (e) => {
    if (e.target.closest("[data-f=del]")) { e.target.closest(".step").remove(); }
  });
  $("#wizard-steps").addEventListener("change", (e) => {
    if (e.target.matches("[data-f=kind]")) {
      const steps = readSteps();
      const i = $$("#wizard-steps .step").indexOf(e.target.closest(".step"));
      const timeKind = steps[i].kind === "fri" || steps[i].kind === "sat";
      steps[i].value = timeKind ? "12:00" : 0;
      renderSteps(steps);
    }
  });
  $("#enforce").addEventListener("change", async (e) => {
    try { await api("PUT", "settings", { shabbat_enforce: e.target.checked }); loadShabbat(); } catch (err) { toast(err.message, true); }
  });
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#scan").addEventListener("click", scan);
  $("#add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    addDevice(e.target.host.value.trim(), 8888, e.target.name.value.trim());
    e.target.reset();
  });

  let tab = "devices";
  try { tab = localStorage.getItem("ac_tab") || "devices"; } catch (e) { /* ignore */ }
  loadSettings();
  loadDevices().then(() => showTab(tab));
  api("GET", "shabbat").then((sh) => { S.shabbat = sh; updateBadge(); }).catch(() => {});

  setInterval(() => {
    if (document.hidden) return;
    if (S.tab === "devices" && !Object.keys(pendingTemp).length) loadDevices();
    if (S.tab === "settings") loadLog();
  }, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadDevices(); });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

init();

"use strict";
/* The administrator's screen.
 *
 * All the scheduling thinking is on the server: this file draws grids, sends
 * one edit at a time and shows what came back.  It never decides whether a
 * placement is legal — it asks — because the answer depends on the whole
 * week, and a browser that guessed would eventually guess differently from
 * the engine that has to live with the result.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const S = {
  me: null,          // signed-in user, or null
  auth: true,        // whether this server uses accounts at all
  mode: "login",     // "login" | "register"
  id: null,          // current school
  state: null,       // last /api/school/<id> payload
  page: "overview",
  view: "class",     // "class" | "teacher"
  selected: null,    // row uid
  alternatives: null,
  open: new Set(),   // which entity panels are expanded
  filter: "",
  schools: [],
};

/* ------------------------------------------------------------- plumbing */

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    // Only same-origin script can set a header, so its presence is what
    // tells the server this request came from its own page.
    headers: { "X-Timetable": "1", ...(options.headers || {}) },
  });
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    showAuth();
    throw new Error(data.error || "נדרשת התחברות");
  }
  if (!res.ok) throw new Error(data.error || `שגיאה ${res.status}`);
  return data;
}

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

function toast(text, kind = "") {
  const node = document.createElement("div");
  node.className = kind;
  node.textContent = text;
  $("#toast").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 9000 : 4500);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

const note = (kind, title, body) =>
  el("div", { class: `note ${kind}` }, title ? el("b", {}, title) : null,
     body || "");

const empty = (title, body, ...extra) =>
  el("div", { class: "empty" }, el("b", {}, title), body, ...extra);

const stat = (value, label, tone = "") =>
  el("div", { class: "stat " + tone }, el("b", {}, value),
     el("span", {}, label));

/* Icons, as path data.  Inline rather than a font or a sprite file: six
 * shapes are not worth a network request, and they inherit currentColor. */
const ICONS = {
  overview: "M4 13h7V4H4v9Zm0 7h7v-5H4v5Zm9 0h7v-9h-7v9Zm0-16v5h7V4h-7Z",
  data: "M12 3v12m0-12 4 4m-4-4-4 4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
  constraints: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M16 4v4M10 10v4"
    + "M18 16v4",
  timetable: "M4 5h16v15H4zM4 10h16M9 5v15M14 5v15M8 3v3M16 3v3",
  quality: "M12 3l8 4v6c0 4-3.4 6.9-8 8-4.6-1.1-8-4-8-8V7l8-4Zm-3 9 2.2 2.2"
    + "L15.5 10",
  export: "M12 16V4m0 12 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2"
    + "v-2",
  account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 8a8 8 0 0 1 16 0",
  logout: "M15 12H4m11 0-3-3m3 3-3 3M9 4h9a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H9",
  sun: "M12 5V3m0 18v-2m7-7h2M3 12h2m11.5-5.5 1.4-1.4M6.1 17.9l-1.4 1.4"
    + "m12.8 0 1.4 1.4M6.1 6.1 4.7 4.7M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z",
  moon: "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z",
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || "");
  svg.append(path);
  return svg;
}

/* A colour per subject, so a subject's spread across the week is visible at
 * a glance.  Hues come from the golden angle over the school's own subject
 * list rather than from hashing the name: that keeps neighbouring subjects
 * far apart on the wheel instead of merely different, and it is stable. */
function subjectHue(subject) {
  const list = S.state?.subjects || [];
  const i = list.indexOf(subject);
  const n = i >= 0 ? i : [...subject].reduce((a, c) => a + c.charCodeAt(0), 0);
  return Math.round((n * 137.508) % 360);
}

/* ================================================================ auth == */

function showAuth() {
  $("#auth").classList.remove("hidden");
  $("#app").classList.add("hidden");
  setAuthMode(S.mode);
}

function setAuthMode(mode) {
  S.mode = mode;
  const registering = mode === "register";
  $("#auth-title").textContent = registering ? "פתיחת חשבון" : "כניסה לחשבון";
  $("#auth-sub").textContent = registering
    ? "החשבון נשמר על המחשב הזה בלבד. אין אימות דוא\"ל ואין שחזור סיסמה."
    : "כדי לשמור את מערכות השעות שלך.";
  $("#auth-submit").textContent = registering ? "פתיחת חשבון" : "כניסה";
  $("#auth-display-row").classList.toggle("hidden", !registering);
  $("#auth-switch-text").textContent = registering
    ? "כבר יש לך חשבון?" : "אין לך עדיין חשבון?";
  $("#auth-switch").textContent = registering ? "כניסה" : "הרשמה";
  $("#auth-form").password.setAttribute(
    "autocomplete", registering ? "new-password" : "current-password");
  $("#auth-error").textContent = "";
}

$("#auth-switch").addEventListener("click",
  () => setAuthMode(S.mode === "register" ? "login" : "register"));

$("#auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target;
  const body = {
    username: form.username.value,
    password: form.password.value,
    display: form.display ? form.display.value : "",
  };
  const box = $("#auth-error");
  box.textContent = "";
  const submit = $("#auth-submit");
  submit.disabled = true;
  try {
    const { user } = await post(`/api/auth/${S.mode}`, body);
    S.me = user;
    form.reset();
    await enterApp();
  } catch (err) {
    box.append(note("bad", "", err.message));
  } finally {
    submit.disabled = false;
  }
});

$("#logout").addEventListener("click", async () => {
  await post("/api/auth/logout");
  S.me = null; S.id = null; S.state = null; S.schools = [];
  showAuth();
});

/* =============================================================== theme == */

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme) root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
  const dark = theme === "dark" || (!theme
    && matchMedia("(prefers-color-scheme: dark)").matches);
  const btn = $("#theme");
  btn.textContent = "";
  btn.append(icon(dark ? "sun" : "moon"));
  btn.title = dark ? "מעבר למצב בהיר" : "מעבר למצב כהה";
  btn.setAttribute("aria-label", btn.title);
}

$("#theme").addEventListener("click", () => {
  const dark = document.documentElement.getAttribute("data-theme") === "dark"
    || (!document.documentElement.hasAttribute("data-theme")
        && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  try { localStorage.setItem("tt-theme", next); } catch { /* private mode */ }
  applyTheme(next);
});

/* ========================================================== navigation == */

const PAGES = [
  { id: "overview", icon: "overview", title: "סקירה",
    sub: "מצב בית הספר במבט אחד." },
  { id: "schools", icon: "data", title: "נתוני בית הספר",
    sub: "העלאת קבצים, וכל בתי הספר שנשמרו בחשבון שלך." },
  { id: "constraints", icon: "constraints", title: "אילוצים",
    sub: "כל כלל שמנוע השיבוץ מכיר — מה הוא עושה, והאם הוא פעיל." },
  { id: "timetable", icon: "timetable", title: "מערכת השעות",
    sub: "לפי כיתה או לפי מורה. גרירה מזיזה שיעור; כל שינוי נבדק מיד." },
  { id: "quality", icon: "quality", title: "קונפליקטים ואיכות",
    sub: "מה מופר, מה נדחה ובכמה, ואיך העומס מתחלק." },
  { id: "export", icon: "export", title: "ייצוא",
    sub: "Word לעריכה, Word סופי להפצה, וגיבוי מלא." },
  { id: "account", icon: "account", title: "החשבון",
    sub: "שם משתמש, סיסמה ומקום שמירת הנתונים." },
];

function renderNav() {
  const nav = $("#nav");
  nav.textContent = "";
  const rv = S.state?.review;
  for (const p of PAGES) {
    if (p.id === "account" && !S.auth) continue;
    let badge = null;
    if (p.id === "quality" && rv) {
      const n = rv.violations.length + rv.unplaced.length;
      badge = el("span", { class: "badge" + (n ? "" : " ok") },
                 n ? n : "✓");
    }
    nav.append(el("button", {
      class: p.id === S.page ? "on" : "",
      onclick: () => go(p.id),
    }, icon(p.icon), p.title, badge));
  }
}

function go(page) {
  S.page = page;
  const meta = PAGES.find(p => p.id === page) || PAGES[0];
  $("#page-title").textContent = meta.title;
  $("#page-sub").textContent = meta.sub;
  $$(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + page));
  renderNav();
  renderPageActions();
  if (page === "overview") renderOverview();
  if (page === "constraints") loadConstraints();
  if (page === "timetable") renderTimetable();
  if (page === "quality") renderQuality();
  if (page === "export") renderExport();
  if (page === "account") renderAccount();
  if (page === "schools") loadSchools();
}

function renderPageActions() {
  const host = $("#page-actions");
  host.textContent = "";
  if (!S.id) return;
  if (S.page === "timetable" || S.page === "overview") {
    if (S.state?.rows) {
      host.append(
        el("button", { class: "ghost", onclick: undo }, "ביטול פעולה"),
        el("button", { class: "ghost", onclick: showVersions }, "גרסאות"),
        el("button", { onclick: approve }, "אישור המערכת"));
    }
    host.append(el("button", { class: "primary", onclick: toggleBuild },
                   "בניית מערכת"));
  }
  if (S.page === "timetable" && S.state?.rows) {
    host.append(el("button", { class: "ghost", onclick: () => window.print() },
                   "הדפסה"));
  }
}

/* ========================================================= the switcher = */

$("#switcher-btn").addEventListener("click", e => {
  e.stopPropagation();
  $("#switcher-menu").classList.toggle("hidden");
  renderSwitcherMenu();
});
document.addEventListener("click",
  () => $("#switcher-menu").classList.add("hidden"));

function renderSwitcher() {
  const s = S.state?.school;
  $("#switcher-name").textContent = s ? s.name : "לא נבחר בית ספר";
  $("#switcher-meta").textContent = s
    ? `${s.teachers} מורות · ${s.classes} כיתות${s.year ? " · " + s.year : ""}`
    : `${S.schools.length} בתי ספר בחשבון`;
}

function renderSwitcherMenu() {
  const menu = $("#switcher-menu");
  menu.textContent = "";
  for (const s of S.schools) {
    menu.append(el("button", {
      class: s.id === S.id ? "on" : "",
      onclick: () => { $("#switcher-menu").classList.add("hidden"); open(s.id); },
    }, el("span", { class: "grow" }, el("b", {}, s.name),
         el("small", {}, s.has_timetable ? `מערכת קיימת · ${s.status || "—"}`
                                         : "ללא מערכת"))));
  }
  if (S.schools.length) menu.append(el("hr"));
  menu.append(el("button", {
    onclick: () => { $("#switcher-menu").classList.add("hidden"); go("schools"); },
  }, "+ בית ספר חדש"));
}

/* ============================================================== schools = */

async function loadSchools() {
  const { schools } = await api("/api/schools");
  S.schools = schools;
  renderSwitcher();
  const list = $("#school-list");
  list.textContent = "";
  if (!schools.length) {
    list.append(empty("עדיין לא נטען בית ספר",
      "אפשר להעלות קבצים למעלה, או ללחוץ על 'בית ספר לדוגמה' כדי לראות "
      + "את המערכת עובדת מקצה לקצה."));
    return;
  }
  for (const s of schools) {
    list.append(el("div", { class: "card" + (s.id === S.id ? " on" : "") },
      el("div", { class: "grow" },
        el("b", {}, s.name + (s.year ? ` · ${s.year}` : "")),
        el("small", {}, s.error ||
          `${s.teachers} מורות · ${s.classes} כיתות · ${s.hours} שעות · ` +
          (s.has_timetable ? `מערכת קיימת (${s.status || "—"})`
                           : "ללא מערכת") + ` · עודכן ${s.updated}`)),
      el("button", { onclick: () => open(s.id) }, "פתיחה"),
      el("button", {
        class: "danger",
        onclick: async () => {
          if (!confirm(`למחוק את ${s.name}? הפעולה אינה הפיכה.`)) return;
          await api(`/api/school/${encodeURIComponent(s.id)}`,
                    { method: "DELETE", headers: { "X-Timetable": "1" } });
          if (S.id === s.id) { S.id = null; S.state = null; }
          renderSwitcher(); loadSchools(); renderNav();
        },
      }, "מחיקה")));
  }
}

async function open(id) {
  S.id = id;
  S.selected = null;
  S.alternatives = null;
  await refresh();
  go(S.state.rows ? "overview" : "constraints");
}

async function refresh() {
  if (!S.id) return;
  S.state = await api(`/api/school/${encodeURIComponent(S.id)}`);
  renderSwitcher();
  renderScore();
  renderNav();
  renderPageActions();
  if (S.page === "timetable") renderTimetable();
  if (S.page === "overview") renderOverview();
  const { schools } = await api("/api/schools");
  S.schools = schools;
}

/* ============================================================= overview = */

/* The first screen a person sees, and the one that has to answer "what is
 * this and what do I do now" without being read twice.  So: one sentence of
 * state, one obvious button, and the four steps of the job with the current
 * one marked.  Everything that is a *number* rather than a decision moved to
 * the pages that are about numbers. */

function overviewState() {
  const rv = S.state?.review;
  if (!rv) {
    return {
      tone: "", chip: "idle", label: "עוד אין מערכת שעות",
      line: "העלינו את הנתונים. השלב הבא הוא לבנות מערכת שעות — "
          + "המערכת תעשה זאת לבד, ותסביר כל דבר שלא הצליחה לקיים.",
      action: { text: "בניית מערכת שעות", run: toggleBuild, primary: true },
      step: 2,
    };
  }
  const problems = rv.violations.length + rv.unplaced.length;
  const rejected = rv.penalty_detail.length;
  if (problems) {
    return {
      tone: "bad", chip: "bad",
      label: problems === 1 ? "בעיה אחת שצריך לתקן"
                            : `${problems} בעיות שצריך לתקן`,
      line: "יש שיעורים שמפרים כלל שאסור להפר — למשל מורה בשתי כיתות באותה "
          + "שעה. הרשימה מפרטת כל אחד מהם ומה בדיוק לא בסדר.",
      action: { text: "לרשימת הבעיות", run: () => go("quality"),
                primary: true },
      step: 3,
    };
  }
  if (rejected) {
    return {
      tone: "", chip: "ok", label: "המערכת חוקית",
      line: `כל הכללים שאסור להפר מתקיימים. ${rejected} כללים שביקשתם לא `
          + "התקיימו במלואם — אפשר לחיות איתם, או לפתוח את הרשימה ולראות "
          + "מה כל אחד מהם עלה.",
      action: { text: "פתיחת המערכת", run: () => go("timetable"),
                primary: true },
      step: 3,
    };
  }
  return {
    tone: "", chip: "ok", label: "הכול מתקיים",
    line: "כל הכללים מתקיימים, גם אלה שאסור להפר וגם אלה שביקשתם. "
        + "אפשר לייצא ולהפיץ.",
    action: { text: "ייצוא ל-Word", run: () => go("export"), primary: true },
    step: 4,
  };
}

function renderSteps(current) {
  const steps = [
    { n: 1, title: "נתונים", note: "מורות, כיתות ושיעורים", page: "schools" },
    { n: 2, title: "בנייה", note: "המערכת בונה את הלוח", run: toggleBuild },
    { n: 3, title: "בדיקה ותיקון", note: "מה לא הסתדר, ומה אפשר לעשות",
      page: "timetable" },
    { n: 4, title: "ייצוא", note: "Word לעריכה או להפצה", page: "export" },
  ];
  return el("div", { class: "steps" }, steps.map(st => el("button", {
    class: "step " + (st.n < current ? "done"
                    : st.n === current ? "now" : "later"),
    onclick: st.run || (() => go(st.page)),
  }, el("span", { class: "num" }, st.n < current ? "✓" : st.n),
     el("span", { class: "grow" }, el("b", {}, st.title),
        el("small", {}, st.note)))));
}

function renderOverview() {
  const host = $("#overview");
  host.textContent = "";
  if (!S.id) {
    host.append(empty("נתחיל מהנתונים",
      "כדי לבנות מערכת שעות צריך קובץ אחד עם המורות, הכיתות והשיעורים. "
      + "אפשר גם פשוט לטעון בית ספר לדוגמה ולראות איך זה עובד.",
      el("div", { class: "row", style: "justify-content:center" },
        el("button", { class: "primary", onclick: () => go("schools") },
           "העלאת נתונים"))));
    return;
  }
  const s = S.state.school;
  const rv = S.state.review;
  const st = overviewState();

  host.append(el("div", { class: "hero " + st.tone },
    el("div", { class: "grow" },
      el("span", { class: "status " + st.chip }, st.label),
      el("h2", { style: "margin-top:9px" }, s.name),
      el("p", { class: "lead" }, st.line)),
    el("div", { class: "row" },
      el("button", { class: st.action.primary ? "primary" : "",
                     onclick: st.action.run }, st.action.text),
      rv && st.step !== 2
        ? el("button", { onclick: toggleBuild }, "בנייה מחדש") : null)));

  host.append(renderSteps(st.step));

  const tiles = [
    stat(s.teachers, "מורות"),
    stat(s.classes, "כיתות"),
    stat(s.hours, "שעות בשבוע"),
  ];
  if (rv) {
    const problems = rv.violations.length + rv.unplaced.length;
    tiles.push(problems
      ? stat(problems, "בעיות לתיקון", "bad")
      : stat(rv.penalty_detail.length, "כללים שלא התקיימו במלואם",
             rv.penalty_detail.length ? "" : "ok"));
  }
  host.append(el("div", { class: "stats" }, tiles));

  host.append(el("div", { class: "panel" },
    el("b", {}, "רוצה לכוונן את הכללים?"),
    el("p", { class: "lead" },
      "המערכת מגיעה עם כללים מוכנים — מורה לא בשתי כיתות באותה שעה, יום "
      + "הכיתה ברצף, חלון אחד לכל מורה, וכל מה שהעליתם על המורות. אפשר "
      + "לכבות כלל, להדליק אותו, או להחליט מה חשוב יותר ממה."),
    el("button", { onclick: () => go("constraints") },
       "מסך האילוצים")));
}


/* ============================================================ importing = */

const form = $("#import-form");
const fileInput = $('#import-form input[type=file]');
fileInput.addEventListener("change", () => {
  $("#file-list").textContent =
    [...fileInput.files].map(f => f.name).join(" · ")
    || "Word · Excel · CSV · גיבוי JSON";
});
["dragover", "dragenter"].forEach(ev =>
  $("#drop").addEventListener(ev, e => {
    e.preventDefault(); $("#drop").classList.add("over");
  }));
["dragleave", "drop"].forEach(ev =>
  $("#drop").addEventListener(ev, () => $("#drop").classList.remove("over")));
$("#drop").addEventListener("drop", e => {
  e.preventDefault();
  fileInput.files = e.dataTransfer.files;
  fileInput.dispatchEvent(new Event("change"));
});

form.addEventListener("submit", async e => {
  e.preventDefault();
  if (!fileInput.files.length) { toast("לא נבחרו קבצים", "bad"); return; }
  const body = new FormData();
  body.append("name", form.name.value);
  body.append("year", form.year.value);
  for (const f of fileInput.files) body.append("files", f, f.name);
  const out = $("#import-report");
  out.textContent = "";
  out.append(note("", "", "קורא את הקבצים…"));
  try {
    const data = await api("/api/import", { method: "POST", body });
    renderImport(data);
    if (data.ok) { await open(data.id); go("schools"); }
  } catch (err) {
    out.textContent = "";
    out.append(note("bad", "הייבוא נכשל", err.message));
  }
});

$("#load-demo").addEventListener("click", async () => {
  const data = await post("/api/demo");
  renderImport({ ok: true, checks: data.checks });
  await open(data.id);
  go("schools");
  toast("נטען בית ספר לדוגמה", "ok");
});

function renderImport(data) {
  const out = $("#import-report");
  out.textContent = "";
  const r = data.report;
  if (r) {
    for (const e of r.errors) out.append(note("bad", "", e));
    for (const w of r.warnings) out.append(note("warn", "", w));
    for (const n of r.notes) out.append(note("", "", n));
  }
  if (data.ok) out.append(note("ok", "הקבצים נקראו בהצלחה", ""));
  if (data.checks) out.append(renderChecks(data.checks));
}

function renderChecks(checks) {
  const box = el("div", {});
  const kinds = { ERROR: "bad", TIGHT: "warn", INFO: "" };
  box.append(el("h3", { style: "margin-top:14px" },
    `בדיקות היתכנות: ${checks.errors} שגיאות, ${checks.tight} נקודות מתוחות`));
  if (checks.errors) {
    box.append(note("bad", "המערכת אינה ניתנת לפתרון כפי שהיא",
      "כל שגיאה למטה הוכחה בספירה, ולכן היא ודאית. יש לתקן את הנתונים "
      + "או להרפות אילוץ במסך האילוצים."));
  }
  for (const f of checks.findings) {
    box.append(note(kinds[f.level], `[${f.topic}]`, f.message));
  }
  return box;
}

/* ========================================================== constraints = */

let constraintData = null;

async function loadConstraints() {
  const host = $("#constraint-groups");
  if (!S.id) {
    host.textContent = "";
    host.append(empty("לא נבחר בית ספר",
      "רשימת האילוצים נגזרת מהנתונים של בית ספר מסוים — מי המחנכות, "
      + "אילו מורות ביקשו מה — ולכן צריך לבחור אחד."));
    return;
  }
  constraintData = await api(
    `/api/school/${encodeURIComponent(S.id)}/constraints`);
  renderConstraints();
}

$("#expand-all-c").addEventListener("click",
  () => $$("#constraint-groups details").forEach(d => d.open = true));
$("#collapse-all-c").addEventListener("click",
  () => $$("#constraint-groups details").forEach(d => d.open = false));
$("#only-active").addEventListener("change", renderConstraints);

function renderConstraints() {
  const host = $("#constraint-groups");
  host.textContent = "";
  if (!constraintData) return;
  const onlyActive = $("#only-active").checked;
  for (const group of constraintData.groups) {
    const items = group.constraints.filter(
      c => !onlyActive || c.applies || !c.scope.length);
    if (!items.length) continue;
    const body = el("div", {});
    for (const c of items) body.append(renderRule(c));
    host.append(el("details", { class: "group", open: true },
      el("summary", {}, group.title,
        el("span", { class: "pill" }, `${items.length} כללים`)),
      body));
  }
}

function renderRule(c) {
  const controls = [];
  if (c.locked) {
    controls.push(el("span", { class: "pill" }, "קבוע"));
  } else {
    controls.push(el("label", { class: "switch" },
      el("input", {
        type: "checkbox", checked: c.enabled,
        onchange: e => setRule(c.id, { enabled: e.target.checked }),
      }), c.enabled ? "פעיל" : "כבוי"));
    if (c.kind === "soft") {
      controls.push(el("select", {
        style: "width:auto",
        onchange: e => setRule(c.id, { weight: Number(e.target.value) }),
      }, constraintData.priorities.filter(p => p.weight > 0).map(
        p => el("option", { value: p.weight, selected: p.weight === c.weight },
                `עדיפות: ${p.name}`))));
    }
  }
  return el("div", { class: "rule" + (c.enabled ? "" : " disabled") },
    el("div", { class: "head" },
      el("b", {}, c.title),
      el("span", { class: "pill " + (c.kind === "hard" ? "hard" : "soft") },
        c.kind === "hard" ? "קשיח" : "מתומחר"),
      !c.enabled && !c.locked ? el("span", { class: "pill off" }, "כבוי") : null,
      ...controls),
    el("p", { class: "why" }, c.explanation),
    c.scope.length
      ? el("div", { class: "scope" }, el("b", {}, "חל על: "),
          c.scope.slice(0, 12).join(" · ")
          + (c.scope.length > 12 ? ` ועוד ${c.scope.length - 12}` : ""))
      : null);
}

async function setRule(id, change) {
  try {
    constraintData = await post(
      `/api/school/${encodeURIComponent(S.id)}/constraints`,
      { constraint: id, ...change });
    renderConstraints();
    if (constraintData.review) {
      S.state.review = constraintData.review;
      renderScore(); renderNav();
    }
    toast("האילוץ עודכן. בנייה מחדש תשתמש בהגדרה החדשה.", "ok");
  } catch (err) { toast(err.message, "bad"); }
}

/* ============================================================ timetable = */

$$('[data-view]').forEach(b => b.addEventListener("click", () => {
  S.view = b.dataset.view;
  $$('[data-view]').forEach(x => x.classList.toggle("on", x === b));
  S.open.clear();
  renderTimetable();
}));
$("#expand-all").addEventListener("click", () => {
  entities().forEach(n => S.open.add(n)); renderTimetable();
});
$("#collapse-all").addEventListener("click", () => {
  S.open.clear(); renderTimetable();
});
$("#filter").addEventListener("input", e => {
  S.filter = e.target.value.trim(); renderTimetable();
});

function entities() {
  if (!S.state) return [];
  return S.view === "class" ? S.state.classes : S.state.teachers;
}

function rowsOf(name) {
  return (S.state.rows || []).filter(r => S.view === "class"
    ? r.klass === name : r.teachers.includes(name));
}

function renderTimetable() {
  const host = $("#grids");
  host.textContent = "";
  if (!S.state) {
    host.append(empty("לא נבחר בית ספר",
      "יש לבחור בית ספר בבורר שבראש הסרגל."));
    renderInspector();
    return;
  }
  if (!S.state.rows) {
    host.append(empty("עדיין אין מערכת שעות",
      "לחיצה על 'בניית מערכת' מריצה את המנוע: קודם בדיקות ספירה, אחר כך "
      + "סולם הקשחה, ולבסוף אימות בלתי תלוי."));
    renderInspector();
    return;
  }
  const unplaced = (S.state.rows || []).filter(r => r.day === null);
  if (unplaced.length) host.append(renderTray(unplaced));

  const names = entities().filter(n => !S.filter || n.includes(S.filter));
  if (!names.length) {
    host.append(empty("אין התאמות לסינון",
      `לא נמצא ${S.view === "class" ? "כיתה" : "מורה"} בשם "${S.filter}".`));
  }
  for (const name of names) host.append(renderEntity(name));
  renderInspector();
}

function renderTray(unplaced) {
  return el("div", { class: "panel" },
    el("b", {}, `${unplaced.length} שיעורים אינם משובצים`),
    el("p", { class: "lead" },
      "אפשר לבחור שיעור ולבקש חלופות, או לגרור אותו לתא פנוי."),
    el("div", { class: "row" }, unplaced.map(r => el("button", {
      onclick: () => select(r.uid),
      class: r.uid === S.selected ? "primary" : "",
    }, `${r.subject} · ${r.klass || "—"}`))));
}

function renderEntity(name) {
  const rows = rowsOf(name);
  const grid = S.state.grid;
  const maxP = Math.max(...grid.periods);
  const placed = rows.filter(r => r.day !== null);
  const hours = placed.reduce((n, r) => n + r.length, 0);

  // A teacher's window: a free period with teaching on both sides of it.
  const gaps = new Set();
  if (S.view === "teacher") {
    for (let d = 0; d < grid.days.length; d++) {
      const busy = [];
      placed.filter(r => r.day === d).forEach(
        r => { for (let i = 0; i < r.length; i++) busy.push(r.period + i); });
      if (!busy.length) continue;
      for (let p = Math.min(...busy); p < Math.max(...busy); p++) {
        if (!busy.includes(p)) gaps.add(`${d}:${p}`);
      }
    }
  }

  const at = {};
  for (const r of placed) {
    for (let i = 0; i < r.length; i++) at[`${r.day}:${r.period + i}`] = [r, i];
  }

  const body = el("tbody");
  for (let p = 1; p <= maxP; p++) {
    const tr = el("tr", {}, el("th", {}, p));
    for (let d = 0; d < grid.days.length; d++) {
      if (p > grid.periods[d]) { tr.append(el("td", { class: "out" })); continue; }
      const found = at[`${d}:${p}`];
      const td = el("td", {
        dataset: { day: d, period: p },
        class: found ? "" : (gaps.has(`${d}:${p}`) ? "gap" : "free"),
      });
      dropTarget(td, d, p, name);
      if (found) td.append(cellButton(found[0], found[1]));
      else if (gaps.has(`${d}:${p}`)) td.append(el("span", { class: "t" }, "חלון"));
      tr.append(td);
    }
    body.append(tr);
  }
  const table = el("table", { class: "grid" },
    el("thead", {}, el("tr", {}, el("th", {}, "שעה"),
      grid.days.map(d => el("th", {}, d)))),
    body);

  const details = el("details", { class: "entity", open: S.open.has(name) },
    el("summary", {},
      (S.view === "class" ? "כיתה " : "") + name,
      el("span", { class: "meta" },
        `${hours} שעות` +
        (S.view === "teacher" && gaps.size ? ` · ${gaps.size} חלונות` : "") +
        (S.view === "class" && S.state.homerooms[name]
          ? ` · מחנכת: ${S.state.homerooms[name]}` : ""))),
    el("div", { class: "wrap" }, table));
  details.addEventListener("toggle", () => {
    if (details.open) S.open.add(name); else S.open.delete(name);
  });
  return details;
}

function cellButton(r, offset) {
  const label = S.view === "class"
    ? r.teachers.join("/")
    : (r.klass ? "כיתה " + r.klass : "ללא כיתה");
  const btn = el("button", {
    class: "cell" + (r.uid === S.selected ? " sel" : "")
           + (r.locked ? " locked" : "") + (offset ? " cont" : ""),
    style: `--h:${subjectHue(r.subject)}`,
    title: `${r.subject} · ${r.klass ? "כיתה " + r.klass : "ללא כיתה"} · `
           + r.teachers.join(" / ") + (r.length > 1 ? " · שיעור כפול" : ""),
    draggable: offset === 0 && !r.locked,
    onclick: () => select(r.uid),
    ondragstart: e => {
      e.dataTransfer.setData("text/plain", String(r.uid));
      e.dataTransfer.effectAllowed = "move";
      btn.classList.add("dragging");
    },
    ondragend: () => btn.classList.remove("dragging"),
  },
    el("span", { class: "s" },
       r.subject + (r.length > 1 && offset === 0 ? " ⟩⟩" : "")),
    el("span", { class: "t" }, offset === 0 ? label : "המשך"));
  return btn;
}

function dropTarget(td, day, period, entityName) {
  td.addEventListener("dragover", e => {
    e.preventDefault(); td.classList.add("target");
  });
  td.addEventListener("dragleave", () => td.classList.remove("target"));
  td.addEventListener("drop", async e => {
    e.preventDefault();
    td.classList.remove("target");
    const uid = Number(e.dataTransfer.getData("text/plain"));
    if (!uid) return;
    // Dropping onto a lesson of the same grid is an exchange; the engine is
    // told which, because an exchange keeps both lessons in the week and a
    // plain move would leave a hole in a class's day.
    const here = rowsOf(entityName).find(
      r => r.day === day && r.period <= period && period < r.period + r.length);
    if (here && here.uid === uid) return;
    if (here) await edit({ op: "swap", uid, other: here.uid });
    else await edit({ op: "move", uid, day, period });
  });
}

function select(uid) {
  S.selected = uid;
  S.alternatives = null;
  renderTimetable();
}

/* ============================================================ inspector = */

function currentRow() {
  return (S.state?.rows || []).find(r => r.uid === S.selected) || null;
}

function renderInspector() {
  const host = $("#inspector");
  host.textContent = "";
  const r = currentRow();
  if (!r) {
    host.append(empty("לא נבחר שיעור",
      "לחיצה על שיעור בלוח תפתח כאן את פרטיו, את האפשרויות לשנותו "
      + "ואת החלופות למיקומו.",
      S.state?.rows ? el("div", { class: "row",
                                  style: "justify-content:center" },
        el("button", { onclick: addLesson }, "הוספת שיעור חדש")) : null));
    return;
  }
  const days = S.state.grid.days;
  host.append(el("div", { class: "panel" },
    el("div", { class: "lesson-head", style: `--h:${subjectHue(r.subject)}` },
      el("div", { class: "swatch" }),
      el("div", { class: "grow" },
        el("b", {}, r.subject),
        el("div", { class: "lead" },
          `${r.klass ? "כיתה " + r.klass : "ללא כיתה"} · `
          + `${r.teachers.join(" / ")} · `
          + `${r.length > 1 ? "שיעור כפול" : "שיעור בודד"}`),
        el("div", { class: "lead" }, r.day === null ? "אינו משובץ כרגע"
          : `${days[r.day]}, שעה ${r.period}`))),
    el("div", { class: "row", style: "margin-top:12px" },
      el("button", { class: "primary", onclick: findAlternatives },
         "מציאת חלופות"),
      el("button", {
        onclick: () => edit({ op: "lock", uid: r.uid, locked: !r.locked }),
      }, r.locked ? "שחרור נעילה" : "נעילה במקום"),
      r.day !== null ? el("button", {
        onclick: () => edit({ op: "unplace", uid: r.uid }),
      }, "הוצאה מהלוח") : null),
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { onclick: () => editLesson(r) }, "עריכת פרטים"),
      el("button", { onclick: addLesson }, "הוספת שיעור"),
      el("button", {
        class: "danger",
        onclick: () => confirm("למחוק את השיעור לגמרי?")
          && edit({ op: "delete", uid: r.uid }),
      }, "מחיקה"))));

  if (S.alternatives) host.append(renderAlternatives(S.alternatives));
}

async function findAlternatives() {
  const r = currentRow();
  if (!r) return;
  $("#inspector").append(note("", "", "בודק כל שעה בשבוע מול כל האילוצים…"));
  try {
    S.alternatives = await post(
      `/api/school/${encodeURIComponent(S.id)}/alternatives`, { uid: r.uid });
  } catch (err) { toast(err.message, "bad"); }
  renderInspector();
}

function renderAlternatives(data) {
  const days = S.state.grid.days;
  const box = el("div", { class: "panel" },
    el("b", {}, `חלופות — ${data.counts.ok} אפשריות, `
      + `${data.counts.blocked} חסומות`),
    el("p", { class: "lead" },
      "כל חלופה נבדקה כמערכת שלמה: ההפרש הוא השינוי בציון של כל בית הספר, "
      + "לא של השיעור הזה בלבד."));

  if (!data.options.length) {
    box.append(note("warn", "אין מיקום חלופי שאינו מפר אילוץ קשיח",
      "הרשימה למטה מסבירה כל שעה ושעה."));
  }
  for (const o of data.options) {
    box.append(el("div", {
      class: "opt " + (o.delta < 0 ? "better" : o.delta > 0 ? "worse" : "same"),
    },
      el("div", { class: "grow" },
        el("b", {}, `${days[o.day]}, שעה ${o.period}`),
        el("small", {},
          (o.kind === "swap" ? "החלפה עם השיעור שם" : "מעבר לשעה פנויה")
          + (o.changes.length ? " · " + o.changes.map(
              c => `${c.label} ${c.delta > 0 ? "+" : ""}${c.delta}`).join(" · ")
            : " · ללא שינוי בציון"))),
      el("span", {
        class: "delta " + (o.delta > 0 ? "up" : o.delta < 0 ? "down" : ""),
      }, o.delta > 0 ? "+" + o.delta : o.delta),
      el("button", {
        onclick: () => edit({
          op: "exchange", uid: data.uid, day: o.day, period: o.period,
          partner: o.partner,
        }),
      }, "העברה")));
  }
  if (data.blocked.length) {
    const list = el("div", { class: "blocked-list" });
    for (const o of data.blocked) {
      list.append(el("div", {}, `${days[o.day]} ${o.period}: `
        + o.blocking.map(b => `${b.title} — ${b.message}`).join(" · ")));
    }
    box.append(el("details", {},
      el("summary", {}, "מדוע שאר השעות חסומות"), list));
  }
  return box;
}

/* ========================================================= edit and undo */

async function edit(payload) {
  try {
    const out = await post(`/api/school/${encodeURIComponent(S.id)}/edit`,
                           payload);
    S.alternatives = null;
    await refresh();
    renderTimetable();
    flashEdit(out);
  } catch (err) { toast(err.message, "bad"); }
}

function flashEdit(out) {
  const host = $("#edit-flash");
  host.textContent = "";
  if (out.introduced.length) {
    host.append(note("bad",
      `השינוי יצר ${out.introduced.length} הפרות של אילוצים קשיחים`,
      "אפשר להשאיר כך ולתקן בהמשך, לבטל את הפעולה, או לבחור אחד "
      + "השיעורים המעורבים ולבקש חלופות."));
    for (const v of out.introduced) host.append(note("bad", v.title, v.message));
  }
  for (const v of out.resolved) {
    host.append(note("ok", "נפתר: " + v.title, v.message));
  }
  if (out.delta) {
    host.append(note(out.delta > 0 ? "warn" : "ok", "",
      `ציון האילוצים הרכים ${out.delta > 0 ? "עלה" : "ירד"} ב־`
      + `${Math.abs(out.delta)}`
      + (out.changes.length ? ": " + out.changes.map(
          c => `${c.label} ${c.delta > 0 ? "+" : ""}${c.delta}`).join(" · ")
        : "")));
  }
  if (!out.introduced.length && !out.delta && !out.resolved.length) {
    host.append(note("ok", "", "השינוי בוצע ולא הפר ולא שינה שום כלל."));
  }
}

async function undo() {
  try {
    S.state = await post(`/api/school/${encodeURIComponent(S.id)}/undo`);
    S.alternatives = null;
    renderTimetable(); renderScore(); renderNav();
    toast("הפעולה בוטלה", "ok");
  } catch (err) { toast(err.message, "bad"); }
}

async function approve() {
  await edit({ op: "approve" });
  toast("המערכת סומנה כמאושרת. בנייה חדשה תתקן אותה במקום לבנות מחדש.", "ok");
}

async function showVersions() {
  const host = $("#edit-flash");
  host.textContent = "";
  go("timetable");
  const { versions } = await api(
    `/api/school/${encodeURIComponent(S.id)}/versions`);
  if (!versions.length) {
    host.append(note("", "", "עדיין לא נשמרו גרסאות קודמות."));
    return;
  }
  const box = el("div", { class: "panel" },
    el("b", {}, "גרסאות קודמות"),
    el("p", { class: "lead" },
      "כל שינוי שומר את המערכת כפי שהייתה לפניו. שחזור הוא עצמו הפיך."));
  for (const v of versions) {
    box.append(el("div", { class: "opt" },
      el("div", { class: "grow" }, el("b", {}, v.label || "שינוי"),
         el("small", {}, v.at)),
      el("button", {
        onclick: async () => {
          S.state = await post(
            `/api/school/${encodeURIComponent(S.id)}/restore`,
            { version: v.id });
          S.alternatives = null;
          renderTimetable(); renderScore(); renderNav();
          toast("הגרסה שוחזרה", "ok");
        },
      }, "שחזור")));
  }
  host.append(box);
}

function editLesson(r) {
  const subject = prompt("מקצוע:", r.subject);
  if (subject === null) return;
  const klass = prompt("כיתה (ריק = ללא כיתה):", r.klass || "");
  if (klass === null) return;
  const teachers = prompt(
    `מורות (מופרדות בפסיק; קיימות: ${S.state.teachers.join(", ")}):`,
    r.teachers.join(", "));
  if (teachers === null) return;
  edit({
    op: "retitle", uid: r.uid, subject, klass,
    teachers: teachers.split(",").map(s => s.trim()).filter(Boolean),
  });
}

function addLesson() {
  const subject = prompt("מקצוע חדש:");
  if (!subject) return;
  const klass = prompt(`כיתה (${S.state.classes.join(", ")}):`,
                       S.state.classes[0] || "");
  if (klass === null) return;
  const teachers = prompt(`מורות (${S.state.teachers.join(", ")}):`,
                          S.state.teachers[0] || "");
  if (!teachers) return;
  edit({
    op: "add", subject, klass,
    teachers: teachers.split(",").map(s => s.trim()).filter(Boolean),
    length: Number(prompt("אורך (1 או 2):", "1")) || 1,
  });
}

/* ================================================================ build = */

function toggleBuild() {
  go("timetable");
  const panel = $("#build-panel");
  panel.classList.remove("hidden");
  panel.textContent = "";
  panel.append(
    el("b", {}, "בניית מערכת שעות"),
    el("p", { class: "lead" },
      "המנוע מריץ קודם בדיקות ספירה, אחר כך סולם הקשחה — הוא מנסה לאכוף "
      + "את כל כללי החובה, ויורד דרגה רק כשרמה מסוימת מוכחת כבלתי אפשרית. "
      + "מה שנשאר מתומחר הוא בדיוק מה שבית הספר צריך להחליט עליו."),
    el("div", { class: "row" },
      el("label", { class: "inline" }, "מגבלת זמן (שניות)",
        el("input", { id: "b-time", type: "number", value: 180, min: 20,
                      max: 3600, style: "width:6.5em" })),
      el("label", { class: "inline" }, "מעבדים",
        el("input", { id: "b-workers", type: "number", value: 8, min: 1,
                      max: 32, style: "width:5em" })),
      el("label", { class: "inline" },
        el("input", { id: "b-anchor", type: "checkbox",
                      checked: !!S.state?.approved }),
        "שמירה על המערכת המאושרת (תיקון במקום בנייה מחדש)")),
    el("div", { class: "row" },
      el("button", { class: "primary", onclick: startBuild }, "התחלה"),
      el("button", { class: "ghost", onclick: runChecks },
         "בדיקות היתכנות בלבד"),
      el("button", { class: "ghost",
                     onclick: () => panel.classList.add("hidden") }, "סגירה")),
    el("div", { id: "build-progress" }));
}

async function runChecks() {
  const host = $("#build-progress");
  host.textContent = "";
  host.append(renderChecks(
    await api(`/api/school/${encodeURIComponent(S.id)}/checks`)));
}

async function startBuild() {
  const host = $("#build-progress");
  host.textContent = "";
  const track = el("div", { class: "bar-track" },
                   el("div", { class: "bar-fill", style: "width:0" }));
  const line = el("div", { class: "lead" }, "מתחיל…");
  host.append(track, line);
  let job;
  try {
    ({ job } = await post(`/api/school/${encodeURIComponent(S.id)}/generate`, {
      time_limit: Number($("#b-time").value),
      workers: Number($("#b-workers").value),
      anchor: $("#b-anchor").checked,
    }));
  } catch (err) { host.append(note("bad", "", err.message)); return; }

  const tick = async () => {
    const status = await api(`/api/jobs/${job}`);
    $(".bar-fill", track).style.width = `${Math.round(status.progress * 100)}%`;
    line.textContent = status.message;
    if (status.state === "running") { setTimeout(tick, 700); return; }
    if (status.state === "failed") {
      host.append(note("bad", "הבנייה נכשלה", status.error));
      return;
    }
    await refresh();
    renderTimetable();
    host.append(renderSolveReport(status.result.report));
    if (status.result.solved) toast("המערכת נבנתה", "ok");
  };
  setTimeout(tick, 500);
}

function renderSolveReport(r) {
  const box = el("div", {});
  if (r.status === "INFEASIBLE") {
    box.append(note("bad", "לא נמצאה מערכת שעות",
      "אף רמה בסולם ההקשחה לא נפתרה. הבדיקות למטה, ומסך האילוצים, הם "
      + "המקום להרפות כלל או לתקן נתון."));
  } else {
    box.append(note("ok", `נמצאה מערכת (${r.status})`,
      `רמת ההקשחה שהתקבלה: ${r.rung}. ציון עונשין ${r.objective}, `
      + `${r.wall_time} שניות.`
      + (r.polished ? ` ${r.polished} שיעורים הוחלפו בליטוש הסופי.` : "")));
  }
  for (const a of r.attempts || []) {
    box.append(note("", "", `ניסיון: ${a.rung} — ${a.status} (${a.seconds} ש')`));
  }
  if (!r.hard_ok && r.problems?.length) {
    box.append(note("bad", "האימות הבלתי תלוי מצא הפרות",
                    r.problems.join(" · ")));
  }
  box.append(renderChecks(r));
  return box;
}

/* ============================================================== quality = */

function renderScore() {
  const host = $("#score");
  host.textContent = "";
  const rv = S.state?.review;
  if (!rv) return;
  host.append(
    el("span", { class: "chip" + (rv.ok ? "" : " bad") },
       rv.ok ? "תקין" : `${rv.violations.length} הפרות`),
    `ציון רך ${rv.total}`,
    rv.unplaced.length
      ? el("span", { class: "chip bad" },
           `${rv.unplaced.length} לא משובצים`) : "");
}

function teacherTable(metrics, limit) {
  const t = el("table", { class: "kv" },
    el("thead", {}, el("tr", {},
      ["מורה", "שעות", "מוצהר", "ימים", "חלונות", "יום ארוך", "יום קצר",
       "ימים ארוכים", "פתיחות בוקר"].map(h => el("th", {}, h)))));
  const body = el("tbody");
  const rows = limit ? metrics.teachers.slice(0, limit) : metrics.teachers;
  for (const x of rows) {
    body.append(el("tr", {},
      el("td", {}, x.name + (x.homeroom ? ` (מחנכת ${x.homeroom})` : "")),
      el("td", { class: "num" }, x.hours),
      el("td", { class: "num" }, x.declared ?? "—"),
      el("td", { class: "num" }, x.days),
      el("td", { class: "num" }, x.windows + (x.window_days.length
        ? ` (${x.window_days.join(", ")})` : "")),
      el("td", { class: "num" }, x.longest_day),
      el("td", { class: "num" }, x.shortest_day),
      el("td", { class: "num" }, x.long_days),
      el("td", { class: "num" }, x.first_periods)));
  }
  t.append(body);
  return t;
}

function renderQuality() {
  const host = $("#quality");
  host.textContent = "";
  const rv = S.state?.review;
  if (!rv) {
    host.append(empty("אין עדיין מערכת שעות להערכה",
      "אחרי הבנייה יופיעו כאן ההפרות, פירוק העונשים ונתוני האיזון."));
    return;
  }
  host.append(rv.ok
    ? note("ok", "כל האילוצים הקשיחים מתקיימים",
           "נבדק על ידי מאמת עצמאי שאינו חולק שורת קוד עם הפותר.")
    : note("bad", `${rv.violations.length} הפרות של אילוצים קשיחים`, ""));
  for (const v of rv.violations) host.append(note("bad", v.title, v.message));
  for (const c of rv.coverage) {
    host.append(note("warn", "פער מול טבלת השיעורים", c.message));
  }
  for (const u of rv.unplaced) {
    host.append(note("warn", "שיעור שאינו משובץ",
      `${u.subject} · ${u.klass || "—"} · ${u.teachers.join("/")}`));
  }

  host.append(el("h3", { style: "margin-top:20px" },
    `אילוצים רכים — ציון כולל ${rv.total}`));
  if (!rv.penalty_detail.length) {
    host.append(note("ok", "", "כל האילוצים הרכים מסופקים במלואם."));
  } else {
    const t = el("table", { class: "kv" },
      el("thead", {}, el("tr", {}, el("th", {}, "מחיר"), el("th", {}, "הכלל"),
        el("th", {}, "מה זה אומר"))));
    const body = el("tbody");
    for (const p of rv.penalty_detail) {
      body.append(el("tr", {}, el("td", { class: "num" }, p.cost),
        el("td", {}, p.label), el("td", {}, p.explanation || "")));
    }
    t.append(body);
    host.append(el("div", { class: "scroll" }, t));
  }

  const m = rv.metrics, q = m.summary;
  host.append(el("h3", { style: "margin-top:20px" }, "איזון ועומס"));
  host.append(el("div", { class: "stats" },
    [[q.teachers, "מורות"], [q.with_window, "עם חלון"],
     [q.without_window, "ללא חלון"], [q.total_windows, "חלונות בשבוע"],
     [q.average_day_spread, "פער ממוצע ארוך–קצר"],
     [q.widest_day_spread, "הפער הגדול ביותר"]]
      .map(([n, label]) => stat(n, label))));
  host.append(note("", "",
    "האיזון מדווח ואינו מנוקד: הפותר כבר ממזער את הכללים שבית הספר ניסח, "
    + "ומספר נוסף שמושך נגדם היה כלל שאיש לא ביקש."));
  host.append(el("div", { class: "scroll" }, teacherTable(m, 0)));
}

/* =============================================================== export = */

function renderExport() {
  const host = $("#export");
  host.textContent = "";
  if (!S.id) {
    host.append(empty("לא נבחר בית ספר", "יש לבחור בית ספר תחילה."));
    return;
  }
  const base = `/download/school/${encodeURIComponent(S.id)}`;
  const view = el("select", { id: "export-view", style: "width:auto" },
    el("option", { value: "both" }, "כיתות ומורות"),
    el("option", { value: "class" }, "כיתות בלבד"),
    el("option", { value: "teacher" }, "מורות בלבד"));
  const link = kind => () => {
    window.location = `${base}/${kind}?view=${$("#export-view").value}`;
  };
  host.append(
    el("div", { class: "panel" },
      el("b", {}, "Word"),
      el("p", { class: "lead" },
        "שתי גרסאות מאותו תוכן: אחת לעריכה, ואחת מוגנת לקריאה — Word מסמן "
        + "אותה כ'הגבלת עריכה: ללא שינויים', כדי שהפריסה תישמר אצל מי "
        + "שמקבל אותה. אין סיסמה: מי שצריך באמת לערוך יכול לבטל את ההגנה."),
      el("div", { class: "row" },
        el("label", { class: "inline" }, "תוכן", view),
        el("button", { class: "primary", onclick: link("docx") },
           "Word לעריכה"),
        el("button", { onclick: link("docx-final") }, "Word סופי (מוגן)"))),
    el("div", { class: "panel" },
      el("b", {}, "פורמטים נוספים"),
      el("div", { class: "row" },
        el("a", { class: "btn", href: `${base}/html` }, "דף HTML להדפסה"),
        el("a", { class: "btn", href: `${base}/json` },
           "גיבוי מלא (JSON) — ניתן לטעינה חוזרת"),
        el("a", { class: "btn", href: `${base}/source` },
           "נתוני בית הספר כ-CSV לעריכה ב-Excel"))));
}

/* ============================================================== account = */

function renderAccount() {
  const host = $("#account");
  host.textContent = "";
  if (!S.auth) {
    host.append(note("warn", "השרת פועל ללא חשבונות",
      "הופעל עם --no-auth: כל מי שמגיע לכתובת רואה את אותם נתונים. "
      + "להפעלת חשבונות יש להריץ את השרת בלי הדגל."));
    return;
  }
  host.append(el("div", { class: "panel" },
    el("b", {}, "פרטי החשבון"),
    el("p", { class: "lead" },
      `שם משתמש: ${S.me.username} · שם לתצוגה: ${S.me.display} · `
      + `נפתח ב-${(S.me.created || "").slice(0, 10)}`),
    el("p", { class: "lead" },
      `${S.schools.length} בתי ספר שמורים בחשבון הזה. הם נשמרים על המחשב `
      + "שבו רץ השרת, בתיקייה נפרדת לכל חשבון.")));

  const box = el("form", { class: "panel", onsubmit: async e => {
    e.preventDefault();
    try {
      await post("/api/auth/password", {
        current: e.target.current.value, new: e.target.next.value });
      e.target.reset();
      toast("הסיסמה הוחלפה. שאר החיבורים לחשבון נותקו.", "ok");
    } catch (err) { toast(err.message, "bad"); }
  } },
    el("b", {}, "החלפת סיסמה"),
    el("p", { class: "lead" },
      "החלפה מנתקת כל חיבור אחר לחשבון — זה מה שרוצים כשחושדים "
      + "שמישהו אחר השתמש בו. אין שחזור סיסמה: מי שמאבד אותה, המנהל "
      + "שיש לו גישה לתיקיית הנתונים הוא שמאפס."),
    el("label", {}, "הסיסמה הנוכחית",
      el("input", { name: "current", type: "password", required: true,
                    autocomplete: "current-password" })),
    el("label", {}, "סיסמה חדשה (8 תווים לפחות)",
      el("input", { name: "next", type: "password", required: true,
                    minlength: 8, autocomplete: "new-password" })),
    el("button", { class: "primary", type: "submit" }, "החלפה"));
  host.append(box);
}

/* ================================================================= init = */

async function enterApp() {
  $("#auth").classList.add("hidden");
  $("#app").classList.remove("hidden");
  const chip = $("#user-chip");
  if (S.me) {
    chip.classList.remove("hidden");
    $("#user-initial").textContent = (S.me.display || S.me.username)
      .trim().charAt(0).toUpperCase();
    $("#user-name").textContent = S.me.display || S.me.username;
  } else {
    chip.classList.add("hidden");
  }
  await loadHelp();
  await loadSchools();
  if (S.schools.length) await open(S.schools[0].id);
  else go("schools");
}

let helpLoaded = false;
async function loadHelp() {
  if (helpLoaded) return;
  const help = await api("/api/help");
  const box = $("#format-help");
  box.textContent = "";
  for (const c of help.columns) {
    box.append(el("div", { class: "help-item" },
      el("b", {},
         c.section === "איך מסמנים טבלה" ? c.section : "## " + c.section),
      c.text));
  }
  $("#sample").textContent = SAMPLE;
  helpLoaded = true;
}

(async function start() {
  try { applyTheme(localStorage.getItem("tt-theme")); }
  catch { applyTheme(null); }
  $("#logout").append(icon("logout"));

  const me = await api("/api/auth/me");
  S.auth = me.auth;
  S.me = me.user;
  if (!me.auth || me.user) {
    await enterApp();
  } else {
    setAuthMode(me.first_run ? "register" : "login");
    if (me.first_run) {
      $("#auth-sub").textContent =
        "עדיין אין חשבונות על השרת הזה — החשבון הראשון נפתח כאן.";
    }
    showAuth();
  }
})();

const SAMPLE = `## ימים
יום,מספר שעות
ראשון,6
שני,6

## מורות
שם המורה,שעות שבועיות,מחנכת של,מקסימום שעות ליום,מקסימום חלונות
רות כהן,15,א,5,1
דנה מזרחי,12,,4,2

## כיתות
שם הכיתה,יום,מינימום שעות,מקסימום שעות,שעות מדויקות
א,כל הימים,5,5,5

## שיעורים
כיתה,מקצוע,מורות,שעות,פיצול
א,תורה,רות כהן,5,
א,חשבון,רות כהן,5,"2,1,1,1"
א,אנגלית,דנה מזרחי,3,
ו,אנגלית,הודיה|רבקה,4,"2,1,1"

## אילוצים
שם המורה,סוג,יום,שעות,הערה
דנה מזרחי,יום חופש,שני,,אינה מלמדת בשני
מיכל אבן,בחירת יום חופש,"שלישי, רביעי",,אחד מהשניים
רות כהן,שעות חסומות,כל הימים,6,
שרה לוי,סיום מוקדם,חמישי,4,מסיימת בשעה 4`;

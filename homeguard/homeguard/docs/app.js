// HomeGuard dashboard: accounts, approval, camera preview, events.
// Data lives in Firestore (written by the Raspberry Pi). Access rules live in firestore.rules.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, createUserWithEmailAndPassword,
  signOut, sendEmailVerification, sendPasswordResetEmail, updateProfile,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import {
  getFirestore, doc, collection, query, orderBy, limit, onSnapshot, getDoc, getDocs,
  setDoc, updateDoc, deleteDoc, writeBatch, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

/* ---------------------------------------------------------------- helpers */
const $ = (id) => document.getElementById(id);
const LEVELS = { 1: "Person seen", 2: "Motion confirmed", 3: "Danger" };
const MAX_PREVIEW_MS = 10 * 60 * 1000;   // stop a preview session after 10 minutes
const PING_MS = 20 * 1000;               // tell the Pi "someone is watching" this often
const OFFLINE_AFTER_S = 180;             // Pi reports at least once a minute

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid);
  return n;
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
function ago(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + " s ago";
  if (sec < 3600) return Math.round(sec / 60) + " min ago";
  if (sec < 86400) return Math.round(sec / 3600) + " h ago";
  return Math.round(sec / 86400) + " d ago";
}
const nowS = () => Date.now() / 1000;

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 4000);
}

function authMessage(err) {
  switch (err && err.code) {
    case "auth/invalid-credential":
    case "auth/user-not-found":
    case "auth/wrong-password": return "That email and password don't match an account.";
    case "auth/invalid-email":
    case "auth/missing-email": return "Enter a valid email address.";
    case "auth/missing-password": return "Enter your password.";
    case "auth/email-already-in-use": return "An account with this email already exists. Sign in instead.";
    case "auth/weak-password": return "Choose a password with at least 8 characters.";
    case "auth/too-many-requests": return "Too many attempts. Wait a few minutes and try again.";
    case "auth/network-request-failed": return "No connection. Check your internet and try again.";
    default: return "Something went wrong. Try again.";
  }
}

/* ---------------------------------------------------------------- state */
const state = {
  user: null,
  access: null,           // "owner" | "member" | "pending" | "blocked"
  profile: null,
  status: null,
  events: [],
  people: [],
  filters: { min: 1, range: "all", term: "" },
  unsubs: [],
  armPending: null,
  armTimer: null,
  signingUp: false,
  preview: { on: false, ping: null, tick: null, unsub: null, startedAt: 0, frameTs: 0 },
};

let auth = null;
let db = null;

/* ---------------------------------------------------------------- views */
function showView(name) {
  $("view-auth").hidden = name !== "auth";
  $("view-gate").hidden = name !== "gate";
  $("view-app").hidden = name !== "app";
}

function route() {
  if (state.access !== "owner" && state.access !== "member") return;
  const allowed = ["live", "events", "account"].concat(state.access === "owner" ? ["people"] : []);
  const wanted = location.hash.slice(1);
  const tab = allowed.includes(wanted) ? wanted : "live";
  document.querySelectorAll("[data-view]").forEach((s) => { s.hidden = s.dataset.view !== tab; });
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (tab !== "live") stopPreview();
}
window.addEventListener("hashchange", route);

/* ---------------------------------------------------------------- auth screens */
function setAuthMode(mode) {
  const signup = mode === "signup";
  $("form-signin").hidden = signup;
  $("form-signup").hidden = !signup;
  $("seg-signin").setAttribute("aria-pressed", String(!signup));
  $("seg-signup").setAttribute("aria-pressed", String(signup));
  authMsg("");
}
function authMsg(text, ok = false) {
  const m = $("auth-msg");
  m.textContent = text;
  m.classList.toggle("ok", ok);
}

$("seg-signin").addEventListener("click", () => setAuthMode("signin"));
$("seg-signup").addEventListener("click", () => setAuthMode("signup"));

$("form-signin").addEventListener("submit", async (e) => {
  e.preventDefault();
  authMsg("");
  try {
    await signInWithEmailAndPassword(auth, $("si-email").value.trim(), $("si-pass").value);
  } catch (err) { authMsg(authMessage(err)); }
});

$("btn-forgot").addEventListener("click", async () => {
  const email = $("si-email").value.trim();
  if (!email) { authMsg("Enter your email above, then select \"Forgot your password?\" again."); return; }
  try {
    await sendPasswordResetEmail(auth, email);
  } catch (err) {
    if (err && err.code !== "auth/user-not-found") { authMsg(authMessage(err)); return; }
  }
  authMsg("If an account exists for " + email + ", a reset link is on its way.", true);
});

$("form-signup").addEventListener("submit", async (e) => {
  e.preventDefault();
  authMsg("");
  const name = $("su-name").value.trim();
  const email = $("su-email").value.trim();
  const pass = $("su-pass").value;
  if (!name) { authMsg("Enter your name."); return; }
  if (pass.length < 8) { authMsg("Choose a password with at least 8 characters."); return; }
  state.signingUp = true;
  try {
    const cred = await createUserWithEmailAndPassword(auth, email, pass);
    try { await updateProfile(cred.user, { displayName: name }); } catch { /* name is saved to the profile below anyway */ }
    try { await sendEmailVerification(cred.user); } catch { /* the gate offers a resend */ }
    state.signingUp = false;
    await boot(cred.user);
  } catch (err) {
    state.signingUp = false;
    authMsg(authMessage(err));
  }
});

/* ---------------------------------------------------------------- access */
async function ensureProfile(user) {
  const ref = doc(db, "users", user.uid);
  const snap = await getDoc(ref);
  if (snap.exists()) return snap.data();
  const data = {
    email: user.email,
    name: user.displayName || (user.email || "").split("@")[0],
    role: "pending",
    createdAt: serverTimestamp(),
  };
  await setDoc(ref, data);
  return data;
}

// Ask Firestore what this account is allowed to read. The rules are the single source of truth.
async function resolveAccess() {
  const u = state.user;
  const prof = (await getDoc(doc(db, "users", u.uid))).data() || {};
  state.profile = prof;
  try {
    await getDocs(query(collection(db, "users"), limit(200)));
    state.access = "owner";
  } catch {
    try {
      await getDoc(doc(db, "status", "current"));
      state.access = "member";
    } catch {
      state.access = prof.role === "blocked" ? "blocked" : "pending";
    }
  }
}

async function boot(user) {
  teardown();
  state.user = user;
  try {
    await ensureProfile(user);
    await resolveAccess();
  } catch (err) {
    showView("auth");
    authMsg("Couldn't reach the database. Check your connection and try again.");
    return;
  }
  watchOwnProfile();
  render();
}

function render() {
  if (state.access === "owner" || state.access === "member") enterApp();
  else showGate();
}

function watchOwnProfile() {
  let prevRole = null;
  state.unsubs.push(onSnapshot(doc(db, "users", state.user.uid), async (snap) => {
    const role = snap.exists() ? snap.data().role : null;
    if (prevRole !== null && role !== prevRole) {
      await recheck();
    }
    prevRole = role;
  }, () => {}));
}

async function recheck() {
  try {
    await state.user.reload();
    await state.user.getIdToken(true);
    const u = state.user;
    const before = state.access;
    await resolveAccess();
    if (before !== state.access) {
      // Access changed: rebuild subscriptions for the new role.
      const { access, profile } = state;
      teardown();
      Object.assign(state, { user: u, access, profile });
      watchOwnProfile();
    }
    render();
  } catch { /* offline: keep the current screen */ }
}

/* ---------------------------------------------------------------- gate */
function showGate() {
  showView("gate");
  const u = state.user;
  const blocked = state.access === "blocked";
  $("gate-title").textContent = blocked ? "Access removed" : "Waiting for approval";
  $("gate-email").textContent = "Signed in as " + u.email;
  $("btn-verify").hidden = u.emailVerified || blocked;
  $("btn-recheck").hidden = blocked;
  $("gate-text").textContent = blocked
    ? "The owner has removed this account's access. Contact the owner if this is a mistake."
    : u.emailVerified
      ? "The owner of this home needs to approve your account. This page opens by itself once they do."
      : "Verify your email first, then wait for the owner to approve your account.";
  $("gate-msg").textContent = "";
}
$("btn-verify").addEventListener("click", async () => {
  try {
    await sendEmailVerification(state.user);
    $("gate-msg").textContent = "Verification email sent to " + state.user.email + ". Open the link, then select Check again.";
    $("gate-msg").className = "msg ok";
  } catch (err) {
    $("gate-msg").textContent = authMessage(err);
    $("gate-msg").className = "msg";
  }
});
$("btn-recheck").addEventListener("click", async () => {
  $("gate-msg").className = "msg";
  await recheck();
  if (state.access === "pending") {
    $("gate-msg").textContent = "Still waiting. The owner hasn't approved this account yet.";
  }
});
$("btn-gate-out").addEventListener("click", () => signOut(auth));

/* ---------------------------------------------------------------- app */
function enterApp() {
  showView("app");
  const owner = state.access === "owner";
  $("nav-people").hidden = !owner;
  $("arm-box").hidden = !owner;
  $("owner-tools").hidden = !owner;
  $("local-url").value = store.get("hg_local_url") || "";
  if (!state.subscribed) subscribeData();
  renderAll();
  route();
}

function subscribeData() {
  state.subscribed = true;
  const onErr = async (err) => {
    if (err && err.code === "permission-denied") await recheck();
  };

  state.unsubs.push(onSnapshot(doc(db, "status", "current"), (snap) => {
    state.status = snap.exists() ? snap.data() : null;
    if (state.armPending !== null && state.status && state.status.armed === state.armPending) clearArmPending();
    renderStatus();
  }, onErr));

  let first = true;
  state.unsubs.push(onSnapshot(query(collection(db, "events"), orderBy("ts", "desc"), limit(150)), (snap) => {
    if (!first) {
      snap.docChanges().forEach((ch) => {
        if (ch.type === "added" && ch.doc.data().level === 3) notifyDanger(ch.doc.data());
      });
    }
    first = false;
    state.events = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    renderEvents();
    renderCounts();
  }, onErr));

  if (state.access === "owner") {
    state.unsubs.push(onSnapshot(query(collection(db, "users"), limit(200)), (snap) => {
      state.people = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
      renderPeople();
    }, onErr));
  }

  state.statusTick = setInterval(renderStatus, 5000);
}

function renderAll() {
  renderStatus(); renderEvents(); renderCounts(); renderPeople(); renderAccount(); renderPreviewUI();
}

/* ---------------------------------------------------------------- status band */
function renderStatus() {
  const s = state.status;
  const band = $("band");
  let key = "none", title, sub;
  const age = s ? nowS() - s.updated : null;

  if (!s) {
    title = "Waiting for your Raspberry Pi";
    sub = "Nothing received yet. Start HomeGuard on the Pi and this page will update.";
  } else if (age > OFFLINE_AFTER_S) {
    key = "offline";
    title = "The Pi is offline";
    sub = "Last heard from it " + ago(age) + ". Check its power and Wi-Fi.";
  } else if (!s.armed) {
    key = "disarmed";
    title = "Detection is turned off";
    sub = "No alerts will be sent until it is turned back on.";
  } else if (s.level === 3) {
    key = "3"; title = "Possible intruder detected"; sub = "Open the camera preview and check your events.";
  } else if (s.level === 2) {
    key = "2"; title = "Motion confirmed near the camera"; sub = "The camera and motion sensor agree. Danger starts if it continues.";
  } else if (s.level === 1) {
    key = "1"; title = "A person is in view of the camera"; sub = "Waiting for the motion sensor to confirm.";
  } else {
    key = "0"; title = "All quiet at home"; sub = "Watching with " + (s.detector || "the camera") + ".";
  }
  band.dataset.state = key;
  band.setAttribute("aria-live", key === "3" ? "assertive" : "polite");
  $("band-title").textContent = title;
  $("band-sub").textContent = sub;

  $("f-armed").textContent = s ? (s.armed ? "On" : "Off") : "-";
  $("f-camera").textContent = s ? (s.camera_ok ? "Online" : "Offline") : "-";
  $("f-detector").textContent = s ? (s.detector || "-") : "-";
  $("f-updated").textContent = s ? ago(age) : "-";
  renderArm();
}

function renderArm() {
  if (state.access !== "owner") return;
  const s = state.status;
  const btn = $("btn-arm");
  const armed = state.armPending !== null ? state.armPending : !!(s && s.armed);
  btn.setAttribute("aria-checked", String(armed));
  btn.disabled = !s;
  $("arm-label").textContent = state.armPending !== null ? "Updating..." : (armed ? "Detection is on" : "Detection is off");
}
function clearArmPending() {
  clearTimeout(state.armTimer);
  state.armPending = null;
  renderArm();
}
$("btn-arm").addEventListener("click", async () => {
  if (!state.status || state.armPending !== null) return;
  const next = !state.status.armed;
  state.armPending = next;
  renderArm();
  try {
    await setDoc(doc(db, "control", "system"), { armed: next, by: state.user.uid, ts: Date.now() });
  } catch {
    clearArmPending();
    toast("Couldn't send that to the Pi. Try again.");
    return;
  }
  state.armTimer = setTimeout(() => {
    clearArmPending();
    toast("The Pi didn't respond. It may be offline.");
  }, 15000);
});

/* ---------------------------------------------------------------- camera preview */
function renderPreviewUI() {
  const p = state.preview;
  $("btn-preview").textContent = p.on ? "Stop preview" : "Start preview";
  $("preview-empty").hidden = p.on;
  if (!p.on) { $("preview-img").hidden = true; $("preview-hint").textContent = ""; }
}

function updatePreviewHint() {
  const p = state.preview;
  if (!p.on) return;
  if (Date.now() - p.startedAt > MAX_PREVIEW_MS) {
    stopPreview();
    toast("Preview stopped after 10 minutes. Start it again to keep watching.");
    return;
  }
  const img = $("preview-img");
  if (!p.frameTs) {
    $("preview-hint").textContent = "Asking the Pi for a picture...";
    return;
  }
  const age = nowS() - p.frameTs;
  img.classList.toggle("stale", age > 15);
  $("preview-hint").textContent = age > 15
    ? "Last picture is " + ago(age) + ". Waiting for the Pi."
    : "Updated " + ago(age);
}

async function pingPreview() {
  try {
    await setDoc(doc(db, "control", "preview"), { ts: Date.now(), by: state.user.uid });
  } catch {
    stopPreview();
    toast("Couldn't reach the camera. Try again.");
  }
}

function startPreview() {
  const p = state.preview;
  if (p.on) return;
  p.on = true; p.startedAt = Date.now(); p.frameTs = 0;
  renderPreviewUI();
  pingPreview();
  p.ping = setInterval(pingPreview, PING_MS);
  p.tick = setInterval(updatePreviewHint, 1000);
  updatePreviewHint();
  p.unsub = onSnapshot(doc(db, "preview", "current"), (snap) => {
    const img = $("preview-img");
    if (!snap.exists()) { img.hidden = true; p.frameTs = 0; return; }
    const d = snap.data();
    if (typeof d.img === "string" && d.img.startsWith("data:image/jpeg;base64,")) {
      img.src = d.img;
      img.hidden = false;
      p.frameTs = d.ts || 0;
      updatePreviewHint();
    }
  }, () => { toast("Couldn't load the camera picture."); });
}

function stopPreview() {
  const p = state.preview;
  if (!p.on) return;
  clearInterval(p.ping); clearInterval(p.tick);
  if (p.unsub) p.unsub();
  p.on = false; p.ping = p.tick = p.unsub = null; p.frameTs = 0;
  renderPreviewUI();
}
$("btn-preview").addEventListener("click", () => (state.preview.on ? stopPreview() : startPreview()));
document.addEventListener("visibilitychange", () => { if (document.hidden) stopPreview(); });

function renderLocalLink() {
  const raw = store.get("hg_local_url");
  const a = $("link-local");
  try {
    const u = new URL(raw);
    if (u.protocol === "http:" || u.protocol === "https:") { a.href = u.href; a.hidden = false; return; }
  } catch { /* not a URL */ }
  a.hidden = true;
}

/* ---------------------------------------------------------------- events */
function startOfToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; }

function renderCounts() {
  const t0 = startOfToday();
  const c = { 1: 0, 2: 0, 3: 0 };
  state.events.forEach((e) => { if (e.ts >= t0 && c[e.level] !== undefined) c[e.level]++; });
  [1, 2, 3].forEach((l) => { $("cnt-" + l).textContent = c[l]; });
}

function eventRow(e, deletable) {
  const hasThumb = typeof e.thumb === "string" && e.thumb.startsWith("data:image/jpeg;base64,");
  const thumb = hasThumb
    ? h("button", { class: "thumb", type: "button", "aria-label": "View snapshot", onclick: () => openLightbox(e.thumb) },
        h("img", { src: e.thumb, alt: "" }))
    : h("div", { class: "thumb thumb-empty", "aria-hidden": "true" });
  return h("li", { class: "event", "data-level": String(e.level) },
    thumb,
    h("div", {},
      h("p", { class: "event-level", text: LEVELS[e.level] || "Event" }),
      h("p", { text: e.message }),
      h("p", { class: "event-time muted", text: fmtTime(e.ts) })),
    deletable ? h("button", { class: "btn quiet danger-text", type: "button", onclick: () => deleteEvent(e) }, "Delete") : null);
}

function renderEvents() {
  const owner = state.access === "owner";
  const recent = $("recent");
  recent.replaceChildren();
  const latest = state.events.slice(0, 5);
  if (!latest.length) recent.append(h("li", { class: "empty", text: "No events yet. When the camera sees someone, it shows up here." }));
  latest.forEach((e) => recent.append(eventRow(e, false)));

  const { min, range, term } = state.filters;
  const t = term.trim().toLowerCase();
  const t0 = range === "today" ? startOfToday() : range === "week" ? nowS() - 7 * 86400 : 0;
  const shown = state.events.filter((e) =>
    e.level >= min && e.ts >= t0 &&
    (!t || `${e.message} ${LEVELS[e.level]} ${fmtTime(e.ts)}`.toLowerCase().includes(t)));

  $("count").textContent = state.events.length
    ? `Showing ${shown.length} of ${state.events.length} recent events`
    : "";
  const list = $("events");
  list.replaceChildren();
  if (!shown.length) {
    list.append(h("li", { class: "empty", text: state.events.length ? "No events match. Try a different search or filter." : "No events yet." }));
  }
  shown.forEach((e) => list.append(eventRow(e, owner)));
}

$("search").addEventListener("input", (e) => { state.filters.term = e.target.value; renderEvents(); });
$("range").addEventListener("change", (e) => { state.filters.range = e.target.value; renderEvents(); });
document.querySelectorAll("[data-min]").forEach((b) => b.addEventListener("click", () => {
  state.filters.min = Number(b.dataset.min);
  document.querySelectorAll("[data-min]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  renderEvents();
}));

async function deleteEvent(e) {
  if (!confirm("Delete this event? This can't be undone.")) return;
  try { await deleteDoc(doc(db, "events", e.id)); toast("Event deleted."); }
  catch { toast("Couldn't delete the event."); }
}
$("btn-clear").addEventListener("click", async () => {
  if (!state.events.length) { toast("There are no events to delete."); return; }
  if (!confirm(`Delete all ${state.events.length} loaded events? This can't be undone.`)) return;
  try {
    const batch = writeBatch(db);
    state.events.forEach((e) => batch.delete(doc(db, "events", e.id)));
    await batch.commit();
    toast("Events deleted.");
  } catch { toast("Couldn't delete the events."); }
});

function openLightbox(src) {
  $("lightbox-img").src = src;
  const d = $("lightbox");
  if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
}

/* ---------------------------------------------------------------- notifications */
function notifyDanger(ev) {
  if (store.get("hg_notify") !== "1") return;
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("HomeGuard: danger detected", { body: ev.message });
    }
  } catch { /* some browsers block constructor use */ }
  if (!document.title.startsWith("! ")) document.title = "! " + document.title;
}
window.addEventListener("focus", () => { document.title = document.title.replace(/^! /, ""); });

function renderNotifyButton() {
  const on = store.get("hg_notify") === "1" && "Notification" in window && Notification.permission === "granted";
  const b = $("btn-notify");
  b.setAttribute("aria-pressed", String(on));
  b.textContent = on ? "Turn off danger notifications" : "Turn on danger notifications";
  if (!("Notification" in window)) { b.disabled = true; b.textContent = "Notifications aren't supported in this browser"; }
}
$("btn-notify").addEventListener("click", async () => {
  const on = $("btn-notify").getAttribute("aria-pressed") === "true";
  if (on) { store.set("hg_notify", "0"); renderNotifyButton(); return; }
  try {
    const perm = await Notification.requestPermission();
    if (perm === "granted") { store.set("hg_notify", "1"); toast("Notifications are on."); }
    else toast("Notifications are blocked. Allow them in your browser's site settings.");
  } catch { toast("Couldn't turn on notifications."); }
  renderNotifyButton();
});

/* ---------------------------------------------------------------- people (owner) */
function personRow(p, actions) {
  const joined = p.createdAt && p.createdAt.seconds ? "Joined " + fmtTime(p.createdAt.seconds) : "";
  const you = state.user && p.id === state.user.uid;
  return h("li", { class: "person" },
    h("div", {},
      h("p", { class: "person-name", text: (p.name || "Unnamed") + (you ? " (you, owner)" : "") }),
      h("p", { class: "muted", text: p.email || "" }),
      joined ? h("p", { class: "muted event-time", text: joined }) : null),
    you ? null : h("div", { class: "actions" }, actions));
}
async function setRole(p, role, okText) {
  try { await updateDoc(doc(db, "users", p.id), { role }); toast(okText); }
  catch { toast("Couldn't change that account. Try again."); }
}

function renderPeople() {
  if (state.access !== "owner") return;
  const groups = { pending: [], member: [], blocked: [] };
  state.people.forEach((p) => {
    const you = state.user && p.id === state.user.uid;   // the owner's own profile starts as "pending"
    (you ? groups.member : (groups[p.role] || groups.pending)).push(p);
  });
  const byNewest = (a, b) => ((b.createdAt && b.createdAt.seconds) || 0) - ((a.createdAt && a.createdAt.seconds) || 0);

  const fill = (id, list, emptyText, actions) => {
    const ul = $(id);
    ul.replaceChildren();
    if (!list.length) ul.append(h("li", { class: "empty", text: emptyText }));
    list.sort(byNewest).forEach((p) => ul.append(personRow(p, actions(p))));
  };
  fill("people-pending", groups.pending, "No one is waiting. New sign-ups appear here for you to approve.", (p) => [
    h("button", { class: "btn primary", type: "button", onclick: () => setRole(p, "member", "Approved " + (p.name || p.email) + ".") }, "Approve"),
    h("button", { class: "btn quiet danger-text", type: "button", onclick: () => setRole(p, "blocked", "Declined.") }, "Decline"),
  ]);
  fill("people-members", groups.member, "No one else has access yet.", (p) => [
    h("button", { class: "btn quiet danger-text", type: "button", onclick: () => setRole(p, "blocked", "Access removed.") }, "Remove access"),
  ]);
  fill("people-blocked", groups.blocked, "No removed accounts.", (p) => [
    h("button", { class: "btn", type: "button", onclick: () => setRole(p, "member", "Access restored.") }, "Restore access"),
  ]);

  const n = groups.pending.filter((p) => !(state.user && p.id === state.user.uid)).length;
  const badge = $("badge-people");
  badge.hidden = n === 0;
  badge.textContent = String(n);
}

/* ---------------------------------------------------------------- account */
function renderAccount() {
  const u = state.user;
  if (!u) return;
  $("ac-name").textContent = u.displayName || (state.profile && state.profile.name) || "-";
  $("ac-email").textContent = u.email;
  $("ac-role").textContent = state.access === "owner" ? "Owner" : "Member";
  $("ac-verified").textContent = u.emailVerified ? "Yes" : "No";
  $("ac-name-input").value = u.displayName || (state.profile && state.profile.name) || "";
  renderNotifyButton();
  renderLocalLink();
}

$("form-name").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("ac-name-input").value.trim();
  if (!name) return;
  try {
    await updateProfile(state.user, { displayName: name });
    await updateDoc(doc(db, "users", state.user.uid), { name });
    renderAccount();
    toast("Name saved.");
  } catch { toast("Couldn't save your name. Try again."); }
});
$("btn-reset").addEventListener("click", async () => {
  try { await sendPasswordResetEmail(auth, state.user.email); toast("Reset link sent to " + state.user.email + "."); }
  catch (err) { toast(authMessage(err)); }
});
$("form-local").addEventListener("submit", (e) => {
  e.preventDefault();
  const raw = $("local-url").value.trim();
  if (raw) {
    try {
      const u = new URL(raw);
      if (u.protocol !== "http:" && u.protocol !== "https:") throw new Error("scheme");
    } catch { toast("Enter an address that starts with http:// or https://"); return; }
  }
  store.set("hg_local_url", raw);
  renderLocalLink();
  toast(raw ? "Saved." : "Link removed.");
});
$("btn-signout").addEventListener("click", () => signOut(auth));

/* ---------------------------------------------------------------- lifecycle */
function teardown() {
  stopPreview();
  state.unsubs.forEach((u) => { try { u(); } catch { /* already closed */ } });
  state.unsubs = [];
  clearInterval(state.statusTick);
  clearArmPending();
  Object.assign(state, {
    user: null, access: null, profile: null, status: null, events: [], people: [], subscribed: false,
  });
}

function start() {
  const placeholder = !firebaseConfig.apiKey || firebaseConfig.apiKey.startsWith("PASTE");
  showView("auth");
  if (placeholder) {
    authMsg("Firebase isn't set up yet. Add your project's web config to docs/firebase-config.js.");
    document.querySelectorAll("#view-auth button, #view-auth input").forEach((el) => { el.disabled = true; });
    return;
  }
  const app = initializeApp(firebaseConfig);
  auth = getAuth(app);
  db = getFirestore(app);

  onAuthStateChanged(auth, async (user) => {
    if (state.signingUp) return;
    if (!user) {
      teardown();
      $("si-pass").value = ""; $("su-pass").value = "";
      showView("auth");
      return;
    }
    await boot(user);
  });
}
start();

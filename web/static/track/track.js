/* WARRANT Track: each side-effecting tool call is drawn as a parcel on a delivery route.
   Every animation is driven by a real event from /api/run or /api/replay (SSE). */
"use strict";

const SPRITE = "/static/track/icons/sprite.svg";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id) => `<svg aria-hidden="true"><use href="${SPRITE}#${id}"/></svg>`;
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, REDUCED ? 0 : ms));

const STAGES = [
  { n: "Order placed", s: "Your request" },
  { n: "Label sealed", s: "What you allowed" },
  { n: "Packed", s: "Assistant’s action" },
  { n: "Customs check", s: "Every field checked" },
  { n: "Test delivery", s: "Dry run" },
  { n: "Delivered", s: "" },
];
const TOOL_ICON = { send_email: "envelope", send_money: "coin", schedule_transaction: "coin", share_file: "file", get_webpage: "link", fetch_url: "link", update_password: "key", create_calendar_event: "calendar" };
const TOOL_NOUN = { send_email: "Email", send_money: "Payment", share_file: "File share", get_webpage: "Web request", update_password: "Password change", create_calendar_event: "Calendar invite" };
const DOC_NOUN = { read_file: "file", read_email: "email", search_emails: "inbox search", list_files: "file list", search_contacts: "contacts", get_webpage: "web page" };
const SCEN_ICON = { exfil: "envelope", iban: "coin", benign: "coin", delegation: "calendar" };
const TO_ARGS = ["recipients", "recipient_iban", "recipient", "email", "participants", "url"];

const settings = { planner: "scripted", mask: false };
let CONFIG = {}, SCENARIOS = [], RUN = null;

/* ---------------------------------------------------------------- helpers */
function inr(n) { return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
function plainMoney(s) { return String(s).replace(/INR\s?([\d,]+(\.\d+)?)/g, "₹$1"); }
function hashGroups(h) { return (h || "").slice(0, 12).toUpperCase().replace(/(.{4})(?=.)/g, "$1·"); }
function barcode(seed, el) {
  const hex = (seed || "0").replace(/[^0-9a-f]/gi, "").padEnd(24, "7").slice(0, 32);
  el.innerHTML = [...hex].map((c, i) => { const v = parseInt(c, 16); return `<i style="width:${1 + (v % 3)}px;margin-right:${1 + ((v >> 2) % 3) + (i % 5 === 4 ? 1 : 0)}px"></i>`; }).join("");
}
function announce(t) { $("#live").textContent = t; }
function plainSource(check, fileNames) {
  if (check.verdict !== "ALLOWED") return null;
  const p = (check.provenance || [])[0] || {};
  const doc = DOC_NOUN[p.tool] || "data";
  switch (check.kind) {
    case "USER_LITERAL": return { short: "You typed it", long: "You typed it in your request", quote: p.excerpt };
    case "TRUSTED": return { short: "From your contacts", long: "Found in your saved contacts", quote: p.excerpt };
    case "LOOKUP": return { short: `From the ${doc} you pointed to`, long: `Read from the ${doc} you asked about`, quote: p.excerpt };
    case "DELEGATED": return { short: "From the email you asked me to follow", long: `A task in the ${doc} you told the assistant to follow`, quote: p.excerpt };
    case "DERIVED": return { short: "Worked out from your data", long: "Calculated from values you authorised", quote: p.excerpt };
    default: return { short: "Authorised", long: check.why, quote: p.excerpt };
  }
}
function plainRefusal(check, code) {
  const r = check.why || "";
  if (/instruction-like|hidden|only appears inside/.test(r) || code === "TAINTED_VALUE") return "Only found inside a hidden note, never in your request";
  if (/delegation|outside the organisation/.test(r) || code === "DELEGATION_SCOPE") return "A new outside address; the email you asked me to follow can’t add strangers";
  if (/does not appear/.test(r)) return "Not something you asked for or the assistant had seen";
  return "No authorisation found";
}
function fileName(id) { return RUN?.files[id] || id; }

/* ---------------------------------------------------------------- boot */
async function boot() {
  barcode("31b90881c979a2", $(".home-sample .barcode"));
  wireSettings();
  try {
    [CONFIG, SCENARIOS] = await Promise.all([fetch("/api/config").then((r) => r.json()), fetch("/api/scenarios").then((r) => r.json())]);
  } catch (e) {
    $("#shipments").innerHTML = `<p class="muted" style="padding:20px">Couldn’t reach the demo server. Start it with run_demo.bat and reload.</p>`;
    return;
  }
  if (!CONFIG.has_key) {
    const g = $("#groqOpt"); g.classList.add("disabled"); g.querySelector("input").disabled = true;
    $("#groqHint").textContent = "Needs a GROQ_API_KEY in .env";
  }
  renderShipments();
  $("#backBtn").onclick = () => { location.hash = ""; };
  $("#homeLink").onclick = (e) => { e.preventDefault(); location.hash = ""; };
  $("#verifyBtn").onclick = verify;
  addEventListener("hashchange", route);
  route();
}

function wireSettings() {
  const btn = $("#settingsBtn"), pop = $("#settingsPop");
  btn.onclick = (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); };
  document.addEventListener("click", (e) => { if (!pop.contains(e.target) && e.target !== btn) { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.hidden = true; });
  pop.querySelectorAll("input[name=planner]").forEach((r) => r.onchange = () => { settings.planner = r.value; $("#modeTag").textContent = r.closest("label").querySelector("b").textContent; });
  $("#maskChk").onchange = (e) => { settings.mask = e.target.checked; };
}

function renderShipments() {
  $("#shipments").innerHTML = SCENARIOS.map((s, i) => `
    <button class="ship" data-id="${esc(s.id)}">
      <div class="ship-top">
        <span class="ship-ico">${icon(SCEN_ICON[s.id] || "parcel")}</span>
        <span class="tag ${s.benign ? "safe" : "risk"}">${s.benign ? "Ordinary" : "Note in box"}</span>
      </div>
      <h3>${esc(s.title)}</h3>
      <p class="ship-q">“${esc(s.query)}”</p>
      <p>${s.benign ? "A normal bill. Nothing hidden. WARRANT should not get in the way." : esc(plainMoney(s.attack.replace(/^A |^The /, (m) => m)))}</p>
      <div class="ship-foot"><span class="mono muted">SHP-0${i + 1}</span><span class="go">Track it ${icon("arrow")}</span></div>
    </button>`).join("");
  document.querySelectorAll(".ship").forEach((b) => b.onclick = () => { location.hash = b.dataset.id; });
}

function route() {
  const id = location.hash.slice(1);
  const s = SCENARIOS.find((x) => x.id === id);
  RUN?.es?.close();
  $("#s-home").hidden = !!s;
  $("#s-run").hidden = !s;
  scrollTo({ top: 0 });
  if (s) start(s);
}

/* ---------------------------------------------------------------- run */
async function start(s) {
  RUN = { s, lanes: {}, files: {}, notes: [], verdicts: {}, done: new Set(), runId: null, hash: null, gates: [], es: null };
  $("#runTag").textContent = s.tag;
  $("#runTitle").textContent = s.title;
  $("#runQuery").textContent = `“${s.query}”`;
  $("#summary").hidden = true; $("#summary").innerHTML = "";
  $("#hist").innerHTML = `<li class="hist-empty muted">Events appear here as the protected assistant works.</li>`;
  $("#verifyBtn").disabled = true; $("#verifyOut").textContent = ""; $("#verifyOut").className = "mono muted";
  $("#techBody").innerHTML = "";
  renderDoc(s.source);
  for (const ln of ["undefended", "warrant"]) {
    const el = $(`#lane-${ln}`);
    el.querySelector("[data-parcels]").innerHTML = "";
    el.querySelector("[data-dest]").innerHTML = "";
    const seal = el.querySelector("[data-seal]"); if (seal) { seal.hidden = true; seal.innerHTML = ""; }
    setPill(el, "Waiting", "");
    RUN.lanes[ln] = { name: ln, el, parcels: {}, q: Promise.resolve(), directives: [] };
  }

  let url;
  if (settings.planner === "replay") {
    const tr = await fetch(`/api/traces?scenario=${s.id}`).then((r) => r.json()).catch(() => []);
    const pick = tr.find((t) => !!t.mask === settings.mask) || tr[0];
    if (pick) url = `/api/replay?trace=${encodeURIComponent(pick.name)}&speed=1`;
  }
  url = url || `/api/run?scenario=${s.id}&planner=${settings.planner === "groq" ? "groq" : "scripted"}&mask=${settings.mask ? 1 : 0}`;
  const es = new EventSource(url);
  RUN.es = es;
  const me = RUN;
  es.onmessage = (m) => { if (RUN !== me) return; const ev = JSON.parse(m.data); if (ev.type === "end") es.close(); else onEvent(ev); };
  es.onerror = () => { es.close(); };
}

function lane(ev) { return RUN.lanes[ev.lane]; }
function chain(L, fn) { L.q = L.q.then(fn).catch((e) => console.error(e)); return L.q; }
function setPill(el, text, cls) { const p = el.querySelector("[data-status]"); p.textContent = text; p.className = `pill mono ${cls}`; }

function onEvent(ev) {
  const L = lane(ev);
  switch (ev.type) {
    case "hello": $("#techBody").dataset.planner = ev.planner; $("#techBody").dataset.mask = ev.mask; break;
    case "run_start": chain(L, async () => setPill(L.el, "Reading", "transit")); break;
    case "contract": RUN.runId = ev.run_id; RUN.hash = ev.contract.hash; chain(L, () => showSeal(L, ev)); break;
    case "tool_result": onResult(L, ev); break;
    case "tool_call":
      if (ev.effect !== "READ") chain(L, () => packParcel(L, ev));
      break;
    case "shadow": chain(L, async () => { const p = L.parcels[ev.id]; if (p) p.shadow = ev; }); break;
    case "gate": if (ev.effect !== "READ") { RUN.gates.push(ev); chain(L, () => customs(L, ev)); } break;
    case "audit": chain(L, async () => addHistory(ev.record)); break;
    case "verdict": RUN.verdicts[ev.lane] = ev; chain(L, () => laneVerdict(L, ev)); break;
    case "lane_done": chain(L, async () => { RUN.done.add(ev.lane); if (ev.lane === "warrant" && RUN.runId) $("#verifyBtn").disabled = false; if (RUN.done.size === 2) showSummary(); }); break;
    case "error": chain(L, async () => setPill(L.el, "Error", "bad")); break;
  }
}

function onResult(L, ev) {
  for (const m of (ev.text || "").matchAll(/^\s*(f\d+)\s*\|\s*(\S+)/gm)) RUN.files[m[1]] = m[2].split("/").pop();
  const dirs = (ev.spans || []).filter((s) => s.label === "DIRECTIVE").map((s) => s.text);
  if (dirs.length) { L.directives.push(...dirs); chain(L, () => markNotes(dirs)); }
  // look the parcel up inside the chain: its tool_call may still be queued for animation
  chain(L, () => { const p = L.parcels[ev.id]; return p && !ev.refused ? deliver(L, p, ev) : null; });
}

/* ---------------------------------------------------------------- document with notes */
function renderDoc(src) {
  $("#doc").hidden = !src;
  if (!src) return;
  $("#docTitle").textContent = (src.kind === "email" ? "Email · " : "File · ") + src.title;
  $("#docBody").textContent = src.text;
  RUN.docText = src.text;
  RUN.noteShown = false;
  $("#docNoteText").textContent = RUN.s.benign
    ? "An ordinary bill. “Please pay by Friday” is a normal human request, not a hidden order. Nothing should be held."
    : "The assistant opens this before it packs anything. Watch for a highlighted note.";
}
async function markNotes(dirs) {
  if (RUN.noteShown || !RUN.docText) return;
  const t = RUN.docText;
  // spans arrive in document order, so search forward from the previous match:
  // a short span like a signature must not match an earlier, innocent occurrence
  const ranges = [];
  let cur = 0;
  for (const d of dirs) {
    const s = d.trim(); if (s.length < 3) continue;
    const i = t.indexOf(s, cur); if (i >= 0) { ranges.push([i, i + s.length]); cur = i + s.length; }
  }
  if (!ranges.length) return;
  RUN.noteShown = true;
  ranges.sort((a, b) => a[0] - b[0]);
  let html = "", pos = 0, lastEnd = -999;
  for (const [a, b] of ranges) {
    if (a < pos) continue;
    html += esc(t.slice(pos, a));
    const tag = a - lastEnd > 40 ? `<span class="nf">not from you</span>` : "";
    html += `<mark>${esc(t.slice(a, b))}</mark>${tag}`;
    pos = b; lastEnd = b;
  }
  html += esc(t.slice(pos));
  $("#docBody").innerHTML = html;
  const marks = [...$("#docBody").querySelectorAll("mark")];
  marks[0]?.scrollIntoView({ block: "nearest" });
  for (const m of marks) { m.classList.add("on"); await sleep(35); }
  $("#docNoteText").innerHTML = `The <span class="hl">highlighted</span> text is a note someone slipped into the box. It is written as orders to the assistant, but it came from the ${RUN.s.source?.kind === "file" ? "file" : "email"}, not from you. Watch what each assistant does with it.`;
  announce("A suspicious note was found in the document.");
}

/* ---------------------------------------------------------------- seal (contract) */
async function showSeal(L, ev) {
  const seal = L.el.querySelector("[data-seal]");
  const c = ev.contract;
  const allowed = (c.allowed_effects || []).filter((e) => e !== "READ").map((e) => ({ EGRESS: "send emails", PAY: "make payments", WRITE: "create events", FORM: "fill forms", SHARE: "share files" }[e] || e.toLowerCase())).join(", ");
  seal.innerHTML = `<div class="barcode"></div><div><span class="smallcaps">Label sealed · Tracking no.</span><div><b class="mono">${hashGroups(c.hash)}</b> · allowed: ${esc(allowed || "reading only")}</div></div>`;
  barcode(c.hash, seal.querySelector(".barcode"));
  seal.hidden = false;
  setPill(L.el, "Label sealed", "transit");
  addHistory({ ts: Date.now() / 1000, type: "sealed", hash: c.hash });
  await sleep(220);
}

/* ---------------------------------------------------------------- parcel */
function labelRows(fn, args) {
  const a = args || {};
  const list = (v) => (Array.isArray(v) ? v : [v]).filter((x) => x != null && x !== "");
  if (fn === "send_email") {
    const att = list(a.attachments).map(fileName);
    return { to: list(a.recipients).join(", "), toMono: true, contents: `${a.subject || "(no subject)"}${att.length ? ` · ${att.length} attachment${att.length > 1 ? "s" : ""}: ${att.join(", ")}` : ""}` };
  }
  if (fn === "send_money" || fn === "schedule_transaction") return { to: a.recipient_iban || a.recipient, toMono: true, contents: `${inr(a.amount)} · ${a.subject || ""}` };
  if (fn === "share_file") return { to: a.email, toMono: true, contents: fileName(a.file_id) };
  if (fn === "create_calendar_event") return { to: list(a.participants).join(", "), toMono: true, contents: `${a.title || "Event"} · ${a.date || a.start_time || ""}` };
  if (fn === "update_password") return { to: "Your account", contents: "New password" };
  if (a.url) return { to: a.url, toMono: true, contents: "Web request" };
  const k = Object.keys(a)[0];
  return { to: k ? String(a[k]) : "-", toMono: true, contents: TOOL_NOUN[fn] || fn };
}
function isBadValue(L, args) {
  const vals = JSON.stringify(args || {}).toLowerCase();
  return L.directives.some((d) => (d.match(/[\w.+-]+@[\w-]+\.[\w.]+|[A-Z]{2}\d{2}[A-Z0-9]{10,}/gi) || []).some((v) => vals.includes(v.toLowerCase()) && !(RUN.s.query.toLowerCase().includes(v.toLowerCase()))));
}

async function packParcel(L, ev) {
  const W = L.name === "warrant";
  const r = labelRows(ev.function, ev.args);
  const p = { id: ev.id, fn: ev.function, args: ev.args, rows: r, bad: isBadValue(L, ev.args) };
  const el = document.createElement("div");
  el.className = "parcel" + (p.bad && !W ? " bad" : "");
  el.innerHTML = `
    <div class="p-top">
      <div class="p-ico" title="${esc(TOOL_NOUN[p.fn] || p.fn)}">${icon(TOOL_ICON[p.fn] || "parcel")}</div>
      <div class="label">
        <div class="label-head mono"><span>${esc((TOOL_NOUN[p.fn] || "Parcel").toUpperCase())}</span><span>${W && RUN.hash ? "TRK " + hashGroups(RUN.hash) : "NO TRACKING LABEL"}</span></div>
        <div class="field" data-f="from"><span class="smallcaps">From</span><span>Your AI assistant</span><svg class="mk"></svg></div>
        <div class="field" data-f="to"><span class="smallcaps">To</span><span class="${r.toMono ? "v-mono" : ""}">${esc(r.to)}</span><svg class="mk"></svg></div>
        <div class="field" data-f="contents"><span class="smallcaps">Contents</span><span>${esc(r.contents)}</span><svg class="mk"></svg></div>
        <div class="field auth ${W ? "" : "none"}" data-f="auth"><span class="smallcaps">Authorised by</span><span>${W ? "Waiting for customs" : "Not checked"}</span><svg class="mk"></svg></div>
        <div class="perfo"></div>
        <div class="barcode"></div>
      </div>
    </div>
    <div class="route">
      <svg class="line" viewBox="0 0 100 4" preserveAspectRatio="none"><line class="base" x1="0" y1="2" x2="100" y2="2" vector-effect="non-scaling-stroke"/><line class="prog" x1="0" y1="2" x2="100" y2="2" pathLength="100" stroke-dasharray="100" stroke-dashoffset="100" vector-effect="non-scaling-stroke"/></svg>
      <div class="cps">${STAGES.map((s, i) => `<div class="cp" data-i="${i}"><span class="dot"></span><span class="cp-l"><b>0${i + 1}</b>${esc(s.n)}</span></div>`).join("")}</div>
      <div class="truck">${icon(TOOL_ICON[p.fn] || "parcel")}</div>
    </div>
    <p class="p-status"></p>`;
  p.el = el;
  barcode(W ? RUN.hash : "0000", el.querySelector(".barcode"));
  if (!W) el.querySelector(".barcode").style.opacity = ".18";
  L.el.querySelector("[data-parcels]").appendChild(el);
  L.parcels[p.id] = p;
  setPill(L.el, "In transit", "transit");
  await moveTo(p, 0, "done");
  if (W) await moveTo(p, 1, "done"); else mark(p, 1, "skip");
  await moveTo(p, 2, "done");
  status(p, `<b>Packed.</b> The assistant wants to send this.`);
  if (!W) { mark(p, 3, "skip"); mark(p, 4, "skip"); status(p, `<b>No customs.</b> Ordinary assistants send whatever they pack.`); }
}
function mark(p, i, cls) { const cp = p.el.querySelector(`.cp[data-i="${i}"]`); cp.classList.remove("done", "skip", "fail", "okend"); cp.classList.add(cls); }
function status(p, html, cls = "") { const s = p.el.querySelector(".p-status"); s.innerHTML = html; s.className = "p-status " + cls; }
async function moveTo(p, i, cls) {
  const pct = (i / (STAGES.length - 1)) * 100;
  p.el.querySelector(".truck").style.left = pct + "%";
  p.el.querySelector(".prog").style.strokeDashoffset = String(100 - pct);
  await sleep(260);
  if (cls) mark(p, i, cls);
  await sleep(60);
}
function setMk(row, ok) {
  const mk = row.querySelector(".mk");
  mk.outerHTML = `<svg class="mk ${ok ? "y" : "n"}" aria-label="${ok ? "passed" : "failed"}"><use href="${SPRITE}#${ok ? "check" : "cross"}"/></svg>`;
  requestAnimationFrame(() => row.querySelector(".mk").classList.add("on"));
  if (!ok) row.classList.add("flag");
}
function stamp(p, cls, text, small) {
  const lab = p.el.querySelector(".label");
  lab.querySelector(".stamp")?.remove();
  const s = document.createElement("div");
  s.className = `stamp ${cls} thump`;
  s.innerHTML = `${esc(text)}${small ? `<small>${esc(small)}</small>` : ""}`;
  lab.appendChild(s);
}

async function customs(L, g) {
  const p = L.parcels[g.id];
  if (!p) return;
  p.gate = g;
  await moveTo(p, 3);
  status(p, `<b>At customs.</b> Checking every field against what you asked for.`);
  const lab = p.el.querySelector(".label");
  lab.style.setProperty("--h", lab.offsetHeight + "px");
  lab.classList.add("scan");
  await sleep(160);
  const checks = g.checks || [];
  const toChecks = checks.filter((c) => TO_ARGS.includes(c.arg));
  const otherChecks = checks.filter((c) => !TO_ARGS.includes(c.arg));
  const toOk = toChecks.every((c) => c.verdict === "ALLOWED");
  const otherOk = otherChecks.every((c) => c.verdict === "ALLOWED");
  const rows = p.el.querySelectorAll(".field");
  const res = [true, toOk, otherOk, g.decision === "ALLOWED"];
  const auth = p.el.querySelector('[data-f="auth"] span:nth-child(2)');
  const firstBad = checks.find((c) => c.verdict !== "ALLOWED");
  const src = toChecks.map((c) => plainSource(c)).find(Boolean) || otherChecks.map((c) => plainSource(c)).find(Boolean);
  auth.textContent = firstBad ? "Nobody. " + plainRefusal(firstBad, g.code) : (src ? src.short : "Nothing sensitive on this label");
  for (let i = 0; i < rows.length; i++) { setMk(rows[i], res[i]); await sleep(80); }
  lab.classList.remove("scan");
  if (g.decision !== "ALLOWED") {
    mark(p, 3, "fail");
    p.el.querySelector(".route").classList.add("bad");
    const what = firstBad && TO_ARGS.includes(firstBad.arg) ? `recipient ${firstBad.value}` : `${firstBad?.arg === "attachments" ? "file " + fileName(firstBad.value) : firstBad?.value ?? "this parcel"}`;
    stamp(p, "held", "Held", `No authorisation for ${what}`);
    status(p, `<b>Held at customs.</b> ${esc(firstBad ? plainRefusal(firstBad, g.code) : "Not authorised")}. Returned to sender.`, "bad");
    setPill(L.el, "Parcel held", "bad");
    announce(`Parcel held at customs: no authorisation for ${what}.`);
    p.held = true;
    return;
  }
  mark(p, 3, "done");
  await moveTo(p, 4, "done");
  const sh = p.shadow;
  status(p, `<b>Test delivery passed.</b> ${sh && sh.diff ? `Dry run changed ${sh.diff.length} thing${sh.diff.length === 1 ? "" : "s"}, all within what you allowed.` : "Dry run looked fine."}`);
}

async function deliver(L, p, ev) {
  const W = L.name === "warrant";
  if (!W) { await moveTo(p, 3); await moveTo(p, 4); }
  await moveTo(p, 5, p.bad && !W ? "fail" : "okend");
  const routeEl = p.el.querySelector(".route");
  routeEl.classList.add(p.bad && !W ? "bad" : "ok");
  if (p.bad && !W) {
    p.el.querySelector(".route").classList.remove("ok");
    stamp(p, "held", "Delivered to attacker", p.rows.to);
    status(p, `<b>Delivered.</b> Nobody checked who asked for this.`, "bad");
  } else {
    stamp(p, "ok", "Delivered");
    status(p, W ? `<b>Delivered.</b> Every field traced back to you.` : `<b>Delivered.</b>`, "ok");
  }
  await flyToTray(L, p);
}

async function flyToTray(L, p) {
  const dest = L.el.querySelector("[data-dest]");
  const ico = TOOL_ICON[p.fn] === "coin" ? "wallet" : TOOL_ICON[p.fn] === "envelope" ? "inbox" : TOOL_ICON[p.fn] || "parcel";
  const bad = p.bad && L.name !== "warrant";
  const tray = document.createElement("div");
  tray.className = "tray" + (bad ? " bad" : "");
  const where = ico === "wallet" ? "Paid into" : ico === "inbox" ? "Inbox" : "Received by";
  tray.innerHTML = `${icon(ico)}<span><b>${bad ? (ico === "wallet" ? "Paid to attacker" : "Attacker’s " + where.toLowerCase()) : where}</b><span class="mono">${esc(p.rows.to)}</span></span>`;
  tray.style.visibility = "hidden";
  dest.appendChild(tray);
  if (!REDUCED) {
    const from = p.el.querySelector(".truck").getBoundingClientRect();
    const to = tray.querySelector("svg").getBoundingClientRect();
    const f = document.createElement("div");
    f.className = "flyer"; f.innerHTML = icon(TOOL_ICON[p.fn] || "parcel");
    if (bad) f.style.color = "var(--bad)";
    f.style.left = from.left + "px"; f.style.top = from.top + "px";
    document.body.appendChild(f);
    const dx = to.left - from.left, dy = to.top - from.top;
    // coins drop (straight down, then across); envelopes fly in an arc
    const kf = TOOL_ICON[p.fn] === "coin"
      ? [{ transform: "translate(0,0)" }, { transform: `translate(${dx}px, ${dy - 24}px)`, offset: .7 }, { transform: `translate(${dx}px, ${dy}px) scale(.8)`, opacity: .6 }]
      : [{ transform: "translate(0,0) rotate(0)" }, { transform: `translate(${dx * .6}px, ${dy * .4 - 30}px) rotate(-8deg)`, offset: .5 }, { transform: `translate(${dx}px, ${dy}px) rotate(0) scale(.8)`, opacity: .6 }];
    await f.animate(kf, { duration: 380, easing: "cubic-bezier(.2,.7,.2,1)" }).finished.catch(() => {});
    f.remove();
  }
  tray.style.visibility = "";
  tray.classList.add("land");
}

/* ---------------------------------------------------------------- verdicts & summary */
async function laneVerdict(L, v) {
  if (L.name === "undefended") setPill(L.el, v.attack_succeeded ? "Sent to attacker" : v.utility ? "All delivered" : "Done", v.attack_succeeded ? "bad" : "ok");
  else {
    const held = Object.values(L.parcels).filter((p) => p.held).length;
    setPill(L.el, held ? `${held} held · rest delivered` : v.utility ? "All delivered" : "Done", v.attack_succeeded ? "bad" : "ok");
  }
  if (!Object.keys(L.parcels).length) L.el.querySelector("[data-parcels]").innerHTML = `<p class="muted" style="padding:18px 0;margin:0">The assistant didn’t send anything.</p>`;
}

function showSummary() {
  const U = RUN.verdicts.undefended || {}, W = RUN.verdicts.warrant || {};
  const sc = RUN.s;
  const wl = RUN.lanes.warrant;
  const held = Object.values(wl.parcels).filter((p) => p.held);
  const cost = U.attack_succeeded ? plainMoney(U.attack_detail).replace(/ paid to attacker IBAN .*/, " sent to attacker").replace(/^Data sent to/, "Files sent to") : null;
  let big;
  if (sc.benign) big = `<div class="big">0 false alarms<small>${esc(W.utility_detail || "")}. A normal “please pay by Friday” is not treated as an attack.</small></div>`;
  else if (cost) big = `<div class="big">${esc(cost.split(":")[0].replace(/^Files sent to /, "Files leaked"))}<small>That’s what the ordinary assistant sent. ${held.length ? `WARRANT held ${held.length} parcel${held.length > 1 ? "s" : ""} at customs` : "WARRANT’s assistant never packed the bad parcel"} and still did your task.</small></div>`;
  else big = `<div class="big">Nothing leaked<small>Neither assistant sent anything to the attacker this time.</small></div>`;

  const traceParcels = held.length ? held : Object.values(wl.parcels);
  const rows = traceParcels.flatMap((p) => (p.gate?.checks || []).map((c) => {
    const ok = c.verdict === "ALLOWED";
    const src = ok ? plainSource(c) : null;
    const field = TO_ARGS.includes(c.arg) ? "To" : c.arg === "attachments" ? "Attachment" : c.arg === "amount" ? "Amount" : c.arg;
    const val = c.arg === "attachments" ? fileName(c.value) : c.arg === "amount" ? inr(c.value) : c.value;
    return `<div class="trace-row ${ok ? "ok" : "bad"}">
      <span class="smallcaps">${esc(field)}</span>
      <div><div class="verdict">${icon(ok ? "check" : "cross")}<span class="mono">${esc(val)}</span></div>
      <div class="src">${ok ? esc(src.long) + (src.quote ? `: <q>${esc(src.quote.slice(0, 120))}</q>` : "") : esc(plainRefusal(c, p.gate.code)) + "."}</div></div></div>`;
  }));
  const whyTitle = held.length ? "Why was this held?" : "Why was this delivered?";
  $("#summary").innerHTML = `
    <div>
      <p class="kicker mono">Result</p>
      ${big}
      <div class="score">
        <div><span class="smallcaps">Ordinary assistant</span><div class="n ${U.attack_succeeded ? "bad" : "ok"}">${esc(plainMoney(U.attack_succeeded ? U.attack_detail : U.utility_detail || ""))}</div></div>
        <div><span class="smallcaps">Protected by WARRANT</span><div class="n ${W.attack_succeeded ? "bad" : "ok"}">${esc(plainMoney(W.utility_detail || ""))}</div></div>
      </div>
      <div class="actions">
        <button class="btn" id="anotherBtn">Try another shipment ${icon("arrow")}</button>
        <button class="btn ghost" id="replayBtn">Replay</button>
      </div>
    </div>
    <div class="why">
      <h3>${whyTitle}</h3>
      <p class="muted" style="margin:0 0 14px">Customs traces every field on the label back to where it came from.</p>
      <div class="trace">${rows.join("") || `<div class="trace-row"><span></span><span class="muted">No labels to trace.</span></div>`}</div>
    </div>`;
  $("#summary").hidden = false;
  $("#anotherBtn").onclick = () => { location.hash = ""; };
  $("#replayBtn").onclick = () => start(sc);
  renderTech();
  announce("Run finished. " + (cost ? cost : "Nothing reached the attacker."));
}

/* ---------------------------------------------------------------- history & verify */
function addHistory(rec) {
  const ol = $("#hist");
  ol.querySelector(".hist-empty")?.remove();
  const tm = new Date((rec.ts || Date.now() / 1000) * 1000).toLocaleTimeString("en-GB", { hour12: false });
  let cls = "", what;
  if (rec.type === "sealed") what = `<b>Label sealed</b><span class="mono">Tracking no. ${hashGroups(rec.hash)}</span>`;
  else if (rec.type === "decision") {
    const ok = rec.decision === "ALLOWED";
    if (rec.effect === "READ") what = `<b>Opened</b><span>${esc(DOC_NOUN[rec.tool] || rec.tool)}</span>`;
    else {
      cls = ok ? "ok" : "bad";
      const r = labelRows(rec.tool, typeof rec.args === "object" ? rec.args : {});
      what = `<b>${ok ? "Cleared customs" : "Held at customs"}: ${esc(TOOL_NOUN[rec.tool] || rec.tool)}</b><span class="mono">to ${esc(r.to)}</span>`;
    }
  } else if (rec.type === "answer") what = `<b>Reply checked</b><span>Unauthorised details removed from the reply</span>`;
  else if (rec.type === "confirmation") what = `<b>You confirmed</b><span class="mono">${esc(rec.value)}</span>`;
  else what = `<b>${esc(rec.type)}</b>`;
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<time>${tm}</time><div class="what">${what}</div>`;
  ol.appendChild(li);
}

async function verify() {
  const out = $("#verifyOut");
  out.textContent = "Checking…"; out.className = "mono muted";
  try {
    const r = await fetch(`/api/audit/verify?run_id=${encodeURIComponent(RUN.runId)}`);
    if (!r.ok) throw new Error(r.status === 404 ? "History file not found (replays don’t keep one)" : "Server error");
    const v = await r.json();
    if (v.ok) { out.textContent = `Verified: ${v.executed_side_effects} delivered parcel${v.executed_side_effects === 1 ? "" : "s"}, all match the sealed label`; out.className = "mono ok"; }
    else { out.textContent = `Problem: ${(v.problems || []).join("; ")}`; out.className = "mono bad"; }
  } catch (e) { out.textContent = e.message; out.className = "mono bad"; }
}

function renderTech() {
  const tb = $("#techBody");
  const g = RUN.gates.map((x) => `${x.tool}: ${x.decision} ${x.code}${(x.checks || []).map((c) => `\n    ${c.arg}=${c.value} → ${c.verdict}${c.kind ? " (" + c.kind + ")" : ""}`).join("")}`).join("\n");
  tb.innerHTML = `<dl>
    <dt>Planner</dt><dd>${esc(tb.dataset.planner)} · directive masking ${tb.dataset.mask === "true" ? "on" : "off"}</dd>
    <dt>Run id (audit log)</dt><dd>${esc(RUN.runId || "-")}</dd>
    <dt>Intent Contract hash</dt><dd>${esc(RUN.hash || "-")}</dd>
    <dt>Warrant Gate decisions</dt><dd>${esc(g || "-")}</dd>
    <dt>Mapping</dt><dd>Label sealed = ContractCompiler · Customs = WarrantGate · Test delivery = shadow execution · Suspicious note = DIRECTIVE span · Tracking history = audit_logs/&lt;run_id&gt;.jsonl</dd>
  </dl>`;
}

boot();

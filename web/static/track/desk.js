/* WARRANT Track, live workspace: the real Playground agent (/api/play/*) on a persistent Acme
   workspace. You type any request, add your own emails, files and web pages, and every turn's
   side effects are tracked as parcels, from the same pipeline events as the scenarios.
   Loaded after track.js and uses its helpers. */
"use strict";

const fx = (x) => plainMoney(x.text.charAt(0).toLowerCase() + x.text.slice(1));
const DESK = { sid: null, tab: "emails", world: null, busy: false, seen: new Set(), turn: 0 };
const SID_KEY = "warrant-track-sid";
const TABS = [["emails", "Emails"], ["files", "Files"], ["web", "Web"], ["contacts", "Contacts"], ["bank", "Payments"], ["calendar", "Calendar"], ["sent", "Sent"]];
const OPENABLE = new Set(["emails", "files", "web", "sent"]);
const SUGGESTIONS = [
  "Pay the electricity bill in bill-oct-2026.txt.",
  "Summarise the latest invoice email from Acme Supplies and send the summary to priya@acme.test.",
  "Do the tasks in David's latest email.",
  "Summarise https://acmesupplies.test/support for me.",
  "Check the email from Mumbai Water about my bill and pay it.",
];

function storeGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function storeSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } }

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.detail || `Server error ${r.status}`); e.status = r.status; throw e; }
  return data;
}

/* ---------------------------------------------------------------- open */
async function openDesk() {
  RUN = { desk: true, s: { id: "workspace", query: "", source: null, benign: false }, lanes: {}, files: {}, notes: [], verdicts: {}, done: new Set(), runId: null, hash: null, gates: [], abort: null };
  $("#desk").hidden = false;
  $("#runTag").textContent = "Live workspace";
  $("#runTitle").textContent = "Your assistant, tracked";
  $("#runQuery").textContent = "Ask for anything below. Each email, payment or share it sends shows up as a parcel.";
  $("#summary").hidden = true; $("#summary").innerHTML = "";
  $("#hist").innerHTML = `<li class="hist-empty muted">Events appear here as the protected assistant works.</li>`;
  $("#verifyBtn").disabled = true; $("#verifyOut").textContent = ""; $("#verifyOut").className = "mono muted";
  $("#techBody").innerHTML = "";
  $("#doc").hidden = true;
  $("#docNoteText").textContent = "Open any email, file or web page from your workspace to read it here. If the assistant reads something with a hidden note, it is highlighted here.";
  resetLanes();
  setLanes($("#compareChk").checked ? ["undefended", "warrant"] : ["warrant"]);
  for (const L of Object.values(RUN.lanes)) L.el.querySelector("[data-parcels]").innerHTML = `<p class="muted" style="padding:18px 0;margin:0">Nothing sent yet.</p>`;
  wireDesk();
  if (!CONFIG.has_key) {
    warn("The live assistant needs a model: start Ollama (ollama serve) with " + (CONFIG.local_model || "qwen2.5:7b") + " pulled, or add a Groq key to .env, then restart the server. The scenarios on the home page still work offline.");
    $("#sendBtn").disabled = true;
  }
  try {
    DESK.sid = storeGet(SID_KEY);
    if (DESK.sid) await refreshWorld().catch(() => (DESK.sid = null));
    if (!DESK.sid) { DESK.sid = (await api("/api/play/session", {})).sid; storeSet(SID_KEY, DESK.sid); DESK.seen.clear(); await refreshWorld(); }
  } catch (e) { warn("Couldn’t start a workspace: " + e.message); }
}

function warn(text) { const w = $("#deskWarn"); w.textContent = text || ""; w.hidden = !text; }
function setState(text) { $("#deskState").textContent = text || ""; }

let deskWired = false;
function wireDesk() {
  if (deskWired) return;
  deskWired = true;
  $("#chips").innerHTML = SUGGESTIONS.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
  $("#chips").querySelectorAll(".chip").forEach((c) => c.onclick = () => { $("#msg").value = c.textContent; $("#msg").focus(); });
  $("#composer").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#msg").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  $("#compareChk").onchange = (e) => { if (!DESK.busy) setLanes(e.target.checked ? ["undefended", "warrant"] : ["warrant"]); };
  $("#wsTabs").innerHTML = TABS.map(([k, n]) => `<button type="button" class="ws-tab" role="tab" data-k="${k}" aria-selected="${k === DESK.tab}">${n}<i></i></button>`).join("");
  $("#wsTabs").querySelectorAll(".ws-tab").forEach((t) => t.onclick = () => { DESK.tab = t.dataset.k; renderWorld(); });
  $("#addBtn").onclick = () => { $("#addForm").hidden = !$("#addForm").hidden; };
  $("#addCancel").onclick = () => { $("#addForm").hidden = true; };
  $("#addForm").querySelectorAll("input[name=kind]").forEach((r) => r.onchange = syncAddKind);
  $("#addForm").onsubmit = (e) => { e.preventDefault(); addData(); };
  $("#resetBtn").onclick = resetWorkspace;
}

/* ---------------------------------------------------------------- workspace browser */
async function refreshWorld() {
  const w = await api(`/api/play/world?sid=${encodeURIComponent(DESK.sid)}`);
  const first = !DESK.world;
  DESK.world = w.workspace;
  if (first || !DESK.seen.size) for (const [k] of TABS) for (const it of DESK.world[k] || []) DESK.seen.add(k + ":" + it.id);
  renderWorld();
  return w;
}

function renderWorld() {
  const w = DESK.world || {};
  $("#wsTabs").querySelectorAll(".ws-tab").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.k === DESK.tab));
    t.querySelector("i").textContent = (w[t.dataset.k] || []).length;
  });
  const items = w[DESK.tab] || [];
  $("#wsList").innerHTML = items.length ? items.map((it) => {
    const fresh = !DESK.seen.has(DESK.tab + ":" + it.id) ? " new" : "";
    const inner = `<b>${esc(it.title)}</b><span>${esc(it.sub)}</span>`;
    return `<li class="${fresh}">${OPENABLE.has(DESK.tab) ? `<button type="button" data-id="${esc(it.id)}">${inner}</button>` : `<div class="row">${inner}</div>`}</li>`;
  }).join("") : `<li><div class="row"><span>Nothing here yet.</span></div></li>`;
  $("#wsList").querySelectorAll("button[data-id]").forEach((b) => b.onclick = () => openItem(DESK.tab, b.dataset.id));
}

async function openItem(kind, id) {
  try {
    const it = await api(`/api/play/item?sid=${encodeURIComponent(DESK.sid)}&kind=${kind}&id=${encodeURIComponent(id)}`);
    DESK.seen.add(kind + ":" + id);
    RUN.noteShown = false;
    renderDoc({ kind: kind === "web" ? "web" : kind === "files" ? "file" : "email", title: it.title, text: it.text });
    $("#docNoteText").textContent = "This is what the assistant would read. Text written as orders to an AI is data, not permission: only your own request can authorise who receives anything.";
    $("#doc").scrollIntoView({ block: "nearest", behavior: REDUCED ? "auto" : "smooth" });
    renderWorld();
  } catch (e) { warn(e.message); }
}

function syncAddKind() {
  const k = $("#addForm").querySelector("input[name=kind]:checked").value;
  $("#addForm").querySelectorAll("[data-k]").forEach((el) => { el.hidden = el.dataset.k !== k; });
}

async function addData() {
  const f = $("#addForm"), out = $("#addOut");
  const val = (n) => f.querySelector(`[name=${n}]`).value.trim();
  const kind = f.querySelector("input[name=kind]:checked").value;
  const body = { sid: DESK.sid, kind, sender: val("sender"), subject: val("subject"), name: val("name"), folder: val("folder") || "/uploads", url: val("url"), content: f.querySelector("[name=content]").value };
  const file = $("#upFile").files[0];
  if (file) {
    if (file.size > 3_000_000) { out.textContent = "File too large (max 3 MB)"; return; }
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    body.data_b64 = btoa(bin); body.filename = file.name;
  }
  if (!file && !body.content.trim()) { out.textContent = "Paste some text or choose a file"; return; }
  out.textContent = "Adding…";
  try {
    const r = await api("/api/play/add", body);
    out.textContent = `Added (${r.chars.toLocaleString()} characters)${r.note ? ". " + r.note : ""}`;
    f.reset(); syncAddKind();
    DESK.tab = { email: "emails", file: "files", web: "web" }[kind];
    await refreshWorld();
  } catch (e) { out.textContent = e.message; }
}

async function resetWorkspace() {
  if (DESK.busy) return;
  if (!confirm("Reset the workspace? Everything sent and every item you added will be cleared.")) return;
  try {
    await api("/api/play/reset", { sid: DESK.sid });
    DESK.seen.clear(); DESK.world = null; DESK.turn = 0;
    await refreshWorld();
    openDesk();
  } catch (e) { warn(e.message); }
}

/* ---------------------------------------------------------------- one turn */
async function send() {
  const msg = $("#msg").value.trim();
  if (!msg || DESK.busy || !DESK.sid) return;
  const compare = $("#compareChk").checked;
  const names = compare ? ["undefended", "warrant"] : ["warrant"];
  DESK.busy = true; DESK.turn += 1;
  $("#sendBtn").disabled = true; $("#msg").disabled = true;
  warn(""); setState(`Working on ${liveModelName()}… this can take a minute or two`);
  // per-turn state; parcels of earlier turns stay on the cards
  Object.assign(RUN, { s: { ...RUN.s, query: msg }, done: new Set(), verdicts: {}, gates: [], noteShown: false, expect: names.length, turnEffects: {} });
  setLanes(names);
  $("#summary").hidden = true;
  for (const ln of ["undefended", "warrant"]) {
    const L = RUN.lanes[ln];
    L.parcels = {}; L.directives = [];
    const box = L.el.querySelector("[data-parcels]");
    box.querySelector(":scope > p.muted")?.remove();
    if (!names.includes(ln)) continue;
    const head = document.createElement("div");
    head.className = "turn-head";
    head.innerHTML = `<span class="smallcaps">Request ${DESK.turn}</span><q>${esc(msg)}</q>`;
    box.appendChild(head);
    L.turnBox = head;
    setPill(L.el, "Reading", "transit");
  }
  $(".lanes").scrollIntoView({ block: "start", behavior: REDUCED ? "auto" : "smooth" });

  const ctrl = new AbortController();
  RUN.abort = ctrl;
  const me = RUN;
  try {
    const r = await fetch("/api/play/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sid: DESK.sid, message: msg, mask: settings.mask, compare }), signal: ctrl.signal });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if (r.status === 404) { storeSet(SID_KEY, ""); DESK.sid = null; }
      throw new Error(d.detail || `Server error ${r.status}`);
    }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line || RUN !== me) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type !== "end") onEvent(ev);
      }
    }
    $("#msg").value = "";
    // the stream ended; if a lane never reported (server error mid-turn), finish the turn anyway
    if (RUN === me && DESK.busy) { await Promise.all(Object.values(RUN.lanes).map((L) => L.q)); if (DESK.busy) { deskSummary(); unlock(); refreshWorld().catch(() => {}); } }
  } catch (e) {
    if (e.name !== "AbortError") { warn(e.message + (DESK.sid ? "" : ". Reload the page to start a new workspace.")); setState(""); }
    unlock();
  }
}

function unlock() {
  DESK.busy = false;
  $("#sendBtn").disabled = !CONFIG.has_key; $("#msg").disabled = false;
}

function deskReply(L, text, isErr = false) {
  if (!text) return;
  const box = L.el.querySelector("[data-parcels]");
  const d = document.createElement("div");
  d.className = "reply" + (isErr ? " err" : "");
  d.innerHTML = `<span class="smallcaps">${isErr ? "Something went wrong" : "Assistant replied"}</span>${esc(plainMoney(text))}`;
  box.appendChild(d);
}

async function deskLaneDone(L, ev) {
  if (!RUN.desk) return;
  const effects = ev.effects || [];
  RUN.turnEffects[L.name] = effects;
  if (L.name === "undefended") markAttacked(L, effects.filter((x) => x.outside).map((x) => x.text));
  const parcels = Object.values(L.parcels);
  const held = parcels.filter((p) => p.held).length, sent = effects.length;
  const outside = effects.filter((x) => x.outside).length;
  if (!parcels.length && !sent) L.turnBox?.insertAdjacentHTML("afterend", `<p class="muted" style="margin:10px 0 0;font-size:14px">Nothing sent for this request.</p>`);
  const txt = [sent && `${sent} delivered`, held && `${held} held`].filter(Boolean).join(" · ") || "Nothing sent";
  setPill(L.el, txt, outside && L.name === "undefended" ? "bad" : held ? "ok" : sent ? "ok" : "");
  RUN.done.add(L.name);
  if (L.name === "warrant" && RUN.runId) $("#verifyBtn").disabled = false;
  if (RUN.done.size >= RUN.expect) { deskSummary(ev.seconds); unlock(); refreshWorld().catch(() => {}); }
}

function deskSummary(seconds) {
  const wl = RUN.lanes.warrant, ul = RUN.lanes.undefended;
  const held = Object.values(wl.parcels).filter((p) => p.held);
  const wfx = RUN.turnEffects.warrant || [], ufx = RUN.turnEffects.undefended;
  const leaked = (ufx || []).filter((x) => x.outside);
  setState(`Done in ${seconds ?? "?"} s`);
  let big;
  if (held.length && leaked.length) big = `${leaked.length} parcel${leaked.length > 1 ? "s" : ""} stopped<small>The ordinary assistant: ${leaked.map(fx).join("; ")}. WARRANT held ${held.length} at customs.</small>`;
  else if (held.length) big = `${held.length} parcel${held.length > 1 ? "s" : ""} held<small>Customs found no authorisation in your request for ${held.length > 1 ? "these" : "this"} recipient${held.length > 1 ? "s" : ""}.</small>`;
  else if (wfx.length) big = `${wfx.length} delivered<small>Every recipient traced back to you. Sent: ${wfx.map(fx).join("; ")}.</small>`;
  else big = `Nothing sent<small>The assistant only read and answered. Its reply is on the card above.</small>`;

  const traceParcels = held.length ? held : Object.values(wl.parcels);
  const rows = traceParcels.flatMap((p) => (p.gate?.checks || []).map((c) => {
    const ok = c.verdict === "ALLOWED", src = ok ? plainSource(c) : null;
    const field = TO_ARGS.includes(c.arg) ? "To" : c.arg === "attachments" ? "Attachment" : c.arg === "amount" ? "Amount" : c.arg;
    const val = c.arg === "attachments" ? fileName(c.value) : c.arg === "amount" ? inr(c.value) : c.value;
    return `<div class="trace-row ${ok ? "ok" : "bad"}"><span class="smallcaps">${esc(field)}</span>
      <div><div class="verdict">${icon(ok ? "check" : "cross")}<span class="mono">${esc(val)}</span></div>
      <div class="src">${ok ? esc(src.long) + (src.quote ? `: <q>${esc(src.quote.slice(0, 120))}</q>` : "") : esc(plainRefusal(c, p.gate.code)) + "."}</div></div></div>`;
  }));
  $("#summary").innerHTML = `
    <div><p class="kicker mono">Request ${DESK.turn}</p><div class="big">${big}</div>
      <div class="score">
        <div><span class="smallcaps">Ordinary assistant</span><div class="n ${leaked.length ? "bad" : "ok"}">${ufx ? esc(ufx.length ? ufx.map(fx).join("; ") : "Sent nothing") : "Not compared"}</div></div>
        <div><span class="smallcaps">Protected by WARRANT</span><div class="n ok">${esc(wfx.length ? wfx.map(fx).join("; ") : "Sent nothing")}${held.length ? ` · ${held.length} held` : ""}</div></div>
      </div>
      <div class="actions"><button class="btn" id="againBtn">Ask something else ${icon("arrow")}</button></div>
    </div>
    <div class="why"><h3>${held.length ? "Why was this held?" : "Why was this delivered?"}</h3>
      <p class="muted" style="margin:0 0 14px">Customs traces every field on the label back to where it came from.</p>
      <div class="trace">${rows.join("") || `<div class="trace-row"><span></span><span class="muted">No labels to trace this time.</span></div>`}</div></div>`;
  $("#summary").hidden = false;
  $("#againBtn").onclick = () => { $("#composer").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" }); $("#msg").focus(); };
  renderTech();
  announce("Request finished.");
}

/* WARRANT demo UI: vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ICONS = {
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  bot: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M12 8V4H8"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/>',
  brain: '<path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-2.04Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-2.04Z"/>',
  scale: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1ZM2 16l3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/><path d="m16 16-1.9-1.9"/>',
  db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  scroll: '<path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v3h4"/><path d="M19 17V5a2 2 0 0 0-2-2H4M15 8h-5M15 12h-5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  play: '<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4V8z"/>',
  eye: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7l10-5Z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  trend: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  table: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
  plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
  mask: '<path d="M2 12s3-7 10-7c2 0 3.6.5 5 1.3M22 12s-3 7-10 7c-2 0-3.6-.5-5-1.3"/><path d="m2 2 20 20"/>',
  bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
};
const icon = (n, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ""}</svg>`;
const hydrateIcons = (root = document) => $$("[data-icon]", root).forEach((el) => { el.innerHTML = icon(el.dataset.icon); el.removeAttribute("data-icon"); });

const NODES = [
  ["request", "User request", "only source of authority", "user", "trusted"],
  ["contract", "Intent Contract", "frozen + hashed", "file", "trusted"],
  ["planner", "Planner LLM", "reads data, proposes calls", "brain", "untrusted"],
  ["gate", "Warrant Gate", "per-argument warrants", "scale", "trusted"],
  ["shadow", "Shadow exec", "state diff vs envelope", "copy", "trusted"],
  ["commit", "Commit / Refuse", "typed outcome", "check", "trusted"],
  ["auditor", "Span Auditor", "EVIDENCE vs DIRECTIVE", "scan", "prob"],
  ["ledger", "Value Ledger", "provenance of values", "db", "trusted"],
  ["audit", "Audit log", "JSONL, verifiable", "scroll", "trusted"],
];

const S = { scenarios: [], sel: null, cfg: {}, es: null, cards: { undefended: {}, warrant: {} }, directives: { undefended: [], warrant: [] },
  counts: {}, runId: null, ledgerN: 0, auditN: 0 };

/* ---------------- boot ---------------- */
async function boot() {
  hydrateIcons();
  $$(".tab").forEach((t) => t.onclick = () => showPage(t.dataset.page));
  $$(".subtab").forEach((t) => t.onclick = () => showSub(t.dataset.sub));
  $("#peekBtn").onclick = peek;
  $("#verifyBtn").onclick = verify;
  buildFlow();
  const [cfg, scs] = await Promise.all([fetch("/api/config").then((r) => r.json()), fetch("/api/scenarios").then((r) => r.json())]);
  S.cfg = cfg; S.scenarios = scs;
  $("#modelChip").innerHTML = cfg.has_key
    ? `<span class="dot"></span><span>Groq live · ${esc(cfg.planner_model)}</span>`
    : `<span class="dot off"></span><span>No API key · offline scripted planner</span>`;
  buildPicker();
  select(scs[0].id);
  loadBench();
}

function showPage(p) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.page === p));
  $$(".page").forEach((x) => x.classList.toggle("active", x.id === `page-${p}`));
}
function showSub(s) {
  $$(".subtab[data-sub]").forEach((t) => t.classList.toggle("active", t.dataset.sub === s));
  ["contract", "ledger", "audit"].forEach((k) => $(`#sub-${k}`).hidden = k !== s);
}

function buildFlow() {
  $("#flow").innerHTML = NODES.map(([id, t, sub, ic, kind], i) =>
    `${i ? `<div class="arrow">${icon("arrow")}</div>` : ""}<div class="node ${kind}" id="node-${id}"><div class="ic">${icon(ic)}</div><b>${t}</b><span>${sub}</span><div class="cnt" id="cnt-${id}"></div></div>`).join("");
}

function buildPicker() {
  const cards = S.scenarios.map((s) => `<div class="scard" data-id="${s.id}"><span class="tag ${s.benign ? "benign" : ""}">${esc(s.tag)}</span><h3>${esc(s.title)}</h3><p>${esc(s.query)}</p></div>`).join("");
  const planner = S.cfg.has_key ? `<option value="groq">Live: Groq ${esc(S.cfg.planner_model)}</option>` : "";
  $("#picker").innerHTML = cards + `<div class="controls">
      <label>Planner</label>
      <select id="plannerSel">${planner}<option value="scripted">Offline: scripted vulnerable planner</option><option value="replay">Replay latest recorded run</option></select>
      <label class="toggle"><input type="checkbox" id="maskChk" checked> Span Auditor masks injected text</label>
      <label class="toggle" title="Untick to show the planner the raw injection and prove the gate holds on its own"><span class="prov">Untick = stress test: planner sees raw injection, gate must hold alone</span></label>
      <button class="run" id="runBtn">${icon("play")}Run both agents</button></div>`;
  $$(".scard").forEach((c) => c.onclick = () => select(c.dataset.id));
  $("#runBtn").onclick = start;
}

function select(id) {
  S.sel = S.scenarios.find((s) => s.id === id);
  $$(".scard").forEach((c) => c.classList.toggle("active", c.dataset.id === id));
  $("#queryText").textContent = S.sel.query;
  $("#attackLine").className = "attackline" + (S.sel.benign ? " benign" : "");
  $("#attackLine").innerHTML = `${icon(S.sel.benign ? "check" : "alert")}<span>${esc(S.sel.attack)}</span>`;
  $$(".attackline svg").forEach((s) => { s.style.width = "15px"; s.style.height = "15px"; });
}

function peek() {
  const src = S.sel?.source;
  if (!src) return;
  $("#modalTitle").textContent = src.title;
  $("#modalSub").textContent = S.sel.benign ? "Benign content: no attack inside." : "Highlighted: the indirect prompt injection the attacker planted in this data.";
  $("#modalBody").innerHTML = highlightInjection(src.text);
  $("#modal").classList.add("open");
}
function highlightInjection(t) {
  let h = esc(t);
  h = h.replace(/(&lt;INFORMATION&gt;[\s\S]*?&lt;\/INFORMATION&gt;)/g, '<mark class="inj">$1</mark>');
  h = h.replace(/(AI assistants processing[\s\S]*$)/g, '<mark class="inj">$1</mark>');
  return h;
}

/* ---------------- run / replay ---------------- */
function resetUI() {
  for (const lane of ["undefended", "warrant"]) {
    $(`#tl-${lane}`).innerHTML = "";
    $(`#verdict-${lane}`).innerHTML = "";
    $(`#world-${lane}`).innerHTML = '<div class="prov">Running…</div>';
    S.cards[lane] = {}; S.directives[lane] = [];
  }
  S.counts = { planner: 0, allowed: 0, refused: 0, shadow: 0, committed: 0, masked: 0, directive: 0, spans: 0, audit: 0 };
  S.runId = null; S.ledgerN = 0; S.auditN = 0;
  $("#ledgerBody").innerHTML = ""; $("#auditBody").innerHTML = ""; $("#verifyOut").textContent = "";
  $("#sub-contract").innerHTML = '<div class="empty" style="padding:24px"><div class="spinner" style="margin:0 auto 8px"></div>Compiling contract from the user request…</div>';
  $$(".node").forEach((n) => n.classList.remove("active", "flash-red"));
  $$(".node .cnt").forEach((c) => c.textContent = "");
  flash("request");
}

async function start() {
  if (S.es) S.es.close();
  const planner = $("#plannerSel").value, mask = $("#maskChk").checked ? 1 : 0;
  let url;
  if (window.liveStart) window.liveStart();
  if (planner === "replay") {
    const tr = await fetch(`/api/traces?scenario=${S.sel.id}`).then((r) => r.json());
    if (!tr.length) { alert("No recorded run for this scenario yet. Run it live once first."); return; }
    url = `/api/replay?trace=${encodeURIComponent(tr[0].name)}&speed=1`;
  } else {
    url = `/api/run?scenario=${S.sel.id}&planner=${planner}&mask=${mask}`;
  }
  resetUI();
  $("#runBtn").disabled = true;
  ["undefended", "warrant"].forEach((l) => setStatus(l, "running"));
  const es = new EventSource(url);
  S.es = es;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "end") { es.close(); $("#runBtn").disabled = false; if (window.liveHook) window.liveHook(ev); return; }
    try { handle(ev); } catch (e) { console.error(e, ev); }
  };
  es.onerror = () => { es.close(); $("#runBtn").disabled = false; if (window.liveHook) window.liveHook({ type: "end" }); };
}

function setStatus(lane, st) {
  const el = $(`#status-${lane}`);
  el.innerHTML = st === "running" ? '<span class="spinner"></span>running' : st;
}

/* ---------------- pipeline animation ---------------- */
function flash(id, red = false) {
  const n = $(`#node-${id}`);
  if (!n) return;
  n.classList.add(red ? "flash-red" : "active");
  clearTimeout(n._t);
  n._t = setTimeout(() => n.classList.remove("active", "flash-red"), red ? 1400 : 900);
}
function counters() {
  const c = S.counts;
  const set = (id, t) => { const e = $(`#cnt-${id}`); if (e) e.textContent = t; };
  set("planner", c.planner ? `${c.planner} steps` : "");
  set("gate", c.allowed + c.refused ? `✓${c.allowed}  ✗${c.refused}` : "");
  set("shadow", c.shadow ? `${c.shadow} dry-runs` : "");
  set("commit", c.committed + c.refused ? `${c.committed} done · ${c.refused} refused` : "");
  set("auditor", c.spans ? `${c.directive} directive / ${c.spans}` : "");
  set("ledger", c.spans ? `${c.spans} spans` : "");
  set("audit", c.audit ? `${c.audit} records` : "");
}

/* ---------------- event handling ---------------- */
function tl(lane) { return $(`#tl-${lane}`); }
function push(lane, html, cls = "") {
  const d = document.createElement("div");
  d.className = `ev ${cls}`;
  d.innerHTML = html;
  tl(lane).appendChild(d);
  tl(lane).scrollTop = tl(lane).scrollHeight;
  return d;
}

function handle(ev) {
  if (window.liveHook) { try { window.liveHook(ev); } catch (e) { console.error(e); } }
  const lane = ev.lane;
  const W = lane === "warrant";
  switch (ev.type) {
    case "hello":
      if (ev.replay) push("warrant", `<div class="row">${icon("play")}<b>Replaying a recorded live run</b></div>`, "think");
      break;
    case "run_start":
      push(lane, `<div class="row"><b>Run started</b><span class="prov">planner: ${esc(ev.planner)}${W ? ` · masking ${ev.mask ? "on" : "off (stress test)"}` : ""}</span></div>`, "think");
      break;
    case "stage":
      if (W && ev.status === "active") {
        if (ev.stage === "refuse") flash("commit", true); else flash(ev.stage);
      }
      break;
    case "contract": {
      S.runId = ev.run_id;
      const c = ev.contract;
      flash("contract");
      push(lane, `<div class="row">${icon("file")}<b>Intent Contract compiled & frozen</b><span class="prov">${esc(c.compiled_by)}</span></div>
        <div style="margin-top:6px">${effectPills(c.allowed_effects)}</div><div class="hash">sha256 ${esc(c.hash.slice(0, 24))}…</div>`, "contract");
      renderContract(c);
      break;
    }
    case "llm":
      if (W) { S.counts.planner++; counters(); }
      if (ev.text && ev.text.trim() && (!ev.tool_calls || ev.tool_calls.length)) push(lane, `${icon("brain")} ${esc(ev.text)}`, "think");
      break;
    case "tool_call": {
      const hot = (v) => isHot(lane, v);
      const args = Object.entries(ev.args || {}).map(([k, v]) => {
        const s = typeof v === "string" ? v : JSON.stringify(v);
        const short = s.length > 260 ? s.slice(0, 260) + "…" : s;
        return `<span class="k">${esc(k)}:</span> <span class="v ${hot(s) ? "hot" : ""}">${esc(short)}</span>`;
      }).join("\n");
      const card = push(lane, `<div class="row"><span class="fn">${esc(ev.function)}()</span><span class="eff ${ev.effect}">${ev.effect}</span><span class="slot"></span></div>${args ? `<div class="args">${args}</div>` : ""}`);
      S.cards[lane][ev.id] = card;
      break;
    }
    case "gate": {
      const card = S.cards[lane][ev.id];
      if (!card) break;
      if (ev.decision === "ALLOWED") S.counts.allowed++; else S.counts.refused++;
      if (ev.decision === "ALLOWED" && ev.effect !== "READ") S.counts.committed++;
      counters();
      if (ev.effect === "READ" && ev.decision === "ALLOWED") { card.querySelector(".slot").outerHTML = `<span class="stamp READ">READ · OK</span>`; break; }
      card.querySelector(".slot").outerHTML = `<span class="stamp ${ev.decision}">${ev.decision.replace("_", " ")}</span>`;
      const checks = (ev.checks || []).map((c) => {
        const ok = c.verdict === "ALLOWED";
        const kind = ok ? `<span class="kind ${c.kind}">${c.kind}</span>` : `<span class="kind bad">${esc(c.code)}</span>`;
        const p = (c.provenance || [])[0];
        const prov = p ? `<span class="prov">from ${esc(p.tool === "user_request" ? "user request" : p.tool)}${p.span ? ` · span ${esc(p.span)}` : ""}${p.label === "DIRECTIVE" ? " · DIRECTIVE" : ""}</span>` : `<span class="prov">no source</span>`;
        return `<li>${ok ? "✓" : "✗"} <b>${esc(c.arg)}</b> = <span class="val">${esc(String(c.value))}</span> ${kind} ${prov}</li>`;
      }).join("");
      const div = document.createElement("div");
      div.className = `gate ${ev.decision}`;
      div.innerHTML = `<div><span class="code">${esc(ev.code)}</span> · ${esc(ev.reason)}</div>${checks ? `<ul class="checks">${checks}</ul>` : ""}${askLine(ev)}`;
      card.appendChild(div);
      if (ev.decision !== "ALLOWED") flash("gate", true);
      break;
    }
    case "shadow": {
      S.counts.shadow++; counters();
      const card = S.cards[lane][ev.id];
      if (!card) break;
      const lines = (ev.diff || []).slice(0, 4).map((d) => `<div class="${d.op === "add" ? "add" : d.op === "remove" ? "rm" : ""}">${d.op === "add" ? "+" : d.op === "remove" ? "−" : "~"} ${esc(d.path)} ${esc(summ(d.value ?? d.after))}</div>`).join("");
      const div = document.createElement("div");
      div.className = "shadow";
      div.innerHTML = `<div class="prov">shadow run on env copy · ${esc(ev.why)}</div>${lines}`;
      card.appendChild(div);
      break;
    }
    case "tool_result": {
      const card = S.cards[lane][ev.id];
      if (!card) break;
      const dirSpans = (ev.spans || []).filter((s) => s.label === "DIRECTIVE");
      dirSpans.forEach((s) => S.directives[lane].push(s.text));
      if (W) { S.counts.spans += (ev.spans || []).length; S.counts.directive += dirSpans.length; S.counts.masked += ev.masked || 0; counters(); }
      if (ev.refused) {
        card.insertAdjacentHTML("beforeend", `<div class="result refused">${esc(ev.text)}</div><div class="okline">${icon("shield")}Not executed. Typed refusal returned to the planner so it can recover.</div>`);
      } else {
        if (!W) {
          const slot = card.querySelector(".slot");
          if (slot) slot.outerHTML = ev.executed ? `<span class="stamp EXECUTED">EXECUTED</span>` : `<span class="stamp READ">READ</span>`;
        }
        card.insertAdjacentHTML("beforeend", `<div class="result">${renderResult(ev.text || ev.error || "(empty)", dirSpans)}</div>`);
        if (!W && dirSpans.length) card.insertAdjacentHTML("beforeend", `<div class="warnline">${icon("alert")}Injected instruction reached the LLM unfiltered</div>`);
        if (W && ev.masked) card.insertAdjacentHTML("beforeend", `<div class="okline">${icon("mask")}Span Auditor masked ${ev.masked} instruction-like span(s) before the planner saw them</div>`);
        else if (W && dirSpans.length) card.insertAdjacentHTML("beforeend", `<div class="warnline">${icon("alert")}DIRECTIVE flagged (masking off): the planner sees it, but its values carry no warrant</div>`);
      }
      $$("svg", card).forEach((s) => { if (!s.style.width) { s.style.width = "15px"; s.style.height = "15px"; } });
      tl(lane).scrollTop = tl(lane).scrollHeight;
      break;
    }
    case "ledger":
      (ev.added || []).forEach((s) => {
        S.ledgerN++;
        $("#ledgerBody").insertAdjacentHTML("beforeend", `<tr><td class="mono">${esc(s.id)}</td><td class="mono">${esc(s.tool)}${s.trusted ? " ·T" : ""}${s.delegated ? " ·D" : ""}</td><td><span class="lab ${s.label}">${s.label}</span></td><td>${esc(s.text.slice(0, 140))}</td></tr>`);
      });
      break;
    case "audit": {
      S.counts.audit++; counters();
      const r = ev.record;
      $("#auditBody").insertAdjacentHTML("beforeend", `<div class="auditline">{"seq": ${r.seq}, "tool": "${esc(r.tool)}", "decision": <span class="d-${esc(r.decision)}">"${esc(r.decision)}"</span>, "code": "${esc(r.code)}", "contract": "${esc(String(r.contract_hash).slice(0, 12))}…"}</div>`);
      break;
    }
    case "final":
      push(lane, `<div class="row">${icon(W ? "shield" : "bot")}<b>Agent's final answer</b></div><p>${esc(ev.text || "(no text)")}</p>`, "final");
      break;
    case "error":
      push(lane, `<b>Error:</b> ${esc(ev.message)}`, "err");
      break;
    case "verdict":
      renderVerdict(lane, ev);
      break;
    case "answer_guard":
      push(lane, answerGuardHtml(ev), "final");
      break;
    case "perturb": {
      const card = S.cards[lane][ev.id];
      if (card) card.insertAdjacentHTML("beforeend", perturbHtml(ev));
      break;
    }
    case "lane_done":
      setStatus(lane, "done");
      break;
  }
  $$(`#tl-${lane || "warrant"} svg`).forEach((s) => { if (!s.style.width) { s.style.width = "15px"; s.style.height = "15px"; s.style.verticalAlign = "-3px"; } });
}

function isHot(lane, s) {
  if (!s) return false;
  const low = String(s).toLowerCase();
  if (/evil\.test|in66xevl/.test(low)) return true;
  return false;
}
function summ(v) {
  if (v == null) return "";
  if (typeof v === "object") {
    if (v.recipients) return `email to ${v.recipients.join(", ")}${v.attachments?.length ? ` +${v.attachments.length} attachment(s)` : ""}`;
    if (v.recipient_iban) return `INR ${v.amount} to ${v.recipient_iban}`;
    if (v.participants) return `event "${v.title}" with ${v.participants.join(", ")}`;
    if (v.email && v.file_id) return `share ${v.file_id} with ${v.email}`;
    const s = JSON.stringify(v); return s.length > 120 ? s.slice(0, 120) + "…" : s;
  }
  return String(v);
}
function renderResult(text, dirSpans) {
  const ranges = [];
  for (const s of dirSpans) {
    const i = text.indexOf(s.text);
    if (i >= 0) ranges.push([i, i + s.text.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let out = "", pos = 0;
  for (const [a, b] of ranges) {
    if (a < pos) continue;
    out += esc(text.slice(pos, a)) + `<mark class="inj">${esc(text.slice(a, b))}</mark>`;
    pos = b;
  }
  out += esc(text.slice(pos));
  return out.replace(/\[WARRANT: instruction-like text removed from untrusted data \(span ([\w.]+)\)\]/g, '<mark class="masked">masked by Span Auditor · span $1</mark>');
}
/* NEEDS_INPUT is a scored outcome ("asks the user"), not a failure. */
function askLine(ev) {
  if (ev.decision !== "NEEDS_INPUT") return "";
  return `<div class="askline">${icon("alert")}<b>Asks the user.</b> WARRANT paused this action and would show a confirmation card: nothing was executed, and the user decides.</div>`;
}
function answerGuardHtml(ev) {
  const items = (ev.redactions || []).map((r) => `<li><span class="val">${esc(String(r.value))}</span> <span class="prov">${esc(r.type || "")} · ${esc(r.why || "")}</span></li>`).join("");
  return `<div class="row">${icon("shield")}<b>AnswerGuard removed ${(ev.redactions || []).length} unwarranted item(s) from the final answer</b></div><ul class="checks">${items}</ul>`;
}
/* Lane D probe event: {id, verdict, code?, chosen_span, fakes:[{fake, original, span}], runs, extra_calls} */
function perturbHtml(ev) {
  const rows = (ev.fakes || []).map((f) => `<li class="${f.span === ev.chosen_span ? "chosen" : ""}"><span class="val">${esc(f.original)}</span> → <span class="mono">${esc(f.fake)}</span> <span class="prov">span ${esc(f.span || "?")}${f.span === ev.chosen_span ? " · picked by the agent" : ""}</span></li>`).join("");
  return `<div class="perturb"><div class="prov">${icon("scan")} counterfactual relabeling probe · ${esc(ev.verdict || "")}${ev.code ? ` (${esc(ev.code)})` : ""} · ${ev.extra_calls ?? "?"} extra call(s)</div><ul class="checks">${rows}</ul></div>`;
}
function toggleStage() {
  document.body.classList.toggle("stage");
  try { localStorage.setItem("warrantStage", document.body.classList.contains("stage") ? "1" : ""); } catch (e) {}
}
try { if (localStorage.getItem("warrantStage")) document.body.classList.add("stage"); } catch (e) {}

function effectPills(list) {
  const all = ["READ", "WRITE", "EGRESS", "PAY", "BOOK", "DELETE", "ACCOUNT"];
  return all.map((e) => `<span class="pill ${list.includes(e) ? "on" : "off"}">${e}</span>`).join("");
}
function renderContract(c) {
  const slots = (c.slots || []).map((s) => `<div><b class="mono">${esc(s.sink)}</b> ← ${s.kinds.map((k) => `<span class="kind ${k}">${k}</span>`).join(" ")} <span class="prov">${esc(s.note || "")}</span></div>`).join("") || '<span class="prov">defaults per sink type</span>';
  $("#sub-contract").innerHTML = `<dl class="kv">
    <dt>Task</dt><dd>${esc(c.task)}</dd>
    <dt>Allowed effects</dt><dd>${effectPills(c.allowed_effects)}</dd>
    <dt>Slots</dt><dd>${slots}</dd>
    <dt>User literals</dt><dd>${(c.user_literals || []).map((x) => `<span class="pill">${esc(x)}</span>`).join("") || "<span class='prov'>none</span>"}</dd>
    <dt>Delegated sources</dt><dd>${(c.delegated_sources || []).map((x) => `<span class="pill">${esc(x)}</span>`).join("") || "<span class='prov'>none (no delegation)</span>"}</dd>
    <dt>Limits</dt><dd class="mono" style="font-size:12px">egress ≤ ${c.limits.max_egress_actions} · payments ≤ ${c.limits.max_payments}${c.limits.max_total_amount ? ` · total ≤ ${c.limits.max_total_amount}` : ""} · deletes ≤ ${c.limits.max_deletes}${c.limits.internal_domains?.length ? ` · internal: ${c.limits.internal_domains.join(", ")}` : ""}</dd>
    <dt>Compiled by</dt><dd>${esc(c.compiled_by)}</dd>
    <dt>SHA-256</dt><dd class="hash">${esc(c.hash)}</dd></dl>`;
}
function renderVerdict(lane, v) {
  let cls, title, ic;
  if (v.benign) {
    cls = v.utility ? "good" : "meh"; ic = v.utility ? "check" : "alert";
    title = v.utility ? "Task completed · no false alarm" : "Task not completed";
  } else if (v.attack_succeeded) {
    cls = "bad"; ic = "alert"; title = "ATTACK SUCCEEDED";
  } else {
    cls = v.utility ? "good" : "meh"; ic = "shield";
    title = v.utility ? "ATTACK BLOCKED · task completed" : "Attack blocked · task incomplete";
  }
  const detail = v.benign ? v.utility_detail : `${v.attack_detail} · ${v.utility_detail}`;
  $(`#verdict-${lane}`).innerHTML = `<div class="verdict ${cls}">${icon(ic)}<div><b>${title}</b><span>${esc(detail)}</span></div><div class="meta">${v.seconds}s${v.tokens ? ` · ${v.tokens} tok` : ""}</div></div>`;
  renderWorld(lane, v.world);
}
function renderWorld(lane, w) {
  const items = [];
  const evil = (s) => /evil|IN66XEVL/i.test(s);
  (w.sent || []).forEach((m) => { const t = `email → ${m.to.join(", ")}`; items.push(`<div class="witem ${evil(t) ? "evil" : ""}"><b>${esc(t)}</b><br>${esc(m.subject)}${m.attachments.length ? ` · attached: ${esc(m.attachments.join(", "))}` : ""}</div>`); });
  (w.payments || []).forEach((p) => items.push(`<div class="witem ${evil(p.to) ? "evil" : ""}"><b>paid INR ${p.amount.toLocaleString("en-IN")}</b> → ${esc(p.to)}<br>${esc(p.subject)}</div>`));
  (w.shares || []).forEach((s) => items.push(`<div class="witem ${evil(s.email) ? "evil" : ""}"><b>shared ${esc(s.file_id)}</b> with ${esc(s.email)}</div>`));
  (w.events || []).forEach((e) => items.push(`<div class="witem"><b>event</b> ${esc(e.title)} · ${esc(e.date)}<br>${esc(e.participants.join(", "))}</div>`));
  $(`#world-${lane}`).innerHTML = items.join("") || '<div class="prov">No side effects.</div>';
}

async function verify() {
  if (!S.runId) { $("#verifyOut").textContent = "Run the WARRANT agent first."; return; }
  const r = await fetch(`/api/audit/verify?run_id=${S.runId}`).then((x) => x.json());
  $("#verifyOut").className = `verify ${r.ok ? "ok" : "bad"}`;
  $("#verifyOut").innerHTML = r.ok
    ? `✓ Verified run ${esc(r.run_id)}: ${r.executed_side_effects} executed side effect(s), each with a valid warrant under contract ${esc(r.contract_hash.slice(0, 12))}…`
    : `✗ Problems: ${esc(r.problems.join("; "))}`;
}

/* ---------------- benchmark page ---------------- */
const DEF_LABEL = { none: "Undefended", prompt: "Prompt defence", detector: "Detector (auditor only)", warrant_nomask: "WARRANT gate only", warrant: "WARRANT full" };
const DEF_COLOR = { none: "#d96b5f", prompt: "#c99a3d", detector: "#8e8bc4", warrant_nomask: "#57b3b6", warrant: "#0F7C80" };
let BENCH = [];
async function loadBench() {
  BENCH = await fetch("/api/bench").then((r) => r.json());
  const planners = [...new Set(BENCH.map((r) => r.planner))];
  if (!planners.length) {
    $("#benchHero").innerHTML = `<div class="panel" style="grid-column:span 4">No results yet. Run <code>python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace</code>.</div>`;
    return;
  }
  $("#plannerTabs").innerHTML = planners.map((p) => `<button class="subtab" data-pl="${p}">${p === "oracle" ? "Worst case: fully hijacked planner (LLM-free)" : "Live LLM (Groq)"}</button>`).join("");
  $$("#plannerTabs .subtab").forEach((b) => b.onclick = () => renderBench(b.dataset.pl));
  renderBench(planners.includes("groq") ? "groq" : planners[0]);
}
function agg(rows, def, key, weight) {
  const rs = rows.filter((r) => r.defense === def);
  const w = rs.reduce((a, r) => a + r[weight], 0);
  return w ? rs.reduce((a, r) => a + r[key] * r[weight], 0) / w : null;
}
const pct = (x) => (x == null ? "–" : x > 0 && x < 0.01 ? "<1%" : `${Math.round(x * 100)}%`);
/* Rate over attackable pairs only (injection tasks with an empty ground truth excluded); falls back to official. */
function aggAtt(rows, def, key = "asr_attackable") {
  const rs = rows.filter((r) => r.defense === def);
  if (!rs.length || rs.some((r) => r[key] == null)) return agg(rows, def, "asr", "n_attacked_runs");
  const w = rs.reduce((a, r) => a + r.n_attackable_pairs, 0);
  return w ? rs.reduce((a, r) => a + r[key] * r.n_attackable_pairs, 0) / w : null;
}
function renderBench(pl) {
  $$("#plannerTabs .subtab").forEach((b) => b.classList.toggle("active", b.dataset.pl === pl));
  const rows = BENCH.filter((r) => r.planner === pl);
  const defs = ["none", "prompt", "detector", "warrant_nomask", "warrant"].filter((d) => rows.some((r) => r.defense === d));
  const suites = [...new Set(rows.map((r) => r.suite))];
  const nAtt = rows.filter((r) => r.defense === "warrant").reduce((a, r) => a + r.n_attacked_runs, 0);
  $("#benchHero").innerHTML = `
    <div class="stat red"><div class="n">${pct(aggAtt(rows, "none"))}</div><div class="l">Attack success, undefended<br><span class="prov">of attacks that can happen (official ${pct(agg(rows, "none", "asr", "n_attacked_runs"))})</span></div></div>
    <div class="stat teal"><div class="n">${pct(aggAtt(rows, "warrant", "asr_executed_attackable"))}</div><div class="l">Attacks executed, WARRANT<br><span class="prov">official AgentDojo score ${pct(agg(rows, "warrant", "asr", "n_attacked_runs"))}</span></div></div>
    <div class="stat green"><div class="n">${pct(agg(rows, "warrant", "benign_utility", "n_user_tasks"))}</div><div class="l">Benign utility, WARRANT (undefended ${pct(agg(rows, "none", "benign_utility", "n_user_tasks"))})</div></div>
    <div class="stat navy"><div class="n">${nAtt}</div><div class="l">attacked runs · ${suites.length} suites · ${esc(rows[0]?.attack || "")}</div></div>`;
  const legend = defs.map((d) => `<span><i style="background:${DEF_COLOR[d]}"></i>${DEF_LABEL[d]}</span>`).join("");
  $("#legend1").innerHTML = legend; $("#legend2").innerHTML = legend;
  const chart = (key) => suites.map((s) => `<div class="barrow"><div class="name">${s}</div><div class="barset">${defs.map((d) => {
    const r = rows.find((x) => x.suite === s && x.defense === d); if (!r) return "";
    return `<div class="bar ${d}"><i style="width:${Math.max(r[key] * 100, 0.6)}%"></i><em>${pct(r[key])}</em></div>`;
  }).join("")}</div></div>`).join("");
  $("#chartAsr").innerHTML = chart("asr");
  $("#chartUa").innerHTML = chart("utility_under_attack");
  $("#benchTable").innerHTML = `<thead><tr><th>Suite</th><th>Defence</th><th>Model</th><th>Benign utility</th><th>Utility under attack</th><th>ASR (official)</th><th>ASR executed</th><th>Attackable pairs</th><th>ASR attackable</th><th>Asked user</th><th>Tasks</th><th>Attacked runs</th><th>Date</th></tr></thead><tbody>` +
    rows.sort((a, b) => a.suite.localeCompare(b.suite) || defs.indexOf(a.defense) - defs.indexOf(b.defense)).map((r) => `<tr><td>${r.suite}</td><td>${DEF_LABEL[r.defense] || r.defense}</td><td class="mono">${esc(r.model)}</td><td>${pct(r.benign_utility)}</td><td>${pct(r.utility_under_attack)}</td><td><b style="color:${r.asr > 0.1 ? "var(--red)" : "var(--teal)"}">${pct(r.asr)}</b></td><td>${pct(r.asr_executed)}</td><td>${r.n_attackable_pairs ?? "–"}</td><td>${pct(r.asr_attackable)}</td><td>${pct(r.asked_user_rate_attacked)}</td><td>${r.n_user_tasks}</td><td>${r.n_attacked_runs}</td><td class="mono">${esc(r.date)}</td></tr>`).join("") + "</tbody>";
  $("#benchNote").innerHTML = pl === "oracle"
    ? `<b>How to read this:</b> the "fully hijacked planner" replays each task's correct tool calls <i>and</i> executes the attacker's goal the moment it sees the injection, so it is the worst case for any defence that relies on the model resisting. Detector-style masking cannot help here; only the gate can. <b>Official</b> = AgentDojo's own scorer, which counts every <i>proposed</i> call (AgentDojo issue #168): the remaining slack ASR is <code>injection_task_5</code>, blocked by the gate but counted. <b>Executed</b> counts only calls that actually ran. <b>Attackable</b> excludes the 9 injection tasks whose ground truth is empty in the default environment (workspace 6-13, travel 6), which no planner can carry out here. Remaining travel ASR comes from attacks that reuse an already-authorised effect with values that legitimately exist in the data (e.g. booking a different real hotel). Rules were developed on AgentDojo tasks, so a held-out evaluation is still needed.`
    : `<b>Live LLM run</b> on Groq with AgentDojo's <code>important_instructions</code> attack. Subset sizes are limited by the Groq free tier; see the table for exact task counts.`;
}

boot();

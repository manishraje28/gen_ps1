/* WARRANT Workspace: a personal AI workspace over mail, Slack, bank and drive (/api/play/*).
   Pages: Home, Inbox, Tasks, Task (Plan / Progress / Results), Apps, Drive, Slack, Bank, Contacts, Calendar,
   Sent, Attack lab. Every task runs twice on two copies of the same world (ordinary vs WARRANT); each outgoing
   action is a ticket that travels through WARRANT's checkpoints and is stamped where it was stopped. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (id) => `<svg aria-hidden="true"><use href="/static/workspace/sprite.svg#${id}"/></svg>`;
  const LOGO = { gmail: "logo-gmail", slack: "logo-slack", bank: "logo-bank", drive: "logo-drive", calendar: "logo-gcal" };
  const logo = (k) => `<svg class="logo" aria-hidden="true"><use href="/static/workspace/sprite.svg#${LOGO[k]}"/></svg>`;
  const sleep = (ms) => (S.fast ? Promise.resolve() : new Promise((r) => setTimeout(r, ms)));
  const clock = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const ago = (t) => { const s = Math.max(1, Math.round((Date.now() - t) / 1000)); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(t).toLocaleDateString(); };

  const S = { sid: null, world: null, conns: {}, hasKey: true, fast: false, tasks: [], busy: false, force: null, forceQ: null, model: "" };
  const SID_KEY = "warrantWorkspaceSid";
  const store = { get: () => { try { return localStorage.getItem(SID_KEY); } catch (e) { return null; } },
                  set: (v) => { try { v ? localStorage.setItem(SID_KEY, v) : localStorage.removeItem(SID_KEY); } catch (e) {} } };

  /* ---------------- vocabulary ---------------- */
  const CHECKPOINTS = ["Your request", "Who authorised it", "Witness & limits", "Dry run", "Delivered"];
  const CODES = {
    EFFECT_OUT_OF_SCOPE: [0, "You never asked for this kind of action."],
    REPEATED_REFUSALS: [0, "This kind of action was refused several times, so it is locked for this request."],
    INTERNAL_ERROR: [0, "The checkpoint could not verify this action, so it failed safe."],
    TAINTED_VALUE: [1, "The target only appears inside an instruction hidden in the data, not in anything you said."],
    TAINTED_CONTENT: [1, "The message carries a link or address that only came from hidden instructions."],
    TAINTED_DOCUMENT: [1, "This value comes from a document that also contains instructions to the AI, so it cannot vouch for it."],
    TAINTED_SOURCE: [1, "Re-checking showed the assistant copied this value from the injected line."],
    NO_PROVENANCE: [1, "Nobody authorised this value: it is not in your request or in anything the assistant read."],
    SLOT_MISMATCH: [1, "The value comes from a source that is not allowed for this field."],
    SECRET_EGRESS: [1, "This would carry confidential data out, and you did not ask to share that file."],
    DELEGATION_SCOPE: [1, "Delegated tasks may not send to new outside recipients."],
    NEW_PAYEE: [2, "You have never paid this account and nothing independent vouches for it."],
    NEW_RECIPIENT: [2, "This recipient is new and nothing independent vouches for it."],
    NEW_LINK: [2, "This link is new and nothing independent vouches for it."],
    PROBE_INCONCLUSIVE: [2, "The re-check could not tell where this value came from."],
    DECISIVE_UNTRUSTED_SPAN: [2, "A suspicious line in the data decided this choice."],
    ENVELOPE_EXCEEDED: [3, "The dry run went beyond what your request allows (count or amount)."],
  };
  const KIND = { USER_LITERAL: "you typed it", TRUSTED: "your own records", LOOKUP: "data you pointed to", DELEGATED: "a task you delegated", DERIVED: "computed from known numbers", CONFIRMED: "you confirmed it" };
  const FN_ICON = { send_email: "envelope", send_money: "coin", schedule_transaction: "coin", update_scheduled_transaction: "coin", share_file: "file", delete_file: "file", create_calendar_event: "calendar", get_webpage: "globe", post_webpage: "globe", send_direct_message: "hash", send_channel_message: "hash", read_channel_messages: "hash", get_channels: "hash", read_email: "envelope", search_emails: "envelope", list_emails: "inbox", read_file: "file", list_files: "folder", search_contacts: "user", get_balance: "wallet", get_transactions: "wallet", update_password: "key" };
  const FN_LABEL = { send_email: "Send email", send_money: "Payment", share_file: "Share file", delete_file: "Delete file", create_calendar_event: "Calendar invite", send_direct_message: "Slack DM", send_channel_message: "Slack post", get_webpage: "Open web page", post_webpage: "Post to web page" };

  const { DAILY, ATTACKS } = window.WARRANT_FLOWS;
  DAILY.push(
    { icon: "inbox", tag: "safe", title: "Summarise my inbox", text: "What needs my attention today.", q: "Summarise my five latest emails and tell me what needs my attention." },
    { icon: "calendar", tag: "safe", title: "Book a meeting", text: "A 30-minute sync with Priya.", q: "Set up a 30-minute sync with priya@acme.test on Friday at 3pm." });
  const NAV = [
    ["home", "Home", "home"], ["inbox", "Inbox", "inbox", () => (S.world?.emails || []).length], ["tasks", "Tasks", "tasks", () => S.tasks.length],
    ["apps", "Apps", "grid"], ["drive", "Drive", "folder"], ["slack", "Slack", "hash"], ["bank", "Bank", "wallet"],
    ["contacts", "Contacts", "user"], ["calendar", "Calendar", "calendar"], ["sent", "Sent", "send"],
  ];

  /* ---------------- API ---------------- */
  async function api(path, body) {
    const r = await fetch(`/api/play/${path}`, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 3200); }
  async function refreshWorld() { S.world = (await api(`world?sid=${S.sid}`)).workspace; renderNav(); renderRail(); if (route().page !== "task") render(); }
  function setMeta(d) {
    S.hasKey = d.has_key; S.conns = d.connections || {}; S.model = d.planner_model || "";
    $("#modelLine").textContent = `Model: ${S.model}${S.hasKey ? "" : " · no API key"}`;
    renderChips();
  }
  async function newSession() { const d = await api("session", {}); S.sid = d.sid; store.set(S.sid); setMeta(d); S.tasks = []; await refreshWorld(); }
  async function restoreSession(sid) {
    S.sid = sid;
    const h = await api(`history?sid=${sid}`);
    setMeta(h);
    S.world = (await api(`world?sid=${sid}`)).workspace;
    S.fast = true;
    try {
      h.history.forEach((rec, i) => {
        const T = newTask(rec.prompt, rec.compare, rec.ts ? rec.ts * 1000 : Date.now());
        T.replay = { rec, pending: h.pending };
      });
      for (const T of S.tasks) await replayTask(T);
    } finally { S.fast = false; }
  }
  async function replayTask(T) {
    const { rec, pending } = T.replay; delete T.replay;
    for (const ev of rec.events) await handle(T, ev);
    for (const a of rec.approvals || []) { const t = T.tickets[`warrant:${a.id}`]; if (t) applyDecision(T, t, a.approved, { ok: true, effects: [] }); }
    $$(".approve", T.el).forEach((b) => { if (!pending.includes(b.dataset.id)) { T.pending.delete(b.dataset.id); b.remove(); } });
    finishTask(T);
  }

  /* ---------------- chrome ---------------- */
  const SVC = [["gmail", "Gmail", "envelope"], ["slack", "Slack", "hash"], ["bank", "Bank", "wallet"], ["drive", "Drive", "folder"]];
  function renderChips() {
    if (!$("#conns")) return;
    $("#conns").innerHTML = SVC.map(([k, l, ic]) => `<span class="chip ${S.conns[k] === "live" ? "live" : ""}" title="${S.conns[k] === "live" ? "Connected to the real service" : "Demo workspace: add credentials in .env to go live"}"><span class="svc-${k}">${icon(ic)}</span><b>${l}</b><i></i>${S.conns[k] === "live" ? "live" : "demo"}</span>`).join("");
  }
  function pendingCount() { return S.tasks.reduce((n, T) => n + T.pending.size, 0); }
  function renderNav() {
    const cur = route().page === "task" ? "tasks" : route().page;
    const pend = pendingCount();
    $("#navList").innerHTML = NAV.map(([k, l, ic, cnt]) => {
      const n = cnt ? cnt() : null;
      const hot = k === "tasks" && pend;
      return `<a class="nav-a ${cur === k ? "on" : ""}" href="#/${k}">${icon(ic)}${l}${hot ? `<span class="n hot">${pend}</span>` : n ? `<span class="n">${n}</span>` : ""}</a>`;
    }).join("");
    $("#bellDot").hidden = !pend;
    $$(".nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#/${cur}`));
  }

  /* ---------------- router ---------------- */
  function route() { const [page, arg] = (location.hash.replace(/^#\/?/, "") || "home").split("/"); return { page, arg }; }
  window.addEventListener("hashchange", () => { document.body.classList.remove("nav-open"); render(); });
  function render() {
    renderNav();
    const { page, arg } = route();
    const P = $("#page");
    P.style.animation = "none"; void P.offsetWidth; P.style.animation = "";
    const fn = { home: pageHome, inbox: pageInbox, tasks: pageTasks, task: pageTask, apps: pageApps, drive: pageDrive, slack: pageSlack, bank: pageBank, contacts: pageContacts, calendar: pageCalendar, sent: pageSent, lab: pageLab }[page] || pageHome;
    fn(P, arg);
  }

  /* ---------------- pages ---------------- */
  function pageHome(P) {
    const w = S.world || {};
    const h = new Date().getHours(), part = h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
    const recent = S.tasks.slice().reverse().slice(0, 6);
    const flagged = (w.emails || []).filter((e) => e.suspicious);
    const quick = [DAILY.find((d) => d.icon === "inbox"), DAILY[0], DAILY.find((d) => d.icon === "hash"), DAILY.find((d) => d.icon === "calendar")].filter(Boolean);
    P.innerHTML = `
      <div class="dhead"><p class="greet">Good ${part}, Arjun</p><time>${new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "short", year: "numeric" })}</time></div>
      <div class="stats">
        ${stat("gmail", "", (w.emails || []).length, "Emails in your inbox", flagged.length ? `${flagged.length} flagged` : "Summarise", flagged.length ? "#/inbox/attention" : "do:inbox")}
        ${stat("slack", "", (w.slack || []).length, "Slack updates", "View", "#/slack")}
        ${stat("calendar", "", (w.calendar || []).length, "Upcoming events", "View", "#/calendar")}
        ${stat("drive", "", (w.files || []).length, "Files in your drive", "View", "#/drive")}
      </div>
      <section class="card hero-ask">
        <h1>What can I help you get done?</h1>
        <p>Ask anything. I can read your mail, Slack, bank and drive, and take action for you.</p>
        <form class="compose" id="ntForm" autocomplete="off">
          <textarea id="ntText" rows="3" placeholder="Ask WARRANT to do something… e.g. “Summarise my latest emails and tell me what needs my attention.”"></textarea>
          <div class="compose-bar">
            <button class="tool-ic" type="button" id="ntAdd" title="Add data (email, file or web page)">${icon("clip")}</button>
            <a class="tool-ic" href="#/inbox" title="Gmail">${logo("gmail")}</a>
            <a class="tool-ic" href="#/slack" title="Slack">${logo("slack")}</a>
            <a class="tool-ic" href="#/bank" title="Bank">${logo("bank")}</a>
            <a class="tool-ic" href="#/drive" title="Drive">${logo("drive")}</a>
            <button class="tool-ic add" type="button" id="ntAttackBtn" title="Try an attack">${icon("plus")}</button>
            <span class="muted small" id="ntNote"></span>
            <span class="grow"></span>
            <label class="mode">${icon("spark")}<select id="ntMode" aria-label="Mode"><option value="compare">Compare</option><option value="solo">WARRANT only</option></select></label>
            <button class="send-wide" id="ntSend" type="submit">${icon("send")}Send</button>
          </div>
        </form>
        <div class="qcards">${quick.map((f) => `<button class="qcard" type="button" data-q="${esc(f.title)}">${icon(f.icon)}<b>${esc(f.title)}</b><span>${esc(f.text)}</span></button>`).join("")}
          <button class="qcard more" type="button" id="qMore">${icon("dots")}<span>More</span></button></div>
      </section>
      <div class="card"><div class="card-h"><h2>Recent activity</h2><a class="link" href="#/tasks">View all ${icon("arrow")}</a></div>
        <ul class="acts">${recent.length ? recent.map(actRow).join("") : flagged.map((e) => `<li class="act" data-mail="${esc(e.id)}"><span class="ico2 ask">${icon("note")}</span><div class="t"><b>${esc(e.title)}</b><span>${esc(e.sender)} · contains instructions aimed at an AI</span></div><time>${esc(e.date)}</time><span class="pill2 ask">Hidden instructions</span><span></span></li>`).join("") || `<li class="empty-note">No tasks yet. Ask WARRANT above.</li>`}</ul></div>`;
    $$(".stat2", P).forEach((b) => b.onclick = () => { const go = b.dataset.go; if (go === "do:inbox") startTask(DAILY.find((d) => d.icon === "inbox").q); else location.hash = go; });
    $$(".act[data-task]", P).forEach((r) => r.onclick = () => location.hash = `#/task/${r.dataset.task}`);
    $$(".act[data-mail]", P).forEach((r) => r.onclick = () => openItem("emails", r.dataset.mail));
    $$(".qcard[data-q]", P).forEach((b) => b.onclick = () => pickFlow(DAILY.find((d) => d.title === b.dataset.q)));
    $("#qMore").onclick = () => { $("#attackPick").hidden = false; };
    $("#ntAttackBtn").onclick = () => { $("#attackPick").hidden = false; };
    $("#ntMode").value = $("#optCompare").checked ? "compare" : "solo";
    $("#ntMode").onchange = () => { $("#optCompare").checked = $("#ntMode").value === "compare"; };
    $("#ntForm").onsubmit = (e) => { e.preventDefault(); startTask($("#ntText").value); };
    $("#ntText").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ntForm").requestSubmit(); } });
    if (S.pendingFill) { $("#ntText").value = S.pendingFill; S.pendingFill = null; setTimeout(() => $("#ntText").focus(), 30); }
    if (S.busy) $("#ntSend").disabled = true;
  }
  function stat(ic, cls, n, label, go, href) { return `<button type="button" class="card stat2" data-go="${href}"><span class="${cls}">${logo(ic)}</span><span class="n">${n}</span><span class="chev">${icon("arrow")}</span><span class="l">${esc(label)}</span><span class="go">${esc(go)}</span></button>`; }
  function taskStatus(T) {
    if (!T.done) return ["run", "Running"];
    if (T.pending.size) return ["ask", "Needs approval"];
    if (T.stats.warrant.held) return ["shield", `${T.stats.warrant.held} blocked`];
    if (T.error) return ["bad", "Failed"];
    return ["ok", "Completed"];
  }
  function taskSummary(T) {
    const w = T.stats.warrant, parts = [];
    if (!T.done) return "Working…";
    if (w.held) parts.push(`${w.held} held by WARRANT`);
    if (w.ask) parts.push(`${w.ask} waited for you`);
    if (w.ok) parts.push(`${w.ok} delivered`);
    const leaks = (T.effects.undefended || []).filter((e) => e.outside).length;
    if (T.compare && leaks) parts.push(`unprotected assistant leaked ${leaks}`);
    return parts.join(" · ") || "Read-only, nothing sent";
  }
  function taskIcon(T) { const f = T.firstAction || T.firstRead; return FN_ICON[f] || "spark"; }
  function actRow(T) {
    const [cls, label] = taskStatus(T);
    return `<li class="act" data-task="${T.id}"><span class="ico2 ${cls === "shield" ? "ask" : cls === "ok" ? "ok" : ""}">${icon(taskIcon(T))}</span><div class="t"><b>${esc(T.prompt)}</b><span>${esc(taskSummary(T))}</span></div><time>${ago(T.ts)}</time><span class="pill2 ${cls}">${esc(label)}</span><span class="more2">${icon("dots")}</span></li>`;
  }

  function avatar(name) {
    const s = String(name || "?"), colors = ["#D97757", "#5B7BD5", "#4F9D69", "#B0743C", "#8C6BC8", "#C2557A"];
    const c = colors[[...s].reduce((a, ch) => a + ch.charCodeAt(0), 0) % colors.length];
    return `<span class="av" style="background:${c}">${esc(s.replace(/@.*/, "").slice(0, 2).toUpperCase())}</span>`;
  }

  function pageInbox(P, arg) {
    const all = S.world?.emails || [];
    const tabs = [["all", "All", all], ["attention", "Hidden instructions", all.filter((e) => e.suspicious)], ["outside", "From outside", all.filter((e) => !/@acme\.test$/i.test(e.sender))]];
    const cur = tabs.find((t) => t[0] === arg) || tabs[0];
    P.innerHTML = `<div class="page-h"><h1>Inbox</h1><div style="display:flex;gap:8px"><button class="btn ghost sm" id="addMail" type="button">${icon("plus")}Add an email</button><button class="btn sm" id="sumMail" type="button">${icon("spark")}Summarise inbox</button></div></div>
      <div class="card"><div class="tabs">${tabs.map(([k, l, list]) => `<a class="tab2 ${cur[0] === k ? "on" : ""}" href="#/inbox/${k}">${l}<span class="c">${list.length}</span></a>`).join("")}</div>
      <ul class="rows">${cur[2].map((e) => `<li class="mrow" data-id="${esc(e.id)}"><span class="dot2" ${e.suspicious ? "" : 'style="visibility:hidden"'}></span>${avatar(e.sender)}<span class="from">${esc(e.sender)}</span>
        <div class="subj"><b>${esc(e.title)}</b><span>${esc(e.preview)}</span></div><div class="meta"><span>${esc(e.date)}</span>${e.suspicious ? `<span class="pill2 ask">Hidden instructions</span>` : ""}</div></li>`).join("") || `<li class="empty-note">Nothing here.</li>`}</ul></div>`;
    $$(".mrow", P).forEach((r) => r.onclick = () => openItem("emails", r.dataset.id));
    $("#addMail").onclick = () => openPlant("email");
    $("#sumMail").onclick = () => startTask(DAILY.find((d) => d.icon === "inbox").q);
  }

  function pageTasks(P, arg) {
    const all = S.tasks.slice().reverse();
    const is = (k) => (T) => taskStatus(T)[0] === k;
    const tabs = [["all", "All", all], ["running", "Running", all.filter(is("run"))], ["approval", "Needs approval", all.filter(is("ask"))],
      ["blocked", "Attacks blocked", all.filter((T) => T.done && T.stats.warrant.held)], ["done", "Completed", all.filter((T) => T.done && !T.pending.size)]];
    const cur = tabs.find((t) => t[0] === arg) || tabs[0];
    P.innerHTML = `<div class="page-h"><h1>Tasks</h1><button class="btn brand sm" id="newT" type="button">${icon("plus")}New task</button></div>
      <div class="card"><div class="tabs">${tabs.map(([k, l, list]) => `<a class="tab2 ${cur[0] === k ? "on" : ""}" href="#/tasks/${k}">${l}<span class="c">${list.length}</span></a>`).join("")}</div>
      <ul class="rows">${cur[2].map((T) => {
        const [cls, label] = taskStatus(T);
        return `<li class="trow" data-id="${T.id}"><span class="ico2 ${cls === "shield" || cls === "ask" ? "ask" : cls === "ok" ? "ok" : ""}">${icon(taskIcon(T))}</span>
          <div class="t"><b>${esc(T.prompt)}</b><span>${esc(taskSummary(T))} · ${ago(T.ts)}</span></div>
          <div class="bar2 ${T.done ? "" : "run"}"><i style="width:${T.done ? 100 : 40}%"></i></div><span class="pill2 ${cls}">${esc(label)}</span><span class="more muted">${icon("dots")}</span></li>`;
      }).join("") || `<li class="empty-note">No tasks here yet.</li>`}</ul></div>`;
    $$(".trow", P).forEach((r) => r.onclick = () => location.hash = `#/task/${r.dataset.id}`);
    $("#newT").onclick = () => openNewTask();
  }

  function pageTask(P, arg) {
    const T = S.tasks.find((t) => String(t.id) === String(arg));
    if (!T) { P.innerHTML = `<a class="crumb" href="#/tasks">${icon("back")}Tasks</a><div class="empty-note">This task no longer exists.</div>`; return; }
    T.tab = T.tab || "progress";
    const [cls, label] = taskStatus(T);
    P.innerHTML = `<a class="crumb" href="#/tasks">${icon("back")}Tasks</a>
      <div class="task-h"><h1>${esc(T.prompt)}</h1><span class="pill2 ${cls}" id="tStatus">${esc(label)}</span></div>
      <div class="task-sub">${new Date(T.ts).toLocaleString()} · ${T.compare ? "compared with an unprotected assistant" : "WARRANT only"}</div>
      ${T.done ? "" : `<div class="task-run" id="tRun"><i></i></div>`}
      <div class="card"><div class="tabs">${[["plan", "Plan"], ["progress", "Progress"], ["results", "Results"]].map(([k, l]) => `<button class="tab2 ${T.tab === k ? "on" : ""}" data-tab="${k}" type="button">${l}</button>`).join("")}</div>
      <div class="task-body" id="tBody"></div></div>`;
    $$(".tab2[data-tab]", P).forEach((b) => b.onclick = () => { T.tab = b.dataset.tab; pageTask(P, arg); });
    S.viewing = T;
    paintTaskBody(T);
  }
  function paintTaskBody(T) {
    const body = $("#tBody");
    if (!body || S.viewing !== T || route().page !== "task") return;
    const st = $("#tStatus"); if (st) { const [c, l] = taskStatus(T); st.className = `pill2 ${c}`; st.textContent = l; }
    if (T.done) $("#tRun")?.remove();
    if (T.tab === "progress") { if (T.el.parentNode !== body) { body.innerHTML = ""; body.appendChild(T.el); } return; }
    if (T.tab === "plan") {
      const all = ["READ", "WRITE", "EGRESS", "PAY", "BOOK", "DELETE", "ACCOUNT"];
      body.innerHTML = `<div class="plan-h">What your request allows</div>
        <div class="eff-row">${T.contract ? all.map((e) => `<span class="eff ${T.contract.allowed_effects.includes(e) ? "on" : ""}">${e}</span>`).join("") : `<span class="muted small">compiling the contract from your words…</span>`}</div>
        <div class="plan-h">Steps (protected assistant)</div>
        <ul class="plan">${T.plan.map((s) => `<li><span class="st ${s.st}">${s.st === "ok" ? icon("check") : s.st === "bad" ? icon("cross") : s.st === "ask" ? icon("pause") : ""}</span><div><b>${esc(s.title)}</b><span class="muted">${esc(s.sub || "")}</span></div><time>${s.t ? clock(s.t) : ""}</time></li>`).join("") || `<li><span class="st run"></span><div><b>Planning…</b><span class="muted">The assistant is reading your request.</span></div><time></time></li>`}</ul>`;
      return;
    }
    const eff = (lane) => T.effects[lane] || [];
    const effList = (lane) => eff(lane).length ? `<ul class="plan">${eff(lane).map((e) => `<li><span class="st ${e.outside ? "bad" : "ok"}">${icon(e.outside ? "cross" : "check")}</span><div><b>${esc(e.text)}</b><span class="muted">${e.outside ? "reached an outside party" : esc(e.detail || "")}</span></div><time></time></li>`).join("")}</ul>` : `<div class="muted small">Nothing left the workspace.</div>`;
    body.innerHTML = `<div class="results-grid">
        ${T.compare ? `<div><div class="plan-h">Unprotected assistant: what happened</div>${effList("undefended")}<div class="plan-h">Its reply</div><div class="reply">${esc(T.finals.undefended || "(no reply)")}</div></div>` : ""}
        <div><div class="plan-h">With WARRANT: what happened</div>${effList("warrant")}
          ${T.stats.warrant.stopped.length ? `<div class="plan-h">Stopped by WARRANT</div><ul class="plan">${T.stats.warrant.stopped.map((x) => `<li><span class="st ${x.ask ? "ask" : "bad"}">${icon(x.ask ? "pause" : "shield")}</span><div><b>${esc(x.text)}</b><span class="muted">${x.ask ? "paused for your approval" : "held at the checkpoint"}</span></div><time></time></li>`).join("")}</ul>` : ""}
          <div class="plan-h">Its reply</div><div class="reply">${esc(T.finals.warrant || "(no reply yet)")}</div></div>
      </div>
      <div class="plan-h">Checkpoint log</div>
      <ul class="tlog">${T.log.map((l) => `<li class="${l.cls}"><time>${clock(l.t)}</time><div><b>${esc(l.what)}</b><br><span class="muted">${esc(l.why || "")}</span></div></li>`).join("") || `<li><time></time><span class="muted">No outgoing actions yet.</span></li>`}</ul>`;
  }

  function pageApps(P) {
    const how = { gmail: "Add Gmail OAuth credentials (docs/integrations/GMAIL.md) to read and send from a dedicated demo account.", slack: "Set SLACK_BOT_TOKEN for a throwaway demo workspace (docs/integrations/SLACK.md).", bank: "Payments run in a simulated bank; Stripe test mode is available (docs/integrations/PAYMENTS.md).", drive: "Files live in the demo drive; uploads via Add data." };
    P.innerHTML = `<div class="page-h"><h1>Apps</h1></div><div class="card glist">${SVC.map(([k, l, ic]) => `<div class="row"><span class="ico2">${logo(k)}</span><div><b>${k === "drive" ? "Google Drive" : l}</b><span class="muted">${esc(how[k])}</span></div><span class="pill2 ${S.conns[k] === "live" ? "ok" : "mute"}">${S.conns[k] === "live" ? "Connected" : "Demo"}</span></div>`).join("")}</div>`;
  }
  function listPage(P, title, items, ic, opts = {}) {
    P.innerHTML = `<div class="page-h"><h1>${esc(title)}</h1>${opts.add ? `<button class="btn ghost sm" id="addIt" type="button">${icon("plus")}${esc(opts.add[1])}</button>` : ""}</div>
      <div class="card glist">${items.map((it) => `<div class="row ${opts.kind ? "click" : ""}" data-id="${esc(it.id)}"><span class="ico2 ${it.suspicious ? "ask" : ""}">${icon(it.suspicious ? "note" : ic)}</span><div><b>${esc(it.title)}</b><span class="muted">${esc(it.sub || "")}</span></div>${it.suspicious ? `<span class="pill2 ask">Hidden instructions</span>` : it.confidential ? `<span class="pill2 mute">Confidential</span>` : "<span></span>"}</div>`).join("") || `<div class="empty-note">Nothing here yet.</div>`}</div>`;
    if (opts.kind) $$(".row", P).forEach((r) => r.onclick = () => openItem(opts.kind, r.dataset.id));
    if (opts.add) $("#addIt").onclick = () => openPlant(opts.add[0]);
  }
  const pageDrive = (P) => listPage(P, "Drive", [...(S.world?.files || []), ...(S.world?.web || []).map((w) => ({ ...w, sub: `web page · ${w.sub}`, kindWeb: 1 }))], "file", { kind: "files", add: ["file", "Add a file"] });
  const pageSlack = (P) => listPage(P, "Slack", S.world?.slack || [], "hash", { kind: "slack" });
  const pageBank = (P) => listPage(P, "Bank", S.world?.bank || [], "coin");
  const pageContacts = (P) => listPage(P, "Contacts", S.world?.contacts || [], "user");
  const pageCalendar = (P) => listPage(P, "Calendar", S.world?.calendar || [], "calendar");
  const pageSent = (P) => listPage(P, "Sent", S.world?.sent || [], "send", { kind: "sent" });
  function pageLab(P) {
    P.innerHTML = `<div class="page-h"><div><h1>Attack lab</h1><p class="muted" style="margin:6px 0 0">Each scenario plants poisoned data in both workspaces, then fills in an ordinary request. Watch the unprotected assistant act on it and WARRANT stop it.</p></div></div>
      <div class="lab-grid">${ATTACKS.map((f, i) => `<button class="card lab-card" type="button" data-i="${i}"><div class="flow-top"><span class="ico">${icon(f.icon)}</span><span class="tag ${f.tag}">${f.tag === "gap" ? "known gap" : "attack"}</span></div><h3>${esc(f.title)}</h3><p>${esc(f.text)}</p><span class="q">“${esc(f.q)}”</span></button>`).join("")}</div>`;
    $$(".lab-card", P).forEach((b) => b.onclick = () => openNewTask(ATTACKS[+b.dataset.i]));
  }

  /* ---------------- reader, add data, new task panel ---------------- */
  function highlightInstructions(text) {
    return esc(text).replace(/((?:&lt;!--[\s\S]*?--&gt;)|(?:[^\n]*\b(?:AI assistants?|assistants?|IMPORTANT|ignore (?:all |your )?previous|before anything else|User:|Assistant:)\b[^\n]*))/gi, "<mark>$1</mark>");
  }
  async function openItem(kind, id) {
    try {
      const d = await api(`item?sid=${S.sid}&kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`);
      const html = highlightInstructions(d.text);
      $("#rdKind").textContent = `What the assistant sees · ${kind}`;
      $("#rdTitle").textContent = d.title;
      $("#rdBody").innerHTML = html;
      $("#rdFlag").hidden = !html.includes("<mark>");
      $("#rdAsk").onclick = () => { $("#reader").hidden = true; openNewTask({ q: kind === "emails" ? `Summarise the email "${d.title}" for me.` : `Summarise ${d.title} for me.` }); };
      $("#reader").hidden = false;
    } catch (e) { toast(e.message); }
  }
  function openPlant(kind) {
    $("#plKind").value = kind || "email"; $("#plKind").onchange(); $("#plStatus").textContent = ""; $("#plant").hidden = false;
  }
  function suggRow(f, set, i) {
    return `<button class="sg" type="button" data-set="${set}" data-i="${i}"><span class="ico2">${icon(f.icon)}</span><div><b>${esc(f.title)}</b><span class="muted">${esc(f.text)}</span></div><span class="tag ${f.tag}">${f.tag === "safe" ? "everyday" : f.tag === "gap" ? "known gap" : "attack"}</span></button>`;
  }
  function renderQuick() {
    $("#ntAttack").innerHTML = ATTACKS.map((f, i) => suggRow(f, "a", i)).join("");
    $$("#ntAttack .sg").forEach((b) => b.onclick = () => { $("#attackPick").hidden = true; pickFlow(ATTACKS[+b.dataset.i]); });
  }
  function promptBox() { return $("#ntText"); }
  function openNewTask(flow) {
    if (flow) { pickFlow(flow); return; }
    S.force = null;
    if (route().page !== "home") location.hash = "#/home";
    setTimeout(() => promptBox()?.focus(), 80);
  }
  async function pickFlow(f) {
    if (route().page !== "home") { S.pendingFill = f.q || ""; location.hash = "#/home"; await new Promise((r) => setTimeout(r, 60)); }
    const note = (t) => { const n = $("#ntNote"); if (n) n.textContent = t; else if (t) toast(t); };
    if (f.plant) {
      note("Planting the poisoned data…");
      try {
        for (const it of f.plant) await api("add", { sid: S.sid, kind: it.kind, sender: it.sender || "", subject: it.subject || "", name: it.name || "", folder: it.folder || "/uploads", url: it.url || "", content: it.content, filename: "", data_b64: "" });
        await refreshWorld();
        note(`Planted in your ${f.plant[0].kind === "email" ? "inbox" : "drive"}.`);
        f.plant = null;  // plant once per page load
      } catch (e) { note(`Could not plant: ${e.message}`); return; }
    }
    S.force = f.attack || null; S.forceQ = f.q;
    const box = promptBox(); box.value = f.q || ""; box.focus();
  }

  /* ---------------- tasks ---------------- */
  function newTask(prompt, compare, ts) {
    const id = S.tasks.length + 1;
    const el = document.createElement("section");
    el.className = "turn";
    el.innerHTML = `<div class="contract" hidden></div><div class="lanes ${compare ? "" : "one"}">${compare ? laneShell("undefended") : ""}${laneShell("warrant")}</div>`;
    const T = { id, prompt, compare, ts: ts || Date.now(), el, tickets: {}, effects: {}, finals: {}, plan: [], log: [], pending: new Set(), done: false, contract: null,
      stats: { warrant: { held: 0, ask: 0, ok: 0, stopped: [] }, undefended: { sent: 0 } }, queue: Promise.resolve() };
    S.tasks.push(T);
    return T;
  }
  function laneShell(lane) {
    const W = lane === "warrant";
    return `<div class="lane ${W ? "safe" : "plain"}" data-lane="${lane}"><div class="lane-head"><h3>${W ? "With WARRANT" : "Unprotected assistant"}</h3><span class="pill work">working</span></div><div class="steps"></div><div class="tail"></div></div>`;
  }
  const laneEl = (T, lane) => $(`.lane[data-lane="${lane}"]`, T.el);
  function touch(T) { paintTaskBody(T); renderChat(); if (route().page === "tasks" || route().page === "home") render(); else renderNav(); }

  async function startTask(text) {
    text = (text || "").trim();
    if (!text) return;
    if (S.busy) { toast("WARRANT is still working on the previous task."); return; }
    if (!S.hasKey) { toast("Add an API key to .env: the workspace runs a real AI agent."); return; }
    const compare = $("#optCompare").checked, gullible = $("#optGullible").checked;
    const mask = gullible ? false : $("#optMask").checked;
    const force = gullible && S.force && S.forceQ === text ? S.force : null;
    S.force = null; S.busy = true;
    if ($("#ntSend")) $("#ntSend").disabled = true;
    if ($("#ntText")) $("#ntText").value = "";

    const T = newTask(text, compare);
    T.tab = "progress";
    location.hash = `#/task/${T.id}`;
    try {
      const r = await fetch("/api/play/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sid: S.sid, message: text, mask, compare, gullible, force }) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
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
          if (line) { try { const ev = JSON.parse(line.slice(6)); T.queue = T.queue.then(() => handle(T, ev)).catch(console.error); } catch (e) { console.error(e); } }
        }
      }
    } catch (e) {
      T.error = e.message;
      T.el.insertAdjacentHTML("beforeend", `<div class="err">${esc(friendlyError(e.message))}</div>`);
    } finally {
      await T.queue;
      finishTask(T);
      S.busy = false; if ($("#ntSend")) $("#ntSend").disabled = false;
      refreshWorld();
    }
  }
  function renderChat() { renderRail(); }
  function renderRail() {
    const accts = { gmail: "arjun@acme.test", slack: "warrant-demo workspace", bank: "IN07 ACME · demo bank", drive: `${(S.world?.files || []).length} files · demo` };
    $("#railApps").innerHTML = SVC.map(([k, l, ic]) => `<div class="rapp"><span class="app-ico">${logo(k)}</span><div><b>${k === "drive" ? "Google Drive" : l}</b><span class="muted">${esc(accts[k])}</span></div><span class="pill2 ${S.conns[k] === "live" ? "ok" : "mute"}">${S.conns[k] === "live" ? "Connected" : "Demo"}</span></div>`).join("");
    $("#railQuick").innerHTML = DAILY.slice(0, 5).map((f, i) => `<button type="button" data-i="${i}">${icon(f.icon)}${esc(f.title)}</button>`).join("")
      + `<button type="button" class="more" id="railMore"><span>${icon("bolt")}Try an attack</span>${icon("arrow")}</button>`;
    $$("#railQuick [data-i]").forEach((b) => b.onclick = () => pickFlow(DAILY[+b.dataset.i]));
    $("#railMore").onclick = () => { $("#attackPick").hidden = false; };
    const items = [];
    for (const T of S.tasks) for (const id of T.pending) { const t = T.tickets[`warrant:${id}`]; if (t) items.push({ T, t, id }); }
    $("#apprH").textContent = `Approvals${items.length ? ` (${items.length})` : ""}`;
    $("#railAppr").innerHTML = items.length ? items.map(({ T, t, id }, i) => `<div class="appr"><div class="appr-top"><span class="svc-bank">${icon(FN_ICON[t.ev.function] || "send")}</span><div><b>${esc(FN_LABEL[t.ev.function] || "Action")} waiting for you</b><span class="muted">${esc(summarise(t.ev))}</span></div><time>${ago(T.ts)}</time></div>
        <div class="appr-act"><button class="rev" type="button" data-k="${i}">Review</button><button class="dis" type="button" data-k="${i}">Dismiss</button></div></div>`).join("")
      : `<div class="appr-empty">Nothing is waiting for you. New payees and recipients pause here for your OK.</div>`;
    $$("#railAppr .rev").forEach((b) => b.onclick = () => { const it = items[+b.dataset.k]; it.T.tab = "progress"; location.hash = `#/task/${it.T.id}`; });
    $$("#railAppr .dis").forEach((b) => b.onclick = async () => {
      const it = items[+b.dataset.k];
      try { const r = await api("approve", { sid: S.sid, id: String(it.id), approve: false }); applyDecision(it.T, it.t, false, r); touch(it.T); } catch (e) { toast(e.message); }
    });
  }
  function addStep(T, st, title, sub) { const s = { st, title, sub, t: Date.now() }; T.plan.push(s); return s; }
  function logLine(T, cls, what, why) { T.log.unshift({ cls, what, why, t: Date.now() }); }

  async function handle(T, ev) {
    const lane = ev.lane, W = lane === "warrant";
    const L = lane && laneEl(T, lane);
    switch (ev.type) {
      case "hijack": T.hijack = T.hijack || {}; T.hijack[`${lane}:${ev.id}`] = true; break;
      case "contract": {
        T.contract = ev.contract;
        const all = ["READ", "WRITE", "EGRESS", "PAY", "BOOK", "DELETE", "ACCOUNT"], box = $(".contract", T.el);
        box.hidden = false;
        box.innerHTML = `<span class="smallcaps">Your request allows</span>${all.map((e) => `<span class="eff ${ev.contract.allowed_effects.includes(e) ? "on" : ""}">${e}</span>`).join("")}<span class="muted mono" style="margin-left:auto;font-size:11px">contract ${esc(ev.contract.hash.slice(0, 10))}…</span>`;
        addStep(T, "ok", "Understood your request", `allowed: ${ev.contract.allowed_effects.join(", ")}`);
        break;
      }
      case "tool_call": {
        if (!L) break;
        if (ev.effect === "READ") {
          T.firstRead = T.firstRead || ev.function;
          const d = document.createElement("div");
          d.className = "read";
          d.innerHTML = `${icon(FN_ICON[ev.function] || "eye")}<div><b>${esc(readLabel(ev))}</b><span class="sub">reading…</span></div>`;
          $(".steps", L).appendChild(d);
          T.tickets[`${lane}:${ev.id}`] = { kind: "read", el: d, ev, step: W ? addStep(T, "run", readLabel(ev), "reading…") : null };
        } else {
          T.firstAction = T.firstAction || ev.function;
          const t = ticket(T, lane, ev);
          t.step = W ? addStep(T, "run", summarise(ev), "at the checkpoint…") : null;
          T.tickets[`${lane}:${ev.id}`] = t;
          if (!W) await sleep(250);
        }
        break;
      }
      case "gate": {
        const t = T.tickets[`${lane}:${ev.id}`];
        if (!t) break;
        if (t.kind === "read") { if (ev.decision !== "ALLOWED") markReadBlocked(T, t, ev); break; }
        await runCheckpoints(T, t, ev);
        break;
      }
      case "tool_result": {
        const t = T.tickets[`${lane}:${ev.id}`];
        if (!t) break;
        if (t.kind === "read") { fillRead(t, ev, W); break; }
        if (!W) await deliverUnprotected(T, t, ev);
        break;
      }
      case "answer_guard":
        if (L) $(".tail", L).insertAdjacentHTML("beforeend", `<div class="guard">${icon("shield")}<span>Removed ${ev.redactions.length} link(s) or address(es) nobody authorised from the reply.</span></div>`);
        break;
      case "final":
        T.finals[lane] = ev.text || "";
        if (L) $(".tail", L).insertAdjacentHTML("afterbegin", `<div class="answer"><span class="smallcaps">Reply to you</span>${esc(ev.text || "(no reply)")}</div>`);
        if (W) addStep(T, "ok", "Replied to you", (ev.text || "").slice(0, 120));
        break;
      case "error":
        if (W) T.error = ev.message;
        if (L) $(".tail", L).insertAdjacentHTML("beforeend", `<div class="err">${esc(friendlyError(ev.message))}</div>`);
        break;
      case "turn_done":
        T.effects[lane] = ev.effects || [];
        if (L) { const p = $(".pill", L); p.className = "pill"; p.textContent = `${ev.seconds}s`; }
        break;
    }
    if (!S.fast) touch(T);
  }
  function readLabel(ev) {
    const a = ev.args || {}, f = ev.function;
    if (f === "read_email") return `Read email ${a.email_id || ""}`;
    if (f === "read_file") return `Open file ${a.name_or_id || a.file_id || ""}`;
    if (f === "get_webpage") return `Open ${a.url || "a web page"}`;
    if (f === "read_channel_messages") return `Read Slack #${String(a.channel || "").replace(/^#/, "")}`;
    if (f === "search_contacts") return `Look up “${a.query || ""}” in contacts`;
    const vals = Object.values(a).filter((v) => v !== null && v !== undefined && v !== "").map(String);
    return f.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) + (vals.length ? ` (${vals.join(", ").slice(0, 60)})` : "");
  }
  function fillRead(t, ev, W) {
    const dir = (ev.spans || []).filter((s) => s.label === "DIRECTIVE");
    const text = ev.raw || ev.text || ev.error || "";
    let note = ev.error ? `<span class="inj">${esc(ev.error).slice(0, 120)}</span>` : "read";
    let plain = ev.error ? "failed" : "read";
    if (dir.length && W && ev.masked) { note = `<span class="msk">${ev.masked} hidden instruction${ev.masked > 1 ? "s" : ""} found and removed before the assistant saw ${ev.masked > 1 ? "them" : "it"}</span>`; plain = `${ev.masked} hidden instruction(s) removed`; }
    else if (dir.length && W) { note = `<span class="inj">${dir.length} instruction${dir.length > 1 ? "s" : ""} found; values in ${dir.length > 1 ? "them" : "it"} carry no authority</span>`; plain = `${dir.length} hidden instruction(s) found, treated as data`; }
    else if (dir.length) note = `<span class="inj">${dir.length} hidden instruction${dir.length > 1 ? "s" : ""} went straight to the assistant</span>`;
    $(".sub", t.el).innerHTML = note;
    if (t.step) { t.step.st = "ok"; t.step.sub = plain; }
    if (text) {
      let shown = esc(text.length > 4000 ? text.slice(0, 4000) + "…" : text);
      for (const s of dir) { const e = esc(s.text); if (e && shown.includes(e)) shown = shown.replace(e, `<mark>${e}</mark>`); }
      shown = shown.replace(/\[WARRANT: instruction-like text removed[^\]]*\]/g, (m) => `<mark class="gone">${m}</mark>`);
      $("div", t.el).insertAdjacentHTML("beforeend", `<details><summary>what came back</summary><pre>${shown}</pre></details>`);
    }
  }
  function markReadBlocked(T, t, ev) {
    const [, plain] = CODES[ev.code] || [1, ev.reason];
    $(".sub", t.el).innerHTML = `<span class="inj">Blocked at the checkpoint: ${esc(plain)}</span>`;
    if (t.step) { t.step.st = "bad"; t.step.sub = plain; }
    logLine(T, "bad", `Blocked: ${readLabel(t.ev)}`, plain);
    T.stats.warrant.held++;
  }

  /* ---------------- tickets ---------------- */
  function fields(ev) {
    const a = ev.args || {}, f = ev.function, rows = [];
    const list = (v) => (Array.isArray(v) ? v.join(", ") : v);
    if (f === "send_email") {
      rows.push(["To", list(a.recipients), "to"]);
      if (a.cc?.length) rows.push(["Cc", list(a.cc), "to"]);
      if (a.bcc?.length) rows.push(["Bcc", list(a.bcc), "to"]);
      rows.push(["Subject", a.subject]);
      if (a.attachments?.length) rows.push(["Attached", list(a.attachments.map((x) => (typeof x === "object" ? x.file_id || JSON.stringify(x) : x)))]);
      if (a.body) rows.push(["Message", String(a.body).slice(0, 160) + (String(a.body).length > 160 ? "…" : "")]);
    } else if (/money|transaction/.test(f)) {
      rows.push(["To account", a.recipient_iban || a.recipient, "to"]);
      if (a.amount !== undefined) rows.push(["Amount", `INR ${Number(a.amount).toLocaleString("en-IN")}`, "amount"]);
      if (a.subject) rows.push(["Reference", a.subject]);
    } else if (f === "share_file") { rows.push(["File", a.file_id]); rows.push(["Share with", a.email, "to"]); }
    else if (f === "send_direct_message") { rows.push(["To", a.recipient, "to"]); rows.push(["Message", String(a.body || "").slice(0, 160)]); }
    else if (f === "send_channel_message") { rows.push(["Channel", `#${String(a.channel || "").replace(/^#/, "")}`, "to"]); rows.push(["Message", String(a.body || "").slice(0, 160)]); }
    else if (f === "create_calendar_event") { rows.push(["Event", a.title]); rows.push(["When", a.date]); rows.push(["Invite", list(a.participants), "to"]); }
    else for (const [k, v] of Object.entries(a)) rows.push([k.replace(/_/g, " "), typeof v === "string" ? v : JSON.stringify(v)]);
    return rows.filter((r) => r[1] !== undefined && r[1] !== "");
  }
  function ticket(T, lane, ev) {
    const W = lane === "warrant";
    const n = Object.keys(T.tickets).filter((k) => k.startsWith(lane) && T.tickets[k].kind === "ticket").length + 1;
    const el = document.createElement("div");
    el.className = "ticket";
    el.innerHTML = `<div class="tk ${W ? "scan" : ""}">
        <div class="tk-head"><span class="k">${icon(FN_ICON[ev.function] || "send")}${esc(FN_LABEL[ev.function] || ev.function.replace(/_/g, " "))}</span><span>${esc(ev.effect)} · #${String(n).padStart(2, "0")}</span></div>
        ${fields(ev).map(([k, v, role]) => `<div class="fld-row" data-role="${role || ""}"><span>${esc(k)}</span><span class="${role ? "v" : ""}">${esc(v)}</span><svg class="mk"></svg></div>`).join("")}
        <div class="fld-row auth"><span>Authorised by</span><span>${W ? "checking…" : "nobody checks"}</span><svg class="mk"></svg></div>
        <div class="tk-foot"></div>
      </div>${route_(W)}<p class="why"></p>`;
    $(".steps", laneEl(T, lane)).appendChild(el);
    if (T.hijack?.[`${lane}:${ev.id}`]) $(".tk", el).insertAdjacentHTML("afterbegin", `<div class="trig">${icon("note")}Triggered by the hidden instruction in the data</div>`);
    return { kind: "ticket", el, ev, lane };
  }
  function route_(W) {
    return `<div class="route ${W ? "" : "skip"}"><svg class="line" preserveAspectRatio="none" viewBox="0 0 100 4"><line class="base" x1="0" y1="2" x2="100" y2="2"/><line class="prog" x1="0" y1="2" x2="100" y2="2" stroke-dasharray="100" stroke-dashoffset="100"/></svg>
      <div class="runner"></div><div class="cps">${CHECKPOINTS.map((c) => `<div class="cp"><span class="dot"></span><span class="cp-l">${c}</span></div>`).join("")}</div></div>`;
  }
  function moveRunner(rt, i) { const pct = (i / (CHECKPOINTS.length - 1)) * 100; $(".runner", rt).style.left = `${pct}%`; $(".prog", rt).setAttribute("stroke-dashoffset", String(100 - pct)); }
  function setMark(row, cls) {
    const mk = $(".mk", row); if (!mk) return;
    mk.innerHTML = `<use href="/static/workspace/sprite.svg#${cls === "y" ? "check" : cls === "a" ? "pause" : "cross"}"/>`;
    mk.className.baseVal = `mk ${cls} on`;
  }
  function authText(ev) {
    const sinks = (ev.checks || []).filter((c) => c.sink !== "content");
    if (!sinks.length) return ev.decision === "ALLOWED" ? "your request (no outside target)" : "";
    return sinks.map((c) => {
      const p = (c.provenance || [])[0];
      if (c.verdict === "ALLOWED") return `${c.arg}: ${KIND[c.kind] || c.kind}${p?.tool && p.tool !== "user_request" ? ` (${p.tool.replace(/_/g, " ")})` : ""}`;
      return `${c.arg}: ${p ? (p.label === "DIRECTIVE" ? "only from a hidden instruction" : `${KIND[p.kind] || "unverified source"}`) : "nobody"}`;
    }).join(" · ");
  }
  function summarise(ev) {
    const a = ev.args || {}, label = FN_LABEL[ev.function] || ev.function.replace(/_/g, " ");
    const to = [a.recipients, a.email, a.recipient, a.recipient_iban, a.channel, a.url].flat().filter(Boolean).join(", ");
    const amt = a.amount !== undefined ? ` INR ${Number(a.amount).toLocaleString("en-IN")}` : "";
    return `${label}${amt}${to ? ` to ${to}` : ""}`;
  }
  function stamp(tk, cls, word, small) { $(".tk-foot", tk).innerHTML = `<span class="stamp ${cls} thump">${esc(word)}<small>${esc(small || "")}</small></span>`; }

  async function runCheckpoints(T, t, ev) {
    const el = t.el, tk = $(".tk", el), rt = $(".route", el), why = $(".why", el);
    const ok = ev.decision === "ALLOWED", ask = ev.decision === "NEEDS_INPUT";
    const [stopAt, plain] = ok ? [CHECKPOINTS.length - 1, ""] : (CODES[ev.code] || [1, ev.reason]);
    tk.classList.add("once");
    tk.style.setProperty("--h", `${tk.offsetHeight}px`);
    const cps = $$(".cp", rt);
    for (let i = 0; i <= stopAt; i++) { moveRunner(rt, i); await sleep(230); if (i < stopAt || ok) cps[i].classList.add("done"); }
    const bad = new Set((ev.checks || []).filter((c) => c.verdict !== "ALLOWED").map((c) => String(c.value)));
    $$(".fld-row", tk).forEach((row) => {
      if (row.classList.contains("auth")) return;
      const val = $("span:nth-child(2)", row)?.textContent || "";
      const hit = [...bad].some((b) => b && val.includes(b.replace(/^"|"$/g, "")));
      if (row.dataset.role) setMark(row, hit ? (ask ? "a" : "n") : "y");
      if (hit) row.classList.add(ask ? "warn" : "flag");
    });
    const authRow = $(".fld-row.auth", tk);
    $("span:nth-child(2)", authRow).textContent = authText(ev) || plain;
    setMark(authRow, ok ? "y" : ask ? "a" : "n");
    if (ok) {
      rt.classList.add("ok"); cps[stopAt].classList.replace("done", "okend");
      stamp(tk, "ok", "Delivered", "every target traced to you");
      why.className = "why ok"; why.innerHTML = `<b>Allowed.</b> Every recipient, account and amount traces back to you or your records.`;
      T.stats.warrant.ok++;
      if (t.step) { t.step.st = "ok"; t.step.sub = `delivered · ${authText(ev)}`; }
      logLine(T, "ok", `Allowed: ${summarise(t.ev)}`, authText(ev));
    } else {
      rt.classList.add(ask ? "ask" : "bad"); cps[stopAt].classList.add(ask ? "wait" : "fail");
      cps.slice(stopAt + 1).forEach((c) => c.classList.add("skip"));
      stamp(tk, ask ? "ask" : "held", ask ? "Asks you first" : "Held", CHECKPOINTS[stopAt]);
      T.stats.warrant.stopped.push({ text: summarise(t.ev), ask });
      if (ask && ev.approvable) addApproval(T, t, ev);
      why.className = `why ${ask ? "ask" : "bad"}`;
      why.innerHTML = `<b>${ask ? "Paused for your OK." : "Stopped."}</b> ${esc(plain)} <span class="tech">${esc(ev.code)}</span>`;
      T.stats.warrant[ask ? "ask" : "held"]++;
      if (t.step) { t.step.st = ask ? "ask" : "bad"; t.step.sub = `${ask ? "waiting for your approval" : "held"} at “${CHECKPOINTS[stopAt]}”: ${plain}`; }
      logLine(T, ask ? "ask" : "bad", `${ask ? "Asked you" : "Held"}: ${summarise(t.ev)}`, plain);
    }
  }
  function addApproval(T, t, ev) {
    const foot = $(".tk-foot", t.el), box = document.createElement("div");
    box.className = "approve"; box.dataset.id = String(ev.id);
    box.innerHTML = `<button class="btn brand" type="button" data-a="1">${icon("check")}Approve &amp; send</button><button class="btn ghost" type="button" data-a="0">Reject</button><span class="hint">nothing was sent yet</span>`;
    foot.prepend(box);
    T.pending.add(String(ev.id));
    if (S.busy) { $$("button", box).forEach((x) => x.disabled = true); $(".hint", box).textContent = "you can decide when both assistants finish"; }
    $$("button", box).forEach((b) => b.onclick = async () => {
      const yes = b.dataset.a === "1";
      $$("button", box).forEach((x) => x.disabled = true);
      $(".hint", box).textContent = yes ? "sending…" : "";
      try { const r = await api("approve", { sid: S.sid, id: String(ev.id), approve: yes }); applyDecision(T, t, yes, r); touch(T); refreshWorld(); }
      catch (e) { $(".hint", box).textContent = e.message; $$("button", box).forEach((x) => x.disabled = false); }
    });
  }
  function applyDecision(T, t, yes, r) {
    $(".approve", t.el)?.remove();
    T.pending.delete(String(t.ev.id ?? ""));
    for (const [k, v] of Object.entries(T.tickets)) if (v === t) T.pending.delete(k.split(":")[1]);
    const tk = $(".tk", t.el), rt = $(".route", t.el), why = $(".why", t.el), cps = $$(".cp", rt);
    if (yes && r.ok) {
      rt.classList.remove("ask"); rt.classList.add("ok");
      cps.forEach((c) => { c.classList.remove("skip", "wait"); c.classList.add("done"); });
      cps[cps.length - 1].classList.replace("done", "okend");
      moveRunner(rt, CHECKPOINTS.length - 1);
      $$(".fld-row.warn", tk).forEach((row) => { row.classList.remove("warn"); setMark(row, "y"); });
      setMark($(".fld-row.auth", tk), "y");
      $(".fld-row.auth span:nth-child(2)", tk).textContent = "you approved it (confirmed for next time)";
      stamp(tk, "ok", "Approved", "sent by you");
      why.className = "why ok"; why.innerHTML = `<b>You approved it.</b> Sent now, and this ${/money|transaction/.test(t.ev.function) ? "payee" : "recipient"} counts as confirmed from now on.`;
      if (t.step) { t.step.st = "ok"; t.step.sub = "approved by you and sent"; }
      if (r.effects?.length) T.effects.warrant = [...(T.effects.warrant || []), ...r.effects];
      logLine(T, "ok", `You approved: ${summarise(t.ev)}`, "confirmation logged in the audit trail");
    } else if (yes) {
      why.innerHTML += ` <b>Could not send:</b> ${esc(r.error || "error")}`;
    } else {
      stamp(tk, "held", "Rejected", "by you");
      why.className = "why bad"; why.innerHTML = `<b>You rejected it.</b> Nothing was sent.`;
      if (t.step) { t.step.st = "bad"; t.step.sub = "rejected by you"; }
      logLine(T, "bad", `You rejected: ${summarise(t.ev)}`, "");
    }
  }
  async function deliverUnprotected(T, t, ev) {
    const tk = $(".tk", t.el), rt = $(".route", t.el), why = $(".why", t.el), cps = $$(".cp", rt);
    cps.slice(1, -1).forEach((c) => c.classList.add("skip"));
    for (const i of [0, CHECKPOINTS.length - 1]) { moveRunner(rt, i); await sleep(260); cps[i].classList.add("done"); }
    if (ev.error) { stamp(tk, "sent", "Failed", "the tool returned an error"); why.innerHTML = `The action failed on its own: ${esc(ev.error).slice(0, 140)}`; return; }
    T.stats.undefended.sent++;
    if (isOutside(t.ev)) {
      rt.classList.add("bad");
      $$(".fld-row", tk).forEach((row) => { if (row.dataset.role === "to") { row.classList.add("flag"); setMark(row, "n"); } });
      stamp(tk, "leak", "Sent", "to an outside party");
      why.className = "why bad"; why.innerHTML = `<b>Went out unchecked</b> to someone outside your contacts and organisation.`;
    } else { stamp(tk, "sent", "Sent", "no checks"); why.innerHTML = `Sent with no checks.`; }
  }
  function isOutside(ev) {
    const a = ev.args || {};
    const vals = [a.recipients, a.cc, a.bcc, a.email, a.recipient, a.recipient_iban, a.participants].flat().filter(Boolean).map(String);
    const contacts = (S.world?.contacts || []).map((c) => c.sub.toLowerCase());
    return vals.some((v) => {
      const low = v.toLowerCase();
      if (low.endsWith("@acme.test")) return false;
      if (contacts.some((c) => c.includes(low))) return false;
      if (/^eve$/i.test(v)) return true;
      if (/^(alice|bob|charlie|general|random|finance)$/i.test(v.replace(/^#/, ""))) return false;
      return /@|^[A-Z]{2}\d{2}[A-Z0-9]{8,}$/i.test(v);
    });
  }
  function finishTask(T) {
    T.done = true;
    $$(".pill.work", T.el).forEach((p) => { p.className = "pill"; p.textContent = "stopped"; });
    $$(".approve", T.el).forEach((b) => { $$("button", b).forEach((x) => x.disabled = false); $(".hint", b).textContent = "nothing was sent yet"; });
    T.plan.forEach((s) => { if (s.st === "run") s.st = "ok"; });
    const w = T.stats.warrant, eff = (lane) => T.effects[lane] || [];
    const list = (lane) => eff(lane).length ? `<ul>${eff(lane).map((e) => `<li class="${e.outside ? "out" : ""}">${icon(e.kind === "payment" ? "coin" : e.kind === "email" ? "envelope" : e.kind === "slack" ? "hash" : e.kind === "share" ? "file" : "check")}<span>${esc(e.text)}${e.outside ? " · outside party" : ""}</span></li>`).join("")}</ul>` : `<p>Nothing left your workspace.</p>`;
    const outU = eff("undefended").filter((e) => e.outside).length, outW = eff("warrant").filter((e) => e.outside).length, stopped = w.held + w.ask;
    const box = document.createElement("div");
    box.className = `outcome ${T.compare ? "" : "one"}`;
    box.innerHTML = `${T.compare ? `<div class="oc"><span class="smallcaps">Unprotected assistant</span><div class="big ${outU ? "bad" : ""}">${outU ? `${outU} leak${outU > 1 ? "s" : ""}` : `${eff("undefended").length} action${eff("undefended").length === 1 ? "" : "s"}`}</div><p>${outU ? "Reached someone outside your contacts or organisation." : "Nothing reached an outsider."}</p>${list("undefended")}</div>` : ""}
      <div class="oc"><span class="smallcaps">With WARRANT</span><div class="big ${outW ? "bad" : stopped ? (w.held ? "ok" : "ask") : "ok"}">${stopped ? `${stopped} stopped` : "All clear"}</div><p>${w.held ? `${w.held} held at the checkpoint. ` : ""}${w.ask ? `${w.ask} paused for your OK. ` : ""}${w.ok} delivered.</p>${w.stopped.length ? `<ul>${w.stopped.map((x) => `<li class="held">${icon(x.ask ? "pause" : "shield")}<span><s>${esc(x.text)}</s> · ${x.ask ? "waiting for you" : "held"}</span></li>`).join("")}</ul>` : ""}${list("warrant")}</div>`;
    T.el.appendChild(box);
    if (!S.fast) touch(T);
  }
  function friendlyError(m) {
    if (/tokens per day|TPD|quota/i.test(m)) return "The AI provider's free quota is used up for this model. Switch models in .env or try later.";
    if (/rate limit|high demand|503/i.test(m)) return "The AI provider is busy or rate limited. Wait a minute and try again.";
    return m;
  }

  /* ---------------- wiring ---------------- */
  function wire() {
    const close = () => {};
    $("#apClose").onclick = () => $("#attackPick").hidden = true;
    $("#attackPick").onclick = (e) => { if (e.target.id === "attackPick") $("#attackPick").hidden = true; };
    if ($("#searchBtn")) $("#searchBtn").onclick = () => openNewTask();
    renderQuick();
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openNewTask(); }
      if (e.key === "Escape") { close(); $("#attackPick").hidden = true; $("#reader").hidden = true; $("#plant").hidden = true; $("#setPop").hidden = true; }
    });
    $("#bellBtn").onclick = () => location.hash = "#/tasks/approval";
    $("#menuBtn").onclick = () => document.body.classList.toggle("nav-open");
    $("#setBtn").onclick = (e) => { e.stopPropagation(); const p = $("#setPop"); p.hidden = !p.hidden; };
    document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) $("#setPop").hidden = true; });
    const sync = () => $("#optMask").closest(".opt").classList.toggle("off", $("#optGullible").checked);
    $("#optGullible").onchange = sync; sync();
    $("#resetBtn").onclick = async () => {
      if (S.busy) return;
      if (!confirm("Reset the workspace? This clears tasks, planted data, approvals and what the assistants did.")) return;
      await api("reset", { sid: S.sid }); S.tasks = []; $("#setPop").hidden = true; location.hash = "#/home"; await refreshWorld(); render();
    };
    $("#rdClose").onclick = () => $("#reader").hidden = true;
    $("#reader").onclick = (e) => { if (e.target.id === "reader") $("#reader").hidden = true; };
    $("#plClose").onclick = () => $("#plant").hidden = true;
    $("#plKind").onchange = () => { const k = $("#plKind").value; $$(".k-email").forEach((x) => x.hidden = k !== "email"); $$(".k-file").forEach((x) => x.hidden = k !== "file"); $$(".k-web").forEach((x) => x.hidden = k !== "web"); };
    $("#plantForm").onsubmit = async (e) => {
      e.preventDefault();
      const k = $("#plKind").value;
      try {
        await api("add", { sid: S.sid, kind: k, sender: $("#plSender").value, subject: $("#plSubject").value, name: $("#plName").value, folder: $("#plFolder").value, url: $("#plUrl").value, content: $("#plContent").value, filename: "", data_b64: "" });
        $("#plant").hidden = true; await refreshWorld(); toast("Added to both workspaces. Ask something that makes the assistant read it.");
      } catch (err) { $("#plStatus").textContent = err.message; }
    };
  }

  wire();
  const saved = store.get();
  (saved ? restoreSession(saved).catch(() => newSession()) : newSession())
    .then(() => { render(); renderChat(); })
    .catch((e) => { $("#page").innerHTML = `<div class="err">Could not start: ${esc(e.message)}</div>`; });
})();

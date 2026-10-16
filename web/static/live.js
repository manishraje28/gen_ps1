/* WARRANT live view: a product-style presentation of the same run events app.js receives.
   Protection off / on shows the undefended or the WARRANT agent; both run on their own copy of the world. */
(() => {
  const START_BALANCE = 250000;
  const KIND_TEXT = {
    USER_LITERAL: "You typed it in your request",
    TRUSTED: "It is in your own records",
    LOOKUP: "It comes from the data you pointed at",
    DELEGATED: "It comes from a source you delegated to",
    DERIVED: "Computed from numbers you, or your records, gave",
  };
  const CODE_TEXT = {
    TAINTED_VALUE: "only appears inside instructions hidden in the data",
    TAINTED_DOCUMENT: "comes from a document that contains hidden instructions",
    TAINTED_CONTENT: "carries a link or address planted by hidden instructions",
    NO_PROVENANCE: "appears nowhere in your request or your data",
    NEW_PAYEE: "is a payee you have never paid, and nobody independent vouches for it",
    NEW_RECIPIENT: "is not a contact of yours",
    SLOT_MISMATCH: "is not the value your request allows",
    SECRET_EGRESS: "would send confidential data you did not ask to share",
    EFFECT_OUT_OF_SCOPE: "is an action you never asked for",
    DELEGATION_SCOPE: "is outside what you delegated",
    ENVELOPE_EXCEEDED: "goes beyond the limits of your request",
    REPEATED_REFUSALS: "is blocked after repeated attempts",
  };
  const L = {};
  let mode = "warrant";
  const fresh = () => ({ items: [], cards: {}, calls: {}, pays: [], mails: [], blocked: [], verdict: null, running: false });
  const lanes = () => ["undefended", "warrant"];
  lanes().forEach((l) => (L[l] = fresh()));

  const money = (n) => "INR " + Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtIban = (s) => String(s || "").replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim();
  const ic = (n) => icon(n, "i");

  /* ---------- wiring ---------- */
  function init() {
    hydrateIcons(document.getElementById("page-demo"));
    $$("#protect button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
    $("#runBtn2").onclick = run;
    $("#peekBtn2").onclick = () => peek();
    const wait = setInterval(() => {
      if (!S.scenarios.length || !$("#plannerSel")) return;
      clearInterval(wait);
      $("#plannerSel2").innerHTML = $("#plannerSel").innerHTML;
      chips();
    }, 80);
    setMode("warrant");
  }

  function chips() {
    $("#chips").innerHTML = S.scenarios.map((s) =>
      `<button class="chip ${s.benign ? "benign" : ""}" data-id="${s.id}">${ic(s.benign ? "check" : chipIcon(s))}${esc(s.title)}</button>`).join("");
    $$("#chips .chip").forEach((c) => (c.onclick = () => pick(c.dataset.id)));
    pick(S.sel?.id || S.scenarios[0].id);
  }
  const chipIcon = (s) => (/iban|pay|bill|money/i.test(s.id + s.title) ? "bolt" : /deleg|todo/i.test(s.id + s.title) ? "user" : "file");

  function pick(id) {
    select(id);
    $$("#chips .chip").forEach((c) => c.classList.toggle("on", c.dataset.id === id));
    lanes().forEach((l) => (L[l] = fresh()));
    $("#outcome").innerHTML = "";
    render();
    const q = S.sel;
    $("#convo").innerHTML = `<div class="msg you"><small>YOU</small>${esc(q.query)}</div>
      <div class="cempty">${q.benign ? "A normal request: nothing malicious in the data." : `Hidden in the data: <b>${esc(q.attack)}</b>`}<br>Press <b>Run</b> to watch the agent work.</div>`;
  }

  function run() {
    $("#plannerSel").value = $("#plannerSel2").value;
    $("#maskChk").checked = $("#maskChk2").checked;
    $("#runBtn").click();
  }

  function setMode(m) {
    mode = m;
    $$("#protect button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
    document.body.classList.toggle("unprotected", m === "undefended");
    $("#modeTag").innerHTML = m === "warrant" ? `${ic("shield")}Protected by WARRANT` : `${ic("alert")}Unprotected`;
    render();
  }

  window.liveStart = () => {
    lanes().forEach((l) => (L[l] = { ...fresh(), running: true }));
    $("#outcome").innerHTML = "";
    $("#runBtn2").disabled = true;
    render();
    pulse("fl-1");
  };

  /* ---------- events ---------- */
  window.liveHook = (ev) => {
    const lane = ev.lane;
    if (ev.type === "end") { $("#runBtn2").disabled = false; lanes().forEach((l) => (L[l].running = false)); render(); return; }
    if (!lane || !L[lane]) return;
    const st = L[lane];
    switch (ev.type) {
      case "run_start":
        st.items.push({ t: "you", text: S.sel?.query || "" });
        break;
      case "llm":
        if (ev.text && ev.text.trim() && ev.tool_calls && ev.tool_calls.length) st.items.push({ t: "think", text: ev.text });
        if (lane === mode) pulse("fl-2");
        break;
      case "tool_call": {
        const item = { t: "call", id: ev.id, fn: ev.function, args: ev.args || {}, effect: ev.effect, state: "pending" };
        st.items.push(item);
        st.cards[ev.id] = item;
        st.calls[ev.id] = item;
        if (lane === mode) { pulse("fl-2"); node("f-agent"); }
        break;
      }
      case "gate": {
        const it = st.cards[ev.id];
        if (!it) break;
        it.decision = ev.decision; it.code = ev.code; it.reason = ev.reason; it.checks = ev.checks || [];
        it.state = ev.decision === "ALLOWED" ? "ok" : ev.decision === "NEEDS_INPUT" ? "ask" : "blocked";
        if (it.state !== "ok" && it.effect !== "READ") st.blocked.push(it);
        if (it.state === "ok" && it.effect !== "READ") applyEffect(st, it);
        if (lane === mode) {
          if (it.state === "ok") { node("f-gate", "ok"); pulse("fl-3"); node("f-world", it.effect === "READ" ? "" : "ok"); }
          else node("f-gate", it.state === "ask" ? "ask" : "bad");
        }
        break;
      }
      case "tool_result": {
        const it = st.cards[ev.id];
        if (!it) break;
        if (lane === "undefended" && !ev.refused) {
          it.state = "done";
          if (it.effect !== "READ") { applyEffect(st, it); if (lane === mode) { pulse("fl-3"); node("f-world", "bad"); } }
        }
        it.injected = (ev.spans || []).some((s) => s.label === "DIRECTIVE");
        it.masked = ev.masked || 0;
        break;
      }
      case "final":
        st.items.push({ t: "final", text: ev.text || "" });
        break;
      case "verdict":
        st.verdict = ev;
        break;
      case "lane_done":
        st.running = false;
        break;
    }
    if (lane === mode || ev.type === "verdict") render();
  };

  function applyEffect(st, it) {
    const a = it.args;
    if (["send_money", "schedule_transaction"].includes(it.fn)) {
      if (st.pays.some((p) => p.id === it.id)) return;
      st.pays.push({ id: it.id, to: a.recipient_iban || a.recipient, amount: Number(a.amount) || 0, subject: a.subject || "" });
    } else if (["send_email", "share_file", "send_direct_message", "send_channel_message"].includes(it.fn)) {
      if (st.mails.some((p) => p.id === it.id)) return;
      const to = [].concat(a.recipients || a.email || a.recipient || a.channel || []).join(", ");
      st.mails.push({ id: it.id, to, subject: a.subject || (it.fn === "share_file" ? `shared file ${a.file_id}` : a.body || "") , attach: [].concat(a.attachments || []) });
    }
  }

  /* ---------- rendering ---------- */
  function render() {
    const st = L[mode];
    const items = st.items.length ? st.items.map((x) => item(x)).join("") : null;
    if (items) {
      $("#convo").innerHTML = items + (st.running ? `<div class="typing"><i></i><i></i><i></i></div>` : "");
      $("#convo").scrollTop = $("#convo").scrollHeight;
    }
    bank(st);
    outcome(st);
    $$("#convo svg, #bankCard svg, #mailTx svg, #outcome svg").forEach((s) => s.classList.add("i"));
  }

  function item(x) {
    if (x.t === "you") return `<div class="msg you"><small>YOU</small>${esc(x.text)}</div>`;
    if (x.t === "think") return `<div class="msg bot"><small>ASSISTANT</small>${esc(x.text)}</div>`;
    if (x.t === "final") return `<div class="msg bot final"><small>ASSISTANT</small>${esc(x.text)}</div>`;
    return call(x);
  }

  function callTitle(x) {
    const a = x.args;
    switch (x.fn) {
      case "send_money": case "schedule_transaction": return `Pay ${money(a.amount)} to ${fmtIban(a.recipient_iban || a.recipient)}`;
      case "send_email": return `Email ${[].concat(a.recipients || []).join(", ")}${a.attachments?.length ? ` with ${a.attachments.length} attachment(s)` : ""}`;
      case "share_file": return `Share file ${a.file_id} with ${a.email}`;
      case "delete_file": return `Delete file ${a.file_id}`;
      case "create_calendar_event": return `Create event "${a.title || ""}"`;
      case "read_email": return `Read email ${a.email_id || ""}`;
      case "read_file": return `Open ${a.name_or_id || a.file_path || "a file"}`;
      case "get_webpage": return `Open ${a.url}`;
      default: return `${x.fn.replace(/_/g, " ")}`;
    }
  }

  function call(x) {
    const read = x.effect === "READ";
    if (read) {
      const inj = x.injected ? `<span class="inj">${ic("alert")}hidden instructions inside${x.masked ? " · masked" : ""}</span>` : "";
      return `<div class="rstep"><span class="dotc"></span>${esc(callTitle(x))}${inj}</div>`;
    }
    const W = mode === "warrant";
    let cls = "pending", head = `${ic("bolt")}Agent wants to act`;
    if (x.state === "ok") { cls = "ok"; head = `${ic("shield")}WARRANT approved`; }
    if (x.state === "blocked") { cls = "bad"; head = `${ic("x")}WARRANT blocked this`; }
    if (x.state === "ask") { cls = "ask"; head = `${ic("alert")}WARRANT paused this: asks you first`; }
    if (!W && x.state === "done") { cls = "danger"; head = `${ic("bolt")}Executed, no questions asked`; }
    let bullets = "";
    if (W && x.checks && x.checks.length) {
      bullets = `<ul>${x.checks.map((c) => {
        const ok = c.verdict === "ALLOWED";
        const why = ok ? (KIND_TEXT[c.kind] || c.kind) : (CODE_TEXT[c.code] || c.code);
        return `<li class="${ok ? "y" : "n"}">${ic(ok ? "check" : "x")}<span><b>${esc(label(c))}</b> ${esc(why)}</span></li>`;
      }).join("")}</ul>`;
    } else if (W && x.state && x.state !== "pending" && x.state !== "ok") {
      bullets = `<ul><li class="n">${ic("x")}<span>${esc(CODE_TEXT[x.code] || x.reason || x.code)}</span></li></ul>`;
    }
    const foot = x.state === "blocked" || x.state === "ask" ? `<div class="foot">Nothing was executed. The agent carries on with your real request.</div>` : "";
    return `<div class="act ${cls}"><div class="ah">${head}</div><div class="what">${esc(callTitle(x))}</div>${bullets}${foot}</div>`;
  }
  const label = (c) => {
    const v = String(c.value ?? "");
    if (c.sink === "iban") return `Account ${fmtIban(v)}`;
    if (c.sink === "amount") return `Amount ${money(v)}`;
    if (c.sink === "recipient") return `Recipient ${v}`;
    if (c.sink === "content") return `Content`;
    return `${c.arg} ${v}`.slice(0, 80);
  };

  function bank(st) {
    const spent = st.pays.reduce((a, p) => a + p.amount, 0);
    $("#bankBal").textContent = money(START_BALANCE - spent);
    $("#bankCard").classList.toggle("hit", st.pays.some((p) => isEvil(p.to)));
    const rows = [
      ...st.pays.map((p) => `<div class="tx ${isEvil(p.to) ? "evil" : "ok"}"><span>${ic(isEvil(p.to) ? "alert" : "check")}</span><div><b>${esc(fmtIban(p.to))}</b><small>${esc(p.subject)}</small></div><em>−${money(p.amount)}</em></div>`),
      ...st.blocked.filter((b) => ["send_money", "schedule_transaction"].includes(b.fn)).map((b) =>
        `<div class="tx stopped"><span>${ic("shield")}</span><div><b>${esc(fmtIban(b.args.recipient_iban || b.args.recipient))}</b><small>${b.state === "ask" ? "Waiting for your confirmation" : "Blocked by WARRANT"} · never left your account</small></div><em>${money(b.args.amount)}</em></div>`),
    ];
    $("#bankTx").innerHTML = rows.join("") || `<div class="txe">No money has moved.</div>`;
    const mails = [
      ...st.mails.map((m) => `<div class="tx ${isEvil(m.to) ? "evil" : "ok"}"><span>${ic(isEvil(m.to) ? "alert" : "check")}</span><div><b>${esc(m.to)}</b><small>${esc(m.subject).slice(0, 90)}${m.attach.length ? ` · ${m.attach.length} attachment(s)` : ""}</small></div></div>`),
      ...st.blocked.filter((b) => ["send_email", "share_file", "send_direct_message", "send_channel_message"].includes(b.fn)).map((b) =>
        `<div class="tx stopped"><span>${ic("shield")}</span><div><b>${esc(callTitle(b))}</b><small>Blocked by WARRANT · never sent</small></div></div>`),
    ];
    $("#mailTx").innerHTML = mails.join("") || `<div class="txe">Nothing sent.</div>`;
  }
  const isEvil = (s) => /evil|IN66XEVL|kestrel|kpmg-review|sunrise-records/i.test(String(s || ""));

  function outcome(st) {
    const v = st.verdict;
    if (!v) { $("#outcome").innerHTML = ""; return; }
    const W = mode === "warrant";
    let cls, title, sub;
    if (v.benign) {
      cls = v.utility ? "good" : "meh";
      title = v.utility ? `Task done, <span>no false alarm</span>` : "Task not completed";
      sub = v.utility_detail;
    } else if (v.attack_succeeded) {
      cls = "bad"; title = `The agent was <span>hijacked</span>`; sub = v.attack_detail;
    } else {
      cls = "good"; title = `WARRANT identified the attack and <span>blocked it</span>`;
      sub = `${v.attack_detail}. ${v.utility ? "Your real task was still completed." : "Your task was not finished."}`;
    }
    const stopped = st.blocked.length;
    $("#outcome").innerHTML = `<div class="ocard ${cls}">
      <div class="otop"><div class="obadge">${ic(cls === "bad" ? "alert" : "shield")}</div><div><h3>${title}</h3><p>${esc(sub)}</p></div></div>
      <div class="orow"><div><small>Attacker</small><b>${v.benign ? "none" : "Hidden text in your data"}</b></div>
        <div><small>Victim</small><b>Arjun's ${/pay|iban|bill/i.test(S.sel?.id + S.sel?.title) ? "bank account" : "mailbox"}</b></div>
        <div><small>Protection</small><b>${W ? `WARRANT · ${stopped} action(s) stopped` : "none"}</b></div>
        <div><small>Time</small><b>${v.seconds}s</b></div></div></div>`;
  }

  /* ---------- flow animation ---------- */
  function pulse(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("go"); void el.offsetWidth; el.classList.add("go");
  }
  function node(id, state = "") {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("ok", "bad", "ask", "hot");
    void el.offsetWidth;
    el.classList.add(state || "hot");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("ok", "bad", "ask", "hot"), 1600);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

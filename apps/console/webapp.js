// RingFence console — functional SPA over the gateway API.
// Auth (JWT in localStorage) -> Overview / Keys / Cases / Live.
// The live view reuses mountConsole() from ./app.js.

import { mountConsole } from "./app.js";
import { startCapture, stopCapture } from "./capture.js";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, props = {}, kids = []) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of [].concat(kids)) n.append(k);
  return n;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const store = {
  get token() { return localStorage.getItem("rf_token"); },
  get org() { return localStorage.getItem("rf_org"); },
  get role() { return localStorage.getItem("rf_role"); },
  get email() { return localStorage.getItem("rf_email"); },
  set(b) {
    localStorage.setItem("rf_token", b.token);
    localStorage.setItem("rf_org", b.org_id);
    localStorage.setItem("rf_role", b.role);
    localStorage.setItem("rf_email", b.email);
  },
  clear() { ["rf_token", "rf_org", "rf_role", "rf_email"].forEach((k) => localStorage.removeItem(k)); },
  get liveKey() { return sessionStorage.getItem("rf_live_key"); },
  set liveKey(k) { sessionStorage.setItem("rf_live_key", k); },
};

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "content-type": "application/json" };
  if (auth && store.token) headers.authorization = `Bearer ${store.token}`;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && auth) { store.clear(); location.hash = "#/signin"; throw new Error("session expired"); }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw Object.assign(new Error((data && data.error) || res.statusText), { status: res.status, data });
  return data;
}

// ---------- shell ----------
const app = $("#app");

function shell(active, view) {
  app.innerHTML = "";
  if (!store.token) { app.append(view); return; } // auth views render bare
  const nav = el("nav");
  nav.innerHTML = `
    <div class="brand">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 2.5 3.5 6v6c0 5 3.6 8.5 8.5 9.5 4.9-1 8.5-4.5 8.5-9.5V6L12 2.5Z" stroke="#3dd4e0" stroke-width="1.6"/><path d="M12 8v4.5M12 15.5v.01" stroke="#3dd4e0" stroke-width="1.8" stroke-linecap="round"/></svg>
      RingFence
    </div>`;
  for (const [href, label] of [["#/overview", "Overview"], ["#/cases", "Cases"], ["#/keys", "API keys"], ["#/live", "Live"]]) {
    nav.append(el("a", { href, className: active === href ? "on" : "", textContent: label }));
  }
  const who = el("div", { className: "who" });
  who.innerHTML = `${esc(store.email || "")}<br><span class="mono">${esc(store.role || "")} · ${esc((store.org || "").slice(0, 8))}</span><br><a href="#" id="logout" style="font-size:12px">Sign out</a>`;
  nav.append(who);
  app.append(nav, view);
  $("#logout").onclick = async (e) => { e.preventDefault(); try { await api("/auth/logout", { method: "POST" }); } catch {} store.clear(); location.hash = "#/signin"; };
}

const page = (title, sub) => {
  const m = el("main");
  m.append(el("h1", { textContent: title }));
  if (sub) m.append(el("div", { className: "sub", textContent: sub }));
  return m;
};

// ---------- auth ----------
function authView(mode) {
  const wrap = el("div", { className: "authwrap" });
  const box = el("div", { className: "authbox card" });
  const isUp = mode === "signup";
  box.innerHTML = `
    <div class="row" style="gap:8px;margin-bottom:18px"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 2.5 3.5 6v6c0 5 3.6 8.5 8.5 9.5 4.9-1 8.5-4.5 8.5-9.5V6L12 2.5Z" stroke="#3dd4e0" stroke-width="1.6"/></svg><b style="font-size:16px">RingFence</b></div>
    <h1 style="font-size:18px">${isUp ? "Create workspace" : "Sign in"}</h1>
    <div class="sub">${isUp ? "Pilot plan — 3 lines, free." : "Welcome back."}</div>
    <div class="grid" style="gap:14px">
      ${isUp ? `<label class="lbl">Organisation<input class="field" id="org" value="Meridian Fraud Desk"></label>` : ""}
      <label class="lbl">Email<input class="field" id="email" value="ops@meridian.example"></label>
      <label class="lbl">Password<input class="field" id="pw" type="password" value="pw-12345678"></label>
    </div>
    <button class="btn" id="go" style="width:100%;margin-top:18px">${isUp ? "Create workspace" : "Sign in"}</button>
    <div class="err" id="err" style="margin-top:12px"></div>
    <div class="sub" style="margin-top:16px">${isUp ? `Have one? <a href="#/signin">Sign in</a>` : `New here? <a href="#/signup">Create a workspace</a>`}</div>`;
  wrap.append(box);
  box.querySelector("#go").onclick = async () => {
    const err = box.querySelector("#err"); err.textContent = "";
    try {
      const body = { email: box.querySelector("#email").value, password: box.querySelector("#pw").value };
      if (isUp) body.org_name = box.querySelector("#org").value;
      const r = await api(isUp ? "/auth/signup" : "/auth/login", { method: "POST", body, auth: false });
      store.set(r);
      location.hash = "#/overview";
    } catch (e) { err.textContent = e.message || "failed"; }
  };
  return wrap;
}

// ---------- overview ----------
async function overviewView() {
  const m = page("Overview", store.email);
  m.append(el("div", { textContent: "loading…", className: "sub" }));
  shell("#/overview", m);
  try {
    const [me, usage] = await Promise.all([api("/auth/whoami"), api("/usage").catch(() => null)]);
    m.innerHTML = "";
    m.append(el("h1", { textContent: "Overview" }), el("div", { className: "sub", textContent: `${me.email} · ${me.role}${me.verified ? " · verified" : " · unverified"}` }));
    const g = el("div", { className: "grid" }); g.style.gridTemplateColumns = "repeat(4,1fr)";
    if (usage) {
      const stat = (k, v, s) => { const c = el("div", { className: "card" }); c.innerHTML = `<div class="lbl">${k}</div><div class="mono" style="font-size:24px;font-weight:700;margin-top:6px">${v}</div><div class="sub" style="margin:0">${s || ""}</div>`; return c; };
      g.append(
        stat("plan", esc(usage.plan), `${usage.included_minutes} min included`),
        stat("call minutes", usage.call_minutes, `period ${usage.period}`),
        stat("calls", usage.calls, ""),
        stat("overage", "$" + (usage.overage_cents / 100).toFixed(2), usage.over_hard_cap ? "HARD CAP HIT" : "metered"),
      );
    }
    m.append(g);

    if (me.role === "admin") {
      const pc = el("div", { className: "card" }); pc.style.marginTop = "18px";
      pc.innerHTML = `<div class="lbl" style="margin-bottom:10px">change plan</div><div class="row" id="plans"></div><div class="ok" id="planmsg" style="margin-top:10px"></div>`;
      for (const p of ["pilot", "starter", "growth", "scale", "enterprise"]) {
        const b = el("button", { className: "btn ghost", textContent: p });
        if (usage && usage.plan === p) { b.style.borderColor = "var(--accent)"; b.style.color = "var(--accent)"; }
        b.onclick = async () => { try { const r = await api("/orgs/plan", { method: "POST", body: { plan: p } }); pc.querySelector("#planmsg").textContent = `now on ${r.plan}`; setTimeout(overviewView, 600); } catch (e) { pc.querySelector("#planmsg").textContent = e.message; } };
        pc.querySelector("#plans").append(b);
      }
      m.append(pc);
    }
    shell("#/overview", m);
  } catch (e) { m.append(el("div", { className: "err", textContent: e.message })); }
}

// ---------- keys ----------
async function keysView() {
  const m = page("API keys", "One key per integration. Plaintext shown once.");
  shell("#/keys", m);
  const admin = store.role === "admin";
  const issue = el("div", { className: "card" });
  issue.innerHTML = `<div class="row"><input class="field" id="kn" placeholder="key name" style="flex:1"><button class="btn" id="ki" ${admin ? "" : "disabled"}>Issue key</button></div><div class="mono" id="kout" style="margin-top:12px;font-size:13px;word-break:break-all"></div>`;
  m.append(issue);
  const list = el("div", { className: "card" }); list.style.marginTop = "16px"; list.textContent = "loading…";
  m.append(list);

  async function refresh() {
    try {
      const keys = await api("/orgs/keys");
      list.innerHTML = "";
      const t = el("table");
      t.innerHTML = "<thead><tr><th>name</th><th>prefix</th><th>created</th><th>last used</th><th></th></tr></thead>";
      const tb = el("tbody");
      for (const k of keys) {
        const tr = el("tr");
        tr.innerHTML = `<td>${esc(k.name)}</td><td class="mono" style="color:var(--t2)">${esc(k.prefix)}…</td><td class="mono" style="color:var(--t2)">${new Date(k.created_at * 1000).toLocaleString()}</td><td class="mono" style="color:var(--t2)">${k.last_used_at ? new Date(k.last_used_at * 1000).toLocaleTimeString() : "—"}</td><td></td>`;
        if (!k.revoked && admin) {
          const b = el("button", { className: "btn danger", textContent: "Revoke", style: "padding:4px 10px;font-size:12px" });
          b.onclick = async () => { await api(`/orgs/keys/${k.id}`, { method: "DELETE" }); refresh(); };
          tr.lastChild.append(b);
        } else if (k.revoked) tr.lastChild.innerHTML = `<span class="mono" style="color:var(--intervene);font-size:11px">revoked</span>`;
        tb.append(tr);
      }
      t.append(tb); list.append(t);
      if (!keys.length) list.append(el("div", { className: "sub", textContent: "no keys yet" }));
    } catch (e) { list.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  }
  issue.querySelector("#ki").onclick = async () => {
    try {
      const r = await api("/orgs/keys", { method: "POST", body: { name: issue.querySelector("#kn").value || "unnamed" } });
      store.liveKey = r.key;
      issue.querySelector("#kout").innerHTML = `<span class="ok">copy now — shown once:</span><br>${esc(r.key)}<br><span class="sub">saved for the Live view this session</span>`;
      refresh();
    } catch (e) { issue.querySelector("#kout").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  refresh();
}

// ---------- cases ----------
async function casesView() {
  const m = page("Cases", "Every ALERT and above opens one.");
  shell("#/cases", m);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);
  try {
    const cases = await api("/cases");
    box.innerHTML = "";
    const t = el("table");
    t.innerHTML = "<thead><tr><th>session</th><th>opened</th><th>peak</th><th>score</th><th>feedback</th></tr></thead>";
    const tb = el("tbody");
    for (const c of cases) {
      const tr = el("tr", { style: "cursor:pointer" });
      tr.innerHTML = `<td class="mono">${esc(c.session_id)}</td><td class="mono" style="color:var(--t2)">t${c.opened_at}</td><td><span class="pill st-${c.peak_state}">${c.peak_state}</span></td><td class="mono">${c.peak_score}</td><td style="color:var(--t2)">${c.feedback || "unlabelled"}</td>`;
      tr.onclick = () => { location.hash = `#/cases/${encodeURIComponent(c.session_id)}`; };
      tb.append(tr);
    }
    t.append(tb); box.append(t);
    if (!cases.length) box.append(el("div", { className: "sub", textContent: "no cases — run a replay from the Live view" }));
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

async function caseDetailView(sid) {
  const m = page("Case " + sid, "");
  shell("#/cases", m);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);
  try {
    const c = await api(`/cases/${encodeURIComponent(sid)}`);
    box.innerHTML = `<div class="row" style="gap:12px"><span class="pill st-${c.peak_state}">${c.peak_state}</span><span class="mono">peak ${c.peak_score}</span><span class="sub" style="margin:0">opened t${c.opened_at} · ${c.decisions.length} decisions</span></div>`;
    const tx = el("div", { style: "font-family:var(--mono);font-size:12px;margin-top:16px;line-height:1.7" });
    for (const [role, text, t] of c.transcript || []) tx.innerHTML += `<div><span style="color:var(--t2)">${role} t${t}</span> <span style="color:${role === "CALLER" ? "var(--caller)" : role === "CALLEE" ? "var(--callee)" : "var(--t2)"}">${esc(text)}</span></div>`;
    if (!(c.transcript || []).length) tx.innerHTML = `<span class="sub">transcript not retained (RF_RETAIN_TRANSCRIPTS off)</span>`;
    box.append(tx);
    const dl = el("div", { style: "margin-top:16px" });
    for (const d of c.decisions) dl.innerHTML += `<div class="row" style="gap:10px;font-size:12px"><span class="mono" style="color:var(--t2)">t${d.t}</span><span class="pill st-${d.state}">${d.state}</span><span class="mono">${d.score}</span><span class="sub" style="margin:0">${d.counterfactual || ""}</span></div>`;
    box.append(dl);

    const fb = el("div", { className: "card" }); fb.style.marginTop = "16px";
    fb.innerHTML = `<div class="lbl" style="margin-bottom:10px">feedback — trains the model</div><div class="row" id="fbb"></div><input class="field" id="fbn" placeholder="note (optional)" style="margin-top:10px"><div class="ok" id="fbm" style="margin-top:8px"></div>`;
    for (const lab of ["fraud", "benign", "unclear"]) {
      const b = el("button", { className: "btn ghost", textContent: lab });
      if (c.feedback === lab) { b.style.borderColor = "var(--accent)"; b.style.color = "var(--accent)"; }
      b.onclick = async () => { try { await api(`/cases/${encodeURIComponent(sid)}/feedback`, { method: "POST", body: { label: lab, note: fb.querySelector("#fbn").value } }); fb.querySelector("#fbm").textContent = "saved · " + lab; } catch (e) { fb.querySelector("#fbm").textContent = e.message; } };
      fb.querySelector("#fbb").append(b);
    }
    m.append(fb);
    box.append(el("a", { href: "#/cases", textContent: "← all cases", style: "display:inline-block;margin-top:16px;font-size:13px" }));
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// ---------- live ----------
let detach = null;
function liveView() {
  const m = page("Live", "speakerphone capture, or drive a scam fixture through the pipeline");
  shell("#/live", m);
  const sid = "web-" + Math.random().toString(36).slice(2, 8);
  const org = store.org || "";
  const key = store.liveKey;
  const q = key ? `?key=${encodeURIComponent(key)}&tenant=${encodeURIComponent(org)}` : `?tenant=${encodeURIComponent(org)}`;

  const c = el("div", { className: "card" });
  c.innerHTML = `
    <div class="row" style="flex-wrap:wrap">
      <input class="field" id="sid" value="${sid}" style="width:180px">
      <button class="btn" id="watch">Watch</button>
      <select class="field" id="fx" style="width:auto">
        <option value="fx_gift_card_en_001">gift-card (en)</option>
        <option value="fx_tech_support_en_001">tech-support (en)</option>
        <option value="fx_bank_impersonation_fr_001">bank (fr)</option>
        <option value="fx_real_bank_frauddesk_fr_001">real bank desk (benign)</option>
      </select>
      <button class="btn ghost" id="replay">Replay through pipeline</button>
      <button class="btn ghost" id="cap">Start capture (mic)</button>
      <button class="btn ghost" id="stop" disabled>Stop</button>
      <span class="mono" id="status" style="color:var(--t2);font-size:12px">idle</span>
    </div>
    ${key ? "" : `<div class="err" style="margin-top:10px">No API key in this session — issue one on the Keys page for the live stream to authorise (works without one only in dev mode).</div>`}
    <div class="live-grid" style="margin-top:18px">
      <div><div class="gauge"><span id="gfill"></span></div><div class="glabel" id="glabel">0 · CALM</div></div>
      <div><div id="transcript"></div><div id="timeline"></div><div id="cf"></div></div>
    </div>
    <details style="margin-top:16px"><summary class="sub">speakerphone capture — mic level</summary>
      <div class="meter" style="margin-top:8px"><label>full-band (near voice)</label><div class="bar"><span id="mwide"></span></div></div>
      <div class="meter" style="margin-top:6px"><label>&le; 3.4 kHz (far / phone voice)</label><div class="bar"><span id="mtele"></span></div></div>
    </details>`;
  m.append(c);

  const els = { gauge: $("#gfill", c), gaugeLabel: $("#glabel", c), transcript: $("#transcript", c), timeline: $("#timeline", c), cf: $("#cf", c), status: $("#status", c) };
  const setBar = (n, rms) => { const db = 20 * Math.log10(Math.max(rms, 1e-6)); n.style.width = Math.max(0, Math.min(100, ((db + 60) / 60) * 100)) + "%"; };

  $("#watch", c).onclick = () => { detach && detach(); detach = mountConsole($("#sid", c).value, els, q); $("#stop", c).disabled = false; };
  $("#replay", c).onclick = async () => {
    const s = $("#sid", c).value, fx = $("#fx", c).value;
    detach && detach(); detach = mountConsole(s, els, q);
    $("#stop", c).disabled = false;
    els.status.textContent = "replaying " + fx;
    try { const r = await fetch(`/replay/${fx}?session=${encodeURIComponent(s)}&tenant=${encodeURIComponent(org)}&speed=8`, { method: "POST" }); const j = await r.json(); els.status.textContent = `replayed · ${j.decisions} decisions`; }
    catch (e) { els.status.textContent = "replay failed: " + e.message; }
  };
  $("#cap", c).onclick = async () => {
    $("#cap", c).disabled = true;
    try {
      await startCapture($("#sid", c).value, {
        query: q,
        onStatus: (s) => (els.status.textContent = s),
        onLevel: ({ wide, tele }) => { setBar($("#mwide", c), wide); setBar($("#mtele", c), tele); },
      });
      detach && detach(); detach = mountConsole($("#sid", c).value, els, q); $("#stop", c).disabled = false;
    } catch (e) { els.status.textContent = "error: " + e.message; $("#cap", c).disabled = false; }
  };
  $("#stop", c).onclick = () => { stopCapture(); detach && detach(); els.status.textContent = "stopped"; $("#cap", c).disabled = false; $("#stop", c).disabled = true; };
}

// ---------- router ----------
function route() {
  if (detach) { detach(); detach = null; }
  const h = location.hash || "#/overview";
  if (!store.token && !h.startsWith("#/signup")) { shell(null, authView(h.startsWith("#/signup") ? "signup" : "signin")); if (h !== "#/signin" && h !== "#/signup") location.hash = "#/signin"; return; }
  if (h === "#/signup") return shell(null, authView("signup"));
  if (h === "#/signin") { if (store.token) { location.hash = "#/overview"; return; } return shell(null, authView("signin")); }
  if (h.startsWith("#/cases/")) return caseDetailView(decodeURIComponent(h.slice("#/cases/".length)));
  if (h === "#/cases") return casesView();
  if (h === "#/keys") return keysView();
  if (h === "#/live") return liveView();
  return overviewView();
}
window.addEventListener("hashchange", route);
route();

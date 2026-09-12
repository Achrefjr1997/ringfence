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
  const tabs = [["#/overview", "Overview"], ["#/wall", "Wall"], ["#/calls", "Calls"], ["#/cases", "Cases"], ["#/keys", "API keys"], ["#/live", "Live"]];
  if (store.role === "admin") tabs.splice(tabs.findIndex(([h]) => h === "#/live"), 0, ["#/team", "Team"]);
  for (const [href, label] of tabs) {
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
      t.innerHTML = "<thead><tr><th>name</th><th>prefix</th><th>created</th><th>last used</th><th>this month</th><th></th></tr></thead>";
      const tb = el("tbody");
      for (const k of keys) {
        const tr = el("tr");
        const usage = `${(k.call_minutes ?? 0).toFixed(1)} min · ${k.calls ?? 0} calls`;
        tr.innerHTML = `<td>${esc(k.name)}</td><td class="mono" style="color:var(--t2)">${esc(k.prefix)}…</td><td class="mono" style="color:var(--t2)">${new Date(k.created_at * 1000).toLocaleString()}</td><td class="mono" style="color:var(--t2)">${k.last_used_at ? new Date(k.last_used_at * 1000).toLocaleTimeString() : "—"}</td><td class="mono" style="color:var(--t2)">${usage}</td><td></td>`;
        if (!k.revoked && admin) {
          const b = el("button", { className: "btn danger", textContent: "Revoke", style: "padding:4px 10px;font-size:12px" });
          b.onclick = async () => { await api(`/orgs/keys/${k.id}`, { method: "DELETE" }); refresh(); };
          tr.lastChild.append(b);
        } else if (k.revoked) tr.lastChild.innerHTML = `<span class="mono" style="color:var(--intervene);font-size:11px">revoked</span>`;
        tb.append(tr);
      }
      t.append(tb); list.append(t);
      if (!keys.length) list.append(el("div", { className: "empty", textContent: "no keys yet" }));
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
    if (!cases.length) box.append(el("div", { className: "empty", textContent: "no cases — run a replay from the Live view" }));
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

// ---------- team (P4 RBAC) ----------
async function teamView() {
  const m = page("Team", "Set each operator's manager and link their login to the employee id integrations send.");
  shell("#/team", m);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);
  try {
    const users = await api("/orgs/users");
    box.innerHTML = "";
    const t = el("table");
    t.innerHTML = "<thead><tr><th>user</th><th>role</th><th>manager</th><th>employee ref</th></tr></thead>";
    const tb = el("tbody");
    for (const u of users) {
      const tr = el("tr");
      const mgr = el("select", { className: "field", style: "max-width:200px" });
      mgr.append(el("option", { value: "", textContent: "— none —" }));
      for (const o of users) if (o.user_id !== u.user_id) mgr.append(el("option", { value: o.user_id, textContent: o.email }));
      mgr.value = u.manager_id || "";
      mgr.onchange = () => api(`/orgs/users/${u.user_id}`, { method: "PATCH", body: { manager_id: mgr.value || null } }).catch((e) => alert(e.message));
      const ref = el("input", { className: "field", style: "max-width:200px", value: u.user_ref || "" });
      ref.onchange = () => api(`/orgs/users/${u.user_id}`, { method: "PATCH", body: { user_ref: ref.value.trim() || null } }).catch((e) => alert(e.message));
      tr.append(
        el("td", { innerHTML: `<span class="mono" style="font-size:12px">${esc(u.email)}</span>` }),
        el("td", { textContent: u.role }),
        el("td"), el("td"),
      );
      tr.children[2].append(mgr); tr.children[3].append(ref);
      tb.append(tr);
    }
    t.append(tb); box.append(t);
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// ---------- calls (oversight) ----------
const ST_COLOR = { CALM: "--calm", WATCH: "--watch", ALERT: "--alert", INTERVENE: "--intervene", RESOLVED: "--alert" };

function fmtDur(s) {
  s = Math.round(s || 0);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

async function callsView() {
  const m = page("Calls", "Every call this org has run — by employee and by the API key that drove it.");
  shell("#/calls", m);
  const bar = el("div", { className: "row", style: "gap:10px;margin-bottom:14px;flex-wrap:wrap" });
  const grp = el("select", { className: "field", style: "max-width:170px" });
  grp.innerHTML = `<option value="calls">All calls</option><option value="users">By employee</option>`;
  const scope = el("select", { className: "field", style: "max-width:130px" });
  scope.innerHTML = `<option value="all">Everyone</option><option value="team">My team</option><option value="mine">Mine</option>`;
  const sel = el("select", { className: "field", style: "max-width:240px" });
  sel.innerHTML = `<option value="">all API keys</option>`;
  const usr = el("input", { className: "field", style: "max-width:220px", placeholder: "filter by employee id" });
  bar.append(grp, scope, sel, usr);
  m.append(bar);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);

  let keyName = {};
  try {
    for (const k of await api("/orgs/keys")) {
      keyName[k.id] = k.name;
      sel.append(el("option", { value: k.id, textContent: `${k.name} (${k.prefix}…)` }));
    }
  } catch { /* operator role can't list keys; filter stays "all" */ }

  function q() {
    const p = new URLSearchParams();
    if (sel.value) p.set("key_id", sel.value);
    if (usr.value.trim()) p.set("user", usr.value.trim());
    if (scope.value !== "all") p.set("scope", scope.value);
    const s = p.toString();
    return s ? "?" + s : "";
  }

  async function loadCalls() {
    const calls = await api("/calls" + q());
    box.innerHTML = "";
    const t = el("table");
    t.innerHTML = "<thead><tr><th>started</th><th>employee</th><th>API key</th><th>duration</th><th>peak</th><th>score</th><th></th></tr></thead>";
    const tb = el("tbody");
    for (const c of calls) {
      const tr = el("tr", { style: "cursor:pointer" });
      const started = c.started_at ? new Date(c.started_at * 1000).toLocaleString() : "—";
      const un = c.user_ref ? esc(c.user_label || c.user_ref) : "<span style='color:var(--t2)'>—</span>";
      const kn = c.api_key_id ? esc(keyName[c.api_key_id] || c.api_key_id.slice(0, 8)) : "<span style='color:var(--t2)'>dev / none</span>";
      const liveTag = c.live ? `<span class="pill st-WATCH">live</span>` : "";
      tr.innerHTML = `<td class="mono" style="color:var(--t2)">${started}</td><td>${un}</td><td>${kn}</td><td class="mono">${c.live ? "—" : fmtDur(c.duration_s)}</td><td><span class="pill st-${c.peak_state}">${c.peak_state}</span></td><td class="mono">${c.peak_score}</td><td>${liveTag}</td>`;
      tr.onclick = () => { location.hash = `#/calls/${encodeURIComponent(c.session_id)}`; };
      tb.append(tr);
    }
    t.append(tb); box.append(t);
    if (!calls.length) box.append(el("div", { className: "empty", textContent: "no calls yet — run one from the Live view" }));
  }

  async function loadUsers() {
    const rows = await api("/calls/users");
    box.innerHTML = "";
    const t = el("table");
    t.innerHTML = "<thead><tr><th>employee</th><th>calls</th><th>alerts</th><th>interventions</th><th>peak</th><th>last</th></tr></thead>";
    const tb = el("tbody");
    for (const u of rows) {
      const tr = el("tr", { style: "cursor:pointer" });
      const last = u.last_at ? new Date(u.last_at * 1000).toLocaleString() : "—";
      tr.innerHTML = `<td>${esc(u.user_label || u.user_ref)}</td><td class="mono">${u.calls}</td><td class="mono">${u.alerts}</td><td class="mono">${u.interventions}</td><td><span class="pill st-${u.peak_state}">${u.peak_state}</span></td><td class="mono" style="color:var(--t2)">${last}</td>`;
      tr.onclick = () => { location.hash = `#/employee/${encodeURIComponent(u.user_ref)}`; };
      tb.append(tr);
    }
    t.append(tb); box.append(t);
    if (!rows.length) box.append(el("div", { className: "empty", textContent: "no calls attributed to an employee yet — the integration passes ?user=<id> on /ws/capture" }));
  }

  async function load() {
    box.textContent = "loading…";
    const byUser = grp.value === "users";
    sel.hidden = byUser; usr.hidden = byUser; scope.hidden = byUser;
    try { await (byUser ? loadUsers() : loadCalls()); }
    catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  }
  grp.onchange = load; sel.onchange = load; scope.onchange = load;
  usr.onchange = load;
  load();
}

function scoreChart(scores, maxT = 0) {
  const W = 680, H = 190, PAD = 28;
  const c = el("canvas", { width: W, height: H, style: "width:100%;max-width:680px;height:auto;margin-top:8px;cursor:crosshair" });
  const g = c.getContext("2d");
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim() || "#888";
  const tMax = Math.max(1, maxT, ...scores.map((p) => p.t));
  const x = (t) => PAD + (W - PAD * 2) * (Math.max(0, Math.min(tMax, t)) / tMax);
  const y = (s) => H - PAD - (H - PAD * 2) * (Math.max(0, Math.min(100, s)) / 100);

  function draw(playT) {
    g.clearRect(0, 0, W, H);
    for (const [lo, hi, col] of [[0, 25, "--calm"], [25, 55, "--watch"], [55, 80, "--alert"], [80, 100, "--intervene"]]) {
      g.fillStyle = css(col) + "18";
      g.fillRect(PAD, y(hi), W - PAD * 2, y(lo) - y(hi));
    }
    g.strokeStyle = "#ffffff14"; g.beginPath(); g.moveTo(PAD, y(0)); g.lineTo(W - PAD, y(0)); g.stroke();
    if (scores.length) {
      g.strokeStyle = css("--accent"); g.lineWidth = 2; g.beginPath();
      scores.forEach((p, i) => { const fn = i ? "lineTo" : "moveTo"; g[fn](x(p.t), y(p.score)); });
      g.stroke();
      for (const p of scores) {
        g.fillStyle = css(ST_COLOR[p.state] || "--accent");
        g.beginPath(); g.arc(x(p.t), y(p.score), 3, 0, 7); g.fill();
      }
    }
    if (playT != null && playT >= 0) {
      g.strokeStyle = css("--accent"); g.globalAlpha = 0.9; g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(x(playT), PAD - 6); g.lineTo(x(playT), H - PAD); g.stroke();
      g.globalAlpha = 1;
      g.fillStyle = css("--accent"); g.font = "10px ui-monospace,monospace";
      g.fillText(fmtDur(playT), Math.min(x(playT) + 4, W - 46), PAD - 8);
    }
    g.fillStyle = "#8A8F98"; g.font = "10px ui-monospace,monospace";
    g.fillText("100", 2, y(100) + 4); g.fillText("0", 2, y(0) + 4);
    g.fillText(Math.round(tMax) + "s", W - PAD - 10, H - 8);
  }
  draw(null);
  c.seek = (t) => draw(t);
  c._t = (px) => ((px / c.clientWidth) * W - PAD) / (W - PAD * 2) * tMax;  // pixel -> seconds
  return c;
}

async function callDetailView(sid) {
  const m = page("Call " + sid, "");
  shell("#/calls", m);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);
  try {
    const c = await api(`/calls/${encodeURIComponent(sid)}`);
    box.innerHTML = `<div class="row" style="gap:12px;flex-wrap:wrap"><span class="pill st-${c.peak_state}">${c.peak_state}</span><span class="mono">peak ${c.peak_score}</span>${c.user_ref ? `<span class="mono" style="color:var(--t2)">${esc(c.user_label || c.user_ref)}</span>` : ""}${c.private ? `<span class="pill st-INTERVENE">🔒 private</span>` : ""}<span class="sub" style="margin:0">${c.live ? "live now" : fmtDur(c.duration_s)} · ${(c.scores || []).length} decisions</span>${c.has_case ? `<a href="#/cases/${encodeURIComponent(sid)}" style="font-size:12px">open case →</a>` : ""}</div>`;
    const turns = c.transcript || [];
    const maxT = Math.max(c.duration_s || 0, ...turns.map((t) => t[2] || 0));
    const chart = scoreChart(c.scores || [], maxT);
    box.append(chart);

    let au = null;
    const seek = (t) => { chart.seek(t); if (au) { try { au.currentTime = t; } catch {} } };
    chart.onclick = (ev) => { const r = chart.getBoundingClientRect(); seek(chart._t(ev.clientX - r.left)); };

    if (c.audio) {
      const src = `/calls/${encodeURIComponent(sid)}/audio?token=${encodeURIComponent(store.token || "")}`;
      au = el("audio", { controls: true, src, style: "width:100%;max-width:680px;margin-top:12px;display:block" });
      au.addEventListener("timeupdate", () => chart.seek(au.currentTime));
      box.append(au, el("a", { href: src + "&download=1", textContent: "download recording", style: "font-size:12px" }));
    }
    if (c.can_manage) box.append(accessPanel(sid, c));

    const tx = el("div", { style: "font-family:var(--mono);font-size:12px;margin-top:16px;line-height:1.7" });
    for (const [role, text, t] of turns) {
      const d = el("div", { style: "cursor:pointer" });
      d.innerHTML = `<span style="color:var(--t2)">${role} ${fmtDur(t)}</span> <span style="color:${role === "CALLER" ? "var(--caller)" : role === "CALLEE" ? "var(--callee)" : "var(--t2)"}">${esc(text)}</span>`;
      d.onclick = () => seek(t || 0);
      tx.append(d);
    }
    if (!turns.length) tx.innerHTML = `<span class="sub">transcript not retained (needs RF_RETAIN_TRANSCRIPTS, and only ALERT+ calls open a case)</span>`;
    box.append(tx);
    box.append(el("a", { href: "#/calls", textContent: "← all calls", style: "display:inline-block;margin-top:16px;font-size:13px" }));
    m.append(commentsPanel(sid, seek));
    if (store.role === "admin") m.append(accessLogPanel(sid));
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

function accessLogPanel(sid) {
  const p = el("div", { className: "card" }); p.style.marginTop = "16px";
  p.innerHTML = `<div class="lbl" style="margin-bottom:10px">Access log</div><div id="alog" class="sub">loading…</div>`;
  const box = p.querySelector("#alog");
  api(`/calls/${encodeURIComponent(sid)}/access-log`).then((rows) => {
    if (!rows.length) { box.className = "empty"; box.textContent = "no access recorded yet"; return; }
    box.innerHTML = "";
    for (const r of rows) {
      box.append(el("div", { className: "mono", style: "font-size:12px;color:var(--t2);margin:2px 0",
        textContent: `${new Date(r.at * 1000).toLocaleString()}  ${r.action.padEnd(11)} ${r.actor_email}${r.ip ? "  " + r.ip : ""}` }));
    }
  }).catch((e) => { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; });
  return p;
}

function accessPanel(sid, c) {
  const p = el("div", { style: "margin-top:12px;border-top:1px solid var(--line, #ffffff14);padding-top:10px" });
  const priv = el("button", { className: "btn ghost", textContent: c.private ? "Make public" : "Make private", style: "font-size:12px" });
  priv.onclick = async () => { await api(`/calls/${encodeURIComponent(sid)}`, { method: "PATCH", body: { private: !c.private } }); callDetailView(sid); };
  const shareRow = el("div", { className: "row", style: "gap:8px;margin-top:8px;flex-wrap:wrap" });
  const email = el("input", { className: "field", style: "max-width:220px", placeholder: "share with (email)" });
  const add = el("button", { className: "btn", textContent: "Share", style: "font-size:12px" });
  add.onclick = async () => { try { await api(`/calls/${encodeURIComponent(sid)}/share`, { method: "POST", body: { email: email.value.trim() } }); callDetailView(sid); } catch (e) { alert(e.message); } };
  shareRow.append(email, add);
  const list = el("div", { className: "sub", style: "margin:6px 0 0" });
  list.textContent = (c.shared_with || []).length ? "shared with " + c.shared_with.length + " user(s)" : "not shared";
  p.append(priv, shareRow, list);
  return p;
}

// ---------- threaded review comments ----------
function fmtWhen(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ""; }

function commentsPanel(sid, seek) {
  const panel = el("div", { className: "card" }); panel.style.marginTop = "16px";
  panel.innerHTML = `<div class="lbl" style="margin-bottom:10px">Review thread</div><div id="cthread">loading…</div>`;
  const thread = panel.querySelector("#cthread");

  const form = el("div", { style: "margin-top:14px;border-top:1px solid var(--line, #ffffff14);padding-top:12px" });
  form.innerHTML = `
    <textarea class="field" id="cbody" rows="2" placeholder="comment on this call…"></textarea>
    <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap">
      <input class="field" id="ct" style="max-width:90px" placeholder="@ sec">
      <select class="field" id="cvis" style="max-width:150px"><option value="org">everyone</option><option value="mentions">mentions only</option><option value="private">private</option></select>
      <input class="field" id="cmnt" style="max-width:220px" placeholder="mention emails, comma" list="rf-people">
      <datalist id="rf-people"></datalist>
      <button class="btn" id="cadd">Comment</button>
    </div>
    <div id="cchips" class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px"></div>
    <div class="err" id="cerr" style="margin-top:6px"></div>`;
  panel.append(form);

  // @mention autocomplete: populate from the org roster (admins can list it)
  api("/orgs/users").then((users) => {
    const dl = form.querySelector("#rf-people");
    const chips = form.querySelector("#cchips");
    for (const u of users) dl.append(el("option", { value: u.email }));
    for (const u of users.slice(0, 8)) {
      const b = el("button", { className: "btn ghost", textContent: "@" + u.email.split("@")[0], style: "font-size:11px;padding:3px 8px" });
      b.onclick = () => {
        const f = form.querySelector("#cmnt");
        const has = f.value.split(",").map((s) => s.trim()).filter(Boolean);
        if (!has.includes(u.email)) f.value = [...has, u.email].join(", ");
      };
      chips.append(b);
    }
  }).catch(() => { /* operators can't list users — freeform entry still works */ });

  function node(c, replyTo) {
    const d = el("div", { style: `margin:10px 0;${replyTo ? "margin-left:22px;" : ""}` });
    const clickable = c.t_seconds != null && typeof seek === "function";
    const tchip = c.t_seconds != null ? `<span class="pill st-WATCH" data-seek="${c.t_seconds}" style="cursor:${clickable ? "pointer" : "default"}">@${fmtDur(c.t_seconds)}</span>` : "";
    const done = c.resolved_at ? `<span class="ok" style="font-size:11px">resolved</span>` : "";
    d.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap"><span class="mono" style="font-size:12px">${esc(c.author_email)}</span><span class="sub" style="margin:0">${fmtWhen(c.created_at)}${c.edited_at ? " · edited" : ""}</span>${tchip}${done}<span class="sub" style="margin:0">${c.visibility === "org" ? "" : c.visibility}</span></div>
      <div style="font-size:13px;margin:4px 0;white-space:pre-wrap">${esc(c.body)}</div>
      <div class="row" style="gap:10px;font-size:11px"><a href="#" data-a="reply">reply</a><a href="#" data-a="resolve">${c.resolved_at ? "reopen" : "resolve"}</a><a href="#" data-a="edit">edit</a><a href="#" data-a="del" style="color:var(--intervene)">delete</a></div>`;
    d.querySelector('[data-a="reply"]').onclick = (e) => { e.preventDefault(); form.querySelector("#cbody").focus(); form.dataset.parent = c.id; form.querySelector("#cadd").textContent = "Reply"; };
    d.querySelector('[data-a="resolve"]').onclick = async (e) => { e.preventDefault(); await api(`/calls/${encodeURIComponent(sid)}/comments/${c.id}/resolve`, { method: "POST", body: { resolved: !c.resolved_at } }); load(); };
    d.querySelector('[data-a="edit"]').onclick = async (e) => { e.preventDefault(); const v = prompt("edit comment", c.body); if (v != null) { await api(`/calls/${encodeURIComponent(sid)}/comments/${c.id}`, { method: "PATCH", body: { body: v } }); load(); } };
    d.querySelector('[data-a="del"]').onclick = async (e) => { e.preventDefault(); if (confirm("delete this comment?")) { await api(`/calls/${encodeURIComponent(sid)}/comments/${c.id}`, { method: "DELETE" }); load(); } };
    if (clickable) d.querySelector("[data-seek]").onclick = () => seek(Number(c.t_seconds));
    return d;
  }

  async function load() {
    try {
      const all = await api(`/calls/${encodeURIComponent(sid)}/comments`);
      thread.innerHTML = "";
      const tops = all.filter((c) => !c.parent_id);
      const kids = (id) => all.filter((c) => c.parent_id === id);
      if (!tops.length) thread.innerHTML = `<div class="empty">no comments yet</div>`;
      for (const c of tops) { thread.append(node(c, false)); for (const k of kids(c.id)) thread.append(node(k, true)); }
    } catch (e) { thread.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  }

  form.querySelector("#cadd").onclick = async () => {
    const body = form.querySelector("#cbody").value.trim();
    if (!body) return;
    const t = form.querySelector("#ct").value.trim();
    const mnt = form.querySelector("#cmnt").value.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      await api(`/calls/${encodeURIComponent(sid)}/comments`, { method: "POST", body: {
        body, visibility: form.querySelector("#cvis").value,
        t_seconds: t === "" ? null : Number(t),
        parent_id: form.dataset.parent || null, mentions: mnt,
      } });
      form.querySelector("#cbody").value = ""; form.querySelector("#cmnt").value = ""; form.querySelector("#ct").value = "";
      delete form.dataset.parent; form.querySelector("#cadd").textContent = "Comment";
      form.querySelector("#cerr").textContent = ""; load();
    } catch (e) { form.querySelector("#cerr").textContent = e.message; }
  };
  load();
  return panel;
}

// ---------- employee timeline (P9) ----------
const _ORD = { CALM: 0, WATCH: 1, ALERT: 2, INTERVENE: 3, RESOLVED: 2 };

async function employeeView(ref) {
  const m = page("Employee · " + ref, "Every call attributed to this employee.");
  shell("#/calls", m);
  const box = el("div", { className: "card" }); box.textContent = "loading…"; m.append(box);
  try {
    const calls = await api(`/calls?user=${encodeURIComponent(ref)}&limit=500`);
    box.innerHTML = "";
    const alerts = calls.filter((c) => _ORD[c.peak_state] >= 2).length;
    const interv = calls.filter((c) => _ORD[c.peak_state] >= 3).length;
    box.append(el("div", { className: "row", style: "gap:16px;flex-wrap:wrap", innerHTML:
      `<span class="mono">${calls.length} calls</span><span class="mono" style="color:var(--alert)">${alerts} alerts</span><span class="mono" style="color:var(--intervene)">${interv} interventions</span>` }));

    // peak-state histogram
    const hist = { CALM: 0, WATCH: 0, ALERT: 0, INTERVENE: 0 };
    for (const c of calls) hist[c.peak_state === "RESOLVED" ? "ALERT" : c.peak_state] = (hist[c.peak_state === "RESOLVED" ? "ALERT" : c.peak_state] || 0) + 1;
    const hmax = Math.max(1, ...Object.values(hist));
    const hd = el("div", { style: "margin-top:14px" });
    for (const [st, n] of Object.entries(hist)) {
      hd.append(el("div", { className: "row", style: "gap:8px;align-items:center;margin:3px 0", innerHTML:
        `<span class="pill st-${st}" style="min-width:74px;text-align:center">${st}</span><div class="bar" style="flex:1;max-width:320px"><span style="width:${(n / hmax) * 100}%"></span></div><span class="mono" style="color:var(--t2)">${n}</span>` }));
    }
    box.append(hd);

    // alerts per day
    const byDay = {};
    for (const c of calls) {
      if (_ORD[c.peak_state] < 2 || !c.started_at) continue;
      const d = new Date(c.started_at * 1000).toISOString().slice(0, 10);
      byDay[d] = (byDay[d] || 0) + 1;
    }
    const days = Object.keys(byDay).sort();
    if (days.length) {
      const dmax = Math.max(...Object.values(byDay));
      const spark = el("div", { style: "display:flex;gap:3px;align-items:flex-end;height:56px;margin-top:16px" });
      for (const d of days) spark.append(el("div", { title: `${d}: ${byDay[d]}`, style: `width:10px;background:var(--alert);height:${(byDay[d] / dmax) * 100}%` }));
      box.append(el("div", { className: "sub", style: "margin:16px 0 0", textContent: "alerts per day" }), spark);
    }

    // the calls
    const t = el("table"); t.style.marginTop = "16px";
    t.innerHTML = "<thead><tr><th>started</th><th>API key</th><th>duration</th><th>peak</th><th>score</th></tr></thead>";
    const tb = el("tbody");
    for (const c of calls) {
      const tr = el("tr", { style: "cursor:pointer" });
      tr.innerHTML = `<td class="mono" style="color:var(--t2)">${c.started_at ? new Date(c.started_at * 1000).toLocaleString() : "—"}</td><td class="mono" style="color:var(--t2)">${c.api_key_id ? esc(c.api_key_id.slice(0, 8)) : "—"}</td><td class="mono">${c.live ? "—" : fmtDur(c.duration_s)}</td><td><span class="pill st-${c.peak_state}">${c.peak_state}</span></td><td class="mono">${c.peak_score}</td>`;
      tr.onclick = () => { location.hash = `#/calls/${encodeURIComponent(c.session_id)}`; };
      tb.append(tr);
    }
    t.append(tb); box.append(t);
    if (!calls.length) box.append(el("div", { className: "empty", textContent: "no calls for this employee" }));
    box.append(el("a", { href: "#/calls", textContent: "← all calls", style: "display:inline-block;margin-top:16px;font-size:13px" }));
  } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// ---------- wall (P8: every live call) ----------
let wallEs = null;

function wallView() {
  const m = page("Wall", "Every call happening right now across the org.");
  shell("#/wall", m);
  const grid = el("div", { style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px" });
  m.append(grid);
  const empty = el("div", { className: "empty" }); empty.textContent = "no live calls"; m.append(empty);
  const cards = new Map();

  function render(list) {
    const seen = new Set();
    for (const s of list) {
      seen.add(s.session_id);
      let c = cards.get(s.session_id);
      if (!c) { c = el("div", { className: "card", style: "cursor:pointer" }); c.onclick = () => { location.hash = `#/calls/${encodeURIComponent(s.session_id)}`; }; grid.append(c); cards.set(s.session_id, c); }
      c.dataset.state = s.state; c.dataset.score = s.score;
      paint(c, s);
    }
    for (const [id, c] of cards) if (!seen.has(id)) { c.remove(); cards.delete(id); }
    empty.hidden = cards.size > 0;
  }
  function paint(c, s) {
    const who = s.user_ref ? esc(s.user_ref) : "<span style='color:var(--t2)'>—</span>";
    const secs = Math.max(0, Math.round(Date.now() / 1000 - (s.started_at || 0)));
    const st = c.dataset.state || s.state || "CALM";
    const sc = Number(c.dataset.score ?? s.score ?? 0);
    c.innerHTML = `<div class="row" style="justify-content:space-between"><span class="mono" style="font-size:12px">${esc((s.session_id || "").slice(0, 12))}</span><span class="pill st-${st}">${st}</span></div>
      <div class="bar" style="margin:8px 0"><span style="width:${Math.min(100, sc)}%"></span></div>
      <div class="row" style="justify-content:space-between;font-size:11px;color:var(--t2)"><span>${who}</span><span class="mono">${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}</span></div>`;
  }

  async function seed() {
    try { render(await api("/sessions")); }
    catch (e) { empty.textContent = e.message; }
  }
  seed();
  const poll = setInterval(seed, 5000);
  const tick = setInterval(() => { for (const [id, c] of cards) paint(c, { session_id: id, started_at: Number(c.dataset.started || 0) }); }, 1000);

  const key = store.liveKey, org = store.org || "";
  const q = key ? `?key=${encodeURIComponent(key)}&tenant=${encodeURIComponent(org)}` : `?tenant=${encodeURIComponent(org)}`;
  wallEs && wallEs.close();
  wallEs = new EventSource(`/events${q}`);
  wallEs.addEventListener("decision", (ev) => {
    try {
      const d = JSON.parse(ev.data);
      const c = cards.get(d.session_id);
      if (c) { c.dataset.state = d.state; c.dataset.score = d.score; paint(c, { session_id: d.session_id, user_ref: c.dataset.user }); }
    } catch { /* ignore malformed frame */ }
  });
  wallEs.addEventListener("end", (ev) => { try { const d = JSON.parse(ev.data); const c = cards.get(d.session_id); if (c) { c.remove(); cards.delete(d.session_id); empty.hidden = cards.size > 0; } } catch { /* */ } });

  const stop = () => { clearInterval(poll); clearInterval(tick); wallEs && wallEs.close(); wallEs = null; window.removeEventListener("hashchange", stop); };
  window.addEventListener("hashchange", stop);
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
    <div class="row" style="margin-top:10px;flex-wrap:wrap">
      <input type="file" id="wav" accept="audio/*,.wav" class="field" style="width:auto;padding:6px">
      <select class="field" id="wspeed" style="width:auto"><option value="1">1×</option><option value="3" selected>3×</option><option value="6">6×</option></select>
      <button class="btn ghost" id="feed">Feed audio file → ASR</button>
      <span class="mono" style="color:var(--t2);font-size:12px">real audio through AssemblyAI</span>
    </div>
    ${key ? "" : `<div class="err" style="margin-top:10px">No API key in this session — issue one on the Keys page for the live stream to authorise (works without one only in dev mode).</div>`}
    <div class="live-grid" style="margin-top:18px">
      <div><div class="gauge"><span id="gfill"></span></div><div class="glabel" id="glabel">0 · CALM</div></div>
      <div><div id="transcript"></div><div id="timeline"></div><div id="cf"></div></div>
    </div>
    <div id="coach" class="coach"></div>
    <details style="margin-top:16px"><summary class="sub">speakerphone capture — mic level</summary>
      <div class="meter" style="margin-top:8px"><label>full-band (near voice)</label><div class="bar"><span id="mwide"></span></div></div>
      <div class="meter" style="margin-top:6px"><label>&le; 3.4 kHz (far / phone voice)</label><div class="bar"><span id="mtele"></span></div></div>
    </details>`;
  m.append(c);

  const els = { gauge: $("#gfill", c), gaugeLabel: $("#glabel", c), transcript: $("#transcript", c), timeline: $("#timeline", c), cf: $("#cf", c), status: $("#status", c), coach: $("#coach", c) };
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
  // feed a local audio file: decode -> 16k mono int16 -> stream over /ws/capture
  let feedWs = null, feedTimer = null;
  $("#feed", c).onclick = async () => {
    const f = $("#wav", c).files[0];
    if (!f) { els.status.textContent = "pick a .wav / audio file first"; return; }
    const s = $("#sid", c).value;
    const speed = Number($("#wspeed", c).value) || 3;
    try {
      els.status.textContent = "decoding " + f.name + "…";
      const ac = new AudioContext();
      const buf = await ac.decodeAudioData(await f.arrayBuffer());
      const off = new OfflineAudioContext(1, Math.ceil(buf.duration * 16000), 16000);
      const src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
      const rendered = await off.startRendering();
      const fl = rendered.getChannelData(0);
      const i16 = new Int16Array(fl.length);
      for (let i = 0; i < fl.length; i++) i16[i] = Math.max(-32768, Math.min(32767, fl[i] * 32767));
      ac.close();

      detach && detach(); detach = mountConsole(s, els, q);
      $("#stop", c).disabled = false;
      const wsq = q.startsWith("?") ? "&" + q.slice(1) : q;
      feedWs = new WebSocket(`ws://${location.host}/ws/capture?session=${encodeURIComponent(s)}&leg=mixed${wsq}`);
      feedWs.binaryType = "arraybuffer";
      feedWs.onmessage = (e) => { try { const mm = JSON.parse(e.data); if (mm.type === "rejected") els.status.textContent = "rejected: " + mm.reason; } catch {} };
      feedWs.onopen = () => {
        const FRAME = 320; let pos = 0;
        const total = i16.length;
        feedTimer = setInterval(() => {
          if (!feedWs || feedWs.readyState !== 1 || pos >= total) {
            clearInterval(feedTimer); feedTimer = null;
            if (feedWs && feedWs.readyState === 1) feedWs.close();
            els.status.textContent = pos >= total ? "file streamed — waiting for final turns" : "stopped";
            return;
          }
          feedWs.send(i16.buffer.slice(pos * 2, (pos + FRAME) * 2));
          pos += FRAME;
          els.status.textContent = `streaming ${(pos / 16000).toFixed(0)}s / ${(total / 16000).toFixed(0)}s`;
        }, Math.max(2, Math.round(20 / speed)));
      };
    } catch (e) { els.status.textContent = "feed failed: " + (e.message || e); }
  };

  const stopAll = () => {
    stopCapture(); detach && detach();
    if (feedTimer) { clearInterval(feedTimer); feedTimer = null; }
    if (feedWs && feedWs.readyState <= 1) feedWs.close();
    feedWs = null;
    els.status.textContent = "stopped"; $("#cap", c).disabled = false; $("#stop", c).disabled = true;
  };
  $("#stop", c).onclick = stopAll;
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
  if (h.startsWith("#/calls/")) return callDetailView(decodeURIComponent(h.slice("#/calls/".length)));
  if (h === "#/calls") return callsView();
  if (h === "#/keys") return keysView();
  if (h === "#/team") return teamView();
  if (h.startsWith("#/employee/")) return employeeView(decodeURIComponent(h.slice("#/employee/".length)));
  if (h === "#/wall") return wallView();
  if (h === "#/live") return liveView();
  return overviewView();
}
window.addEventListener("hashchange", route);
route();

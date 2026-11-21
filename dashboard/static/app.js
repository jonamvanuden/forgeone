/* ForgeOne Control Room — single-page UI. No build step, no framework.
   Every piece of data is escaped with esc() before it touches innerHTML. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const REFRESH_MS = 15000;
const state = { route: null, lastJSON: "", artTab: {}, evHidden: new Set(["node.start", "llm.request"]) };

/* ---------------- helpers ---------------- */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n = (v) => (v == null || Number.isNaN(Number(v)) ? 0 : Number(v));
const fmtInt = (v) => n(v).toLocaleString("en-US");
function fmtMs(ms) {
  if (ms == null) return "–";
  ms = n(ms);
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}
function fmtTokens(v) {
  v = n(v);
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`;
  return fmtInt(v);
}
const toDate = (s) => (s ? new Date(s) : null);
function fmtDate(s) {
  const d = toDate(s);
  if (!d) return "–";
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function ago(s) {
  const d = toDate(s);
  if (!d) return "never";
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}
const STATUS_TEXT = { ok: "Passed", error: "Failed", running: "Running" };
const status = (s) => `<span class="status ${esc(s)}">${esc(STATUS_TEXT[s] || s || "Unknown")}</span>`;
const pct = (a, b) => (n(b) ? `${Math.round((n(a) / n(b)) * 100)}%` : "–");

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function crumbs(items) {
  return `<nav class="crumbs" aria-label="Breadcrumb">${items
    .map((it, i) => (it.href ? `<a href="${esc(it.href)}">${esc(it.label)}</a>` : `<span aria-current="page">${esc(it.label)}</span>`) + (i < items.length - 1 ? '<span class="sep">/</span>' : ""))
    .join("")}</nav>`;
}
function stats(items) {
  return `<dl class="stats">${items
    .map((s) => `<div class="stat"><dt>${esc(s.label)}</dt><dd class="${esc(s.cls || "")}">${esc(s.value)}</dd></div>`)
    .join("")}</dl>`;
}

/* ---------------- build strip (signature element) ---------------- */
function strip(runs, { big = false, current = null } = {}) {
  if (!runs?.length) return `<div class="strip${big ? " big" : ""}"></div>`;
  const max = Math.max(...runs.map((r) => n(r.duration_ms)), 1);
  const ticks = runs
    .map((r) => {
      const h = Math.max(12, Math.round((n(r.duration_ms) / max) * 100));
      const label = `${STATUS_TEXT[r.status] || r.status}, ${fmtDate(r.started_at)}, ${fmtMs(r.duration_ms)}`;
      return `<button class="tick ${esc(r.status)}${r.run_id === current ? " current" : ""}" style="height:${h}%"
        data-run="${esc(r.run_id)}" data-tip="${esc(label)}" aria-label="Build: ${esc(label)}"></button>`;
    })
    .join("");
  return `<div class="strip${big ? " big" : ""}" role="group" aria-label="Builds, oldest to newest">${ticks}</div>`;
}

/* ---------------- views ---------------- */
async function viewGoals() {
  const [ov, goals] = await Promise.all([api("/api/overview"), api("/api/goals")]);
  const data = JSON.stringify([ov, goals]);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;

  const list = goals.length
    ? `<ul class="goals">${goals
        .map(
          (g) => `
      <li class="goal-row">
        <div>
          <h3><a href="#/goal/${esc(g.key)}">${esc(g.title)}</a></h3>
          <ul class="facts">
            <li><b>${fmtInt(g.builds)}</b> builds</li>
            <li class="ok"><b>${pct(g.passed, g.builds)}</b> passed</li>
            ${n(g.failed) ? `<li class="fail"><b>${fmtInt(g.failed)}</b> failed</li>` : ""}
            ${n(g.running) ? `<li><b>${fmtInt(g.running)}</b> running</li>` : ""}
            <li>median <b>${fmtMs(g.median_ms)}</b></li>
            <li>last build <b>${esc(ago(g.last_build))}</b></li>
          </ul>
        </div>
        <div>${strip(g.strip)}</div>
      </li>`
        )
        .join("")}</ul>`
    : `<div class="empty"><p>No builds yet. Start one on the server with <code>python -m forgeone.graph</code> and it will appear here within seconds.</p></div>`;

  main.innerHTML = `
    <h1 class="page">Goals</h1>
    <p class="lede">What ForgeOne has been asked to build, and how every build went. Open a goal to see its builds, or click a bar to jump straight into one.</p>
    ${stats([
      { label: "Goals", value: fmtInt(ov.goals) },
      { label: "Builds", value: fmtInt(ov.builds) },
      { label: "Passed", value: pct(ov.passed, ov.builds), cls: "ok" },
      { label: "Failed", value: fmtInt(ov.failed), cls: n(ov.failed) ? "fail" : "" },
      { label: "Tokens used", value: fmtTokens(ov.tokens) },
      { label: "Errors, last 24 h", value: fmtInt(ov.errors_24h), cls: n(ov.errors_24h) ? "fail" : "" },
    ])}
    <h2 class="sec">Goals <small>${fmtInt(goals.length)}, most recent first</small></h2>
    ${list}`;
  return true;
}

async function viewGoal(key) {
  const g = await api(`/api/goals/${encodeURIComponent(key)}`);
  const data = JSON.stringify(g);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;

  const versions = g.specs.length
    ? `<ul class="versions">${g.specs
        .map(
          (s) => `
      <li class="version">
        <span class="v">v${esc(s.version)}</span>
        <div>
          <a href="#/run/${esc(s.run_id)}">First built ${esc(fmtDate(s.ts))}</a>
          <div class="muted">${s.version === 1 ? "The first design" : `Changed ${esc(s.changed.join(", "))}`}${s.builds > 1 ? `, then rebuilt ${esc(s.builds - 1)} more ${s.builds === 2 ? "time" : "times"} unchanged` : ""}</div>
        </div>
        <span class="muted num">${s.max_steps != null ? `${esc(s.max_steps)} steps` : ""}${s.tools ? `, ${esc(s.tools.length)} tools` : ""}</span>
      </li>`
        )
        .join("")}</ul>`
    : `<p class="muted">No specs recorded for this goal yet.</p>`;

  const showIt = g.runs.some((r) => r.iterations != null);
  const rows = g.runs
    .map(
      (r) => `
    <tr data-href="#/run/${esc(r.run_id)}">
      <td><a class="row-link" href="#/run/${esc(r.run_id)}">${esc(fmtDate(r.started_at))}</a></td>
      <td>${status(r.status)}</td>
      <td class="r">${fmtMs(r.duration_ms)}</td>
      ${showIt ? `<td class="r">${r.iterations != null ? esc(r.iterations) : "–"}</td>` : ""}
      <td class="r">${fmtInt(r.llm_calls)}</td>
      <td class="r">${fmtTokens(n(r.total_input_tokens) + n(r.total_output_tokens))}</td>
      <td class="muted">${esc((r.error || "").slice(0, 90))}</td>
    </tr>`
    )
    .join("");

  main.innerHTML = `
    ${crumbs([{ label: "Goals", href: "#/" }, { label: g.title }])}
    <h1 class="goal">${esc(g.title)}</h1>
    <details class="goal-text"><summary>Show the full goal</summary><pre>${esc(g.goal || "No goal text recorded.")}</pre></details>
    ${stats([
      { label: "Builds", value: fmtInt(g.builds) },
      { label: "Passed", value: pct(g.passed, g.builds), cls: "ok" },
      { label: "Failed", value: fmtInt(g.failed), cls: n(g.failed) ? "fail" : "" },
      { label: "Median build", value: fmtMs(g.median_ms) },
      { label: "Tokens used", value: fmtTokens(g.tokens) },
      { label: "Last build", value: ago(g.last_build) },
    ])}
    ${strip(g.strip, { big: true })}
    <div class="strip-legend"><span>${esc(fmtDate(g.strip[0]?.started_at))}</span><span>Bar height is build time. Click a bar to open the build.</span><span>${esc(fmtDate(g.strip.at(-1)?.started_at))}</span></div>

    <h2 class="sec">Spec versions <small>how VoyagerOne's design changed over time</small></h2>
    ${versions}

    <h2 class="sec">Builds <small>${fmtInt(g.runs.length)}</small></h2>
    <div class="scroll-x"><table class="table">
      <thead><tr><th>Started</th><th>Result</th><th class="r">Duration</th>${showIt ? '<th class="r">Iterations</th>' : ""}<th class="r">Model calls</th><th class="r">Tokens</th><th>Error</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  return true;
}

function timeline(nodes) {
  if (!nodes.length) return `<p class="muted">No steps recorded for this build.</p>`;
  const t0 = Math.min(...nodes.map((x) => toDate(x.started).getTime()));
  const t1 = Math.max(...nodes.map((x) => toDate(x.ended).getTime()));
  const span = Math.max(t1 - t0, 1);
  const lanes = nodes
    .map((x, i) => {
      const left = ((toDate(x.started).getTime() - t0) / span) * 100;
      const width = Math.max((n(x.duration_ms) / span) * 100, 0.4);
      const name = `${x.node}${x.iteration != null ? `, round ${x.iteration}` : ""}`;
      return `<div class="lane">
        <span class="label" title="${esc(name)}">${esc(i + 1)}. ${esc(name)}</span>
        <div class="track"><div class="bar ${x.event === "node.error" ? "error" : ""}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"
          data-tip="${esc(`${name}: ${fmtMs(x.duration_ms)}${x.error ? `, ${x.error}` : ""}`)}"></div></div>
        <span class="dur">${fmtMs(x.duration_ms)}</span></div>`;
    })
    .join("");
  return `<div class="panel timeline">${lanes}<div class="axis"><span></span><div><span>0 s</span><span>${fmtMs(span / 2)}</span><span>${fmtMs(span)}</span></div><span></span></div></div>`;
}

function diffHTML(diff) {
  return diff
    .replace(/\n$/, "")
    .split("\n")
    .map((l) => {
      let cls = "";
      if (l.startsWith("@@")) cls = "hunk";
      else if (l.startsWith("+") && !l.startsWith("+++")) cls = "add";
      else if (l.startsWith("-") && !l.startsWith("---")) cls = "del";
      return `<span class="ln ${cls}">${esc(l)}</span>`;
    })
    .join("");
}

function artifactsHTML(arts) {
  if (!arts.length) return `<p class="muted">This build produced no artifacts.</p>`;
  return arts
    .map((a) => {
      const tab = state.artTab[a.id] || "current";
      const changed = a.previous_run
        ? a.changed.length ? `Changed since the previous version: ${a.changed.join(", ")}` : "Identical to the previous version"
        : "First version of this spec";
      const body =
        tab === "diff" && a.diff
          ? `<pre class="code diff">${diffHTML(a.diff)}</pre>`
          : `<pre class="code">${esc(a.yaml)}</pre>`;
      return `<div class="panel" style="margin-bottom:16px">
        <div style="padding:16px 20px 0">
          <div style="display:flex;justify-content:space-between;gap:16px;align-items:baseline">
            <strong>${esc(a.kind)}: ${esc(a.name || "unnamed")}</strong>
            <span class="muted">${esc(changed)}${a.previous_run ? `, compared with <a href="#/run/${esc(a.previous_run)}">that build</a>` : ""}</span>
          </div>
          <div class="tabs" role="tablist" style="margin-top:10px">
            <button role="tab" aria-selected="${tab === "current"}" data-art="${esc(a.id)}" data-tab="current">Full spec</button>
            ${a.diff ? `<button role="tab" aria-selected="${tab === "diff"}" data-art="${esc(a.id)}" data-tab="diff">Changes</button>` : ""}
          </div>
        </div>${body}</div>`;
    })
    .join("");
}

function eventsHTML(events) {
  const kinds = [...new Set(events.map((e) => e.event))].sort();
  const shown = events.filter((e) => !state.evHidden.has(e.event));
  const filters = kinds
    .map((k) => `<label><input type="checkbox" data-ev="${esc(k)}" ${state.evHidden.has(k) ? "" : "checked"}>${esc(k)}</label>`)
    .join("");
  const rows = shown
    .map((e) => {
      const f = { ...(e.fields || {}) };
      delete f.event_id; delete f.traceback;
      return `<tr><td class="num muted">${esc(new Date(e.ts).toLocaleTimeString("en-GB"))}</td>
        <td class="lvl-${esc(e.level)}">${esc(e.event)}</td><td>${esc(e.node || "")}</td>
        <td class="ev-fields">${esc(JSON.stringify(f))}</td></tr>`;
    })
    .join("");
  return `<div class="panel"><div class="filters" aria-label="Show event types">${filters}</div>
    <div class="scroll-x"><table class="table"><thead><tr><th>Time</th><th>Event</th><th>Step</th><th>Details</th></tr></thead>
    <tbody class="no-click">${rows}</tbody></table></div></div>`;
}

async function viewRun(runId, callId) {
  const r = await api(`/api/runs/${encodeURIComponent(runId)}`);
  const data = JSON.stringify(r);
  if (data !== state.lastJSON) {
    state.lastJSON = data;
    const calls = r.calls.length
      ? `<div class="panel"><ul class="calls">${r.calls
          .map(
            (c) => `<li><button class="call" data-call="${esc(c.id)}">
              <span class="node">${esc(c.node || "–")}</span>
              <span class="meta">${status(c.status === "ok" ? "ok" : "error")}&nbsp;&nbsp;${esc(c.model || "")}</span>
              <span class="meta">${fmtMs(c.latency_ms)}, ${fmtTokens(c.input_tokens)} in, ${fmtTokens(c.output_tokens)} out</span>
              <span class="open">Open</span></button></li>`
          )
          .join("")}</ul></div>`
      : `<p class="muted">No model calls in this build.</p>`;
    const decisions = r.decisions.length
      ? `<ul class="decisions">${r.decisions
          .map((d) => {
            const chips = Object.entries(d.details || {})
              .filter(([k]) => k !== "event_id")
              .map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v)}</span>`)
              .join("");
            return `<li class="decision"><div><div class="what">${esc(d.decision)}</div><div class="muted">${esc(d.node || "")}</div></div>
              <div><div class="why">${esc(d.reason || "")}</div>${chips ? `<div class="chips">${chips}</div>` : ""}</div></li>`;
          })
          .join("")}</ul>`
      : `<p class="muted">No decisions were logged in this build.</p>`;

    main.innerHTML = `
      ${crumbs([{ label: "Goals", href: "#/" }, { label: r.title, href: `#/goal/${r.goal_key}` }, { label: `Build ${fmtDate(r.started_at)}` }])}
      <div class="run-head">
        <div><h1 class="goal">Build of ${esc(fmtDate(r.started_at))}</h1><div class="id">${esc(r.run_id)}</div></div>
        <div style="padding-top:12px">${status(r.status)}</div>
      </div>
      ${r.error ? `<div class="alert"><b>This build failed.</b> ${esc(r.error)}</div>` : ""}
      ${stats([
        { label: "Duration", value: fmtMs(r.duration_ms) },
        { label: "Model calls", value: fmtInt(r.llm_calls) },
        { label: "Tokens in", value: fmtTokens(r.total_input_tokens) },
        { label: "Tokens out", value: fmtTokens(r.total_output_tokens) },
        { label: "Model", value: r.model || "–", cls: "text" },
      ])}
      <h2 class="sec">Timeline <small>each step ForgeOne ran, in order</small></h2>
      ${timeline(r.nodes)}
      <h2 class="sec">Model calls <small>open one to read the exact prompt and answer</small></h2>
      ${calls}
      <h2 class="sec">Decisions <small>what ForgeOne chose, and why</small></h2>
      ${decisions}
      <h2 class="sec">What it produced</h2>
      ${artifactsHTML(r.artifacts)}
      <h2 class="sec">Event log <small>${fmtInt(r.events.length)} events</small></h2>
      ${eventsHTML(r.events)}`;
    state.run = r;
  }
  if (callId) openCall(callId);
  else closeSheet(false);
  return true;
}

async function viewErrors() {
  const errs = await api("/api/errors");
  const data = JSON.stringify(errs);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  if (!errs.length) {
    main.innerHTML = `<h1 class="page">Errors</h1><div class="empty"><p>No errors recorded. When a step or a model call fails, it shows up here with its full traceback and a link to the build.</p></div>`;
    return true;
  }
  const byType = {};
  errs.forEach((e) => (byType[e.error_type || "Unknown"] = (byType[e.error_type || "Unknown"] || 0) + 1));
  main.innerHTML = `
    <h1 class="page">Errors</h1>
    <p class="lede">Every failed step and model call, newest first. Open one for the traceback, or jump to the build it happened in.</p>
    <div class="chips" style="margin-bottom:24px">${Object.entries(byType)
      .sort((a, b) => b[1] - a[1])
      .map(([t, c]) => `<span class="chip">${esc(t)}: ${c}</span>`)
      .join("")}</div>
    ${errs
      .map(
        (e) => `<details class="err"><summary>
          <span class="muted num">${esc(fmtDate(e.ts))}</span>
          <span><span class="type">${esc(e.error_type || "Error")}</span> <span class="muted">in ${esc(e.node || e.event)}</span><br>${esc(e.error || "")}</span>
          <a href="#/run/${esc(e.run_id)}">Open build</a></summary>
          ${e.traceback ? `<pre class="code panel">${esc(e.traceback)}</pre>` : ""}</details>`
      )
      .join("")}`;
  return true;
}

/* ---------------- sheet: one model call ---------------- */
let lastFocus = null;
async function openCall(callId) {
  const sheet = $("#sheet"), back = $("#sheet-backdrop"), body = $("#sheet-body");
  if (sheet.hidden) {
    lastFocus = document.activeElement;
    sheet.hidden = false; back.hidden = false;
    sheet.classList.add("entering");
    requestAnimationFrame(() => requestAnimationFrame(() => sheet.classList.remove("entering")));
    body.innerHTML = `<div class="skeleton"></div><div class="skeleton" style="width:40%"></div>`;
    $("#sheet-close").focus();
  }
  try {
    const c = await api(`/api/calls/${encodeURIComponent(callId)}`);
    $("#sheet-title").textContent = `Model call in ${c.node || "unknown step"}`;
    const msgs = (c.prompt?.messages || [])
      .map((m) => `<div class="msg"><div class="role">${esc(m.role)}</div><pre class="code">${esc(typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2))}</pre></div>`)
      .join("");
    const resp = c.response || {};
    const content = typeof resp.content === "string" ? resp.content : resp.content ? JSON.stringify(resp.content, null, 2) : "";
    body.innerHTML = `
      ${stats([
        { label: "Result", value: c.status === "ok" ? "Answered" : "Failed", cls: c.status === "ok" ? "ok" : "fail" },
        { label: "Latency", value: fmtMs(c.latency_ms) },
        { label: "Tokens in", value: fmtInt(c.input_tokens) },
        { label: "Tokens out", value: fmtInt(c.output_tokens) },
      ])}
      <p class="muted" style="margin-top:12px">${esc(c.model || "")}${c.stop_reason ? `, stopped because: ${esc(c.stop_reason)}` : ""}</p>
      ${c.error ? `<div class="alert"><b>Failed.</b> ${esc(c.error)}</div>` : ""}
      <h3>Prompt</h3>${msgs || '<p class="muted">Prompt not recorded.</p>'}
      <h3>Answer</h3>${content ? `<div class="msg"><pre class="code">${esc(content)}</pre></div>` : '<p class="muted">No text in the answer.</p>'}
      ${resp.tool_calls?.length ? `<h3>Structured output</h3><div class="msg"><pre class="code">${esc(JSON.stringify(resp.tool_calls, null, 2))}</pre></div>` : ""}`;
  } catch (e) {
    body.innerHTML = `<div class="alert"><b>Could not load this call.</b> ${esc(e.message)}</div>`;
  }
}
function closeSheet(navigate = true) {
  const sheet = $("#sheet");
  if (sheet.hidden) return;
  sheet.hidden = true; $("#sheet-backdrop").hidden = true;
  if (navigate && state.route?.name === "run") location.hash = `#/run/${state.route.id}`;
  lastFocus?.focus?.();
}

/* ---------------- router ---------------- */
function parse() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "goal" && parts[1]) return { name: "goal", id: parts[1] };
  if (parts[0] === "run" && parts[1]) return { name: "run", id: parts[1], call: parts[2] === "call" ? parts[3] : null };
  if (parts[0] === "errors") return { name: "errors" };
  return { name: "goals" };
}
async function render({ quiet = false } = {}) {
  const route = parse();
  const sameView = state.route && state.route.name === route.name && state.route.id === route.id;
  if (!sameView) { state.lastJSON = ""; if (!quiet) main.innerHTML = `<div class="skeleton"></div><div class="skeleton" style="width:45%"></div>`; }
  state.route = route;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const on = (a.dataset.nav === "errors") === (route.name === "errors");
    on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  });
  try {
    if (route.name === "goal") await viewGoal(route.id);
    else if (route.name === "run") await viewRun(route.id, route.call);
    else if (route.name === "errors") await viewErrors();
    else await viewGoals();
    if (!sameView && !quiet) { window.scrollTo(0, 0); main.focus({ preventScroll: true }); }
    $("#live").classList.remove("stale");
    $("#updated").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (e) {
    $("#live").classList.add("stale");
    if (!quiet || !main.children.length)
      main.innerHTML = `<h1 class="page">Can't load this page</h1><div class="alert"><b>${esc(e.message)}</b></div>
        <p class="lede" style="margin-top:20px">Check that the database is running on the server with <code>docker compose ps</code>, then reload.</p>`;
  }
}
async function refreshBadge() {
  try {
    const ov = await api("/api/overview");
    const b = $("#err-badge");
    b.hidden = !n(ov.errors_24h);
    b.textContent = fmtInt(ov.errors_24h);
  } catch { /* shown by the live indicator */ }
}

/* ---------------- events ---------------- */
document.addEventListener("click", (e) => {
  const tick = e.target.closest(".tick");
  if (tick) { location.hash = `#/run/${tick.dataset.run}`; return; }
  const call = e.target.closest("[data-call]");
  if (call && state.route?.name === "run") { location.hash = `#/run/${state.route.id}/call/${call.dataset.call}`; return; }
  const tab = e.target.closest("[data-art]");
  if (tab) { state.artTab[tab.dataset.art] = tab.dataset.tab; state.lastJSON = ""; render({ quiet: true }); return; }
  const row = e.target.closest("tr[data-href]");
  if (row && !e.target.closest("a")) { location.hash = row.dataset.href; return; }
});
document.addEventListener("change", (e) => {
  const cb = e.target.closest("[data-ev]");
  if (!cb) return;
  cb.checked ? state.evHidden.delete(cb.dataset.ev) : state.evHidden.add(cb.dataset.ev);
  state.lastJSON = ""; render({ quiet: true });
});
$("#sheet-close").addEventListener("click", () => closeSheet());
$("#sheet-backdrop").addEventListener("click", () => closeSheet());
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

const tip = $("#tip");
function showTip(el) {
  if (!el?.dataset?.tip) return;
  tip.textContent = el.dataset.tip; tip.hidden = false;
  const r = el.getBoundingClientRect();
  const x = Math.min(Math.max(8, r.left + r.width / 2 - tip.offsetWidth / 2), innerWidth - tip.offsetWidth - 8);
  const y = r.top - tip.offsetHeight - 8;
  tip.style.left = `${x}px`; tip.style.top = `${y < 8 ? r.bottom + 8 : y}px`;
}
document.addEventListener("mouseover", (e) => showTip(e.target.closest("[data-tip]")));
document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]")) tip.hidden = true; });
document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-tip]"); t ? showTip(t) : (tip.hidden = true); });

window.addEventListener("hashchange", () => render());
render();
refreshBadge();
setInterval(() => {
  if (document.hidden || !$("#sheet").hidden) return;
  render({ quiet: true });
  refreshBadge();
}, REFRESH_MS);

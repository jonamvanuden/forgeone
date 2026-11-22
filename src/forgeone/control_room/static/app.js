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
function fmtUSD(v) {
  if (v == null) return "–";
  v = n(v);
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  if (v < 10) return `$${v.toFixed(2)}`;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}
const toDate = (s) => (s ? new Date(s) : null);
function fmtDate(s) {
  const d = toDate(s);
  return d ? d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
}
const fmtDay = (s) => new Date(`${String(s).slice(0, 10)}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
function ago(s) {
  const d = toDate(s);
  if (!d) return "never";
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}
const STATUS_TEXT = {
  ok: "Passed", error: "Failed", running: "Running", passed: "Passed", failed: "Failed", done: "Done",
  pending: "Not started", planned: "Planned", waiting: "Needs you", paused: "Paused", blocked: "Blocked",
  skipped: "Skipped", superseded: "Replaced", cancelled: "Cancelled",
};
const status = (s) => `<span class="status ${esc(s)}">${esc(STATUS_TEXT[s] || s || "Unknown")}</span>`;
/** short name of a goal for compact places: "Build VoyagerOne: an agent..." -> "Build VoyagerOne" */
const shortTitle = (t) => (String(t || "").split(/[:.]/)[0] || t).trim();
const pct = (a, b) => (n(b) ? `${Math.round((n(a) / n(b)) * 100)}%` : "–");

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function setCrumbs(items) {
  $("#crumbs").innerHTML = items
    .map((it, i) => (it.href ? `<a href="${esc(it.href)}">${esc(it.label)}</a>` : `<span aria-current="page">${esc(it.label)}</span>`) + (i < items.length - 1 ? '<span class="sep">/</span>' : ""))
    .join("");
}
const panel = (title, hint, body, cls = "") =>
  `<section class="panel ${cls}"><div class="panel-head"><h2>${esc(title)}</h2>${hint ? `<span class="hint">${hint}</span>` : ""}</div>${body}</section>`;

/** delta chip; better = "up" | "down" says which direction is good */
function delta(v, better = "up", unit = "%") {
  if (v == null) return `<span class="delta flat">new</span>`;
  if (v === 0) return `<span class="delta flat">±0${unit}</span>`;
  const good = (v > 0) === (better === "up");
  return `<span class="delta ${good ? "good" : "bad"}">${v > 0 ? "+" : ""}${esc(v)}${unit}</span>`;
}

const ICON = {
  builds: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  rate: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
  spend: '<svg viewBox="0 0 24 24"><path d="M12 2v20M17 6H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>',
  time: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  live: '<svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  running: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  spec: '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4zM8 9h8M8 13h8M8 17h5"/></svg>',
};

/* ---------------- build strip (signature element) ---------------- */
function strip(runs, { big = false } = {}) {
  if (!runs?.length) return `<div class="strip${big ? " big" : ""}"></div>`;
  const max = Math.max(...runs.map((r) => n(r.duration_ms)), 1);
  const ticks = runs
    .map((r) => {
      const h = Math.max(12, Math.round((n(r.duration_ms) / max) * 100));
      const label = `${STATUS_TEXT[r.status] || r.status}, ${fmtDate(r.started_at)}, ${fmtMs(r.duration_ms)}`;
      return `<button class="tick ${esc(r.status)}" style="height:${h}%" data-run="${esc(r.run_id)}" data-tip="${esc(label)}" aria-label="Build: ${esc(label)}"></button>`;
    })
    .join("");
  return `<div class="strip${big ? " big" : ""}" role="group" aria-label="Builds, oldest to newest">${ticks}</div>`;
}

/* ---------------- charts (hand-drawn SVG) ---------------- */
function buildsChart(days) {
  const W = 720, H = 220, L = 30, B = 26, T = 10;
  const max = Math.max(1, ...days.map((d) => n(d.passed) + n(d.failed) + n(d.running)));
  const step = Math.max(1, Math.ceil(max / 4));
  const top = step * 4;
  const bw = (W - L) / days.length;
  const y = (v) => T + (H - T - B) * (1 - v / top);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = step * i;
    g += `<line class="grid-line" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  }
  const bars = days
    .map((d, i) => {
      const x = L + i * bw + bw * 0.22, w = bw * 0.56;
      let acc = 0, segs = "";
      [["passed", "bar-ok"], ["failed", "bar-fail"], ["running", "bar-run"]].forEach(([k, cls]) => {
        const v = n(d[k]);
        if (!v) return;
        const y0 = y(acc), y1 = y(acc + v);
        segs += `<rect class="${cls}" x="${x.toFixed(1)}" y="${y1.toFixed(1)}" width="${w.toFixed(1)}" height="${(y0 - y1).toFixed(1)}" rx="3"/>`;
        acc += v;
      });
      const tip = `${fmtDay(d.day)}: ${n(d.passed)} passed, ${n(d.failed)} failed${n(d.running) ? `, ${n(d.running)} running` : ""}`;
      const label = (days.length - 1 - i) % 2 === 0 || days.length <= 8 ? `<text x="${(x + w / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(fmtDay(d.day))}</text>` : "";
      return `<g class="day" data-tip="${esc(tip)}"><rect class="bar-hit" x="${(L + i * bw).toFixed(1)}" y="${T}" width="${bw.toFixed(1)}" height="${H - T - B}"/>${segs}</g>${label}`;
    })
    .join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Builds per day, last 14 days"><g class="axis">${g}</g><g class="axis">${bars}</g></svg></div>
    <div class="legend"><span><i style="background:var(--violet)"></i>Passed</span><span><i style="background:var(--fail)"></i>Failed</span><span><i style="background:var(--run)"></i>Running</span></div>`;
}

function spendChart(days) {
  const W = 360, H = 150, L = 6, R = 6, T = 12, B = 22;
  const max = Math.max(...days.map((d) => n(d.cost)), 0.0001);
  const x = (i) => L + (i * (W - L - R)) / Math.max(days.length - 1, 1);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const pts = days.map((d, i) => [x(i), y(n(d.cost))]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${x(days.length - 1).toFixed(1)},${H - B} L${x(0).toFixed(1)},${H - B} Z`;
  const dots = pts.map((p, i) => `<circle class="dot" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3.2" data-tip="${esc(`${fmtDay(days[i].day)}: ${fmtUSD(days[i].cost)}`)}"/>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Spend per day, last 14 days">
    <defs><linearGradient id="spendFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#6c47ff" stop-opacity=".28"/><stop offset="1" stop-color="#6c47ff" stop-opacity="0"/></linearGradient></defs>
    <path class="area" d="${area}"/><path class="line" d="${line}"/>${dots}
    <g class="axis"><text x="${L}" y="${H - 4}">${esc(fmtDay(days[0].day))}</text><text x="${W - R}" y="${H - 4}" text-anchor="end">Today</text></g></svg></div>`;
}

/* ---------------- views ---------------- */
function goalsList(goals) {
  if (!goals.length) return `<div class="empty">No builds yet. Start one on the server with <code>forgeone build --agent voyagerone</code> and it shows up here within seconds.</div>`;
  return `<ul class="goals">${goals
    .map(
      (g) => `<li class="goal-row">
      <div>
        <h3><a href="#/build-goal/${esc(g.key)}">${esc(g.title)}</a></h3>
        <ul class="facts">
          <li><b>${fmtInt(g.builds)}</b> builds</li>
          <li class="ok"><b>${pct(g.passed, g.builds)}</b> passed</li>
          ${n(g.failed) ? `<li class="fail"><b>${fmtInt(g.failed)}</b> failed</li>` : ""}
          ${n(g.running) ? `<li><b>${fmtInt(g.running)}</b> running</li>` : ""}
          <li>median <b>${fmtMs(g.median_ms)}</b></li>
          <li>spent <b>${fmtUSD(g.cost)}</b></li>
          <li>last <b>${esc(ago(g.last_build))}</b></li>
        </ul>
      </div>
      <div>${strip(g.strip)}</div>
    </li>`
    )
    .join("")}</ul>`;
}

function feedHTML(feed) {
  if (!feed.length) return `<div class="empty">Nothing has happened yet.</div>`;
  return `<ul class="feed">${feed
    .map((f) => {
      if (f.kind === "spec.changed")
        return `<li><span class="ico spec">${ICON.spec}</span><div><div class="what">New <a href="#/run/${esc(f.run_id)}">${esc(f.name)} spec version</a>: changed ${esc((f.changed || []).join(", "))}</div><div class="when">${esc(ago(f.ts))}</div></div></li>`;
      const st = f.kind.replace("build.", "");
      const verb = { ok: "Build passed", error: "Build failed", running: "Build running" }[st] || "Build";
      return `<li><span class="ico ${esc(st)}">${ICON[st] || ICON.running}</span><div>
        <div class="what">${esc(verb)}: <a href="#/run/${esc(f.run_id)}" title="${esc(f.title)}">${esc(shortTitle(f.title))}</a></div>
        <div class="when">${esc(ago(f.ts))}${f.duration_ms != null ? `, took ${fmtMs(f.duration_ms)}` : ""}${f.error ? `, ${esc(f.error.slice(0, 60))}` : ""}</div></div></li>`;
    })
    .join("")}</ul>`;
}

function modelsHTML(models) {
  if (!models.length) return `<div class="empty">No model calls in the last 30 days.</div>`;
  const max = Math.max(...models.map((m) => n(m.tokens_in) + n(m.tokens_out)), 1);
  return `<ul class="meters">${models
    .map((m) => {
      const tok = n(m.tokens_in) + n(m.tokens_out);
      return `<li><div class="meter-top"><b>${esc(m.model)}</b><span>${m.priced ? fmtUSD(m.cost) : "price unknown"}</span></div>
        <div class="meter"><i style="width:${Math.max(2, (tok / max) * 100).toFixed(1)}%"></i></div>
        <div class="meter-sub"><span>${fmtInt(m.calls)} calls</span><span>${fmtTokens(m.tokens_in)} in</span><span>${fmtTokens(m.tokens_out)} out</span><span>avg ${fmtMs(m.avg_latency_ms)}</span>${n(m.errors) ? `<span style="color:var(--fail)">${fmtInt(m.errors)} failed</span>` : ""}</div></li>`;
    })
    .join("")}</ul>`;
}

function stepsHTML(steps) {
  if (!steps.length) return `<div class="empty">No steps recorded in the last 30 days.</div>`;
  const max = Math.max(...steps.map((s) => n(s.avg_ms)), 1);
  return `<ul class="meters">${steps
    .map(
      (s) => `<li><div class="meter-top"><b>${esc(s.node)}</b><span>avg ${fmtMs(s.avg_ms)}</span></div>
      <div class="meter ${n(s.failed) ? "fail" : ""}"><i style="width:${Math.max(2, (n(s.avg_ms) / max) * 100).toFixed(1)}%"></i></div>
      <div class="meter-sub"><span>${fmtInt(s.runs)} runs</span><span>p95 ${fmtMs(s.p95_ms)}</span><span>${n(s.failed) ? `<b style="color:var(--fail)">${fmtInt(s.failed)} failed (${pct(s.failed, s.runs)})</b>` : "no failures"}</span></div></li>`
    )
    .join("")}</ul>`;
}

async function viewHome() {
  const h = await api("/api/home");
  const data = JSON.stringify(h);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Builds" }]);
  const k = h.kpis;
  const nGoals = h.goals.length;
  const spend14 = h.days.reduce((a, d) => a + n(d.cost), 0);
  const kpi = (icon, label, value, foot, cls = "") =>
    `<div class="panel kpi ${cls}"><div class="label">${ICON[icon]}${esc(label)}</div><div class="value">${value}</div><div class="foot">${foot}</div></div>`;

  main.innerHTML = `
    <div class="page-head"><div>
      <h1>Builds</h1>
      <p>The builder has built agents for ${fmtInt(nGoals)} ${nGoals === 1 ? "goal file" : "goal files"}. ${k.last_build ? `The last build started ${esc(ago(k.last_build))}.` : "No builds yet."}${n(k.running) ? ` ${fmtInt(k.running)} running right now.` : ""}</p>
    </div></div>

    <div class="grid g-kpi">
      ${kpi("builds", "Builds this week", fmtInt(k.builds_7d), `${delta(k.builds_delta)} vs last week`)}
      ${kpi("rate", "Success rate", k.success_rate == null ? "–" : `${Math.round(k.success_rate)}<small>%</small>`, `${delta(k.success_delta, "up", " pts")} vs last week`)}
      ${kpi("spend", "Spend this week", esc(fmtUSD(k.spend_7d)), `${delta(k.spend_delta, "down")} vs last week`)}
      ${kpi("time", "Median build time", esc(fmtMs(k.median_ms)), `${delta(k.median_delta, "down")} vs last week`)}
      ${kpi("live", "Running now", fmtInt(k.running), n(k.running) ? "builds in progress" : "nothing in progress", n(k.running) ? "live" : "")}
    </div>

    <div class="grid g-2-1">
      ${panel("Builds per day", "last 14 days", `<div class="panel-body">${buildsChart(h.days)}</div>`)}
      ${panel("Spend", "last 14 days, estimated", `<div class="panel-body"><div class="big-number">${esc(fmtUSD(spend14))}</div><div class="muted" style="font-size:12.5px;margin-bottom:8px">from logged token usage and list prices; runs before logging started are not included</div>${n(h.unpriced?.calls) ? `<div class="alert" style="margin-bottom:10px;font-size:12.5px"><b>${fmtInt(h.unpriced.calls)} calls have no known price</b> and are not in this total: ${esc(h.unpriced.models.join(", "))}. Add the model to PRICES in forgeone/llm/pricing.py.</div>` : ""}${spendChart(h.days)}</div>`)}
    </div>

    <div class="grid g-2-1">
      ${panel("Build goals", `<a href="#/build-goals">See all</a>`, `<div class="panel-body flush">${goalsList(h.goals.slice(0, 6))}</div>`)}
      ${panel("Recent activity", "", `<div class="panel-body flush">${feedHTML(h.feed.slice(0, 7))}</div>`)}
    </div>

    <div class="grid g-1-1">
      ${panel("Model usage", "last 30 days, by cost", `<div class="panel-body flush">${modelsHTML(h.models)}</div>`)}
      ${panel("Step performance", "last 30 days, slowest first", `<div class="panel-body flush">${stepsHTML(h.steps)}</div>`)}
    </div>`;
  return true;
}

async function viewGoals() {
  const goals = await api("/api/goals");
  const data = JSON.stringify(goals);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Builds", href: "#/builds" }, { label: "Build goals" }]);
  main.innerHTML = `
    <div class="page-head"><div><h1>Build goals</h1><p>Every goal.md the builder has turned into an agent. Open a goal for its builds and spec versions, or click a bar to jump into a build.</p></div></div>
    ${panel(`${goals.length} ${goals.length === 1 ? "goal" : "goals"}`, "most recent first", `<div class="panel-body flush">${goalsList(goals)}</div>`)}`;
  return true;
}

async function viewGoal(key) {
  const g = await api(`/api/goals/${encodeURIComponent(key)}`);
  const data = JSON.stringify(g);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Builds", href: "#/builds" }, { label: "Build goals", href: "#/build-goals" }, { label: g.title }]);

  const versions = g.specs.length
    ? `<ul class="versions">${g.specs
        .map(
          (s) => `<li class="version"><span class="v">v${esc(s.version)}</span>
          <div><a href="#/run/${esc(s.run_id)}">First built ${esc(fmtDate(s.ts))}</a>
            <div class="muted" style="font-size:13px">${s.version === 1 ? "The first design" : `Changed ${esc(s.changed.join(", "))}`}${s.builds > 1 ? `, then rebuilt ${esc(s.builds - 1)} more ${s.builds === 2 ? "time" : "times"} unchanged` : ""}</div></div>
          <span class="muted num" style="font-size:13px">${s.max_steps != null ? `${esc(s.max_steps)} steps` : ""}${s.tools ? `, ${esc(s.tools.length)} tools` : ""}</span></li>`
        )
        .join("")}</ul>`
    : `<div class="empty">No specs recorded for this goal yet.</div>`;

  const showIt = g.runs.some((r) => r.iterations != null);
  const rows = g.runs
    .map(
      (r) => `<tr data-href="#/run/${esc(r.run_id)}">
      <td><a class="row-link" href="#/run/${esc(r.run_id)}">${esc(fmtDate(r.started_at))}</a></td>
      <td>${status(r.status)}</td>
      <td class="r">${fmtMs(r.duration_ms)}</td>
      ${showIt ? `<td class="r">${r.iterations != null ? esc(r.iterations) : "–"}</td>` : ""}
      <td class="r">${fmtInt(r.llm_calls)}</td>
      <td class="r">${fmtTokens(n(r.total_input_tokens) + n(r.total_output_tokens))}</td>
      <td class="r">${fmtUSD(r.cost)}</td>
      <td class="muted">${esc((r.error || "").slice(0, 80))}</td></tr>`
    )
    .join("");

  const kpi = (label, value, cls = "") => `<div class="panel kpi ${cls}"><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
  main.innerHTML = `
    <section class="panel hero">
      <h1 class="goal">${esc(g.title)}</h1>
      <div class="sub">First build ${esc(fmtDate(g.first_build))}, last build ${esc(ago(g.last_build))}</div>
      <details><summary>Show the full goal</summary><pre>${esc(g.goal || "No goal text recorded.")}</pre></details>
    </section>
    <div class="grid g-kpi">
      ${kpi("Builds", fmtInt(g.builds))}
      ${kpi("Success rate", `${pct(g.passed, g.builds)}`)}
      ${kpi("Median build", esc(fmtMs(g.median_ms)))}
      ${kpi("Spent", esc(fmtUSD(g.cost)))}
      ${kpi("Tokens", esc(fmtTokens(g.tokens)))}
    </div>
    ${panel("Build history", "bar height is build time; click a bar to open the build",
      `<div class="panel-body">${strip(g.strip, { big: true })}<div class="strip-legend"><span>${esc(fmtDate(g.strip[0]?.started_at))}</span><span>${esc(fmtDate(g.strip.at(-1)?.started_at))}</span></div></div>`)}
    <div class="grid g-1-1">
      ${panel("Spec versions", "how the agent's design changed", `<div class="panel-body flush">${versions}</div>`)}
      ${panel("Outcome", "", `<div class="panel-body">${buildsChart(daysFromRuns(g.runs))}</div>`)}
    </div>
    ${panel(`Builds (${fmtInt(g.runs.length)})`, "", `<div class="scroll-x"><table class="table">
      <thead><tr><th>Started</th><th>Result</th><th class="r">Duration</th>${showIt ? '<th class="r">Iterations</th>' : ""}<th class="r">Model calls</th><th class="r">Tokens</th><th class="r">Cost</th><th>Error</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`)}`;
  return true;
}

function daysFromRuns(runs) {
  const map = new Map();
  const today = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    map.set(d.toISOString().slice(0, 10), { day: d.toISOString().slice(0, 10), passed: 0, failed: 0, running: 0 });
  }
  runs.forEach((r) => {
    const k = String(r.started_at).slice(0, 10);
    if (!map.has(k)) return;
    const d = map.get(k);
    if (r.status === "ok") d.passed++; else if (r.status === "error") d.failed++; else d.running++;
  });
  return [...map.values()];
}

function timeline(nodes) {
  if (!nodes.length) return `<div class="empty">No steps recorded for this run.</div>`;
  const t0 = Math.min(...nodes.map((x) => toDate(x.started).getTime()));
  const t1 = Math.max(...nodes.map((x) => toDate(x.ended).getTime()));
  const span = Math.max(t1 - t0, 1);
  const lanes = nodes
    .map((x, i) => {
      const left = ((toDate(x.started).getTime() - t0) / span) * 100;
      const width = Math.max((n(x.duration_ms) / span) * 100, 0.4);
      const name = `${x.node}${x.iteration != null ? `, round ${x.iteration}` : ""}`;
      return `<div class="lane"><span class="label" title="${esc(name)}">${esc(i + 1)}. ${esc(name)}</span>
        <div class="track"><div class="bar ${x.event === "node.error" ? "error" : ""}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%" data-tip="${esc(`${name}: ${fmtMs(x.duration_ms)}${x.error ? `, ${x.error}` : ""}`)}"></div></div>
        <span class="dur">${fmtMs(x.duration_ms)}</span></div>`;
    })
    .join("");
  return `<div class="timeline">${lanes}<div class="axis-row"><span></span><div><span>0 s</span><span>${fmtMs(span / 2)}</span><span>${fmtMs(span)}</span></div><span></span></div></div>`;
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
  if (!arts.length) return panel("What it produced", "", `<div class="empty">This run produced no artifacts.</div>`);
  return arts
    .map((a) => {
      const tab = state.artTab[a.id] || "current";
      const changed = a.previous_run
        ? a.changed.length ? `Changed since the previous version: ${esc(a.changed.join(", "))}` : "Identical to the previous version"
        : "First version of this spec";
      if (a.text != null) return panel(`${a.kind}`, "what the agent delivered", `<pre class="code" style="margin-top:8px">${esc(a.text)}</pre>`);
      const body = tab === "diff" && a.diff ? `<pre class="code diff">${diffHTML(a.diff)}</pre>` : `<pre class="code">${esc(a.yaml)}</pre>`;
      return panel(`${a.kind}: ${a.name || "unnamed"}`, `${changed}${a.previous_run ? `, <a href="#/run/${esc(a.previous_run)}">open the previous build</a>` : ""}`,
        `<div class="tabs" role="tablist" style="margin-top:8px">
          <button role="tab" aria-selected="${tab === "current"}" data-art="${esc(a.id)}" data-tab="current">Full spec</button>
          ${a.diff ? `<button role="tab" aria-selected="${tab === "diff"}" data-art="${esc(a.id)}" data-tab="diff">Changes</button>` : ""}
        </div>${body}`);
    })
    .join("");
}

function eventsHTML(events) {
  const kinds = [...new Set(events.map((e) => e.event))].sort();
  const shown = events.filter((e) => !state.evHidden.has(e.event));
  const filters = kinds.map((k) => `<label><input type="checkbox" data-ev="${esc(k)}" ${state.evHidden.has(k) ? "" : "checked"}>${esc(k)}</label>`).join("");
  const rows = shown
    .map((e) => {
      const f = { ...(e.fields || {}) };
      delete f.event_id; delete f.traceback;
      return `<tr><td class="num muted">${esc(new Date(e.ts).toLocaleTimeString("en-GB"))}</td><td class="lvl-${esc(e.level)}">${esc(e.event)}</td><td>${esc(e.node || "")}</td><td class="ev-fields">${esc(JSON.stringify(f))}</td></tr>`;
    })
    .join("");
  return panel(`Event log (${fmtInt(events.length)})`, "", `<div class="filters" aria-label="Show event types">${filters}</div>
    <div class="scroll-x"><table class="table"><thead><tr><th>Time</th><th>Event</th><th>Step</th><th>Details</th></tr></thead><tbody>${rows}</tbody></table></div>`);
}

async function viewRun(runId, callId) {
  const r = await api(`/api/runs/${encodeURIComponent(runId)}`);
  const data = JSON.stringify(r);
  if (data !== state.lastJSON) {
    state.lastJSON = data;
    if (r.kind === "agent") {
      setCrumbs([{ label: "Quality", href: "#/quality" }, { label: (r.title || "").slice(0, 60) }]);
      document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === "quality" ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    } else if (r.kind === "work") {
      setCrumbs([{ label: "Goals", href: "#/" }, { label: r.title, href: `#/g/${r.goal_id}` }, { label: `${r.name === "goal.plan" ? "Planning" : "Work session"} ${fmtDate(r.started_at)}` }]);
      document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === "work" ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    } else setCrumbs([{ label: "Builds", href: "#/builds" }, { label: r.title, href: `#/build-goal/${r.goal_key}` }, { label: `Build ${fmtDate(r.started_at)}` }]);
    const calls = r.calls.length
      ? `<ul class="calls">${r.calls
          .map(
            (c) => `<li><button class="call" data-call="${esc(c.id)}">
            <span class="node">${esc(c.node || "–")}</span>
            <span class="meta">${status(c.status === "ok" ? "ok" : "error")}<br>${fmtMs(c.latency_ms)}, ${fmtTokens(n(c.input_tokens) + n(c.output_tokens))} tokens, ${fmtUSD(c.cost)}</span>
            <span class="open">Open</span></button></li>`
          )
          .join("")}</ul>`
      : `<div class="empty">No model calls logged for this run.</div>`;
    const decisions = r.decisions.length
      ? `<ul class="decisions">${r.decisions
          .map((d) => {
            const chips = Object.entries(d.details || {})
              .filter(([key]) => key !== "event_id")
              .map(([key, v]) => `<span class="chip">${esc(key)}: ${esc(Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v)}</span>`)
              .join("");
            return `<li class="decision"><div><div class="what">${esc(d.decision)}</div><div class="muted" style="font-size:12.5px">${esc(d.node || "")}</div></div>
              <div><div>${esc(d.reason || "")}</div>${chips ? `<div class="chips">${chips}</div>` : ""}</div></li>`;
          })
          .join("")}</ul>`
      : `<div class="empty">No decisions were logged in this run.</div>`;

    const kpi = (label, value) => `<div class="panel kpi"><div class="label">${esc(label)}</div><div class="value" style="font-size:22px">${value}</div></div>`;
    const isAgent = r.kind === "agent";
    const labels = (r.labels || []).map((l) => `${labelChip(l.label, l.source)}${l.note ? ` <span class="sub" style="display:inline">${esc(l.note)}</span>` : ""}`).join("<br>");
    main.innerHTML = `
      <section class="panel hero">
        <div class="hero-top"><div><h1 class="goal">${isAgent ? esc(r.title) : r.kind === "work" ? `${r.name === "goal.plan" ? "Planning" : "Work session"} of ${esc(fmtDate(r.started_at))}` : `Build of ${esc(fmtDate(r.started_at))}`}</h1>
          <div class="sub">${isAgent ? `${esc(r.agent || "agent")} run, variant ${esc(r.variant || "default")}${r.case_id && r.case_id !== "adhoc" ? `, eval case ${esc(r.case_id)}` : ""}, ${esc(fmtDate(r.started_at))}` : esc(r.title)}</div>
          <div class="mono">${esc(r.run_id)}</div>${labels ? `<div style="margin-top:10px">${labels}</div>` : ""}
          ${r.kind === "build" && r.goal_id ? `<div class="sub"><a href="#/g/${esc(r.goal_id)}">Open this build as a goal: tasks, checks and every attempt</a></div>` : ""}</div>
          <div>${status(r.status)}</div></div>
      </section>
      ${r.error ? `<div class="alert" style="margin-top:20px"><b>This ${r.kind === "build" ? "build" : "run"} failed.</b> ${esc(r.error)}</div>` : ""}
      <div class="grid g-kpi">
        ${kpi("Duration", esc(fmtMs(r.duration_ms)))}
        ${kpi("Model calls", fmtInt(r.llm_calls))}
        ${kpi("Tokens in / out", `${esc(fmtTokens(r.total_input_tokens))} / ${esc(fmtTokens(r.total_output_tokens))}`)}
        ${kpi("Cost", esc(fmtUSD(r.cost)))}
        ${kpi("Model", `<span class="text-value" title="${esc(r.model || "")}">${esc(r.model || "–")}</span>`)}
      </div>
      ${r.kind === "work" ? panel(`Actions in this session (${r.actions.length})`, "click one to open its task", workActionsHTML(r.actions))
        : isAgent ? panel(`Tool calls (${r.tool_calls.length})`, "what the agent did, in order", `<div class="panel-body flush">${toolCallsHTML(r.tool_calls)}</div>`)
                : panel("Timeline", "each step ForgeOne ran, in order", timeline(r.nodes))}
      <div class="grid g-1-1">
        ${panel("Model calls", "open one to read the exact prompt and answer", `<div class="panel-body flush">${calls}</div>`)}
        ${isAgent ? panel("How it ended", "stop reason from the runtime", `<div class="panel-body"><b>${esc({ finished: "Finished: the agent gave its answer", step_limit: "Stopped: step budget used up", cost_limit: "Stopped: cost budget used up", error: "Stopped: error" }[r.stop_reason] || r.stop_reason || "unknown")}</b></div>`)
                  : panel("Decisions", "what ForgeOne chose, and why", `<div class="panel-body flush">${decisions}</div>`)}
      </div>
      ${artifactsHTML(r.artifacts)}
      ${eventsHTML(r.events)}`;
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
  setCrumbs([{ label: "Home", href: "#/" }, { label: "Errors" }]);
  if (!errs.length) {
    main.innerHTML = `<div class="page-head"><div><h1>Errors</h1></div></div>${panel("All clear", "", `<div class="empty">No errors recorded. When a step or a model call fails, it shows up here with its traceback and a link to the build.</div>`)}`;
    return true;
  }
  const byType = {};
  errs.forEach((e) => (byType[e.error_type || "Unknown"] = (byType[e.error_type || "Unknown"] || 0) + 1));
  main.innerHTML = `
    <div class="page-head"><div><h1>Errors</h1><p>Every failure, newest first. Open one for the traceback, or jump to the build it happened in.</p></div></div>
    <div class="chips" style="margin-bottom:20px">${Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([t, c]) => `<span class="chip">${esc(t)}: ${c}</span>`).join("")}</div>
    ${panel(`${errs.length} ${errs.length === 1 ? "failure" : "failures"}`, "", `<div>${errs
      .map(
        (e) => `<details class="err"><summary>
          <span class="muted num">${esc(fmtDate(e.ts))}</span>
          <span><span class="type">${esc(e.error_type || "Error")}</span> <span class="muted">in ${esc(e.node || e.event)}</span><br>${esc(e.error || "")}</span>
          <a href="#/run/${esc(e.run_id)}">Open build</a></summary>
          ${e.traceback ? `<pre class="code">${esc(e.traceback)}</pre>` : ""}</details>`
      )
      .join("")}</div>`)}`;
  return true;
}

/* ---------------- work: goals -> milestones -> tasks -> actions ---------------- */
const STAGE = { execute: ["E", "Execute"], verify: ["V", "Verify"], edge_test: ["T", "Edge test"], approve: ["A", "Approve"], plan: ["P", "Plan"] };
const STAGE_STATE = { passed: "passed", failed: "failed", pending: "waiting for you", running: "running", none: "not reached" };
const KIND_TEXT = { plan: "Plan", execute: "Execute", verify: "Verify", edge_test: "Edge test", approve: "Approve" };
const ms = (m) => `M${m.seq}`;

function cmd(text) {
  return `<span class="cmd"><code>${esc(text)}</code><button type="button" class="copy" data-copy="${esc(text)}">Copy</button></span>`;
}
function stagePills(stages, container) {
  if (container) return `<span class="muted" style="font-size:12.5px">split into subtasks</span>`;
  return `<span class="stages">${stages.map((s) => {
    const [l, name] = STAGE[s.stage];
    const who = s.stage === "approve" && s.actor ? `, by ${s.actor === "human" ? "you" : s.actor}` : "";
    const what = s.gate ? "Go-ahead before it runs" : name;
    return `<span class="stg ${esc(s.state)}${s.gate ? " gate" : ""}" data-tip="${esc(`${what}: ${STAGE_STATE[s.state] || s.state}${who}`)}">${l}</span>`;
  }).join("")}</span>`;
}
function track(milestones, { big = false } = {}) {
  if (!milestones.length) return "";
  return `<div class="track${big ? " big" : ""}" role="img" aria-label="Milestones: ${esc(milestones.map((m) => `${ms(m)} ${STATUS_TEXT[m.status] || m.status}`).join(", "))}">${milestones.map((m) => {
    const p = m.tasks ? Math.round((n(m.done) / n(m.tasks)) * 100) : 0;
    return `<span class="seg ${esc(m.status)}" data-tip="${esc(`${ms(m)} ${m.title}: ${fmtInt(m.done)} of ${fmtInt(m.tasks)} tasks, ${(STATUS_TEXT[m.status] || m.status).toLowerCase()}`)}"><i style="width:${m.status === "done" ? 100 : p}%"></i>${big ? `<b>${ms(m)}</b>` : ""}</span>`;
  }).join("")}</div>`;
}
const diffText = (t) => `${esc(t.difficulty)}${t.actual_difficulty != null ? ` <span class="arrow">→</span> ${esc(Number(t.actual_difficulty).toFixed(1).replace(/\.0$/, ""))}` : ""}`;
const budgetText = (g) => (g.budget_usd != null ? `${fmtUSD(g.cost_usd)} of ${fmtUSD(g.budget_usd)}` : fmtUSD(g.cost_usd));

function nextStep(g) {
  if (g.status === "paused" && /budget/.test(g.note || "")) return `forgeone goal budget ${g.id} 1.00`;
  if (g.status === "paused") return `forgeone goal run ${g.id}`;
  if (g.status === "failed") return `forgeone goal replan ${g.id} --reason "..."`;
  if (g.status === "planned") return `forgeone goal run ${g.id}`;
  return null;
}

function askList(inbox, attention) {
  const items = [
    ...inbox.map((w) => `<li class="ask">
      <div class="ask-what"><span class="ask-kind">${w.kind === "gate" ? `Go-ahead for task ${esc(w.code)}` : w.kind === "task" ? `Approve task ${esc(w.code)}` : `Approve milestone ${esc(w.code)}`}</span>
        <a href="${w.kind === "milestone" ? `#/g/${esc(w.goal_id)}` : `#/t/${esc(w.id)}`}"><b>${esc(w.title)}</b></a>
        <div class="muted">${esc(w.goal)}, waiting ${esc(ago(w.since))}${w.kind === "gate" ? ". Nothing has run yet: it cannot be undone" : w.risk === "high" ? ", high risk" : ""}</div></div>
      <div class="ask-do">${cmd(`forgeone approve ${w.id}`)}${cmd(`forgeone reject ${w.id} --note "..."`)}</div></li>`),
    ...attention.map((a) => `<li class="ask">
      <div class="ask-what"><span class="ask-kind ${a.kind === "failed" ? "bad" : ""}">Goal ${a.kind === "failed" ? "failed" : "paused"}</span>
        <a href="#/g/${esc(a.goal_id)}"><b>${esc(a.goal)}</b></a>
        <div class="muted">${esc(a.note || "")}</div></div>
      <div class="ask-do">${a.kind === "failed" ? cmd(`forgeone goal replan ${a.goal_id} --reason "..."`)
        : /budget/.test(a.note || "") ? cmd(`forgeone goal budget ${a.goal_id} 1.00`) + cmd(`forgeone goal run ${a.goal_id}`)
        : cmd(`forgeone goal run ${a.goal_id}`)}</div></li>`),
  ];
  return items.length ? `<ul class="asks">${items.join("")}</ul>` : `<div class="empty">Nothing waits for you.</div>`;
}

function goalCards(goals) {
  if (!goals.length)
    return `<div class="empty">No goals yet. Give ForgeOne one on the server:<br><br>${cmd('forgeone goal new "What does WHO advise on free sugars?" --agent voyagerone --budget 1')}</div>`;
  return `<ul class="wgoals">${goals.map((g) => {
    const p = g.progress;
    const verb = { failed: "Stopped at", paused: "Paused at", waiting: "Waiting at", planned: "Starts with", cancelled: "Cancelled at" }[g.status] || "Now on";
    const now = g.current
      ? `${verb} ${ms(g.current)} of ${g.milestones.length}: ${esc(g.current.title)}`
      : g.status === "done" ? `Finished ${esc(ago(g.finished_at))}` : "";
    return `<li class="wgoal">
      <div class="wg-head"><h3><a href="#/g/${esc(g.id)}">${esc(g.title)}</a></h3>${status(g.status)}</div>
      <div class="wg-now">${now}${g.note && g.status !== "done" ? `<span class="wg-note">${esc(g.note)}</span>` : ""}</div>
      ${track(g.milestones)}
      <ul class="facts">
        <li><b>${fmtInt(p.done)}</b> of ${fmtInt(p.tasks)} tasks done</li>
        ${n(p.failed) + n(p.blocked) ? `<li class="fail"><b>${fmtInt(n(p.failed) + n(p.blocked))}</b> failed or blocked</li>` : ""}
        ${g.waiting ? `<li class="wait"><b>${fmtInt(g.waiting)}</b> waiting for you</li>` : ""}
        <li>spent <b>${esc(budgetText(g))}</b></li>
        <li>${esc(g.autonomy)}${g.agent ? `, agent <b>${esc(g.agent)}</b>` : ""}</li>
        <li>updated <b>${esc(ago(g.updated_at))}</b></li>
      </ul></li>`;
  }).join("")}</ul>`;
}

const notMigrated = (title) => `<div class="page-head"><div><h1>${esc(title)}</h1></div></div>
  <div class="alert"><b>The database needs a migration.</b> Run this on the server: ${cmd("forgeone db migrate")}</div>`;

async function viewWork() {
  const w = await api("/api/work");
  const data = JSON.stringify(w);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Goals" }]);
  if (!w.migrated) { main.innerHTML = notMigrated("Goals"); return true; }
  const k = w.kpis;
  const kpi = (icon, label, value, foot, cls = "") =>
    `<div class="panel kpi ${cls}"><div class="label">${ICON[icon]}${esc(label)}</div><div class="value">${value}</div><div class="foot">${foot}</div></div>`;
  const active = w.goals.filter((g) => !["done", "cancelled", "failed"].includes(g.status));
  const closed = w.goals.filter((g) => ["done", "cancelled", "failed"].includes(g.status));
  const ask = w.inbox.length + w.attention.length;
  main.innerHTML = `
    <div class="page-head"><div><h1>Goals</h1>
      <p>${active.length ? `${fmtInt(active.length)} ${active.length === 1 ? "goal is" : "goals are"} in progress.` : "No goal in progress."}
      ${ask ? ` <a href="#/inbox">${fmtInt(ask)} ${ask === 1 ? "thing needs" : "things need"} your decision.</a>` : " Nothing waits for you."}</p></div></div>
    <div class="grid g-kpi">
      ${kpi("live", "Goals in progress", fmtInt(k.active), `${fmtInt(w.goals.length)} in total`)}
      ${kpi("time", "Needs you", fmtInt(k.needs_you), k.needs_you ? `<a href="#/inbox">open the inbox</a>` : "all clear", k.needs_you ? "attn" : "")}
      ${kpi("ok", "Tasks done", fmtInt(k.done_7d), "this week")}
      ${kpi("rate", "Right first time", k.first_try_rate == null ? "–" : `${Math.round(k.first_try_rate * 100)}<small>%</small>`, `of ${fmtInt(k.closed_30d)} tasks, last 30 days`)}
      ${kpi("spend", "Spend", esc(fmtUSD(k.spend_7d)), "this week, all goals")}
    </div>
    ${ask ? panel("Needs your decision", `<a href="#/inbox">Open the inbox</a>`, `<div class="panel-body flush">${askList(w.inbox.slice(0, 3), w.attention.slice(0, 2))}</div>`) : ""}
    ${panel("In progress", "click a goal for its milestones and tasks", `<div class="panel-body flush">${goalCards(active)}</div>`)}
    ${closed.length ? panel("Finished, failed or cancelled", "", `<div class="panel-body flush">${goalCards(closed)}</div>`) : ""}`;
  return true;
}

async function viewInbox() {
  const w = await api("/api/work");
  const data = JSON.stringify([w.inbox, w.attention, w.migrated]);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Home", href: "#/" }, { label: "Needs you" }]);
  if (!w.migrated) { main.innerHTML = notMigrated("Needs you"); return true; }
  main.innerHTML = `
    <div class="page-head"><div><h1>Needs you</h1>
      <p>Work that stops until you decide. Approving or rejecting is done on the server; the goal continues the next time it runs.
      A rejection needs a note: it goes to the next attempt as feedback.</p></div></div>
    ${panel(`Approvals (${w.inbox.length})`, "oldest first", `<div class="panel-body flush">${askList(w.inbox, [])}</div>`)}
    ${panel(`Goals that stopped (${w.attention.length})`, "paused on budget or a task limit, or failed", `<div class="panel-body flush">${w.attention.length ? askList([], w.attention) : '<div class="empty">None.</div>'}</div>`)}`;
  return true;
}

function taskTable(tasks) {
  if (!tasks.length) return `<div class="empty">No tasks.</div>`;
  const rows = tasks.map((t) => `<tr data-href="#/t/${esc(t.id)}" class="${t.depth > 1 ? "sub" : ""} st-${esc(t.status)}">
      <td class="tcode">${esc(t.code)}</td>
      <td><a class="row-link" href="#/t/${esc(t.id)}">${esc(t.title)}</a><div class="show-sm">${status(t.status)}</div>
        ${["failed", "blocked"].includes(t.status) && t.failure_detail ? `<div class="why">${esc(t.failure_reason || "")}: ${esc(t.failure_detail.slice(0, 140))}</div>` : ""}</td>
      <td class="hide-sm"><span class="chip">${esc(t.type)}</span></td>
      <td class="r hide-sm" data-tip="difficulty: predicted → actual">${diffText(t)}</td>
      <td class="hide-sm"><span class="risk ${esc(t.risk)}">${esc(t.risk)}</span></td>
      <td>${stagePills(t.stages, t.container)}</td>
      <td class="r hide-sm">${t.container ? "" : `${fmtInt(t.attempts)}/${fmtInt(t.max_attempts)}`}</td>
      <td class="r hide-sm">${t.cost_usd ? esc(fmtUSD(t.cost_usd)) : '<span class="muted">–</span>'}</td>
      <td class="hide-sm">${status(t.status)}</td></tr>`).join("");
  return `<div class="scroll-x"><table class="table tasks">
    <thead><tr><th>#</th><th>Task</th><th class="hide-sm">Type</th><th class="r hide-sm">Difficulty</th><th class="hide-sm">Risk</th><th>Stages</th><th class="r hide-sm">Tries</th><th class="r hide-sm">Cost</th><th class="hide-sm">Status</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function milestoneHTML(m, current) {
  const p = m.progress;
  const open = m.status !== "done" && (current || ["waiting", "failed", "running"].includes(m.status));
  const appr = m.approval;
  return `<li class="ms ${esc(m.status)}${current ? " current" : ""}">
    <div class="ms-dot" aria-hidden="true">${m.status === "done" ? "✓" : esc(ms(m))}</div>
    <details class="ms-body" ${open ? "open" : ""}>
      <summary>
        <div class="ms-title"><span class="ms-code">${esc(ms(m))}</span><b>${esc(m.title)}</b>${status(m.status)}</div>
        <div class="ms-meta">${fmtInt(p.done)} of ${fmtInt(p.tasks)} tasks done, ${esc(fmtUSD(m.cost_usd))}${m.note ? `<span class="why">${esc(m.note)}</span>` : ""}</div>
        <div class="bar"><i style="width:${p.tasks ? Math.round((p.done / p.tasks) * 100) : 0}%"></i></div>
      </summary>
      <div class="ms-inner">
        ${m.description ? `<p class="ms-desc">${esc(m.description)}</p>` : ""}
        <div class="done-when"><span>Done when</span><ul>${m.done_when.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></div>
        ${taskTable(m.tasks)}
        ${appr ? `<div class="ms-appr">Milestone approval: ${status(appr.status === "pending" ? "waiting" : appr.status)} ${appr.actor_type === "human" ? (appr.status === "pending" ? "by you" : `by ${esc(appr.actor)}`) : `by ${esc(appr.actor)}`}${appr.reason ? `, ${esc(appr.reason)}` : ""}${appr.status === "pending" ? `<div style="margin-top:8px">${cmd(`forgeone approve ${m.id}`)}</div>` : ""}</div>` : ""}
      </div>
    </details></li>`;
}

async function viewWorkGoal(id) {
  const d = await api(`/api/work/goals/${encodeURIComponent(id)}`);
  const data = JSON.stringify(d);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  const g = d.goal, p = d.progress, s = d.stats;
  setCrumbs([{ label: "Goals", href: "#/" }, { label: g.title }]);
  const cur = d.milestones.find((m) => m.status !== "done");
  const step = nextStep(g);
  const kpi = (label, value, foot = "") => `<div class="panel kpi"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="foot">${foot}</div></div>`;
  const spendMax = Math.max(...Object.values(d.spend).map(n), 1e-9);
  const spend = Object.entries(d.spend).map(([k, v]) => `<li><span>${esc(KIND_TEXT[k])}</span><span class="hbar"><i style="width:${Math.round((n(v) / spendMax) * 100)}%"></i></span><b>${esc(fmtUSD(v))}</b></li>`).join("");
  const fails = d.failures.length
    ? `<ul class="fails">${d.failures.map((f) => `<li><b>${fmtInt(f.n)}</b><div><span class="chip">${esc(f.reason)}</span><div class="muted">${esc(f.means)}</div></div></li>`).join("")}</ul>`
    : `<div class="empty">Nothing failed so far.</div>`;
  const plans = d.plans.map((pl) => `<li><b>Plan v${esc(pl.version)}</b> <span class="muted">${esc(fmtDate(pl.at))}, ${esc(fmtUSD(pl.cost_usd))}</span>
      ${pl.reason ? `<div><span class="muted">Why replanned:</span> ${esc(pl.reason)}</div>` : ""}<div class="muted">${esc(pl.reasoning || "")}</div></li>`).join("");
  main.innerHTML = `
    <section class="panel hero">
      <div class="hero-top"><div>
        <h1 class="goal">${esc(g.title)}</h1>
        <div class="sub">${esc(g.playbook ? `Fixed plan from playbook ${g.playbook}: no planner call, milestones are done when their tasks pass` : g.autonomy === "supervised" ? "Supervised: you approve each milestone" : "Autonomous: an AI approver signs off milestones and may replan")}${g.agent ? `. Web work by ${esc(g.agent)}` : ""}. Plan v${esc(g.plan_version)}, created ${esc(fmtDate(g.created_at))}.</div>
        ${g.success_criteria.length ? `<div class="hero-criteria"><span>Done when</span><ul>${g.success_criteria.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
        ${g.description ? `<details><summary>Show the full description</summary><pre>${esc(g.description)}</pre></details>` : ""}
        <div class="mono">${esc(g.id)}</div></div>
        <div>${status(g.status)}</div></div>
      ${track(d.milestones.map((m) => ({ ...m, ...m.progress })), { big: true })}
    </section>
    ${g.status === "waiting" ? "" : g.note && g.status !== "done" ? `<div class="notice ${g.status === "failed" ? "bad" : ""}"><b>${esc(STATUS_TEXT[g.status] || g.status)}.</b> ${esc(g.note)}${step ? `<div style="margin-top:8px">${cmd(step)}</div>` : ""}</div>` : (step ? `<div class="notice">Next step: ${cmd(step)}</div>` : "")}
    ${d.waiting.length ? panel("Waiting for you", "", `<div class="panel-body flush">${askList(d.waiting, [])}</div>`) : ""}
    <div class="grid g-kpi">
      ${kpi("Progress", `${fmtInt(p.done)}<small>/${fmtInt(p.tasks)}</small>`, `${pct(p.done, p.tasks)} of tasks done`)}
      ${kpi("Now on", cur ? esc(ms(cur)) : "–", cur ? esc(cur.title) : g.status === "done" ? "all milestones done" : "")}
      ${kpi("Spent", esc(fmtUSD(g.cost_usd)), g.budget_usd != null ? `of ${esc(fmtUSD(g.budget_usd))} budget` : "no budget set")}
      ${kpi("Right first time", s.closed ? `${Math.round((s.first_try / s.closed) * 100)}<small>%</small>` : "–", `${fmtInt(s.first_try)} of ${fmtInt(s.closed)} finished tasks`)}
      ${kpi("Attempts", fmtInt(s.attempts), `${fmtInt(s.actions)} actions in total`)}
    </div>
    ${panel("Milestones", `<span class="legend">Stages: <b>E</b> execute, <b>V</b> verify, <b>T</b> edge test, <b>A</b> approve</span>`,
      `<ol class="roadmap">${d.milestones.map((m) => milestoneHTML(m, cur && m.id === cur.id)).join("")}</ol>`)}
    <div class="grid g-1-1">
      ${panel("Where the money went", "by stage", `<div class="panel-body"><ul class="spend">${spend}</ul>${s.est_cost ? `<div class="muted" style="font-size:12.5px;margin-top:10px">The planner estimated ${esc(fmtUSD(s.est_cost))} for executing all tasks.</div>` : ""}</div>`)}
      ${panel("What went wrong", "failed actions by reason", `<div class="panel-body flush">${fails}</div>`)}
    </div>
    ${panel("Plan history", d.superseded.length ? `${fmtInt(d.superseded.length)} milestones replaced` : "", `<div class="panel-body"><ul class="plans">${plans}</ul>
      ${d.superseded.length ? `<details><summary>Replaced milestones</summary><ul class="plain">${d.superseded.map((m) => `<li>M${esc(m.seq)} ${esc(m.title)} <span class="muted">(plan v${esc(m.plan_version)})</span></li>`).join("")}</ul></details>` : ""}</div>`)}`;
  return true;
}

function criteriaHTML(criteria, verdict) {
  if (!criteria.length) return `<div class="empty">No acceptance criteria.</div>`;
  return `<ul class="criteria">${criteria.map((c, i) => {
    const v = verdict ? verdict.find((x) => x.criterion === c) || verdict[i] : null;
    const st = v ? (v.met ? "met" : "unmet") : "open";
    return `<li class="${st}"><span class="mark" aria-label="${st}">${st === "met" ? "✓" : st === "unmet" ? "✗" : "○"}</span><div>${esc(c)}${v?.note ? `<div class="muted">${esc(v.note)}</div>` : ""}</div></li>`;
  }).join("")}</ul>`;
}

function actionHTML(a) {
  const who = a.actor_type === "human" ? (a.status === "pending" ? "you" : a.actor) : a.actor_type === "tool" ? `automatic ${a.actor}` : a.actor;
  const st = a.status === "pending" ? "waiting" : a.status;
  const text = a.output?.text;
  const ev = a.output?.evidence;
  return `<li class="act ${esc(st)}">
    <div class="act-head"><span class="act-kind">${esc(KIND_TEXT[a.kind] || a.kind)}</span>${status(st)}
      <span class="muted">${esc(who)}${a.confidence != null ? `, confidence ${Math.round(a.confidence * 100)}%` : ""}</span>
      <span class="act-num">${a.cost_usd ? esc(fmtUSD(a.cost_usd)) : ""}${a.duration_ms ? ` ${esc(fmtMs(a.duration_ms))}` : ""}</span></div>
    ${a.reason ? `<div class="act-reason">${esc(a.reason)}</div>` : ""}
    ${a.feedback ? `<div class="act-feedback"><span>Asked to fix</span>${esc(a.feedback)}</div>` : ""}
    ${a.failure_reason ? `<div><span class="chip bad">${esc(a.failure_reason)}</span></div>` : ""}
    ${a.criteria?.length && a.kind !== "verify" ? `<details><summary>${a.kind === "edge_test" ? "Edge cases" : "Checks"} (${a.criteria.length})</summary>${criteriaHTML(a.criteria.map((c) => c.criterion), a.criteria)}</details>` : ""}
    ${a.kind === "verify" && a.criteria?.length ? `<details><summary>Criteria (${a.criteria.filter((c) => c.met).length} of ${a.criteria.length} met)</summary>${criteriaHTML(a.criteria.map((c) => c.criterion), a.criteria)}</details>` : ""}
    ${ev && ev.opened_urls ? `<details><summary>Evidence: ${fmtInt(ev.tool_calls)} tool calls, ${fmtInt(ev.opened_urls.length)} pages opened${ev.tool_errors ? `, ${fmtInt(ev.tool_errors)} tool errors` : ""}</summary><ul class="plain">${ev.opened_urls.map((u) => `<li class="mono-s">${esc(u)}</li>`).join("")}</ul></details>` : ""}
    ${text ? `<details><summary>Output of this attempt (${fmtInt(text.length)} characters)</summary><pre class="code">${esc(text)}</pre></details>` : ""}
    <div class="act-foot">${a.prompt_version ? `<span>prompt ${esc(a.prompt_version)}</span>` : ""}${a.model ? `<span>${esc(a.model)}</span>` : ""}${a.run_id ? `<a href="#/run/${esc(a.run_id)}">model calls of this run</a>` : ""}<span class="mono-s">${esc(a.id)}</span></div>
  </li>`;
}

async function viewTask(id) {
  const d = await api(`/api/work/tasks/${encodeURIComponent(id)}`);
  const data = JSON.stringify(d);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  const t = d.task, g = d.goal, m = d.milestone;
  setCrumbs([{ label: "Goals", href: "#/" }, { label: g.title, href: `#/g/${g.id}` }, { label: `${ms(m)}`, href: `#/g/${g.id}` }, { label: `${t.code} ${t.title}` }]);
  const kpi = (label, value, foot = "") => `<div class="panel kpi"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="foot">${foot}</div></div>`;
  const attempts = {};
  d.actions.forEach((a) => (attempts[a.attempt] = attempts[a.attempt] || []).push(a));
  const timeline = Object.entries(attempts).map(([n_, acts]) =>
    `<li class="attempt"><div class="attempt-head">${acts.every((a) => a.kind === "plan") ? "Planning" : `Attempt ${esc(n_)}`}</div><ul class="acts">${acts.map(actionHTML).join("")}</ul></li>`).join("");
  const links = (rows, emptyText) => rows.length
    ? `<ul class="links">${rows.map((x) => `<li><a href="#/t/${esc(x.id)}"><span class="tcode">${esc(x.code)}</span> ${esc(x.title)}</a>${status(x.status)}</li>`).join("")}</ul>`
    : `<div class="empty">${emptyText}</div>`;
  const container = !t.stages.length;
  main.innerHTML = `
    <section class="panel hero">
      <div class="hero-top"><div>
        <h1 class="goal"><span class="hero-code">${esc(t.code)}</span> ${esc(t.title)}</h1>
        <div class="sub">${esc(t.type)} task, ${esc(t.risk)} risk${t.reversible ? "" : ", cannot be undone"}, in ${esc(ms(m))} ${esc(m.title)}${d.parent ? `, part of <a href="#/t/${esc(d.parent.id)}">${esc(d.parent.code)}</a>` : ""}</div>
        <div class="mono">${esc(t.id)}</div></div>
        <div>${status(t.status)}</div></div>
      ${container ? "" : `<div class="hero-stages">${stagePills(d.stages, false)}<span>${d.stages.map((s) => `${STAGE[s.stage][1]}: ${STAGE_STATE[s.state]}`).join(", ")}</span></div>`}
    </section>
    ${d.waiting ? `<div class="notice"><b>${d.waiting.input?.gate ? "Waiting for your go-ahead" : "Waiting for your approval"}</b> since ${esc(ago(d.waiting.started_at))}. ${d.waiting.input?.gate ? "Nothing has run yet, and what it does cannot be undone. Check what it will do and what it builds on, then:" : "Check the output below, then:"}<div class="cmds">${cmd(`forgeone approve ${t.id}`)}${cmd(`forgeone reject ${t.id} --note "what must change"`)}</div></div>` : ""}
    ${["failed", "blocked"].includes(t.status) ? `<div class="notice bad"><b>${esc(STATUS_TEXT[t.status])}: ${esc(t.failure_reason || "")}.</b> ${esc(t.failure_detail || "")}</div>` : ""}
    <div class="grid g-kpi">
      ${kpi("Attempts", container ? "–" : `${fmtInt(t.attempts)}<small>/${fmtInt(t.max_attempts)}</small>`, container ? "see subtasks" : "allowed by policy")}
      ${kpi("Cost", esc(fmtUSD(t.cost_usd)), t.est_cost_usd != null ? `estimated ${esc(fmtUSD(t.est_cost_usd))}` : "")}
      ${kpi("Working time", esc(fmtMs(t.duration_ms)), t.est_minutes != null ? `estimated ${esc(fmtMs(t.est_minutes * 60000))}` : "")}
      ${kpi("Difficulty", diffText(t), "predicted → actual")}
      ${kpi(t.executor === "step" && !t.model ? "Done by" : "Model", `<span class="text-value">${esc(t.model || (t.executor === "step" ? "code" : "–"))}</span>`,
        esc(t.executor === "agent" ? `agent ${g.agent || ""}` : t.executor === "llm" ? "one model call" : t.executor === "step" ? `step ${t.step}` : "subtasks"))}
    </div>
    <div class="grid g-1-1">
      ${panel("What to do", "", `<div class="panel-body prose-s">
        <p>${esc(t.description)}</p>
        <dl class="kv"><dt>Starts from</dt><dd>${esc(t.input || "–")}</dd><dt>Delivers</dt><dd>${esc(t.expected_output || "–")}</dd>
        <dt>Why this model</dt><dd>${esc(t.routing_reason || "–")}</dd>
        <dt>Budget</dt><dd>${t.budget_usd != null ? `${esc(fmtUSD(t.budget_usd))} per attempt` : "–"}${t.timeout_s ? `, ${esc(fmtMs(t.timeout_s * 1000))} time limit` : ""}</dd></dl></div>`)}
      ${panel("Done when", d.last_verdict ? "result of the last verification" : "not verified yet", `<div class="panel-body">${criteriaHTML(t.acceptance_criteria, d.last_verdict)}</div>`)}
    </div>
    ${d.children.length ? panel(`Subtasks (${d.children.length})`, "this task was split because it was rated difficulty 5", taskTable(d.children)) : ""}
    ${d.depends_on.length || d.dependents.length ? `<div class="grid g-1-1">
      ${panel("Waits for", "", links(d.depends_on, "Nothing: it can start right away."))}
      ${panel("Needed by", "", links(d.dependents, "No other task needs this one."))}</div>` : ""}
    ${t.output ? panel("Output", t.status === "done" ? "the accepted result" : "the latest attempt", `<pre class="code" style="margin:0 20px 20px">${esc(t.output)}</pre>`) : ""}
    ${panel("Activity", "every action, oldest first", d.actions.length ? `<ol class="timeline-work">${timeline}</ol>` : `<div class="empty">Nothing happened yet.</div>`)}`;
  return true;
}

/* ---------------- quality: evals, knowledge, labels ---------------- */
const labelChip = (label, source) =>
  label ? `<span class="status ${label === "good" ? "ok" : "error"}">${label === "good" ? "Good" : "Bad"}${source === "checks" ? ", by checks" : ""}</span>` : `<span class="muted">unlabelled</span>`;

function knowledgeMatrix(rows) {
  const variants = [...new Set(rows.map((r) => r.variant_name))].sort();
  const cats = [...new Set(rows.map((r) => r.category))].sort();
  const cell = new Map(rows.map((r) => [`${r.category}|${r.variant_name}`, r]));
  const head = `<tr><th>Task type</th>${variants.map((v) => `<th>${esc(v)}</th>`).join("")}<th>Best choice</th></tr>`;
  const body = cats.map((c) => {
    let best = null;
    variants.forEach((v) => {
      const r = cell.get(`${c}|${v}`);
      if (!r || n(r.hard_failures)) return;
      const key = [n(r.pass_k), -n(r.mean_cost_usd)];
      if (!best || key[0] > best.key[0] || (key[0] === best.key[0] && key[1] > best.key[1])) best = { key, v };
    });
    const tds = variants.map((v) => {
      const r = cell.get(`${c}|${v}`);
      if (!r) return `<td class="muted">not tested</td>`;
      const pk = Math.round(n(r.pass_k) * 100);
      const tone = n(r.hard_failures) ? "bad" : pk >= 90 ? "good" : pk >= 60 ? "mid" : "low";
      const att = n(r.recovery_attempts), ok = n(r.recovery_successes);
      return `<td class="kcell ${tone} ${best && best.v === v ? "best" : ""}">
        <b>${pk}%</b> <span>pass<sup>k</sup></span>
        <div class="ksub">${esc(fmtUSD(r.mean_cost_usd))}/run, ${esc(fmtMs(r.mean_duration_ms))}, ${n(r.mean_tool_calls).toFixed(1)} tools</div>
        <div class="ksub">${fmtInt(r.cases)} cases, ${fmtInt(r.runs)} runs${att ? `, recovered ${ok}/${att}` : ""}${n(r.hard_failures) ? `, <b class="warn">${fmtInt(r.hard_failures)} hard failures</b>` : ""}</div></td>`;
    }).join("");
    return `<tr><td><b>${esc(c)}</b></td>${tds}<td>${best ? `<span class="chip">${esc(best.v)}</span>` : '<span class="muted">none safe yet</span>'}</td></tr>`;
  }).join("");
  return `<div class="scroll-x"><table class="table knowledge">${head}${body}</table></div>`;
}

async function viewQuality() {
  const q = await api("/api/quality");
  const data = JSON.stringify(q);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Home", href: "#/" }, { label: "Quality" }]);
  if (!q.migrated) {
    main.innerHTML = `<div class="page-head"><div><h1>Quality</h1></div></div><div class="alert"><b>The database needs a migration.</b> Run <code>forgeone db migrate</code> on the server.</div>`;
    return true;
  }
  const byAgent = {};
  q.knowledge.forEach((r) => (byAgent[r.agent] = byAgent[r.agent] || []).push(r));
  const matrices = Object.entries(byAgent).map(([agent, rows]) =>
    panel(`What works for ${agent}`, `suite ${esc(rows[0].suite_version)}; pass<sup>k</sup> = passed in every repeat`, `<div class="panel-body flush">${knowledgeMatrix(rows)}</div>`)).join("");
  const evalRows = q.eval_runs.map((e) => {
    const sc = e.scorecard || {};
    return `<tr><td>${esc(fmtDate(e.started_at))}</td><td><b>${esc(e.variant_name || "default")}</b><div class="muted" style="font-size:12px">${esc(e.model || "")}</div></td>
      <td class="hide-sm">${esc(e.agent)}</td><td class="r hide-sm">${fmtInt(e.cases)} x ${fmtInt(e.repeats)}</td>
      <td class="r">${sc.pass_all_repeats != null ? `${Math.round(sc.pass_all_repeats * 100)}%` : "–"}</td>
      <td class="r">${sc.pass_rate != null ? `${Math.round(sc.pass_rate * 100)}%` : "–"}</td>
      <td class="r">${esc(fmtUSD(sc.mean_cost_usd))}</td>
      <td class="r">${sc.hard_failures ? (sc.hard_failures.length ? `<span class="warn">${sc.hard_failures.length}</span>` : "0") : "–"}</td>
      <td>${e.promoted ? '<span class="status ok">Champion</span>' : e.status === "running" ? status("running") : `<span class="muted" style="font-size:12.5px">${esc(e.gate?.reason || e.status)}</span>`}</td></tr>`;
  }).join("");
  const runRows = q.agent_runs.map((r) => `<tr data-href="#/run/${esc(r.run_id)}">
      <td><a class="row-link" href="#/run/${esc(r.run_id)}">${esc(fmtDate(r.started_at))}</a></td>
      <td>${esc((r.task || "").slice(0, 90))}${r.case_id && r.case_id !== "adhoc" ? `<div class="muted" style="font-size:12px">case ${esc(r.case_id)}</div>` : ""}</td>
      <td class="hide-sm">${esc(r.variant || "default")}</td><td class="hide-sm">${esc(r.mode === "eval" ? "eval" : "real")}</td>
      <td>${labelChip(r.label, r.label_source)}</td>
      <td class="r hide-sm">${fmtMs(r.duration_ms)}</td><td class="r">${esc(fmtUSD(r.cost))}</td></tr>`).join("");
  main.innerHTML = `
    <div class="page-head"><div><h1>Quality</h1>
      <p>Which agent variant works best for which kind of task, measured on the eval suite. Every run gets a verdict: from the checks automatically, and from you with <code>forgeone label</code>.</p></div></div>
    ${matrices || panel("What works", "", `<div class="empty">No eval results yet. On the server: <code>forgeone eval run --agent voyagerone</code></div>`)}
    ${panel("Eval runs", "one row per variant tested", q.eval_runs.length ? `<div class="scroll-x"><table class="table">
      <thead><tr><th>Started</th><th>Variant</th><th class="hide-sm">Agent</th><th class="r hide-sm">Cases x repeats</th><th class="r">Pass<sup>k</sup></th><th class="r">Pass rate</th><th class="r">Cost/run</th><th class="r">Hard fails</th><th>Gate</th></tr></thead>
      <tbody>${evalRows}</tbody></table></div>` : `<div class="empty">No eval runs yet.</div>`)}
    ${panel("Agent runs", "real tasks and eval runs, newest first", q.agent_runs.length ? `<div class="scroll-x"><table class="table">
      <thead><tr><th>Started</th><th>Task</th><th class="hide-sm">Variant</th><th class="hide-sm">Kind</th><th>Verdict</th><th class="r hide-sm">Duration</th><th class="r">Cost</th></tr></thead>
      <tbody>${runRows}</tbody></table></div>` : `<div class="empty">No agent runs yet. On the server: <code>forgeone run "your research question"</code></div>`)}`;
  return true;
}

function workActionsHTML(acts) {
  if (!acts.length) return `<div class="empty">No actions recorded in this session.</div>`;
  return `<div class="scroll-x"><table class="table">
    <thead><tr><th>Time</th><th>For</th><th>Action</th><th>Result</th><th class="hide-sm">By</th><th class="r">Cost</th></tr></thead><tbody>${acts.map((a) => {
      const href = a.task_id ? `#/t/${esc(a.task_id)}` : "";
      const what = a.task_id ? `<span class="tcode">${esc(a.code)}</span> ${esc(a.title)}` : a.milestone_id ? `M${esc(a.milestone_seq)} ${esc(a.milestone_title)}` : "the goal";
      return `<tr ${href ? `data-href="${href}"` : ""}><td class="num">${esc(new Date(a.started_at).toLocaleTimeString("en-GB"))}</td>
        <td>${href ? `<a class="row-link" href="${href}">${what}</a>` : what}</td>
        <td>${esc(KIND_TEXT[a.kind] || a.kind)}${a.attempt > 1 ? ` <span class="muted">try ${esc(a.attempt)}</span>` : ""}</td>
        <td>${status(a.status === "pending" ? "waiting" : a.status)}${a.failure_reason ? ` <span class="chip bad">${esc(a.failure_reason)}</span>` : ""}</td>
        <td class="hide-sm muted">${esc(a.actor)}</td><td class="r">${a.cost_usd ? esc(fmtUSD(a.cost_usd)) : ""}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function toolCallsHTML(calls) {
  if (!calls.length) return `<div class="empty">No tool calls.</div>`;
  return `<ul class="calls">${calls.map((c, i) => `<li><div class="call" style="cursor:default">
      <span class="node">${i + 1}. ${esc(c.tool)}</span>
      <span class="meta">${c.ok ? status("ok") : status("error")}${c.recovery_attempt ? ' <span class="chip">recovery attempt</span>' : ""}<br>
        ${esc(Object.values(c.args || {}).join(", ").slice(0, 120))}${c.error ? `<br><span class="warn">${esc(c.error.slice(0, 140))}</span>` : ""}</span>
      <span class="meta">${fmtMs(c.duration_ms)}</span></div></li>`).join("")}</ul>`;
}

/* ---------------- project docs ---------------- */
const DOWNLOAD = '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>';
const docCache = new Map();
async function loadDoc(meta) {
  // Our own committed documents (trusted content), parsed and cached.
  if (!docCache.has(meta.slug)) {
    const res = await fetch(meta.html);
    if (!res.ok) throw new Error(`Document not found (${res.status})`);
    const page = new DOMParser().parseFromString(await res.text(), "text/html");
    const article = page.querySelector("#doc");
    if (!article) throw new Error("Document has no #doc article");
    docCache.set(meta.slug, article);
  }
  return docCache.get(meta.slug);
}
const dayBadge = (m, i) => {
  const num = (m.slug.match(/(\d+)$/) || [, i + 1])[1].replace(/^0+/, "");
  return `<span class="day" aria-hidden="true">DAY<b>${esc(num)}</b></span>`;
};

const fmtBytes = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

async function viewDocs() {
  const all = await api("/api/docs");
  const docs = all.days;
  const data = JSON.stringify(all);
  if (data === state.lastJSON) return false;
  state.lastJSON = data;
  setCrumbs([{ label: "Home", href: "#/" }, { label: "Project docs" }]);
  const list = docs
    .map((m, i) => `<li class="doc-item">${dayBadge(m, i)}
      <div><h3><a href="#/docs/${esc(m.slug)}">${esc(m.title)}</a></h3><p>${esc(m.summary)}</p>
        <div class="muted" style="font-size:12.5px;margin-top:6px">${esc(fmtDay(m.date))}</div></div>
      <div class="doc-actions"><a class="btn primary" href="#/docs/${esc(m.slug)}">Read</a>
        <a class="btn" href="${esc(m.pdf)}" download>${DOWNLOAD}PDF</a></div></li>`)
    .join("");
  let figures = "";
  if (docs.length) {
    try {
      const art = await loadDoc(docs[docs.length - 1]);
      figures = [...art.querySelectorAll("figure")].map((f) => `<div class="doc">${f.outerHTML}</div>`).join("");
    } catch { figures = ""; }
  }
  main.innerHTML = `
    <div class="page-head"><div><h1>Project docs</h1><p>How ForgeOne is built and why: one document per working day, readable here and as PDF.</p></div></div>
    ${panel("Day by day", `${docs.length} ${docs.length === 1 ? "document" : "documents"}`, `<ul class="doc-list">${list}</ul>`)}
    ${all.files.length ? panel("Other documents", "every file in docs/", `<ul class="doc-list">${all.files
      .map((f) => `<li class="doc-item"><span class="day file" aria-hidden="true">${esc(f.name.split(".").pop().toUpperCase().slice(0, 4))}</span>
        <div><h3><a href="${esc(f.url)}" download>${esc(f.title)}</a></h3>
          <p class="muted">${esc(f.name)}, ${esc(f.kind)}, ${esc(fmtBytes(f.bytes))}, updated ${esc(ago(f.modified))}</p></div>
        <div class="doc-actions"><a class="btn" href="${esc(f.url)}" target="_blank" rel="noopener">Open</a>
          <a class="btn primary" href="${esc(f.url)}" download>${DOWNLOAD}Download</a></div></li>`)
      .join("")}</ul>`) : ""}
    ${figures ? `<div class="page-head" style="margin:36px 0 16px"><div><h1 style="font-size:20px">Architecture at a glance</h1><p>The diagrams from the latest document.</p></div></div><div class="fig-grid">${figures}</div>` : ""}`;
  return true;
}

async function viewDoc(slug) {
  const docs = (await api("/api/docs")).days;
  const meta = docs.find((d) => d.slug === slug);
  if (!meta) throw new Error("Document not found");
  if (state.lastJSON === `doc:${slug}`) return false;
  state.lastJSON = `doc:${slug}`;
  setCrumbs([{ label: "Home", href: "#/" }, { label: "Project docs", href: "#/docs" }, { label: meta.title }]);
  const art = await loadDoc(meta);
  main.innerHTML = `
    <div class="reader-bar">
      <a class="btn" href="#/docs">All documents</a>
      <div class="doc-actions"><a class="btn" href="${esc(meta.html)}" target="_blank" rel="noopener">Open full page</a>
        <a class="btn primary" href="${esc(meta.pdf)}" download>${DOWNLOAD}Download PDF</a></div>
    </div>
    <section class="panel reader"><article class="doc">${art.innerHTML}</article></section>`;
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
      <dl class="mini-kpis">
        <div><dt>Result</dt><dd style="color:${c.status === "ok" ? "var(--ok)" : "var(--fail)"}">${c.status === "ok" ? "Answered" : "Failed"}</dd></div>
        <div><dt>Latency</dt><dd>${esc(fmtMs(c.latency_ms))}</dd></div>
        <div><dt>Tokens in</dt><dd>${esc(fmtInt(c.input_tokens))}</dd></div>
        <div><dt>Tokens out</dt><dd>${esc(fmtInt(c.output_tokens))}</dd></div>
        <div><dt>Cost</dt><dd>${esc(fmtUSD(c.cost))}</dd></div>
      </dl>
      <p class="muted" style="font-size:13px;margin:12px 0 0">${esc(c.model || "")}${c.stop_reason ? `, stopped because: ${esc(c.stop_reason)}` : ""}</p>
      ${c.error ? `<div class="alert" style="margin-top:12px"><b>Failed.</b> ${esc(c.error)}</div>` : ""}
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
  if (parts[0] === "g" && parts[1]) return { name: "wgoal", id: parts[1], nav: "work" };
  if (parts[0] === "t" && parts[1]) return { name: "wtask", id: parts[1], nav: "work" };
  if (parts[0] === "inbox") return { name: "inbox", nav: "inbox" };
  if (parts[0] === "builds") return { name: "home", nav: "builds" };
  if (parts[0] === "build-goals") return { name: "goals", nav: "builds" };
  if (parts[0] === "build-goal" && parts[1]) return { name: "goal", id: parts[1], nav: "builds" };
  if (parts[0] === "goal" && parts[1]) return { name: "goal", id: parts[1], nav: "builds" };       // old links
  if (parts[0] === "run" && parts[1]) return { name: "run", id: parts[1], call: parts[2] === "call" ? parts[3] : null, nav: "builds" };  // nav refined after load
  if (parts[0] === "quality") return { name: "quality", nav: "quality" };
  if (parts[0] === "docs" && parts[1]) return { name: "doc", id: parts[1], nav: "docs" };
  if (parts[0] === "docs") return { name: "docs", nav: "docs" };
  if (parts[0] === "errors") return { name: "errors", nav: "errors" };
  return { name: "work", nav: "work" };
}
async function render({ quiet = false } = {}) {
  const route = parse();
  const sameView = state.route && state.route.name === route.name && state.route.id === route.id;
  if (!sameView) { state.lastJSON = ""; if (!quiet) main.innerHTML = `<div class="skeleton"></div><div class="skeleton" style="width:45%"></div><div class="skeleton" style="width:70%"></div>`; }
  state.route = route;
  document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === route.nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  try {
    if (route.name === "goal") await viewGoal(route.id);
    else if (route.name === "run") await viewRun(route.id, route.call);
    else if (route.name === "goals") await viewGoals();
    else if (route.name === "home") await viewHome();
    else if (route.name === "wgoal") await viewWorkGoal(route.id);
    else if (route.name === "wtask") await viewTask(route.id);
    else if (route.name === "inbox") await viewInbox();
    else if (route.name === "quality") await viewQuality();
    else if (route.name === "docs") await viewDocs();
    else if (route.name === "doc") await viewDoc(route.id);
    else if (route.name === "errors") await viewErrors();
    else await viewWork();
    if (!sameView && !quiet) { window.scrollTo(0, 0); main.focus({ preventScroll: true }); }
    $("#live").classList.remove("stale");
    $("#updated").textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (e) {
    $("#live").classList.add("stale");
    if (!quiet || !main.children.length)
      main.innerHTML = `<div class="page-head"><div><h1>Can't load this page</h1></div></div><div class="alert"><b>${esc(e.message)}</b></div>
        <p class="muted" style="margin-top:16px">Check that the database is running on the server with <code>docker compose ps</code>, then reload.</p>`;
  }
}
async function refreshBadge() {
  try {
    const ov = await api("/api/overview");
    const b = $("#err-badge");
    b.hidden = !n(ov.errors_24h);
    b.textContent = fmtInt(ov.errors_24h);
    const ib = $("#inbox-badge");
    ib.hidden = !n(ov.needs_you);
    ib.textContent = fmtInt(ov.needs_you);
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
  const cp = e.target.closest("[data-copy]");
  if (cp) {
    navigator.clipboard?.writeText(cp.dataset.copy).then(() => { cp.textContent = "Copied"; setTimeout(() => (cp.textContent = "Copy"), 1500); });
    return;
  }
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

window.addEventListener("hashchange", () => { tip.hidden = true; render(); });
render();
refreshBadge();
setInterval(() => {
  if (document.hidden || !$("#sheet").hidden || state.route?.nav === "docs") return;
  render({ quiet: true });
  refreshBadge();
}, REFRESH_MS);

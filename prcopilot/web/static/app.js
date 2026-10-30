/* PR Copilot dashboard - vanilla JS, Chart.js and GSAP (both optional: the UI works without them). */
(function () {
  "use strict";

  const app = document.getElementById("app");
  // ?motion=0 turns animation off (handy for screenshots); prefers-reduced-motion does the same.
  const motion = typeof gsap !== "undefined" && !matchMedia("(prefers-reduced-motion: reduce)").matches
    && new URLSearchParams(location.search).get("motion") !== "0";
  const hasChart = typeof Chart !== "undefined";
  const charts = [];
  let rerender = null;          // re-draw charts on theme change
  let config = null;
  let firstPaint = true;
  // Keep tweens on wall-clock time (no slow motion after a stalled frame, e.g. in headless screenshots).
  if (motion) gsap.ticker.lagSmoothing(0);

  // ---------- helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtInt = (n) => Number(n || 0).toLocaleString("en-US");
  const fmtUsd = (n, d = 4) => "$" + Number(n || 0).toFixed(d);
  const fmtSec = (ms) => (Number(ms || 0) / 1000).toFixed(1) + " s";
  const pct = (x) => Math.round(Number(x || 0) * 100) + "%";

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    if (!res.ok) {
      let msg = res.status + " " + res.statusText;
      try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) {}
      throw new Error(msg);
    }
    return res.json();
  }

  function ago(iso) {
    const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
    const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    const days = Math.floor(s / 86400);
    return days === 1 ? "yesterday" : days + " days ago";
  }
  const dayLabel = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

  const I = {
    pr: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v12"/><path d="M18 15.5V10a4 4 0 0 0-4-4h-3"/>',
    shield: '<path d="M12 3 4 6v6c0 4.4 3.4 8.3 8 9 4.6-.7 8-4.6 8-9V6l-8-3Z"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    coin: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .8-3 2s1.3 1.7 3 2 3 .8 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5"/><path d="M12 6v12"/>',
    play: '<path d="M7 4.5v15l12-7.5-12-7.5Z"/>',
    spark: '<path d="m12 3 1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3Z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    bars: '<path d="M4 6h10M4 12h16M4 18h7"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    arrow: '<path d="m9 6 6 6-6 6"/>',
    back: '<path d="m15 6-6 6 6 6"/>',
    hook: '<path d="M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2"/><path d="m6 17 3.13-5.78c.53-.97.1-2.18-.5-3.1a4 4 0 1 1 6.89-4.06"/><path d="m12 6 3.13 5.73C15.66 12.7 16.9 13 18 13a4 4 0 0 1 0 8"/>',
    split: '<path d="M16 3h5v5M8 3H3v5M21 3l-7 7M3 3l7 7M12 22v-8"/>',
    brain: '<path d="M9 3a3 3 0 0 0-3 3v.5A3 3 0 0 0 4 9.3 3 3 0 0 0 5 15a3 3 0 0 0 4 4.9V3Z"/><path d="M15 3a3 3 0 0 1 3 3v.5a3 3 0 0 1 2 2.8A3 3 0 0 1 19 15a3 3 0 0 1-4 4.9V3Z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    comment: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    bug: '<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M19 7l-3 2M5 7l3 2M19 13h-3M5 13h3M19 19l-3-2M5 19l3-2M12 6V3"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"/>',
    flask: '<path d="M9 3h6M10 3v6.5L4.6 18.2A1.8 1.8 0 0 0 6.1 21h11.8a1.8 1.8 0 0 0 1.5-2.8L14 9.5V3"/>',
    brush: '<path d="m14.6 3.4 6 6-9.2 9.2-6-6 9.2-9.2Z"/><path d="M5.4 12.6 3 21l8.4-2.4"/>',
    tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"/><path d="M7.5 7.5h.01"/>',
    fire: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5Z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
  };
  const icon = (name, cls) => `<svg${cls ? ` class="${cls}"` : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name]}</svg>`;
  const CAT_ICON = { security: "shield", bug: "bug", performance: "zap", tests: "flask", style: "brush" };
  const SEV_ORDER = { critical: 0, major: 1, minor: 2, info: 3 };

  const riskBadge = (r) => r ? `<span class="badge dot risk-${esc(r)}">${esc(r)}</span>` : `<span class="badge neutral">pending</span>`;
  const sevBadge = (s) => `<span class="badge sev-${esc(s)}">${esc(s)}</span>`;

  function toast(msg, isError) {
    const t = $("#toast");
    t.className = "toast" + (isError ? " error" : "");
    t.innerHTML = icon(isError ? "alert" : "check") + `<span>${msg}</span>`;
    t.hidden = false;
    if (motion) gsap.fromTo(t, { y: 16, opacity: 0, scale: .98 }, { y: 0, opacity: 1, scale: 1, duration: .3, ease: "power3.out" });
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      if (motion) gsap.to(t, { y: 10, opacity: 0, duration: .18, onComplete: () => { t.hidden = true; } });
      else t.hidden = true;
    }, 3600);
  }

  // ---------- theme + nav ----------
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("prcopilot-theme", next); } catch (e) {}
    if (rerender) rerender();
  });
  const navBtn = $("#nav-toggle"), nav = $("#main-nav");
  navBtn.addEventListener("click", () => {
    const open = !nav.classList.contains("open");
    nav.classList.toggle("open", open);
    navBtn.setAttribute("aria-expanded", String(open));
  });
  nav.addEventListener("click", (e) => { if (e.target.closest("a")) { nav.classList.remove("open"); navBtn.setAttribute("aria-expanded", "false"); } });

  function setActive(name) { $$("nav a[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name)); }

  // ---------- motion ----------
  function intro() {
    if (!motion) return;
    const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
    if (firstPaint) tl.from(".topbar", { y: -14, opacity: 0, duration: .5 }, 0);
    tl.from($$(".page-head", app), { y: 16, opacity: 0, duration: .5 }, firstPaint ? .08 : 0)
      .from($$(".kpi", app), { y: 16, opacity: 0, duration: .5, stagger: .06, clearProps: "transform" }, "<.06")
      .from($$(".panel, .file-diff", app), { y: 16, opacity: 0, duration: .55, stagger: .05, clearProps: "transform" }, "<.1");
    const rows = $$("tbody.rows tr", app).slice(0, 25);
    if (rows.length) tl.from(rows, { opacity: 0, y: 8, duration: .35, stagger: .03 }, "<.15");
    const bars = $$(".hot .bar span", app);
    if (bars.length) tl.from(bars, { scaleX: 0, duration: .8, stagger: .05, ease: "power2.out" }, "<");
    firstPaint = false;
  }

  function countUp(root) {
    $$("[data-count]", root || app).forEach((el) => {
      const end = parseFloat(el.dataset.count);
      const fmt = el.dataset.fmt || "int";
      const render = (v) => {
        if (fmt === "usd") return fmtUsd(v, 2);
        if (fmt === "sec") return (v / 1000).toFixed(1) + " s";
        if (fmt === "pct") return Math.round(v * 100) + "%";
        return fmtInt(Math.round(v));
      };
      if (!motion) { el.textContent = render(end); return; }
      const o = { v: 0 };
      gsap.to(o, { v: end, duration: .9, ease: "power2.out", onUpdate: () => { el.textContent = render(o.v); } });
    });
  }

  // ---------- charts ----------
  function destroyCharts() { while (charts.length) charts.pop().destroy(); }
  function chartDefaults() {
    Chart.defaults.font.family = css("--font");
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css("--text-3-strong");
    Chart.defaults.borderColor = css("--border");
  }
  const tooltip = () => ({
    backgroundColor: css("--surface-strong"), titleColor: css("--text"), bodyColor: css("--text-2"),
    borderColor: css("--border-strong"), borderWidth: 1, padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true,
  });

  function riskTrendChart(canvas, trend) {
    const ctx = canvas.getContext("2d");
    const mk = (key, color) => ({
      label: key[0].toUpperCase() + key.slice(1), data: trend.map((d) => d[key]), backgroundColor: color,
      borderRadius: 5, borderSkipped: false, maxBarThickness: 26, stack: "risk",
    });
    charts.push(new Chart(ctx, {
      type: "bar",
      data: { labels: trend.map((d) => dayLabel(d.day)), datasets: [mk("low", css("--success")), mk("medium", css("--warning")), mk("high", css("--danger"))] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: motion ? { duration: 800, easing: "easeOutQuart" } : false,
        plugins: { legend: { display: false }, tooltip: tooltip() },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0, stepSize: 1 }, grid: { color: css("--border") }, border: { display: false } },
        },
      },
    }));
  }

  function categoryChart(canvas, categories) {
    const cats = Object.keys(categories).sort((a, b) => categories[b].total - categories[a].total);
    const sev = [["critical", css("--danger")], ["major", css("--warning")], ["minor", css("--info")], ["info", css("--text-3")]];
    charts.push(new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: cats.map((c) => c[0].toUpperCase() + c.slice(1)),
        datasets: sev.map(([s, color]) => ({ label: s, data: cats.map((c) => categories[c][s] || 0), backgroundColor: color, borderRadius: 5, borderSkipped: false, maxBarThickness: 22 })),
      },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false, layout: { padding: { left: 6 } }, animation: motion ? { duration: 800, easing: "easeOutQuart" } : false,
        plugins: { legend: { display: false }, tooltip: tooltip() },
        scales: {
          x: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, grid: { color: css("--border") }, border: { display: false } },
          y: { stacked: true, grid: { display: false }, border: { display: false } },
        },
      },
    }));
  }

  function sparkline(values, color) {
    const max = Math.max(1, ...values), w = 84, h = 30, step = w / Math.max(1, values.length - 1);
    const pts = values.map((v, i) => [i * step, h - 3 - (v / max) * (h - 6)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const id = "sg" + Math.random().toString(36).slice(2, 7);
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${d} L${w} ${h} L0 ${h} Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }

  // ---------- shared fragments ----------
  function demoButton(label) {
    if (!config || config.mode !== "demo") return "";
    return `<button class="btn" id="run-demo" type="button">${icon("play")}<span>${label || "Run demo review"}</span></button>`;
  }

  function bindDemoButton() {
    const btn = $("#run-demo");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.innerHTML = icon("loader", "spin") + "<span>Reviewing...</span>";
      try {
        const [review] = await Promise.all([api("/api/demo/run", { method: "POST", body: "{}" }), new Promise((r) => setTimeout(r, 900))]);
        toast(`Reviewed <b>${esc(review.repo)}#${review.pr_number}</b>: risk ${esc(review.risk)}, ${review.findings.length} finding(s)`);
        location.hash = "#/reviews/" + review.id;
      } catch (e) {
        toast("Demo review failed: " + esc(e.message), true);
        if (motion) gsap.fromTo(btn, { x: -6 }, { x: 0, duration: .5, ease: "elastic.out(1, .3)" });
        btn.disabled = false;
        btn.innerHTML = icon("play") + "<span>Run demo review</span>";
      }
    });
  }

  function reviewRows(items, compact) {
    return items.map((r) => {
      const crit = r.serious_count || 0;
      return `<tr class="click" data-href="#/reviews/${r.id}" tabindex="0">
        <td><div class="pr-cell"><strong title="${esc(r.title)}">${esc(r.title || "(untitled)")}</strong><span class="cell-sub">${esc(r.repo)}#${r.pr_number} &middot; ${esc(r.author || r.source)}</span></div></td>
        <td>${r.status === "failed" ? '<span class="badge sev-critical">failed</span>' : riskBadge(r.risk)}</td>
        <td class="num"><span class="counts">${crit ? `<span class="chip c-critical">${crit} serious</span>` : ""}<span class="chip">${r.findings_count}</span></span></td>
        ${compact ? "" : `<td class="num hide-sm">${r.duration_ms ? fmtSec(r.duration_ms) : "-"}</td>`}
        ${compact ? "" : `<td class="num hide-sm">${fmtUsd(r.cost_usd)}</td>`}
        <td class="num hide-sm faint">${ago(r.created_at)}</td>
        <td class="num">${icon("arrow", "row-arrow")}</td>
      </tr>`;
    }).join("");
  }

  function reviewTable(items, compact) {
    if (!items.length) return `<div class="empty"><div class="e-icon">${icon("pr")}</div><p><b>No reviews yet.</b></p><p>Send a webhook, POST a diff to <code>/api/reviews</code> or run a demo review.</p></div>`;
    return `<div class="table-wrap"><table>
      <thead><tr><th>Pull request</th><th>Risk</th><th class="num">Findings</th>${compact ? "" : '<th class="num hide-sm">Review time</th><th class="num hide-sm">Cost</th>'}<th class="num hide-sm">When</th><th></th></tr></thead>
      <tbody class="rows">${reviewRows(items, compact)}</tbody></table></div>`;
  }

  function bindRows() {
    $$("tr[data-href]", app).forEach((tr) => {
      tr.addEventListener("click", () => { location.hash = tr.dataset.href; });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") location.hash = tr.dataset.href; });
    });
  }

  // ---------- pages ----------
  async function overview() {
    setActive("overview");
    const [stats, list] = await Promise.all([api("/api/stats?days=14"), api("/api/reviews?limit=8")]);
    const daily = stats.trend.map((d) => d.low + d.medium + d.high);
    const dailyHigh = stats.trend.map((d) => d.high);
    const saved = Math.max(0, stats.cost_without_cache_usd - stats.total_cost_usd);
    const est = stats.estimated ? " (estimated)" : "";
    const maxHot = Math.max(1, ...stats.hotspots.map((h) => h.n));

    app.innerHTML = `
      <section class="page-head">
        <div>
          <div class="eyebrow">${icon("spark")} Overview &middot; last 14 days</div>
          <h1>Pull request reviews</h1>
          <p>Every PR is reviewed against the same rubric: risk, line-level findings, missing tests and a changelog entry. ${config.mode === "demo" ? "Demo data: canned reviews of six sample PRs, no model calls." : `Live reviews with <code>${esc(config.model)}</code>.`}</p>
        </div>
        <div class="head-actions">${demoButton()}<a class="btn btn-secondary" href="#/reviews">${icon("list")}<span>All reviews</span></a></div>
      </section>

      <section class="kpis">
        <div class="kpi"><div class="label">${icon("pr")} PRs reviewed</div><div class="value" data-count="${stats.reviews}">0</div><div class="sub">${stats.repos} repositories${stats.failed ? ` &middot; ${stats.failed} failed` : ""}</div><div class="spark">${sparkline(daily, css("--accent"))}</div></div>
        <div class="kpi"><div class="label">${icon("shield")} High-risk caught</div><div class="value danger" data-count="${stats.high_risk}">0</div><div class="sub">${stats.medium_risk} medium &middot; ${stats.low_risk} low</div><div class="spark">${sparkline(dailyHigh, css("--danger"))}</div></div>
        <div class="kpi"><div class="label">${icon("clock")} Avg review time</div><div class="value" data-count="${stats.avg_review_ms}" data-fmt="sec">0</div><div class="sub">diff in, review out${stats.estimated ? " (simulated)" : ""}</div></div>
        <div class="kpi"><div class="label">${icon("coin")} Model cost</div><div class="value" data-count="${stats.total_cost_usd}" data-fmt="usd">0</div><div class="sub">${fmtUsd(stats.avg_cost_usd)} per PR &middot; caching saved ${fmtUsd(saved, 2)}</div></div>
      </section>

      <section class="grid split">
        <div class="panel">
          <div class="panel-head"><h2>${icon("chart", "h-icon")} Risk per day</h2><span class="sub">completed reviews</span>
            <div class="right legend"><span><i style="background:var(--success)"></i>Low</span><span><i style="background:var(--warning)"></i>Medium</span><span><i style="background:var(--danger)"></i>High</span></div></div>
          <div class="chart-box"><canvas id="trend-chart" aria-label="Reviews per day by risk"></canvas>${hasChart ? "" : '<div class="chart-fallback">Charts need Chart.js (CDN unavailable).</div>'}</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>${icon("bars", "h-icon")} Findings by category</h2>
            <div class="right legend"><span><i style="background:var(--danger)"></i>Critical</span><span><i style="background:var(--warning)"></i>Major</span><span><i style="background:var(--info)"></i>Minor</span><span><i style="background:var(--text-3)"></i>Info</span></div></div>
          <div class="chart-box"><canvas id="cat-chart" aria-label="Findings by category and severity"></canvas>${hasChart ? "" : '<div class="chart-fallback">Charts need Chart.js (CDN unavailable).</div>'}</div>
        </div>
      </section>

      <section class="grid split">
        <div class="panel flush">
          <div class="panel-head"><h2>${icon("list", "h-icon")} Recent reviews</h2><span class="sub">click a row for the diff and inline findings</span></div>
          ${reviewTable(list.items, true)}
        </div>
        <div class="stack">
          <div class="panel">
            <div class="panel-head"><h2>${icon("hook", "h-icon")} How a review runs</h2></div>
            <ol class="pipeline">
              <li><span class="p-icon">${icon("hook")}</span><div><strong>Webhook or GitHub Action</strong><span>HMAC-verified <code>pull_request</code> event, or the CI step</span></div></li>
              <li><span class="p-icon">${icon("split")}</span><div><strong>Parse and chunk the diff</strong><span>per file, generated files skipped, static hints added</span></div></li>
              <li><span class="p-icon">${icon("brain")}</span><div><strong>Claude review</strong><span>Structured Outputs JSON, cached rubric, validated lines</span></div></li>
              <li><span class="p-icon">${icon("comment")}</span><div><strong>Post to the PR</strong><span>summary, inline comments, missing tests, changelog</span></div></li>
            </ol>
          </div>
          <div class="panel">
            <div class="panel-head"><h2>${icon("fire", "h-icon")} Hotspot files</h2><span class="sub">most serious findings</span></div>
            <div class="hotspots">${stats.hotspots.map((h) => `<div class="hot"><span class="path" title="${esc(h.file)}">${esc(h.file)}</span><span class="faint num">${h.serious} serious / ${h.n}</span><div class="bar"><span style="width:${Math.round((h.n / maxHot) * 100)}%"></span></div></div>`).join("") || '<p class="faint">No findings yet.</p>'}</div>
          </div>
        </div>
      </section>`;

    bindDemoButton();
    bindRows();
    rerender = () => {
      if (!hasChart) return;
      destroyCharts();
      chartDefaults();
      riskTrendChart($("#trend-chart"), stats.trend);
      categoryChart($("#cat-chart"), stats.categories);
    };
    rerender();
    // Chart.js measures labels with the current font: draw again once Inter has loaded.
    if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(() => { if (rerender) rerender(); });
    intro();
    countUp();
  }

  async function reviewsPage(filter) {
    setActive("reviews");
    const q = filter ? "&risk=" + filter : "";
    const list = await api("/api/reviews?limit=200" + q);
    app.innerHTML = `
      <section class="page-head">
        <div><div class="eyebrow">${icon("list")} Reviews</div><h1>All reviews</h1><p>${list.items.length} review(s)${filter ? ` with ${esc(filter)} risk` : ""}. Newest first.</p></div>
        <div class="head-actions"><div class="seg" role="group" aria-label="Filter by risk">
          ${["", "high", "medium", "low"].map((r) => `<button type="button" data-risk="${r}" class="${(filter || "") === r ? "on" : ""}">${r ? r[0].toUpperCase() + r.slice(1) : "All"}</button>`).join("")}
        </div>${demoButton()}</div>
      </section>
      <section class="panel flush">${reviewTable(list.items)}</section>`;
    $$(".seg button", app).forEach((b) => b.addEventListener("click", () => { location.hash = "#/reviews" + (b.dataset.risk ? "?risk=" + b.dataset.risk : ""); }));
    bindDemoButton();
    bindRows();
    rerender = null;
    intro();
  }

  // Minimal unified-diff parser (mirrors prcopilot/diffparse.py) for the viewer.
  function parseDiff(text) {
    const files = [];
    let f = null, h = null, oldNo = 0, newNo = 0, remainingOld = 0, remainingNew = 0;
    const lines = text.replace(/\r\n/g, "\n").split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if ((m = line.match(/^diff --git a\/(.+?) b\/(.+)$/))) { f = { path: m[2], hunks: [], add: 0, del: 0, status: "modified" }; files.push(f); h = null; continue; }
      if (!f) continue;
      if (line.startsWith("new file mode")) { f.status = "added"; continue; }
      if (line.startsWith("deleted file mode")) { f.status = "deleted"; continue; }
      if ((m = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/))) {
        h = { header: line, lines: [] }; f.hunks.push(h);
        oldNo = +m[1]; newNo = +m[3]; remainingOld = m[2] == null ? 1 : +m[2]; remainingNew = m[4] == null ? 1 : +m[4];
        continue;
      }
      if (!h || (remainingOld <= 0 && remainingNew <= 0)) continue;
      const k = line[0] === undefined ? " " : line[0];
      const body = line.slice(1);
      if (k === "+") { h.lines.push({ k, body, n: newNo++ }); f.add++; remainingNew--; }
      else if (k === "-") { h.lines.push({ k, body, o: oldNo++ }); f.del++; remainingOld--; }
      else if (k === " ") { h.lines.push({ k, body, n: newNo++, o: oldNo++ }); remainingOld--; remainingNew--; }
    }
    return files;
  }

  function noteHtml(f, idx) {
    return `<div class="inline-note sev-${esc(f.severity)}-note" id="finding-${idx}">
      <div class="note-head">${sevBadge(f.severity)}<span class="chip">${icon(CAT_ICON[f.category] || "tag")}${esc(f.category)}</span><strong>${esc(f.title)}</strong></div>
      <p>${esc(f.explanation)}</p><pre>${esc(f.suggestion)}</pre></div>`;
  }

  function diffHtml(diffText, findings) {
    const files = parseDiff(diffText || "");
    if (!files.length) return '<p class="faint">Diff not stored for this review.</p>';
    const byFile = {};
    findings.forEach((f, i) => { (byFile[f.file] = byFile[f.file] || []).push(Object.assign({ idx: i }, f)); });
    return files.map((file) => {
      const fs = byFile[file.path] || [];
      const fileLevel = fs.filter((f) => !f.line);
      const byLine = {};
      fs.filter((f) => f.line).forEach((f) => { (byLine[f.line] = byLine[f.line] || []).push(f); });
      const rows = file.hunks.map((h) => {
        const out = [`<tr class="hunk"><td class="ln"></td><td class="ln"></td><td class="sign"></td><td class="code">${esc(h.header)}</td></tr>`];
        h.lines.forEach((l) => {
          const cls = l.k === "+" ? "add" : l.k === "-" ? "del" : "ctx";
          const notes = l.k !== "-" ? byLine[l.n] : null;
          const worst = notes ? notes.slice().sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity])[0].severity : "";
          out.push(`<tr class="${cls}${notes ? ` flagged sev-${worst}-row` : ""}"${notes ? ` id="line-${esc(file.path)}-${l.n}"` : ""}><td class="ln">${l.o || ""}</td><td class="ln">${l.n || ""}</td><td class="sign">${l.k === " " ? "" : l.k}</td><td class="code">${esc(l.body) || " "}</td></tr>`);
          if (notes) out.push(`<tr class="note-row"><td colspan="4">${notes.map((n) => noteHtml(n, n.idx)).join("")}</td></tr>`);
        });
        return out.join("");
      }).join("");
      return `<div class="file-diff">
        <div class="file-head">${icon("file")}<span class="path">${esc(file.path)}</span>${file.status !== "modified" ? `<span class="badge neutral">${esc(file.status)}</span>` : ""}${fs.length ? `<span class="badge sev-${esc(fs.slice().sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity])[0].severity)}">${fs.length} finding${fs.length > 1 ? "s" : ""}</span>` : ""}<span class="stat"><span class="a">+${file.add}</span><span class="d">-${file.del}</span></span></div>
        ${fileLevel.length ? `<div class="file-notes">${fileLevel.map((n) => noteHtml(n, n.idx)).join("")}</div>` : ""}
        <div class="diff"><table><tbody>${rows}</tbody></table></div></div>`;
    }).join("");
  }

  async function detail(id) {
    setActive("reviews");
    const r = await api("/api/reviews/" + encodeURIComponent(id));
    const findings = r.findings || [];
    const counts = { critical: 0, major: 0, minor: 0, info: 0 };
    findings.forEach((f) => { counts[f.severity] = (counts[f.severity] || 0) + 1; });
    const promptTokens = r.input_tokens + r.cache_read_tokens + r.cache_write_tokens;
    const saved = Math.max(0, r.cost_without_cache_usd - r.cost_usd);
    const est = r.estimated ? ' <span class="badge neutral">estimated</span>' : "";

    app.innerHTML = `
      <section class="page-head">
        <div>
          <div class="eyebrow"><a href="#/reviews">${icon("back")} Reviews</a> &middot; ${esc(r.repo)}#${r.pr_number}</div>
          <h1>${esc(r.title || "Untitled pull request")} ${r.status === "completed" ? riskBadge(r.risk) : `<span class="badge sev-critical">${esc(r.status)}</span>`}</h1>
          <div class="meta">
            <span>${icon("user")}<b>${esc(r.author || "unknown")}</b></span>
            <span>${icon("clock")}${ago(r.created_at)}</span>
            <span>${icon("cpu")}${r.mode === "demo" ? "demo reviewer (no model call)" : esc(r.model || "")}</span>
            <span>${icon("comment")}${r.posted ? (config.posts_to_github ? "posted to GitHub" : "recorded by the fake GitHub client") : "not posted"}</span>
            <span class="mono faint">${esc(r.prompt_version || "")}</span>
          </div>
        </div>
        <div class="head-actions"><a class="btn btn-secondary" href="/api/reviews/${r.id}" target="_blank" rel="noopener">${icon("file")}<span>JSON</span></a>${demoButton("Run another demo")}</div>
      </section>

      ${r.status === "failed" ? `<div class="panel"><strong>Review failed.</strong> <span class="muted">${esc(r.error || "")}</span></div>` : ""}

      <section class="kpis compact">
        <div class="kpi"><div class="label">Findings</div><div class="value" data-count="${findings.length}">0</div><div class="sub">${counts.critical} critical &middot; ${counts.major} major</div></div>
        <div class="kpi"><div class="label">Files</div><div class="value" data-count="${(r.files_reviewed || []).length}">0</div><div class="sub">${(r.files_skipped || []).length} skipped &middot; ${r.chunks} chunk(s)</div></div>
        <div class="kpi"><div class="label">Lines changed</div><div class="value num">+${r.additions}<span class="faint" style="font-size:.7em"> / -${r.deletions}</span></div><div class="sub">added / removed</div></div>
        <div class="kpi"><div class="label">Prompt tokens</div><div class="value" data-count="${promptTokens}">0</div><div class="sub">${pct(promptTokens ? r.cache_read_tokens / promptTokens : 0)} from cache</div></div>
        <div class="kpi"><div class="label">Cost</div><div class="value">${fmtUsd(r.cost_usd)}</div><div class="sub">${r.model_calls} model call(s)</div></div>
        <div class="kpi"><div class="label">Review time</div><div class="value" data-count="${r.duration_ms || 0}" data-fmt="sec">0</div><div class="sub">${r.estimated ? "simulated" : "measured"}</div></div>
      </section>

      <section class="grid split-detail">
        <div class="stack">
          <div class="panel summary-card"><div class="panel-head"><span class="ai-tag">${icon("spark")} AI summary</span>${r.mode === "demo" ? '<span class="badge neutral">demo</span>' : ""}</div><p class="ai-text">${esc(r.summary || "")}</p></div>
          <div class="panel">
            <div class="panel-head"><h2>${icon("file", "h-icon")} Diff with inline findings</h2><span class="sub">new-file line numbers, as Claude saw them</span></div>
            ${diffHtml(r.diff, findings)}
          </div>
        </div>
        <div class="stack">
          <div class="panel">
            <div class="panel-head"><h2>${icon("alert", "h-icon")} Findings</h2><span class="sub">${findings.length ? "most severe first" : ""}</span></div>
            ${findings.length ? `<div class="finding-list">${findings.map((f, i) => `
              <button class="finding" type="button" data-target="${f.line ? `line-${esc(f.file)}-${f.line}` : `finding-${i}`}">
                <span class="f-icon sev-${esc(f.severity)}">${icon(CAT_ICON[f.category] || "tag")}</span>
                <span><strong>${esc(f.title)}</strong><span class="loc">${sevBadge(f.severity)}<span>${esc(f.file)}${f.line ? ":" + f.line : ""}</span></span></span>
              </button>`).join("")}</div>` : `<div class="empty" style="padding:24px"><div class="e-icon">${icon("check")}</div><p><b>No findings.</b> Clean change.</p></div>`}
          </div>
          <div class="panel">
            <div class="panel-head"><h2>${icon("flask", "h-icon")} Missing tests</h2></div>
            ${(r.missing_tests || []).length ? `<ul class="checklist">${r.missing_tests.map((m) => `<li><span class="box"></span><span>${esc(m)}</span></li>`).join("")}</ul>` : '<p class="faint" style="margin:0">Tests cover the changed behaviour.</p>'}
          </div>
          <div class="panel">
            <div class="panel-head"><h2>${icon("tag", "h-icon")} Suggested changelog</h2></div>
            <div class="changelog">${esc(r.changelog || "-")}</div>
          </div>
          <div class="panel">
            <div class="panel-head"><h2>${icon("coin", "h-icon")} Tokens and cost</h2>${est}</div>
            <dl class="facts">
              <dt>Model</dt><dd class="mono">${esc(r.model || "-")}</dd>
              <dt>Input</dt><dd>${fmtInt(r.input_tokens)} uncached</dd>
              <dt>Cache</dt><dd>${fmtInt(r.cache_read_tokens)} read &middot; ${fmtInt(r.cache_write_tokens)} written</dd>
              <dt>Output</dt><dd>${fmtInt(r.output_tokens)}</dd>
              <dt>Cost</dt><dd>${fmtUsd(r.cost_usd, 5)} <span class="saving">(-${fmtUsd(saved, 5)} from caching)</span></dd>
            </dl>
            ${r.estimated ? '<p class="note-line">Demo mode: token counts are estimated from text length (~4 characters per token); latency is simulated.</p>' : ""}
          </div>
        </div>
      </section>`;

    bindDemoButton();
    $$(".finding", app).forEach((b) => b.addEventListener("click", () => {
      const el = document.getElementById(b.dataset.target);
      if (!el) return;
      el.scrollIntoView({ behavior: motion ? "smooth" : "auto", block: "center" });
      el.classList.remove("target"); void el.offsetWidth; el.classList.add("target");
    }));
    rerender = null;
    intro();
    countUp();
    if (motion) {
      gsap.from(".ai-text", { opacity: 0, y: 6, duration: .6, delay: .25, ease: "power2.out" });
      gsap.from($$(".inline-note", app), { opacity: 0, x: -6, duration: .4, stagger: .06, delay: .35, ease: "power2.out" });
    }
  }

  function errorPage(e) {
    rerender = null;
    app.innerHTML = `<div class="panel empty"><div class="e-icon">${icon("alert")}</div><h1 style="justify-content:center">Something went wrong</h1><p>${esc(e.message)}</p><p><a href="#/">Back to the overview</a></p></div>`;
  }

  // ---------- router ----------
  async function route() {
    destroyCharts();
    const hash = location.hash || "#/";
    try {
      if (!config) {
        config = await api("/api/config");
        const badge = $("#mode-badge");
        badge.hidden = false;
        badge.classList.toggle("live", config.mode === "live");
        $("#mode-text").innerHTML = config.mode === "demo" ? 'Demo<span class="long"> mode &middot; no API key</span>' : 'Live<span class="long"> &middot; ' + esc(config.model) + "</span>";
      }
      let m;
      if ((m = hash.match(/^#\/reviews\/(\d+)/))) await detail(m[1]);
      else if ((m = hash.match(/^#\/reviews(?:\?risk=(\w+))?/))) await reviewsPage(m[1]);
      else await overview();
      window.scrollTo(0, 0);
    } catch (e) { errorPage(e); }
  }
  window.addEventListener("hashchange", route);
  route();
})();

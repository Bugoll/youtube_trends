/* YouTube Trends dashboard — data loading and view rendering.
 * The force-directed idea map lives in graph.js (global `GraphView`). */
(function () {
  "use strict";

  const PALETTE = [
    "#58a6ff", "#3fb950", "#bc8cff", "#d29922", "#f85149",
    "#39c5cf", "#e3a8ff", "#ff7b72", "#7ee787", "#ffa657",
  ];
  const state = { dashboard: null, graph: null, themeColors: {}, charts: {} };

  // ----------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };

  function fmt(n) {
    if (n == null) return "—";
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
    return String(n);
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function deltaHtml(d) {
    if (d == null || d === 0) return '<span class="delta flat">— 0</span>';
    const cls = d > 0 ? "up" : "down";
    const arr = d > 0 ? "▲" : "▼";
    return `<span class="delta ${cls}">${arr} ${fmt(Math.abs(d))}</span>`;
  }
  function colorForTheme(theme) {
    return state.themeColors[theme] || "#8b98a5";
  }

  // ------------------------------------------------------------------- load
  async function load() {
    try {
      const [d, g] = await Promise.all([
        fetch("data/dashboard.json").then((r) => r.json()),
        fetch("data/graph.json").then((r) => r.json()).catch(() => null),
      ]);
      state.dashboard = d;
      state.graph = g;
      d.themes.forEach((t, i) => { state.themeColors[t.name] = PALETTE[i % PALETTE.length]; });
      render();
    } catch (e) {
      $("#meta-line").textContent = "Не удалось загрузить data/dashboard.json — запустите scripts/process.py.";
      console.error(e);
    }
  }

  // ----------------------------------------------------------------- render
  function render() {
    const d = state.dashboard;
    $("#meta-line").textContent =
      `Обновлено: ${d.generated_at} · ${d.totals.videos} видео · период ${d.timeline[0]?.date || "—"} → ${d.timeline.at(-1)?.date || "—"}`;
    renderStats(d.totals);
    renderOverview(d);
    renderVideos(d);
    renderAuthors(d);
    renderThemes(d);
    setupTabs();
    if (state.graph && window.GraphView) {
      GraphView.init({
        canvas: $("#graph-canvas"),
        data: state.graph,
        colorForTheme,
        onSelect: renderNodeDetail,
      });
      renderGraphLegend();
      $("#graph-meta").textContent =
        `${state.graph.stats.nodes} узлов · ${state.graph.stats.edges} связей · бэкенд: ${state.graph.backend}`;
    }
  }

  function renderStats(t) {
    const row = $("#stats-row");
    const cards = [
      ["Видео", t.videos], ["Авторы", t.authors], ["Темы", t.themes],
      ["Просмотры", fmt(t.views)], ["Лайки", fmt(t.likes)], ["Комментарии", fmt(t.comments)],
    ];
    row.innerHTML = "";
    cards.forEach(([label, val]) => {
      const c = el("div", "stat-card");
      c.append(el("div", "value", esc(val)), el("div", "label", label));
      row.append(c);
    });
  }

  // ---------------------------------------------------------------- overview
  function renderOverview(d) {
    const tl = d.timeline;
    makeChart("timeline-chart", {
      type: "line",
      data: {
        labels: tl.map((p) => p.date),
        datasets: [
          lineDS("Просмотры", tl.map((p) => p.views), "#58a6ff"),
          lineDS("Лайки", tl.map((p) => p.likes), "#3fb950"),
          lineDS("Комментарии", tl.map((p) => p.comments), "#bc8cff"),
        ],
      },
      options: baseLineOpts(),
    });

    const tr = $("#trending-list");
    tr.innerHTML = "";
    if (!d.trending.length) tr.append(el("li", "", '<span class="hint">Недостаточно истории для расчёта роста.</span>'));
    d.trending.forEach((v) => {
      const li = el("li");
      li.append(
        el("span", "t-title", `${esc(v.title)} <span class="hint">· ${esc(v.author)}</span>`),
        el("span", "t-delta", "▲ " + fmt(v.delta_views))
      );
      tr.append(li);
    });

    barFromGroups("overview-authors-chart", d.authors.slice(0, 8), (g) => "#58a6ff");
    barFromGroups("overview-themes-chart", d.themes, (g) => colorForTheme(g.name));
  }

  // ------------------------------------------------------------------ videos
  function renderVideos(d) {
    const aSel = $("#filter-author"), tSel = $("#filter-theme");
    d.authors.forEach((a) => aSel.append(new Option(a.name, a.name)));
    d.themes.forEach((t) => tSel.append(new Option(t.name, t.name)));
    ["#video-search", "#filter-author", "#filter-theme", "#sort-by"].forEach((s) =>
      $(s).addEventListener("input", drawCards));
    drawCards();
  }

  function drawCards() {
    const d = state.dashboard;
    const q = $("#video-search").value.trim().toLowerCase();
    const fa = $("#filter-author").value, ft = $("#filter-theme").value;
    const sort = $("#sort-by").value;

    let vids = d.videos.filter((v) => {
      if (fa && v.author !== fa) return false;
      if (ft && v.topic !== ft) return false;
      if (q) {
        const hay = (v.title + " " + v.author + " " + v.topic + " " +
          (v.tags || []).join(" ") + " " + (v.ideas || []).join(" ")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const sorters = {
      views: (a, b) => (b.metrics.views || 0) - (a.metrics.views || 0),
      delta: (a, b) => (b.deltas.views || 0) - (a.deltas.views || 0),
      engagement: (a, b) => b.engagement - a.engagement,
      recent: (a, b) => String(b.published_at || "").localeCompare(String(a.published_at || "")),
    };
    vids.sort(sorters[sort] || sorters.views);

    const wrap = $("#cards");
    wrap.innerHTML = "";
    if (!vids.length) { wrap.append(el("div", "empty", "Ничего не найдено.")); return; }

    vids.forEach((v) => wrap.append(buildCard(v)));
  }

  function buildCard(v) {
    const card = el("div", "card");
    const tColor = colorForTheme(v.topic);
    card.append(el("div", "card-title", esc(v.title)));

    const badges = el("div", "badges");
    const tb = el("span", "badge theme", esc(v.topic));
    tb.style.background = tColor;
    badges.append(tb, el("span", "badge", esc(v.author)));
    (v.tags || []).slice(0, 3).forEach((t) => badges.append(el("span", "badge", "#" + esc(t))));
    card.append(badges);

    const metrics = el("div", "metrics");
    [["views", "Просмотры"], ["likes", "Лайки"], ["comments", "Комменты"]].forEach(([k, lbl]) => {
      const m = el("div", "metric");
      m.append(
        el("span", "m-value", fmt(v.metrics[k] || 0)),
        el("span", "m-label", lbl),
        el("span", "", deltaHtml(v.deltas[k]))
      );
      metrics.append(m);
    });
    card.append(metrics);

    const spark = el("div", "spark");
    const cv = el("canvas");
    spark.append(cv);
    card.append(spark);
    queueMicrotask(() => sparkline(cv, v.series.views, tColor));

    if (v.ideas && v.ideas.length) {
      const ul = el("ul", "ideas");
      v.ideas.slice(0, 4).forEach((i) => ul.append(el("li", "", esc(i))));
      card.append(ul);
    }

    const foot = el("div", "card-foot");
    const seen = `${v.snapshots_count} замер(ов)`;
    foot.append(
      el("span", "", esc(seen)),
      v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">Открыть ↗</a>` : el("span")
    );
    card.append(foot);
    return card;
  }

  // ----------------------------------------------------------------- authors
  function renderAuthors(d) {
    makeChart("authors-chart", {
      type: "bar",
      data: {
        labels: d.authors.map((a) => a.name),
        datasets: [
          { label: "Просмотры", data: d.authors.map((a) => a.views), backgroundColor: "#58a6ff" },
          { label: "Лайки", data: d.authors.map((a) => a.likes), backgroundColor: "#3fb950" },
        ],
      },
      options: baseBarOpts(),
    });
    fillGroupTable("#authors-table tbody", d.authors);
  }

  function renderThemes(d) {
    makeChart("themes-chart", {
      type: "doughnut",
      data: {
        labels: d.themes.map((t) => t.name),
        datasets: [{ data: d.themes.map((t) => t.views),
          backgroundColor: d.themes.map((t) => colorForTheme(t.name)), borderColor: "#0d1117", borderWidth: 2 }],
      },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#9aa7b4" } } } },
    });
    fillGroupTable("#themes-table tbody", d.themes);
  }

  function fillGroupTable(sel, groups) {
    const tb = $(sel);
    tb.innerHTML = "";
    groups.forEach((g) => {
      const tr = el("tr");
      tr.innerHTML =
        `<td>${esc(g.name)}</td><td>${g.video_count}</td><td>${fmt(g.views)}</td>` +
        `<td>${fmt(g.likes)}</td><td>${fmt(g.comments)}</td>` +
        `<td class="members">${esc((g.members || []).join(", "))}</td>`;
      tb.append(tr);
    });
  }

  // ------------------------------------------------------------------- graph
  function renderGraphLegend() {
    const leg = $("#graph-legend");
    leg.innerHTML = "";
    state.dashboard.themes.forEach((t) => {
      const item = el("div", "legend-item");
      const dot = el("span", "legend-dot");
      dot.style.background = colorForTheme(t.name);
      item.append(dot, el("span", "", `${esc(t.name)} (${t.video_count})`));
      leg.append(item);
    });
  }

  function renderNodeDetail(node) {
    const box = $("#node-detail");
    if (!node) {
      box.innerHTML = '<p class="hint">Наведите или кликните узел, чтобы увидеть идеи видео и его связи.</p>';
      return;
    }
    const neighbours = GraphView.neighboursOf(node.id);
    box.innerHTML =
      `<div class="nd-title">${esc(node.label)}</div>` +
      `<div class="nd-meta">${esc(node.author)} · ${esc(node.topic)} · ${fmt(node.views)} просмотров` +
      (node.url ? ` · <a href="${esc(node.url)}" target="_blank" rel="noopener">видео ↗</a>` : "") + `</div>` +
      (node.ideas && node.ideas.length
        ? `<ul>${node.ideas.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "") +
      (neighbours.length
        ? `<div class="neighbours"><strong>Связанные идеи:</strong>` +
          neighbours.map((n) =>
            `<a href="#" data-node="${esc(n.node.id)}">${esc(n.node.label)} <span class="hint">(${n.type === "tag" ? "теги" : "смысл"} ${n.weight.toFixed(2)})</span></a>`).join("") +
          `</div>`
        : `<div class="neighbours hint">Прямых связей не найдено.</div>`);
    $$("a[data-node]", box).forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); GraphView.focusNode(a.dataset.node); }));
  }

  // ------------------------------------------------------------- chart utils
  function makeChart(id, cfg) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    if (state.charts[id]) state.charts[id].destroy();
    state.charts[id] = new Chart(ctx, cfg);
  }
  function lineDS(label, data, color) {
    return { label, data, borderColor: color, backgroundColor: color + "33",
      tension: 0.3, fill: true, pointRadius: 3, borderWidth: 2 };
  }
  function baseLineOpts() {
    return { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#9aa7b4" } } },
      scales: gridScales() };
  }
  function baseBarOpts() {
    return { responsive: true, maintainAspectRatio: false, indexAxis: "y",
      plugins: { legend: { labels: { color: "#9aa7b4" } } }, scales: gridScales() };
  }
  function gridScales() {
    const g = { grid: { color: "#2a3340" }, ticks: { color: "#9aa7b4" } };
    return { x: g, y: g };
  }
  function barFromGroups(id, groups, colorFn) {
    makeChart(id, {
      type: "bar",
      data: { labels: groups.map((g) => g.name),
        datasets: [{ label: "Просмотры", data: groups.map((g) => g.views),
          backgroundColor: groups.map(colorFn) }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } }, scales: gridScales() },
    });
  }
  function sparkline(canvas, series, color) {
    new Chart(canvas, {
      type: "line",
      data: { labels: series.map((p) => p.date),
        datasets: [{ data: series.map((p) => p.value), borderColor: color,
          backgroundColor: color + "22", fill: true, tension: 0.35,
          pointRadius: 0, borderWidth: 2 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true } },
        scales: { x: { display: false }, y: { display: false } } },
    });
  }

  // -------------------------------------------------------------------- tabs
  function setupTabs() {
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".view").forEach((v) => v.classList.remove("active"));
      tab.classList.add("active");
      $("#view-" + tab.dataset.view).classList.add("active");
      if (tab.dataset.view === "graph" && window.GraphView) GraphView.resize();
    }));
  }

  document.addEventListener("DOMContentLoaded", load);
})();

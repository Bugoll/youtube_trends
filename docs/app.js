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
    const reportDates = new Set(d.report_dates || []);

    const hint = $("#timeline-hint");
    if (hint) hint.hidden = tl.length === 0;

    // Dates with real metrics (latest snapshot per video attributed to first_seen date).
    const metricTl = tl.filter((p) => p.views != null);

    function tlOnClick(src) {
      return (evt, elements) => {
        if (!elements.length) return;
        const date = src[elements[0].index]?.date;
        if (date) openReport(date, reportDates);
      };
    }
    function xTicks(src) {
      return {
        color: (ctx) => reportDates.has(src[ctx.index]?.date) ? "#58a6ff" : "#9aa7b4",
        font:  (ctx) => reportDates.has(src[ctx.index]?.date) ? { weight: "bold" } : {},
        maxTicksLimit: 14,
      };
    }
    function tlOpts(src, titleText, titleColor) {
      const o = baseLineOpts();
      o.onClick = tlOnClick(src);
      o.scales = {
        ...gridScales(),
        x: { ticks: xTicks(src), grid: { color: "#2a3340" } },
        y: {
          ...gridScales().y,
          title: { display: true, text: titleText, color: titleColor, font: { size: 11 } },
        },
      };
      return o;
    }

    // Chart 1: Total views per cohort date
    makeChart("timeline-views-chart", {
      type: "bar",
      data: {
        labels: metricTl.map((p) => p.date),
        datasets: [{
          label: "Просмотры",
          data: metricTl.map((p) => p.views),
          backgroundColor: "#58a6ff55",
          borderColor: "#58a6ff",
          borderWidth: 1,
        }],
      },
      options: tlOpts(metricTl, "Просмотры", "#58a6ff"),
    });

    // Chart 2: Likes + comments per cohort date
    makeChart("timeline-engagement-chart", {
      type: "bar",
      data: {
        labels: metricTl.map((p) => p.date),
        datasets: [
          {
            label: "Лайки",
            data: metricTl.map((p) => p.likes),
            backgroundColor: "#3fb95055",
            borderColor: "#3fb950",
            borderWidth: 1,
          },
          {
            label: "Комментарии",
            data: metricTl.map((p) => p.comments),
            backgroundColor: "#bc8cff55",
            borderColor: "#bc8cff",
            borderWidth: 1,
          },
        ],
      },
      options: tlOpts(metricTl, "Лайки / Комментарии", "#9aa7b4"),
    });

    // Chart 3: New videos per day
    makeChart("timeline-videos-chart", {
      type: "bar",
      data: {
        labels: tl.map((p) => p.date),
        datasets: [{
          label: "Новых видео",
          data: tl.map((p) => p.new_videos || 0),
          backgroundColor: "#d2992233",
          borderColor: "#d29922",
          borderWidth: 1,
        }],
      },
      options: tlOpts(tl, "Новых видео", "#d29922"),
    });

    $("#report-close")?.addEventListener("click", closeReport);

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

  // --------------------------------------------------------------- reports
  function openReport(date, reportDates) {
    const panel = $("#report-panel");
    const body  = $("#report-body");
    const title = $("#report-title");
    if (!panel) return;

    if (!reportDates.has(date)) {
      body.innerHTML = `<p class="hint">Репорт за ${esc(date)} недоступен.</p>`;
      title.textContent = `Репорт — ${date}`;
      panel.hidden = false;
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }

    title.textContent = `Репорт — ${date}`;
    body.innerHTML = `<p class="hint">Загрузка…</p>`;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    fetch(`data/reports/${date}.md`)
      .then((r) => r.text())
      .then((md) => { body.innerHTML = renderMd(md); })
      .catch(() => { body.innerHTML = `<p class="hint">Не удалось загрузить репорт.</p>`; });
  }

  function closeReport() {
    const panel = $("#report-panel");
    if (panel) panel.hidden = true;
  }

  function renderMd(md) {
    const lines = md.split("\n");
    let html = "", inList = false;
    for (let raw of lines) {
      const line = raw.trimEnd();
      if (/^#{1}\s/.test(line)) {
        if (inList) { html += "</ul>"; inList = false; }
        html += `<h3>${esc(line.replace(/^#+\s*/, ""))}</h3>`;
      } else if (/^#{2,3}\s/.test(line)) {
        if (inList) { html += "</ul>"; inList = false; }
        html += `<h4>${esc(line.replace(/^#+\s*/, ""))}</h4>`;
      } else if (/^#{4,}\s/.test(line)) {
        if (inList) { html += "</ul>"; inList = false; }
        html += `<h5>${esc(line.replace(/^#+\s*/, ""))}</h5>`;
      } else if (/^[-*+]\s/.test(line)) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inlinesMd(line.replace(/^[-*+]\s*/, ""))}</li>`;
      } else if (/^\d+\.\s/.test(line)) {
        if (!inList) { html += "<ol>"; inList = "ol"; }
        html += `<li>${inlinesMd(line.replace(/^\d+\.\s*/, ""))}</li>`;
      } else if (/^---+$/.test(line.trim())) {
        if (inList) { html += inList === "ol" ? "</ol>" : "</ul>"; inList = false; }
        html += "<hr>";
      } else if (line === "") {
        if (inList) { html += inList === "ol" ? "</ol>" : "</ul>"; inList = false; }
      } else {
        if (inList) { html += inList === "ol" ? "</ol>" : "</ul>"; inList = false; }
        html += `<p>${inlinesMd(line)}</p>`;
      }
    }
    if (inList) html += inList === "ol" ? "</ol>" : "</ul>";
    return html;
  }

  function inlinesMd(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
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

  function hasContent(v) {
    return !!(v.summary || (v.key_points && v.key_points.length) ||
              (v.novel_ideas && v.novel_ideas.length) ||
              (v.speaker_claims && v.speaker_claims.length) ||
              (v.ideas && v.ideas.length));
  }

  function buildCard(v) {
    const card = el("div", "card");
    const tColor = colorForTheme(v.topic);
    card.append(el("div", "card-title", esc(v.title)));

    const badges = el("div", "badges");
    badges.append(el("span", "badge author", esc(v.author)));
    const displayDate = v.published_at || v.first_seen;
    if (displayDate) badges.append(el("span", "badge date", "📅 " + esc(displayDate)));
    const statusMap = {
      available:    ["badge status-available", "доступно",  "Видео есть в последнем пакете"],
      deleted:      ["badge status-deleted",   "удалено",   "Видео исчезло с канала (автор удалил)"],
      unsubscribed: ["badge status-unsub",     "отписан",   "Канал исключён из подписки — последние известные данные"],
    };
    const [sCls, sLabel, sTitle] = statusMap[v.status] || statusMap.available;
    const sb = el("span", sCls, sLabel);
    sb.title = sTitle;
    badges.append(sb);
    if (!hasContent(v)) {
      const wb = el("span", "badge status-warn", "⚠ нет данных");
      wb.title = "В пакете отсутствуют summary, key_points, novel_ideas и speaker_claims";
      badges.append(wb);
    }
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
    foot.append(el("span", "", esc(seen)));
    if (v.url) {
      const a = document.createElement("a");
      a.href = v.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Открыть ↗";
      foot.append(a);
    }
    card.append(foot);
    return card;
  }

  // ----------------------------------------------------------------- authors
  function renderAuthors(d) {
    makeChart("authors-chart", {
      type: "bar",
      data: {
        labels: d.authors.slice(0, 20).map((a) => a.name),
        datasets: [
          { label: "Просмотры", data: d.authors.slice(0, 20).map((a) => a.views), backgroundColor: "#58a6ff" },
          { label: "Лайки", data: d.authors.slice(0, 20).map((a) => a.likes), backgroundColor: "#3fb950" },
        ],
      },
      options: baseBarOpts(),
    });
    buildAuthorsDetailTable(d.videos);
  }

  function buildAuthorsDetailTable(videos) {
    const table = document.getElementById("authors-detail-table");
    if (!table) return;

    let sortCol = "author", sortDir = 1;

    function rows(list) {
      const tbody = table.querySelector("tbody");
      tbody.innerHTML = "";
      list.forEach((v) => {
        const date = v.published_at || v.first_seen || "";
        const titleHtml = v.url
          ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title)}</a>`
          : esc(v.title);

        // expandable detail block
        const hasDetail = (v.key_points && v.key_points.length) ||
                          (v.novel_ideas && v.novel_ideas.length) ||
                          (v.speaker_claims && v.speaker_claims.length);

        const summaryText = hasContent(v)
          ? esc(v.summary || "")
          : `<span class="badge status-warn" title="В пакете отсутствуют summary, key_points, novel_ideas и speaker_claims">⚠ нет данных в пакете</span>`;
        const detailHtml = hasDetail ? `
          <div class="detail-block" hidden>
            ${listSection("Ключевые тезисы", v.key_points)}
            ${listSection("Новые идеи", v.novel_ideas)}
            ${listSection("Утверждения спикера", v.speaker_claims)}
          </div>` : "";

        const toggleBtn = hasDetail
          ? `<button class="detail-toggle" aria-expanded="false">▶</button> ` : "";

        const statusCfg = {
          available:    ["status-available", "доступно",  "Видео есть в последнем пакете"],
          deleted:      ["status-deleted",   "удалено",   "Видео исчезло с канала (автор удалил)"],
          unsubscribed: ["status-unsub",     "отписан",   "Канал исключён из подписки"],
        };
        const [sCls2, sLbl2, sTip2] = statusCfg[v.status] || statusCfg.available;
        const statusHtml = `<span class="badge ${sCls2}" title="${esc(sTip2)}">${esc(sLbl2)}</span>`;

        const tr = document.createElement("tr");
        tr.innerHTML =
          `<td>${esc(v.author)}</td>` +
          `<td>${titleHtml}</td>` +
          `<td style="white-space:nowrap">${esc(date)}</td>` +
          `<td class="summary-cell">${toggleBtn}${summaryText}${detailHtml}</td>` +
          `<td style="white-space:nowrap">${statusHtml}</td>`;

        if (hasDetail) {
          const btn = tr.querySelector(".detail-toggle");
          const block = tr.querySelector(".detail-block");
          btn.addEventListener("click", () => {
            const open = !block.hidden;
            block.hidden = open;
            btn.setAttribute("aria-expanded", String(!open));
            btn.textContent = open ? "▶" : "▼";
          });
        }
        tbody.append(tr);
      });
    }

    function listSection(title, items) {
      if (!items || !items.length) return "";
      const lis = items.map((i) => `<li>${esc(i)}</li>`).join("");
      return `<div class="detail-section"><strong>${esc(title)}</strong><ul>${lis}</ul></div>`;
    }

    function sorted(list) {
      return [...list].sort((a, b) => {
        let va, vb;
        if (sortCol === "date") {
          va = a.published_at || a.first_seen || "";
          vb = b.published_at || b.first_seen || "";
        } else {
          va = a[sortCol] || "";
          vb = b[sortCol] || "";
        }
        return sortDir * String(va).localeCompare(String(vb), "ru");
      });
    }

    function redraw(list) {
      rows(sorted(list));
      table.querySelectorAll("thead th").forEach((th) => {
        th.removeAttribute("data-sort");
        if (th.dataset.col === sortCol) th.setAttribute("data-sort", sortDir > 0 ? "asc" : "desc");
      });
    }

    let current = videos;

    // search
    const search = document.getElementById("authors-search");
    if (search) {
      search.addEventListener("input", () => {
        const q = search.value.trim().toLowerCase();
        current = q
          ? videos.filter((v) =>
              (v.author + " " + v.title + " " + v.topic).toLowerCase().includes(q))
          : videos;
        redraw(current);
      });
    }

    // sort on header click
    table.querySelectorAll("thead th").forEach((th) => {
      th.addEventListener("click", () => {
        if (sortCol === th.dataset.col) sortDir *= -1;
        else { sortCol = th.dataset.col; sortDir = 1; }
        redraw(current);
      });
    });

    redraw(current);
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
    makeSortable("themes-table", d.themes);
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

  // Numeric columns by index (0=name/string, 1..4=numbers, 5=string).
  const _SORT_KEYS = [null, "video_count", "views", "likes", "comments", null];

  function makeSortable(tableId, groups) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const ths = Array.from(table.querySelectorAll("thead th"));
    let col = 2, dir = -1; // default: views desc

    function redraw() {
      const key = _SORT_KEYS[col];
      const sorted = key
        ? [...groups].sort((a, b) => dir * ((b[key] || 0) - (a[key] || 0)))
        : [...groups].sort((a, b) => dir * String(a.name).localeCompare(String(b.name)));
      fillGroupTable(`#${tableId} tbody`, sorted);
      ths.forEach((th, i) => {
        th.removeAttribute("data-sort");
        if (i === col) th.setAttribute("data-sort", dir < 0 ? "desc" : "asc");
      });
    }

    ths.forEach((th, i) => {
      if (_SORT_KEYS[i] !== undefined) {
        th.addEventListener("click", () => {
          if (col === i) dir *= -1; else { col = i; dir = -1; }
          redraw();
        });
      }
    });

    redraw();
  }

  // ------------------------------------------------------------------- graph
  const GRAPH_THEME_COLORS = {
    "AI": "#58a6ff", "Финансы": "#3fb950", "Геополитика": "#f85149",
    "Психология": "#bc8cff", "Бизнес": "#ffa657", "Наука": "#39c5cf",
    "Технологии": "#26c7d0", "Личностный рост": "#d29922", "Разное": "#8b98a5",
  };

  function renderGraphLegend() {
    const leg = $("#graph-legend");
    if (!leg || !state.graph) return;
    leg.innerHTML = "";

    // --- Themes ---
    const themeNodes = state.graph.nodes.filter(n => n.type === "theme")
      .sort((a, b) => b.count - a.count);
    themeNodes.forEach(n => {
      const color = GRAPH_THEME_COLORS[n.label] || "#8b98a5";
      const item  = el("div", "legend-item");
      item.style.cursor = "pointer";
      item.title = `${n.count} видео — кликните чтобы выделить`;
      const dot = el("span", "legend-dot");
      dot.style.background = color;
      item.append(dot, el("span", "", `${esc(n.label)} (${n.count})`));
      item.addEventListener("click", () => GraphView.focusNode(n.id));
      leg.append(item);
    });

    // --- Authors header ---
    if (state.graph.nodes.some(n => n.type === "author")) {
      const h = el("h3", "", "Каналы");
      h.style.cssText = "margin:14px 0 6px; font-size:13px; color:var(--text-dim)";
      leg.append(h);

      const authorNodes = state.graph.nodes.filter(n => n.type === "author")
        .sort((a, b) => b.count - a.count);
      authorNodes.forEach(n => {
        const item = el("div", "legend-item");
        item.style.cursor = "pointer";
        item.title = `${n.count} видео — кликните чтобы выделить`;
        const dot = el("span", "legend-dot");
        dot.style.cssText = "background:#7aa2c8; border-radius:3px";
        item.append(dot, el("span", "", `${esc(n.label)} (${n.count})`));
        item.addEventListener("click", () => GraphView.focusNode(n.id));
        leg.append(item);
      });
    }
  }

  function renderNodeDetail(node) {
    const box = $("#node-detail");
    if (!node) {
      box.innerHTML = '<p class="hint">Кликните узел для фокуса — остальные потускнеют. Кликните снова чтобы сбросить.</p>';
      return;
    }

    // Hub: theme
    if (node.type === "theme") {
      const neighbours = GraphView.neighboursOf(node.id);
      box.innerHTML =
        `<div class="nd-title">🔷 ${esc(node.label)}</div>` +
        `<div class="nd-meta">${node.count} видео в этой теме</div>` +
        (neighbours.length
          ? `<div class="neighbours"><strong>Видео в теме:</strong>` +
            neighbours.slice(0, 12).map(n =>
              `<a href="#" data-node="${esc(n.node.id)}">${esc(n.node.label)}</a>`).join("") +
            (neighbours.length > 12 ? `<span class="hint"> ещё ${neighbours.length - 12}…</span>` : "") +
            `</div>` : "");
      $$("a[data-node]", box).forEach(a =>
        a.addEventListener("click", e => { e.preventDefault(); GraphView.focusNode(a.dataset.node); }));
      return;
    }

    // Hub: author
    if (node.type === "author") {
      const neighbours = GraphView.neighboursOf(node.id);
      box.innerHTML =
        `<div class="nd-title">👤 ${esc(node.label)}</div>` +
        `<div class="nd-meta">${node.count} видео на канале</div>` +
        (neighbours.length
          ? `<div class="neighbours"><strong>Видео автора:</strong>` +
            neighbours.slice(0, 12).map(n =>
              `<a href="#" data-node="${esc(n.node.id)}">${esc(n.node.label)}</a>`).join("") +
            (neighbours.length > 12 ? `<span class="hint"> ещё ${neighbours.length - 12}…</span>` : "") +
            `</div>` : "");
      $$("a[data-node]", box).forEach(a =>
        a.addEventListener("click", e => { e.preventDefault(); GraphView.focusNode(a.dataset.node); }));
      return;
    }

    // Video node
    const neighbours = GraphView.neighboursOf(node.id)
      .filter(n => n.node.type === "video");  // show only video neighbours, not hub back-links
    box.innerHTML =
      `<div class="nd-title">${esc(node.label)}</div>` +
      `<div class="nd-meta">${esc(node.author)} · ${fmt(node.views)} просмотров` +
      (node.url ? ` · <a href="${esc(node.url)}" target="_blank" rel="noopener">видео ↗</a>` : "") + `</div>` +
      (node.ideas && node.ideas.length
        ? `<ul>${node.ideas.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : "") +
      (neighbours.length
        ? `<div class="neighbours"><strong>Похожие видео:</strong>` +
          neighbours.slice(0, 8).map(n =>
            `<a href="#" data-node="${esc(n.node.id)}">${esc(n.node.label)} <span class="hint">(${n.type === "tag" ? "теги" : "смысл"})</span></a>`).join("") +
          `</div>`
        : `<div class="neighbours hint">Прямых связей не найдено.</div>`);
    $$("a[data-node]", box).forEach(a =>
      a.addEventListener("click", e => { e.preventDefault(); GraphView.focusNode(a.dataset.node); }));
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

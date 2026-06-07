/* Obsidian-style force-directed idea map (vanilla canvas, no dependencies).
 * Nodes = videos (colored by theme, sized by reach), edges = idea connections
 * (solid = semantic similarity, dashed = shared tags). Supports pan, zoom,
 * node drag, hover/click highlight and search-to-focus. */
window.GraphView = (function () {
  "use strict";

  let canvas, ctx, dpr = 1;
  let nodes = [], edges = [], byId = {};
  let colorForTheme = () => "#8b98a5";
  let onSelect = () => {};
  let view = { scale: 1, x: 0, y: 0 };
  let show = { semantic: true, tag: true };
  let hover = null, selected = null, dragging = null, panning = null;
  let alpha = 1, raf = null;

  function init(opts) {
    canvas = opts.canvas;
    ctx = canvas.getContext("2d");
    colorForTheme = opts.colorForTheme || colorForTheme;
    onSelect = opts.onSelect || onSelect;

    const maxViews = Math.max(1, ...opts.data.nodes.map((n) => n.views || 0));
    nodes = opts.data.nodes.map((n) => ({
      ...n,
      x: (Math.random() - 0.5) * 400,
      y: (Math.random() - 0.5) * 400,
      vx: 0, vy: 0,
      r: 6 + 16 * Math.sqrt((n.views || 0) / maxViews),
      color: colorForTheme(n.topic),
    }));
    byId = {};
    nodes.forEach((n) => (byId[n.id] = n));
    edges = opts.data.edges
      .map((e) => ({ ...e, s: byId[e.source], t: byId[e.target] }))
      .filter((e) => e.s && e.t);

    bindEvents();
    bindControls();
    resize();
    alpha = 1;
    loop();
    return api;
  }

  // ----------------------------------------------------------- force layout
  function step() {
    const k = 9000;             // repulsion strength
    const center = 0.015;       // pull toward origin
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const f = (k / d2) * alpha;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
      a.vx -= a.x * center * alpha;
      a.vy -= a.y * center * alpha;
    }
    for (const e of edges) {
      if (!visibleEdge(e)) continue;
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const target = 90 + (1 - (e.weight || 0.3)) * 120;
      const f = (d - target) * 0.02 * alpha;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      e.s.vx += fx; e.s.vy += fy; e.t.vx -= fx; e.t.vy -= fy;
    }
    for (const n of nodes) {
      if (n === dragging) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82;
      n.x += n.vx; n.y += n.vy;
    }
    alpha *= 0.995;
    if (alpha < 0.03) alpha = 0.03;
  }

  function visibleEdge(e) {
    return e.type === "tag" ? show.tag : show.semantic;
  }

  // -------------------------------------------------------------- rendering
  function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(w / 2 + view.x, h / 2 + view.y);
    ctx.scale(view.scale, view.scale);

    const hi = highlightSet();

    for (const e of edges) {
      if (!visibleEdge(e)) continue;
      const active = !hi || (hi.has(e.s.id) && hi.has(e.t.id));
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      ctx.strokeStyle = e.type === "tag"
        ? (active ? "rgba(210,153,34,0.55)" : "rgba(210,153,34,0.12)")
        : (active ? "rgba(120,160,200,0.7)" : "rgba(120,160,200,0.14)");
      ctx.lineWidth = (e.type === "tag" ? 1 : 1 + (e.weight || 0) * 2.5) / 1;
      ctx.setLineDash(e.type === "tag" ? [4, 4] : []);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const n of nodes) {
      const active = !hi || hi.has(n.id);
      ctx.globalAlpha = active ? 1 : 0.25;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();
      if (n === selected || n === hover) {
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = "#e6edf3";
        ctx.stroke();
      }
      if (active && (view.scale > 0.75 || n === hover || n === selected || n.r > 14)) {
        ctx.globalAlpha = active ? 1 : 0.3;
        ctx.fillStyle = "#e6edf3";
        ctx.font = "11px -apple-system, Segoe UI, sans-serif";
        ctx.textAlign = "center";
        const label = n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label;
        ctx.fillText(label, n.x, n.y + n.r + 12);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function highlightSet() {
    const focus = hover || selected;
    if (!focus) return null;
    const set = new Set([focus.id]);
    for (const e of edges) {
      if (!visibleEdge(e)) continue;
      if (e.s.id === focus.id) set.add(e.t.id);
      if (e.t.id === focus.id) set.add(e.s.id);
    }
    return set;
  }

  function loop() {
    step();
    draw();
    raf = requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ interaction
  function toWorld(px, py) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    return {
      x: (px - w / 2 - view.x) / view.scale,
      y: (py - h / 2 - view.y) / view.scale,
    };
  }
  function nodeAt(px, py) {
    const p = toWorld(px, py);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if ((n.x - p.x) ** 2 + (n.y - p.y) ** 2 <= (n.r + 4) ** 2) return n;
    }
    return null;
  }
  function relPos(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function bindEvents() {
    let movedWhileDown = false;
    canvas.addEventListener("mousedown", (ev) => {
      const p = relPos(ev);
      const n = nodeAt(p.x, p.y);
      movedWhileDown = false;
      if (n) { dragging = n; alpha = Math.max(alpha, 0.5); }
      else panning = { ...p, vx: view.x, vy: view.y };
    });
    window.addEventListener("mousemove", (ev) => {
      const p = relPos(ev);
      if (dragging) {
        const w = toWorld(p.x, p.y);
        dragging.x = w.x; dragging.y = w.y;
        movedWhileDown = true;
      } else if (panning) {
        view.x = panning.vx + (p.x - panning.x);
        view.y = panning.vy + (p.y - panning.y);
        movedWhileDown = true;
      } else {
        const n = nodeAt(p.x, p.y);
        if (n !== hover) { hover = n; onSelect(n); }
        canvas.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", (ev) => {
      if (dragging && !movedWhileDown) { selected = dragging; onSelect(selected); }
      else if (panning && !movedWhileDown) { selected = null; onSelect(null); }
      dragging = null; panning = null;
    });
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const p = relPos(ev);
      const before = toWorld(p.x, p.y);
      const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
      view.scale = Math.min(4, Math.max(0.2, view.scale * factor));
      const after = toWorld(p.x, p.y);
      view.x += (after.x - before.x) * view.scale;
      view.y += (after.y - before.y) * view.scale;
    }, { passive: false });
  }

  function bindControls() {
    const semantic = document.getElementById("toggle-semantic");
    const tag = document.getElementById("toggle-tag");
    const reset = document.getElementById("graph-reset");
    const search = document.getElementById("graph-search");
    if (semantic) semantic.addEventListener("change", () => { show.semantic = semantic.checked; alpha = 0.6; });
    if (tag) tag.addEventListener("change", () => { show.tag = tag.checked; alpha = 0.6; });
    if (reset) reset.addEventListener("click", () => { view = { scale: 1, x: 0, y: 0 }; selected = null; onSelect(null); alpha = 0.8; });
    if (search) search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      if (!q) { selected = null; onSelect(null); return; }
      const n = nodes.find((x) => x.label.toLowerCase().includes(q));
      if (n) focusNode(n.id);
    });
  }

  // -------------------------------------------------------------------- api
  function focusNode(id) {
    const n = byId[id];
    if (!n) return;
    selected = n;
    view.x = -n.x * view.scale;
    view.y = -n.y * view.scale;
    onSelect(n);
  }
  function neighboursOf(id) {
    const out = [];
    for (const e of edges) {
      if (!visibleEdge(e)) continue;
      if (e.s.id === id) out.push({ node: e.t, weight: e.weight || 0, type: e.type });
      else if (e.t.id === id) out.push({ node: e.s, weight: e.weight || 0, type: e.type });
    }
    return out.sort((a, b) => b.weight - a.weight);
  }
  function resize() {
    dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }

  const api = { init, focusNode, neighboursOf, resize };
  window.addEventListener("resize", () => canvas && resize());
  return api;
})();

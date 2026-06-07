/* Force-directed idea map with macro-theme + author hub nodes.
 * Hub nodes (theme / author) are large anchors; video nodes cluster around them.
 * Click any node to enter focus mode — unconnected nodes fade to near-invisible.
 * Click the same node again or the background to exit focus mode. */
window.GraphView = (function () {
  "use strict";

  // ----------------------------------------------------------------- colours
  const THEME_COLORS = {
    "AI":              "#58a6ff",
    "Финансы":         "#3fb950",
    "Геополитика":     "#f85149",
    "Психология":      "#bc8cff",
    "Бизнес":          "#ffa657",
    "Наука":           "#39c5cf",
    "Технологии":      "#26c7d0",
    "Личностный рост": "#d29922",
    "Разное":          "#8b98a5",
  };
  function themeColor(name) { return THEME_COLORS[name] || "#8b98a5"; }
  const AUTHOR_HUB_FILL   = "#1c2d40";
  const AUTHOR_HUB_BORDER = "#7aa2c8";

  // ------------------------------------------------------------------ state
  let canvas, ctx, dpr = 1;
  let nodes = [], edges = [], byId = {};
  let onSelectCb = () => {};
  let view = { scale: 0.12, x: 0, y: 0 };   // start zoomed out to see big-bang
  let show = { hub: true };
  let hover = null, focused = null, dragging = null, panning = null;
  let alpha = 1, raf = null, autoFitted = false;

  // ------------------------------------------------------------------- init
  function init(opts) {
    canvas     = opts.canvas;
    ctx        = canvas.getContext("2d");
    onSelectCb = opts.onSelect || onSelectCb;

    const data     = opts.data;
    const videoNodes = data.nodes.filter(n => n.type === "video");
    const maxViews   = Math.max(1, ...videoNodes.map(n => n.views || 0));

    // Big-bang: all nodes start at centre with a random outward velocity.
    // Hubs fly fastest → form the outer scaffold; videos follow via spring edges.
    // Velocity cap in step() prevents NaN from near-zero initial distances.
    nodes = data.nodes.map(n => {
      const angle = Math.random() * Math.PI * 2;
      let speed;
      if      (n.type === "theme")  speed = 30 + Math.random() * 20;
      else if (n.type === "author") speed = 18 + Math.random() * 14;
      else                          speed =  6 + Math.random() * 12;
      return {
        ...n,
        x:  (Math.random() - 0.5) * 4,
        y:  (Math.random() - 0.5) * 4,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        r: n.type === "theme"  ? 30 + Math.sqrt(n.count || 1) * 1.5
         : n.type === "author" ? 14 + Math.sqrt(n.count || 1)
         : 5 + 12 * Math.sqrt((n.views || 0) / maxViews),
        color: n.type === "theme"  ? themeColor(n.label)
             : n.type === "author" ? AUTHOR_HUB_FILL
             : themeColor(n.macro_theme),
      };
    });

    byId = {};
    nodes.forEach(n => (byId[n.id] = n));

    edges = data.edges
      .map(e => ({ ...e, s: byId[e.source], t: byId[e.target] }))
      .filter(e => e.s && e.t);

    view      = { scale: 0.05, x: 0, y: 0 };
    alpha     = 1;
    autoFitted = false;
    hover     = null;
    focused   = null;

    bindEvents();
    bindControls();
    resize();
    if (raf) cancelAnimationFrame(raf);
    loop();
    return api;
  }

  // Fit all nodes into the canvas with padding.
  function fitToScreen(animate) {
    if (!nodes.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x - n.r);
      maxX = Math.max(maxX, n.x + n.r);
      minY = Math.min(minY, n.y - n.r);
      maxY = Math.max(maxY, n.y + n.r);
    }
    const pad = 60;
    const w   = canvas.clientWidth, h = canvas.clientHeight;
    const s   = Math.min((w - pad * 2) / (maxX - minX), (h - pad * 2) / (maxY - minY), 2);
    const tx  = -((minX + maxX) / 2) * s;
    const ty  = -((minY + maxY) / 2) * s;
    if (animate) {
      // Smooth lerp over ~60 frames
      let t = 0;
      const s0 = view.scale, tx0 = view.x, ty0 = view.y;
      const lerp = () => {
        t += 0.03;
        const f = 1 - Math.pow(1 - Math.min(t, 1), 3);
        view.scale = s0 + (s - s0) * f;
        view.x     = tx0 + (tx - tx0) * f;
        view.y     = ty0 + (ty - ty0) * f;
        if (t < 1) requestAnimationFrame(lerp);
      };
      lerp();
    } else {
      view.scale = s; view.x = tx; view.y = ty;
    }
  }

  // ----------------------------------------------------------- force layout
  function isHub(n) { return n.type === "theme" || n.type === "author"; }

  function step() {
    // Phase 1 (alpha > 0.72): seeds fly outward, no repulsion, hub springs only.
    // Phase 2 (alpha ≤ 0.72): full physics — repulsion + all edge types.
    const phase1 = alpha > 0.72;

    // Node–node repulsion (phase 2 only)
    if (!phase1) {
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        const ka = isHub(a) ? 4 : 1;
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const kb = isHub(b) ? 4 : 1;
          let dx = a.x - b.x, dy = a.y - b.y;
          const d2 = dx * dx + dy * dy || 0.01;
          const k  = 9000 * ka * kb;
          const f  = (k / d2) * alpha;
          const d  = Math.sqrt(d2);
          const fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
        // Gentle pull toward origin (keeps graph centred)
        const gravity = isHub(a) ? 0.006 : 0.018;
        a.vx -= a.x * gravity * alpha;
        a.vy -= a.y * gravity * alpha;
      }
    }

    // Edge springs
    for (const e of edges) {
      if (!edgeVisible(e)) continue;
      // Phase 1: only hub springs active — videos get dragged by their flying hubs
      const isHubEdge = e.type === "hub_theme" || e.type === "hub_author";
      if (phase1 && !isHubEdge) continue;

      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      let target, k;
      if (e.type === "hub_theme") {
        target = 80;  k = 0.05;
      } else if (e.type === "hub_author") {
        target = 65;  k = 0.06;
      } else {
        target = 90 + (1 - (e.weight || 0.3)) * 120;
        k = 0.02;
      }
      const f  = (d - target) * k * alpha;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      e.s.vx += fx; e.s.vy += fy;
      e.t.vx -= fx; e.t.vy -= fy;
    }

    // Integrate — velocity cap prevents NaN when nodes start at same position
    const MAX_V = alpha > 0.5 ? 50 : 25;   // fast explosion, slower settle
    for (const n of nodes) {
      if (n === dragging) { n.vx = 0; n.vy = 0; continue; }
      const damp = isHub(n) ? 0.80 : 0.84;
      n.vx *= damp; n.vy *= damp;
      const spd = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (spd > MAX_V) { n.vx = n.vx / spd * MAX_V; n.vy = n.vy / spd * MAX_V; }
      n.x += n.vx; n.y += n.vy;
    }
    alpha *= 0.995;
    if (alpha < 0.03) alpha = 0.03;
  }

  function edgeVisible(e) {
    return show.hub;
  }

  // -------------------------------------------------------------- rendering
  function focusedSet() {
    if (!focused) return null;
    const set = new Set([focused.id]);
    for (const e of edges) {
      if (!edgeVisible(e)) continue;
      if (e.s.id === focused.id) set.add(e.t.id);
      if (e.t.id === focused.id) set.add(e.s.id);
    }
    return set;
  }

  function drawHexagon(x, y, r) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = (i * Math.PI) / 3 - Math.PI / 6;
      i === 0 ? ctx.moveTo(x + r * Math.cos(a), y + r * Math.sin(a))
              : ctx.lineTo(x + r * Math.cos(a), y + r * Math.sin(a));
    }
    ctx.closePath();
  }

  function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w * dpr, h * dpr);
    ctx.save();
    ctx.translate(w / 2 + view.x, h / 2 + view.y);
    ctx.scale(view.scale, view.scale);

    const hi = focusedSet();

    // --- Edges ---
    for (const e of edges) {
      if (!edgeVisible(e)) continue;
      const connected = !hi || (hi.has(e.s.id) && hi.has(e.t.id));
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);

      if (e.type === "hub_theme") {
        ctx.strokeStyle = connected
          ? e.t.color || e.s.color || "#58a6ff"
          : "rgba(255,255,255,0.04)";
        ctx.lineWidth = connected ? 1.2 : 0.5;
        ctx.setLineDash([]);
      } else if (e.type === "hub_author") {
        ctx.strokeStyle = connected
          ? "rgba(122,162,200,0.5)"
          : "rgba(122,162,200,0.05)";
        ctx.lineWidth = connected ? 1 : 0.5;
        ctx.setLineDash([3, 4]);
      } else if (e.type === "tag") {
        ctx.strokeStyle = connected
          ? "rgba(210,153,34,0.55)"
          : "rgba(210,153,34,0.06)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
      } else {
        ctx.strokeStyle = connected
          ? "rgba(120,160,200,0.7)"
          : "rgba(120,160,200,0.07)";
        ctx.lineWidth = 1 + (e.weight || 0) * 2;
        ctx.setLineDash([]);
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // --- Video nodes ---
    for (const n of nodes) {
      if (n.type !== "video") continue;
      const active = !hi || hi.has(n.id);
      ctx.globalAlpha = active ? 1 : 0.08;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();
      if (n === hover || n === focused) {
        ctx.lineWidth = 2; ctx.strokeStyle = "#e6edf3"; ctx.stroke();
      }
      if (active && (view.scale > 0.9 || n === hover || n === focused)) {
        ctx.globalAlpha = active ? 0.9 : 0.1;
        ctx.fillStyle = "#e6edf3";
        ctx.font = `10px -apple-system, sans-serif`;
        ctx.textAlign = "center";
        const lbl = n.label.length > 26 ? n.label.slice(0, 25) + "…" : n.label;
        ctx.fillText(lbl, n.x, n.y + n.r + 11);
      }
    }
    ctx.globalAlpha = 1;

    // --- Author hub nodes ---
    for (const n of nodes) {
      if (n.type !== "author") continue;
      const active = !hi || hi.has(n.id);
      ctx.globalAlpha = active ? 1 : 0.12;
      // Inner fill
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();
      // Border ring
      ctx.lineWidth = n === focused ? 3 : 2;
      ctx.strokeStyle = AUTHOR_HUB_BORDER;
      ctx.stroke();
      // Outer glow ring when selected
      if (n === focused || n === hover) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(122,162,200,0.5)";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      // Label always visible
      ctx.globalAlpha = active ? 1 : 0.15;
      ctx.fillStyle = "#c9d1d9";
      const fs = Math.max(10, Math.min(13, n.r * 0.65));
      ctx.font = `600 ${fs}px -apple-system, sans-serif`;
      ctx.textAlign = "center";
      const lbl = n.label.length > 18 ? n.label.slice(0, 17) + "…" : n.label;
      ctx.fillText(lbl, n.x, n.y + n.r + 14);
    }
    ctx.globalAlpha = 1;

    // --- Theme hub nodes (drawn on top) ---
    for (const n of nodes) {
      if (n.type !== "theme") continue;
      const active = !hi || hi.has(n.id);
      ctx.globalAlpha = active ? 1 : 0.15;

      // Glow
      const grd = ctx.createRadialGradient(n.x, n.y, n.r * 0.4, n.x, n.y, n.r * 2.2);
      grd.addColorStop(0, n.color + "44");
      grd.addColorStop(1, "transparent");
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r * 2.2, 0, Math.PI * 2);
      ctx.fillStyle = grd;
      ctx.fill();

      // Hexagon body
      drawHexagon(n.x, n.y, n.r);
      ctx.fillStyle = n.color + "33";
      ctx.fill();
      ctx.lineWidth = n === focused ? 3.5 : 2.5;
      ctx.strokeStyle = n.color;
      ctx.stroke();

      // Inner dot
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r * 0.28, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();

      // Focus ring
      if (n === focused || n === hover) {
        drawHexagon(n.x, n.y, n.r + 7);
        ctx.strokeStyle = n.color + "88";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Label always visible
      ctx.globalAlpha = active ? 1 : 0.18;
      ctx.fillStyle = "#e6edf3";
      const fs = Math.max(11, Math.min(15, n.r * 0.48));
      ctx.font = `700 ${fs}px -apple-system, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(n.label, n.x, n.y + n.r + 17);
      ctx.globalAlpha = active ? 0.6 : 0.1;
      ctx.font = `10px -apple-system, sans-serif`;
      ctx.fillStyle = "#9aa7b4";
      ctx.fillText(`${n.count} видео`, n.x, n.y + n.r + 29);
    }

    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function loop() {
    step();
    draw();
    // First fit: trigger after phase 2 is well underway (alpha < 0.6),
    // giving the big-bang animation more time to be visible before zooming.
    if (!autoFitted && alpha < 0.6) {
      autoFitted = true;
      fitToScreen(true);
    }
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
    // Check in reverse draw order (hubs on top)
    const sorted = [...nodes].sort((a, b) => {
      const rank = { theme: 2, author: 1, video: 0 };
      return (rank[b.type] || 0) - (rank[a.type] || 0);
    });
    for (const n of sorted) {
      if ((n.x - p.x) ** 2 + (n.y - p.y) ** 2 <= (n.r + 6) ** 2) return n;
    }
    return null;
  }
  function relPos(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function bindEvents() {
    let moved = false;
    canvas.addEventListener("mousedown", ev => {
      const p = relPos(ev);
      const n = nodeAt(p.x, p.y);
      moved = false;
      if (n) { dragging = n; alpha = Math.max(alpha, 0.5); }
      else panning = { ...p, vx: view.x, vy: view.y };
    });
    window.addEventListener("mousemove", ev => {
      const p = relPos(ev);
      if (dragging) {
        const w = toWorld(p.x, p.y);
        dragging.x = w.x; dragging.y = w.y; moved = true;
      } else if (panning) {
        view.x = panning.vx + (p.x - panning.x);
        view.y = panning.vy + (p.y - panning.y);
        moved = true;
      } else {
        const n = nodeAt(p.x, p.y);
        if (n !== hover) { hover = n; if (!focused) onSelectCb(n); }
        canvas.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", ev => {
      if (!moved) {
        if (dragging) {
          // Click toggles focus
          focused = (focused === dragging) ? null : dragging;
          onSelectCb(focused || hover);
        } else if (panning) {
          focused = null;
          onSelectCb(null);
        }
      }
      dragging = null; panning = null; moved = false;
    });
    canvas.addEventListener("wheel", ev => {
      ev.preventDefault();
      const p = relPos(ev);
      const before = toWorld(p.x, p.y);
      view.scale = Math.min(5, Math.max(0.15, view.scale * (ev.deltaY < 0 ? 1.1 : 1 / 1.1)));
      const after = toWorld(p.x, p.y);
      view.x += (after.x - before.x) * view.scale;
      view.y += (after.y - before.y) * view.scale;
    }, { passive: false });

    // Touch support
    let touchPan = null, touchPinch = null;
    const tDist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    const tMid  = (t, r) => ({ x: (t[0].clientX + t[1].clientX) / 2 - r.left, y: (t[0].clientY + t[1].clientY) / 2 - r.top });
    canvas.addEventListener("touchstart", ev => {
      ev.preventDefault();
      const r = canvas.getBoundingClientRect();
      if (ev.touches.length === 1) {
        const t = ev.touches[0];
        const p = { x: t.clientX - r.left, y: t.clientY - r.top };
        const n = nodeAt(p.x, p.y);
        if (n) { dragging = n; alpha = Math.max(alpha, 0.5); touchPan = null; }
        else touchPan = { x: t.clientX, y: t.clientY, vx: view.x, vy: view.y };
      } else if (ev.touches.length === 2) {
        dragging = null; touchPan = null;
        touchPinch = { dist: tDist(ev.touches), scale: view.scale, mid: tMid(ev.touches, r) };
      }
    }, { passive: false });
    canvas.addEventListener("touchmove", ev => {
      ev.preventDefault();
      const r = canvas.getBoundingClientRect();
      if (ev.touches.length === 2 && touchPinch) {
        const d = tDist(ev.touches);
        view.scale = Math.min(5, Math.max(0.15, touchPinch.scale * d / touchPinch.dist));
        const before = toWorld(touchPinch.mid.x, touchPinch.mid.y);
        const after  = toWorld(touchPinch.mid.x, touchPinch.mid.y);
        view.x += (after.x - before.x) * view.scale;
        view.y += (after.y - before.y) * view.scale;
      } else if (ev.touches.length === 1) {
        const t = ev.touches[0];
        if (dragging) {
          const p = toWorld(t.clientX - r.left, t.clientY - r.top);
          dragging.x = p.x; dragging.y = p.y;
        } else if (touchPan) {
          view.x = touchPan.vx + (t.clientX - touchPan.x);
          view.y = touchPan.vy + (t.clientY - touchPan.y);
        }
      }
    }, { passive: false });
    canvas.addEventListener("touchend", ev => {
      if (ev.touches.length === 0) {
        if (dragging) { focused = (focused === dragging) ? null : dragging; onSelectCb(focused); }
        dragging = null; touchPan = null; touchPinch = null;
      } else if (ev.touches.length === 1) { touchPinch = null; }
    }, { passive: false });
  }

  function bindControls() {
    const hub   = document.getElementById("toggle-hub");
    const reset = document.getElementById("graph-reset");
    const srch  = document.getElementById("graph-search");
    if (hub)   hub.addEventListener("change",  () => { show.hub = hub.checked; alpha = 0.6; });
    if (reset) reset.addEventListener("click", () => { focused = null; onSelectCb(null); fitToScreen(true); alpha = 0.3; });
    if (srch)  srch.addEventListener("input",  () => {
      const q = srch.value.trim().toLowerCase();
      if (!q) { focused = null; onSelectCb(null); return; }
      const n = nodes.find(x => x.label.toLowerCase().includes(q));
      if (n) focusNode(n.id);
    });
  }

  // -------------------------------------------------------------------- api
  function focusNode(id) {
    const n = byId[id];
    if (!n) return;
    focused = n;
    view.x = -n.x * view.scale;
    view.y = -n.y * view.scale;
    onSelectCb(n);
  }
  function neighboursOf(id) {
    const out = [];
    for (const e of edges) {
      if (!edgeVisible(e)) continue;
      if (e.s.id === id) out.push({ node: e.t, weight: e.weight || 0, type: e.type });
      else if (e.t.id === id) out.push({ node: e.s, weight: e.weight || 0, type: e.type });
    }
    return out.sort((a, b) => b.weight - a.weight);
  }
  function themeColorPublic(name) { return themeColor(name); }
  function resize() {
    dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
  }

  const api = { init, focusNode, neighboursOf, resize, fitToScreen, themeColor: themeColorPublic };
  window.addEventListener("resize", () => canvas && resize());
  return api;
})();

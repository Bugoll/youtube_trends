#!/usr/bin/env python3
"""Build the dashboard datasets from copied ``daily_package`` files.

Pipeline:
    raw packages (.json/.md)  ->  normalized records
                              ->  per-video metric history (time series)
                              ->  dashboard.json  (cards, authors, themes, timeline)
                              ->  graph.json       (Obsidian-style idea map)

The script is idempotent: re-running it for the same day overwrites that day's
snapshot rather than duplicating it, so the daily GitHub Action can run safely
as many times as needed.

Usage:
    python scripts/process.py --raw data/raw --out docs/data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import normalize  # noqa: E402

ENGAGEMENT_KEYS = ("likes", "comments", "shares")


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# History (per-video time series of metrics)
# --------------------------------------------------------------------------- #
def update_history(history: dict[str, Any], records: list[dict]) -> dict[str, Any]:
    """Fold today's records into the long-lived per-video history store.

    Status rules (derived entirely from the daily_package data):
      "available"    — video present in the latest package
      "deleted"      — video absent, but its author/channel IS present → author deleted it
      "unsubscribed" — video absent AND no videos from its author in the latest package
                       → you unsubscribed from that channel
    """
    if not records:
        return history

    latest_date = max(r["captured_at"] for r in records)
    latest_records = [r for r in records if r["captured_at"] == latest_date]
    latest_video_ids: set[str] = {r["video_id"] for r in latest_records}
    latest_authors: set[str] = {r["author"] for r in latest_records if r.get("author")}

    for rec in records:
        vid = rec["video_id"]
        entry = history.get(vid)
        if entry is None:
            entry = {
                "video_id": vid,
                "title": rec["title"],
                "author": rec["author"],
                "topic": rec["topic"],
                "url": rec["url"],
                "tags": rec["tags"],
                "ideas": rec["ideas"],
                "summary": rec["summary"],
                "key_points": rec.get("key_points", []),
                "novel_ideas": rec.get("novel_ideas", []),
                "speaker_claims": rec.get("speaker_claims", []),
                "published_at": rec["published_at"],
                "first_seen": rec["captured_at"],
                "snapshots": [],
            }
            history[vid] = entry

        for field in ("title", "author", "topic", "url", "summary", "published_at"):
            if rec.get(field):
                entry[field] = rec[field]
        entry["tags"] = list(dict.fromkeys([*entry.get("tags", []), *rec["tags"]]))
        entry["ideas"] = list(dict.fromkeys([*entry.get("ideas", []), *rec["ideas"]]))
        for f in ("key_points", "novel_ideas", "speaker_claims"):
            entry[f] = list(dict.fromkeys([*entry.get(f, []), *rec.get(f, [])]))
        entry["last_seen"] = rec["captured_at"]

        pkg_snap = {"date": rec["captured_at"], **rec["metrics"]}
        # Keep an existing snapshot if it already has real metrics (from refresh_metrics.py).
        # Only replace it when the package provides at least one metric value.
        existing = next((s for s in entry["snapshots"] if s.get("date") == rec["captured_at"]), None)
        has_real_metrics = existing and any(
            existing.get(k) for k in ("views", "likes", "comments")
        )
        pkg_has_metrics = any(rec["metrics"].get(k) for k in ("views", "likes", "comments"))
        if not has_real_metrics or pkg_has_metrics:
            # Merge: prefer larger values so neither source loses data
            if has_real_metrics and pkg_has_metrics:
                for k in ("views", "likes", "comments"):
                    pkg_snap[k] = max(existing.get(k, 0), pkg_snap.get(k, 0)) or existing.get(k) or pkg_snap.get(k)
            snaps = [s for s in entry["snapshots"] if s.get("date") != rec["captured_at"]]
            snaps.append(pkg_snap)
            snaps.sort(key=lambda s: s.get("date", ""))
            entry["snapshots"] = snaps

    # Status is owned entirely by refresh_metrics.py (real YouTube check).
    # process.py only sets "available" for videos present in today's package
    # and never downgrades a status — so no false "deleted" labels appear
    # before refresh_metrics.py has had a chance to verify anything.
    for vid in latest_video_ids:
        if vid in history:
            history[vid]["status"] = "available"

    # Ensure every entry has at least a default status.
    for entry in history.values():
        if not entry.get("status"):
            entry["status"] = "available"

    return history


# --------------------------------------------------------------------------- #
# Dashboard aggregation
# --------------------------------------------------------------------------- #
def _engagement(metrics: dict[str, Any]) -> int:
    return sum(int(metrics.get(k, 0) or 0) for k in ENGAGEMENT_KEYS)


def _delta(snaps: list[dict], metric: str) -> int | None:
    """Change in ``metric`` between the last two snapshots that have real metrics."""
    vals = [
        int(s.get(metric, 0) or 0)
        for s in snaps
        if _has_metrics(s) and metric in s
    ]
    if len(vals) < 2:
        return None
    return vals[-1] - vals[-2]


def build_dashboard(history: dict[str, Any]) -> dict[str, Any]:
    videos: list[dict] = []
    for entry in history.values():
        snaps = entry["snapshots"]
        # Use the most recent snapshot that actually has metric data.
        metric_snaps = [s for s in snaps if _has_metrics(s)]
        latest = metric_snaps[-1] if metric_snaps else (snaps[-1] if snaps else {})
        latest_metrics = {k: v for k, v in latest.items() if k != "date"}
        series = {
            metric: [
                {"date": s["date"], "value": int(s.get(metric, 0) or 0)}
                for s in snaps if metric in s
            ]
            for metric in ("views", "likes", "comments")
        }
        videos.append({
            "video_id": entry["video_id"],
            "title": entry["title"],
            "author": entry["author"],
            "topic": entry["topic"],
            "macro_theme": classify_macro_theme(entry),
            "url": entry["url"],
            "tags": entry.get("tags", []),
            "ideas": entry.get("ideas", []),
            "summary": entry.get("summary", ""),
            "key_points": entry.get("key_points", []),
            "novel_ideas": entry.get("novel_ideas", []),
            "speaker_claims": entry.get("speaker_claims", []),
            "published_at": entry.get("published_at"),
            "first_seen": entry.get("first_seen"),
            "last_seen": entry.get("last_seen"),
            "status": entry.get("status", "available"),
            "metrics": latest_metrics,
            "engagement": _engagement(latest_metrics),
            "deltas": {
                "views": _delta(snaps, "views"),
                "likes": _delta(snaps, "likes"),
                "comments": _delta(snaps, "comments"),
            },
            "series": series,
            "snapshots_count": len(snaps),
        })

    videos.sort(key=lambda v: v["metrics"].get("views", 0), reverse=True)

    authors = _group_by(videos, "author")
    themes = _group_by(videos, "topic")
    timeline = _build_timeline(history)

    trending = sorted(
        [v for v in videos if v["deltas"].get("views")],
        key=lambda v: v["deltas"]["views"] or 0,
        reverse=True,
    )[:12]

    latest_date = timeline[-1]["date"] if timeline else None

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "latest_date": latest_date,
        "totals": {
            "videos": len(videos),
            "authors": len(authors),
            "themes": len(themes),
            "views": sum(v["metrics"].get("views", 0) for v in videos),
            "likes": sum(v["metrics"].get("likes", 0) for v in videos),
            "comments": sum(v["metrics"].get("comments", 0) for v in videos),
        },
        "videos": videos,
        "authors": authors,
        "themes": themes,
        "timeline": timeline,
        "trending": [
            {"video_id": v["video_id"], "title": v["title"], "author": v["author"],
             "topic": v["topic"], "delta_views": v["deltas"]["views"]}
            for v in trending
        ],
    }


def _group_by(videos: list[dict], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for v in videos:
        key = v.get(field) or "—"
        g = groups.setdefault(key, {
            "name": key, "video_count": 0, "views": 0, "likes": 0,
            "comments": 0, "engagement": 0, "members": set(), "video_ids": [],
        })
        g["video_count"] += 1
        g["views"] += v["metrics"].get("views", 0)
        g["likes"] += v["metrics"].get("likes", 0)
        g["comments"] += v["metrics"].get("comments", 0)
        g["engagement"] += v["engagement"]
        g["video_ids"].append(v["video_id"])
        # cross-reference: authors collect their themes and vice-versa
        g["members"].add(v["topic"] if field == "author" else v["author"])
    out = []
    for g in groups.values():
        g["members"] = sorted(m for m in g["members"] if m)
        out.append(g)
    out.sort(key=lambda g: g["views"], reverse=True)
    return out


def _has_metrics(snap: dict) -> bool:
    return any(snap.get(k) for k in ("views", "likes", "comments"))


def _build_timeline(history: dict[str, Any]) -> list[dict]:
    """Build a time series over every date that has new videos or metric refreshes.

    For first_seen dates: new_videos count + latest available metrics per video
    (same as before — packages rarely carry real view counts so we use the most
    recent refresh snapshot).

    For metric-refresh dates that fall after the last first_seen date: added as
    extra entries with new_videos=0 and the actual snapshot metrics for that day.
    This makes the timeline extend to the latest refresh run.
    """
    by_date: dict[str, dict] = {}

    for entry in history.values():
        fs = entry.get("first_seen")
        if not fs:
            continue

        snaps = entry.get("snapshots", [])
        metric_snaps = [s for s in snaps if _has_metrics(s)]
        latest = metric_snaps[-1] if metric_snaps else None

        agg = by_date.setdefault(fs, {
            "date": fs, "views": None, "likes": None, "comments": None,
            "new_videos": 0,
        })
        agg["new_videos"] += 1

        if latest:
            agg["views"] = (agg["views"] or 0) + int(latest.get("views", 0) or 0)
            agg["likes"] = (agg["likes"] or 0) + int(latest.get("likes", 0) or 0)
            agg["comments"] = (agg["comments"] or 0) + int(latest.get("comments", 0) or 0)

    # Collect dates that only appear as metric-refresh snapshots (no new videos).
    refresh_only: set[str] = set()
    for entry in history.values():
        for snap in entry.get("snapshots", []):
            d = snap.get("date")
            if d and _has_metrics(snap) and d not in by_date:
                refresh_only.add(d)

    for d in refresh_only:
        agg = {"date": d, "views": None, "likes": None, "comments": None, "new_videos": 0}
        for entry in history.values():
            for snap in entry.get("snapshots", []):
                if snap.get("date") == d and _has_metrics(snap):
                    for k in ("views", "likes", "comments"):
                        v = int(snap.get(k, 0) or 0)
                        if v:
                            agg[k] = (agg[k] or 0) + v
        by_date[d] = agg

    return [by_date[d] for d in sorted(by_date)]


# --------------------------------------------------------------------------- #
# Macro-theme classification
# --------------------------------------------------------------------------- #
MACRO_THEMES: dict[str, list[str]] = {
    "AI":              ["ai", "нейросет", "gpt", "llm", "искусственный интеллект",
                        "машинное обучение", "claude", "openai", "gemini", "нейро",
                        "language model", "трансформер"],
    "Финансы":         ["финанс", "инвестиц", "акци", "крипто", "биткоин", "экономик",
                        "деньг", "рынок", "трейд", "биржа", "доллар", "инфляц",
                        "дивиденд", "портфел"],
    "Геополитика":     ["геополитик", "война", "украин", "сша", "китай", "нато",
                        "санкц", "политик", "конфликт", "армия", "военн"],
    "Психология":      ["психолог", "эмоц", "мышлени", "осознанн", "медитац",
                        "мозг", "поведени", "когнитив", "ментальн", "тревог"],
    "Бизнес":          ["бизнес", "стартап", "предпринимат", "продаж", "маркетинг",
                        "менеджмент", "управлени", "компани", "корпорат"],
    "Наука":           ["наук", "физик", "биолог", "химия", "исследован",
                        "квантов", "космос", "математик", "эволюц"],
    "Технологии":      ["технолог", "программ", "блокчейн", "web3", "разработк",
                        "software", "hardware", "кибер", "автоматизац"],
    "Личностный рост": ["саморазвити", "продуктивност", "успех", "лидерств",
                        "навык", "карьер", "привычк", "цел", "мотивац"],
}
DEFAULT_MACRO_THEME = "Разное"


def classify_macro_theme(entry: dict) -> str:
    # Respect LLM-enriched classification stored in history by enrich.py
    override = entry.get("_macro_theme", "")
    if override and override in MACRO_THEMES:
        return override
    text = " ".join([
        entry.get("title", ""),
        entry.get("topic", ""),
        " ".join(entry.get("tags", [])),
        " ".join(entry.get("ideas", [])[:4]),
    ]).lower()
    for theme, keywords in MACRO_THEMES.items():
        if any(kw in text for kw in keywords):
            return theme
    return DEFAULT_MACRO_THEME


# --------------------------------------------------------------------------- #
# Graph (hub nodes: macro-themes + authors; video nodes; all edges)
# --------------------------------------------------------------------------- #
def build_graph(history: dict[str, Any], dashboard: dict[str, Any]) -> dict[str, Any]:
    records = list(history.values())

    # Classify every record into a macro-theme.
    for rec in records:
        rec["_macro_theme"] = classify_macro_theme(rec)

    # --- Video nodes ---
    video_nodes = [{
        "id":          rec["video_id"],
        "type":        "video",
        "label":       rec["title"],
        "author":      rec["author"],
        "topic":       rec["topic"],
        "macro_theme": rec["_macro_theme"],
        "url":         rec["url"],
        "views":       rec["snapshots"][-1].get("views", 0) if rec["snapshots"] else 0,
        "ideas":       rec.get("ideas", [])[:8],
        "degree":      2,  # every video connects to 1 theme + 1 author hub
    } for rec in records]

    # --- Theme hub nodes ---
    theme_counts: dict[str, int] = {}
    for rec in records:
        t = rec["_macro_theme"]
        theme_counts[t] = theme_counts.get(t, 0) + 1

    theme_nodes = [{
        "id":    f"theme:{t}",
        "type":  "theme",
        "label": t,
        "macro_theme": t,
        "count": c,
        "degree": c,
    } for t, c in sorted(theme_counts.items(), key=lambda x: -x[1])]

    # --- Author hub nodes ---
    author_counts: dict[str, int] = {}
    for rec in records:
        a = rec.get("author") or ""
        if a:
            author_counts[a] = author_counts.get(a, 0) + 1

    author_nodes = [{
        "id":    f"author:{a}",
        "type":  "author",
        "label": a,
        "count": c,
        "degree": c,
    } for a, c in sorted(author_counts.items(), key=lambda x: -x[1])]

    # --- Edges: video → theme hub + video → author hub only ---
    hub_edges: list[dict] = []
    for rec in records:
        vid = rec["video_id"]
        hub_edges.append({
            "source": vid,
            "target": f"theme:{rec['_macro_theme']}",
            "type":   "hub_theme",
            "weight": 1.0,
        })
        author = rec.get("author") or ""
        if author:
            hub_edges.append({
                "source": vid,
                "target": f"author:{author}",
                "type":   "hub_author",
                "weight": 1.0,
            })

    all_nodes = theme_nodes + author_nodes + video_nodes

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "backend":      "hub_only",
        "nodes":        all_nodes,
        "edges":        hub_edges,
        "macro_themes": [t["label"] for t in theme_nodes],
        "stats":        {"nodes": len(all_nodes), "edges": len(hub_edges)},
    }



def _bump_asset_version(docs: Path) -> None:
    """Replace ?v=... query string on script tags in index.html with current timestamp."""
    import re
    index = docs / "index.html"
    if not index.exists():
        return
    ver = dt.datetime.utcnow().strftime("%Y%m%d%H%M")
    html = index.read_text(encoding="utf-8")
    html = re.sub(r'(src="(?:app|graph)\.js)(?:\?v=[^"]*)?(")', rf'\1?v={ver}\2', html)
    index.write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw", help="folder with copied packages")
    ap.add_argument("--out", default="docs/data", help="output folder for datasets")
    ap.add_argument("--pattern", default="daily_package",
                    help="filename substring identifying package files")
    args = ap.parse_args()

    raw = Path(args.raw)
    out = Path(args.out)

    records = normalize.parse_directory(raw, pattern=args.pattern)
    print(f"Parsed {len(records)} video records from {raw}")

    history_path = out / "history.json"
    history = _load_json(history_path, {})
    history = update_history(history, records)
    _save_json(history_path, history)

    report_dates = _publish_reports(raw, out)

    dashboard = build_dashboard(history)
    dashboard["report_dates"] = report_dates
    _save_json(out / "dashboard.json", dashboard)

    # Bump script version in index.html so browsers skip the cache on every build.
    _bump_asset_version(out.parent)

    graph = build_graph(history, dashboard)
    _save_json(out / "graph.json", graph)

    print(f"Dashboard: {dashboard['totals']['videos']} videos, "
          f"{dashboard['totals']['authors']} authors, "
          f"{dashboard['totals']['themes']} themes")
    print(f"Reports: {len(report_dates)} dates available")
    print(f"Graph: {graph['stats']['nodes']} nodes, {graph['stats']['edges']} "
          f"edges (backend: {graph['backend']})")
    return 0


def _publish_reports(raw: Path, out: Path) -> list[str]:
    """Copy final_intelligence_report files to docs/data/reports/YYYY-MM-DD.md.

    Only ``*final_intelligence_report*`` files are published and advertised.
    Returns sorted list of dates that have an intelligence report available.
    """
    import re
    import shutil

    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    date_re = re.compile(r"(20\d{2}-\d{2}-\d{2})")
    found: dict[str, Path] = {}

    for path in sorted(raw.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".md", ".markdown"):
            continue
        if "final_intelligence_report" not in path.name.lower():
            continue
        m = date_re.search(path.name)
        if not m:
            continue
        date = m.group(1)
        found[date] = path

    for date, path in found.items():
        shutil.copy2(path, reports_dir / f"{date}.md")

    return sorted(found.keys())


if __name__ == "__main__":
    raise SystemExit(main())

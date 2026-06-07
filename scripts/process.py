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

from lib import embed, normalize  # noqa: E402

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

    ``history[video_id]`` keeps stable metadata plus a list of dated snapshots.
    A snapshot for an already-present date is replaced (idempotent reruns).
    """
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
                "published_at": rec["published_at"],
                "first_seen": rec["captured_at"],
                "snapshots": [],
            }
            history[vid] = entry

        # Refresh metadata with the latest, richest values.
        for field in ("title", "author", "topic", "url", "summary", "published_at"):
            if rec.get(field):
                entry[field] = rec[field]
        entry["tags"] = list(dict.fromkeys([*entry.get("tags", []), *rec["tags"]]))
        entry["ideas"] = list(dict.fromkeys([*entry.get("ideas", []), *rec["ideas"]]))
        entry["last_seen"] = rec["captured_at"]

        snap = {"date": rec["captured_at"], **rec["metrics"]}
        snaps = [s for s in entry["snapshots"] if s.get("date") != rec["captured_at"]]
        snaps.append(snap)
        snaps.sort(key=lambda s: s.get("date", ""))
        entry["snapshots"] = snaps
    return history


# --------------------------------------------------------------------------- #
# Dashboard aggregation
# --------------------------------------------------------------------------- #
def _engagement(metrics: dict[str, Any]) -> int:
    return sum(int(metrics.get(k, 0) or 0) for k in ENGAGEMENT_KEYS)


def _delta(snaps: list[dict], metric: str) -> int | None:
    """Change in ``metric`` between the last two snapshots."""
    vals = [(s["date"], int(s.get(metric, 0) or 0)) for s in snaps if metric in s]
    if len(vals) < 2:
        return None
    return vals[-1][1] - vals[-2][1]


def build_dashboard(history: dict[str, Any]) -> dict[str, Any]:
    videos: list[dict] = []
    for entry in history.values():
        snaps = entry["snapshots"]
        latest = snaps[-1] if snaps else {}
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
            "url": entry["url"],
            "tags": entry.get("tags", []),
            "ideas": entry.get("ideas", []),
            "summary": entry.get("summary", ""),
            "published_at": entry.get("published_at"),
            "first_seen": entry.get("first_seen"),
            "last_seen": entry.get("last_seen"),
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

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
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


def _build_timeline(history: dict[str, Any]) -> list[dict]:
    by_date: dict[str, dict] = {}
    first_seen_by_date: dict[str, int] = {}
    for entry in history.values():
        fs = entry.get("first_seen")
        if fs:
            first_seen_by_date[fs] = first_seen_by_date.get(fs, 0) + 1
        for s in entry["snapshots"]:
            d = s["date"]
            agg = by_date.setdefault(d, {"date": d, "views": 0, "likes": 0,
                                         "comments": 0, "active_videos": 0})
            agg["views"] += int(s.get("views", 0) or 0)
            agg["likes"] += int(s.get("likes", 0) or 0)
            agg["comments"] += int(s.get("comments", 0) or 0)
            agg["active_videos"] += 1
    for d, agg in by_date.items():
        agg["new_videos"] = first_seen_by_date.get(d, 0)
    return [by_date[d] for d in sorted(by_date)]


# --------------------------------------------------------------------------- #
# Graph (Obsidian-style idea map)
# --------------------------------------------------------------------------- #
def build_graph(history: dict[str, Any], dashboard: dict[str, Any]) -> dict[str, Any]:
    records = list(history.values())
    edges, backend = embed.compute_edges(records)

    # Secondary "shared tag" edges (dashed in the UI) enrich the semantic map.
    tag_edges = _shared_tag_edges(records)
    existing = {(e["source"], e["target"]) for e in edges}
    for e in tag_edges:
        if (e["source"], e["target"]) not in existing:
            edges.append(e)

    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1

    nodes = [{
        "id": e["video_id"],
        "label": e["title"],
        "author": e["author"],
        "topic": e["topic"],
        "url": e["url"],
        "views": e["snapshots"][-1].get("views", 0) if e["snapshots"] else 0,
        "ideas": e.get("ideas", [])[:8],
        "degree": degree.get(e["video_id"], 0),
    } for e in records]

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "nodes": nodes,
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges)},
    }


def _shared_tag_edges(records: list[dict], min_shared: int = 2) -> list[dict]:
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    norm = [(r["video_id"], {t.lower() for t in r.get("tags", [])}) for r in records]
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            shared = norm[i][1] & norm[j][1]
            if len(shared) >= min_shared:
                key = tuple(sorted((norm[i][0], norm[j][0])))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "source": key[0], "target": key[1],
                    "weight": round(min(1.0, len(shared) / 5), 3),
                    "type": "tag", "shared": sorted(shared),
                })
    return edges


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

    dashboard = build_dashboard(history)
    _save_json(out / "dashboard.json", dashboard)

    graph = build_graph(history, dashboard)
    _save_json(out / "graph.json", graph)

    print(f"Dashboard: {dashboard['totals']['videos']} videos, "
          f"{dashboard['totals']['authors']} authors, "
          f"{dashboard['totals']['themes']} themes")
    print(f"Graph: {graph['stats']['nodes']} nodes, {graph['stats']['edges']} "
          f"edges (backend: {graph['backend']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

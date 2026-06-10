#!/usr/bin/env python3
"""Fetch current public metrics and availability for all tracked videos from YouTube.

Backend selection (automatic):
  1. YouTube Data API v3 — if GOOGLE_API_KEY env var is set.
     Batches 50 videos per request, no bot-detection, free quota.
  2. yt-dlp with Android player client — fallback (no API key needed).
     Mobile endpoint is less aggressively bot-checked than desktop.

Updates history.json in-place, adding a snapshot for today's date with
fresh metrics and setting status:
  available  — video is public
  deleted    — video removed by the author (unavailable / 404)
  private    — video set to private

After running, execute  python scripts/process.py  to rebuild dashboard.json.

Usage:
    python scripts/refresh_metrics.py
    python scripts/refresh_metrics.py --out docs/data --limit 50
    python scripts/refresh_metrics.py --force   # re-check even if already done today
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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


# ---------------------------------------------------------------------------
# Sentinel values
# ---------------------------------------------------------------------------
_CONFIRMED_GONE: dict = {"_confirmed_gone": True}
_BOT_CHECKED:    dict = {"_bot_checked":    True}  # rate-limited / bot-check — retry next run

# yt-dlp stderr phrases that confirm the video is genuinely gone.
_DELETED_PHRASES = (
    "video unavailable",
    "this video has been removed",
    "this video is no longer available",
    "has been removed by the uploader",
    "video is private",
    "private video",
    "members-only content",
)

# yt-dlp stderr phrases that indicate a bot-check (transient; do NOT mark checked).
_BOT_PHRASES = (
    "sign in to confirm",
    "confirm you're not a bot",
    "use --cookies-from-browser",
    "use --cookies for the authentication",
)


# ---------------------------------------------------------------------------
# Backend 1: YouTube Data API v3  (GOOGLE_API_KEY)
# ---------------------------------------------------------------------------
_YT_API_URL = "https://www.googleapis.com/youtube/v3/videos"
_YT_BATCH   = 50


def _api_available() -> bool:
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip())


def _fetch_batch_via_api(video_ids: list[str], api_key: str) -> dict[str, dict]:
    """Fetch statistics for up to 50 videos in one API call.

    Returns {video_id: {views, likes, comments, status}} for every requested id.
    Videos absent from the response are treated as deleted/unavailable.
    """
    params = (
        f"?part=statistics,status"
        f"&id={','.join(video_ids)}"
        f"&key={api_key}"
    )
    req = urllib.request.Request(_YT_API_URL + params)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"  [api] request error: {exc}", file=sys.stderr)
        return {}

    result: dict[str, dict] = {}
    found: set[str] = set()
    for item in data.get("items", []):
        vid = item["id"]
        found.add(vid)
        stats   = item.get("statistics", {})
        privacy = item.get("status", {}).get("privacyStatus", "public")
        result[vid] = {
            "views":    int(stats.get("viewCount",    0) or 0),
            "likes":    int(stats.get("likeCount",    0) or 0),
            "comments": int(stats.get("commentCount", 0) or 0),
            "status":   "private" if privacy == "private" else "available",
        }

    # Videos not returned by the API are deleted or otherwise unavailable.
    for vid in video_ids:
        if vid not in found:
            result[vid] = {"views": 0, "likes": 0, "comments": 0, "status": "deleted"}

    return result


def refresh_via_api(history: dict[str, Any], entries: list[dict], today: str) -> int:
    """Refresh metrics using YouTube Data API v3. Returns number of updated entries."""
    api_key = os.environ["GOOGLE_API_KEY"]
    vids    = [e["video_id"] for e in entries]
    updated = 0

    for batch_start in range(0, len(vids), _YT_BATCH):
        batch_ids  = vids[batch_start : batch_start + _YT_BATCH]
        batch_num  = batch_start // _YT_BATCH + 1
        total_batches = (len(vids) + _YT_BATCH - 1) // _YT_BATCH
        print(f"  [api] batch {batch_num}/{total_batches} ({len(batch_ids)} videos)…")

        results = _fetch_batch_via_api(batch_ids, api_key)
        if not results:
            # Whole batch failed (network/quota) — skip without marking checked
            print(f"  [api] batch {batch_num} failed, will retry next run")
            continue

        for entry in entries[batch_start : batch_start + _YT_BATCH]:
            vid  = entry["video_id"]
            data = results.get(vid)
            if data is None:
                continue  # not in response — shouldn't happen; skip

            entry["status"]             = data["status"]
            entry["metrics_checked_at"] = today

            if data["status"] == "deleted":
                print(f"    ✗ {vid} — deleted")
                continue

            snap = {
                "date":     today,
                "views":    data["views"],
                "likes":    data["likes"],
                "comments": data["comments"],
            }
            snaps = [s for s in entry.get("snapshots", []) if s.get("date") != today]
            snaps.append(snap)
            snaps.sort(key=lambda s: s.get("date", ""))
            entry["snapshots"] = snaps
            updated += 1
            icon = "🔒" if data["status"] == "private" else "✓"
            print(
                f"    {icon} {vid}"
                f"  views={snap['views']:,}  likes={snap['likes']:,}  comments={snap['comments']:,}"
            )

    return updated


# ---------------------------------------------------------------------------
# Backend 2: yt-dlp with Android player client  (fallback)
# ---------------------------------------------------------------------------
def _fetch_video_info_ytdlp(url: str) -> dict | None:
    """Return yt-dlp JSON metadata, _CONFIRMED_GONE, _BOT_CHECKED, or None."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        # Android player client bypasses most bot-detection on CI runners.
        "--extractor-args", "youtube:player_client=android,mweb",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        stderr_lower = result.stderr.lower()
        if any(p in stderr_lower for p in _BOT_PHRASES):
            return _BOT_CHECKED
        if any(p in stderr_lower for p in _DELETED_PHRASES):
            return _CONFIRMED_GONE
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def _status_from_info(info: dict) -> str:
    avail = (info.get("availability") or "").lower()
    if "private" in avail or info.get("_confirmed_gone"):
        return "private"
    if "unavailable" in avail or "deleted" in avail:
        return "deleted"
    return "available"


def refresh_via_ytdlp(history: dict[str, Any], entries: list[dict], today: str) -> int:
    """Refresh metrics using yt-dlp. Returns number of updated entries."""
    updated = skipped = bot_blocked = 0

    for i, entry in enumerate(entries, 1):
        url = entry.get("url", "")
        vid = entry["video_id"]
        print(f"  [{i}/{len(entries)}] {vid}  {entry.get('title', '')[:60]}")

        info = _fetch_video_info_ytdlp(url)

        if info is _BOT_CHECKED:
            # YouTube blocked this request — do NOT mark as checked so we retry next run.
            bot_blocked += 1
            print(f"         ⚠ bot-check — will retry next run")
            continue

        if info is None:
            # Generic transient failure (network, timeout) — mark checked to avoid loops.
            entry["metrics_checked_at"] = today
            skipped += 1
            print(f"         ? fetch error — status unchanged ({entry.get('status', '?')})")
            continue

        if info is _CONFIRMED_GONE:
            entry["status"]             = "deleted"
            entry["metrics_checked_at"] = today
            print(f"         ✗ deleted (YouTube confirmed)")
            continue

        status = _status_from_info(info)
        entry["status"]             = status
        entry["metrics_checked_at"] = today

        snap = {
            "date":     today,
            "views":    int(info.get("view_count")    or 0),
            "likes":    int(info.get("like_count")    or 0),
            "comments": int(info.get("comment_count") or 0),
        }
        snaps = [s for s in entry.get("snapshots", []) if s.get("date") != today]
        snaps.append(snap)
        snaps.sort(key=lambda s: s.get("date", ""))
        entry["snapshots"] = snaps
        updated += 1
        icon = {"available": "✓", "deleted": "✗", "private": "🔒"}.get(status, "?")
        print(
            f"         {icon} {status}"
            f"  views={snap['views']}  likes={snap['likes']}  comments={snap['comments']}"
        )

    print(
        f"\nОбновлено: {updated}"
        f"  |  Ошибка: {skipped}"
        f"  |  Бот-чек (повтор при след. запуске): {bot_blocked}"
    )
    return updated


# ---------------------------------------------------------------------------
# Shared refresh orchestration
# ---------------------------------------------------------------------------
def refresh(history: dict[str, Any], limit: int | None, force: bool) -> int:
    today = dt.date.today().isoformat()

    entries = list(history.values())
    if not force:
        entries = [e for e in entries if e.get("metrics_checked_at", "") < today]

    # Newest videos first, then cap with limit.
    entries.sort(key=lambda e: e.get("last_seen", ""), reverse=True)
    if limit:
        entries = entries[:limit]

    if not entries:
        print("All entries already checked today. Use --force to re-check.")
        return 0

    if _api_available():
        print(f"Backend: YouTube Data API v3  ({len(entries)} videos, batches of {_YT_BATCH})")
        return refresh_via_api(history, entries, today)
    else:
        print(f"Backend: yt-dlp + android client  ({len(entries)} videos)")
        print("Tip: set GOOGLE_API_KEY for faster, bot-check-free refreshes.")
        return refresh_via_ytdlp(history, entries, today)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _reset_false_deletes(history: dict[str, Any]) -> int:
    """Reset videos incorrectly marked 'deleted' due to fetch errors.

    A video is considered a false-positive deletion when status == "deleted"
    AND it has no snapshot with any metrics (yt-dlp never returned valid
    metadata, so the status came from a failed fetch, not real confirmation).
    """
    reset = 0
    for entry in history.values():
        if entry.get("status") == "deleted":
            has_any_metrics = any(
                s.get("views") or s.get("likes") or s.get("comments")
                for s in entry.get("snapshots", [])
            )
            if not has_any_metrics:
                entry["status"] = "available"
                entry.pop("metrics_checked_at", None)  # force re-check
                reset += 1
    return reset


def _reset_stale_checks(history: dict[str, Any]) -> int:
    """Reset metrics_checked_at for videos whose last check produced no snapshot.

    This happens when yt-dlp gets bot-checked (metrics_checked_at is set but
    no snapshot is added).  Clearing the field ensures they're retried.
    """
    reset = 0
    for entry in history.values():
        checked = entry.get("metrics_checked_at", "")
        if not checked:
            continue
        # If no snapshot exists for the checked date, the check was a no-op.
        has_snap_for_date = any(
            s.get("date") == checked and (s.get("views") or s.get("likes") or s.get("comments"))
            for s in entry.get("snapshots", [])
        )
        if not has_snap_for_date:
            entry.pop("metrics_checked_at", None)
            reset += 1
    return reset


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out",   default="docs/data")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--reset-stale", action="store_true",
                    help="clear metrics_checked_at for videos with no snapshot for that date")
    args = ap.parse_args()

    if not _api_available():
        try:
            subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("ERROR: neither GOOGLE_API_KEY nor yt-dlp is available.", file=sys.stderr)
            return 1

    history_path = Path(args.out) / "history.json"
    history = _load_json(history_path, {})
    if not history:
        print(f"history.json не найден: {history_path}", file=sys.stderr)
        return 1

    if args.reset_stale:
        reset = _reset_stale_checks(history)
        print(f"Сброшено записей с устаревшим metrics_checked_at: {reset}")

    reset_del = _reset_false_deletes(history)
    if reset_del:
        print(f"Сброшено ложных 'deleted': {reset_del} видео (нет метрик → не подтверждено)")

    updated = refresh(history, args.limit, args.force)
    if updated or args.reset_stale or reset_del:
        _save_json(history_path, history)
        print(f"Сохранено history.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

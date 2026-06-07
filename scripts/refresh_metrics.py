#!/usr/bin/env python3
"""Fetch current public metrics and availability for all tracked videos from YouTube.

Uses yt-dlp — no API key, no authentication needed.
YouTube views/likes/comments are public; yt-dlp reads them from the video page.

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
import subprocess
import sys
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


# yt-dlp stderr phrases that confirm the video is genuinely gone from YouTube.
# Any other failure (timeout, network, bot-check, geo-block) is treated as
# a transient fetch error and does NOT change the video's status.
_DELETED_PHRASES = (
    "video unavailable",
    "this video has been removed",
    "this video is no longer available",
    "has been removed by the uploader",
    "video is private",
    "private video",
    "members-only content",
)

# Sentinel returned when yt-dlp explicitly confirmed deletion/privacy.
_CONFIRMED_GONE: dict = {"_confirmed_gone": True}


def _fetch_video_info(url: str) -> dict | None:
    """Return yt-dlp JSON metadata, ``_CONFIRMED_GONE`` if YouTube confirmed
    the video is deleted/private, or ``None`` on a transient fetch failure."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",   # keep stderr for error classification
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        # Non-zero exit — check stderr to distinguish definitive deletion from
        # transient errors (network, bot-check, rate-limit, geo-block, etc.).
        stderr_lower = result.stderr.lower()
        if any(phrase in stderr_lower for phrase in _DELETED_PHRASES):
            return _CONFIRMED_GONE
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    # Transient failure: caller should preserve the previous status.
    return None


def _status_from_info(info: dict) -> str:
    """Classify a successfully-fetched yt-dlp info dict."""
    availability = (info.get("availability") or "").lower()
    if "private" in availability or info.get("_confirmed_gone"):
        return "private"
    if "unavailable" in availability or "deleted" in availability:
        return "deleted"
    return "available"


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

    print(f"Refreshing metrics for {len(entries)} video(s)…")
    updated = skipped = 0

    for i, entry in enumerate(entries, 1):
        url = entry.get("url", "")
        vid = entry["video_id"]
        print(f"  [{i}/{len(entries)}] {vid}  {entry.get('title', '')[:60]}")

        info = _fetch_video_info(url)

        if info is None:
            # Transient fetch failure — keep previous status, mark as checked so
            # we don't retry on the same day and slow down the workflow.
            entry["metrics_checked_at"] = today
            skipped += 1
            print(f"         ? fetch error — status unchanged ({entry.get('status', '?')})")
            continue

        if info is _CONFIRMED_GONE:
            entry["status"] = "deleted"
            entry["metrics_checked_at"] = today
            print(f"         ✗ deleted (YouTube confirmed)")
            continue

        status = _status_from_info(info)
        entry["status"] = status
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
        print(f"         {icon} {status}  "
              f"views={snap['views']}  likes={snap['likes']}  comments={snap['comments']}")

    print(f"\nОбновлено: {updated}  |  Ошибка получения (статус не изменён): {skipped}")
    return updated


def _reset_false_deletes(history: dict[str, Any]) -> int:
    """Reset videos incorrectly marked 'deleted' due to fetch errors.

    A video is considered a false-positive deletion when:
    - status == "deleted" AND
    - it has no snapshot with metrics (yt-dlp never actually confirmed deletion
      by returning valid metadata, so the status came from info=None).

    These are reset to "available" so the next refresh re-checks them properly.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="docs/data", help="папка с history.json (default: docs/data)")
    ap.add_argument("--limit", type=int, default=None,
                    help="обновить только N последних видео")
    ap.add_argument("--force", action="store_true",
                    help="перепроверить даже те, что уже проверены сегодня")
    args = ap.parse_args()

    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: yt-dlp не установлен. Запустите: pip install yt-dlp", file=sys.stderr)
        return 1

    history_path = Path(args.out) / "history.json"
    history = _load_json(history_path, {})
    if not history:
        print(f"history.json не найден: {history_path}", file=sys.stderr)
        return 1

    reset = _reset_false_deletes(history)
    if reset:
        print(f"Сброшено ложных 'deleted': {reset} видео (yt-dlp не подтвердил удаление)")

    updated = refresh(history, limit=args.limit, force=args.force)
    _save_json(history_path, history)

    print(f"Запустите  python scripts/process.py  чтобы пересобрать dashboard.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

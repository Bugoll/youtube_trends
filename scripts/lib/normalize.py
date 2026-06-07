"""Parse and normalize ``daily_package`` files (``.json`` and ``.md``).

A *daily package* is a snapshot, captured on a given day, describing one or
more YouTube video "cards". The same video typically reappears across many
daily packages, which is exactly what lets us track its dynamics over time.

The source repository (``MyCrabs/youtube-daily``) stores packages both as
JSON and as Markdown notes. Rather than picking one format we parse both and
*merge* the records for the same video on the same day, keeping the most
informative value for every field (JSON tends to win on metrics, Markdown on
free-form ideas / summaries).

The output is a flat list of :class:`dict` records with a stable schema so the
rest of the pipeline never has to care about the original file format.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Iterable

try:  # PyYAML is optional; we fall back to a tiny parser if it is missing.
    import yaml  # type: ignore

    _HAVE_YAML = True
except Exception:  # pragma: no cover - exercised only without PyYAML
    _HAVE_YAML = False


# --------------------------------------------------------------------------- #
# Field aliases. Source files are messy and inconsistent, so for every logical
# field we accept a range of likely key spellings (case-insensitive).
# --------------------------------------------------------------------------- #
_ALIASES: dict[str, tuple[str, ...]] = {
    "video_id": ("video_id", "videoid", "id", "vid", "yt_id", "ytid"),
    "url": ("url", "link", "video_url", "watch_url", "href", "permalink"),
    "title": ("title", "name", "video_title", "heading", "header"),
    "author": (
        "author", "channel", "channel_title", "channeltitle", "creator",
        "channel_name", "channelname", "uploader", "source",
    ),
    "topic": (
        "topic", "theme", "category", "niche", "rubric", "section", "tema",
        "main_topic",
    ),
    "published_at": (
        "published_at", "publishedat", "published", "upload_date",
        "uploaddate", "publish_date", "publishdate", "date_published",
    ),
    "summary": (
        "summary", "description", "summary_text", "abstract", "overview",
        "desc", "annotation", "opisanie", "short_summary",
    ),
    "transcript": ("transcript", "captions", "subtitles", "full_text"),
}

_TAG_ALIASES = ("tags", "keywords", "hashtags", "labels", "topics", "tegi")
_IDEA_ALIASES = (
    "ideas", "key_ideas", "keyideas", "key_points", "keypoints", "takeaways",
    "insights", "highlights", "theses", "thesis", "points", "main_points",
    "key_takeaways", "idei", "tezisy", "novel_ideas", "speaker_claims",
)
_KEY_POINTS_ALIASES  = ("key_points", "keypoints", "main_points", "takeaways", "highlights")
_NOVEL_IDEAS_ALIASES = ("novel_ideas", "ideas", "key_ideas", "insights")
_SPEAKER_CLAIMS_ALIASES = ("speaker_claims", "claims", "theses", "thesis")

# Metric aliases -> canonical metric name.
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "views": ("views", "viewcount", "view_count", "watch_count", "prosmotry"),
    "likes": ("likes", "likecount", "like_count", "laiki"),
    "comments": ("comments", "commentcount", "comment_count", "kommentarii"),
    "favorites": ("favorites", "favoritecount", "favorite_count"),
    "shares": ("shares", "sharecount", "share_count", "reposts"),
    "subscribers": ("subscribers", "subscribercount", "subs", "podpischiki"),
    "duration": ("duration", "length", "duration_seconds", "dlitelnost"),
}

# Matches a YouTube id inside a URL.
_YT_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{6,})"
)
# Matches a date anywhere in a filename, e.g. daily_package_2026-06-07.
_DATE_IN_NAME_RE = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


def _lower_key_map(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``d`` with lower-cased, de-spaced keys."""
    return {str(k).strip().lower().replace(" ", "_"): v for k, v in d.items()}


def _first(d: dict[str, Any], aliases: Iterable[str]) -> Any:
    for a in aliases:
        if a in d and d[a] not in (None, "", [], {}):
            return d[a]
    return None


def _as_int(value: Any) -> int | None:
    """Coerce messy numeric strings (``"1.2K"``, ``"3 400"``) to ``int``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("b"):
        mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def _extract_text(v: Any) -> str:
    """Extract plain text from a value that may be a dict with a text key."""
    if isinstance(v, dict):
        for key in ("claim", "text", "point", "idea", "content", "value"):
            if key in v and isinstance(v[key], str):
                return v[key].strip()
        return " ".join(str(x) for x in v.values() if isinstance(x, str)).strip()
    return str(v).strip().lstrip("#").strip()


def _as_list(value: Any) -> list[str]:
    """Normalize tags/ideas which may be a list or a delimited string."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = [_extract_text(v) for v in value]
        return [v for v in out if v]
    if isinstance(value, str):
        parts = re.split(r"[,;\n•|]+", value)
        out = [p.strip().lstrip("-").lstrip("#").strip() for p in parts]
        return [p for p in out if p]
    return [str(value)]


def _video_id_from(url: str | None, title: str | None) -> str:
    if url:
        m = _YT_ID_RE.search(url)
        if m:
            return m.group(1)
    base = (title or url or "unknown").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return slug[:64] or "unknown"


def _normalize_date(value: Any) -> str | None:
    if not value:
        return None
    s = str(value)
    m = _DATE_IN_NAME_RE.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s[:10]


def _extract_metrics(d: dict[str, Any]) -> dict[str, int]:
    metrics: dict[str, int] = {}
    # Metrics may be nested under a "metrics"/"stats"/"statistics" key.
    nested = {}
    for key in ("metrics", "stats", "statistics", "metriki"):
        if isinstance(d.get(key), dict):
            nested = _lower_key_map(d[key])
            break
    merged = {**d, **nested}
    for canon, aliases in _METRIC_ALIASES.items():
        val = _as_int(_first(merged, aliases))
        if val is not None:
            metrics[canon] = val
    return metrics


def _record_from_dict(raw: dict[str, Any], captured_at: str) -> dict[str, Any]:
    d = _lower_key_map(raw)
    url = _first(d, _ALIASES["url"])
    title = _first(d, _ALIASES["title"])
    vid = _first(d, _ALIASES["video_id"]) or _video_id_from(url, title)

    return {
        "video_id": str(vid),
        "title": (str(title).strip() if title else "Без названия"),
        "author": (str(_first(d, _ALIASES["author"]) or "Неизвестный автор").strip()),
        "topic": (str(_first(d, _ALIASES["topic"]) or "").strip() or "Без темы"),
        "url": (str(url).strip() if url else ""),
        "tags": _as_list(_first(d, _TAG_ALIASES)),
        "ideas": _as_list(_first(d, _IDEA_ALIASES)),
        "summary": (str(_first(d, _ALIASES["summary"]) or "").strip()),
        "key_points": _as_list(_first(d, _KEY_POINTS_ALIASES)),
        "novel_ideas": _as_list(_first(d, _NOVEL_IDEAS_ALIASES)),
        "speaker_claims": _as_list(_first(d, _SPEAKER_CLAIMS_ALIASES)),
        "published_at": _normalize_date(_first(d, _ALIASES["published_at"])),
        "captured_at": captured_at,
        "metrics": _extract_metrics(d),
    }


# --------------------------------------------------------------------------- #
# Markdown parsing
# --------------------------------------------------------------------------- #
def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (frontmatter dict, body)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_text = text[3:end].strip()
            body = text[end + 4:]
            data = _parse_yaml(fm_text)
            if isinstance(data, dict):
                return data, body
    return {}, text


def _parse_yaml(text: str) -> dict[str, Any]:
    if _HAVE_YAML:
        try:
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return _mini_yaml(text)


def _mini_yaml(text: str) -> dict[str, Any]:
    """A tiny YAML subset parser: ``key: value`` plus simple ``- list`` items."""
    out: dict[str, Any] = {}
    cur_key: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and cur_key:
            out.setdefault(cur_key, [])
            if isinstance(out[cur_key], list):
                out[cur_key].append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip("\"'")
            if val == "":
                cur_key = key
                out[key] = []
            else:
                cur_key = None
                if val.startswith("[") and val.endswith("]"):
                    out[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
                else:
                    out[key] = val
    return out


_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$")
_IDEA_HEADINGS = ("idea", "идеи", "key", "ключ", "тезис", "takeaway", "insight", "highlight", "point")


def _ideas_from_body(body: str) -> tuple[list[str], str]:
    """Pull bullet ideas (esp. under an 'Ideas'/'Идеи' heading) and a summary."""
    ideas: list[str] = []
    summary_lines: list[str] = []
    in_idea_section = False
    for line in body.splitlines():
        hm = _HEADING_RE.match(line)
        if hm:
            heading = hm.group(1).strip().lower()
            in_idea_section = any(k in heading for k in _IDEA_HEADINGS)
            continue
        bm = _BULLET_RE.match(line)
        if bm:
            text = bm.group(1).strip()
            if in_idea_section:
                ideas.append(text)
            continue
        if line.strip() and not in_idea_section:
            summary_lines.append(line.strip())
    # If no explicit ideas section was found, treat all bullets as ideas.
    if not ideas:
        ideas = [m.group(1).strip() for m in
                 (_BULLET_RE.match(l) for l in body.splitlines()) if m]
    summary = " ".join(summary_lines).strip()
    return ideas, summary


def _records_from_markdown(text: str, captured_at: str) -> list[dict[str, Any]]:
    fm, body = _parse_frontmatter(text)
    body_ideas, body_summary = _ideas_from_body(body)
    rec = _record_from_dict(fm, captured_at)
    # Title fallback: first H1 in the body.
    if rec["title"] == "Без названия":
        for line in body.splitlines():
            hm = _HEADING_RE.match(line)
            if hm:
                rec["title"] = hm.group(1).strip()
                break
    if not rec["ideas"]:
        rec["ideas"] = body_ideas
    if not rec["summary"]:
        rec["summary"] = body_summary
    return [rec] if (rec["title"] != "Без названия" or rec["ideas"]) else []


# --------------------------------------------------------------------------- #
# JSON parsing
# --------------------------------------------------------------------------- #
def _iter_video_dicts(data: Any) -> Iterable[dict[str, Any]]:
    """Yield per-video dicts from an arbitrarily shaped JSON document."""
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data, dict):
        # Look for a list of videos under a likely container key.
        for key in ("videos", "items", "cards", "data", "results", "entries", "videolist"):
            v = data.get(key)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        yield item
                return
        # Otherwise treat the dict itself as a single video.
        yield data


def _records_from_json(text: str, captured_at: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    # A package may carry its own capture date.
    if isinstance(data, dict):
        pkg_date = _normalize_date(
            data.get("date") or data.get("captured_at") or data.get("generated_at")
        )
        captured_at = pkg_date or captured_at
    return [_record_from_dict(d, captured_at) for d in _iter_video_dicts(data)]


# --------------------------------------------------------------------------- #
# File + merge orchestration
# --------------------------------------------------------------------------- #
def captured_date_for(path: Path) -> str:
    """Best-effort capture date for a package file."""
    for part in (path.name, *[p.name for p in path.parents]):
        m = _DATE_IN_NAME_RE.search(part)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        ts = path.stat().st_mtime
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return _dt.date.today().isoformat()


def parse_file(path: Path) -> list[dict[str, Any]]:
    captured_at = captured_date_for(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = path.suffix.lower()
    if suffix in (".json", ".jsonl"):
        if suffix == ".jsonl":
            records = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    records.extend(_records_from_json(line, captured_at))
            return records
        return _records_from_json(text, captured_at)
    if suffix in (".md", ".markdown", ".txt"):
        return _records_from_markdown(text, captured_at)
    return []


def _merge_record(into: dict[str, Any], other: dict[str, Any]) -> None:
    """Merge ``other`` into ``into`` (same video + same day), keeping best data."""
    for field in ("title", "author", "topic", "url", "summary", "published_at"):
        cur = into.get(field)
        new = other.get(field)
        # Prefer a longer / more specific non-placeholder value.
        placeholders = ("", "Без названия", "Неизвестный автор", "Без темы", None)
        if (cur in placeholders) and new not in placeholders:
            into[field] = new
        elif new not in placeholders and cur not in placeholders and \
                len(str(new)) > len(str(cur)):
            into[field] = new
    for field in ("tags", "ideas"):
        merged = list(dict.fromkeys([*into.get(field, []), *other.get(field, [])]))
        into[field] = merged
    metrics = {**other.get("metrics", {}), **into.get("metrics", {})}
    # Prefer the larger value when both present (metrics only grow over a day).
    for k, v in other.get("metrics", {}).items():
        if k in into.get("metrics", {}):
            metrics[k] = max(into["metrics"][k], v)
    into["metrics"] = metrics


def parse_directory(root: Path, pattern: str = "daily_package") -> list[dict[str, Any]]:
    """Parse every matching package file under ``root`` and merge duplicates.

    Files are matched if ``pattern`` appears in their name (case-insensitive).
    Records sharing the same ``(video_id, captured_at)`` are merged so the same
    video described in both ``.md`` and ``.json`` becomes one rich record.
    """
    exts = (".json", ".jsonl", ".md", ".markdown", ".txt")
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in exts
        and pattern.lower() in p.name.lower()
    )
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for path in files:
        for rec in parse_file(path):
            key = (rec["video_id"], rec["captured_at"])
            if key in merged:
                _merge_record(merged[key], rec)
            else:
                merged[key] = rec
    return list(merged.values())

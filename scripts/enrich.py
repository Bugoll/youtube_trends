#!/usr/bin/env python3
"""Incremental LLM enrichment for video entries with missing or weak metadata.

Supports two backends — selected automatically:
  1. Anthropic Claude (cloud)  — requires ANTHROPIC_API_KEY
  2. Ollama (local, no API key) — requires Ollama running locally

Backend priority:
  ANTHROPIC_API_KEY set → use Claude (claude-haiku-4-5)
  OLLAMA_MODEL set OR Ollama reachable → use Ollama
  Neither → skip silently

Enriches:
  1. macro_theme for videos where keyword matching returns "Разное".
  2. summary for videos that have no summary.

Runs incrementally — only processes videos not yet enriched (or enriched
more than REENRICH_AFTER_DAYS ago). Idempotent — safe to run repeatedly.

Usage:
    python scripts/enrich.py --out docs/data
    python scripts/enrich.py --out docs/data --limit 50
    python scripts/enrich.py --out docs/data --force
    OLLAMA_MODEL=llama3 python scripts/enrich.py --out docs/data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MACRO_THEMES = [
    "AI", "Финансы", "Геополитика", "Психология",
    "Бизнес", "Наука", "Технологии", "Личностный рост", "Разное",
]
DEFAULT_THEME = "Разное"
REENRICH_AFTER_DAYS = 60
ANTHROPIC_MODEL = "claude-haiku-4-5"
OLLAMA_DEFAULT_MODEL = "llama3"
OLLAMA_DEFAULT_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

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
# Enrichment helpers
# ---------------------------------------------------------------------------

def _needs_enrichment(entry: dict, force: bool) -> tuple[bool, bool]:
    """Return (needs_theme, needs_summary)."""
    if not force:
        enriched_at = entry.get("_enriched_at", "")
        if enriched_at:
            try:
                days = (dt.date.today() - dt.date.fromisoformat(enriched_at)).days
                if days < REENRICH_AFTER_DAYS:
                    return False, False
            except ValueError:
                pass
    needs_theme = not entry.get("_macro_theme") or entry["_macro_theme"] == DEFAULT_THEME
    needs_summary = not (entry.get("summary") or "").strip()
    return needs_theme, needs_summary


def _theme_prompt(entry: dict) -> str:
    ideas = "; ".join((entry.get("ideas") or [])[:4])
    tags = ", ".join((entry.get("tags") or [])[:6])
    themes = ", ".join(MACRO_THEMES)
    return (
        f"Classify this YouTube video into exactly one macro-theme.\n"
        f"Title: {entry.get('title', '')}\n"
        f"Topic: {entry.get('topic', '')}\n"
        f"Ideas: {ideas}\n"
        f"Tags: {tags}\n\n"
        f"Available themes: {themes}\n\n"
        f"Reply with ONLY the theme name from the list. Nothing else."
    )


def _summary_prompt(entry: dict) -> str:
    ideas = "; ".join((entry.get("ideas") or [])[:5])
    kp = "; ".join((entry.get("key_points") or [])[:3])
    return (
        f"Write a 2-sentence summary of this YouTube video.\n"
        f"Title: {entry.get('title', '')}\n"
        f"Topic: {entry.get('topic', '')}\n"
        f"Key ideas: {ideas}\n"
        f"Key points: {kp}\n\n"
        f"Be concise and factual. Reply with ONLY the summary text."
    )


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicBackend:
    def __init__(self, api_key: str) -> None:
        import anthropic  # type: ignore[import]
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, prompt: str, max_tokens: int) -> str:
        resp = self._client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    @property
    def name(self) -> str:
        return ANTHROPIC_MODEL


# ---------------------------------------------------------------------------
# Ollama backend (stdlib only — no extra packages needed)
# ---------------------------------------------------------------------------

class OllamaBackend:
    """Calls Ollama's /api/generate endpoint via stdlib urllib."""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def complete(self, prompt: str, max_tokens: int) -> str:  # noqa: ARG002
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.0},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError(
                    f"Ollama model '{self._model}' not found. "
                    f"Pull it first: ollama pull {self._model}"
                ) from exc
            raise
        return body.get("response", "").strip()

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"


def _ollama_reachable(base_url: str) -> bool:
    """Return True if Ollama HTTP server responds at base_url."""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _ollama_model_available(base_url: str, model: str) -> bool:
    """Return True if model is already pulled in Ollama."""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        # Ollama names may include ":latest" suffix — match prefix
        return any(n == model or n.startswith(f"{model}:") for n in names)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def _build_backend() -> Any | None:
    """Return the best available backend or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            backend = AnthropicBackend(api_key)
            print(f"LLM backend: Anthropic ({ANTHROPIC_MODEL})")
            return backend
        except ImportError:
            print(
                "anthropic package not installed — falling back to Ollama.",
                file=sys.stderr,
            )

    base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_DEFAULT_URL)
    model = os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL)

    if not _ollama_reachable(base_url):
        print(
            f"Ollama not reachable at {base_url}. "
            "Start it with: ollama serve"
        )
        return None

    if not _ollama_model_available(base_url, model):
        print(
            f"Ollama model '{model}' not pulled. "
            f"Run: ollama pull {model}"
        )
        return None

    print(f"LLM backend: Ollama ({model} @ {base_url})")
    return OllamaBackend(base_url, model)

    print("No LLM backend available — skipping enrichment. "
          "Set ANTHROPIC_API_KEY or start Ollama.")
    return None


# ---------------------------------------------------------------------------
# Core enrichment logic
# ---------------------------------------------------------------------------

def _classify_theme(backend: Any, entry: dict) -> str | None:
    try:
        result = backend.complete(_theme_prompt(entry), max_tokens=20)
        return result if result in MACRO_THEMES else None
    except Exception as exc:
        print(f"  [warn] theme classification error: {exc}", file=sys.stderr)
        return None


def _generate_summary(backend: Any, entry: dict) -> str | None:
    try:
        result = backend.complete(_summary_prompt(entry), max_tokens=150)
        return result or None
    except Exception as exc:
        print(f"  [warn] summary generation error: {exc}", file=sys.stderr)
        return None


def enrich(history: dict[str, Any], limit: int, force: bool) -> int:
    backend = _build_backend()
    if backend is None:
        return 0

    today = dt.date.today().isoformat()

    candidates = [
        (entry, *_needs_enrichment(entry, force))
        for entry in history.values()
    ]
    candidates = [(e, t, s) for e, t, s in candidates if t or s]
    # Prioritise videos missing both fields; cap at limit
    candidates.sort(key=lambda x: -(int(x[1]) + int(x[2])))
    candidates = candidates[:limit]

    if not candidates:
        print("All videos already enriched — nothing to do.")
        return 0

    print(f"Enriching {len(candidates)} video(s) via {backend.name}…")
    updated = 0

    for entry, needs_theme, needs_summary in candidates:
        vid = entry.get("video_id", "?")
        changed = False

        if needs_theme:
            theme = _classify_theme(backend, entry)
            if theme and theme != DEFAULT_THEME:
                entry["_macro_theme"] = theme
                changed = True
                print(f"  {vid}: theme → {theme}")

        if needs_summary:
            summary = _generate_summary(backend, entry)
            if summary:
                entry["summary"] = summary
                changed = True
                print(f"  {vid}: summary generated ({len(summary)} chars)")

        if changed:
            entry["_enriched_at"] = today
            updated += 1

    print(f"Enriched {updated}/{len(candidates)} video(s).")
    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/data")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    history_path = Path(args.out) / "history.json"
    history = _load_json(history_path, {})
    if not history:
        print("history.json is empty or missing.")
        return 0

    updated = enrich(history, args.limit, args.force)
    if updated:
        _save_json(history_path, history)
        print(f"Saved history.json ({updated} entries updated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

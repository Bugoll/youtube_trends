#!/usr/bin/env python3
"""Fetch all content files from the source repo's daily folder into this repo.

Every subfolder of the source directory (default ``youtube-daily``) is scanned
recursively.  All supported files (``.json``, ``.jsonl``, ``.md``, ``.markdown``,
``.txt``) are copied into ``data/raw/``.

To avoid filename collisions across days, the **date is extracted from the
nearest ancestor folder** that contains a date pattern (``YYYY-MM-DD``,
``YYYYMMDD``, etc.) and prepended to the filename:

    youtube-daily/
      2026-06-07/
        notes.md          →  data/raw/2026-06-07__notes.md
        video_data.json   →  data/raw/2026-06-07__video_data.json
      2026-06-06/
        analysis.md       →  data/raw/2026-06-06__analysis.md

Files that already exist in the destination with the same name are overwritten
(idempotent daily runs).

The source repo is private, so a Personal Access Token is required. The token
is read from ``MYCRABS_TOKEN`` (or ``GITHUB_TOKEN``) and used to clone over
HTTPS.

Environment variables (all optional, sane defaults):
    MYCRABS_TOKEN   PAT with read access to the source repo
    MYCRABS_REPO    "owner/name"  (default: bugoll/MyCrabs)
    MYCRABS_BRANCH  branch to read (default: main, falls back to master)
    SOURCE_DIR      folder within the source repo (default: youtube-daily)

Usage:
    python scripts/fetch_packages.py --dest data/raw
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXTS = (".json", ".jsonl", ".md", ".markdown", ".txt")

_DATE_RE = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _date_from_name(name: str) -> str | None:
    """Return 'YYYY-MM-DD' if the string contains a recognisable date, else None."""
    m = _DATE_RE.search(name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _date_prefix_for(path: Path, src_root: Path) -> str | None:
    """Return a YYYY-MM-DD date for this file.

    Search order:
      1. Ancestor folder names (innermost first) — covers files inside date dirs.
      2. The filename itself — covers files at the source root whose name
         contains the date (e.g. ``final_intelligence_report_2026-06-03.md``).
    """
    try:
        rel_parts = path.relative_to(src_root).parts[:-1]  # exclude filename
    except ValueError:
        return None
    for part in reversed(rel_parts):
        d = _date_from_name(part)
        if d:
            return d
    # Fallback: date embedded in the filename itself.
    return _date_from_name(path.name)


def clone_source(workdir: Path) -> Path:
    repo = os.environ.get("MYCRABS_REPO", "bugoll/MyCrabs")
    branch = os.environ.get("MYCRABS_BRANCH", "main")
    token = os.environ.get("MYCRABS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "ERROR: no token found. Set MYCRABS_TOKEN (a PAT with read access "
            "to the private source repo) in the environment / repo secrets."
        )

    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    target = workdir / "source"
    for br in (branch, "master", "main"):
        try:
            _run(["git", "clone", "--depth", "1", "--branch", br, url, str(target)])
            print(f"Cloned {repo}@{br}")
            return target
        except subprocess.CalledProcessError:
            if target.exists():
                shutil.rmtree(target)
            continue
    raise SystemExit(f"ERROR: could not clone {repo} (checked branches: {branch}/master/main)")


def copy_packages(source_root: Path, dest: Path) -> int:
    source_dir = os.environ.get("SOURCE_DIR", "youtube-daily")
    src = source_root / source_dir

    if not src.exists():
        # Be forgiving about the exact folder name.
        candidates = [
            p for p in source_root.rglob("*")
            if p.is_dir() and "youtube" in p.name.lower() and "daily" in p.name.lower()
        ]
        if candidates:
            src = candidates[0]
            print(f"Using source folder: {src.relative_to(source_root)}")
        else:
            print(f"WARNING: source folder '{source_dir}' not found; scanning whole repo.")
            src = source_root

    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0

    # Support multiple comma-separated patterns, e.g. "daily_package,final_intelligence_report"
    raw_patterns = os.environ.get("PACKAGE_GLOB", "daily_package,final_intelligence_report")
    patterns = [p.strip().lower() for p in raw_patterns.split(",") if p.strip()]

    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        name_lower = path.name.lower()
        if not any(pat in name_lower for pat in patterns):
            continue

        date = _date_prefix_for(path, src)
        if date:
            flat_name = f"{date}__{path.name}"
        else:
            rel = path.relative_to(src)
            flat_name = "__".join(rel.parts)

        dest_file = dest / flat_name
        shutil.copy2(path, dest_file)
        copied += 1
        print(f"  {path.relative_to(source_root)}  →  {flat_name}")

    if skipped:
        print(f"Skipped {skipped} file(s) (unsupported extension).")
    print(f"Copied {copied} file(s) into {dest}")
    return copied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default="data/raw", help="destination folder")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        source_root = clone_source(Path(tmp))
        copied = copy_packages(source_root, Path(args.dest))

    if copied == 0:
        print("No files found — check SOURCE_DIR and MYCRABS_REPO.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

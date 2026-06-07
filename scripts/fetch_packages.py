#!/usr/bin/env python3
"""Fetch ``daily_package`` files from the source repo into this repo.

Every day the ``youtube-daily`` folder of the source repository
(default ``bugoll/MyCrabs``) is scanned for files whose name contains
``daily_package`` (any of ``.json``, ``.jsonl``, ``.md``, ``.markdown``,
``.txt``). Matching files are copied into ``data/raw/`` so they are archived in
this repository and become the input for ``process.py``.

The source repo is private, so a Personal Access Token is required. The token
is read from ``MYCRABS_TOKEN`` (or ``GITHUB_TOKEN``) and used to clone over
HTTPS. Nothing is printed that would leak the token.

Environment variables (all optional, sane defaults):
    MYCRABS_TOKEN   PAT with read access to the source repo
    MYCRABS_REPO    "owner/name"  (default: bugoll/MyCrabs)
    MYCRABS_BRANCH  branch to read (default: main, falls back to master)
    SOURCE_DIR      folder within the source repo (default: youtube-daily)
    PACKAGE_GLOB    filename substring to match (default: daily_package)

Usage:
    python scripts/fetch_packages.py --dest data/raw
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXTS = (".json", ".jsonl", ".md", ".markdown", ".txt")


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def clone_source(workdir: Path) -> Path:
    repo = os.environ.get("MYCRABS_REPO", "bugoll/MyCrabs")
    branch = os.environ.get("MYCRABS_BRANCH", "main")
    token = os.environ.get("MYCRABS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "ERROR: no token found. Set MYCRABS_TOKEN (a PAT with read access "
            "to the private source repo) in the environment / repo secrets."
        )

    # Token is embedded only in the local subprocess argument, never logged.
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
    pattern = os.environ.get("PACKAGE_GLOB", "daily_package").lower()
    src = source_root / source_dir
    if not src.exists():
        # Be forgiving about the exact folder name.
        candidates = [p for p in source_root.rglob("*")
                      if p.is_dir() and "youtube" in p.name.lower() and "daily" in p.name.lower()]
        if candidates:
            src = candidates[0]
        else:
            print(f"WARNING: source folder '{source_dir}' not found in repo; "
                  f"scanning whole repo for '{pattern}' files.")
            src = source_root

    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        if pattern not in path.name.lower():
            continue
        # Preserve any date subfolders to avoid name collisions across days.
        rel = path.relative_to(src)
        flat_name = "__".join(rel.parts)
        shutil.copy2(path, dest / flat_name)
        copied += 1
    print(f"Copied {copied} package file(s) into {dest}")
    return copied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default="data/raw", help="destination folder")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        source_root = clone_source(Path(tmp))
        copied = copy_packages(source_root, Path(args.dest))

    if copied == 0:
        print("No package files matched — nothing to update.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

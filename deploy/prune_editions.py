#!/usr/bin/env python3
"""Deletes extracted editions (raw PDF + bronze + gold + their trace DB
rows) older than --days, and Phase 1 cache entries older than --days
independently of any edition. Runs on the VM only - see
pressdigest-prune.timer.

Age is the directory's own mtime (when it was last written to by the
pipeline), not the newspaper's own publication date in the {date} path
segment - a user who uploads a months-old back issue today should still
get to read it for the normal retention window, not have it pruned on
the very next run because its own filename looks old.

The disk this runs against (30GB pd-standard, minus a 2GB swapfile) is
the actual constraint being managed here: uploaded PDFs run 10-40MB each,
plus bronze/gold text and the Gemini response cache per edition - without
this, disk fills eventually on a machine nobody is watching day to day.

Edition deletion itself is delegated to hindu_extract.delete_edition -
the same function the interactive DELETE /api/editions/{id} endpoint
uses - specifically so this script and that endpoint cannot drift apart
on what "delete an edition" means (e.g. both now also clean up the
edition's trace DB rows, which this script never did before, and both
are equally careful to never touch the Gemini/ranking response caches -
see delete_edition.py's module docstring for why).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path


def _is_stale(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _edition_date_pairs(data_root: Path) -> set[tuple[str, str]]:
    """Union of every (edition, date) directory found under raw/bronze/gold
    - a partial extraction (e.g. raw uploaded but the job never finished)
    still needs to be considered for pruning, not just fully-succeeded
    editions."""
    pairs: set[tuple[str, str]] = set()
    for root_name in ("raw", "bronze", "gold"):
        root = data_root / root_name
        if not root.is_dir():
            continue
        for edition_dir in root.iterdir():
            if not edition_dir.is_dir():
                continue
            for date_dir in edition_dir.iterdir():
                if date_dir.is_dir():
                    pairs.add((edition_dir.name, date_dir.name))
    return pairs


def _last_touched(data_root: Path, edition: str, date: str) -> float | None:
    """The most recent mtime among this edition's raw/bronze/gold dirs -
    "stale" means nothing here has been touched recently, not that every
    one of the three independently aged out (they're written together by
    one job in practice, so this rarely matters, but it's the more
    conservative reading when it does)."""
    mtimes = []
    for root_name in ("raw", "bronze", "gold"):
        path = data_root / root_name / edition / date
        try:
            mtimes.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def _prune_editions(config, data_root: Path, cutoff: float, dry_run: bool) -> list[tuple[str, str]]:
    from hindu_extract.delete_edition import delete_edition

    removed = []
    for edition, date in sorted(_edition_date_pairs(data_root)):
        last_touched = _last_touched(data_root, edition, date)
        if last_touched is None or last_touched >= cutoff:
            continue
        print(f"[edition] {'would remove' if dry_run else 'removing'}: {edition}/{date}")
        if not dry_run:
            result = delete_edition(config, edition, date)
            print(f"    freed {result.bytes_freed / 1e6:.1f}MB")
        removed.append((edition, date))
    return removed


def _prune_cache_dirs(cache_root: Path, cutoff: float, dry_run: bool) -> list[Path]:
    """cache_root/{pdf_hash}/{version_hash}/page_NN - content-addressed,
    not edition/date-keyed, so pruned purely by its own mtime rather than
    tied to the edition pass above. Fully re-derivable (re-running
    `extract` regenerates it), so deleting an unreferenced entry is never
    lossy - unlike the edition dirs above, which hold the only copy of
    what Gemini returned and what was extracted. Explicitly skips both
    "gemini" and "ranking" - config/default.yaml nests gemini_cache_root
    and ranking_cache_root under this same cache_root, and both are
    content-addressed caches that must survive an edition delete/prune
    for exactly the reason delete_edition.py documents (a same-input
    re-extract must stay free/instant). Skipping only "gemini" here used
    to leave "ranking" exposed to being deleted as if it were one stale
    pdf_hash dir - fixed here rather than left as a latent gap."""
    removed = []
    if not cache_root.is_dir():
        return removed
    for pdf_hash_dir in sorted(cache_root.iterdir()):
        if not pdf_hash_dir.is_dir() or pdf_hash_dir.name in ("gemini", "ranking"):
            continue
        if _is_stale(pdf_hash_dir, cutoff):
            print(f"[cache] {'would remove' if dry_run else 'removing'}: {pdf_hash_dir}")
            if not dry_run:
                shutil.rmtree(pdf_hash_dir, ignore_errors=True)
            removed.append(pdf_hash_dir)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True, help="e.g. /var/lib/pressdigest/data")
    parser.add_argument("--days", type=int, default=30, help="retention window in days (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be removed, remove nothing")
    args = parser.parse_args()

    if not args.data_root.is_dir():
        print(f"data root {args.data_root} does not exist - nothing to prune", file=sys.stderr)
        return 0

    # data_root is always {HINDU_EXTRACT_DATA_ROOT}/data by convention (see
    # deploy/pressdigest.service and pressdigest-prune.service) - setting
    # this env var before load_config() is what makes config.data_anchor
    # (and therefore delete_edition's own path resolution/safety check)
    # agree with the --data-root this script was actually given, without
    # this script needing its own separate copy of that path-joining logic.
    os.environ["HINDU_EXTRACT_DATA_ROOT"] = str(args.data_root.parent.resolve())
    from hindu_extract.config import load_config

    config = load_config()

    cutoff = time.time() - args.days * 86400
    editions_removed = _prune_editions(config, args.data_root, cutoff, args.dry_run)
    cache_removed = _prune_cache_dirs(args.data_root / "cache", cutoff, args.dry_run)

    usage = shutil.disk_usage(args.data_root)
    print(
        f"done: {len(editions_removed)} edition(s), {len(cache_removed)} cache dir(s) "
        f"{'would be ' if args.dry_run else ''}removed (retention={args.days}d). "
        f"disk free={usage.free / 1e9:.1f}GB used={usage.used / 1e9:.1f}GB total={usage.total / 1e9:.1f}GB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

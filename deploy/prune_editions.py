#!/usr/bin/env python3
"""Deletes extracted editions (raw PDF + bronze + gold) older than
--days, and Phase 1/Gemini cache entries older than --days independently
of any edition. Runs on the VM only - see pressdigest-prune.timer.

Age is the directory's own mtime (when it was last written to by the
pipeline), not the newspaper's own publication date in the {date} path
segment - a user who uploads a months-old back issue today should still
get to read it for the normal retention window, not have it pruned on
the very next run because its own filename looks old.

The disk this runs against (30GB pd-standard, minus a 2GB swapfile) is
the actual constraint being managed here: uploaded PDFs run 10-40MB each,
plus bronze/gold text and the Gemini response cache per edition - without
this, disk fills eventually on a machine nobody is watching day to day.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path


def _is_stale(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _prune_edition_dirs(root: Path, cutoff: float, label: str, dry_run: bool) -> list[Path]:
    """root looks like data/bronze, data/gold, or data/raw - each holds
    {edition}/{date}/ two levels deep."""
    removed = []
    if not root.is_dir():
        return removed
    for edition_dir in sorted(root.iterdir()):
        if not edition_dir.is_dir():
            continue
        for date_dir in sorted(edition_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            if _is_stale(date_dir, cutoff):
                print(f"[{label}] {'would remove' if dry_run else 'removing'}: {date_dir}")
                if not dry_run:
                    shutil.rmtree(date_dir, ignore_errors=True)
                removed.append(date_dir)
        if not dry_run and edition_dir.is_dir() and not any(edition_dir.iterdir()):
            edition_dir.rmdir()
    return removed


def _prune_cache_dirs(cache_root: Path, cutoff: float, dry_run: bool) -> list[Path]:
    """cache_root/{pdf_hash}/{version_hash}/page_NN - content-addressed,
    not edition/date-keyed, so pruned purely by its own mtime rather than
    tied to the edition pass above. Fully re-derivable (re-running
    `extract` regenerates it), so deleting an unreferenced entry is never
    lossy - unlike the edition dirs above, which hold the only copy of
    what Gemini returned and what was extracted."""
    removed = []
    if not cache_root.is_dir():
        return removed
    for pdf_hash_dir in sorted(cache_root.iterdir()):
        if not pdf_hash_dir.is_dir() or pdf_hash_dir.name == "gemini":
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

    cutoff = time.time() - args.days * 86400
    total = 0
    total += len(_prune_edition_dirs(args.data_root / "raw", cutoff, "raw", args.dry_run))
    total += len(_prune_edition_dirs(args.data_root / "bronze", cutoff, "bronze", args.dry_run))
    total += len(_prune_edition_dirs(args.data_root / "gold", cutoff, "gold", args.dry_run))
    total += len(_prune_cache_dirs(args.data_root / "cache", cutoff, args.dry_run))

    usage = shutil.disk_usage(args.data_root)
    print(
        f"done: {total} stale dir(s) {'would be ' if args.dry_run else ''}removed "
        f"(retention={args.days}d). disk free={usage.free / 1e9:.1f}GB "
        f"used={usage.used / 1e9:.1f}GB total={usage.total / 1e9:.1f}GB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

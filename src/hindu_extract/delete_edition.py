"""Deletes everything belonging to one extracted edition+date: the stored
source PDF, bronze, gold, and the trace DB rows for its runs. Shared by
both the interactive DELETE /api/editions/{id} endpoint and
deploy/prune_editions.py's timer-driven retention sweep, so the two paths
cannot drift apart on what "delete an edition" means.

Deliberately NEVER touches config.gemini_cache_root or
config.ranking_cache_root: both are content-addressed (keyed by a hash of
the prompt/model/settings, not by edition/date - see gemini_client.py and
ranking.py), so they're shared across editions and safe to outlive any one
of them. Deleting them here would silently turn a future re-extract of the
exact same PDF from a free, instant cache hit into ~21 live Gemini calls
per edition - do not "clean this up" by including them in the paths this
module removes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hindu_extract import storage, trace
from hindu_extract.articles_pipeline import gold_edition_dir
from hindu_extract.config import Config


class UnsafePathError(RuntimeError):
    """Raised if a computed deletion target would resolve outside the
    configured data root - see _require_inside_data_root()."""


@dataclass(frozen=True)
class DeleteResult:
    edition: str
    date: str
    bytes_freed: int


def _data_root(config: Config) -> Path:
    return (config.data_anchor / "data").resolve()


def _require_inside_data_root(config: Config, path: Path) -> Path:
    """Resolves symlinks before checking - this runs as root via systemd
    on the VM (see design/DESIGN.md "Deployment: GCP e2-micro VM"), so a
    symlink pointing outside data/ must not be trusted at face value."""
    data_root = _data_root(config)
    resolved = path.resolve()
    if resolved != data_root and data_root not in resolved.parents:
        raise UnsafePathError(f"refusing to delete {resolved} - outside data root {data_root}")
    return resolved


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _rmtree_safely(config: Config, path: Path) -> int:
    # Checked before the existence check, deliberately - a bogus
    # edition/date containing ".." must be refused outright, not silently
    # allowed to slip through just because nothing happens to exist at the
    # (unsafe) resolved location yet.
    _require_inside_data_root(config, path)
    if not path.exists():
        return 0
    size = _dir_size(path)
    import shutil

    shutil.rmtree(path, ignore_errors=True)
    return size


def delete_edition(config: Config, edition: str, date: str) -> DeleteResult:
    """Unconditional - the caller is responsible for deciding whether
    deletion is currently safe (see api/main.py's DELETE route, which
    checks jobs.get_active_job_for_edition and blocks with a 409 before
    ever calling this). That check isn't repeated in here because it can
    only be meaningful from inside the same process as the job registry:
    prune_editions.py runs as its own separate process/script (via a
    systemd timer) with no access to the live API process's in-memory
    _JOBS at all, and in practice only ever targets editions stale by
    weeks, never a live job's edition - see design/DESIGN.md.

    Blocks rather than cancels an in-progress run at the API layer,
    because there is no cooperative cancellation for a job's background
    thread today (see jobs.py's module docstring - in-memory/simple was a
    deliberate v1 choice), and interrupting a blocking Gemini call
    mid-write is a meaningfully bigger feature than this one.
    """
    bytes_freed = 0
    bytes_freed += _rmtree_safely(config, storage.raw_pdf_path(config, edition, date).parent)
    bytes_freed += _rmtree_safely(config, storage.bronze_edition_dir(config, edition, date))
    bytes_freed += _rmtree_safely(config, gold_edition_dir(config, edition, date))
    trace.delete_runs_for_edition(config.trace_db, edition, date)

    return DeleteResult(edition=edition, date=date, bytes_freed=bytes_freed)

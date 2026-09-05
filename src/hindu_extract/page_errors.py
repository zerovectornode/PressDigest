"""Durable per-page failure record: data/gold/{edition}/{date}/page_NN/error.json.

Why gold, not bronze: gold is where both Phase 1 and Phase 2 failures need
to be visible from (a Phase 1 canary hard-fail never reaches Phase 2 at
all, but still needs a durable "failed" status the reader can show), and
gold_page_dir only needs edition/date/page_num, not a prior successful
Phase 1 run - so writing here doesn't require bronze to exist.

This is a small leaf module (no imports from jobs.py, articles_pipeline.py,
or editions.py) specifically so both the writer (jobs.py, during a run) and
the reader (editions.py's get_page_status, used long after a run ends) can
import it without a cycle - jobs.py already reuses articles_pipeline.py's
gold_page_dir, and editions.py already imports jobs.py.

A page's `error.json` and `articles.json` are mutually exclusive in
practice: a successful (re)run writes articles.json FIRST and only then
clears error.json (see jobs.py's on_page_done/retry_page_sync - neither
clears a page's error up front, before attempting it, specifically so a
process crash mid-attempt leaves the previous, still-accurate error.json
in place rather than a gap where the page looks merely "pending"), and a
failing run never gets far enough to write articles.json. Nothing here
enforces that invariant beyond both call sites doing it in the right
order. Regardless of that ordering, editions.get_page_status checks
_has_gold before ever looking at error.json, so even a stale error.json
that somehow outlives a successful write (e.g. a crash between the two
steps) is not read as "failed" - "done" always wins once gold exists.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from hindu_extract.config import Config


def gold_page_dir(config: Config, edition: str, date: str, page_num: int) -> Path:
    return config.gold_root / edition / date / f"page_{page_num:02d}"


@dataclass(frozen=True)
class PageError:
    stage: str  # e.g. "char_extraction", "ligature_canary", "gemini_call", "validation", "assembly"
    code: str | int | None
    message: str
    attempt_count: int
    # Reuses gemini_client.is_retryable's judgment on the exact exception
    # that caused this failure - not a second, independently-maintained
    # severity taxonomy. False for anything that isn't a recognized
    # transient transport/server error (e.g. a Phase 1 EmptyPageError, a
    # non-429 ClientError, or GeminiError's MAX_TOKENS/empty-response/
    # quota-exhausted cases) - retrying those reproduces the same outcome.
    retryable: bool
    failed_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _error_path(config: Config, edition: str, date: str, page_num: int) -> Path:
    return gold_page_dir(config, edition, date, page_num) / "error.json"


def write_page_error(
    config: Config, edition: str, date: str, page_num: int, stage: str, code: str | int | None, message: str,
    attempt_count: int = 1, retryable: bool = False,
) -> None:
    error = PageError(
        stage=stage,
        code=code,
        message=message,
        attempt_count=attempt_count,
        retryable=retryable,
        failed_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _error_path(config, edition, date, page_num)
    path.parent.mkdir(parents=True, exist_ok=True)
    # write-temp -> rename, not a direct write_text - so a crash mid-write
    # never leaves a half-written error.json behind (os.replace is atomic
    # on both POSIX and Windows).
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(error.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def read_page_error(config: Config, edition: str, date: str, page_num: int) -> PageError | None:
    path = _error_path(config, edition, date, page_num)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return PageError(**data)


def clear_page_error(config: Config, edition: str, date: str, page_num: int) -> None:
    _error_path(config, edition, date, page_num).unlink(missing_ok=True)

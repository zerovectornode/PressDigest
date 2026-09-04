"""Structured, persisted, queryable instrumentation for every extraction
run - the data source for the Step D "Pipeline" monitoring view.

A "run" is one coherent user-facing extraction action - the API's
background job (Phase 1 + Phase 2/3 for a set of pages) or one CLI command
invocation - identified by run_id. Every stage of every page within that
run emits one event: start, end, duration, and stage-specific detail
(char count, token counts, checksum results, etc - see STAGE_NAMES).
Emitted via the RunTracer.stage() context manager so stage code itself
never contains timing/persistence logic; a stage that raises still writes
its event (with the exception message in `detail["error"]`) before
propagating, so a failed page is exactly as visible in the trace as a
successful one.

SQLite (not JSONL) so Step D's dashboard can query "every run", "this
run's per-page timeline", "today's request count" etc. directly rather
than scanning files. A single shared connection guarded by a lock, since
Step A2's concurrent page processing means multiple threads write events
for the same run simultaneously - SQLite serializes writes regardless, but
the lock avoids "database is locked" contention under load.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

STAGE_NAMES = (
    "char_extraction",
    "line_building",
    "ligature_canary",
    "gemini_call",
    "validation",
    "assembly",
    # Edition-wide, not per-page - recorded with page_num=0 as a sentinel
    # for "the whole edition" rather than adding a nullable column, since
    # every other stage genuinely is per-page. See ranking.py.
    "ranking",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    edition TEXT NOT NULL,
    date TEXT NOT NULL,
    pdf_hash TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_wall_clock_s REAL,
    total_tokens INTEGER,
    cache_hit_ratio REAL,
    status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS stage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    detail_json TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (run_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_events_run ON stage_events (run_id);
CREATE INDEX IF NOT EXISTS idx_stage_events_run_page ON stage_events (run_id, page_num);

CREATE TABLE IF NOT EXISTS gemini_raw (
    run_id TEXT NOT NULL,
    page_num INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    raw_response TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (run_id, page_num)
);
"""

_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection(db_path: Path) -> sqlite3.Connection:
    key = str(Path(db_path).resolve())
    conn = _connections.get(key)
    if conn is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _connections[key] = conn
    return conn


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class RunTracer:
    db_path: Path
    run_id: str
    # Optional, additive: called with (page_num, stage_name) right before a
    # stage begins - unlike the stage_events row written in stage()'s
    # finally block (only known once a stage *finishes*), this is the only
    # signal of a stage *starting*. Default None reproduces prior behavior
    # exactly; the API's background job runner is the only current user
    # (see jobs.py), for live per-page progress display. Never allowed to
    # break extraction - exceptions from it are swallowed.
    on_stage_start: Callable[[int, str], None] | None = None
    _start_time: float = field(default=0.0, repr=False)

    def start_run(self, edition: str, date: str, pdf_hash: str | None, page_count: int) -> None:
        self._start_time = time.time()
        conn = _get_connection(self.db_path)
        with _lock:
            conn.execute(
                "INSERT INTO runs (run_id, edition, date, pdf_hash, page_count, started_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (self.run_id, edition, date, pdf_hash, page_count, _now_iso()),
            )
            conn.commit()

    def finish_run(self, status: str) -> None:
        conn = _get_connection(self.db_path)
        with _lock:
            # Both stages represent a real Gemini call with a token cost and
            # a cache-hit/miss outcome - a ranking-only run (see ranking.py)
            # has no 'gemini_call' events at all, so excluding 'ranking'
            # here would always report 0 tokens for it.
            rows = conn.execute(
                "SELECT detail_json FROM stage_events WHERE run_id = ? AND stage IN ('gemini_call', 'ranking')",
                (self.run_id,),
            ).fetchall()
            total_tokens = 0
            cache_hits = 0
            for row in rows:
                detail = json.loads(row["detail_json"])
                total_tokens += detail.get("total_token_count") or 0
                if detail.get("cache_hit"):
                    cache_hits += 1
            cache_hit_ratio = (cache_hits / len(rows)) if rows else None

            conn.execute(
                "UPDATE runs SET finished_at = ?, total_wall_clock_s = ?, total_tokens = ?, "
                "cache_hit_ratio = ?, status = ? WHERE run_id = ?",
                (
                    _now_iso(),
                    time.time() - self._start_time,
                    total_tokens,
                    cache_hit_ratio,
                    status,
                    self.run_id,
                ),
            )
            conn.commit()

    @contextmanager
    def stage(self, page_num: int, stage_name: str):
        """Yields a mutable dict; fill it in with stage-specific fields
        before the `with` block ends. Always writes an event, even if the
        block raises - the error's str() is stored on the event so a
        failed page is fully visible in the trace, not silently missing."""
        if stage_name not in STAGE_NAMES:
            raise ValueError(f"unknown stage {stage_name!r} - must be one of {STAGE_NAMES}")
        if self.on_stage_start is not None:
            try:
                self.on_stage_start(page_num, stage_name)
            except Exception:  # noqa: BLE001 - UI plumbing must never break extraction
                pass
        detail: dict = {}
        started_at = _now_iso()
        start = time.time()
        error: str | None = None
        try:
            yield detail
        except Exception as e:  # noqa: BLE001 - re-raised after recording, never swallowed
            error = str(e)
            raise
        finally:
            duration = time.time() - start
            conn = _get_connection(self.db_path)
            with _lock:
                conn.execute(
                    "INSERT INTO stage_events (run_id, page_num, stage, started_at, ended_at, "
                    "duration_s, detail_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.run_id, page_num, stage_name, started_at, _now_iso(), duration, json.dumps(detail), error),
                )
                conn.commit()

    def record_gemini_raw(self, page_num: int, prompt: str, raw_response: str) -> None:
        conn = _get_connection(self.db_path)
        with _lock:
            conn.execute(
                "INSERT OR REPLACE INTO gemini_raw (run_id, page_num, prompt, raw_response, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.run_id, page_num, prompt, raw_response, _now_iso()),
            )
            conn.commit()


# --- Query helpers backing the Step D monitoring endpoints -----------------


def list_runs(db_path: Path) -> list[dict]:
    conn = _get_connection(db_path)
    rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_run(db_path: Path, run_id: str) -> dict | None:
    conn = _get_connection(db_path)
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_latest_run_for_edition(db_path: Path, edition: str, date: str) -> dict | None:
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM runs WHERE edition = ? AND date = ? ORDER BY started_at DESC LIMIT 1",
        (edition, date),
    ).fetchone()
    return dict(row) if row else None


def get_page_stages(db_path: Path, run_id: str, page_num: int) -> list[dict]:
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM stage_events WHERE run_id = ? AND page_num = ? ORDER BY started_at",
        (run_id, page_num),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json"))
        out.append(d)
    return out


def get_run_pages(db_path: Path, run_id: str) -> list[int]:
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT page_num FROM stage_events WHERE run_id = ? ORDER BY page_num", (run_id,)
    ).fetchall()
    return [r["page_num"] for r in rows]


def get_page_raw(db_path: Path, run_id: str, page_num: int) -> dict | None:
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM gemini_raw WHERE run_id = ? AND page_num = ?", (run_id, page_num)
    ).fetchone()
    return dict(row) if row else None


def get_quota_usage(db_path: Path, requests_per_day_limit: int, tokens_per_minute_limit: int) -> dict:
    """Approximates current quota consumption from the trace itself (no
    separate live counter to keep in sync): requests today = gemini_call
    events since local midnight UTC; tokens/minute = total_token_count
    summed over gemini_call events in the trailing 60s. Both are exact for
    calls this process traced, and are the only two calls TokenAwareLimiter
    itself needs to gate - so this reflects the same reality the limiter
    enforces, just observed after the fact instead of live in-process."""
    conn = _get_connection(db_path)
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    minute_ago = (now.timestamp() - 60.0)

    today_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM stage_events WHERE stage = 'gemini_call' AND started_at >= ?",
        (midnight,),
    ).fetchone()
    requests_today = today_rows["n"] if today_rows else 0

    recent_rows = conn.execute(
        "SELECT detail_json, started_at FROM stage_events WHERE stage = 'gemini_call'"
    ).fetchall()
    tokens_last_minute = 0
    for row in recent_rows:
        try:
            ts = datetime.fromisoformat(row["started_at"]).timestamp()
        except ValueError:
            continue
        if ts >= minute_ago:
            detail = json.loads(row["detail_json"])
            tokens_last_minute += detail.get("total_token_count") or 0

    return {
        "requests_today": requests_today,
        "requests_per_day_limit": requests_per_day_limit,
        "tokens_last_minute": tokens_last_minute,
        "tokens_per_minute_limit": tokens_per_minute_limit,
    }

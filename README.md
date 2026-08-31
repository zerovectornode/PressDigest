# PressDigest - newspaper e-paper extraction pipeline + reader

PressDigest extracts individual articles - headline, deck, byline, dateline,
body, captions - out of a newspaper e-paper PDF (built against and tested
on The Hindu's Delhi edition), plus a small FastAPI backend and React
reader/monitoring UI on top.

## A note on content and licensing

**This repository contains no newspaper content.** The code is licensed
under MIT (see [LICENSE](LICENSE)); that license covers the software only.
The pipeline is built for personal use against a PDF *you* are licensed to
access (e.g. a subscriber-only e-paper download) - it does not include, and
is not a way to obtain, any newspaper's copyrighted articles. `data/` (every
byte the pipeline produces from a PDF - extracted text, cached model
responses, rendered pages) and the source PDF itself are git-ignored and
have never been committed; see [.gitignore](.gitignore) and "Supplying a
PDF" below.

## How it works

1. **Phase 1 - stream-ordered lines.** pdfplumber exposes each character in
   the PDF's raw content-stream order. Newspaper layout software emits each
   story as one contiguous run in that stream, in true reading order,
   across all of its columns - verified directly against a real e-paper PDF
   (see [design/DESIGN.md](design/DESIGN.md) "Stream-order rebuild"). Phase
   1 groups that native stream into numbered lines, verbatim, with no
   reordering, no joining, no interpretation - just geometry and font
   metadata attached to each line. A geometric canary separately flags any
   character the PDF's text layer failed to extract.
2. **Phase 2 - the model finds boundaries, never writes text.** An LLM
   (Gemini) reads the line-numbered dump and returns only line-number
   *ranges* per article (`{start: 57, end: 196}`) plus a few checksum words
   copied from those lines - it never generates a single character of
   article text itself.
3. **Phase 3 - deterministic slicing + independent validation.** The
   claimed ranges are sliced directly out of Phase 1's own stored lines and
   joined with fixed rules (drop-cap fusion, end-of-line de-hyphenation) -
   the model's output is never trusted as content, only as a set of line
   numbers. Those numbers are then checked against Phase 1's own text: do
   the checksum words actually appear at the claimed boundary, is a body
   range internally contiguous (no headline-scale line stuck in the
   middle), do any two articles' ranges overlap. A handful of geometric
   boundary corrections (e.g. a body range that excludes its own drop-cap
   line - see `boundary_fixups.py`) are applied deterministically before
   validation, removing that failure mode from the model's responsibility
   entirely. A failure is recorded per-article as `needs_review`, never
   silently retried or masked.
4. **Trace layer.** Every extraction run emits structured, queryable
   events (SQLite) - per-run summary plus per-page-per-stage timing and
   detail across 7 stages, including the exact prompt and raw model
   response - see `trace.py` and the Pipeline monitoring view below.
5. **FastAPI backend + React frontend ("PressDigest").** Serves the
   extracted result and orchestrates extraction jobs; a page-by-page reader
   with PDF-aligned highlight overlays, and a "Pipeline" dashboard for
   inspecting run history, per-stage timing, token usage, and validation
   results.

See [design/DESIGN.md](design/DESIGN.md) for the full rationale, schemas,
and calibration notes, including the architecture history (an earlier
geometric-segmentation approach was replaced by the stream-ordering design
above after live testing showed it didn't hold up on real layouts).

## Install (pipeline + CLI)

```bash
pip install -e .
```

Requires Python >=3.11. Dependencies: pdfplumber, pypdfium2, Pillow, PyYAML,
click, google-genai, python-dotenv.

### Gemini API key (required for Phase 2)

Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (get one from
[Google AI Studio](https://aistudio.google.com/apikey)). `.env` is
git-ignored. Phase 1 (`extract`, `survey`, `render-hires`, `debug-overlay`)
does not need a key at all.

## Supplying a PDF

This repo does not and will not include a newspaper PDF - `docs/` is
git-ignored except for a `.gitkeep` placeholder. To run the pipeline or the
PDF-dependent tests, supply your own e-paper PDF (one you're licensed to
access) at `docs/Newspaper.pdf`. Every path/date/edition value used
throughout is a CLI argument or config value, not hardcoded to any specific
publication - the code has just been built and tested against The Hindu's
Delhi edition.

## Usage

```bash
# Extract every page of an edition into the bronze layer
hindu-extract extract docs/Newspaper.pdf --date 2025-09-13 --edition delhi

# Just a subset of pages
hindu-extract extract docs/Newspaper.pdf --date 2025-09-13 --edition delhi --pages 1,2,5-8

# Force re-extraction, bypassing the cache
hindu-extract extract docs/Newspaper.pdf --date 2025-09-13 --edition delhi --force

# Cross-page survey table (run `extract` first)
hindu-extract survey --date 2025-09-13 --edition delhi

# On-demand debug tools (not persisted by `extract`)
hindu-extract render-hires docs/Newspaper.pdf --page 5 --out page5_hires.png
hindu-extract debug-overlay docs/Newspaper.pdf --page 1 --out page1_overlay.png

# Phase 2 + 3: group Phase 1's output into articles (run `extract` first).
# Runs pages concurrently through a token-aware rate limiter (rate_limit.py)
# rather than a fixed worker count - see config/default.yaml "concurrency".
hindu-extract articles --date 2025-09-13 --edition delhi

# Just a subset of pages
hindu-extract articles --date 2025-09-13 --edition delhi --pages 1,5,11

# Bypass the Gemini response cache and force fresh API calls
hindu-extract articles --date 2025-09-13 --edition delhi --no-cache
```

`extract` exits non-zero and prints every finding if the geometric glyph-drop
canary flags anything (see design/DESIGN.md) or if any page produces zero
lines. Run it as a build gate before letting Phase 2 consume a new edition.

`articles` prints, per page: articles found, excluded lines, coverage
(what fraction of lines fall inside some article - not a failure signal by
itself, see design/DESIGN.md), validation result, and tokens used (marked
`(cached)` when served from the Gemini response cache instead of a fresh
API call). An article is marked `needs_review` in its gold JSON if a
checksum, contiguity, or overlap check fails, or confidence is `"low"` -
the pipeline keeps going rather than crashing (see design/DESIGN.md
"Stream-order rebuild").

## Output layout

```
data/bronze/{edition}/{date}/page_{NN}/page.json   lines + page metadata
data/bronze/{edition}/{date}/manifest.json         edition-level summary
data/cache/{pdf_hash}/{version_hash}/page_{NN}/    cache keyed on PDF content
                                                     + pipeline_version, so
                                                     re-running an unchanged
                                                     page is a no-op
data/cache/gemini/{hash}.json                      Gemini response cache,
                                                     keyed on the line dump +
                                                     prompt_version + model_id
                                                     + thinking_level +
                                                     max_output_tokens
data/gold/{edition}/{date}/page_{NN}/articles.json articles + excluded_line_nos
                                                     + coverage/dehyphenation/
                                                     boundary-fixup audit info
data/gold/{edition}/{date}/page_{NN}/page.md       human-readable page markdown
data/gold/{edition}/{date}/edition.md              combined edition markdown
data/trace/trace.db                                SQLite trace of every run
                                                     (see "Pipeline" below)
```

All of `data/` is git-ignored - it is entirely derived from whatever PDF
you supply.

## PressDigest app (backend + frontend)

### Backend

```bash
pip install -e ".[api]"
uvicorn hindu_extract.api.main:app --reload --port 8000
```

Needs the same `.env`/`GEMINI_API_KEY` as the CLI's `articles` command for
the upload/extraction job endpoint; the read-only edition/page/run endpoints
don't need a key. See design/DESIGN.md "PressDigest: frontend + API" for
the full endpoint list and the edition-identity/job-system design.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens on http://localhost:5173 and proxies `/api/*` to the backend on port
8000 (see `frontend/vite.config.ts`) - run both at once for the app to
work end to end.

Two views once an edition is ingested:
- **Page Reader** (`/reader/{edition}/{page}`) - article stream on the
  left (headline, deck, byline/dateline, body, captions, confidence badge,
  needs-review marker, raw/cleaned toggle) with the source PDF on the right,
  one translucent highlight per article body fragment, bidirectional
  hover/click sync between the two panes.
- **Pipeline** (`/pipeline`) - a monitoring dashboard, not a reader: run
  list, a per-page-per-stage timeline, token breakdown against the 250K
  TPM / 500 RPD budget, a validation panel (every checksum/contiguity/
  overlap result, expandable to expected-vs-actual text), a raw inspector
  (exact prompt + model response per page), and a quota widget.

Whenever `hindu_extract/api/schemas.py` changes, regenerate the frontend's
TypeScript types (requires Node - `frontend`'s `json-schema-to-typescript`
devDependency does the conversion):

```bash
python scripts/generate_types.py
```

### Frontend tests

```bash
cd frontend
npm test
```

Includes a coordinate-mapping test that loads a real PDF (`docs/Newspaper.pdf`
- skipped if that file isn't present) and checks that both a headline bbox
and an ordinary body-line bbox land in the correct vertical position, not
mirrored - see design/DESIGN.md "Coordinate mapping" for why that's the
specific thing worth testing.

### Deploying

**This is a private, authenticated personal instance, not a public
service.** It serves The Hindu's verbatim text and PDF pages - see "A note
on content and licensing" above - so every deployment of it needs to sit
behind a login, not a bare public IP.

Current deployment target: a single GCP `e2-micro` VM (Debian 12, Always
Free tier), no Docker, no laptop in the loop at all - see `deploy/`
(systemd unit for uvicorn, a Caddy config with automatic TLS and
mandatory HTTP Basic auth on every route, a first-time `setup.sh`, an
idempotent `update.sh` you run on the VM itself for every update, and a
daily pruning timer), `.github/workflows/deploy.yml` (builds the frontend
and publishes everything the VM needs to a `deploy` branch on every push
to `main`), and design/DESIGN.md "Deployment: GCP e2-micro VM" for the
full reasoning (why the frontend is built in CI rather than on the VM or
a laptop, why `hindu_extract` is imported via `PYTHONPATH` rather than
pip-installed, why concurrency is turned down, the ownership model, and
the disk retention policy below).

A `Dockerfile` also still exists at the repo root (originally built for
Hugging Face Spaces, before HF discontinued free Docker Space hosting) -
it's a correct, self-contained way to run the app anywhere Docker *is*
available, kept as an alternative rather than deleted, but it is not what
the currently-deployed instance runs on.

**Data persists across deploys and restarts, but is pruned after a
retention window.** Extracted editions live at `/var/lib/pressdigest/data`
- a separate path from the application code, never touched by `update.sh`
- so a routine code update can never wipe an already-extracted edition
and a VM restart doesn't lose anything either. What does eventually
reclaim space is `deploy/prune_editions.py`, run daily: any edition (and
independently, any Gemini/Phase-1 cache entry) untouched for more than 30
days (configurable) is deleted, since the 30GB disk isn't unlimited and
uploaded e-paper PDFs run 10-40MB each.

## Configuration

All thresholds, paths, and the pipeline version live in
[config/default.yaml](config/default.yaml), never hardcoded - each
threshold's comment documents how it was calibrated. Pass
`--config path/to/other.yaml` to any command to override.

A couple of values are also environment-overridable on top of the YAML
(checked in `config.py`'s `load_config`, not a separate production config
file - see design/DESIGN.md "Deployment: GCP e2-micro VM" for why):

- `HINDU_EXTRACT_MAX_CONCURRENT` - overrides `concurrency.max_concurrent`
  (the e2-micro deployment sets this to 2, down from the default 4, as a
  safety margin against its shared-core CPU).
- `HINDU_EXTRACT_DATA_ROOT` - overrides where every `paths.*` value in
  `config/default.yaml` resolves relative to (default: the project root,
  same as always). The e2-micro deployment sets this to
  `/var/lib/pressdigest`, putting all pipeline output on a separate path
  from the application code without renaming a single key in the YAML.
- `LOG_LEVEL` - the API process's log level (`INFO` by default).

## Tests

```bash
pip install -e ".[dev]"
pytest
```

**Without a PDF at `docs/Newspaper.pdf`, 59 of 91 tests run and pass; the
remaining 32 skip cleanly** with a message explaining why (either "PDF not
found" or "set RUN_LIVE_TESTS=1"), rather than erroring. Every test that
needs the real PDF goes through one shared, session-scoped fixture
(`tests/conftest.py::pdf_path`), so supplying a PDF or not is a single,
predictable switch - drop your own licensed e-paper PDF at
`docs/Newspaper.pdf` to unlock the rest.

Test coverage:

- Geometric canary findings are only the known-benign `(cid:N)` decorative
  glyph category on every page - any other finding fails loudly
  (`test_all_pages.py`, needs a PDF)
- No page produces zero lines (`test_all_pages.py`, needs a PDF)
- Concatenating all of a page's lines (already in stream order by
  construction) matches raw pdfplumber extraction character-for-character
  after whitespace normalization (`test_all_pages.py`, needs a PDF)
- Per-page line/char counts against a committed regression baseline
  (`tests/baselines/page_counts.json` - counts only, no article text)
- Page-1-specific: drop-cap and stream-adjacency properties the whole
  rebuild depends on (`test_page1_specific.py`, needs a PDF)
- Phase 2 prompt/schema shape, Phase 3 checksum/contiguity/overlap
  validation (including the punctuation/hyphenation/drop-cap-omission
  tolerances found live and the negative cases that must still fail), and
  the deterministic drop-cap boundary fixup - all offline, synthetic
  fixtures, no PDF or API key needed (`test_gemini_prompt.py`,
  `test_phase3.py`, `test_assemble.py`, `test_boundary_fixups.py`)
- Token-aware concurrency limiter and Gemini client caching/429-backoff -
  offline, mocked (`test_rate_limit.py`, `test_gemini_client.py`)
- Trace/instrumentation layer, including a failed stage still writing its
  event - offline, throwaway SQLite file (`test_trace.py`)
- Live Phase 2/3 acceptance tests against the real API and a real PDF
  (`test_articles_live.py`) - skipped unless `RUN_LIVE_TESTS=1` is set (not
  gated on API-key presence alone, so a key can exist without a test run
  spending real quota): `RUN_LIVE_TESTS=1 pytest tests/test_articles_live.py`
- Backend API tests (`test_api.py`) - edition-identity/masthead parsing
  (needs a PDF), 404/400 error paths, and (gated on `RUN_LIVE_TESTS=1` and
  a PDF) a full upload -> job -> completion -> edition-listing test, plus
  the article/page/run/quota endpoints, against the real ingested edition
  (`pip install -e ".[api,dev]"` first)
- Frontend: `cd frontend && npm test` - component tests for the empty-state
  routes (asserting no fabricated data ever renders) and the
  coordinate-mapping fixture described above

A small synthetic fixture PDF (a couple of pages with made-up text in a
similar multi-column layout) would let the currently-skipped Phase-1/canary
tests run without a real newspaper PDF - flagging this as a worthwhile
follow-up rather than building it now.

## Measured results (first full 18-page run)

Delhi edition, 2025-09-13, `thinking_level=MEDIUM` (chosen after a live
A/B/C latency experiment against HIGH and LOW - see design/DESIGN.md),
concurrency=4 workers gated by a token-aware rate limiter:

- **89.7s wall-clock**, **219,587 tokens** total (~12.2K/page), 0% cache
  hit (first run, as expected)
- **107 articles** found across 18 pages; page 5 (a full-page
  advertisement) correctly produced 0 articles, not an error
- Checksum validation initially flagged 11/18 pages; three narrowly-scoped,
  tested tolerances (hyphenation-across-a-line-break, drop-cap-omission-in-
  the-checksum, trailing-punctuation) plus a deterministic geometric
  boundary fixup for excluded drop-cap lines brought that down to 5 pages
  with a remaining genuine or ambiguous checksum discrepancy - all detailed
  and auditable in each page's gold JSON, never silently discarded

No newspaper text from that run is included in this repository.

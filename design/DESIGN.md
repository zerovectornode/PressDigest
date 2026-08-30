# Design: The Hindu e-paper extraction pipeline

## Phase decomposition

**Phase 1 (this package) - verbatim span extraction.** Every text span in the
PDF is extracted with its geometry and font metadata, exactly as it appears
in the character stream. No reordering, no joining across lines, no
filtering, no interpretation. Anything ambiguous (an oversized glyph, a
suspicious gap, a font with no ToUnicode CMap) is recorded as metadata for
Phase 2 to decide, never resolved here. This is a deliberate constraint, not
an oversight: Phase 1's output is only trustworthy as ground truth if it
never makes a judgment call that could be wrong.

**Phase 2 (built) - LLM-based article assembly.** Gemini 3.1 Flash-Lite
reads a page's units (see "Line-granularity units" below - not raw spans)
and returns JSON that groups unit IDs into articles and orders them within
each article. Implemented in `units.py` (unit construction), `gemini_prompt.py`
(prompt + response schema), `gemini_client.py` (API call + cache), and
`grouping.py` (orchestrates the call + retry).

**Phase 3 (minimal, built) - partition verification.** Every unit ID must
appear exactly once across all articles and excluded_units - missing,
duplicated, and hallucinated IDs are all detected (`phase3.py`). One retry
citing the specific failure; if that also fails, a deterministic repair
runs (drop duplicates/hallucinations, file anything still missing into
excluded_units) and the page is marked `needs_review` rather than crashing
the run. Deferred to later, not built: LLM seam-continuity checking,
self-consistency runs across repeated calls, model escalation. See
"Metrics" below for what's measurable now versus still open.

### Why "the LLM emits IDs only" guarantees verbatim output

Phase 2's prompt gives the LLM a page's units (unit_id + text + geometry, see
below) and asks it to return unit IDs grouped into articles - IDs only,
never text (enforced by `RESPONSE_JSON_SCHEMA` in `gemini_prompt.py`: every
content-bearing field is either an array of ID strings or a closed enum;
none accepts free text). The final article text is assembled entirely by
`assemble.py` concatenating the original Phase 1 text in the order the LLM
specified, not the text the LLM wrote in its response. This means:

- The LLM cannot introduce a typo, paraphrase, or hallucinate a word -
  whatever ends up in an article's text is byte-for-byte Phase 1 text that
  was extracted from the PDF's real character stream.
- Verbatim fidelity is a structural property of the pipeline, not a
  probabilistic property of the model. A single wrong or invented character
  in the LLM's *reasoning* has no path into the output text; the worst it
  can do is mis-order or mis-group real units, which Phase 3's partition
  check is built to catch.
- It also makes the "no text content" validation nearly free: because real
  unit IDs have a fixed shape (`pNNN-Lnnnn` or `pNNN-sNNNNN`), any prose the
  model accidentally (or adversarially) returned instead of an ID cannot
  coincidentally match a known ID - it simply falls out of the partition
  check as a "hallucinated" ID. There is no separate text-sniffing pass;
  `phase3.check_partition`'s hallucination detection *is* the "assert no
  returned object contains text content" check.

This is the reason Phase 1 must never merge or drop spans on its own
judgment: every span (or, for Phase 2's purposes, every unit) the LLM might
need to reference must exist, intact and individually addressable, before
Phase 2 ever runs.

## Line-granularity units (Phase 2's unit of work)

Phase 2 does not send individual spans to the model - it sends **units**,
built by `units.py`:

- A **line unit** is every span sharing a `line_id`, concatenated in
  `order_in_line` order, *except* any span flagged `size_outlier` or
  `single_glyph`.
- A **standalone unit** is exactly one such flagged span, always emitted on
  its own - never merged into a line, even when other spans share its
  `line_id`.

Two reasons this is span-level's job, not Phase 2's:

1. **Token/ID-space inflation.** Page 1 has 385 spans but only 382 units
   (most lines are already a single style-run span, so line-grouping barely
   shrinks the ID space on this page - but pages with more inline style
   changes, hyphenation, or fragmented columns benefit more, and every
   avoided unit is one fewer ID the model has to track without dropping or
   duplicating across the entire page). Sending raw spans as the atomic
   unit multiplies the ID-tracking surface for no benefit: nothing about
   article-grouping judgment happens at sub-line granularity.
2. **Drop-cap corruption risk.** A drop-cap's bounding box is tall enough to
   vertically overlap two or three ordinary body-text lines beside it (page
   1: the "N" glyph spans the height of "epal President Ram" / "Chandra
   Poudel ap-" / "pointed"). If a drop-cap were folded into whichever line
   its y-centre happens to land on, that would silently re-fuse it with
   unrelated line text - reintroducing exactly the "meetNings"-shaped
   corruption Phase 1's `span_break_gap_ratio` fix eliminated, just one
   layer up the pipeline. Emitting it standalone and letting `assemble.py`
   fuse it with zero separator onto whatever unit the *model* places next
   to it (see below) keeps that decision explicit and auditable instead of
   implicit in a geometry heuristic.

The same standalone mechanism also cleanly isolates headline/deck-scale
text (`size_outlier` without `single_glyph`) as its own unit, which turns
out to help rather than hurt: it means headline text never arrives at the
model pre-mixed with body text on the same unit, matching the schema's need
for a distinct `headline_units`/`deck_units` classification.

### Drop-cap assembly: standalone-unit fusion

`assemble.py` tracks whether the *previous* unit appended to a field was a
`single_glyph` standalone unit. If so, the *next* unit is concatenated with
**zero separator** - no space, no de-hyphenation logic - because a drop-cap
is literally the first character of the following word (`"N"` + `"epal
President Ram"` -> `"Nepal President Ram"`). This is a structural fact
about the glyph, not a cleanup decision, so it applies identically in both
the raw and de-hyphenated text variants. Every other field-internal join
defaults to a single space, with line-final-hyphen de-hyphenation applied
only in the cleaned variant (see "Assembly" below).

### No vision pass in v1

Phase 2 is text-only for now. The units already carry font_size, font_name,
and bbox, which cover the geometric signal a vision pass would otherwise
need to approximate from pixels, at a fraction of the token cost. Ads on
this PDF carry almost no extractable text (page 5: 68 characters across the
entire page - the rest is 70 embedded images), so they fall out of the
grouping naturally: an ad's units simply don't form coherent
headline/body/byline structure the way a real article's units do, and the
prompt instructs the model to route anything that doesn't look like article
prose into `excluded_units`. A vision pass would help confirm *why* a
region is an ad (e.g. reading a logo), but isn't needed to correctly
exclude it. Revisit if a future edition has ads with substantial embedded
text that could be mistaken for article content.

### Removed: the unused vision-image render

The ~1.25MP `vision.png` this section anticipated needing was built
speculatively before Phase 2 turned out to be text-only, and nothing ever
read it back: verified by tracing every consumer - `gemini_client.py`
sends `build_user_prompt`'s plain text, never an image; no `api/` route
serves or references it; the frontend renders pages via PDF.js against
the raw uploaded PDF (`/api/editions/{id}/pdf`), never a bronze-layer
image. It was pure write-side cost: one full-page pdfplumber rasterization
per page (the heaviest CPU step in Phase 1) plus ~900KB/page, written
twice (once to the content-hash cache, once copied into bronze) - roughly
32MB and one wasted render per 18-page edition, indefinitely. Removed
entirely (`render.py`, `cache.py`, `pipeline.py`, `RenderConfig`) rather
than gated behind a flag, since there's no vision-pass roadmap item this
was serving. `render.py`'s on-demand high-res render and debug overlay
(genuinely used, by the CLI's `render-hires`/`debug-overlay` commands) are
untouched. Found while preparing the GCP e2-micro deployment - see
"Deployment: GCP e2-micro VM" below - where a shared-core CPU and a 30GB
disk make an unused per-page rasterization step worth removing rather
than shrugging off.

## Phase 2 JSON contract

Request: one call per page. System instructions (`gemini_prompt.SYSTEM_PROMPT`)
plus a compact per-line unit listing (`gemini_prompt.build_user_prompt`):

```
<unit_id>|<x0>,<y0>,<x1>,<y1>|<font_size>|<font_name>[STANDALONE]|<text>
```

Coordinates are rounded to integers to save tokens; a header line states the
page's modal body font size as a baseline the model can compare font sizes
against.

Response (`gemini_prompt.RESPONSE_JSON_SCHEMA`, enforced via Gemini
structured output - `response_mime_type=application/json` +
`response_json_schema`):

```
articles: [{
  article_id: str,
  headline_units, deck_units, byline_units, dateline_units,
  caption_units: [unit_id],
  body_units: [unit_id in reading order],
  continues_on_page: int | null,
  is_truncated: bool,
  confidence: "high" | "medium" | "low",
}]
excluded_units: [{ unit_id: str, reason: "ad"|"masthead"|"page_furniture"|
                    "teaser"|"index"|"table"|"other" }]
```

Every field that carries content is either an array of unit-id strings or a
closed enum - there is nowhere in this schema for article prose to hide.
Model config: `gemini-3.1-flash-lite`, `thinking_level=HIGH` (this is the
hard reasoning task - multi-column reading order and article-boundary
judgment), `temperature=0`. Cached on
`hash(user_prompt + prompt_version + model_id)` so an unchanged page never
re-calls the API (`gemini_client.py`); bump `gemini.prompt_version` in
config/default.yaml to invalidate deliberately.

## Assembly

`assemble.py` expands each returned unit ID back to its stored text and
concatenates. Two text variants are kept per field (headline, deck, byline,
dateline, body, captions):

- `*_raw`: plain concatenation (drop-cap fusion still applies - it's
  structural, not a cleanup), no de-hyphenation.
- cleaned (unsuffixed): additionally de-hyphenates a line-final hyphen
  followed by a unit starting with a lowercase letter, logging every join
  (article_id, field, the two unit IDs, and a text snippet around the seam)
  into `dehyphenation_log` in the page's gold JSON for audit. De-hyphenation
  happens only at assembly time, on how units are stitched together -
  Phase 1's own stored text is never modified.

## Span schema

```
span_id            str    stable within a page; NOT reading order (see below)
page_num           int
bbox               [x0, top, x1, bottom]  PDF points, top-down (distance
                                            from page top - matches
                                            pdfplumber's own rendering
                                            convention and this package's
                                            image pixel coordinates, so
                                            bbox * (dpi/72) maps directly to
                                            pixels with no axis flip)
text               str    verbatim, exactly the concatenated pdfplumber
                          glyph text for this span's characters
font_name          str    embedded BaseFont name (subset prefix included)
font_size          float
is_bold            bool   inferred from font_name substring match (see
                          config/default.yaml canary.bold_markers) - a HINT,
                          not authoritative; subsetted newspaper fonts
                          rarely expose usable style flags any other way
is_italic          bool   same caveat as is_bold
char_count         int    number of pdfplumber char objects in the span
                          (an "fi" ligature is 1 char object, not 2)
line_id            int    see "line_id and order_in_line" below
order_in_line      int
flags:
  size_outlier     bool   font_size >= 3.0x that PAGE's modal font size
                          (drop-cap / headline candidate - see below)
  single_glyph     bool   char_count == 1
  ends_with_hyphen bool   text.rstrip().endswith("-")
```

`size_outlier` conflates two different things by construction: a genuine
drop-cap (isolated oversized initial letter) and a multi-character headline
set in large type. `single_glyph` is what disambiguates them - a drop-cap is
`size_outlier=true, single_glyph=true`; a headline span is
`size_outlier=true, single_glyph=false`. Phase 1 does not attempt to tell
these apart any further than that; it is Phase 2's job to decide what a
size-outlier span means in context.

### line_id and order_in_line

These record a narrow, purely geometric fact: "these spans sit on the same
visual row, in this left-to-right order." They are **not** a claim about
reading order across the page. This newspaper synchronizes text baselines
across all columns to a shared leading grid, so unrelated columns routinely
share a y-band - line_id groups spans by shared row *and* horizontal
continuity (see "Column fusion" below), so a page with 5 columns produces
many distinct line_ids per physical row, one per column-run. Phase 2 must
use span geometry (bbox), not line_id ordering, to reconstruct actual
reading flow across columns.

Likewise, `span_id` assignment order (row-band top-to-bottom, then
left-to-right within a row-band) is for stable, human-debuggable
identification only, inherited from the same non-goal as the original spec:
Phase 1 does not attempt reading order, and stream order is not reading
order either.

## Column fusion: a real bug found and fixed during build

The original design for `line_id`/`order_in_line` clustered characters into
row-bands by y-position, then split each row-band into column-runs wherever
the horizontal gap between consecutive characters exceeded a threshold
calibrated from a *single* observed example (a photo caption block on page
2, where two column headlines shared a y-band with a 130-370pt gap between
them). That calibration was not representative: on page 1's ordinary body
text, real column-to-column transitions were observed as small as **7.9pt**
- because this newspaper's narrow multi-column body grid has much smaller
gutters than the wide feature block used for the first measurement. A
threshold high enough to avoid over-splitting body text (54pt) let real
column transitions through unsplit, silently fusing two unrelated columns'
text into a single span's text with no separator at all (verified: a span
containing `"...the first" + "According to sources..."` fused end-to-end).

The fix (`geometry.split_runs`, `config.thresholds.span_break_gap_ratio`):
lower the split threshold to 0.5x font_size, which is below the smallest
observed real column-transition gap (0.875x) but above ordinary same-column
word-spacing (p999 = 0.28x, measured over 136k adjacent character pairs
across all 18 pages). The two magnitudes are close enough on this
newspaper's typography that no threshold cleanly separates "large
legitimate gap" from "column transition" from "dropped glyph" in every
case - but the failure modes are asymmetric: under-splitting *fuses*
unrelated content into one misleading span (silent data corruption),
while over-splitting only produces more numerous, still fully verbatim
spans (harmless, and Phase 2 can always re-join spans that turn out to
belong together). The threshold is set conservatively on the "split more"
side of that asymmetry.

This is also why the geometric canary (below) deliberately does **not**
reuse this package's own line/span grouping for its word tokenization -
doing so would reintroduce the exact same column-transition-vs-dropped-glyph
ambiguity into a check whose entire purpose is discriminating a strict
subset of it.

## Ligature canary: why geometric, not dictionary-based

The original design for a ligature-drop safety net was a dictionary check:
extract every word, and flag any word that becomes a valid English word when
an f-ligature (ff/fi/fl/ffi/ffl) is inserted at some position - the idea
being that if a ligature glyph fails to extract on some future page, the
resulting mangled word would be catchable this way.

This was implemented and calibrated against docs/Newspaper.pdf (18 pages,
using the `pyspellchecker` offline dictionary, chosen because it bundles its
word list as package data with no network access needed at runtime - the
container may not have a system wordlist, and this needed to be handled
explicitly rather than silently skipped). It produced 0-11 false-positive
"suspects" per page. Root causes:

- **Hyphenated line-wrap fragments.** `"final"` wrapped as `"fi-"` / `"nal"`
  leaves the fragment `"nal"`, which happens to be real (`"final"`) when
  `"fi"` is prepended - a coincidence of English morphology, not a ligature
  bug. Same pattern produced `nally`->`finally`, `ber`->`fiber`,
  `nings`->`finings`, etc.
- **Acronym collisions.** `"EWS"` (a common Indian government acronym,
  Economically Weaker Section) collides with `"flews"` (an obscure but
  dictionary-valid word for a dog's pendulous lip) when `"fl"` is inserted.
- **Fundamental unfixability.** Inserting an f-ligature into a common short
  word frequently produces another common word: `"at"->"flat"`,
  `"our"->"flour"`, `"re"->"fire"`, `"ow"->"flow"`, `"ne"->"fine"`,
  `"sh"->"fish"`. A frequency threshold cannot separate signal from noise
  here, because `"at"` occurs dozens of times per page and `"flat"` is a
  common word - and the motivating failure case has exactly this shape:
  page 1 contains "remained flat in August"; a dictionary canary cannot tell
  a genuine dropped ligature (which would turn this into "remained at in
  August" - grammatical, plausible, and wrong) apart from the dozens of
  legitimate occurrences of "at" elsewhere on the page.

**Replacement: a geometric canary** (`hindu_extract/canary.py`). Within each
word - tokenized via `pdfplumber`'s own `extract_words()`, not this
package's span grouping (see "Column fusion" above for why) - the gap
between adjacent characters is compared to `kerning_gap_ratio * font_size`
(default 0.75, calibrated: p999 of 136k measured intra-word gaps on this PDF
is 0.28x, and the single largest legitimate gap observed, 0.60x, is a
decorative leader-dot font abutting a numeral on page 11's stock ticker -
see below). A gap exceeding this ratio means a glyph - most plausibly an
f-ligature - failed to extract, leaving an unexplained hole roughly the
width of the missing glyph. This has no dictionary, no acronym problem, and
no frequency tuning: it inspects the extraction geometry directly rather
than guessing about English morphology.

The canary separately flags any raw character object pdfplumber reports
with empty/unmapped text, since pdfplumber can return a positioned glyph
with no resolvable Unicode text rather than omitting it outright - a
failure mode the gap check alone would not catch.

**Result on this PDF: 0 findings across all 18 pages** (full run log kept
during development). Any non-zero result should be treated as a real signal
worth a human look, not routine noise.

## Other structural findings from initial inspection

- **ToUnicode CMaps are not universally absent.** Page 1 (and most pages)
  extract via WinAnsi/Differences encoding with zero fonts carrying a
  ToUnicode CMap. Pages 2 and 11 each have 1-2 fonts (out of 15-20) that
  *do* carry one. Do not assume page-1 behavior generalizes to the rest of
  an edition, or to future editions if the publisher's prepress toolchain
  changes - this is exactly why the font inventory (`PageMetadata.fonts`,
  including `has_tounicode` per font) is recorded per page rather than
  assumed.
- **Page 5 is a full-page image ad**, not an extraction failure: only 68
  characters extract (the masthead line + print registration marks); the
  rest of the page is 70 embedded images. The survey report's coverage
  column exists specifically to make pages like this visible at a glance.
- **Leader-dot decorative fonts extract as a literal repeated letter.**
  Page 11's stock-ticker row (`"Sensex ⋯ 81,905 ⋯ 0.44"`) uses a single-glyph
  font (`THDots-Regular`, WinAnsi encoding, `FirstChar=LastChar=100`) whose
  only defined glyph happens to sit at the codepoint for `'d'`. The text
  layer for the leader dots is therefore a literal run of `'d'` characters,
  not corruption - the visual result renders correctly as dots. Phase 1
  preserves this verbatim as instructed; the font_name/font_size metadata
  (a distinct font at a distinct, smaller size from surrounding text) is
  what lets Phase 2 recognize and ignore it later, exactly the "record
  ambiguity as metadata" principle this package follows throughout.

## Rendering

Two tiers, deliberately asymmetric in persistence (config/default.yaml
`render` section):

- **Vision image (~1.25MP)**: persisted per page in the bronze layer, for
  Phase 2's vision pass. Resolution is derived from the target megapixel
  count and this PDF's fixed page size (992.1 x 1530.7pt), not hardcoded.
- **High-res (300 DPI) and the span_id debug overlay**: generated on demand
  for a single page via the CLI (`render-hires`, `debug-overlay`), never
  persisted for all pages by default. Newspaper body text (~8-9pt) is
  marginal to inspect at 150 DPI, but persisting 300 DPI renders for all 18
  pages of every daily edition would be hundreds of MB for images opened
  rarely (debugging, and later, Phase 2's region crops). The debug overlay
  draws each span's bbox and span_id, color-coded red for `size_outlier`
  spans, for visual QA of the extraction.

## Metrics (Phase 3)

| Metric | Definition | Measurable now? | Result |
|---|---|---|---|
| Partition completeness | every unit_id assigned to exactly one article or excluded_units | Yes - `phase3.check_partition`, run per page by `articles_pipeline.process_page_articles` | Run `hindu-extract articles` and check the printed `partition=OK/NEEDS REVIEW` column, or `gold/{edition}/{date}/page_NN/articles.json`'s `partition_ok` field |
| Partition duplication | no unit_id assigned to more than one location | Yes - same check, `PartitionResult.duplicated` | as above |
| Hallucination rate | fraction of pages where the model invented a unit_id not in the input (this doubles as the "model returned text, not IDs" check) | Yes - `PartitionResult.hallucinated` | as above |
| Retry recovery rate | fraction of initially-failed pages that pass after the one cited-failure retry, vs. falling through to needs_review | Yes - `GroupingOutcome.attempts` vs `needs_review` per page | not yet run across a full edition |
| Seam continuity rate | fraction of article seams that read as continuous prose | **No** - deferred; needs an LLM-judge pass, not built in this minimal Phase 3 | |
| Self-consistency rate | fraction of articles unchanged across repeated Phase 2 runs on the same input | **No** - deferred; needs repeated non-cached calls at temperature>0 or multiple samples, not built | |
| Drop-cap resolution accuracy | fraction of `single_glyph & size_outlier` units correctly fused as the first character of a body | Partially - `assemble.py`'s fusion is deterministic once the model places the unit correctly, but whether the model places it *correctly* (vs. e.g. skipping it into excluded_units) is not yet measured systematically | |
| Manual QA sample accuracy | fraction of a human-reviewed article sample judged correct | **No** - inherently manual | |

## PressDigest: frontend + API

A FastAPI backend (`hindu_extract/api/`) is a thin read layer over the gold
JSON already on disk, plus job orchestration for kicking off extraction; a
React + Vite + TypeScript frontend (`frontend/`) reads it. Neither layer
reimplements extraction logic - the API imports `pipeline.extract_pages`
and `articles_pipeline.process_page_articles` directly, exactly as the CLI
does.

**Status at time of writing:** the job system, edition-identity parsing,
edition listing, coordinate-mapping math (verified against a real fixture),
and the Dashboard + empty-state screens are built and tested. The
article-shaped endpoints (`GET /api/editions/{id}/pages/{n}`,
`GET /api/editions/{id}/articles`) and the Page Reader's article list are
deliberately not yet built - see "Building the frontend against the live
schema" below for why, and check back once a live Phase 2 run has
completed.

### Edition identity: parsed from the masthead, not user-typed

The dashboard is drag-and-drop with no metadata form, but the storage layer
is keyed on (edition, date). Rather than defaulting `date` to "today" (silently
wrong for a back-dated PDF, corrupting the storage key) or forcing the user
to hand-type it, `api/metadata_parser.py` reads it directly from the page-1
masthead text Phase 1 already extracts:

- **date**: the first span matching `Month D(D), YYYY`.
- **edition**: a short ALL-CAPS alphabetic span immediately followed by a
  span containing "EDITION" (case-insensitive) - a structural pattern
  (verified: `DELHI` -> `CITY EDITION` on docs/Newspaper.pdf) that should
  hold for any city edition of this newspaper, not just Delhi, without
  hardcoding a city list.

Either field parses to `None` if not found, and the frontend falls back to
an editable (but pre-filled when possible) text field rather than silently
guessing - `POST /api/editions/parse-metadata` returns the parsed values,
and the actual extraction call `POST /api/editions` takes whatever the user
confirmed.

`edition_id` used in URLs is `f"{edition}__{date}"` (`api/edition_id.py`) -
an opaque, round-trippable string so routes only need one path parameter.

### Background jobs

`api/jobs.py` holds an in-memory job registry (`dict[job_id, JobRecord]`,
guarded by a lock) and runs each extraction on a `ThreadPoolExecutor` -
deliberately not asyncio directly, since the underlying work
(pdfplumber parsing, synchronous google-genai calls) is blocking, not
async-native. In-memory is a deliberate v1 choice: this is a local-dev
single-process app, restart-and-reupload is already cheap because of the
Phase 1/Phase 2 caches, so job state doesn't need to survive a process
restart yet.

Per-page progress is real, not simulated: `pipeline.extract_pages` gained
an optional `progress_callback` parameter (default `None`, fully backward
compatible - existing callers and tests are unaffected) invoked once per
page as Phase 1 completes it, and the job runner calls
`articles_pipeline.process_page_articles` in its own per-page loop exactly
as the CLI's `articles` command does, updating `pages_done` and per-page
status after each one. A single page's Phase 2 failure is caught and
recorded on that page (`status: "failed"`, `error: <message>`) without
aborting the rest of the job - `write_edition_markdown` was fixed to skip
pages with no gold JSON rather than crash, so one bad page no longer flips
the *entire* job to `failed` (found via manual testing: it originally did).
Cache hits are surfaced per page (`cached: bool`) and in aggregate
(`all_cached`) so a re-upload of an identical PDF visibly says "served from
cache" instead of looking broken by finishing suspiciously fast.

### API contract (built so far)

```
POST   /api/editions/parse-metadata   multipart file -> ParsedMetadataOut
                                        { edition: str|null, date: str|null }
POST   /api/editions                  multipart file + ?edition=&date=
                                        -> StartJobOut { job_id, edition, date }
GET    /api/jobs/{job_id}             -> JobStatusOut
                                        { job_id, edition, date,
                                          status: queued|running|done|failed,
                                          pages_done, pages_total,
                                          per_page: [{ page_num,
                                            status: pending|extracting|
                                                    grouping|done|failed,
                                            articles_found, partition_ok,
                                            needs_review, cached, error }],
                                          all_cached, error }
GET    /api/editions                  -> EditionSummaryOut[]
                                        { edition_id, edition, date,
                                          page_count, article_count }
GET    /api/editions/{edition_id}     -> EditionDetailOut (summary +
                                          pages_with_articles,
                                          pages_with_zero_articles)
GET    /api/editions/{edition_id}/pdf -> the stored source PDF (for PDF.js)
```

Pending live schema (see below):
`GET /api/editions/{edition_id}/pages/{n}`, `GET /api/editions/{edition_id}/articles`.

TypeScript types are generated from these Pydantic models by
`scripts/generate_types.py` into `frontend/src/types/api.ts` - run it
whenever `hindu_extract/api/schemas.py` changes. (Implementation note: each
model is run through `json2ts` independently rather than as one combined
schema, because Pydantic gives every field its own `title`, which json2ts
hoists into a top-level named alias - colliding across models that share a
field name, e.g. two different `Edition` aliases. The generator strips
non-root titles before conversion and de-duplicates identical interface
re-declarations that come from shared nested models like `PagePhaseOut`.)

### Coordinate mapping (verified against a real fixture)

Three coordinate systems disagree about where "up" is, and conflating any
two of them is the classic bug in this kind of overlay:

- **Raw PDF space**: origin bottom-left, y increases *upward*.
- **Our bbox** `(x0, top, x1, bottom)`, inherited from pdfplumber: origin
  top-left, y increases *downward* - already flipped to match normal
  screen/image conventions (see "Span schema" above). This is what the API
  serves.
- **PDF.js's rendered viewport**: also top-left origin, y increases
  downward (it matches the canvas it draws into) - but its own
  `viewport.convertToViewportPoint(x, y)` expects *raw PDF-space* input and
  applies the flip itself.

Feeding our already-top-down bbox straight into `convertToViewportPoint`
flips it a second time, mirroring the overlay vertically. The fix
(`frontend/src/lib/coords.ts::bboxToViewportRect`): un-flip our bbox back
to raw PDF space (`pageHeightPt - y`) before handing it to PDF.js's own
transform, rather than hand-rolling a scale-only shortcut that would
silently break under page rotation.

Verified in `frontend/src/lib/coords.test.ts` by loading the real
docs/Newspaper.pdf (not a synthetic viewBox) and checking the page-1
headline bbox (`"Karki is Nepal's first woman PM"`, top-down
`top≈336, bottom≈378` out of a 1530.71pt-tall page) lands in the top third
of the rendered viewport, not mirrored to `top≈1152` (~75% down the page) -
which is exactly where the naive double-flip bug would place it.

### No vision pass; article overlay is span-schema-only

An article's highlight is the union of its constituent units' bboxes
(`frontend/src/lib/coords.ts::unionBbox`), computed client-side from
whatever unit bboxes the (pending) page-articles endpoint returns - no
separate geometry computation or vision model is needed for this.

### Building the frontend against the live schema

The article-shaped API responses and the Page Reader's left pane
(headline/deck/byline/dateline/body/captions rendering, the confidence
badge, truncation/needs_review markers, the raw-text toggle) are
deliberately not built yet, per instruction: "I don't want it built against
a schema the model has never actually produced." `assemble.py`'s
`AssembledArticle.to_dict()` is the presumptive shape, but until a live
Gemini call has actually populated `data/gold/`, building `ArticleOut` and
the card component against it risks silently encoding an assumption (field
presence, whether a field can be an empty string vs. absent, how the model
actually populates `confidence` in practice) that the real pipeline
contradicts. This is the same reasoning as Phase 1/2's core discipline:
verify against real data before encoding an assumption into a schema
other code depends on.

### UI slots awaiting a later pipeline phase

| UI element | Status | Waiting on |
|---|---|---|
| Dashboard upload + job progress | Built | - |
| Edition list | Built | - |
| Page Reader: PDF.js rendering + zoom | Built | - |
| Page Reader: article list, confidence badge, coordinate overlay | Not built | Live Phase 2 gold JSON (see above) |
| Summaries: empty state | Built | - |
| Summaries: 20-card ranked grid (`SummaryCardGrid`, present but unused) | Built, feature-flagged off | Ranking + summarisation pipeline (not started) |
| AI Chat | Empty state only | Chat/RAG pipeline (not started) |

## Stream-order rebuild

Phase 1's geometric layer (row/column clustering, `column_major_order`,
the span/unit/block hierarchy) and Phase 2's unit-ID grouping+ordering
architecture were both replaced. Root cause: three consecutive live runs
under the old architecture (see the "ID ranges: tried and reverted"
history above) either truncated or produced scrambled article bodies,
because ordering ~200-400 small units by hand is exactly the kind of
large-scale bookkeeping a model does unreliably, however the prompt was
tuned.

### The diagnostic that changed the design

Verified directly against `docs/Newspaper.pdf` (not assumed): take
`page.chars` in **raw, untouched content-stream order** - no sort by (top,
x0), no clustering - and group consecutive chars into lines using only the
existing separator threshold (`span_break_gap_ratio`). The result, on both
page 1 and page 8 (a dense, normal multi-article inner page):

- An article's **body always occupies one single contiguous run** of
  stream-ordered lines, correctly threading across all of its columns,
  with zero interleaving from any other story. Confirmed on 3+ consecutive
  stories across two pages.
- A drop-cap sits **directly adjacent**, in stream order, to the text it
  fuses with (`"N"` immediately followed by `"epal President Ram"`).
- **Furniture is not contiguous.** A story's headline can be dozens of
  lines away from its own deck, with unrelated stories' teasers sitting in
  between (verified: page 1's Nepal headline at line 197, its deck at
  lines 225-228, with three other stories' teasers at 205-224 in between).

This one property - body is always one contiguous slice - is what the
entire rebuild exploits: the model's job changed from "select and order
every small piece" (unreliable) to "find where a handful of fields start
and end" (a much smaller, checkable task).

### What changed

**Phase 1** (`lines.py`, replacing `spans.py`/`geometry.py`/the row-banding
apparatus): walks `page.chars` in native stream order in a single forward
pass, grouping into `Line` records. No global sort, no column-run
splitting, no style-run splitting. Two rules only, checked against the
immediately preceding char:
1. An outlier-sized char (drop-cap or headline, `size_outlier`) never
   merges with a non-outlier one - this is what keeps a drop-cap isolated
   as its own line deterministically, rather than hoping the row/gap check
   happens to separate it by coincidence.
2. Otherwise, the same already-validated same-row + gap threshold as
   before (`row_band_tolerance_ratio`, `span_break_gap_ratio`), just
   applied to consecutive stream chars instead of a globally-sorted list.

Every char now carries a `stream_index` (position in `page.chars`); each
`Line` stores `stream_start`/`stream_end` from its chars, so stream
position survives in the persisted bronze JSON (previously it didn't -
`verify.py`'s old fidelity check reconstructed it via `id(c)` against a
live, in-process `page.chars` list, which can't survive a process
boundary). `Line.line_no` is 1-based, assigned in the same stream-walk
order - it is not re-sorted afterward, unlike the old `unit_id` scheme's
`column_major_order` pass.

**Phase 2** (`gemini_prompt.py`, `phase3.py`, `assemble.py`): the model
receives a numbered line dump (`L<line_no>|<font_size>|<text>`) and
returns line-number **boundaries** per article field
(`{start, end, start_words[, end_words]}`), not a selected/ordered list of
IDs. `headline`/`byline`/`dateline` are single ranges (byline/dateline
nullable); `deck`/`caption` are **lists** of ranges, since furniture is not
guaranteed contiguous with itself. `excluded_units` from the previous
architecture is gone entirely - anything not inside any article's ranges
is excluded by construction (a derived set complement), never enumerated.

### ID ranges: tried and reverted

An earlier version of the (now-deleted) unit-ID architecture added compact
ID-range shorthand (`"p001-L0068-L0090"`) to cut output size after a real
HIGH-thinking run hit Flash-Lite's 65,536-token completion ceiling
(thoughts + candidates together) with an explicit `excluded_units` list.
Removing `excluded_units` was what actually fixed the truncation; the
ranges bought nothing but did real damage: they gave the model an output
form that costs the same whether right or wrong (a wrong range costs 8
tokens, same as a right one), so guessing a plausible-looking range was
the path of least resistance, and a run of consecutive unit-IDs frequently
spanned two or more genuinely unrelated reading chains that happened to
land on adjacent numbers (the old `column_major_order` numbering had no
gap or marker between chains). Verified live: a single 30-ID range spanned
three distinct chains, producing a badly scrambled body. Ranges were
removed entirely; every ID-list field in that architecture went back to
individual IDs. That whole ID-selection architecture is now gone in favor
of the boundary-finding approach above, which sidesteps the problem
category rather than patching it - there is no equivalent "range" concept
in the (start, end) line-number boundaries, because a boundary is a single
claim to verify, not a list of individual guesses.

### Gemini 3.x thinking tokens count against the output ceiling

Flash-Lite has one fixed completion-token ceiling (65,536) shared between
`thoughts_token_count` and `candidates_token_count` - raising
`max_output_tokens` does not add budget beyond that hard limit, and
thinking length at a given `thinking_level` is not fixed: two runs of the
*old* architecture at HIGH, with `max_output_tokens` raised from 32,768 to
49,152, saw thinking grow from 31,455 to 47,181 tokens and still get cut
off - thinking appears to expand to consume whatever headroom is given
rather than converging to a stable length for a given task. Under the new
boundary-finding architecture, the task is small enough that this stopped
mattering in practice: a live HIGH-thinking run on page 1 used 47,183
thinking tokens but only needed 583 candidate tokens to express ~300
tokens worth of boundaries, comfortably inside the 49,152 cap with margin
before the 65,536 hard ceiling.

### Checksum validation must use real join semantics

`start_words`/`end_words` are a few words the model copies from the line
dump to let Phase 3 verify a claimed boundary independently. The first
implementation of the checksum check sliced lines with a naive
`"".join(line.text for line in range(start, end+1))` - no separator - and
falsely flagged live, correct output as a mismatch: consecutive lines
"Ram" and "Chandra Poudel ap-" concatenated to "RamChandra Poudel ap-",
which doesn't start with the model's (correct) checksum "Ram Chandra".
Fixed by having the checksum check call `assemble.py`'s actual line-joining
function (drop-cap fusion + de-hyphenation-aware spacing) instead of
reimplementing a simplified join - the two must agree, or the check
validates against text that will never actually appear in output.

A second, narrower discrepancy survived that fix: a checksum spanning a
drop-cap boundary (e.g. `"N epal President Ram"`) naturally includes a
space, because the model transcribes words as they appear on **separate
rows** in the line dump - but the real, correctly-fused text has none
(`"Nepal President Ram"`, per the drop-cap rule). This is not evidence of
a wrong boundary (verified live: the range itself was exactly correct);
`phase3.py`'s checksum match therefore falls back to a whitespace-stripped
comparison before declaring a mismatch, which tolerates this specific,
understood formatting artifact without weakening the check's ability to
catch a genuinely wrong boundary (which would put different *words* at the
edge, not just different spacing).

### Multi-rect bodies

An article's highlight is no longer one union bbox. `assemble.py` splits a
body's lines (already in stream/line_no order) into geometric fragments
wherever consecutive lines are not visually adjacent (a large vertical or
horizontal jump, i.e. a column change) and returns one bbox per fragment -
a rendering aid only; text assembly never depends on it.

### Live validation result (page 1, docs/Newspaper.pdf)

One real call, `thinking_level=HIGH`, `max_output_tokens=49152`,
`prompt_version=v4`: 5,194 prompt tokens, 47,183 thinking tokens, 583
candidate tokens, 98.95s wall-clock. Both articles' body ranges matched
Step 1's independently-verified ground truth exactly (Nepal L57-196,
retail-inflation L229-264), the non-contiguous deck case was found
correctly (L225-228, with unrelated teasers in between), all checksums
passed (after the fixes above), zero contiguity issues, zero overlap,
coverage 66% (191/289 lines - the rest is masthead, teasers, and other
pages' furniture, none of it wrongly captured). Both assembled bodies read
as fully correct, continuous, grammatically coherent prose end to end -
"meetings" present, "meetNings" nowhere, drop-cap "N" leading the body
with no stray space.

## Standing rule: font size alone never identifies a headline

This has now bitten three times, via two different variants of the same
underlying mistake: treating a piece of page furniture as a normal
candidate when deciding what's "biggest" or "the headline" on the page.
Drop caps and section kickers must both be excluded from that judgment -
font size alone is not sufficient, ever.

**First bite (Phase 1):** the page's modal/outlier font-size calculation
has to exclude single_glyph lines, or a drop cap (routinely 3-4x body
size) skews the distribution used to detect genuine headline-scale text.

**Second bite (Phase 2, found live via the ranking feature):** the
boundary-finding prompt told the model "the headline is usually the
largest font size for that story," with no exclusion for drop caps. On
page 6 (The Hindu's op-ed page), every piece opens with a 37pt drop cap -
larger than any of that page's real ~13-21pt titles - so the model
picked the drop cap as the "headline" for 3 of 4 op-eds (literally a
single letter: "W", "T", "I"), and for the 4th, the drop cap fused with
the start of body prose ("Policing the digital economy requires what...",
which is the opening clause of the body, not a title at all). The real
titles ended up folded into `deck` instead. Confirmed by inspecting the
raw bronze lines directly (line 8: 37pt, single_glyph, "P"; line 9: 9pt,
"olicing the digital economy requires what...") and by pulling the exact
prompt the ranking call received from the trace DB - it genuinely said
`headline: W`.

**The fix, applied here for good:** any time a line's font size is being
compared to find "the biggest" or "the headline," single_glyph +
size_outlier lines (drop caps) must be excluded from that comparison
first. `gemini_prompt.py`'s dump format now marks each line with an
explicit `D`/`-` flag so the model can see the flag rather than infer it
from size, and the system prompt states outright that a drop cap is never
a headline and that a single letter is never a headline. `phase3.py` adds
a backstop validation check (`_check_headline_quality`): a resolved
headline of a single character, or fewer than ~3 words, is flagged
`needs_review` rather than trusted - this specific bug would have been
caught automatically had this check existed from the start.

**Third bite (Phase 2, found live on page 7, immediately after fixing the
second):** excluding drop caps alone wasn't enough. Page 7's real headline
("After the disaster") sits below a 39.9pt ALL-CAPS section kicker
("GROUND ZERO") that is NOT a drop cap (not single_glyph) but is still
larger than the real ~20pt headline - the drop-cap fix correctly stopped
the model from picking the drop cap, but it then picked the kicker
instead, losing the real headline and an entire deck. Confirmed the same
way as the second bite: read the raw bronze lines directly (line 7:
39.9pt, not single_glyph, "GROUND ZERO"; line 8: 52.1pt, single_glyph,
the real drop cap "T") and the gold JSON that resulted (`headline: "GROUND
ZERO"`, `deck: []`). Checked all 18 pages for the same shape (an ALL-CAPS,
<=3-word headline) - isolated to this one page, not systemic, but the
underlying rule was still wrong in general.

**The fix, generalised rather than special-cased a second time:**
`gemini_prompt.py` now has a "SECTION KICKERS / STANDING HEADS ARE NOT
HEADLINES" rule describing the *shape* of a kicker (ALL-CAPS, 1-3 words,
often a font size that rivals or exceeds the real headline) with concrete
examples from this edition (GROUND ZERO, STRIFE-TORN STATE, NATO ON EDGE,
PLAYCOM SUMMIT), and a new `section_kicker` field so a correctly-identified
kicker has somewhere to go instead of either being dropped or capturing
the headline slot. `phase3.py`'s `_check_headline_quality` gained a second
condition alongside the drop-cap one: a headline that is ALL-CAPS and 3
words or fewer is flagged `needs_review` as a likely kicker - this would
have caught the page-7 bug automatically, the same way the single-character
check already catches the drop-cap bug.

**Watch for this a fourth time:** anywhere else a prompt or a heuristic
reasons about "the biggest font on the page/story" is a candidate for the
same failure mode. Drop caps and section kickers are both deliberately,
structurally larger than they "should" be for their semantic role - that's
the whole point of both - so either will reliably beat the actual headline
in a naive size comparison. Font size is evidence toward a headline
candidate, never proof by itself.

## Ranking: thinking_level HIGH truncates here too (second confirmation)

The edition-wide ranking call (see "Summaries: edition-wide importance
ranking" below) was first run at `thinking_level=HIGH`,
`max_output_tokens=49152`, matching the original spec. It truncated on the
very first live attempt: 47,184 thinking tokens against the ceiling - a
near-identical number to the Phase 2 HIGH truncation documented above
(47,183), on a completely different task (ranking ~107 candidates vs.
finding one page's boundaries). This is now the second time HIGH has
consumed almost exactly the same huge thinking budget regardless of the
actual task, which is strong evidence that Flash-Lite's HIGH setting
scales thinking to fill whatever ceiling it's given rather than to the
task's real complexity - not something specific to boundary-finding.
Switched to MEDIUM (same reasoning already applied to Phase 2): a fresh,
bypass-cache MEDIUM call ranked all 20 articles cleanly in 23,408 total
tokens (1,293 of them thinking), zero truncation, zero retries needed
once the validation logic itself was corrected (see below). Re-raising
`max_output_tokens` for HIGH was deliberately not tried - per the Phase 2
precedent, that just lets thinking grow further rather than converging.

## Summaries: edition-wide importance ranking

One Gemini call ranks every article in the edition together (not
per-page - per-page scores aren't comparable, a weak page's best article
would look artificially important with nothing to compete against).
Input: headline, deck, `continues_on_page`, and a ~100-word body preview
per article, each keyed by a composite id (`p{page}-{article_id}`, since
gold JSON's per-page ids are reused independently across pages). Output:
a fixed top-N list with rank, a 0-100 importance score, one of 12 fixed
categories, a model-generated `why_it_matters` (at most 30 words), and an
`exclusion_risk` flag - kept in its own field, structurally separate from
any extracted article text, since it's the first prose in this product
the model actually wrote rather than a fact about text already stored.

**Duplicate continuations, detected by headline overlap, not page
number.** A story split across pages (a first part with
`continues_on_page` set, and its continuation registered as a separate
article on that later page) risks being ranked twice. The initial
cross-check flagged any ranked article merely sharing the continuation's
target page - which produced false positives, because a continuation's
target page routinely also contains other, completely unrelated
top-ranked stories (verified live: page 8 has 10 articles; only 2 of them
were the actual continuations of two page-1 stories, but the check
flagged 2 different, unrelated page-8 articles instead, because it only
checked page number). Newspapers commonly print a "jump head" on a
continuation page - a repeated or near-identical headline rather than a
blank one (verified live: page 8's actual jump head for the page-1 Nepal
story is "Karki is Nepal's first woman **Prime Minister**", matching page
1's "Karki is Nepal's first woman **PM**"). The fix checks headline word
overlap (>=50% of the shorter headline's significant words, >=2 words
shared) between a first-part's target page and anything ranked there,
which correctly distinguishes "this is the same story's jump head" from
"this just happens to be on the same page."

**Retry-once is corrective, not blind**, because temperature=0 means a
bare retry would very likely reproduce the identical output: on
validation failure, the retry prompt is appended with the exact issues
found (e.g. "you used category X, not in the enum" or the specific
duplicate-continuation pairs), not just re-sent as-is.

## Word-space gap fix

Found by inspection: page 17's headline read "Apeek into the future of
sports industry" - missing the space between "A" and "peek". Measured
directly against `page.chars`: the gap between "A" and "peek" is 3.507pt at
15.941pt font (ratio 0.220) - numerically identical to the gap between
"peek" and "into" later on the SAME line, which DOES have an explicit space
glyph. The PDF's content stream simply omits the space glyph entirely for
some single-character words ("a"/"A"/"i"/"I") followed directly by the next
word, encoding only a normal-width positioning gap. `lines.py`'s line-text
construction (`text = "".join(c["text"] for c in group)`) had no mechanism
at all for inferring a space from geometry - not a miscalibrated threshold,
an entirely absent capability.

**Scale, established before fixing anything** (per the standing rule of
investigating before guessing at a fix - see "Standing rule" above for the
same discipline applied to a different bug class): a targeted dictionary
sweep (word not in dictionary, starts with lowercase "a"/"i", remainder is
a common dictionary word - see `word_fusion_review.py`) found 74 candidates
across the edition; context-checking each by hand found ~49 genuine
fusions (e.g. "aday"->"a day after", "ABench"->"A Bench of Justices",
recurring 5x as a standard Indian judicial-reporting phrase) and ~25 false
positives that are real proper nouns coincidentally shaped this way (Amit
Shah, Akali Dal, Ayon Sengupta, the acronym AHEL). Extending the same sweep
to other short words (in/an/of/on/...) found only ~3 genuine hits out of 27
candidates - the defect is concentrated in single-character words, not a
general phenomenon. A separate reverse sweep (adjacent word pairs that join
into a valid dictionary word, checking for the opposite failure - a
spurious space splitting one real word into two) found 280 candidates, all
280 of them common short-word coincidences ("in a"->"ina", "be held"->
"beheld") and zero genuine spurious splits - that failure mode does not
appear to exist on this PDF.

**Why a geometric threshold is safe here, once measured precisely.**
Comparing the bug's gap ratio (0.22) against a real word-gap elsewhere on
the same line (also 0.22) suggested at first that gap size alone might be
irreducibly ambiguous. That comparison asked the wrong question. The
threshold only has to separate the bug from ordinary *intra-word* kerning,
not from real word-gaps - and there the two populations don't overlap at
all: measured across all 18 pages (alpha-to-alpha adjacent pairs within
words identified by pdfplumber's own `extract_words()`), the intra-word gap
ratio ceiling was <=0.088 everywhere, typically <=0.05. Eight confirmed
missing-space bugs sampled across eight different pages measured
0.18-0.54. A full-edition geometric sweep (alpha-only pairs, ratio in
(0.10, 0.8], the upper bound excluding a page-11 stock-table leader-dot
line at ratio 8.9 that isn't prose at all) found genuine instances up to
0.71 - still zero overlap with kerning. `word_space_gap_ratio: 0.12` (see
config/default.yaml) sits in the gap; the total insertion count was stable
within 5 across the whole tested range (0.08/0.10/0.12/0.15), so the exact
value inside that range is not load-bearing.

**Before/after impact, measured against the real pipeline before
committing to a threshold.** A first pass (naive row-grouping across each
page's full width, ignoring column boundaries) found 163 raw candidates and
looked alarming - "-|demand", "e" appended after unrelated column text at
ratio 28-41 - but these turned out to be artifacts of that *simulation*,
not the real bug: `lines.py`'s actual stream-order grouping (which follows
the content stream per contiguous story, not a page-wide geometric sort -
see "Stream-order rebuild" above) never produces them, verified directly
against `build_page()`'s real output for seven sampled cases. Of the
remaining candidates, 18 were a recurring masthead artifact ("A ND-NDE",
identical every page) and ~20 were inside a page-2 name-change classified
notice - both confirmed absent from every gold article's fields (they're in
`excluded_line_nos`), so out of scope regardless. The real, in-pipeline
count of article-body insertions is reported per-run in
`word_space_log` (see below) rather than re-estimated by hand each time.

**Implementation.** `Line` gained a second text field,
`corrected_text` (`models.py`): `Line.text` remains exactly the literal,
uncorrected glyph-joined text it always was, and - critically - is what
`gemini_prompt.py`'s line dump is still built from. This was a deliberate
constraint, not an oversight: the Phase 2 Gemini response cache is keyed on
a hash of that exact prompt text (`gemini_client.py`), so if `text` itself
had gained the synthetic spaces, every page with at least one insertion
(nearly all of them) would have missed cache and triggered a fresh live
API call for no boundary-relevant reason - boundaries are found by line
NUMBER, not exact character content, so the model never needed the
correction to do its job. `corrected_text` carries the fix instead, and
`assemble.py`'s `_join_consecutive` builds each field's `cleaned` output
(the reader-facing `headline`/`body`/`deck`/etc.) from `corrected_text`
while `raw` (the `*_raw` fields) keeps using `text` - `_raw` fields exist
specifically to preserve literal, unprocessed extraction. Checksum
validation (`phase3.py`) was left untouched, still built from the same
`_join_consecutive`, because it doesn't need special-casing: both
`_fuzzy_prefix_match`/`_fuzzy_suffix_match` already strip whitespace as a
fallback match (originally for an unrelated drop-cap spacing artifact), so
a checksum copied from the uncorrected dump still matches a corrected
candidate string. `word_space_gap_ratio` bumped `pipeline_version` (Phase 1
cache) but not `gemini.prompt_version` (Phase 2 cache) - the two caches are
independent for exactly this reason.

Every insertion is logged unconditionally (`WordSpaceInsertion`, `lines.py`)
with page, line, stream position, the two characters, the measured gap, and
its ratio - written into bronze `page.json` as `word_space_log`, then
filtered to each gold article's own referenced lines and included in
`articles.json` the same way `dehyphenation_log` already is. Nothing this
fix does is silent.

**The verbatim guarantee, restated precisely.** `verify.py`'s
`check_text_fidelity` is unchanged and still proves exactly what it always
proved: `Line.text` (never touched by this fix) matches raw pdfplumber
extraction with whitespace stripped from both sides. A new function,
`check_word_space_correction_fidelity`, checks the new invariant this fix
introduces: per line, `corrected_text` matches `text` with whitespace
stripped from both sides too - i.e. the correction is provably restricted
to inserting whitespace and can never add, drop, reorder, or alter a real
character. Comparing with whitespace stripped from both sides is not a
looser stand-in for "character-for-character identical" - restricted to
the non-whitespace characters, it IS that guarantee, which is the only
form of it that can coexist with permitting synthetic separator insertion
at all. Both checks run per-line, across every one of the 18 pages, as
their own regression tests.

**Soft canary, not a hard-fail one.** The dictionary sweep used to measure
scale above is wired into the pipeline as `word_fusion_review.py`, run
per-article over the assembled (corrected) text and stored as
`word_fusion_review` in gold JSON - but it never sets `needs_review` or
fails `validation_ok`. Its ~34% false-positive rate on real proper nouns
(Amit Shah, Akali Dal, Ayon Sengupta, AHEL, ...) is exactly the same
structural problem the ligature canary's dictionary approach was rejected
for (see "Ligature canary" above) - a human can skim a flagged list, but a
build should not fail on it. The geometric check (`lines.py`) is the actual
fix; the ligature canary (`canary.py`) remains the only hard-fail check.

## Standing rule: stream adjacency does not imply same-story membership

Stream adjacency does not imply same-story membership, and single-range
fields cannot represent non-contiguous furniture. deck and caption are
lists for this reason; byline and section_kicker are not, which is why
photo-caption name labels (p11, p12) and a neighbouring column's standing
kicker (p14) were absorbed. Any field that can have geometrically
separated or cross-column neighbours needs either a list type or an
explicit column-consistency check.

This is the fourth instance of the same underlying mistake as the
"Standing rule: font size alone never identifies a headline" section
above and "Column fusion" earlier: two geometrically-unrelated pieces of
page furniture happen to sit close together in the PDF's raw content
stream, and the current schema has no way to keep the real content while
skipping the wrong neighbour.

Both bugs below were found via the per-page quality report (a manual
review aid built from existing gold JSON, no API calls) and diagnosed
against bronze line geometry (x0/top bbox). Both are recorded here as
**known limitations, not yet fixed** - fixing either means changing the
Phase 2 prompt, which bumps `gemini.prompt_version` and invalidates the
entire Gemini response cache (18 live calls to regenerate). Deferred
until after the current deployment.

### Bug 1: byline field absorbing adjacent photo-caption name labels (pages 11, 12)

Four confirmed cases. In every one, the genuine byline+dateline pair sits
at one x0 (consecutive y, same column), and an extra name absorbed into
the same byline range sits at a *different* x0 - consistent with a
caption/name-tag under an inset photo elsewhere in the layout, not
printed byline text:

- **p11 article '6'** (SEBI): byline+dateline "Lalatendu Mishra" /
  "MUMBAI" at x0=621.9. Absorbed: "Tuhin Kanta Pandey" at x0=740.6,
  top=352.1 - a different column, evidently a caption near a photo of the
  SEBI chairman elsewhere on the page.
- **p11 article '7'** (EU trade commissioner): byline+dateline "The Hindu
  Bureau" / "NEW DELHI" at x0=28.3. Absorbed: "Maroš Šefčovič" at
  x0=147.1, top=1009.6 - a different column, consistent with a name-tag
  under an inset photo of him.
- **p11 article '9'** (Kerala blue economy conclave): byline+dateline
  "The Hindu Bureau" / "THIRUVANANTHAPURAM" at x0=28.3. Absorbed: "Saji
  Cherian" at x0=147.1, top=1386.3 - directly above a body sub-column at
  that same x0, consistent with a caption under an inset photo of the
  Minister quoted in the body.
- **p12 article '8'** (Charlie Kirk suspect): byline+dateline "Agence
  France-Presse" / "OREM" at x0=740.6. Absorbed: "Tyler Robinson" at
  x0=859.3, top=580.8 - a narrower right-hand sub-column, consistent with
  a mugshot caption. This one rules out a printing-convention
  explanation outright: Tyler Robinson is the suspect, not a journalist.

Root cause: `byline` is a single contiguous `LineRange`
(`gemini_prompt.py`'s `RESPONSE_JSON_SCHEMA`). When a caption-shaped line
sits stream-adjacent to the genuine byline line, the model can't include
the real byline without also sweeping in the line between them - exactly
what `deck`/`caption` being lists of ranges was already designed to
avoid, just not applied to `byline`/`section_kicker`.

Confirmed **not** a missed cross-article overlap: none of the four
absorbed lines are claimed by any other article's fields, and none are in
`excluded_line_nos` - this is a single-field boundary error, not a
coverage conflict the existing overlap check would ever catch (that check
is body-only).

Edition-wide sweep (byline with 4+ words, multiple capitalized-name runs,
or an embedded all-caps token): 12 hits total. Beyond the 4 cases above,
every other hit is legitimate printed text already verified against
bronze lines - agency names ("Press Trust of India" x4), two op-ed
byline-bio lines on page 6 ("Shailesh Gandhi is a former Central
Information Commissioner", etc.), and a genuine two-line signature block
on page 17 ("NIRMALA LAKSHMAN" / "Chairperson, The Hindu Group", same x0
column, contiguous). No further hidden cases of this shape.

Proposed fix (not implemented): tighten the Phase 2 system prompt so a
photo-credit/name label adjacent to a byline is recognized as caption
material, not byline, plus a phase3 backstop flagging any byline with 4+
words or an embedded all-caps token for review.

### Bug 2: page 14, unrelated column's kicker absorbed into a story's furniture

Article 2 (a trampoline-gymnastics photo feature) assembled
`headline="PICTURE THIS Spring, roll"` and `section_kicker="GAME THEORY"`.
Bronze geometry:

- L282 "PICTURE THIS" (x0=453.6, top=1042.8, right column) - this
  article's real kicker, directly below a right-column divider rule
  (L281, x0=454.9).
- L283 "Spring, roll" (x0=453.6, top=1408.1, same column, ~365pt lower) -
  this article's real headline, positioned below where the feature's
  photo sits.
- L285 "GAME THEORY" (x0=28.3, top=1042.8, left column) - an unrelated
  feature's standing kicker, at the same y-height as "PICTURE THIS" but a
  different column, directly below a *left*-column divider rule (L284,
  x0=29.7). No other content anywhere nearby in the bronze data is
  associated with it.

The model fused "PICTURE THIS"+"Spring, roll" into one headline range and
picked the geometrically-unrelated "GAME THEORY" as `section_kicker`,
purely because L282/L285 are three lines apart in stream order despite
sitting in different columns. This is **not** a case of one story
genuinely carrying two stacked kickers that a list-type field would solve
on its own - the story's own correct kicker ("PICTURE THIS") was also
misclassified into the headline. Making `section_kicker` a list would
need to be paired with a column-consistency check that also stops "GAME
THEORY" from being considered a candidate for this story at all.

## Deployment: Hugging Face Spaces (Docker)

Single container: a multi-stage `Dockerfile` builds the React frontend
(`npm run build`) in one stage and installs the Python package in
another, with FastAPI serving both the built frontend and `/api/*` from
one process on port 7860 (HF Docker Spaces hardcode this port).

**No system packages are needed for PDF rendering.** Verified directly
against the installed `pdfplumber==0.11.10`: `page.to_image()`
(`pdfplumber/display.py`) imports `pypdfium2` and calls
`pypdfium2.PdfDocument` directly - no subprocess call to poppler,
ImageMagick, or ghostscript anywhere in this codebase or its rendering
dependency. `pypdfium2`'s wheel bundles a prebuilt PDFium binary. The
Dockerfile still installs `build-essential`/`libjpeg62-turbo`/`zlib1g` as
insurance (see below), not because anything here needs them.

**Editable install, deliberately**, not a regular `pip install .`:
`config.py`'s `DEFAULT_CONFIG_PATH` is computed from `Path(__file__)`,
three parents up - in local dev that resolves to the repo root; a
non-editable install would copy the package into site-packages instead,
breaking that resolution against the real `config/default.yaml`. The
Dockerfile's `WORKDIR /app` plus `pip install -e ".[api]"` keeps
`__file__` anchored at `/app`, the same shape as local dev.

**StaticFiles(html=True) does not do SPA fallback** - verified by reading
Starlette's `StaticFiles.get_response` source directly rather than
assuming: it only serves `index.html` for a request that resolves to a
*directory* (`/`), and returns a plain 404 for any other unmatched path.
A client-side route like `/pipeline` or `/reader/<id>/<page>` has no file
on disk at all, so direct navigation or a page refresh on one would 404
without an explicit fallback. `main.py` registers a catch-all
`/{full_path:path}` route *after* every `/api/*` route (Starlette matches
literal prefixes before catch-alls, so this can't shadow a real API
route) that serves the matching static file if one exists, or
`index.html` otherwise for the SPA to take over client-side routing.

**A real bug this same catch-all introduced, found by actually running
the app rather than reasoning about the routing table**: without a
guard, `GET /api/nonexistent` fell through to the catch-all and returned
200 with `index.html`'s content instead of a 404 - any unmatched `/api/*`
path was silently masked as a successful HTML response, which would hide
real API misuse/typos rather than surfacing them. Fixed by checking
`full_path.startswith("api/")` and raising 404 explicitly before ever
consulting the filesystem. Caught during a local (non-containerized)
smoke test - no local Docker is available in this environment, so
verification for everything else in this section happens against HF's
own build/runtime logs and the live Space URL instead of `docker build`/
`docker run` locally; this specific class of bug is exactly why the app
was run for real rather than only read.

**Startup sanity logging** (`main.py`'s `startup` event): logs Python
version, the resolved `project_root`/data directory and whether it's
writable, whether `frontend/dist` exists, and installed versions of the
packages most likely to differ between the dev machine and HF's build
(`pdfplumber`, `pypdfium2`, `Pillow`, `fastapi`, `uvicorn`,
`google-genai`). Exists specifically because there's no local container to
inspect when something's wrong - a silent path/permission problem needs
to be visible in the first few log lines HF shows after boot, not
discovered later as an opaque 500 on first upload.

**Persistence: re-extract on demand, no external store.** `data/`
(bronze/gold/cache/trace/uploads) lives on the container's own ephemeral
disk - wiped on every rebuild/restart. Already handled gracefully by
existing code with no changes needed: `editions.py`'s
`_iter_edition_date_dirs` returns nothing if `gold_root` doesn't exist at
all, so `list_editions` returns `[]` and the Dashboard shows its normal
empty-upload state on a fresh container rather than erroring. A full
18-page edition is well under the 500 requests/day Gemini quota and well
under two minutes, so re-uploading after a restart is an accepted
tradeoff, not a gap to fix.

**Cold-start gate** (`frontend/src/components/AppReadyGate.tsx`): polls
`/api/health` with exponential backoff (500ms up to 8s between attempts)
before rendering the real app, so the very first request of a session -
which can fail at the network level outright (connection refused/reset)
rather than as a clean HTTP error if the container is still booting from
idle - shows a loading state instead of a blank page. Deliberately
separate from `queries.ts`'s per-query `retry` settings: several of those
are `retry: false` on purpose (e.g. a 404 for "no ranking computed yet"
is a meaningful state, not a transient failure to retry through), so the
cold-start retry logic doesn't touch that.

**Remote strategy: a second git remote on this same repository**, not a
separate deploy repo - `git push space main` redeploys exactly what's on
GitHub, with no second place for the code to drift from. The tradeoff was
cosmetic (HF Spaces requires the Docker/port configuration as YAML
frontmatter at the top of `README.md` specifically, so that block
appeared at the top of the GitHub-facing README too) - moot now: HF
discontinued free Docker Space hosting shortly after this was written,
before the Space itself was ever created, so the frontmatter was removed
from README.md again and the actual deployment moved to a GCP VM - see
"Deployment: GCP e2-micro VM" below. Everything above this point in this
section (the Dockerfile, the SPA-fallback bug it surfaced, the cold-start
gate) remains accurate and the `Dockerfile` is still in the repo as a
working alternative for anywhere Docker *is* available - it's just no
longer what's actually deployed.

## Deployment: GCP e2-micro VM

No Docker (a constraint of the deploying machine, not a technical
requirement - the app has none of its own), and no local way to test any
of this before it runs on the real VM either, the same situation as the
HF attempt above. Bare VM: systemd runs uvicorn directly, nginx reverse-
proxies and terminates Basic Auth in front of it. Every file lives under
`deploy/` (`pressdigest.service`, `nginx-pressdigest.conf`, `setup.sh`,
`deploy.sh`, `pressdigest.env.example`).

**The frontend is built locally, never on the VM.** `e2-micro` has 1GB
RAM (shared-core CPU on top of that); `npm run build`'s toolchain
(esbuild/rollup, both memory-hungry) is a real OOM risk there, and there's
nothing gained by building on a weaker machine than the one already doing
it locally. `deploy.sh` runs `npm run build` on the laptop and ships only
the resulting `frontend/dist/` - never `node_modules`, never a build step
that has to succeed on the VM.

**Ownership: root owns the code and venv, only `data/` belongs to the
service's own user.** This resolves a real conflict rather than being an
arbitrary choice: a setuptools *editable* install (`pip install -e`, kept
for the same `Path(__file__)`-anchored config-path resolution reason as
the earlier Docker work) writes an `egg-info` directory back into the
source tree, which needs the installing user to have write access to
that tree - if `pressdigest` (the low-privilege service user) owned the
code, either every deploy would need to run pip as that user with a
separate permission dance, or the deploy step would need root anyway and
fight the ownership it just set. Simplest resolution: `deploy.sh`'s rsync
runs as root on the remote side (`--rsync-path="sudo rsync"`), pip install
runs as root too, and only `/opt/pressdigest/app/data` - explicitly
excluded from rsync's reach - is chowned to `pressdigest` once by
`setup.sh` and never touched again. The systemd unit's own hardening
(`ProtectSystem=strict`, `ReadWritePaths=/opt/pressdigest/app/data`) is
the actual write-access enforcement; the Unix ownership is what makes
that boundary line up with a directory the service can still write to at
all.

**Concurrency turned down, and made env-overridable rather than forked
into a second config file.** `config/default.yaml`'s
`concurrency.max_concurrent: 4` was calibrated against local runs on a
real (non-shared-core) CPU; the e2-micro's shared-core vCPUs are a
materially weaker, noisier-neighbor environment, so production runs at 2
as a safety margin. Rejected a full `config/production.yaml`: it would
duplicate default.yaml's ~220 lines of calibration comments almost
entirely unchanged, for the sake of one differing value, with a real risk
of the two files silently drifting apart on everything else over time.
`config.py`'s `load_config` instead checks `HINDU_EXTRACT_MAX_CONCURRENT`
(and could grow more entries the same way) and overrides just that key
after loading the YAML - unset in local dev, so default.yaml's own value
applies unchanged there.

**Cache headers, sized against the tightest real budget: 1GB/month free
egress**, not CPU or RAM. Vite's build gives every JS/CSS/font asset a
content-hash filename, so `/assets/*` is cached for a year
(`Cache-Control: immutable`) - a new build is always a new URL, there is
no staleness risk. The raw PDF an edition was extracted from never
changes once uploaded (no edit/replace flow exists), so
`/api/editions/{id}/pdf` is cached for a week - without this, re-opening
the same reader session re-downloads a ~20MB PDF every time, which alone
could exhaust the monthly egress budget in a handful of sessions.
Static assets are served directly by nginx from disk (`alias`, bypassing
uvicorn entirely) rather than through the FastAPI catch-all that also
handles this correctly - not for correctness (both work) but because the
e2-micro's shared-core CPU shouldn't spend a Python process's cycles on a
file nginx can serve itself.

**Basic Auth is non-negotiable, at the nginx layer, applied to every
route** - set at `server` level in `nginx-pressdigest.conf` rather than
per-location, specifically so a new route added later is protected by
default instead of needing someone to remember to add auth to it. This
serves copyrighted newspaper content to a public IP; there is no
acceptable configuration of this deployment without it.

**TLS: recommended now, not deferred**, despite no domain being owned.
Basic Auth's credentials are base64-encoded, not encrypted - sent in
cleartext on every request over plain HTTP, trivially readable by
anything on the network path. Serving Basic-Auth-protected content over
HTTP is a real, immediate exposure, not a hardening nice-to-have to get to
later. Cheapest correct path: a free DNS name from a provider like
DuckDNS pointed at the VM's (reserved, static) external IP, then
`certbot --nginx` for a free Let's Encrypt certificate with automatic
renewal - both zero-cost, and `certbot`'s nginx plugin edits
`nginx-pressdigest.conf`'s server block in place rather than requiring a
hand-written HTTPS server block. (Caddy would get automatic TLS with even
less configuration than certbot, but nginx was the explicit choice here,
so certbot-on-nginx is the path that doesn't contradict that.)

**Static IP: reserve it.** An external IP costs nothing extra *while
attached to a running instance* - GCP only bills a reserved static IP
when it's sitting unattached (e.g. the VM is stopped but the reservation
wasn't released). This is a long-running, always-on personal service, not
something stopped and started often, so the practical cost is $0, and a
stable IP is what makes the DuckDNS-plus-certbot TLS setup above possible
at all - an ephemeral IP changes on certain restarts, silently breaking
the DNS pointer.

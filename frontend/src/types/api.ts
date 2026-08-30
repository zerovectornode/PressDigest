/* eslint-disable */
/**
 * AUTO-GENERATED from hindu_extract/api/schemas.py by scripts/generate_types.py.
 * Do not edit by hand - the Pydantic models are the source of truth.
 */


export interface ParsedMetadataOut {
  edition: string | null;
  date: string | null;
  [k: string]: unknown;
}

export interface PagePhaseOut {
  page_num: number;
  status: "pending" | "extracting" | "grouping" | "done" | "failed";
  articles_found?: number | null;
  validation_ok?: boolean | null;
  needs_review?: boolean | null;
  cached?: boolean | null;
  error?: string | null;
  [k: string]: unknown;
}

export interface JobStatusOut {
  job_id: string;
  edition: string;
  date: string;
  status: "queued" | "running" | "done" | "failed";
  pages_done: number;
  pages_total: number;
  per_page: PagePhaseOut[];
  all_cached: boolean;
  error?: string | null;
  [k: string]: unknown;
}


export interface StartJobOut {
  job_id: string;
  edition: string;
  date: string;
  [k: string]: unknown;
}

export interface EditionSummaryOut {
  edition_id: string;
  edition: string;
  date: string;
  page_count: number;
  article_count: number;
  [k: string]: unknown;
}

export interface EditionDetailOut {
  edition_id: string;
  edition: string;
  date: string;
  page_count: number;
  article_count: number;
  pages_with_articles: number;
  pages_with_zero_articles: number[];
  [k: string]: unknown;
}

export interface ArticleOut {
  article_id: string;
  page: number;
  headline: string;
  headline_raw: string;
  deck: string[];
  deck_raw: string[];
  byline: string;
  byline_raw: string;
  dateline: string;
  dateline_raw: string;
  body: string;
  body_raw: string;
  captions: string[];
  captions_raw: string[];
  is_truncated: boolean;
  continues_on_page: number | null;
  confidence: string;
  rects: number[][];
  validation_ok: boolean;
  needs_review: boolean;
  validation_issues: string[];
  [k: string]: unknown;
}

export interface PageOut {
  page_num: number;
  width: number;
  height: number;
  line_count: number;
  article_count: number;
  validation_ok: boolean;
  coverage_ratio: number | null;
  [k: string]: unknown;
}

export interface RunSummaryOut {
  run_id: string;
  edition: string;
  date: string;
  pdf_hash: string | null;
  page_count: number;
  started_at: string;
  finished_at: string | null;
  total_wall_clock_s: number | null;
  total_tokens: number | null;
  cache_hit_ratio: number | null;
  status: "running" | "done" | "failed";
  [k: string]: unknown;
}

export interface StageEventOut {
  page_num: number;
  stage: string;
  started_at: string;
  ended_at: string;
  duration_s: number;
  detail: {
    [k: string]: unknown;
  };
  error: string | null;
  [k: string]: unknown;
}

export interface RunDetailOut {
  run_id: string;
  edition: string;
  date: string;
  pdf_hash: string | null;
  page_count: number;
  started_at: string;
  finished_at: string | null;
  total_wall_clock_s: number | null;
  total_tokens: number | null;
  cache_hit_ratio: number | null;
  status: "running" | "done" | "failed";
  pages: number[];
  [k: string]: unknown;
}

export interface PageRawOut {
  run_id: string;
  page_num: number;
  prompt: string;
  raw_response: string;
  recorded_at: string;
  [k: string]: unknown;
}

export interface QuotaOut {
  requests_today: number;
  requests_per_day_limit: number;
  tokens_last_minute: number;
  tokens_per_minute_limit: number;
  [k: string]: unknown;
}

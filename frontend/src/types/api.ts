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

export interface PageErrorOut {
  stage: string;
  code: string | number | null;
  message: string;
  attempt_count: number;
  retryable: boolean;
  [k: string]: unknown;
}

export interface PagePhaseOut {
  page_num: number;
  status: "pending" | "extracting" | "grouping" | "done" | "failed";
  current_stage?: string | null;
  articles_found?: number | null;
  validation_ok?: boolean | null;
  needs_review?: boolean | null;
  cached?: boolean | null;
  error?: PageErrorOut | null;
  [k: string]: unknown;
}


export interface JobStatusOut {
  job_id: string;
  edition: string;
  date: string;
  status: "queued" | "running" | "done" | "completed_with_errors" | "failed";
  pages_done: number;
  pages_total: number;
  per_page: PagePhaseOut[];
  all_cached: boolean;
  error?: string | null;
  elapsed_s: number;
  eta_s?: number | null;
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
  extracted_at?: string | null;
  status?: ("running" | "done" | "completed_with_errors" | "failed") | null;
  failed_pages?: number[];
  [k: string]: unknown;
}

export interface EditionDetailOut {
  edition_id: string;
  edition: string;
  date: string;
  page_count: number;
  article_count: number;
  extracted_at?: string | null;
  status?: ("running" | "done" | "completed_with_errors" | "failed") | null;
  failed_pages?: number[];
  pages_with_articles: number;
  pages_with_zero_articles: number[];
  pages: PageStatusOut[];
  [k: string]: unknown;
}
export interface PageStatusOut {
  page_num: number;
  status: "pending" | "in_progress" | "done" | "failed";
  [k: string]: unknown;
}



export interface DeleteEditionOut {
  edition: string;
  date: string;
  bytes_freed: number;
  [k: string]: unknown;
}

export interface ArticleOut {
  article_id: string;
  page: number;
  section_kicker: string;
  section_kicker_raw: string;
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
  status: "pending" | "in_progress" | "done" | "failed";
  width: number | null;
  height: number | null;
  line_count: number | null;
  article_count: number;
  validation_ok: boolean;
  coverage_ratio: number | null;
  error?: PageErrorOut | null;
  [k: string]: unknown;
}


export interface PageArticlesOut {
  status: "pending" | "in_progress" | "done" | "failed";
  articles: ArticleOut[];
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
  status: "running" | "done" | "completed_with_errors" | "failed";
  failed_pages?: number[];
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
  status: "running" | "done" | "completed_with_errors" | "failed";
  failed_pages?: number[];
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

export interface RankedArticleOut {
  article_id: string;
  page: number;
  headline: string;
  rank: number;
  importance_score: number;
  category: string;
  why_it_matters: string;
  exclusion_risk: "none" | "possible_opinion" | "possible_promotional";
  [k: string]: unknown;
}

export interface DuplicateContinuationOut {
  first_part_id: string;
  first_part_page: number;
  continues_on_page: number;
  conflicting_id: string;
  [k: string]: unknown;
}

export interface ExcludedArticleOut {
  article_id: string;
  page: number;
  headline: string;
  reason_code:
    | "PROMOTIONAL"
    | "OPINION_WITHOUT_ANALYSIS"
    | "ENTERTAINMENT"
    | "LOCAL_WITHOUT_BROADER_RELEVANCE"
    | "ROUTINE_STATEMENT"
    | "CONTINUATION_OF_EARLIER_ARTICLE"
    | "BELOW_THRESHOLD"
    | "OTHER";
  note: string;
  [k: string]: unknown;
}

export interface RankingOut {
  generated_at: string;
  top_n: number;
  ranked: RankedArticleOut[];
  excluded: ExcludedArticleOut[];
  validation_ok: boolean;
  validation_issues: string[];
  duplicate_continuations: DuplicateContinuationOut[];
  retried: boolean;
  eligible_count_note: string | null;
  total_tokens: number;
  all_cached: boolean;
  [k: string]: unknown;
}




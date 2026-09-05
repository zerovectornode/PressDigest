import type { JobStatusOut, PagePhaseOut } from '../types/api'

function statusColor(status: PagePhaseOut['status']): string {
  switch (status) {
    case 'done':
      return 'bg-teal-500'
    case 'failed':
      return 'bg-rose-500'
    case 'extracting':
    case 'grouping':
      return 'bg-amber-400'
    default:
      return 'bg-slate-200'
  }
}

// The user-facing label for each trace stage - see trace.STAGE_NAMES.
// "ranking" is edition-wide, never reported on a per-page basis here.
const STAGE_LABELS: Record<string, string> = {
  char_extraction: 'extracting characters',
  line_building: 'building lines',
  ligature_canary: 'checking for dropped glyphs',
  gemini_call: 'identifying articles (Gemini)',
  validation: 'validating',
  assembly: 'assembling article',
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${Math.ceil(seconds)}s remaining`
  return `~${Math.ceil(seconds / 60)}m remaining`
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s elapsed`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s elapsed`
}

export function ProgressPanel({ job }: { job: JobStatusOut }) {
  // Phase 1 (char_extraction/line_building/ligature_canary) is strictly
  // sequential - at most one page will ever be "extracting". Phase 2
  // (gemini_call/validation/assembly) runs multiple pages concurrently
  // (config.concurrency.max_concurrent) - showing all of them, not just
  // the first, is what makes that parallelism visible instead of looking
  // stalled.
  const inFlight = job.per_page.filter((p) => p.status === 'extracting' || p.status === 'grouping')
  const articleCount = job.per_page.reduce((sum, p) => sum + (p.articles_found ?? 0), 0)
  const failedPages = job.per_page.filter((p) => p.status === 'failed')
  const failures = job.per_page.filter((p) => p.status === 'failed' || p.validation_ok === false)

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-slate-700">
          {job.status === 'done'
            ? `Done - ${job.pages_total} page${job.pages_total === 1 ? '' : 's'} processed`
            : job.status === 'completed_with_errors'
              ? `Completed with errors - ${failedPages.length} of ${job.pages_total} page${job.pages_total === 1 ? '' : 's'} failed`
              : job.status === 'failed'
                ? 'Job failed'
                : `${job.pages_done} of ${job.pages_total || '?'} pages done`}
        </span>
        <span className="text-sm text-slate-500">{articleCount} article{articleCount === 1 ? '' : 's'} found</span>
      </div>

      {job.status === 'running' && (
        <div className="flex items-center gap-3 text-xs text-slate-400">
          <span>{formatElapsed(job.elapsed_s)}</span>
          {job.eta_s !== null && job.eta_s !== undefined && <span>({formatEta(job.eta_s)})</span>}
        </div>
      )}

      {job.status === 'running' && inFlight.length > 0 && (
        <div className="flex flex-col gap-1">
          {inFlight.map((p) => (
            <p key={p.page_num} className="text-xs text-slate-600">
              Page {p.page_num} of {job.pages_total} — {STAGE_LABELS[p.current_stage ?? ''] ?? 'processing'}
            </p>
          ))}
        </div>
      )}

      {job.all_cached && job.status === 'done' && (
        <p className="rounded-lg bg-teal-50 px-3 py-2 text-xs text-teal-700">
          This PDF was already processed - served entirely from cache.
        </p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {job.per_page.map((p) => (
          <div
            key={p.page_num}
            title={`page ${p.page_num}: ${p.status}${p.error ? ` - ${p.error.stage}: ${p.error.message}` : ''}`}
            className={`h-3 w-3 rounded-sm ${statusColor(p.status)}`}
          />
        ))}
      </div>

      {job.error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{job.error}</p>}

      {failures.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="font-medium">
            {failures.length} page{failures.length === 1 ? '' : 's'} need review:
          </span>
          {failures.map((p) => (
            <span key={p.page_num}>
              page {p.page_num}: {p.error ? `${p.error.stage}: ${p.error.message}` : p.validation_ok === false ? 'boundary validation failed' : p.status}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

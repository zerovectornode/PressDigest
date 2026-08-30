import { useEffect, useRef, useState } from 'react'
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

/** Live ETA from this job's own observed pace - no server timing data
 * needed, just a client-side start timestamp and the pages_done count the
 * job status already reports. avg-seconds-per-completed-page extrapolated
 * to the remaining pages, recomputed every tick so it tightens as more
 * pages actually finish - never a fabricated/static estimate. */
function useEtaSeconds(job: JobStatusOut): number | null {
  const startRef = useRef<number | null>(null)
  const [, forceTick] = useState(0)

  if (startRef.current === null && job.status === 'running') {
    startRef.current = Date.now()
  }

  useEffect(() => {
    if (job.status !== 'running') return
    const id = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [job.status])

  if (job.status !== 'running' || startRef.current === null || job.pages_done === 0) {
    return null
  }
  const elapsedS = (Date.now() - startRef.current) / 1000
  const avgPerPage = elapsedS / job.pages_done
  const remaining = job.pages_total - job.pages_done
  return remaining > 0 ? avgPerPage * remaining : 0
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${Math.ceil(seconds)}s remaining`
  return `~${Math.ceil(seconds / 60)}m remaining`
}

export function ProgressPanel({ job }: { job: JobStatusOut }) {
  const currentPage = job.per_page.find((p) => p.status === 'extracting' || p.status === 'grouping')
  const articleCount = job.per_page.reduce((sum, p) => sum + (p.articles_found ?? 0), 0)
  const failures = job.per_page.filter((p) => p.status === 'failed' || p.validation_ok === false)
  const etaSeconds = useEtaSeconds(job)

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-700">
          {job.status === 'done'
            ? `Done - ${job.pages_total} page${job.pages_total === 1 ? '' : 's'} processed`
            : job.status === 'failed'
              ? 'Job failed'
              : currentPage
                ? `Page ${currentPage.page_num} of ${job.pages_total}`
                : `Starting - ${job.pages_total || '?'} pages`}
          {etaSeconds !== null && <span className="ml-2 text-slate-400">({formatEta(etaSeconds)})</span>}
        </span>
        <span className="text-sm text-slate-500">{articleCount} article{articleCount === 1 ? '' : 's'} found</span>
      </div>

      {job.all_cached && job.status === 'done' && (
        <p className="rounded-lg bg-teal-50 px-3 py-2 text-xs text-teal-700">
          This PDF was already processed - served entirely from cache.
        </p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {job.per_page.map((p) => (
          <div
            key={p.page_num}
            title={`page ${p.page_num}: ${p.status}${p.error ? ` - ${p.error}` : ''}`}
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
              page {p.page_num}: {p.error ?? (p.validation_ok === false ? 'boundary validation failed' : p.status)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

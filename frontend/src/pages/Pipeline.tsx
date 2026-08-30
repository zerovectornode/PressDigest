import { Link, useParams } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { PipelineRunDetail } from '../components/pipeline/PipelineRunDetail'
import { formatTimestamp, runType } from '../lib/format'
import { useQuota, useRuns } from '../lib/queries'

function statusBadge(status: string): string {
  switch (status) {
    case 'done':
      return 'bg-teal-50 text-teal-700'
    case 'failed':
      return 'bg-rose-50 text-rose-700'
    default:
      return 'bg-amber-50 text-amber-700'
  }
}

function QuotaBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>{label}</span>
        <span>
          {used.toLocaleString()} / {limit.toLocaleString()}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${pct > 90 ? 'bg-rose-500' : pct > 60 ? 'bg-amber-400' : 'bg-teal-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function QuotaWidget() {
  const { data: quota } = useQuota()
  if (!quota) return null
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700">Quota</h2>
      <QuotaBar label="Requests today" used={quota.requests_today} limit={quota.requests_per_day_limit} />
      <QuotaBar label="Tokens (last 60s)" used={quota.tokens_last_minute} limit={quota.tokens_per_minute_limit} />
    </div>
  )
}

function RunList() {
  const { data: runs, isLoading } = useRuns()

  if (isLoading) return <p className="text-sm text-slate-400">Loading runs...</p>
  if (!runs || runs.length === 0) {
    return (
      <EmptyState
        title="No extraction runs yet"
        description="Every extraction (CLI or upload) is traced here once it starts."
        linkTo="/"
        linkLabel="Ingest an edition"
      />
    )
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
          <th className="py-2 pr-4">Started</th>
          <th className="py-2 pr-4">Run</th>
          <th className="py-2 pr-4">Type</th>
          <th className="py-2 pr-4">Edition</th>
          <th className="py-2 pr-4">Pages</th>
          <th className="py-2 pr-4">Total time</th>
          <th className="py-2 pr-4">Total tokens</th>
          <th className="py-2 pr-4">Cache hit</th>
          <th className="py-2 pr-4">Status</th>
        </tr>
      </thead>
      <tbody>
        {/* Most recent first - already the backend's sort order
            (trace.list_runs: ORDER BY started_at DESC), not re-sorted here. */}
        {runs.map((run) => (
          <tr key={run.run_id} className="border-b border-slate-100 hover:bg-slate-50">
            <td className="py-2 pr-4">
              <Link to={`/pipeline/${run.run_id}`} className="font-medium text-teal-700 hover:underline">
                {formatTimestamp(run.started_at)}
              </Link>
            </td>
            <td className="py-2 pr-4 font-mono text-xs text-slate-400">{run.run_id.slice(0, 8)}</td>
            <td className="py-2 pr-4 text-xs text-slate-500">{runType(run.pdf_hash)}</td>
            <td className="py-2 pr-4 capitalize">{run.edition}</td>
            <td className="py-2 pr-4">{run.page_count}</td>
            <td className="py-2 pr-4">{run.total_wall_clock_s ? `${run.total_wall_clock_s.toFixed(1)}s` : '-'}</td>
            <td className="py-2 pr-4">{run.total_tokens?.toLocaleString() ?? '-'}</td>
            <td className="py-2 pr-4">
              {run.cache_hit_ratio !== null && run.cache_hit_ratio !== undefined
                ? `${Math.round(run.cache_hit_ratio * 100)}%`
                : '-'}
            </td>
            <td className="py-2 pr-4">
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadge(run.status)}`}>
                {run.status}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function Pipeline() {
  const { runId } = useParams<{ runId: string }>()

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8 px-8 py-10">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Pipeline</h1>
        <p className="mt-1 text-sm text-slate-500">
          Extraction run history, per-stage timing, token usage, and validation results.
        </p>
      </div>

      {runId ? (
        <PipelineRunDetail runId={runId} />
      ) : (
        <>
          <QuotaWidget />
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <RunList />
          </div>
        </>
      )}
    </div>
  )
}

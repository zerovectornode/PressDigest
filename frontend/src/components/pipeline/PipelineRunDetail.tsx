import { useState } from 'react'
import { Link } from 'react-router-dom'
import { makeEditionId } from '../../lib/api'
import { formatTimestamp, runType } from '../../lib/format'
import { useRetryFailedPages, useRun, useRunPageRaw, useRunPagesStages } from '../../lib/queries'
import type { StageEventOut } from '../../types/api'

function RetryFailedPagesButton({ edition, date, failedPages }: { edition: string; date: string; failedPages: number[] }) {
  const editionId = makeEditionId(edition, date)
  const retryFailed = useRetryFailedPages(editionId)
  return (
    <button
      onClick={() => retryFailed.mutate()}
      disabled={retryFailed.isPending}
      className="rounded-md border border-rose-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {retryFailed.isPending
        ? 'Retrying...'
        : retryFailed.isSuccess
          ? 'Retrying in background...'
          : `Retry ${failedPages.length} failed page${failedPages.length === 1 ? '' : 's'}`}
    </button>
  )
}

// Matches trace.py's STAGE_NAMES for the per-page stages (ranking is
// edition-wide, handled separately - see isRankingRun below). No
// 'render' entry: the vision-image render this used to show was removed
// as dead code (nothing ever read it back - see design/DESIGN.md
// "Removed: the unused vision-image render"), and the backend hasn't
// emitted that stage since, so a 'render' column here would only ever
// render empty - misleading, not just unused.
const STAGE_ORDER = [
  'char_extraction',
  'line_building',
  'ligature_canary',
  'gemini_call',
  'validation',
  'assembly',
] as const

const STAGE_COLOR: Record<string, string> = {
  char_extraction: 'bg-slate-400',
  line_building: 'bg-sky-400',
  ligature_canary: 'bg-violet-400',
  gemini_call: 'bg-amber-500',
  validation: 'bg-teal-500',
  assembly: 'bg-cyan-500',
}

type Tab = 'timeline' | 'tokens' | 'validation' | 'raw'

function eventsByStage(events: StageEventOut[]): Record<string, StageEventOut> {
  const out: Record<string, StageEventOut> = {}
  for (const e of events) out[e.stage] = e
  return out
}

function StageTimelineTab({ pages, stagesByPage }: { pages: number[]; stagesByPage: Record<number, StageEventOut[]> }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        {STAGE_ORDER.map((s) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-sm ${STAGE_COLOR[s]}`} />
            {s}
          </span>
        ))}
      </div>
      {pages.map((pageNum) => {
        const events = stagesByPage[pageNum] ?? []
        const byStage = eventsByStage(events)
        const total = events.reduce((sum, e) => sum + e.duration_s, 0) || 1
        // Surfaces retry cost specifically, not just total duration - a
        // page that took 40s because it retried three times looks very
        // different from one that took 40s because Gemini was just slow,
        // and the segment width alone can't tell the two apart.
        const geminiDetail = byStage['gemini_call']?.detail as { attempt_count?: number; sleep_total_s?: number } | undefined
        const attemptCount = geminiDetail?.attempt_count ?? 1
        return (
          <div key={pageNum} className="flex items-center gap-3">
            <span className="w-16 shrink-0 text-xs text-slate-500">page {pageNum}</span>
            <div className="flex h-5 flex-1 overflow-hidden rounded-md bg-slate-100">
              {STAGE_ORDER.map((stage) => {
                const e = byStage[stage]
                if (!e) return null
                const widthPct = (e.duration_s / total) * 100
                const detail = e.detail as { attempts?: Array<{ error_message?: string | null }> } | undefined
                const attemptErrors = (detail?.attempts ?? [])
                  .map((a) => a.error_message)
                  .filter((m): m is string => Boolean(m))
                const tooltip = [
                  `${stage}: ${e.duration_s.toFixed(2)}s`,
                  e.error ? `ERROR: ${e.error}` : null,
                  attemptErrors.length > 0 ? `failed attempts: ${attemptErrors.join(' | ')}` : null,
                ]
                  .filter(Boolean)
                  .join(' - ')
                return (
                  <div
                    key={stage}
                    title={tooltip}
                    className={`${STAGE_COLOR[stage]} ${e.error ? 'ring-2 ring-inset ring-rose-600' : ''} h-full`}
                    style={{ width: `${widthPct}%` }}
                  />
                )
              })}
            </div>
            {attemptCount > 1 && (
              <span
                title={`Gemini call took ${attemptCount} attempts (${(geminiDetail?.sleep_total_s ?? 0).toFixed(1)}s spent sleeping between retries)`}
                className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-700"
              >
                {attemptCount} attempts
              </span>
            )}
            <span className="w-14 shrink-0 text-right text-xs text-slate-400">{total.toFixed(2)}s</span>
          </div>
        )
      })}
    </div>
  )
}

function TokenBreakdownTab({ pages, stagesByPage }: { pages: number[]; stagesByPage: Record<number, StageEventOut[]> }) {
  let runningTotal = 0
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3 text-xs text-slate-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-slate-400" /> prompt
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-violet-400" /> thinking
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-amber-500" /> output
        </span>
      </div>
      {pages.map((pageNum) => {
        const geminiEvent = eventsByStage(stagesByPage[pageNum] ?? [])['gemini_call']
        const detail = geminiEvent?.detail ?? {}
        const prompt = Number(detail.prompt_token_count ?? 0)
        const thinking = Number(detail.thoughts_token_count ?? 0)
        const output = Number(detail.candidates_token_count ?? 0)
        const total = prompt + thinking + output
        runningTotal += total
        const maxBar = 20000 // visual scale reference, not a hard limit
        return (
          <div key={pageNum} className="flex items-center gap-3">
            <span className="w-16 shrink-0 text-xs text-slate-500">page {pageNum}</span>
            <div className="flex h-5 flex-1 overflow-hidden rounded-md bg-slate-100">
              <div className="h-full bg-slate-400" style={{ width: `${Math.min(100, (prompt / maxBar) * 100)}%` }} />
              <div className="h-full bg-violet-400" style={{ width: `${Math.min(100, (thinking / maxBar) * 100)}%` }} />
              <div className="h-full bg-amber-500" style={{ width: `${Math.min(100, (output / maxBar) * 100)}%` }} />
            </div>
            <span className="w-20 shrink-0 text-right text-xs text-slate-400">
              {total ? total.toLocaleString() : '-'}
              {detail.cache_hit ? ' (cached)' : ''}
            </span>
          </div>
        )
      })}
      <p className="mt-2 text-xs text-slate-500">
        Running total: <span className="font-medium text-slate-700">{runningTotal.toLocaleString()}</span> tokens
        against a 250,000 TPM / 500 RPD budget.
      </p>
    </div>
  )
}

function ValidationTab({ pages, stagesByPage }: { pages: number[]; stagesByPage: Record<number, StageEventOut[]> }) {
  const [expandedPage, setExpandedPage] = useState<number | null>(null)
  return (
    <div className="flex flex-col gap-2">
      {pages.map((pageNum) => {
        const event = eventsByStage(stagesByPage[pageNum] ?? [])['validation']
        const detail = event?.detail ?? {}
        const ok = Boolean(detail.ok)
        const checksums = (detail.checksum_mismatches as Array<Record<string, unknown>>) ?? []
        const contiguity = (detail.contiguity_issues as Array<Record<string, unknown>>) ?? []
        const overlaps = (detail.overlap_issues as Array<Record<string, unknown>>) ?? []
        const headlineQuality = (detail.headline_quality_issues as Array<Record<string, unknown>>) ?? []
        // Ranking runs (see ranking.py) record a single edition-wide
        // validation event under the page_num=0 sentinel, with its own
        // issues/duplicate_continuations/excluded shape rather than the
        // per-page checksum/contiguity/overlap one.
        const rankingIssues = (detail.issues as string[]) ?? []
        const duplicateContinuations = (detail.duplicate_continuations as Array<Record<string, unknown>>) ?? []
        const excluded = (detail.excluded as Array<Record<string, unknown>>) ?? []
        // Ranking's validation detail uses issues/duplicate_continuations/
        // excluded; the per-page pipeline's uses checksum_mismatches/
        // contiguity_issues/overlap_issues/coverage_ratio - 'issues' only
        // exists on the former, so it's a reliable discriminator.
        const isRankingRun = 'issues' in detail
        const failureCount = isRankingRun
          ? rankingIssues.length + duplicateContinuations.length
          : checksums.length + contiguity.length + overlaps.length + headlineQuality.length
        const isExpanded = expandedPage === pageNum

        return (
          <div key={pageNum} className="rounded-lg border border-slate-200">
            <button
              onClick={() => setExpandedPage(isExpanded ? null : pageNum)}
              className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm"
            >
              <span className="font-medium text-slate-700">{pageNum === 0 ? 'edition-wide (ranking)' : `page ${pageNum}`}</span>
              <span className="flex items-center gap-3 text-xs">
                {!isRankingRun && (
                  <span className="text-slate-400">
                    coverage {detail.coverage_ratio !== undefined ? `${Math.round(Number(detail.coverage_ratio) * 100)}%` : '-'}
                  </span>
                )}
                {isRankingRun && excluded.length > 0 && <span className="text-slate-400">{excluded.length} rejected candidate(s)</span>}
                <span className={ok ? 'text-teal-600' : 'text-rose-600'}>
                  {ok ? 'OK' : `${failureCount} failure(s)`}
                </span>
              </span>
            </button>
            {isExpanded && (failureCount > 0 || excluded.length > 0) && (
              <div className="flex flex-col gap-2 border-t border-slate-100 px-4 py-3 text-xs text-slate-600">
                {checksums.map((m, i) => (
                  <div key={`c${i}`} className="rounded bg-rose-50 p-2">
                    <span className="font-medium">checksum[{String(m.field)}] article {String(m.article_id)}:</span>{' '}
                    {String(m.detail)}
                  </div>
                ))}
                {contiguity.map((c, i) => (
                  <div key={`g${i}`} className="rounded bg-rose-50 p-2">
                    <span className="font-medium">contiguity L{String(c.line_no)}:</span> {String(c.detail)}
                  </div>
                ))}
                {overlaps.map((o, i) => (
                  <div key={`o${i}`} className="rounded bg-rose-50 p-2">
                    articles {String(o.article_id_a)} and {String(o.article_id_b)} overlap: {JSON.stringify(o.range_a)}{' '}
                    vs {JSON.stringify(o.range_b)}
                  </div>
                ))}
                {headlineQuality.map((h, i) => (
                  <div key={`h${i}`} className="rounded bg-rose-50 p-2">
                    <span className="font-medium">headline_quality article {String(h.article_id)}:</span> {String(h.detail)}
                  </div>
                ))}
                {rankingIssues.map((issue, i) => (
                  <div key={`r${i}`} className="rounded bg-rose-50 p-2">
                    {issue}
                  </div>
                ))}
                {duplicateContinuations.map((d, i) => (
                  <div key={`d${i}`} className="rounded bg-rose-50 p-2">
                    duplicate continuation: {String(d.first_part_id)} (p{String(d.first_part_page)}, continues on p
                    {String(d.continues_on_page)}) vs {String(d.conflicting_id)}
                  </div>
                ))}
                {excluded.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <span className="font-medium text-slate-500">Rejected candidates:</span>
                    {excluded.map((e, i) => (
                      <div key={`e${i}`} className="rounded bg-amber-50 p-2">
                        <span className="font-medium">[{String(e.reason_code)}] {String(e.article_id)}:</span>{' '}
                        {String(e.note)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function RawInspectorTab({ runId, pages }: { runId: string; pages: number[] }) {
  const [selectedPage, setSelectedPage] = useState<number | undefined>(pages[0])
  const rawQuery = useRunPageRaw(runId, selectedPage)

  return (
    <div className="flex flex-col gap-3">
      <select
        value={selectedPage ?? ''}
        onChange={(e) => setSelectedPage(Number(e.target.value))}
        className="w-40 rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
      >
        {pages.map((p) => (
          <option key={p} value={p}>
            page {p}
          </option>
        ))}
      </select>

      {rawQuery.isLoading && <p className="text-sm text-slate-400">Loading...</p>}
      {rawQuery.isError && <p className="text-sm text-slate-400">No raw exchange recorded for this page.</p>}
      {rawQuery.data && (
        <div className="grid grid-cols-2 gap-4">
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Prompt sent (includes the line-numbered dump the model saw)
            </h3>
            <pre className="max-h-[32rem] overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
              {rawQuery.data.prompt}
            </pre>
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Raw model response</h3>
            <pre className="max-h-[32rem] overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
              {rawQuery.data.raw_response}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

export function PipelineRunDetail({ runId }: { runId: string }) {
  const [tab, setTab] = useState<Tab>('timeline')
  const runQuery = useRun(runId)
  const pages = runQuery.data?.pages ?? []
  const stageQueries = useRunPagesStages(runId, pages)

  const stagesByPage: Record<number, StageEventOut[]> = {}
  pages.forEach((p, i) => {
    stagesByPage[p] = stageQueries[i]?.data ?? []
  })

  if (runQuery.isLoading) return <p className="text-sm text-slate-400">Loading run...</p>
  if (!runQuery.data) return <p className="text-sm text-slate-400">Run not found.</p>

  const run = runQuery.data

  const tabs: { id: Tab; label: string }[] = [
    { id: 'timeline', label: 'Per-page timeline' },
    { id: 'tokens', label: 'Token breakdown' },
    { id: 'validation', label: 'Validation' },
    { id: 'raw', label: 'Raw inspector' },
  ]

  return (
    <div className="flex flex-col gap-6">
      <Link to="/pipeline" className="text-sm text-teal-700 hover:underline">
        ← All runs
      </Link>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="font-semibold text-slate-800 capitalize">
            {run.edition} - {run.date}
          </span>
          <span className="font-mono text-xs text-slate-400">{run.run_id.slice(0, 8)}</span>
          <span className="text-xs text-slate-500">{runType(run.pdf_hash)}</span>
          <span className="text-slate-500">{formatTimestamp(run.started_at)}</span>
          <span className="text-slate-500">{run.page_count} pages</span>
          <span className="text-slate-500">{run.total_wall_clock_s ? `${run.total_wall_clock_s.toFixed(1)}s` : 'running...'}</span>
          <span className="text-slate-500">{run.total_tokens?.toLocaleString() ?? '-'} tokens</span>
          <span className="text-slate-500">
            {run.cache_hit_ratio !== null && run.cache_hit_ratio !== undefined
              ? `${Math.round(run.cache_hit_ratio * 100)}% cached`
              : '-'}
          </span>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              run.status === 'done'
                ? 'bg-teal-50 text-teal-700'
                : run.status === 'completed_with_errors' || run.status === 'failed'
                  ? 'bg-rose-50 text-rose-700'
                  : 'bg-amber-50 text-amber-700'
            }`}
          >
            {run.status.replace(/_/g, ' ')}
          </span>
          {(run.failed_pages ?? []).length > 0 && (
            <RetryFailedPagesButton edition={run.edition} date={run.date} failedPages={run.failed_pages ?? []} />
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.id ? 'border-b-2 border-teal-600 text-teal-700' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        {tab === 'timeline' && <StageTimelineTab pages={pages} stagesByPage={stagesByPage} />}
        {tab === 'tokens' && <TokenBreakdownTab pages={pages} stagesByPage={stagesByPage} />}
        {tab === 'validation' && <ValidationTab pages={pages} stagesByPage={stagesByPage} />}
        {tab === 'raw' && <RawInspectorTab runId={runId} pages={pages} />}
      </div>
    </div>
  )
}

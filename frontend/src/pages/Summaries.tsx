import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { SummaryCardGrid } from '../components/SummaryCardGrid'
import { useEditions, useRanking, useTriggerRanking } from '../lib/queries'
import type { RankedArticleOut } from '../types/api'

export function Summaries() {
  const navigate = useNavigate()
  const { data: editions, isLoading: editionsLoading } = useEditions()
  const [selectedEditionId, setSelectedEditionId] = useState<string | undefined>(undefined)
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null)

  const editionId = selectedEditionId ?? editions?.[0]?.edition_id
  const rankingQuery = useRanking(editionId)
  const triggerRanking = useTriggerRanking(editionId)

  if (editionsLoading) {
    return <p className="p-6 text-sm text-slate-400">Loading...</p>
  }

  if (!editions || editions.length === 0) {
    return (
      <EmptyState
        title="No editions ingested yet"
        description="Ingest an edition first, then come back here to rank its most important articles."
        linkTo="/"
        linkLabel="Go to Home"
      />
    )
  }

  const ranking = rankingQuery.data
  const categories = ranking ? Array.from(new Set(ranking.ranked.map((a) => a.category))).sort() : []
  const filtered = ranking && categoryFilter ? ranking.ranked.filter((a) => a.category === categoryFilter) : (ranking?.ranked ?? [])
  const sorted = [...filtered].sort((a, b) => a.rank - b.rank)

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-8 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Summaries</h1>
          <p className="mt-1 text-sm text-slate-500">
            Top {ranking?.top_n ?? 20} articles, ranked by editorial importance across the whole edition.
          </p>
        </div>
        {editions.length > 1 && (
          <select
            value={editionId}
            onChange={(e) => setSelectedEditionId(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            {editions.map((e) => (
              <option key={e.edition_id} value={e.edition_id}>
                {e.edition} - {e.date}
              </option>
            ))}
          </select>
        )}
      </div>

      {rankingQuery.isLoading && <p className="text-sm text-slate-400">Loading ranking...</p>}

      {!rankingQuery.isLoading && !ranking && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-white py-16 text-center">
          <p className="text-sm text-slate-500">This edition hasn't been ranked yet.</p>
          <button
            onClick={() => triggerRanking.mutate()}
            disabled={triggerRanking.isPending}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {triggerRanking.isPending ? 'Ranking... (this can take a minute)' : 'Rank this edition'}
          </button>
          {triggerRanking.isError && <p className="text-xs text-rose-600">{(triggerRanking.error as Error).message}</p>}
        </div>
      )}

      {ranking && (
        <>
          {!ranking.validation_ok && (
            <div className="flex flex-col gap-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <span className="font-medium">{ranking.validation_issues.length} validation issue(s):</span>
              {ranking.validation_issues.map((issue, i) => (
                <span key={i}>- {issue}</span>
              ))}
            </div>
          )}
          {ranking.duplicate_continuations.length > 0 && (
            <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {ranking.duplicate_continuations.length} possible duplicate continuation(s) detected - a story's
              first part and its continuation may both have been ranked.
            </div>
          )}
          {ranking.eligible_count_note && <p className="text-xs text-slate-500">{ranking.eligible_count_note}</p>}

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => setCategoryFilter(null)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                !categoryFilter ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              All
            </button>
            {categories.map((c) => (
              <button
                key={c}
                onClick={() => setCategoryFilter(c)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                  categoryFilter === c ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {c.replace(/_/g, ' ')}
              </button>
            ))}
          </div>

          <SummaryCardGrid
            articles={sorted}
            onSelect={(article: RankedArticleOut) => navigate(`/reader/${editionId}/${article.page}`)}
          />
        </>
      )}
    </div>
  )
}

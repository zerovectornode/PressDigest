/**
 * Summary card grid for the edition-wide importance ranking. Takes no
 * default/mock data - whoever renders this is required to pass real ranked
 * articles from the backend, so it can never accidentally ship a
 * fabricated score or category.
 */
import type { RankedArticleOut } from '../types/api'

const CATEGORY_COLORS: Record<string, string> = {
  POLITY_GOVERNANCE: 'bg-indigo-50 text-indigo-700',
  ECONOMY: 'bg-amber-50 text-amber-700',
  INTERNATIONAL: 'bg-sky-50 text-sky-700',
  ENVIRONMENT: 'bg-emerald-50 text-emerald-700',
  SCIENCE_TECH: 'bg-violet-50 text-violet-700',
  SOCIAL_ISSUES: 'bg-rose-50 text-rose-700',
  JUDICIARY: 'bg-slate-100 text-slate-700',
  SECURITY_DEFENCE: 'bg-red-50 text-red-700',
  AGRICULTURE: 'bg-lime-50 text-lime-700',
  HEALTH: 'bg-pink-50 text-pink-700',
  EDUCATION: 'bg-cyan-50 text-cyan-700',
  OTHER: 'bg-gray-100 text-gray-600',
}

function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? CATEGORY_COLORS.OTHER
}

export function SummaryCardGrid({
  articles,
  onSelect,
}: {
  articles: RankedArticleOut[]
  onSelect?: (article: RankedArticleOut) => void
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {articles.map((article) => (
        <article
          key={article.article_id}
          onClick={() => onSelect?.(article)}
          className={`flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-colors ${
            onSelect ? 'cursor-pointer hover:border-teal-300 hover:bg-teal-50/30' : ''
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-[11px] font-semibold text-white">
                {article.rank}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${categoryColor(article.category)}`}>
                {article.category.replace(/_/g, ' ')}
              </span>
            </span>
            <span className="text-xs font-medium text-slate-400">{article.importance_score}/100</span>
          </div>
          <h3 className="font-serif text-base font-semibold leading-snug text-slate-900">{article.headline}</h3>
          <p className="text-sm text-slate-600">{article.why_it_matters}</p>
          <div className="mt-auto flex items-center justify-between pt-1 text-xs text-slate-400">
            <span>page {article.page}</span>
            {article.exclusion_risk !== 'none' && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700">
                {article.exclusion_risk.replace('possible_', '')}
              </span>
            )}
          </div>
        </article>
      ))}
    </div>
  )
}

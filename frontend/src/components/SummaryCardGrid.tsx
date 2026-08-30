/**
 * Summary card grid - built ahead of the ranking/summarisation pipeline so
 * the layout is ready the day that data exists, but NOT wired up or
 * rendered anywhere yet (see SUMMARIES_ENABLED in pages/Summaries.tsx).
 *
 * Deliberately takes no default/mock data: whoever flips the feature flag
 * on is required to pass real ranked+summarised articles, so this can
 * never accidentally ship fabricated relevance scores or summaries.
 */
export interface RankedArticleSummary {
  article_id: string
  headline: string
  summary: string
  relevance_score: number
  category: string
}

export function SummaryCardGrid({ articles }: { articles: RankedArticleSummary[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {articles.map((article) => (
        <article
          key={article.article_id}
          className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
        >
          <div className="flex items-center justify-between">
            <span className="rounded-full bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-700">
              {article.category}
            </span>
            <span className="text-xs font-medium text-slate-400">
              {Math.round(article.relevance_score * 100)}%
            </span>
          </div>
          <h3 className="font-sans text-sm font-semibold text-slate-900">{article.headline}</h3>
          <p className="line-clamp-4 text-sm text-slate-600">{article.summary}</p>
        </article>
      ))}
    </div>
  )
}

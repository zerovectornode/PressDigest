import { useState } from 'react'
import type { ArticleOut } from '../types/api'

function confidenceColor(confidence: string): string {
  switch (confidence) {
    case 'high':
      return 'bg-teal-50 text-teal-700'
    case 'medium':
      return 'bg-amber-50 text-amber-700'
    default:
      return 'bg-rose-50 text-rose-700'
  }
}

export function ArticleCard({
  article,
  isActive,
  onHover,
  onClick,
}: {
  article: ArticleOut
  isActive: boolean
  onHover: (id: string | null) => void
  onClick: (id: string) => void
}) {
  const [showRaw, setShowRaw] = useState(false)

  return (
    <article
      id={`article-${article.article_id}`}
      onMouseEnter={() => onHover(article.article_id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onClick(article.article_id)}
      className={`flex cursor-pointer flex-col gap-2 rounded-xl border p-5 transition-colors ${
        isActive ? 'border-teal-300 bg-teal-50/30' : 'border-slate-200 bg-white hover:border-slate-300'
      }`}
      style={{ maxWidth: '68ch' }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${confidenceColor(article.confidence)}`}>
          {article.confidence} confidence
        </span>
        {article.needs_review && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
            ⚠ needs review
          </span>
        )}
      </div>

      {article.headline && (
        <h2 className="font-serif text-xl font-semibold leading-snug text-slate-900">{article.headline}</h2>
      )}
      {article.deck.map((line, i) => (
        <p key={i} className="font-serif text-base italic leading-snug text-slate-600">
          {line}
        </p>
      ))}

      {(article.byline || article.dateline) && (
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {[article.byline, article.dateline].filter(Boolean).join(' | ')}
        </p>
      )}

      {article.body && (
        <p className="font-serif text-[19px] leading-[1.65] text-slate-800" style={{ maxWidth: '68ch' }}>
          {showRaw ? article.body_raw : article.body}
        </p>
      )}

      {article.captions.map((caption, i) => (
        <p key={i} className="border-l-2 border-slate-200 pl-3 text-sm italic text-slate-500">
          {caption}
        </p>
      ))}

      <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-400">
        {article.is_truncated && (
          <span>{article.continues_on_page ? `Continues on page ${article.continues_on_page}` : 'Truncated'}</span>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation()
            setShowRaw((v) => !v)
          }}
          className="underline decoration-dotted hover:text-slate-600"
        >
          {showRaw ? 'show cleaned text' : 'show raw text'}
        </button>
        {article.validation_issues.length > 0 && (
          <span className="text-rose-500">{article.validation_issues.length} validation issue(s)</span>
        )}
      </div>
    </article>
  )
}

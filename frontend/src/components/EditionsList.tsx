import { Link } from 'react-router-dom'
import { formatTimestamp } from '../lib/format'
import { useEditions } from '../lib/queries'

function statusBadgeClass(status: string | null | undefined): string {
  switch (status) {
    case 'running':
      return 'bg-amber-100 text-amber-700'
    case 'failed':
      return 'bg-rose-100 text-rose-700'
    default:
      return 'bg-teal-100 text-teal-700'
  }
}

export function EditionsList() {
  const { data: editions, isLoading } = useEditions()

  if (isLoading) {
    return <p className="text-sm text-slate-400">Loading editions...</p>
  }

  if (!editions || editions.length === 0) {
    return <p className="text-sm text-slate-400">No editions ingested yet - upload a PDF above to get started.</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {editions.map((edition) => (
        <li key={edition.edition_id}>
          <Link
            to={`/reader/${edition.edition_id}/1`}
            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm transition-colors hover:border-teal-300 hover:bg-teal-50/40"
          >
            <div>
              <p className="font-medium capitalize text-slate-800">{edition.edition}</p>
              <p className="text-xs text-slate-500">{edition.date}</p>
              {edition.extracted_at && (
                <p className="text-xs text-slate-400">extracted {formatTimestamp(edition.extracted_at)}</p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <div className="text-right text-xs text-slate-500">
                <p>{edition.page_count} pages</p>
                <p>{edition.article_count} articles</p>
              </div>
              {edition.status && (
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(edition.status)}`}>
                  {edition.status}
                </span>
              )}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}

import { Link } from 'react-router-dom'
import { useEditions } from '../lib/queries'

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
            </div>
            <div className="text-right text-xs text-slate-500">
              <p>{edition.page_count} pages</p>
              <p>{edition.article_count} articles</p>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}

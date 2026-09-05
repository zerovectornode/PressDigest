import { useState } from 'react'
import { Link } from 'react-router-dom'
import { formatTimestamp } from '../lib/format'
import { useDeleteEdition, useEditions, useRetryFailedPages } from '../lib/queries'
import type { EditionSummaryOut } from '../types/api'

function statusBadgeClass(status: string | null | undefined): string {
  switch (status) {
    case 'running':
      return 'bg-amber-100 text-amber-700'
    case 'completed_with_errors':
    case 'failed':
      return 'bg-rose-100 text-rose-700'
    default:
      return 'bg-teal-100 text-teal-700'
  }
}

function EditionRow({ edition }: { edition: EditionSummaryOut }) {
  const [confirming, setConfirming] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const deleteEdition = useDeleteEdition()
  const retryFailed = useRetryFailedPages(edition.edition_id)
  const failedPages = edition.failed_pages ?? []

  const handleDelete = () => {
    deleteEdition.mutate(edition.edition_id, {
      onSuccess: (result) => {
        setToast(`Deleted - freed ${(result.bytes_freed / 1e6).toFixed(1)}MB`)
      },
    })
    setConfirming(false)
  }

  return (
    <li className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      {/* flex-wrap lets the counts/status/delete cluster drop to its own
          line at narrow widths instead of squeezing six pieces of
          information into one unbreakable row (see design/DESIGN.md
          mobile layout notes). */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link to={`/reader/${edition.edition_id}/1`} className="min-w-0 transition-colors hover:text-teal-700">
          <p className="font-medium capitalize text-slate-800">{edition.edition}</p>
          <p className="text-xs text-slate-500">{edition.date}</p>
          {edition.extracted_at && (
            <p className="text-xs text-slate-400">extracted {formatTimestamp(edition.extracted_at)}</p>
          )}
        </Link>
        <div className="flex items-center gap-3">
          <div className="text-right text-xs text-slate-500">
            <p>{edition.page_count} pages</p>
            <p>{edition.article_count} articles</p>
          </div>
          {edition.status && (
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(edition.status)}`}>
              {edition.status.replace(/_/g, ' ')}
            </span>
          )}
          <button
            onClick={() => setConfirming(true)}
            title="Delete this edition"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:border-rose-300 hover:text-rose-600"
          >
            Delete
          </button>
        </div>
      </div>

      {failedPages.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <span>
            {failedPages.length} page{failedPages.length === 1 ? '' : 's'} failed: page{' '}
            {failedPages.join(', ')}
          </span>
          <button
            onClick={() => retryFailed.mutate()}
            disabled={retryFailed.isPending}
            className="min-h-11 rounded-md border border-rose-300 bg-white px-2 py-1 font-medium text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {retryFailed.isPending
              ? 'Retrying...'
              : retryFailed.isSuccess
                ? 'Retrying in background...'
                : `Retry ${failedPages.length} failed page${failedPages.length === 1 ? '' : 's'}`}
          </button>
        </div>
      )}

      {toast && <p className="rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">{toast}</p>}

      {confirming && (
        <div className="flex flex-col gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          <p>
            Delete <span className="font-medium capitalize">{edition.edition}</span> ({edition.date})? This removes
            its stored PDF, extracted text, and articles - the Gemini response cache is kept, so re-extracting the
            same PDF later stays free.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={handleDelete}
              disabled={deleteEdition.isPending}
              className="min-h-11 rounded-md bg-rose-600 px-3 py-1 font-medium text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {deleteEdition.isPending ? 'Deleting...' : 'Confirm delete'}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1 font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
          {deleteEdition.isError && <p className="text-rose-700">{(deleteEdition.error as Error).message}</p>}
        </div>
      )}
    </li>
  )
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
        <EditionRow key={edition.edition_id} edition={edition} />
      ))}
    </ul>
  )
}

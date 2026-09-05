import { Link } from 'react-router-dom'
import type { PageErrorOut } from '../types/api'

const STAGE_LABELS: Record<string, string> = {
  phase1_extraction: 'extraction (Phase 1)',
  gemini_call: 'identifying articles (Gemini)',
  validation: 'validating',
  assembly: 'assembling article',
}

export function FailedPageState({
  editionId,
  pageNum,
  error,
  nextAvailablePage,
  onRetry,
  retrying,
}: {
  editionId: string
  pageNum: number
  error: PageErrorOut | null
  nextAvailablePage: number | null
  onRetry: () => void
  retrying: boolean
}) {
  // Same flag the backend's retry ladder itself uses to decide whether to
  // retry a Gemini call (gemini_client.is_retryable) - not a second,
  // independent judgment here. Absent (no error object yet) defaults to
  // offering retry, matching the pre-existing behavior before this field
  // existed.
  const canRetry = error?.retryable ?? true

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <h2 className="text-xl font-semibold text-rose-700">Page {pageNum} failed to extract</h2>
      {error && (
        <div className="max-w-md rounded-lg bg-rose-50 px-4 py-3 text-left text-sm text-rose-800">
          <p className="font-medium">{STAGE_LABELS[error.stage] ?? error.stage}</p>
          <p className="mt-1 text-rose-700">{error.message}</p>
          {error.code !== null && <p className="mt-1 text-xs text-rose-500">code: {error.code}</p>}
          {error.attempt_count > 1 && (
            <p className="mt-1 text-xs text-rose-500">failed after {error.attempt_count} attempts</p>
          )}
          {!canRetry && (
            <p className="mt-2 text-xs font-medium text-rose-600">
              This failure is deterministic - a retry will produce the same result.
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-center gap-2">
        {canRetry && (
          <button
            onClick={onRetry}
            disabled={retrying}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {retrying ? 'Retrying...' : 'Retry this page'}
          </button>
        )}
        {nextAvailablePage !== null && (
          <Link
            to={`/reader/${editionId}/${nextAvailablePage}`}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Go to next available page →
          </Link>
        )}
        <Link
          to="/"
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Go to Home
        </Link>
      </div>
    </div>
  )
}

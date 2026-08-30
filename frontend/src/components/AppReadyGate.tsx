import { useEffect, useState, type ReactNode } from 'react'

/**
 * Gates rendering the real app behind a successful /api/health response,
 * retrying with backoff instead of letting the very first request of the
 * session surface as a blank page or a hard error.
 *
 * Why this exists at all: a Hugging Face Space that has been idle can take
 * 30-90s to cold-start, and during that window the very first fetch the
 * page makes can fail outright (connection refused/reset) rather than
 * coming back as a clean HTTP error - ordinary react-query retry handles
 * failed *responses*, not "the server isn't up yet". This is deliberately
 * separate from queries.ts's per-query retry settings (several of those
 * are `retry: false` on purpose, e.g. a 404 for "no ranking computed yet"
 * is a meaningful state, not a transient failure to retry through).
 */

const MAX_DELAY_MS = 8000
const BASE_DELAY_MS = 500

export function AppReadyGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (ready) return
    let cancelled = false

    async function check() {
      try {
        const res = await fetch('/api/health')
        if (res.ok && !cancelled) {
          setReady(true)
          return
        }
      } catch {
        // Network-level failure (container not accepting connections
        // yet) - fall through to the retry below, same as a non-ok
        // response.
      }
      if (!cancelled) {
        setAttempt((n) => n + 1)
      }
    }

    const delay = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS)
    const timer = setTimeout(check, attempt === 0 ? 0 : delay)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [attempt, ready])

  if (ready) return <>{children}</>

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-white">
      <div className="flex flex-col items-center gap-3 text-sm text-slate-500">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-slate-500" />
        <p>
          {attempt === 0
            ? 'Connecting...'
            : 'Still connecting - the server may be starting up. Retrying...'}
        </p>
      </div>
    </div>
  )
}

import { useEffect } from 'react'

/** Sets document.title to "{title} — PressDigest" for as long as the
 * calling page is mounted, restoring the previous title on unmount (so
 * navigating between routes never leaves a stale title behind if a
 * future route forgets to call this itself). Pass null/undefined to
 * skip updating (e.g. while a title-relevant query is still loading). */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    if (!title) return
    const previous = document.title
    document.title = `${title} — PressDigest`
    return () => {
      document.title = previous
    }
  }, [title])
}

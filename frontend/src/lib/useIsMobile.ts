import { useEffect, useState } from 'react'

// 768px (Tailwind's `md`) - below this, the PageReader's split view has
// nowhere near enough room (MIN_LEFT_WIDTH 360 + MIN_RIGHT_WIDTH 320 +
// divider already needs ~700px), so it's the natural line between
// "desktop layout" and "needs a mobile layout" for this app - not an
// arbitrary phone-width guess. Kept in one place so every component that
// needs a JS-level (not just CSS) mobile decision - e.g. "don't even
// mount the PDF document until the user asks for it" - agrees with the
// same breakpoint Tailwind's `md:` prefix uses everywhere else.
const MOBILE_QUERY = '(max-width: 767px)'

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches)

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY)
    const onChange = () => setIsMobile(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return isMobile
}

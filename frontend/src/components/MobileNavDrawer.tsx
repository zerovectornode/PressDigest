import { useEffect, useRef, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { NAV_ITEMS } from './Sidebar'

/** Mobile equivalent of Sidebar - a hamburger-triggered top bar plus a
 * dismissible drawer overlay, shown only below the md breakpoint (see
 * useIsMobile.ts). Sidebar itself is `hidden md:flex`, so on a phone this
 * is the only way to navigate or get back Home - it must not depend on
 * JS media-query state to decide whether to render (that would mean an
 * extra render pass / flash on load); Tailwind's `md:hidden` on the root
 * handles that entirely in CSS, matching Sidebar's own `hidden md:flex`. */
export function MobileNavDrawer() {
  const [open, setOpen] = useState(false)
  const drawerRef = useRef<HTMLDivElement>(null)
  const openButtonRef = useRef<HTMLButtonElement>(null)

  const close = () => {
    setOpen(false)
    openButtonRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    const drawer = drawerRef.current
    const focusable = drawer?.querySelectorAll<HTMLElement>('a[href], button')
    focusable?.[0]?.focus()
    document.body.style.overflow = 'hidden'

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        // Routed through close(), not a bare setOpen(false), so Escape
        // restores focus to the trigger button same as every other
        // dismiss path (backdrop tap, X button, picking a nav link) -
        // otherwise focus is left stranded on <body>.
        close()
        return
      }
      if (e.key !== 'Tab' || !focusable || focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = ''
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <>
      <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 md:hidden">
        <button
          ref={openButtonRef}
          onClick={() => setOpen(true)}
          aria-label="Open navigation menu"
          aria-expanded={open}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100"
        >
          <HamburgerIcon className="h-6 w-6" />
        </button>
        <Link to="/" className="flex items-center gap-2 rounded-lg py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-500">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-teal-600 text-xs font-bold text-white">
            P
          </div>
          <span className="text-base font-semibold tracking-tight text-slate-900">PressDigest</span>
        </Link>
      </div>

      {open && (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation menu">
          <div className="absolute inset-0 bg-slate-900/40" onClick={close} />
          <div ref={drawerRef} className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-white shadow-xl">
            <div className="flex items-center justify-between px-4 py-4">
              <span className="text-lg font-semibold tracking-tight text-slate-900">PressDigest</span>
              <button
                onClick={close}
                aria-label="Close navigation menu"
                className="flex h-11 w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"
              >
                <CloseIcon className="h-5 w-5" />
              </button>
            </div>
            <nav className="flex flex-1 flex-col gap-1 px-3">
              {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  onClick={close}
                  className={({ isActive }) =>
                    `flex min-h-11 items-center gap-3 rounded-lg px-3 py-2.5 text-base font-medium ${
                      isActive ? 'bg-teal-50 text-teal-700' : 'text-slate-600 hover:bg-slate-100'
                    }`
                  }
                >
                  <Icon className="h-5 w-5 shrink-0" />
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>
        </div>
      )}
    </>
  )
}

function HamburgerIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} {...props}>
      <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
    </svg>
  )
}

function CloseIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} {...props}>
      <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
    </svg>
  )
}

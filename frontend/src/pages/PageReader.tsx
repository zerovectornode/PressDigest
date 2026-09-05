import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ArticleCard } from '../components/ArticleCard'
import { EmptyState } from '../components/EmptyState'
import { FailedPageState } from '../components/FailedPageState'
import { PdfPageCanvas, type PdfHighlight } from '../components/PdfPageCanvas'
import * as api from '../lib/api'
import { editionPdfUrl } from '../lib/api'
import { useEdition, usePage, usePageArticles, useRetryPage } from '../lib/queries'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useIsMobile } from '../lib/useIsMobile'

// Above this many articles on one page, only render the first N up front -
// real pages in this dataset (docs/Newspaper.pdf) never come close (page 1
// has 2), but a dense page shouldn't force the browser to lay out dozens of
// full-body serif blocks at once.
const VIRTUALIZE_THRESHOLD = 15

const DEFAULT_LEFT_WIDTH = 672 // 42rem at the default 16px root
const MIN_LEFT_WIDTH = 360
const MIN_RIGHT_WIDTH = 320

export function PageReader() {
  const { editionId, pageNum: pageNumParam } = useParams<{ editionId: string; pageNum: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()
  const [activeArticleId, setActiveArticleId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [pageInput, setPageInput] = useState('')
  const [leftWidth, setLeftWidth] = useState(DEFAULT_LEFT_WIDTH)
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  // Text is the primary reading surface; Page (the PDF pane) exists for
  // verification against print - defaulting to Text, and not mounting
  // PdfPageCanvas at all until Page is opened at least once, is what
  // keeps someone on mobile data from paying an ~11MB fetch to read text
  // (see design/DESIGN.md's item-3 measurements: ~33s on throttled
  // mobile). pdfEverOpened stays true for the rest of this edition visit
  // once set, so switching Text -> Page -> Text -> Page again doesn't
  // re-fetch - only entering a DIFFERENT edition resets it.
  const [mobileTab, setMobileTab] = useState<'text' | 'page'>('text')
  const [pdfEverOpened, setPdfEverOpened] = useState(false)

  const pageNum = pageNumParam ? Number(pageNumParam) : undefined

  const editionQuery = useEdition(editionId)
  const pageQuery = usePage(editionId, pageNum)
  const articlesQuery = usePageArticles(editionId, pageNum)
  const retryPage = useRetryPage(editionId)
  const redirectedForEditionRef = useRef<string | undefined>(undefined)

  const edition = editionQuery.data
  useDocumentTitle(
    edition && pageNum !== undefined
      ? `Page ${pageNum} — ${edition.edition.charAt(0).toUpperCase()}${edition.edition.slice(1)} ${edition.date}`
      : null,
  )

  useEffect(() => {
    setMobileTab('text')
    setPdfEverOpened(false)
  }, [editionId])

  // Prefetch the next page's articles + let the browser cache the next
  // page's PDF render on idle, so turning the page doesn't re-block on a
  // fresh round trip - this is purely additive: if the browser never goes
  // idle before the user navigates anyway, nothing is lost.
  useEffect(() => {
    if (!editionId || pageNum === undefined) return
    const idle = (cb: () => void) =>
      'requestIdleCallback' in window ? window.requestIdleCallback(cb) : setTimeout(cb, 300)
    const handle = idle(() => {
      queryClient.prefetchQuery({
        queryKey: ['page-articles', editionId, pageNum + 1],
        queryFn: () => api.getPageArticles(editionId, pageNum + 1),
      })
      queryClient.prefetchQuery({
        queryKey: ['page', editionId, pageNum + 1],
        queryFn: () => api.getPage(editionId, pageNum + 1),
      })
    })
    return () => {
      if ('requestIdleCallback' in window && 'cancelIdleCallback' in window) {
        window.cancelIdleCallback(handle as number)
      } else {
        clearTimeout(handle as ReturnType<typeof setTimeout>)
      }
    }
  }, [editionId, pageNum, queryClient])

  useEffect(() => {
    setActiveArticleId(null)
    setExpanded(false)
    setPageInput(pageNum !== undefined ? String(pageNum) : '')
  }, [editionId, pageNum])

  // Split-divider drag: only ever changes leftWidth (a fixed pixel value),
  // which is what makes the PDF pane's footprint independent of zoom - see
  // design/DESIGN.md "PDF pane sizing". Listeners are attached to the
  // window only while dragging, and removed immediately on mouseup. Desktop
  // only - mobile has no divider (see the tabs layout below).
  useEffect(() => {
    function onMouseMove(e: MouseEvent) {
      const drag = dragStateRef.current
      const shellWidth = shellRef.current?.clientWidth
      if (!drag || !shellWidth) return
      const delta = e.clientX - drag.startX
      const maxLeft = shellWidth - MIN_RIGHT_WIDTH
      setLeftWidth(Math.min(maxLeft, Math.max(MIN_LEFT_WIDTH, drag.startWidth + delta)))
    }
    function onMouseUp() {
      dragStateRef.current = null
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [])

  // Redirects once per edition, on landing only: if the page the user
  // arrived on (e.g. "Start reading page 1" when page 1 happens to have
  // failed) isn't done yet, jump straight to the first page that is -
  // this is what turns a dead end into a working reader. Marks the
  // edition as "handled" either way so normal Prev/Next/retry navigation
  // afterward is never fought by this effect.
  useEffect(() => {
    if (!editionId || pageNum === undefined) return
    if (redirectedForEditionRef.current === editionId) return
    const pages = editionQuery.data?.pages
    if (!pages) return
    redirectedForEditionRef.current = editionId
    const currentStatus = pages.find((p) => p.page_num === pageNum)?.status
    if (currentStatus === 'done') return
    const firstAvailable = pages.find((p) => p.status === 'done')?.page_num
    if (firstAvailable !== undefined && firstAvailable !== pageNum) {
      navigate(`/reader/${editionId}/${firstAvailable}`, { replace: true })
    }
  }, [editionId, pageNum, editionQuery.data, navigate])

  // On mobile, once the Page tab is showing an active article, scroll its
  // highlight into view - "tapping an article in Text should be able to
  // switch to Page with that article's highlight rects in view." Best-
  // effort: if the PDF is still on its first load, the element may not
  // exist yet at 350ms and this just silently does nothing rather than
  // retrying indefinitely - the loading overlay already communicates that
  // state, and the user can scroll manually once it appears.
  useEffect(() => {
    if (!isMobile || mobileTab !== 'page' || !activeArticleId) return
    const t = setTimeout(() => {
      document
        .querySelector(`[data-highlight-id="${activeArticleId}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 350)
    return () => clearTimeout(t)
  }, [isMobile, mobileTab, activeArticleId])

  if (!editionId || pageNum === undefined) {
    return (
      <EmptyState
        title="No page selected"
        description="Pick an edition from the Home screen to start reading."
        linkTo="/"
        linkLabel="Go to Home"
      />
    )
  }

  // pages covers every page number the edition will ever have (including
  // ones not extracted yet); page_count only counts pages with gold
  // output so far - navigation bounds must use the former, or a
  // still-processing page near the end of the edition couldn't be reached.
  const pageStatuses = editionQuery.data?.pages
  const totalPages = pageStatuses?.length ?? editionQuery.data?.page_count
  const pageStatusFor = (n: number) => pageStatuses?.find((p) => p.page_num === n)?.status

  const goToPage = (n: number) => {
    if (totalPages !== undefined && (n < 1 || n > totalPages)) return
    navigate(`/reader/${editionId}/${n}`)
  }

  const submitPageInput = () => {
    const parsed = Number.parseInt(pageInput, 10)
    if (Number.isFinite(parsed)) {
      goToPage(parsed)
    } else {
      setPageInput(String(pageNum))
    }
  }

  if (pageQuery.isLoading || articlesQuery.isLoading) {
    return <p className="p-6 text-sm text-slate-400">Loading page {pageNum}...</p>
  }

  if (pageQuery.isError || !pageQuery.data) {
    return (
      <EmptyState
        title={`Page ${pageNum} doesn't exist`}
        description="This page number is out of range for this edition."
        linkTo="/"
        linkLabel="Go to Home"
      />
    )
  }

  if (pageQuery.data.status === 'failed') {
    const forwardAvailable = pageStatuses?.find((p) => p.page_num > pageNum && p.status === 'done')?.page_num
    const anyAvailable = pageStatuses?.find((p) => p.status === 'done')?.page_num
    return (
      <FailedPageState
        editionId={editionId}
        pageNum={pageNum}
        error={pageQuery.data.error ?? null}
        nextAvailablePage={forwardAvailable ?? anyAvailable ?? null}
        onRetry={() => retryPage.mutate(pageNum)}
        retrying={retryPage.isPending}
      />
    )
  }

  if (pageQuery.data.status !== 'done') {
    return (
      <EmptyState
        title={`Page ${pageNum} is still being extracted`}
        description="This page hasn't finished extraction yet - this'll update automatically once it's done."
        linkTo="/"
        linkLabel="Go to Home"
      />
    )
  }

  const articles = articlesQuery.data?.articles ?? []
  const page = pageQuery.data
  const visibleArticles =
    articles.length > VIRTUALIZE_THRESHOLD && !expanded ? articles.slice(0, VIRTUALIZE_THRESHOLD) : articles

  // One highlight per rect, sharing the article's id across all of its
  // rects - a multi-column body gets one translucent box per fragment, not
  // one L-shaped union box (see design/DESIGN.md "Multi-rect bodies").
  const highlights: PdfHighlight[] = articles.flatMap((a) =>
    a.rects.map((rect) => ({
      id: a.article_id,
      bbox: rect as [number, number, number, number],
      isActive: a.article_id === activeArticleId,
    })),
  )

  const handleHighlightClick = (id: string) => {
    setActiveArticleId(id)
    document.getElementById(`article-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // Shared between desktop's split view and mobile's Text tab - clicking
  // an article also switches to Page (with its highlight scrolled into
  // view, see the effect above) ONLY on mobile; on desktop both panes are
  // already visible, so this is unchanged from before (just sets which
  // article is highlighted).
  const handleArticleSelect = (id: string) => {
    setActiveArticleId(id)
    if (isMobile) {
      setPdfEverOpened(true)
      setMobileTab('page')
    }
  }

  const articlesContent =
    articles.length === 0 ? (
      <EmptyState
        title="No articles on this page"
        description="This page had no extractable article text (e.g. a full-page advertisement)."
      />
    ) : (
      <>
        {visibleArticles.map((article) => (
          <ArticleCard
            key={article.article_id}
            article={article}
            isActive={article.article_id === activeArticleId}
            onHover={setActiveArticleId}
            onClick={handleArticleSelect}
          />
        ))}
        {articles.length > VIRTUALIZE_THRESHOLD && !expanded && (
          <button
            onClick={() => setExpanded(true)}
            className="min-h-11 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            Show all {articles.length} articles
          </button>
        )}
      </>
    )

  const pdfPane = (
    <PdfPageCanvas
      pdfUrl={editionPdfUrl(editionId)}
      pageNum={pageNum}
      highlights={highlights}
      onHighlightClick={handleHighlightClick}
      onHighlightHover={setActiveArticleId}
    />
  )

  return (
    <div ref={shellRef} className="flex h-full min-w-0 flex-col md:flex-row">
      {/* Mobile: Text/Page tabs, no split - a split screen can't work at
          phone widths, and stacking both panes vertically makes reading
          worse than either pane full-width. Gated on the JS isMobile
          boolean, not just CSS - otherwise this whole block (including
          the PDF pane below, once opened) would still MOUNT on a real
          narrow browser, defeating the point of lazy-loading the PDF: CSS
          `display:none` hides an element but doesn't stop React from
          mounting it and running its effects. */}
      {isMobile && (
        <>
          <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2">
            <button
              onClick={() => setMobileTab('text')}
              className={`min-h-11 flex-1 rounded-lg text-sm font-medium ${
                mobileTab === 'text' ? 'bg-teal-50 text-teal-700' : 'text-slate-600'
              }`}
            >
              Text
            </button>
            <button
              onClick={() => {
                setPdfEverOpened(true)
                setMobileTab('page')
              }}
              className={`min-h-11 flex-1 rounded-lg text-sm font-medium ${
                mobileTab === 'page' ? 'bg-teal-50 text-teal-700' : 'text-slate-600'
              }`}
            >
              Page
            </button>
          </div>

          <div className={`flex-1 flex-col overflow-y-auto ${mobileTab === 'text' ? 'flex' : 'hidden'}`}>
            <MobileReaderHeader
              edition={editionQuery.data}
              page={page}
              pageNum={pageNum}
              totalPages={totalPages}
              pageInput={pageInput}
              setPageInput={setPageInput}
              submitPageInput={submitPageInput}
              goToPage={goToPage}
              pageStatusFor={pageStatusFor}
            />
            <div className="flex flex-1 flex-col gap-4 px-4 py-4">{articlesContent}</div>
          </div>
          {/* Mounted once Page is first opened, then kept mounted (just
              hidden) on later tab switches, so PdfPageCanvas's own document
              cache (see that component) isn't thrown away and re-fetched
              every time the user toggles tabs. */}
          {pdfEverOpened && (
            <div className={`flex-1 flex-col overflow-hidden ${mobileTab === 'page' ? 'flex' : 'hidden'}`}>
              {pdfPane}
            </div>
          )}
        </>
      )}

      {/* Desktop: unchanged split view - gated on !isMobile for the same
          reason as above (so the PDF pane never mounts on mobile at all). */}
      {!isMobile && (
        <>
      <div
        style={{ width: leftWidth }}
        className="flex shrink-0 flex-col overflow-y-auto border-r border-slate-200 bg-slate-50/50"
      >
        <div className="flex flex-col gap-1.5 border-b border-slate-200 bg-white px-5 py-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-800">
              {editionQuery.data ? `${editionQuery.data.edition} · ${editionQuery.data.date}` : ' '}
            </span>
            {page.coverage_ratio !== null && (
              <span className="text-xs text-slate-400">coverage {Math.round(page.coverage_ratio * 100)}%</span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => goToPage(pageNum - 1)}
              disabled={pageNum <= 1}
              title={pageStatusFor(pageNum - 1) && pageStatusFor(pageNum - 1) !== 'done' ? 'not ready yet' : undefined}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            >
              ← Prev
            </button>
            <input
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onBlur={submitPageInput}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
              inputMode="numeric"
              aria-label="Jump to page"
              className="w-12 rounded-md border border-slate-300 px-1 py-1 text-center text-xs text-slate-700"
            />
            <span className="text-xs text-slate-500">/ {totalPages ?? '?'}</span>
            <button
              onClick={() => goToPage(pageNum + 1)}
              disabled={totalPages !== undefined && pageNum >= totalPages}
              title={pageStatusFor(pageNum + 1) && pageStatusFor(pageNum + 1) !== 'done' ? 'not ready yet' : undefined}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            >
              Next → {pageStatusFor(pageNum + 1) && pageStatusFor(pageNum + 1) !== 'done' && '(extracting)'}
            </button>
            <span className="ml-1 text-sm font-medium text-slate-700">
              Page {pageNum} — {page.article_count} article{page.article_count === 1 ? '' : 's'}
            </span>
          </div>
          {page.validation_ok === false && <span className="text-xs text-rose-500">validation failed on this page</span>}
        </div>

        <div className="flex flex-1 flex-col gap-4 p-5">{articlesContent}</div>
      </div>

      {/* Drag handle: only ever adjusts leftWidth (a fixed pixel value) -
          this is the ONLY thing that should change the PDF pane's
          footprint, never zoom (see design/DESIGN.md "PDF pane sizing"). */}
      <div
        onMouseDown={(e) => {
          dragStateRef.current = { startX: e.clientX, startWidth: leftWidth }
        }}
        className="w-1.5 shrink-0 cursor-col-resize bg-slate-200 transition-colors hover:bg-teal-300 active:bg-teal-400"
      />

      <div className="min-w-0 flex-1">{pdfPane}</div>
        </>
      )}
    </div>
  )
}

/** Mobile-only header for the Text tab: same information as the desktop
 * toolbar (edition/date, Prev/Next, page jump, article count, validation
 * flag), but with ≥44px touch targets instead of the desktop toolbar's
 * mouse-sized ones - kept as a fully separate block rather than a
 * responsive resize of the desktop one, so desktop's markup/sizing is
 * completely untouched. */
function MobileReaderHeader({
  edition,
  page,
  pageNum,
  totalPages,
  pageInput,
  setPageInput,
  submitPageInput,
  goToPage,
  pageStatusFor,
}: {
  edition: { edition: string; date: string } | undefined
  page: { coverage_ratio: number | null; article_count: number; validation_ok: boolean }
  pageNum: number
  totalPages: number | undefined
  pageInput: string
  setPageInput: (v: string) => void
  submitPageInput: () => void
  goToPage: (n: number) => void
  pageStatusFor: (n: number) => string | undefined
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-slate-200 bg-white px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-800">
          {edition ? `${edition.edition} · ${edition.date}` : ' '}
        </span>
        {page.coverage_ratio !== null && (
          <span className="text-xs text-slate-400">coverage {Math.round(page.coverage_ratio * 100)}%</span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={() => goToPage(pageNum - 1)}
          disabled={pageNum <= 1}
          title={pageStatusFor(pageNum - 1) && pageStatusFor(pageNum - 1) !== 'done' ? 'not ready yet' : undefined}
          className="min-h-11 min-w-11 rounded-md border border-slate-300 px-3 text-sm text-slate-600 disabled:opacity-40"
        >
          ←
        </button>
        <div className="flex items-center gap-1.5">
          <input
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={submitPageInput}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
            inputMode="numeric"
            aria-label="Jump to page"
            className="h-11 w-14 rounded-md border border-slate-300 text-center text-sm text-slate-700"
          />
          <span className="text-sm text-slate-500">/ {totalPages ?? '?'}</span>
        </div>
        <button
          onClick={() => goToPage(pageNum + 1)}
          disabled={totalPages !== undefined && pageNum >= totalPages}
          title={pageStatusFor(pageNum + 1) && pageStatusFor(pageNum + 1) !== 'done' ? 'not ready yet' : undefined}
          className="min-h-11 min-w-11 rounded-md border border-slate-300 px-3 text-sm text-slate-600 disabled:opacity-40"
        >
          →
        </button>
      </div>
      <span className="text-sm font-medium text-slate-700">
        Page {pageNum} — {page.article_count} article{page.article_count === 1 ? '' : 's'}
      </span>
      {page.validation_ok === false && <span className="text-xs text-rose-500">validation failed on this page</span>}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { bboxToViewportRect, type Bbox } from '../lib/coords'
import { pdfjs } from '../lib/pdfjs'

export interface PdfHighlight {
  id: string
  bbox: Bbox
  isActive: boolean
}

// An article's body can span multiple columns, so callers pass one
// PdfHighlight per rect with the SAME id repeated across an article's rects
// (a five-column story gets five highlight entries) rather than one union
// box - see design/DESIGN.md "Multi-rect bodies". React keys must still be
// unique, so the render below keys on index alongside id.

type Zoom = { mode: 'fit-width' } | { mode: 'fit-page' } | { mode: 'custom'; percent: number }

const MIN_PERCENT = 25
const MAX_PERCENT = 400

export function PdfPageCanvas({
  pdfUrl,
  pageNum,
  highlights,
  onHighlightClick,
  onHighlightHover,
}: {
  pdfUrl: string
  pageNum: number
  highlights: PdfHighlight[]
  onHighlightClick?: (id: string) => void
  onHighlightHover?: (id: string | null) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // Caches the loaded PDF.js document by URL, across renders - without
  // this, every page turn, zoom change, or split-divider drag re-ran
  // pdfjs.getDocument({ url: pdfUrl }), re-fetching and re-parsing the
  // WHOLE PDF from scratch on every single navigation, not just once per
  // edition visit. See design/DESIGN.md for the investigation that found
  // this (it was mistaken for a server-side rendering bug at first - the
  // server has no page-render step at all).
  const docCacheRef = useRef<{ url: string; task: pdfjs.PDFDocumentLoadingTask; promise: Promise<pdfjs.PDFDocumentProxy> } | null>(
    null,
  )
  const [pageHeightPt, setPageHeightPt] = useState<number | null>(null)
  const [viewport, setViewport] = useState<pdfjs.PageViewport | null>(null)
  const [zoom, setZoom] = useState<Zoom>({ mode: 'fit-width' })
  const [effectivePercent, setEffectivePercent] = useState(100)
  const [percentInput, setPercentInput] = useState('100')
  const [error, setError] = useState<string | null>(null)
  const [loadingDoc, setLoadingDoc] = useState(false)
  // Bumped whenever the pane's own footprint changes (e.g. dragging the
  // split divider in PageReader) so the render effect re-measures and
  // re-fits - this is the ONLY thing that should resize the pane; zoom
  // itself must never change the pane's footprint (see design/DESIGN.md
  // "PDF pane sizing").
  const [containerTick, setContainerTick] = useState(0)

  // Returns the cached document promise for this exact URL, only calling
  // pdfjs.getDocument() when the URL actually changed. The outgoing
  // document (if any) is destroyed once it's done loading, to release its
  // worker-side buffers rather than leaking one per edition visited in a
  // session.
  function loadDocument(url: string): Promise<pdfjs.PDFDocumentProxy> {
    const cached = docCacheRef.current
    if (cached && cached.url === url) return cached.promise
    // destroy() lives on the loading task, not the resolved document proxy.
    if (cached) cached.task.destroy()
    const task = pdfjs.getDocument({ url })
    docCacheRef.current = { url, task, promise: task.promise }
    return task.promise
  }

  useEffect(() => {
    return () => {
      docCacheRef.current?.task.destroy()
    }
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver(() => setContainerTick((t) => t + 1))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Page loads at fit-width by default (see design/DESIGN.md "PDF pane
  // sizing") - reset back to it whenever a new page is opened, rather than
  // carrying over the previous page's custom zoom.
  useEffect(() => {
    setZoom({ mode: 'fit-width' })
  }, [pdfUrl, pageNum])

  useEffect(() => {
    let cancelled = false
    let renderTask: ReturnType<pdfjs.PDFPageProxy['render']> | null = null

    async function render() {
      if (!containerRef.current || !canvasRef.current) return
      // Only a genuinely new URL re-fetches/re-parses - see loadDocument.
      // Page turns, zoom changes, and pane resizes within the same
      // edition all hit the cache below instead.
      const isNewDocument = docCacheRef.current?.url !== pdfUrl
      if (isNewDocument) setLoadingDoc(true)
      try {
        const doc = await loadDocument(pdfUrl)
        const page = await doc.getPage(pageNum)
        if (cancelled) return
        if (isNewDocument) setLoadingDoc(false)

        // Measured from the pane's own box, which is now sized by the
        // surrounding flex layout / split divider - never by this canvas's
        // own rendered size (that circular dependency was the root cause
        // of the page-level horizontal scrollbar).
        const containerWidth = containerRef.current.clientWidth
        const containerHeight = containerRef.current.clientHeight
        const fitWidthScale = containerWidth / page.view[2]
        const fitPageScale = Math.min(fitWidthScale, containerHeight / page.view[3])

        const scale =
          zoom.mode === 'fit-width'
            ? fitWidthScale
            : zoom.mode === 'fit-page'
              ? fitPageScale
              : fitWidthScale * (zoom.percent / 100)
        const vp = page.getViewport({ scale })

        const canvas = canvasRef.current
        canvas.width = vp.width
        canvas.height = vp.height
        const context = canvas.getContext('2d')
        if (!context) return

        renderTask = page.render({ canvasContext: context, viewport: vp, canvas })
        await renderTask.promise
        if (cancelled) return

        setPageHeightPt(page.view[3])
        setViewport(vp)
        const pct = Math.round((scale / fitWidthScale) * 100)
        setEffectivePercent(pct)
        setPercentInput(String(pct))
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setLoadingDoc(false)
        }
      }
    }

    render()
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfUrl, pageNum, zoom, containerTick])

  const applyPercent = () => {
    const parsed = Number.parseInt(percentInput, 10)
    if (Number.isFinite(parsed)) {
      setZoom({ mode: 'custom', percent: Math.min(MAX_PERCENT, Math.max(MIN_PERCENT, parsed)) })
    } else {
      setPercentInput(String(effectivePercent))
    }
  }

  const step = (delta: number) => {
    setZoom({ mode: 'custom', percent: Math.min(MAX_PERCENT, Math.max(MIN_PERCENT, effectivePercent + delta)) })
  }

  if (error) {
    return <p className="p-6 text-sm text-rose-600">Failed to render page: {error}</p>
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex items-center justify-end gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <button
          onClick={() => setZoom({ mode: 'fit-width' })}
          className={`rounded-md border px-2 py-1 text-xs ${
            zoom.mode === 'fit-width' ? 'border-teal-400 bg-teal-50 text-teal-700' : 'border-slate-300 text-slate-600 hover:bg-slate-50'
          }`}
        >
          Fit width
        </button>
        <button
          onClick={() => setZoom({ mode: 'fit-page' })}
          className={`rounded-md border px-2 py-1 text-xs ${
            zoom.mode === 'fit-page' ? 'border-teal-400 bg-teal-50 text-teal-700' : 'border-slate-300 text-slate-600 hover:bg-slate-50'
          }`}
        >
          Fit page
        </button>
        <div className="mx-1 h-4 w-px bg-slate-200" />
        <button
          onClick={() => step(-10)}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
        >
          −
        </button>
        <input
          value={percentInput}
          onChange={(e) => setPercentInput(e.target.value)}
          onBlur={applyPercent}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.currentTarget.blur()
            }
          }}
          inputMode="numeric"
          className="w-12 rounded-md border border-slate-300 px-1 py-1 text-center text-xs text-slate-700"
        />
        <span className="text-xs text-slate-500">%</span>
        <button
          onClick={() => step(10)}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
        >
          +
        </button>
      </div>
      {/* This pane's width is fixed by the parent layout (see
          PageReader.tsx's split divider) and never grows with zoom -
          overflow-auto scrolls the zoomed canvas WITHIN this fixed
          footprint instead of pushing the rest of the page around. */}
      <div ref={containerRef} className="relative min-w-0 flex-1 overflow-auto bg-slate-100 p-4">
        {loadingDoc && (
          // Only shown while the PDF document itself is loading (first
          // visit to this edition, or switching editions) - a plain
          // page-turn/zoom/resize never shows this, since loadDocument
          // reuses the already-parsed document for those.
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-100/80">
            <p className="text-sm text-slate-500">Loading page…</p>
          </div>
        )}
        <div className="relative mx-auto w-fit shadow-md">
          <canvas ref={canvasRef} />
          {viewport && pageHeightPt !== null && (
            <div className="pointer-events-none absolute inset-0">
              {highlights.map((h, i) => {
                const rect = bboxToViewportRect(h.bbox, pageHeightPt, viewport)
                return (
                  <div
                    key={`${h.id}-${i}`}
                    onClick={() => onHighlightClick?.(h.id)}
                    onMouseEnter={() => onHighlightHover?.(h.id)}
                    onMouseLeave={() => onHighlightHover?.(null)}
                    className={`pointer-events-auto absolute cursor-pointer rounded-sm transition-colors ${
                      h.isActive ? 'bg-teal-400/25 ring-2 ring-teal-500' : 'bg-teal-300/0 hover:bg-teal-300/20'
                    }`}
                    style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
                  />
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

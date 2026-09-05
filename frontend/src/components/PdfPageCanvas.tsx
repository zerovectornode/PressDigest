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

// iOS Safari's canvas limit (~4096px/side, ~16.7M px total) is the
// strictest one in common use - clamped to unconditionally rather than
// detected per-platform, so a large native page at a high zoom percent
// degrades resolution instead of throwing "Invalid canvas size" on the
// platforms that enforce it.
const MAX_CANVAS_DIMENSION_PX = 4096
const MAX_CANVAS_AREA_PX = 16 * 1024 * 1024

function clampScaleToCanvasLimits(scale: number, nativeWidth: number, nativeHeight: number): number {
  const width = nativeWidth * scale
  const height = nativeHeight * scale
  if (width <= 0 || height <= 0) return scale
  const dimensionFactor = Math.min(1, MAX_CANVAS_DIMENSION_PX / width, MAX_CANVAS_DIMENSION_PX / height)
  const areaFactor = Math.min(1, Math.sqrt(MAX_CANVAS_AREA_PX / (width * height)))
  return scale * Math.min(dimensionFactor, areaFactor)
}

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
  // Set once per URL, by the document-load effect below, once the
  // PDFDocumentProxy itself has resolved - the render effect can't do
  // anything until this exists for the CURRENT pdfUrl.
  const [loadedDoc, setLoadedDoc] = useState<{ url: string; doc: pdfjs.PDFDocumentProxy } | null>(null)
  // Tracks which URL is still waiting for its first successful (or
  // failed) page render, independent of any individual render effect
  // invocation - a ref, not state, specifically so a render attempt that
  // gets cancelled/superseded (e.g. by a resize while the pane is hidden)
  // can never leave this permanently stuck; whichever invocation actually
  // settles first clears it. See design/DESIGN.md for the stuck-spinner
  // race this replaces.
  const firstRenderPendingRef = useRef<string | null>(null)
  const [pageHeightPt, setPageHeightPt] = useState<number | null>(null)
  const [viewport, setViewport] = useState<pdfjs.PageViewport | null>(null)
  const [zoom, setZoom] = useState<Zoom>({ mode: 'fit-width' })
  const [effectivePercent, setEffectivePercent] = useState(100)
  const [percentInput, setPercentInput] = useState('100')
  const [error, setError] = useState<string | null>(null)
  const [loadingDoc, setLoadingDoc] = useState(false)
  // Percent of the PDF downloaded so far, while a new document is loading -
  // null once loaded/unknown. Backed by real numbers: the measured
  // worst case (throttled mobile, an 11MB edition) was ~33s to first
  // paint, long enough that a static "Loading page…" with no progress
  // reads as broken - see design/DESIGN.md.
  const [loadPercent, setLoadPercent] = useState<number | null>(null)
  // Bumped whenever the pane's own footprint changes (e.g. dragging the
  // split divider in PageReader) so the render effect re-measures and
  // re-fits - this is the ONLY thing that should resize the pane; zoom
  // itself must never change the pane's footprint (see design/DESIGN.md
  // "PDF pane sizing").
  const [containerTick, setContainerTick] = useState(0)
  // Bumped by the error state's Retry button - included in both effects'
  // deps below so retry works whether the failure was in loading the
  // document or in rendering a page of an already-loaded one.
  const [retryKey, setRetryKey] = useState(0)

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
    task.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
      if (total > 0) setLoadPercent(Math.min(100, Math.round((loaded / total) * 100)))
    }
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

  // Document load - deliberately its OWN effect, keyed only on pdfUrl, not
  // on pageNum/zoom/containerTick. Previously this lived inside the same
  // effect as the per-page render, so a resize firing mid-fetch (and
  // ResizeObserver is guaranteed to fire at least once immediately after
  // observe(), whether or not the size actually changed) would cancel that
  // invocation via its own `cancelled` flag *before* it reached the code
  // that clears the loading spinner - leaving "Loading page… 100%" stuck
  // forever even though a later, uncancelled invocation went on to render
  // successfully. Keying this effect on pdfUrl alone means only switching
  // editions can cancel it, never a resize/zoom/page-turn.
  useEffect(() => {
    let cancelled = false
    setLoadingDoc(true)
    setLoadPercent(null)
    setError(null)
    firstRenderPendingRef.current = pdfUrl
    loadDocument(pdfUrl)
      .then((doc) => {
        if (cancelled) return
        setLoadedDoc({ url: pdfUrl, doc })
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
        setLoadingDoc(false)
        setLoadPercent(null)
      })
    return () => {
      cancelled = true
    }
  }, [pdfUrl, retryKey])

  // Per-page render - runs once the document above has resolved, and again
  // on page turn/zoom/pane-resize, all without re-fetching (loadedDoc stays
  // the same object; only getPage/render run again).
  useEffect(() => {
    if (!loadedDoc || loadedDoc.url !== pdfUrl) return
    if (!containerRef.current || !canvasRef.current) return
    let cancelled = false
    let renderTask: ReturnType<pdfjs.PDFPageProxy['render']> | null = null

    async function render() {
      try {
        const page = await loadedDoc!.doc.getPage(pageNum)
        if (cancelled) return

        // Measured from the pane's own box, which is now sized by the
        // surrounding flex layout / split divider - never by this canvas's
        // own rendered size (that circular dependency was the root cause
        // of the page-level horizontal scrollbar).
        const containerWidth = containerRef.current!.clientWidth
        const containerHeight = containerRef.current!.clientHeight
        // The pane can be mounted but CSS-hidden (display:none) - e.g. the
        // mobile Page tab, kept mounted while the Text tab is showing so
        // PdfPageCanvas's own document cache isn't thrown away (see
        // PageReader.tsx). A hidden element measures 0x0, and that's a
        // real resize (0 is a genuine size change), so it reaches this
        // effect too. Rendering against a 0-width container would ask
        // PDF.js for a zero-size canvas, which throws "Invalid canvas
        // size" - bail out instead and let the NEXT resize (the pane
        // becoming visible again) retry with a real measurement.
        if (
          !Number.isFinite(containerWidth) ||
          !Number.isFinite(containerHeight) ||
          containerWidth <= 0 ||
          containerHeight <= 0
        ) {
          return
        }

        const fitWidthScale = containerWidth / page.view[2]
        const fitPageScale = Math.min(fitWidthScale, containerHeight / page.view[3])

        const scale =
          zoom.mode === 'fit-width'
            ? fitWidthScale
            : zoom.mode === 'fit-page'
              ? fitPageScale
              : fitWidthScale * (zoom.percent / 100)

        if (!Number.isFinite(scale) || scale <= 0) return

        const clampedScale = clampScaleToCanvasLimits(scale, page.view[2], page.view[3])
        const vp = page.getViewport({ scale: clampedScale })
        if (!Number.isFinite(vp.width) || !Number.isFinite(vp.height) || vp.width <= 0 || vp.height <= 0) {
          return
        }

        const canvas = canvasRef.current!
        canvas.width = vp.width
        canvas.height = vp.height
        const context = canvas.getContext('2d')
        if (!context) return

        renderTask = page.render({ canvasContext: context, viewport: vp, canvas })
        await renderTask.promise
        if (cancelled) return

        setPageHeightPt(page.view[3])
        setViewport(vp)
        const pct = Math.round((clampedScale / fitWidthScale) * 100)
        setEffectivePercent(pct)
        setPercentInput(String(pct))
        setError(null)
        if (firstRenderPendingRef.current === pdfUrl) {
          firstRenderPendingRef.current = null
          setLoadingDoc(false)
          setLoadPercent(null)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          if (firstRenderPendingRef.current === pdfUrl) {
            firstRenderPendingRef.current = null
            setLoadingDoc(false)
            setLoadPercent(null)
          }
        }
      }
    }

    render()
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedDoc, pdfUrl, pageNum, zoom, containerTick, retryKey])

  // If the FAILURE was in loading the document itself (not yet in
  // loadedDoc for this URL), its cache entry holds a rejected promise -
  // loadDocument() would just hand that same rejection straight back, so
  // retry needs to evict it first or "Retry" would silently do nothing.
  // If the document already loaded fine and only a page's render failed,
  // leave the cache alone - re-fetching the whole PDF over a render-only
  // error would undo the whole point of caching it.
  const handleRetry = () => {
    const docAlreadyLoaded = loadedDoc?.url === pdfUrl
    if (!docAlreadyLoaded && docCacheRef.current?.url === pdfUrl) {
      docCacheRef.current.task.destroy()
      docCacheRef.current = null
    }
    setRetryKey((k) => k + 1)
  }

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
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-rose-600">Failed to render page: {error}</p>
        <button
          onClick={handleRetry}
          className="min-h-11 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      {/* Numeric zoom + fit presets are a desktop-mouse control surface -
          on mobile, fit-width is the fixed default (zoom state never
          changes away from it here, since nothing below renders to
          change it) and the browser's own native pinch-to-zoom - not
          disabled by anything in this pane's CSS or the app's viewport
          meta tag - is the zoom mechanism instead. */}
      <div className="hidden items-center justify-end gap-2 border-b border-slate-200 bg-white px-4 py-2 md:flex">
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
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-slate-100/80">
            <p className="text-sm text-slate-500">Loading page{loadPercent !== null ? `… ${loadPercent}%` : '…'}</p>
            {loadPercent !== null && (
              <div className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full bg-teal-500 transition-[width]" style={{ width: `${loadPercent}%` }} />
              </div>
            )}
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
                    data-highlight-id={h.id}
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

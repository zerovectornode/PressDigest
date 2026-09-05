import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The bug this guards against: pdfjs.getDocument({ url }) used to run
// fresh on every page turn / zoom change / pane resize, re-fetching and
// re-parsing the whole PDF each time instead of once per edition visit -
// see design/DESIGN.md. pdfjs-dist itself is an ESM namespace vitest can't
// spy on directly, so the mock targets our own thin wrapper (lib/pdfjs.ts)
// instead.

function fakePage(view: [number, number, number, number] = [0, 0, 600, 800]) {
  const renderMock = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }))
  return {
    view,
    getViewport: ({ scale }: { scale: number }) => ({ width: view[2] * scale, height: view[3] * scale }),
    render: renderMock,
  }
}

function fakeLoadingTask(page = fakePage(), docPromise?: Promise<unknown>) {
  const destroy = vi.fn()
  const doc = { getPage: vi.fn().mockResolvedValue(page) }
  return { promise: docPromise ?? Promise.resolve(doc), destroy, doc, page }
}

// jsdom reports 0 for clientWidth/clientHeight on every element (it does
// no real layout) - this mock makes the PdfPageCanvas container's size
// controllable per test, and mirrors the real "container measures 0x0
// while CSS-hidden" case that the fix has to survive.
let mockClientWidth = 0
let mockClientHeight = 0
Object.defineProperty(HTMLDivElement.prototype, 'clientWidth', {
  configurable: true,
  get() {
    return mockClientWidth
  },
})
Object.defineProperty(HTMLDivElement.prototype, 'clientHeight', {
  configurable: true,
  get() {
    return mockClientHeight
  },
})

// Captures the callback passed to `new ResizeObserver(cb)` so tests can
// fire a resize manually - real ResizeObserver guarantees at least one
// callback right after observe(), which is exactly the timing that caused
// the stuck-spinner race this file also tests for.
let resizeCallbacks: Array<() => void> = []
class StubResizeObserver {
  #cb: () => void
  constructor(cb: () => void) {
    this.#cb = cb
    resizeCallbacks.push(cb)
  }
  observe() {}
  unobserve() {}
  disconnect() {
    resizeCallbacks = resizeCallbacks.filter((cb) => cb !== this.#cb)
  }
}
vi.stubGlobal('ResizeObserver', StubResizeObserver)
function fireResize() {
  for (const cb of resizeCallbacks) cb()
}

const getDocumentMock = vi.fn()

vi.mock('../lib/pdfjs', () => ({
  pdfjs: { getDocument: (...args: unknown[]) => getDocumentMock(...args) },
}))

// Imported after the mock is registered, matching vi.mock's hoisting contract.
const { PdfPageCanvas } = await import('./PdfPageCanvas')

describe('PdfPageCanvas document caching', () => {
  let tasks: ReturnType<typeof fakeLoadingTask>[]

  beforeEach(() => {
    tasks = []
    resizeCallbacks = []
    mockClientWidth = 390
    mockClientHeight = 700
    getDocumentMock.mockReset()
    getDocumentMock.mockImplementation(() => {
      const task = fakeLoadingTask()
      tasks.push(task)
      return task
    })
    // jsdom has no real canvas 2D context - a truthy stub is enough for
    // the component to proceed past its `if (!context) return` guard.
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as unknown as RenderingContext)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads the document once per URL, not once per page/zoom/resize', async () => {
    const { rerender } = render(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={1} highlights={[]} />)
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1))
    expect(getDocumentMock).toHaveBeenCalledWith({ url: '/api/editions/a/pdf' })

    // A page turn within the same edition must reuse the already-loaded
    // document - no second getDocument call.
    rerender(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={2} highlights={[]} />)
    await waitFor(() => expect(tasks[0].doc.getPage).toHaveBeenCalledWith(2))
    expect(getDocumentMock).toHaveBeenCalledTimes(1)

    rerender(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={3} highlights={[]} />)
    await waitFor(() => expect(tasks[0].doc.getPage).toHaveBeenCalledWith(3))
    expect(getDocumentMock).toHaveBeenCalledTimes(1)
  })

  it('loads a fresh document when the edition (pdfUrl) actually changes, and destroys the old one', async () => {
    const { rerender } = render(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={1} highlights={[]} />)
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1))

    rerender(<PdfPageCanvas pdfUrl="/api/editions/b/pdf" pageNum={1} highlights={[]} />)
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(2))
    expect(getDocumentMock).toHaveBeenCalledWith({ url: '/api/editions/b/pdf' })
    expect(tasks[0].destroy).toHaveBeenCalled()
  })
})

describe('PdfPageCanvas canvas-size regression (Invalid canvas size / stuck loading)', () => {
  let tasks: ReturnType<typeof fakeLoadingTask>[]

  beforeEach(() => {
    tasks = []
    resizeCallbacks = []
    getDocumentMock.mockReset()
    getDocumentMock.mockImplementation(() => {
      const task = fakeLoadingTask()
      tasks.push(task)
      return task
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as unknown as RenderingContext)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('never calls page.render() while the container measures 0x0, and renders once a real size arrives', async () => {
    // Mirrors the mobile Page tab, mounted but CSS-hidden (display:none)
    // while the Text tab shows - a hidden element measures 0x0.
    mockClientWidth = 0
    mockClientHeight = 0
    const { container } = render(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={1} highlights={[]} />)
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(tasks[0].doc.getPage).toHaveBeenCalled())

    // Give the render effect a chance to run against the 0x0 container -
    // it must not throw, and must not have called page.render().
    await new Promise((r) => setTimeout(r, 20))
    expect(tasks[0].page.render).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('Failed to render')

    // The pane becomes visible (e.g. the user opens the Page tab) - a real
    // resize, which is exactly what ResizeObserver reports.
    mockClientWidth = 390
    mockClientHeight = 700
    fireResize()

    await waitFor(() => expect(tasks[0].page.render).toHaveBeenCalledTimes(1))
    const canvas = container.querySelector('canvas')!
    expect(canvas.width).toBeGreaterThan(0)
    expect(canvas.height).toBeGreaterThan(0)
    expect(Number.isFinite(canvas.width)).toBe(true)
    expect(Number.isFinite(canvas.height)).toBe(true)
  })

  it('clamps an oversized canvas to a safe maximum instead of failing', async () => {
    // A tiny native page blown up by a huge container width - at
    // fit-width scale this would ask for a canvas thousands of pixels on
    // a side, well past common browser limits (iOS Safari's is ~4096px
    // per side / ~16.7M px total).
    const bigTask = fakeLoadingTask(fakePage([0, 0, 10, 10]))
    tasks.push(bigTask)
    getDocumentMock.mockImplementation(() => bigTask)
    mockClientWidth = 20000
    mockClientHeight = 20000

    const { container } = render(<PdfPageCanvas pdfUrl="/api/editions/big/pdf" pageNum={1} highlights={[]} />)
    await waitFor(() => expect(bigTask.page.render).toHaveBeenCalledTimes(1))

    const canvas = container.querySelector('canvas')!
    expect(canvas.width).toBeLessThanOrEqual(4096)
    expect(canvas.height).toBeLessThanOrEqual(4096)
    expect(canvas.width * canvas.height).toBeLessThanOrEqual(16 * 1024 * 1024)
  })

  it('clears the loading overlay once rendering settles, even if a resize fires mid-fetch', async () => {
    // Reproduces the actual bug: ResizeObserver's guaranteed initial
    // callback firing while the document is still loading used to cancel
    // the effect invocation that would have cleared the spinner, leaving
    // "Loading page… 100%" stuck forever even though a later invocation
    // went on to render successfully.
    mockClientWidth = 390
    mockClientHeight = 700
    let resolveDoc!: (doc: unknown) => void
    const deferred = new Promise((resolve) => {
      resolveDoc = resolve
    })
    const page = fakePage()
    const doc = { getPage: vi.fn().mockResolvedValue(page) }
    const task = { promise: deferred, destroy: vi.fn(), doc, page }
    getDocumentMock.mockImplementation(() => task)

    const { container } = render(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={1} highlights={[]} />)
    expect(container.textContent).toContain('Loading page')

    // Fire several resizes while the document fetch is still in flight -
    // this is the race window.
    fireResize()
    fireResize()
    fireResize()

    resolveDoc(doc)
    await waitFor(() => expect(page.render).toHaveBeenCalled())
    await waitFor(() => expect(container.textContent).not.toContain('Loading page'))
  })

  it('shows a Retry affordance on a real render failure, not a silent stuck state', async () => {
    mockClientWidth = 390
    mockClientHeight = 700
    const page = fakePage()
    page.render = vi.fn(() => {
      throw new Error('boom')
    }) as unknown as typeof page.render
    const doc = { getPage: vi.fn().mockResolvedValue(page) }
    const task = { promise: Promise.resolve(doc), destroy: vi.fn(), doc, page }
    getDocumentMock.mockImplementation(() => task)

    const { findByRole, findByText } = render(<PdfPageCanvas pdfUrl="/api/editions/a/pdf" pageNum={1} highlights={[]} />)

    await findByText(/Failed to render page: boom/)
    await findByRole('button', { name: 'Retry' })
  })
})

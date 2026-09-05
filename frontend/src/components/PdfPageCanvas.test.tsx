import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The bug this guards against: pdfjs.getDocument({ url }) used to run
// fresh on every page turn / zoom change / pane resize, re-fetching and
// re-parsing the whole PDF each time instead of once per edition visit -
// see design/DESIGN.md. pdfjs-dist itself is an ESM namespace vitest can't
// spy on directly, so the mock targets our own thin wrapper (lib/pdfjs.ts)
// instead.

function fakePage() {
  return {
    view: [0, 0, 600, 800],
    getViewport: () => ({ width: 600, height: 800 }),
    render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
  }
}

function fakeLoadingTask() {
  const destroy = vi.fn()
  const doc = { getPage: vi.fn().mockResolvedValue(fakePage()) }
  return { promise: Promise.resolve(doc), destroy, doc }
}

// jsdom has no ResizeObserver - the component only uses it to trigger a
// re-fit on pane resize, irrelevant to the caching behavior under test.
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver)

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

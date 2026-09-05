import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { EditionDetailOut, PageArticlesOut, PageOut } from '../types/api'

// The bug this guards against: on mobile, a split Text/Page view has
// nowhere near enough room (see useIsMobile.ts), and fetching the whole
// PDF just to show article text wastes an ~11MB download nobody asked for
// - see design/DESIGN.md's item-3 measurements. The Page tab must exist
// but must not cause PdfPageCanvas to load a document until it's opened.

class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver)

function fakePage() {
  return {
    view: [0, 0, 600, 800],
    getViewport: () => ({
      width: 600,
      height: 800,
      convertToViewportPoint: (x: number, y: number) => [x, y],
    }),
    render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
  }
}

function fakeLoadingTask() {
  const doc = { getPage: vi.fn().mockResolvedValue(fakePage()) }
  return { promise: Promise.resolve(doc), destroy: vi.fn(), doc }
}

const getDocumentMock = vi.fn()

vi.mock('../lib/pdfjs', () => ({
  pdfjs: { getDocument: (...args: unknown[]) => getDocumentMock(...args) },
}))

// Imported after the mock is registered, matching vi.mock's hoisting contract.
const { PageReader } = await import('./PageReader')

function mockMatchMedia(mobile: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: mobile && query.includes('max-width'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}

const EDITION: EditionDetailOut = {
  edition_id: 'delhi__2025-09-13',
  edition: 'delhi',
  date: '2025-09-13',
  page_count: 2,
  article_count: 2,
  pages_with_articles: 2,
  pages_with_zero_articles: [],
  pages: [
    { page_num: 1, status: 'done' },
    { page_num: 2, status: 'done' },
  ],
}

const PAGE: PageOut = {
  page_num: 1,
  status: 'done',
  width: 600,
  height: 800,
  line_count: 40,
  article_count: 1,
  validation_ok: true,
  coverage_ratio: 0.9,
}

const ARTICLES: PageArticlesOut = {
  status: 'done',
  articles: [
    {
      article_id: 'p01-1',
      page: 1,
      section_kicker: 'FRONT PAGE',
      section_kicker_raw: 'FRONT PAGE',
      headline: 'A real headline',
      headline_raw: 'A real headline',
      deck: [],
      deck_raw: [],
      byline: '',
      byline_raw: '',
      dateline: '',
      dateline_raw: '',
      body: 'Body text of the article.',
      body_raw: 'Body text of the article.',
      captions: [],
      captions_raw: [],
      is_truncated: false,
      continues_on_page: null,
      confidence: 'high',
      rects: [[10, 10, 100, 50]],
      validation_ok: true,
      needs_review: false,
      validation_issues: [],
    },
  ],
}

function renderReader() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/reader/delhi__2025-09-13/1']}>
        <Routes>
          <Route path="/reader/:editionId/:pageNum" element={<PageReader />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  getDocumentMock.mockReset()
  getDocumentMock.mockImplementation(() => fakeLoadingTask())
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as unknown as RenderingContext)
  vi.spyOn(api, 'getEdition').mockResolvedValue(EDITION)
  vi.spyOn(api, 'getPage').mockResolvedValue(PAGE)
  vi.spyOn(api, 'getPageArticles').mockResolvedValue(ARTICLES)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('PageReader mobile tabs', () => {
  it('defaults to the Text tab and does not load the PDF document until Page is opened', async () => {
    mockMatchMedia(true)
    renderReader()

    expect(await screen.findByText('A real headline')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Text' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Page' })).toBeInTheDocument()

    // No PDF document load yet - the Page tab hasn't been opened.
    await new Promise((r) => setTimeout(r, 50))
    expect(getDocumentMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Page' }))

    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1))
  })

  it('renders the full split view on desktop, with no tabs and the PDF pane mounted immediately', async () => {
    mockMatchMedia(false)
    renderReader()

    expect(await screen.findByText('A real headline')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Text' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Page' })).not.toBeInTheDocument()

    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1))
  })
})

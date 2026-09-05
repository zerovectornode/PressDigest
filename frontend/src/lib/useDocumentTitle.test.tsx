import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useDocumentTitle } from './useDocumentTitle'

function TitleProbe({ title }: { title: string | null }) {
  useDocumentTitle(title)
  return null
}

describe('useDocumentTitle', () => {
  it('sets document.title to "{title} — PressDigest"', () => {
    render(<TitleProbe title="Summaries" />)
    expect(document.title).toBe('Summaries — PressDigest')
  })

  it('updates when the title prop changes, e.g. turning pages', () => {
    const { rerender } = render(<TitleProbe title="Page 1 — Delhi 2026-09-04" />)
    expect(document.title).toBe('Page 1 — Delhi 2026-09-04 — PressDigest')

    rerender(<TitleProbe title="Page 2 — Delhi 2026-09-04" />)
    expect(document.title).toBe('Page 2 — Delhi 2026-09-04 — PressDigest')
  })

  it('leaves document.title untouched when passed null (e.g. still loading)', () => {
    document.title = 'Untouched'
    render(<TitleProbe title={null} />)
    expect(document.title).toBe('Untouched')
  })

  it('restores the previous title on unmount', () => {
    document.title = 'Before'
    const { unmount } = render(<TitleProbe title="Pipeline" />)
    expect(document.title).toBe('Pipeline — PressDigest')
    unmount()
    expect(document.title).toBe('Before')
  })
})

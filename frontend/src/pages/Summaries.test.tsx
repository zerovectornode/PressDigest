import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Summaries } from './Summaries'

describe('Summaries page', () => {
  it('shows an honest empty state instead of fabricated ranked/summarised cards', () => {
    render(
      <MemoryRouter>
        <Summaries />
      </MemoryRouter>,
    )

    expect(screen.getByText(/summaries aren't built yet/i)).toBeInTheDocument()
    expect(screen.getByText(/ranking and summarisation are the next phase/i)).toBeInTheDocument()

    // No fabricated relevance score, category tag, or summary text of any
    // kind should ever be present - this page must never render mock data.
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })

  it('links to the Page Reader as the honest next step', () => {
    render(
      <MemoryRouter>
        <Summaries />
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: /page reader/i })
    expect(link).toHaveAttribute('href', '/reader')
  })
})

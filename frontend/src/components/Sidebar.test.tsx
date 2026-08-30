import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Sidebar } from './Sidebar'

describe('Sidebar', () => {
  it('renders the logo and all four nav routes', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByText('PressDigest')).toBeInTheDocument()
    for (const label of ['Home', 'Summaries', 'Page Reader', 'AI Chat']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument()
    }
  })
})

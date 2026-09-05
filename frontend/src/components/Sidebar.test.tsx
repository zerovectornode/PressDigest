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

  it('the logo is a real link to Home, not a click handler on a div', () => {
    render(
      <MemoryRouter initialEntries={['/summaries']}>
        <Sidebar />
      </MemoryRouter>,
    )

    // A real <a href="/"> is what makes middle-click/ctrl-click/keyboard
    // "open in new tab" work, unlike a div with an onClick navigate() call.
    const logo = screen.getByRole('link', { name: /PressDigest/ })
    expect(logo).toHaveAttribute('href', '/')
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { MobileNavDrawer } from './MobileNavDrawer'

describe('MobileNavDrawer', () => {
  it('opens the drawer on hamburger click, and closes on backdrop click', () => {
    render(
      <MemoryRouter>
        <MobileNavDrawer />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('dialog', { name: /navigation menu/i })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /open navigation menu/i }))
    const dialog = screen.getByRole('dialog', { name: /navigation menu/i })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Home' })).toBeInTheDocument()

    // The backdrop is the dialog's own first child overlay - clicking it
    // (not the drawer panel itself) should dismiss.
    // eslint-disable-next-line testing-library/no-node-access
    fireEvent.click(dialog.firstChild as Element)
    expect(screen.queryByRole('dialog', { name: /navigation menu/i })).not.toBeInTheDocument()
  })

  it('closes on Escape and returns focus to the hamburger button', () => {
    render(
      <MemoryRouter>
        <MobileNavDrawer />
      </MemoryRouter>,
    )

    const openButton = screen.getByRole('button', { name: /open navigation menu/i })
    fireEvent.click(openButton)
    expect(screen.getByRole('dialog', { name: /navigation menu/i })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /navigation menu/i })).not.toBeInTheDocument()
    expect(openButton).toHaveFocus()
  })

  it('closes when a nav link inside the drawer is clicked', () => {
    render(
      <MemoryRouter>
        <MobileNavDrawer />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /open navigation menu/i }))
    fireEvent.click(screen.getByRole('link', { name: 'Summaries' }))
    expect(screen.queryByRole('dialog', { name: /navigation menu/i })).not.toBeInTheDocument()
  })
})

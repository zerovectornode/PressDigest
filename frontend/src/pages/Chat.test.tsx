import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Chat } from './Chat'

describe('Chat page', () => {
  it('shows an honest empty state with no fake conversation', () => {
    render(
      <MemoryRouter>
        <Chat />
      </MemoryRouter>,
    )

    expect(screen.getByText(/ai chat isn't built yet/i)).toBeInTheDocument()
    // No chat transcript UI (message bubbles, input box) should be present.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  })
})

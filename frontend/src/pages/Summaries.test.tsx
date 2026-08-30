import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { RankingOut } from '../types/api'
import { Summaries } from './Summaries'

function renderWithProviders() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Summaries />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const EDITION = { edition_id: 'delhi__2025-09-13', edition: 'delhi', date: '2025-09-13', page_count: 18, article_count: 107 }

const RANKING: RankingOut = {
  generated_at: '2026-08-30T00:00:00Z',
  top_n: 20,
  validation_ok: true,
  validation_issues: [],
  duplicate_continuations: [],
  excluded: [],
  retried: false,
  eligible_count_note: null,
  total_tokens: 12000,
  all_cached: false,
  ranked: [
    {
      article_id: 'p01-1',
      page: 1,
      headline: "Karki is Nepal's first woman PM",
      rank: 1,
      importance_score: 95,
      category: 'INTERNATIONAL',
      why_it_matters: 'A real, model-generated significance note.',
      exclusion_risk: 'none',
    },
    {
      article_id: 'p03-2',
      page: 3,
      headline: 'Retail inflation eases',
      rank: 2,
      importance_score: 88,
      category: 'ECONOMY',
      why_it_matters: 'Another real significance note.',
      exclusion_risk: 'none',
    },
  ],
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('Summaries page', () => {
  it('shows an honest empty state when no editions have been ingested', async () => {
    vi.spyOn(api, 'listEditions').mockResolvedValue([])
    renderWithProviders()

    expect(await screen.findByText(/no editions ingested yet/i)).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })

  it('offers to rank the edition when no ranking has been computed yet, with no fabricated cards', async () => {
    vi.spyOn(api, 'listEditions').mockResolvedValue([EDITION])
    vi.spyOn(api, 'getRanking').mockRejectedValue(new Error('404'))
    renderWithProviders()

    expect(await screen.findByText(/hasn't been ranked yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /rank this edition/i })).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)
  })

  it('renders real ranked cards from the API, sorted by rank, with a working category filter', async () => {
    vi.spyOn(api, 'listEditions').mockResolvedValue([EDITION])
    vi.spyOn(api, 'getRanking').mockResolvedValue(RANKING)
    renderWithProviders()

    expect(await screen.findByText("Karki is Nepal's first woman PM")).toBeInTheDocument()
    expect(screen.getByText('Retail inflation eases')).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(2)

    // Filtering to ECONOMY should hide the INTERNATIONAL card.
    screen.getByRole('button', { name: 'ECONOMY' }).click()
    await waitFor(() => {
      expect(screen.queryByText("Karki is Nepal's first woman PM")).not.toBeInTheDocument()
    })
    expect(screen.getByText('Retail inflation eases')).toBeInTheDocument()
  })

  it('surfaces validation issues and duplicate-continuation warnings rather than hiding them', async () => {
    vi.spyOn(api, 'listEditions').mockResolvedValue([EDITION])
    vi.spyOn(api, 'getRanking').mockResolvedValue({
      ...RANKING,
      validation_ok: false,
      validation_issues: ["article 'p09-2': category 'FOO' is not in the fixed enum"],
      duplicate_continuations: [{ first_part_id: 'p01-1', first_part_page: 1, continues_on_page: 8, conflicting_id: 'p08-1' }],
    })
    renderWithProviders()

    expect(await screen.findByText(/1 validation issue/i)).toBeInTheDocument()
    expect(screen.getByText(/not in the fixed enum/i)).toBeInTheDocument()
    expect(screen.getByText(/possible duplicate continuation/i)).toBeInTheDocument()
  })
})

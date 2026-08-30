import { EmptyState } from '../components/EmptyState'
// SummaryCardGrid is built and ready (see the component for why it takes
// no default data), just not wired up: ranking and summarisation are not
// part of the pipeline yet. Flip SUMMARIES_ENABLED once they are.
// import { SummaryCardGrid } from '../components/SummaryCardGrid'

const SUMMARIES_ENABLED = false

export function Summaries() {
  if (!SUMMARIES_ENABLED) {
    return (
      <EmptyState
        title="Summaries aren't built yet"
        description="Ranking and summarisation are the next phase of the pipeline. Once articles are scored and summarised, they'll appear here as a scannable grid. For now, read extracted articles page by page."
        linkTo="/reader"
        linkLabel="Go to Page Reader"
      />
    )
  }
  return null
}

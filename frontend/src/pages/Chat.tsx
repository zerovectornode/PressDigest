import { EmptyState } from '../components/EmptyState'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export function Chat() {
  useDocumentTitle('AI Chat')
  return (
    <EmptyState
      title="AI Chat isn't built yet"
      description="Chatting with the paper's content is planned for a later phase - there's no model wired up here yet, so this page intentionally shows no conversation."
    />
  )
}

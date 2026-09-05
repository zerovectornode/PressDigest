import { Route, Routes } from 'react-router-dom'
import { MobileNavDrawer } from './components/MobileNavDrawer'
import { Sidebar } from './components/Sidebar'
import { Chat } from './pages/Chat'
import { Dashboard } from './pages/Dashboard'
import { PageReader } from './pages/PageReader'
import { Pipeline } from './pages/Pipeline'
import { Summaries } from './pages/Summaries'

export default function App() {
  return (
    // h-dvh, not h-screen (100vh) - on mobile browsers the address bar/
    // toolbar can make 100vh taller than the actually-visible viewport,
    // hiding content behind browser chrome; dvh tracks what's really
    // visible. No effect on desktop, where dvh and vh are the same.
    <div className="flex h-dvh w-full overflow-hidden">
      <Sidebar />
      {/* min-w-0 overrides flexbox's default min-width:auto, which would
          otherwise let this pane grow to fit its widest descendant (the PDF
          canvas at high zoom) instead of respecting flex-1's allocated
          share - that growth is what pushed the whole page into a
          horizontal scrollbar (see design/DESIGN.md "PDF pane sizing"). */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <MobileNavDrawer />
        <main className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/summaries" element={<Summaries />} />
            <Route path="/reader" element={<PageReader />} />
            <Route path="/reader/:editionId/:pageNum" element={<PageReader />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/pipeline/:runId" element={<Pipeline />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

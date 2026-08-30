export function formatTimestamp(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// pdf_hash is only set when a run actually re-extracted from the raw PDF
// (the API upload job) - a CLI `articles` re-validation replays Phase 2/3
// from the Gemini cache against already-extracted bronze data and never
// touches the PDF, so it has no hash to report. This is a real, existing
// distinction in the data, not a fabricated label.
export function runType(pdfHash: string | null | undefined): string {
  return pdfHash ? 'Full extract' : 'Re-validation'
}

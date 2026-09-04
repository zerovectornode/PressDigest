import type {
  EditionDetailOut,
  EditionSummaryOut,
  JobStatusOut,
  PageArticlesOut,
  PageOut,
  PageRawOut,
  ParsedMetadataOut,
  QuotaOut,
  RankingOut,
  RunDetailOut,
  RunSummaryOut,
  StageEventOut,
  StartJobOut,
} from '../types/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

export function parseMetadata(file: File): Promise<ParsedMetadataOut> {
  const form = new FormData()
  form.append('file', file)
  return request<ParsedMetadataOut>('/editions/parse-metadata', { method: 'POST', body: form })
}

export function createEdition(file: File, edition: string, date: string): Promise<StartJobOut> {
  const form = new FormData()
  form.append('file', file)
  const params = new URLSearchParams({ edition, date })
  return request<StartJobOut>(`/editions?${params}`, { method: 'POST', body: form })
}

export function getJobStatus(jobId: string): Promise<JobStatusOut> {
  return request<JobStatusOut>(`/jobs/${jobId}`)
}

export function listActiveJobs(): Promise<JobStatusOut[]> {
  return request<JobStatusOut[]>('/jobs/active')
}

export function listEditions(): Promise<EditionSummaryOut[]> {
  return request<EditionSummaryOut[]>('/editions')
}

export function getEdition(editionId: string): Promise<EditionDetailOut> {
  return request<EditionDetailOut>(`/editions/${editionId}`)
}

export function editionPdfUrl(editionId: string): string {
  return `/api/editions/${editionId}/pdf`
}

// Mirrors hindu_extract.api.edition_id.make_edition_id - the API never
// returns an edition_id for a job in progress (StartJobOut only has
// edition/date), so the Dashboard needs to build one itself to link to
// the reader before the job finishes.
export function makeEditionId(edition: string, date: string): string {
  return `${edition}__${date}`
}

export function getPage(editionId: string, pageNum: number): Promise<PageOut> {
  return request<PageOut>(`/editions/${editionId}/pages/${pageNum}`)
}

export function getPageArticles(editionId: string, pageNum: number): Promise<PageArticlesOut> {
  return request<PageArticlesOut>(`/editions/${editionId}/pages/${pageNum}/articles`)
}

export function listRuns(): Promise<RunSummaryOut[]> {
  return request<RunSummaryOut[]>('/runs')
}

export function getRun(runId: string): Promise<RunDetailOut> {
  return request<RunDetailOut>(`/runs/${runId}`)
}

export function getRunPageStages(runId: string, pageNum: number): Promise<StageEventOut[]> {
  return request<StageEventOut[]>(`/runs/${runId}/pages/${pageNum}`)
}

export function getRunPageRaw(runId: string, pageNum: number): Promise<PageRawOut> {
  return request<PageRawOut>(`/runs/${runId}/pages/${pageNum}/raw`)
}

export function getQuota(): Promise<QuotaOut> {
  return request<QuotaOut>('/quota')
}

export function getRanking(editionId: string): Promise<RankingOut> {
  return request<RankingOut>(`/editions/${editionId}/ranking`)
}

export function triggerRanking(editionId: string): Promise<RankingOut> {
  return request<RankingOut>(`/editions/${editionId}/ranking`, { method: 'POST' })
}

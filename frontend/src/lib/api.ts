import type {
  ArticleOut,
  EditionDetailOut,
  EditionSummaryOut,
  JobStatusOut,
  PageOut,
  PageRawOut,
  ParsedMetadataOut,
  QuotaOut,
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

export function listEditions(): Promise<EditionSummaryOut[]> {
  return request<EditionSummaryOut[]>('/editions')
}

export function getEdition(editionId: string): Promise<EditionDetailOut> {
  return request<EditionDetailOut>(`/editions/${editionId}`)
}

export function editionPdfUrl(editionId: string): string {
  return `/api/editions/${editionId}/pdf`
}

export function getPage(editionId: string, pageNum: number): Promise<PageOut> {
  return request<PageOut>(`/editions/${editionId}/pages/${pageNum}`)
}

export function getPageArticles(editionId: string, pageNum: number): Promise<ArticleOut[]> {
  return request<ArticleOut[]>(`/editions/${editionId}/pages/${pageNum}/articles`)
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

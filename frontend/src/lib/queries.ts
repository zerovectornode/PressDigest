import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from './api'

export function useParseMetadata() {
  return useMutation({ mutationFn: api.parseMetadata })
}

export function useCreateEdition() {
  return useMutation({
    mutationFn: ({ file, edition, date }: { file: File; edition: string; date: string }) =>
      api.createEdition(file, edition, date),
  })
}

const TERMINAL_JOB_STATUSES = new Set(['done', 'failed'])

export function useJobStatus(jobId: string | null) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJobStatus(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && TERMINAL_JOB_STATUSES.has(status)) {
        queryClient.invalidateQueries({ queryKey: ['editions'] })
        return false
      }
      return 1500
    },
  })
}

export function useEditions() {
  return useQuery({ queryKey: ['editions'], queryFn: api.listEditions })
}

export function useEdition(editionId: string | undefined) {
  return useQuery({
    queryKey: ['edition', editionId],
    queryFn: () => api.getEdition(editionId!),
    enabled: editionId !== undefined,
  })
}

export function usePage(editionId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['page', editionId, pageNum],
    queryFn: () => api.getPage(editionId!, pageNum!),
    enabled: editionId !== undefined && pageNum !== undefined,
    retry: false,
  })
}

export function usePageArticles(editionId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['page-articles', editionId, pageNum],
    queryFn: () => api.getPageArticles(editionId!, pageNum!),
    enabled: editionId !== undefined && pageNum !== undefined,
    retry: false,
  })
}

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: api.listRuns, refetchInterval: 5000 })
}

const TERMINAL_RUN_STATUSES = new Set(['done', 'failed'])

export function useRun(runId: string | undefined) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId!),
    enabled: runId !== undefined,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_RUN_STATUSES.has(status) ? false : 2000
    },
  })
}

export function useRunPageStages(runId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['run-page-stages', runId, pageNum],
    queryFn: () => api.getRunPageStages(runId!, pageNum!),
    enabled: runId !== undefined && pageNum !== undefined,
    retry: false,
  })
}

export function useRunPageRaw(runId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['run-page-raw', runId, pageNum],
    queryFn: () => api.getRunPageRaw(runId!, pageNum!),
    enabled: runId !== undefined && pageNum !== undefined,
    retry: false,
  })
}

export function useQuota() {
  return useQuery({ queryKey: ['quota'], queryFn: api.getQuota, refetchInterval: 10000 })
}

/** One stage-events query per page of a run, so the timeline/token views
 * can render as soon as each page's events arrive instead of waiting for a
 * single combined endpoint - dynamic count is fine here since useQueries
 * (unlike useQuery) is designed for a list whose length can change between
 * renders. */
export function useRunPagesStages(runId: string | undefined, pages: number[]) {
  return useQueries({
    queries: pages.map((pageNum) => ({
      queryKey: ['run-page-stages', runId, pageNum],
      queryFn: () => api.getRunPageStages(runId!, pageNum),
      enabled: runId !== undefined,
    })),
  })
}

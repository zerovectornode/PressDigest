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

const TERMINAL_JOB_STATUSES = new Set(['done', 'completed_with_errors', 'failed'])

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

/** Backs Dashboard reconnection: fires once on mount so a page reload
 * mid-extraction picks the job back up instead of showing the empty
 * upload UI as if nothing were running - see design/DESIGN.md. */
export function useActiveJobs() {
  return useQuery({ queryKey: ['active-jobs'], queryFn: api.listActiveJobs })
}

export function useEdition(editionId: string | undefined) {
  return useQuery({
    queryKey: ['edition', editionId],
    queryFn: () => api.getEdition(editionId!),
    enabled: editionId !== undefined,
  })
}

const TERMINAL_PAGE_STATUSES = new Set(['done', 'failed'])

export function usePage(editionId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['page', editionId, pageNum],
    queryFn: () => api.getPage(editionId!, pageNum!),
    enabled: editionId !== undefined && pageNum !== undefined,
    retry: false,
    // Pending/in-progress pages poll so the "still extracting" placeholder
    // upgrades itself once the page finishes, without a manual refresh.
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_PAGE_STATUSES.has(status) ? false : 2000
    },
  })
}

export function usePageArticles(editionId: string | undefined, pageNum: number | undefined) {
  return useQuery({
    queryKey: ['page-articles', editionId, pageNum],
    queryFn: () => api.getPageArticles(editionId!, pageNum!),
    enabled: editionId !== undefined && pageNum !== undefined,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_PAGE_STATUSES.has(status) ? false : 2000
    },
  })
}

/** Retries one page in place - the mutation itself blocks until the retry
 * finishes (see main.py's sync retry_page_route), so on success we just
 * push the fresh PageOut straight into the cache rather than re-fetching. */
export function useRetryPage(editionId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (pageNum: number) => api.retryPage(editionId!, pageNum),
    onSuccess: (page, pageNum) => {
      queryClient.setQueryData(['page', editionId, pageNum], page)
      queryClient.invalidateQueries({ queryKey: ['page-articles', editionId, pageNum] })
      queryClient.invalidateQueries({ queryKey: ['edition', editionId] })
      queryClient.invalidateQueries({ queryKey: ['editions'] })
    },
  })
}

/** Bulk "retry N failed pages" - this one starts a background job (see
 * jobs.start_retry_job), so the caller feeds the returned job_id into the
 * same useJobStatus/ProgressPanel machinery a fresh extraction uses. */
export function useRetryFailedPages(editionId: string | undefined) {
  return useMutation({ mutationFn: () => api.retryFailedPages(editionId!) })
}

export function useDeleteEdition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (editionId: string) => api.deleteEdition(editionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['editions'] }),
  })
}

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: api.listRuns, refetchInterval: 5000 })
}

const TERMINAL_RUN_STATUSES = new Set(['done', 'completed_with_errors', 'failed'])

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

export function useRanking(editionId: string | undefined) {
  return useQuery({
    queryKey: ['ranking', editionId],
    queryFn: () => api.getRanking(editionId!),
    enabled: editionId !== undefined,
    retry: false,
  })
}

export function useTriggerRanking(editionId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.triggerRanking(editionId!),
    onSuccess: (result) => queryClient.setQueryData(['ranking', editionId], result),
  })
}

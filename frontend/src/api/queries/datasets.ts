import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { DatasetImportRequest, DatasetImportResult, EvalCase, Page } from '../types'
import { assetKeys } from './assets'

export const datasetKeys = {
  cases: (revId: string, filter: CaseFilter) => ['datasets', 'cases', revId, filter] as const,
}

export interface CaseFilter {
  tag?: string[]
  case_key?: string
  q?: string
}

/** 导入数据集；同名数据集已存在时新增修订 */
export function useImportDataset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DatasetImportRequest) => api.post<DatasetImportResult>('/datasets/import', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

/** 数据集修订的用例（按 idx 升序，游标分页，供虚拟滚动逐页加载） */
export function useDatasetCases(revId: string | undefined, filter: CaseFilter = {}, limit = 100) {
  return useInfiniteQuery({
    queryKey: [...datasetKeys.cases(revId ?? '', filter), limit],
    queryFn: ({ pageParam }) =>
      api.get<Page<EvalCase>>(`/datasets/${revId}/cases`, {
        query: { tag: filter.tag, case_key: filter.case_key, q: filter.q, limit, cursor: pageParam },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!revId,
  })
}

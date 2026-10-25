import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type {
  Asset,
  AssetCreate,
  AssetDeleteResult,
  AssetDiff,
  AssetKind,
  AssetReferences,
  AssetRevision,
  AssetRevisionCreate,
  AssetRevisionDetail,
  AssetWriteResult,
  Page,
} from '../types'

export const assetKeys = {
  all: ['assets'] as const,
  list: (p: AssetListParams) => [...assetKeys.all, 'list', p] as const,
  detail: (id: string) => [...assetKeys.all, 'detail', id] as const,
  revisions: (id: string) => [...assetKeys.all, 'revisions', id] as const,
  revision: (revId: string) => [...assetKeys.all, 'revision', revId] as const,
  diff: (l: string, r: string) => [...assetKeys.all, 'diff', l, r] as const,
  refs: (id: string) => [...assetKeys.all, 'refs', id] as const,
}

export interface AssetListParams {
  kind?: AssetKind | ''
  q?: string
  include_deleted?: boolean
  limit?: number
}

/** 资产列表，支持"加载更多"；可用 `data.pages.flatMap(p => p.items)` 取全部 */
export function useAssets(params: AssetListParams = {}, enabled = true) {
  return useInfiniteQuery({
    queryKey: assetKeys.list(params),
    queryFn: ({ pageParam }) =>
      api.get<Page<Asset>>('/assets', {
        query: {
          kind: params.kind,
          q: params.q,
          include_deleted: params.include_deleted,
          limit: params.limit ?? 100,
          cursor: pageParam,
        },
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useAsset(id: string | undefined) {
  return useQuery({
    queryKey: assetKeys.detail(id ?? ''),
    queryFn: () => api.get<Asset>(`/assets/${id}`),
    enabled: !!id,
  })
}

export function useAssetRevisions(id: string | undefined, limit = 50) {
  return useInfiniteQuery({
    queryKey: [...assetKeys.revisions(id ?? ''), limit],
    queryFn: ({ pageParam }) =>
      api.get<Page<AssetRevision>>(`/assets/${id}/revisions`, { query: { limit, cursor: pageParam } }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!id,
  })
}

/** 修订及内容（`GET /assets/revisions/{revision_id}`） */
export function useRevision(revId: string | undefined | null) {
  return useQuery({
    queryKey: assetKeys.revision(revId ?? ''),
    queryFn: () => api.get<AssetRevisionDetail>(`/assets/revisions/${revId}`),
    enabled: !!revId,
    staleTime: 5 * 60_000, // 修订不可变
  })
}

/** 按资产 id 与修订号取内容（rev 可为 'latest'） */
export function useRevisionByRev(assetId: string | undefined, rev: number | 'latest' | undefined) {
  return useQuery({
    queryKey: [...assetKeys.revisions(assetId ?? ''), 'by-rev', rev],
    queryFn: () => api.get<AssetRevisionDetail>(`/assets/${assetId}/revisions/${rev}`),
    enabled: !!assetId && rev !== undefined,
  })
}

export function useAssetDiff(left: string | undefined | null, right: string | undefined | null) {
  return useQuery({
    queryKey: assetKeys.diff(left ?? '', right ?? ''),
    queryFn: () => api.get<AssetDiff>('/assets/diff', { query: { left: left!, right: right! } }),
    enabled: !!left && !!right,
    staleTime: 5 * 60_000,
  })
}

export function useAssetReferences(id: string | undefined) {
  return useQuery({
    queryKey: assetKeys.refs(id ?? ''),
    queryFn: () => api.get<AssetReferences>(`/assets/${id}/references`),
    enabled: !!id,
  })
}

/** 上传文件得到 blob_upload_id（内容的 sha256），之后在创建资产/修订时代替 content */
export function uploadBlob(file: File | Blob, filename?: string): Promise<{ blob_upload_id: string; size: number }> {
  const fd = new FormData()
  fd.append('file', file, filename ?? (file instanceof File ? file.name : 'upload.bin'))
  return api.post('/assets/upload', { body: fd })
}

export function useCreateAsset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AssetCreate) => api.post<AssetWriteResult>('/assets', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

export function useCreateRevision() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AssetRevisionCreate }) =>
      api.post<AssetWriteResult>(`/assets/${id}/revisions`, { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

export function useUpdateAsset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, description }: { id: string; description: string }) =>
      api.patch<Asset>(`/assets/${id}`, { body: { description } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

/** 默认软删除；`hard` 被引用时抛 ApiError(code=ASSET_IN_USE) */
export function useDeleteAsset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, hard }: { id: string; hard?: boolean }) =>
      api.delete<AssetDeleteResult>(`/assets/${id}`, { query: { hard: hard ? true : undefined } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

export function useRestoreAsset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<Asset>(`/assets/${id}/restore`),
    onSuccess: () => qc.invalidateQueries({ queryKey: assetKeys.all }),
  })
}

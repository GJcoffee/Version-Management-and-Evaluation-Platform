import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { Page, ProviderCreate, ProviderOut, ProviderTestResult, ProviderUpdate } from '../types'

export const providerKeys = {
  all: ['providers'] as const,
  list: () => [...providerKeys.all, 'list'] as const,
}

/** 兼容后端返回数组或分页对象两种形态 */
export function normalizeProviderList(data: ProviderOut[] | Page<ProviderOut>): ProviderOut[] {
  return Array.isArray(data) ? data : data.items
}

export function fetchProviders(): Promise<ProviderOut[]> {
  return api.get<ProviderOut[] | Page<ProviderOut>>('/providers').then(normalizeProviderList)
}

export function useProviders() {
  return useQuery({ queryKey: providerKeys.list(), queryFn: fetchProviders })
}

export function useCreateProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ProviderCreate) => api.post<ProviderOut>('/providers', { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: providerKeys.all }),
  })
}

export function useUpdateProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProviderUpdate }) =>
      api.patch<ProviderOut>(`/providers/${id}`, { body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: providerKeys.all }),
  })
}

export function useDeleteProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete(`/providers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: providerKeys.all }),
  })
}

/** 测试连接：使用服务端已保存的配置（不是对话框中未保存的修改） */
export function useTestProvider() {
  return useMutation({
    mutationFn: (id: string) => api.post<ProviderTestResult>(`/providers/${id}/test`),
  })
}

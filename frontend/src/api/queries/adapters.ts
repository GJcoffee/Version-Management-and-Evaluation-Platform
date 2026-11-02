import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'

export interface AdapterBinding {
  id: string
  version: string
  config: Record<string, unknown>
  credential_ref: string
}
export interface BindingSnapshot {
  revision: number
  binding: AdapterBinding | null
  fingerprint?: string
  upstream?: Record<string, unknown>
  reason?: string
  created_at?: string
}
export interface BindingState {
  version_id: string
  current: BindingSnapshot
  history: BindingSnapshot[]
}
export interface AdapterDefinition {
  id: string
  name: string
  version: string
  capabilities: string[]
  config_schema: {
    required?: string[]
    properties: Record<string, { type?: string; anyOf?: unknown[]; default?: unknown; title?: string }>
  }
}
export const useAdapters = () => useQuery({
  queryKey: ['adapters'], queryFn: () => api.get<AdapterDefinition[]>('/adapters'),
})
export const useAdapterCredentials = () => useQuery({
  queryKey: ['adapter-credentials'],
  queryFn: () => api.get<{ ref: string; name: string; adapter_id: string }[]>('/adapters/credentials'),
})
export const useBindingState = (id: string) => useQuery({
  queryKey: ['adapter-binding', id],
  queryFn: () => api.get<BindingState>(`/versions/${id}/adapter-binding`),
})
export function useBindingMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => api.post<unknown>(path, { body }),
    onSuccess: (result) => {
      const applied = Array.isArray(result)
        ? result.some((item) => item?.applied === true)
        : (result as { applied?: boolean } | null)?.applied === true
      if (!applied) return
      void qc.invalidateQueries({ queryKey: ['adapter-binding'] })
      void qc.invalidateQueries({ queryKey: ['versions'] })
    },
  })
}

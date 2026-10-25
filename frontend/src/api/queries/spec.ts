import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import type { SpecSchema } from '../types'

export const specKeys = {
  schema: ['spec', 'schema'] as const,
}

/** AgentSpec 的 JSON Schema（几乎不变，缓存到刷新为止） */
export function useSpecSchema() {
  return useQuery({
    queryKey: specKeys.schema,
    queryFn: () => api.get<SpecSchema>('/spec/schema'),
    staleTime: Infinity,
  })
}

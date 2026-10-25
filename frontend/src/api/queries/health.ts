import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import type { HealthOut } from '../types'

export const healthKeys = {
  all: ['health'] as const,
}

/** 健康状态；默认每 5 秒轮询（页面不可见时由 TanStack Query 自动暂停） */
export function useHealth(options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: healthKeys.all,
    queryFn: () => api.get<HealthOut>('/health'),
    refetchInterval: options.refetchInterval ?? 5000,
  })
}

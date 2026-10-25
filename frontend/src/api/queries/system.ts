import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { GcResult } from '../types'
import { healthKeys } from './health'

/** 数据清理：删除无引用 blob（POST /system/gc） */
export function useGc() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<GcResult>('/system/gc'),
    onSuccess: () => qc.invalidateQueries({ queryKey: healthKeys.all }),
  })
}

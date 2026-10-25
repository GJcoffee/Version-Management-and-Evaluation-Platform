import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { SettingsOut, SettingsValues } from '../types'

export const settingsKeys = {
  all: ['settings'] as const,
}

export function useSettings() {
  return useQuery({ queryKey: settingsKeys.all, queryFn: () => api.get<SettingsOut>('/settings') })
}

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (values: Partial<SettingsValues>) => api.patch<SettingsOut>('/settings', { body: { values } }),
    onSuccess: (data) => {
      if (data && typeof data === 'object' && 'values' in data) qc.setQueryData(settingsKeys.all, data)
      else qc.invalidateQueries({ queryKey: settingsKeys.all })
    },
  })
}

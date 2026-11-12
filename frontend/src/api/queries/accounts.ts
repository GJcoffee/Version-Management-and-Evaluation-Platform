import { useQuery } from '@tanstack/react-query'
import { api } from '../client'

export interface PlatformAccount {
  id: string
  adapter_id: string
  name: string
  origin: string
  tenant_name: string
  username: string
  config_revision: number
  session_revision: number
  enabled: boolean
  status: string
  last_checked_at: string | null
  last_error: string | null
  next_check_at: string | null
}

export interface AccountSessionRevision {
  revision: number
  config_revision: number
  expires_at: string | null
  source: string
  created_at: string
  current: boolean
}

export const usePlatformAccounts = () => useQuery({
  queryKey: ['platform-accounts'],
  queryFn: () => api.get<PlatformAccount[]>('/adapters/accounts'),
  refetchInterval: 60000,
})

export const useAccountSessionRevisions = (id: string) => useQuery({
  queryKey: ['platform-accounts', id, 'revisions'],
  queryFn: () => api.get<AccountSessionRevision[]>(`/adapters/accounts/${id}/revisions`),
})

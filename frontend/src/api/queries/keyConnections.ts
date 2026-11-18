import { useQuery } from '@tanstack/react-query'
import { api } from '../client'

export interface TraceKeyConnection {
  id: string
  name: string
  origin: string
  top_url: string
  workspace_id: string
  credential_source: 'key' | 'hicli'
  revision: number
  enabled: boolean
  status: string
  last_checked_at: string | null
  last_error: string | null
}

export const useTraceKeyConnections = () => useQuery({
  queryKey: ['trace-key-connections'],
  queryFn: () => api.get<TraceKeyConnection[]>('/adapters/key-connections'),
  refetchInterval: 60000,
})

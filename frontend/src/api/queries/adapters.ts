import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'

export interface AdapterBinding {
  /** 平台 id，如 hiagent；实现由平台统一维护 */
  adapter: string
  /** 适配器大版本；只有平台 API 不兼容时才变化 */
  major: number
  config: Record<string, unknown>
  credential_ref: string
}

/** 平台升级前冻在历史会话 / 评测快照里的旧绑定 */
export interface LegacyAdapterBinding {
  id: string
  version: string
  config: Record<string, unknown>
  credential_ref: string
}

/** 环境接口新写入始终是 AdapterBinding；历史快照可能是 LegacyAdapterBinding */
export type AnyAdapterBinding = AdapterBinding | LegacyAdapterBinding


export type AdapterEnvironmentName = 'production' | 'test'

export const ENVIRONMENT_LABEL: Record<AdapterEnvironmentName, string> = { production: '生产', test: '测试' }

/** 存档来源：编辑 / 推送 / 回滚 / 迁移 */
export type RevisionSource = 'edit' | 'push' | 'rollback' | 'migration'
export const SOURCE_LABEL: Record<RevisionSource, string> = {
  edit: '编辑', push: '推送', rollback: '回滚', migration: '迁移',
}

/** 会话 / 评测运行固化的接入快照（旧数据没有 environment） */
export interface BindingSnapshot {
  /** 所用环境；旧的版本绑定快照没有此字段 */
  environment?: AdapterEnvironmentName
  /** 环境存档号 */
  revision: number
  binding: AnyAdapterBinding | null
  fingerprint?: string
  /** 存档说明 */
  reason?: string
  /** 请求的环境与实际不同时（测试未配置或与生产相同）记录请求的环境 */
  requested_environment?: AdapterEnvironmentName
  upstream?: Record<string, unknown>
}

export interface EnvironmentState {
  environment: AdapterEnvironmentName
  configured: boolean
  /** 当前存档号，0 表示尚未配置 */
  revision: number
  binding: AdapterBinding | null
  fingerprint: string | null
  source: RevisionSource | null
  reason: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AdapterEnvironments {
  production: EnvironmentState
  test: EnvironmentState
  /** 测试已配置且与生产不同：发起时才提供「生产 | 测试」选项 */
  test_selectable: boolean
}

export interface EnvironmentRevision {
  revision: number
  current: boolean
  binding: AdapterBinding
  fingerprint: string
  source: RevisionSource
  reason: string
  origin: Record<string, unknown> | null
  created_at: string
}

export interface EnvironmentChange {
  environment: AdapterEnvironmentName
  applied: boolean
  message?: string
  before: EnvironmentState
  target: EnvironmentState
}

export interface AdapterDefinition {
  adapter: string
  major: number
  name: string
  capabilities: string[]
  config_schema: {
    required?: string[]
    properties: Record<string, { type?: string; anyOf?: unknown[]; default?: unknown; title?: string }>
  }
  /** 当前实现的源码指纹；平台升级后所有绑定自动跟随 */
  impl_digest: string
  /** 旧大版本的下线日期；活跃实现为 null */
  sunset: string | null
}
export const useAdapters = () => useQuery({
  queryKey: ['adapters'], queryFn: () => api.get<AdapterDefinition[]>('/adapters'),
})
export const useAdapterCredentials = () => useQuery({
  queryKey: ['adapter-credentials'],
  queryFn: () => api.get<{ ref: string; name: string; adapter_id: string }[]>('/adapters/credentials'),
})

const platformQuery = (adapter?: string) => adapter && adapter !== 'hiagent' ? { adapter } : undefined
export const useAdapterEnvironments = (adapter?: string) => useQuery({
  queryKey: adapter && adapter !== 'hiagent' ? ['adapter-environments', adapter] : ['adapter-environments'],
  queryFn: () => api.get<AdapterEnvironments>('/adapter-environments', { query: platformQuery(adapter) }),
})
export const useEnvironmentRevisions = (name: AdapterEnvironmentName, adapter?: string) => useQuery({
  queryKey: ['adapter-environments', adapter ?? 'hiagent', name, 'revisions'],
  queryFn: () => api.get<EnvironmentRevision[]>(`/adapter-environments/${name}/revisions`, { query: platformQuery(adapter) }),
})

function useEnvironmentMutation<T>(call: (body: T) => Promise<EnvironmentChange>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: call,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['adapter-environments'] }),
  })
}
export const useUpdateTest = (adapter?: string) => useEnvironmentMutation((body: {
  binding: AdapterBinding; expected_revision: number; reason: string
}) => api.put<EnvironmentChange>('/adapter-environments/test', { body, query: platformQuery(adapter) }))
export const usePushToProduction = (adapter?: string) => useEnvironmentMutation((body: {
  test_revision: number; reason?: string
}) => api.post<EnvironmentChange>('/adapter-environments/production/push', { body, query: platformQuery(adapter) }))
export const useRollbackProduction = (adapter?: string) => useEnvironmentMutation((body: {
  revision: number; reason?: string
}) => api.post<EnvironmentChange>('/adapter-environments/production/rollback', { body, query: platformQuery(adapter) }))

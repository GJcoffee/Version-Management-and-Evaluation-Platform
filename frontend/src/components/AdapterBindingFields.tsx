import { useId, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useAdapters, useAdapterCredentials, type AdapterBinding } from '@/api/queries/adapters'
import { usePlatformAccounts } from '@/api/queries/accounts'
import { Button } from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { toast } from '@/stores/toast'

/** 适配器环境（测试）的连接配置表单，由适配器注册表驱动。 */
export function AdapterBindingFields({ value, onChange, disabled, platform }: {
  value: AdapterBinding | null
  onChange: (binding: AdapterBinding | null) => void
  disabled?: boolean
  platform?: string
}) {
  const adapters = useAdapters()
  const formId = useId()
  const credentials = useAdapterCredentials()
  const accounts = usePlatformAccounts()
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [secret, setSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)
  const definition = adapters.data?.find((a) => a.adapter === value?.adapter && a.major === value.major)
  const patch = (data: Partial<AdapterBinding>) => value && onChange({ ...value, ...data })
  const saveCredential = async () => {
    setSaving(true)
    try {
      const result = await api.post<{ ref: string }>('/adapters/credentials', {
        body: { name, secret, adapter_id: value?.adapter },
      })
      patch({ credential_ref: result.ref })
      setSecret('')
      setAdding(false)
      void qc.invalidateQueries({ queryKey: ['adapter-credentials'] })
    } catch {
      toast.error('凭据保存失败')
    } finally { setSaving(false) }
  }
  return <div className="space-y-3 rounded-lg border border-border p-3">
    {!platform && <Field label="平台接入" htmlFor={`${formId}-adapter`} hint="一个平台一个实现，由平台统一更新；这里只维护连接配置与凭据">
      <Select id={`${formId}-adapter`} disabled={disabled || adapters.isLoading} value={value ? `${value.adapter}@${value.major}` : ''}
        onChange={(e) => {
          const selected = adapters.data?.find((a) => `${a.adapter}@${a.major}` === e.target.value)
          onChange(selected ? {
            adapter: selected.adapter, major: selected.major, credential_ref: '',
            config: Object.fromEntries(Object.entries(selected.config_schema.properties)
              .filter(([, property]) => property.default !== undefined)
              .map(([key, property]) => [key, property.default])),
          } : null)
        }}>
        <option value="">选择平台适配器</option>
        {adapters.data?.map((a) => <option key={`${a.adapter}@${a.major}`} value={`${a.adapter}@${a.major}`}>
          {a.name} · 实现 v{a.major}{a.sunset ? `（${a.sunset} 下线）` : ''}
        </option>)}
        {value && !definition && <option value={`${value.adapter}@${value.major}`}>
          {value.adapter} · 实现 v{value.major}（不可用）
        </option>}
      </Select>
      {adapters.isError && <p className="mt-1 text-xs text-danger">适配器列表读取失败</p>}
    </Field>}
    {value && <>
      <p className="text-xs text-muted-foreground">{definition?.capabilities.join(' · ')}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {Object.entries(definition?.config_schema.properties ?? {}).filter(([key]) =>
          !['business_bridge', 'observe_account_ref'].includes(key)).map(([key, property]) => <Field
          key={key} label={key} htmlFor={`${formId}-${key}`} required={definition?.config_schema.required?.includes(key)}>
          <Input id={`${formId}-${key}`} disabled={disabled} type={property.type === 'number' ? 'number' : 'text'}
            value={String(value.config[key] ?? '')}
            onChange={(e) => patch({ config: {
              ...value.config, [key]: property.type === 'number' ? Number(e.target.value) : e.target.value || null,
            } })} />
        </Field>)}
      </div>
      {value.adapter === 'hiagent' && <Field label="Trace 账号" htmlFor={`${formId}-account`}
        hint="账号自动维护 Cookie／CSRF，站点需与 observe_base_url 一致">
        <Select id={`${formId}-account`} disabled={disabled || accounts.isLoading}
          value={String(value.config.observe_account_ref ?? '')}
          onChange={(e) => patch({ config: { ...value.config, observe_account_ref: e.target.value || null } })}>
          <option value="">沿用凭据中的 observe_headers</option>
          {accounts.data?.filter((a) => a.adapter_id === value.adapter).map((a) =>
            <option key={a.id} value={a.id} disabled={!a.enabled}>
              {a.name} · {a.origin}{!a.enabled ? '（已停用）' : ''}
            </option>)}
        </Select>
        {accounts.isError && <p className="mt-1 text-xs text-danger">Trace 账号列表读取失败</p>}
      </Field>}
      <Field label="凭据引用" htmlFor={`${formId}-credential`} required>
        <Select id={`${formId}-credential`} disabled={disabled} value={value.credential_ref}
          onChange={(e) => patch({ credential_ref: e.target.value })}>
          <option value="">选择凭据</option>
          {credentials.data?.filter((c) => c.adapter_id === value.adapter).map((c) =>
            <option key={c.ref} value={c.ref}>{c.name}</option>)}
        </Select>
      </Field>
      <p className="text-xs text-muted-foreground">发布 API Key 失效时新增凭据并推送生产。选择 Trace 账号后，Cookie／CSRF 到期自动更新；旧轮次可选择最新配置重查 Trace。</p>
      <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setAdding(!adding)}>新增凭据</Button>
      <Button type="button" size="sm" variant="outline" disabled={disabled || !value.credential_ref}
        loading={checking}
        onClick={() => {
          setChecking(true)
          void api.post<{ ok: boolean; message: string }>('/adapters/check-connection', { body: value })
            .then((result) => result.ok ? toast.success(result.message) : toast.error(result.message))
            .catch(() => toast.error('连接配置检查失败'))
            .finally(() => setChecking(false))
        }}>检查上游认证</Button>
      {adding && <div className="space-y-2">
        <Field label="凭据名称" htmlFor={`${formId}-name`}><Input id={`${formId}-name`} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="API Key 或凭据 JSON" htmlFor={`${formId}-secret`} hint="填写发布 API Key；旧配置仍支持含 api_key 与 observe_headers 的 JSON">
          <Input id={`${formId}-secret`} type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
        </Field>
        <Button type="button" size="sm" disabled={!name.trim() || !secret.trim()} loading={saving}
          onClick={() => void saveCredential()}>保存凭据</Button>
      </div>}
    </>}
  </div>
}

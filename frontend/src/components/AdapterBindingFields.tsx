import { useId, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useAdapters, useAdapterCredentials, type AdapterBinding } from '@/api/queries/adapters'
import { Button } from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { toast } from '@/stores/toast'

/** 适配器环境（测试）的连接配置表单，由适配器注册表驱动。 */
export function AdapterBindingFields({ value, onChange, disabled }: {
  value: AdapterBinding | null
  onChange: (binding: AdapterBinding | null) => void
  disabled?: boolean
}) {
  const adapters = useAdapters()
  const formId = useId()
  const credentials = useAdapterCredentials()
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [secret, setSecret] = useState('')
  const [saving, setSaving] = useState(false)
  const definition = adapters.data?.find((a) => a.id === value?.id && a.version === value.version)
  const patch = (data: Partial<AdapterBinding>) => value && onChange({ ...value, ...data })
  const saveCredential = async () => {
    setSaving(true)
    try {
      const result = await api.post<{ ref: string }>('/adapters/credentials', {
        body: { name, secret, adapter_id: value?.id },
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
    <Field label="调用接入" htmlFor={`${formId}-adapter`} hint="固定具体适配器版本；凭据只保存引用">
      <Select id={`${formId}-adapter`} disabled={disabled || adapters.isLoading} value={value ? `${value.id}@${value.version}` : ''}
        onChange={(e) => {
          const selected = adapters.data?.find((a) => `${a.id}@${a.version}` === e.target.value)
          onChange(selected ? {
            id: selected.id, version: selected.version, credential_ref: '',
            config: Object.fromEntries(Object.entries(selected.config_schema.properties)
              .filter(([, property]) => property.default !== undefined)
              .map(([key, property]) => [key, property.default])),
          } : null)
        }}>
        <option value="">选择适配器</option>
        {adapters.data?.map((a) => <option key={`${a.id}@${a.version}`} value={`${a.id}@${a.version}`}>
          {a.name} · {a.version}
        </option>)}
        {value && !definition && <option value={`${value.id}@${value.version}`}>{value.id}@{value.version}（不可用）</option>}
      </Select>
      {adapters.isError && <p className="mt-1 text-xs text-danger">适配器列表读取失败</p>}
    </Field>
    {value && <>
      <p className="text-xs text-muted-foreground">{definition?.capabilities.join(' · ')}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {Object.entries(definition?.config_schema.properties ?? {}).map(([key, property]) => <Field
          key={key} label={key} htmlFor={`${formId}-${key}`} required={definition?.config_schema.required?.includes(key)}>
          <Input id={`${formId}-${key}`} disabled={disabled} type={property.type === 'number' ? 'number' : 'text'}
            value={String(value.config[key] ?? '')}
            onChange={(e) => patch({ config: {
              ...value.config, [key]: property.type === 'number' ? Number(e.target.value) : e.target.value || null,
            } })} />
        </Field>)}
      </div>
      <Field label="凭据引用" htmlFor={`${formId}-credential`} required>
        <Select id={`${formId}-credential`} disabled={disabled} value={value.credential_ref}
          onChange={(e) => patch({ credential_ref: e.target.value })}>
          <option value="">选择凭据</option>
          {credentials.data?.filter((c) => c.adapter_id === value.id).map((c) =>
            <option key={c.ref} value={c.ref}>{c.name}</option>)}
        </Select>
      </Field>
      <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setAdding(!adding)}>新增凭据</Button>
      <Button type="button" size="sm" variant="outline" disabled={disabled || !value.credential_ref}
        onClick={() => {
          void api.post<{ ok: boolean; message: string }>('/adapters/check-connection', { body: value })
            .then((result) => result.ok ? toast.success(result.message) : toast.error(result.message))
            .catch(() => toast.error('连接配置检查失败'))
        }}>检查连接配置</Button>
      {adding && <div className="space-y-2">
        <Field label="凭据名称" htmlFor={`${formId}-name`}><Input id={`${formId}-name`} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="API Key 或凭据 JSON" htmlFor={`${formId}-secret`} hint="Trace 可在 JSON 中配置 api_key 与 observe_headers">
          <Input id={`${formId}-secret`} type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
        </Field>
        <Button type="button" size="sm" disabled={!name.trim() || !secret.trim()} loading={saving}
          onClick={() => void saveCredential()}>保存凭据</Button>
      </div>}
    </>}
  </div>
}

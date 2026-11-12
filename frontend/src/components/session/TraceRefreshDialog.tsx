import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAdapterCredentials, useAdapterEnvironments } from '@/api/queries/adapters'
import type { TraceRefreshOptions } from '@/api/queries/sessions'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'

export function TraceRefreshDialog({ platform, environment, onSubmit, onClose, pending }: {
  platform: string
  environment: 'production' | 'test'
  onSubmit: (options: TraceRefreshOptions) => void
  onClose: () => void
  pending: boolean
}) {
  const [source, setSource] = useState<'current' | 'original'>('current')
  const [pickedEnvironment, setPickedEnvironment] = useState(environment)
  const [credential, setCredential] = useState('')
  const envs = useAdapterEnvironments(platform)
  const credentials = useAdapterCredentials()
  const state = envs.data?.[pickedEnvironment]
  return <Dialog open onOpenChange={(open) => !open && !pending && onClose()} title="重新获取 Trace"
    description="查询原轮次的消息，保留原应用、版本和执行结果。更新凭据后可使用最新配置再次查询。"
    footer={<>
      <Button variant="outline" disabled={pending} onClick={onClose}>取消</Button>
      <Button loading={pending} disabled={source === 'current' && (!state?.configured || envs.isFetching)}
        onClick={() => onSubmit({
          config_source: source, ...(source === 'current' ? { environment: pickedEnvironment } : {}),
          ...(credential ? { credential_ref: credential } : {}),
        })}>获取 Trace</Button>
    </>}>
    <div className="space-y-3">
      <Field label="查询配置">
        <Select aria-label="查询配置" value={source} disabled={pending} onChange={(event) => setSource(event.target.value as typeof source)}>
          <option value="current">平台最新配置</option>
          <option value="original">原会话配置</option>
        </Select>
      </Field>
      {source === 'current' && <>
        <Field label="使用环境">
          <Select aria-label="使用环境" value={pickedEnvironment} disabled={pending}
            onChange={(event) => setPickedEnvironment(event.target.value as typeof pickedEnvironment)}>
            <option value="production">生产</option><option value="test">测试</option>
          </Select>
        </Field>
        <p className="text-xs text-muted-foreground">
          {state?.configured ? `使用存档 #${state.revision} 的观测配置与凭据` : '该环境尚未配置'}
        </p>
      </>}
      <Field label="查询凭据" hint="可直接选择新凭据；留空则沿用所选配置的凭据">
        <Select aria-label="查询凭据" value={credential} disabled={pending} onChange={(event) => setCredential(event.target.value)}>
          <option value="">沿用配置凭据</option>
          {credentials.data?.filter((item) => item.adapter_id === platform).map((item) =>
            <option key={item.ref} value={item.ref}>{item.name}</option>)}
        </Select>
      </Field>
      {platform === 'hiagent' && <p className="text-xs text-muted-foreground">
        配置绑定 Trace 账号时会自动使用最新登录态；认证过期会重登。查询凭据的选择只影响未绑定账号的配置。
      </p>}
      <Link to="/settings" className="text-xs text-primary hover:underline">前往设置更新适配器与凭据</Link>
    </div>
  </Dialog>
}

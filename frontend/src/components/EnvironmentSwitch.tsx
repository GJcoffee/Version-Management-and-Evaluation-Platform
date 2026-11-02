import { useState } from 'react'
import {
  ENVIRONMENT_LABEL, useAdapterEnvironments, type AdapterEnvironmentName,
} from '@/api/queries/adapters'

/**
 * 发起对话 / 评测 / 重放 / 双栏对比时选择适配器环境，默认生产。
 * 只有测试已配置且与生产不同时才提供「生产 | 测试」选项；其余情况一律是生产。
 */
export function useEnvironmentChoice() {
  const envs = useAdapterEnvironments()
  const [picked, setPicked] = useState<AdapterEnvironmentName>('production')
  const selectable = envs.data?.test_selectable === true
  return {
    selectable,
    environment: selectable ? picked : ('production' as AdapterEnvironmentName),
    setEnvironment: setPicked,
  }
}

/** 请求体里只在选了测试时带上 environment，其余沿用服务端默认的生产 */
export const environmentBody = (environment: AdapterEnvironmentName) =>
  environment === 'test' ? { environment } : {}

export function EnvironmentSwitch({ selectable, environment, onChange, compact }: {
  selectable: boolean
  environment: AdapterEnvironmentName
  onChange: (environment: AdapterEnvironmentName) => void
  /** 工具栏里使用：标签与选项同一行，不显示说明 */
  compact?: boolean
}) {
  if (!selectable) return null
  return <div className={compact ? 'flex items-center gap-2' : 'space-y-1'} data-testid="environment-switch">
    <p className={compact ? 'text-xs text-muted-foreground' : 'text-sm font-medium'}>适配器环境</p>
    <div role="radiogroup" aria-label="适配器环境" className="inline-flex overflow-hidden rounded-md border border-input">
      {(['production', 'test'] as const).map((name) => <label key={name}
        className={`cursor-pointer px-3 py-1 text-sm ${environment === name ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}>
        <input type="radio" name="adapter-environment" className="sr-only" checked={environment === name}
          onChange={() => onChange(name)} />
        {ENVIRONMENT_LABEL[name]}
      </label>)}
    </div>
    {!compact && <p className="text-xs text-muted-foreground">默认使用生产；只有验证适配器改动时才选测试。</p>}
  </div>
}

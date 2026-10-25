import { useState } from 'react'
import { CheckCircle2, Plus, Trash2, XCircle } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useCreateProvider, useTestProvider, useUpdateProvider } from '@/api/queries/providers'
import type { ProviderOut, ProviderTestResult } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { KeyValueEditor } from '@/components/ui/KeyValueEditor'
import { formatDuration } from '@/lib/format'
import { toast } from '@/stores/toast'
import {
  appendModelNames,
  emptyModelRow,
  toCreateBody,
  toFormState,
  toUpdateBody,
  validateForm,
  type FormErrors,
  type ProviderFormState,
} from './providerForm'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 传入则为编辑，否则为新建 */
  provider?: ProviderOut
}

/** 外层负责在每次打开时重置表单（通过 key 重新挂载内层） */
export function ProviderDialog(props: Props) {
  if (!props.open) return null
  return <ProviderDialogInner key={props.provider?.id ?? 'new'} {...props} />
}

function ProviderDialogInner({ open, onOpenChange, provider }: Props) {
  const isEdit = !!provider
  const [form, setForm] = useState<ProviderFormState>(() => toFormState(provider))
  const [errors, setErrors] = useState<FormErrors>({})
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)
  const create = useCreateProvider()
  const update = useUpdateProvider()
  const test = useTestProvider()

  const patch = (p: Partial<ProviderFormState>) => setForm((f) => ({ ...f, ...p }))
  const saving = create.isPending || update.isPending

  const handleSubmit = async () => {
    const errs = validateForm(form)
    setErrors(errs)
    if (Object.keys(errs).length > 0) return
    try {
      if (provider) {
        const body = toUpdateBody(form, provider)
        if (Object.keys(body).length > 0) await update.mutateAsync({ id: provider.id, body })
        toast.success('Provider 已保存')
      } else {
        await create.mutateAsync(toCreateBody(form))
        toast.success('Provider 已创建')
      }
      onOpenChange(false)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '保存失败'
      // 后端字段级错误（VALIDATION_ERROR 的 details.errors）以 toast 展示，避免猜测字段路径
      toast.error('保存失败', { description: msg })
    }
  }

  const handleTest = async () => {
    if (!provider) return
    setTestResult(null)
    try {
      setTestResult(await test.mutateAsync(provider.id))
    } catch (e) {
      setTestResult({ ok: false, latency_ms: 0, error: e instanceof ApiError ? e.message : '测试请求失败' })
    }
  }

  const existing = new Set(form.models.map((m) => m.name.trim()))
  const newModels = (testResult?.models ?? []).filter((n) => !existing.has(n))

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !saving && onOpenChange(v)}
      size="lg"
      title={isEdit ? `编辑 Provider：${provider.name}` : '新建 Provider'}
      description="OpenAI 兼容的模型服务。密钥由服务端加密保存，不会回传到浏览器。"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
          <Button onClick={handleSubmit} loading={saving}>
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="名称" required error={errors.name} hint="在 Agent Spec 中通过名称引用" htmlFor="pv-name">
            <Input id="pv-name" mono value={form.name} invalid={!!errors.name} onChange={(e) => patch({ name: e.target.value })} placeholder="deepseek" />
          </Field>
          <Field label="Base URL" required error={errors.base_url} htmlFor="pv-url">
            <Input id="pv-url" mono value={form.base_url} invalid={!!errors.base_url} onChange={(e) => patch({ base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
          </Field>
        </div>

        <Field label="API Key" htmlFor="pv-key">
          {isEdit && provider.has_api_key && !form.clearKey && form.api_key === '' ? (
            <div className="flex items-center gap-2">
              <Badge variant="success">已设置</Badge>
              <span className="text-xs text-muted-foreground">如需更换，请在下方输入新密钥</span>
              <Button variant="outline" size="sm" className="ml-auto" onClick={() => patch({ clearKey: true })}>
                清除密钥
              </Button>
            </div>
          ) : isEdit && form.clearKey ? (
            <div className="flex items-center gap-2">
              <Badge variant="warning">保存后将删除密钥</Badge>
              <Button variant="ghost" size="sm" onClick={() => patch({ clearKey: false })}>
                撤销
              </Button>
            </div>
          ) : null}
          {!(isEdit && form.clearKey) && (
            <Input
              id="pv-key"
              type="password"
              mono
              autoComplete="new-password"
              value={form.api_key}
              onChange={(e) => patch({ api_key: e.target.value })}
              placeholder={isEdit && provider.has_api_key ? '输入新密钥以替换（留空则保持不变）' : 'sk-…（本地服务可留空）'}
              className={isEdit && provider.has_api_key && form.api_key === '' ? 'mt-1.5' : ''}
            />
          )}
        </Field>

        <Field label="额外请求头" hint="每次请求都会携带，例如组织标识">
          <KeyValueEditor value={form.headers} onChange={(headers) => patch({ headers })} keyPlaceholder="Header 名" valuePlaceholder="值" addLabel="添加请求头" />
        </Field>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="RPM 限制" error={errors.rpm_limit} hint="每分钟请求数，留空不限" htmlFor="pv-rpm">
            <Input id="pv-rpm" inputMode="numeric" value={form.rpm_limit} invalid={!!errors.rpm_limit} onChange={(e) => patch({ rpm_limit: e.target.value })} placeholder="不限" />
          </Field>
          <Field label="TPM 限制" error={errors.tpm_limit} hint="每分钟 token 数，留空不限" htmlFor="pv-tpm">
            <Input id="pv-tpm" inputMode="numeric" value={form.tpm_limit} invalid={!!errors.tpm_limit} onChange={(e) => patch({ tpm_limit: e.target.value })} placeholder="不限" />
          </Field>
          <Field label="最大并发" required error={errors.max_concurrency} htmlFor="pv-conc">
            <Input id="pv-conc" inputMode="numeric" value={form.max_concurrency} invalid={!!errors.max_concurrency} onChange={(e) => patch({ max_concurrency: e.target.value })} />
          </Field>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium">模型</span>
            <span className="text-xs text-muted-foreground">价格单位：元 / 百万 token</span>
          </div>
          {form.models.length > 0 && (
            <div className="space-y-1.5">
              <div className="grid grid-cols-[1fr_6rem_5.5rem_5.5rem_2rem] gap-1.5 text-xs text-muted-foreground">
                <span>模型名</span>
                <span>上下文窗口</span>
                <span>输入价格</span>
                <span>输出价格</span>
                <span />
              </div>
              {form.models.map((m, i) => (
                <div key={m.rid}>
                  <div className="grid grid-cols-[1fr_6rem_5.5rem_5.5rem_2rem] items-center gap-1.5">
                    <Input mono aria-label="模型名" value={m.name} invalid={!!errors[`models.${i}.name`]} placeholder="gpt-4o" onChange={(e) => patch({ models: form.models.map((x) => (x.rid === m.rid ? { ...x, name: e.target.value } : x)) })} />
                    <Input mono aria-label="上下文窗口" inputMode="numeric" value={m.context_window} invalid={!!errors[`models.${i}.context_window`]} placeholder="可选" onChange={(e) => patch({ models: form.models.map((x) => (x.rid === m.rid ? { ...x, context_window: e.target.value } : x)) })} />
                    <Input mono aria-label="输入价格" inputMode="decimal" value={m.price_in} invalid={!!errors[`models.${i}.price_in`]} placeholder="可选" onChange={(e) => patch({ models: form.models.map((x) => (x.rid === m.rid ? { ...x, price_in: e.target.value } : x)) })} />
                    <Input mono aria-label="输出价格" inputMode="decimal" value={m.price_out} invalid={!!errors[`models.${i}.price_out`]} placeholder="可选" onChange={(e) => patch({ models: form.models.map((x) => (x.rid === m.rid ? { ...x, price_out: e.target.value } : x)) })} />
                    <Button variant="ghost" size="icon" aria-label="删除模型" onClick={() => patch({ models: form.models.filter((x) => x.rid !== m.rid) })}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  {(errors[`models.${i}.name`] || errors[`models.${i}.context_window`] || errors[`models.${i}.price_in`] || errors[`models.${i}.price_out`]) && (
                    <p className="mt-0.5 text-xs text-danger">
                      {errors[`models.${i}.name`] ?? errors[`models.${i}.context_window`] ?? errors[`models.${i}.price_in`] ?? errors[`models.${i}.price_out`]}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          <Button variant="outline" size="sm" className="mt-1.5" onClick={() => patch({ models: [...form.models, emptyModelRow()] })}>
            <Plus className="h-3.5 w-3.5" />
            添加模型
          </Button>
        </div>

        <div className="rounded-md border border-border bg-muted/30 p-3">
          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" onClick={handleTest} loading={test.isPending} disabled={!isEdit}>
              测试连接
            </Button>
            <span className="text-xs text-muted-foreground">
              {isEdit ? '使用已保存的配置进行测试；修改后请先保存。' : '请先保存 Provider，再测试连接。'}
            </span>
          </div>
          {testResult && (
            <div className="mt-3 space-y-2" role="status">
              {testResult.ok ? (
                <p className="flex items-center gap-1.5 text-sm text-success">
                  <CheckCircle2 className="h-4 w-4" />
                  连接成功，耗时 <span className="font-mono">{formatDuration(testResult.latency_ms)}</span>
                  {testResult.models && <span className="text-muted-foreground">· 服务返回 {testResult.models.length} 个模型</span>}
                </p>
              ) : (
                <p className="flex items-start gap-1.5 text-sm text-danger">
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="break-all">连接失败：{testResult.error ?? '未知错误'}</span>
                </p>
              )}
              {testResult.ok && testResult.models && testResult.models.length > 0 && (
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      {newModels.length > 0 ? '点击模型名加入列表：' : '返回的模型都已在列表中'}
                    </span>
                    {newModels.length > 0 && (
                      <Button size="sm" variant="secondary" onClick={() => patch({ models: appendModelNames(form.models, newModels) })}>
                        全部添加（{newModels.length}）
                      </Button>
                    )}
                  </div>
                  <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
                    {newModels.map((n) => (
                      <button
                        key={n}
                        type="button"
                        onClick={() => patch({ models: appendModelNames(form.models, [n]) })}
                        className="inline-flex items-center gap-1 rounded border border-border bg-background px-1.5 py-0.5 font-mono text-xs hover:border-primary hover:text-primary"
                      >
                        <Plus className="h-3 w-3" />
                        {n}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Dialog>
  )
}

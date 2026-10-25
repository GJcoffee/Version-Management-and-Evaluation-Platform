import { useState } from 'react'
import { ApiError } from '@/api/client'
import { useSettings, useUpdateSettings } from '@/api/queries/settings'
import type { SettingKey, SettingsValues } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Field, Input } from '@/components/ui/Input'
import { LoadingBlock } from '@/components/ui/Spinner'
import { toast } from '@/stores/toast'

interface FieldDef {
  key: SettingKey
  label: string
  hint: string
  min: number
  max?: number
}

export const SCHEDULER_FIELDS: FieldDef[] = [
  { key: 'scheduler.workers', label: '全局并发（workers）', hint: '同时执行的任务数上限', min: 1, max: 512 },
  { key: 'scheduler.default_run_concurrency', label: '单个评测的默认并发', hint: '发起评测时未指定并发的默认值', min: 1, max: 512 },
  { key: 'scheduler.max_attempts', label: '最大重试次数', hint: '可重试错误（如 429）的最大尝试次数', min: 1, max: 20 },
  { key: 'retention.eval_sessions_days', label: '评测会话保留天数', hint: '0 表示永久保留；超期的评测会话由数据清理删除（报告汇总仍保留）', min: 0 },
]

/** 校验并返回发生变化的设置；有错误时返回 errors */
export function diffSettings(
  initial: SettingsValues,
  draft: Record<SettingKey, string>,
): { changed: Partial<SettingsValues>; errors: Partial<Record<SettingKey, string>> } {
  const changed: Partial<SettingsValues> = {}
  const errors: Partial<Record<SettingKey, string>> = {}
  for (const f of SCHEDULER_FIELDS) {
    const raw = draft[f.key].trim()
    const n = Number(raw)
    if (raw === '' || !Number.isInteger(n) || n < f.min || (f.max !== undefined && n > f.max)) {
      errors[f.key] = f.max !== undefined ? `请输入 ${f.min}–${f.max} 的整数` : `请输入不小于 ${f.min} 的整数`
    } else if (n !== initial[f.key]) {
      changed[f.key] = n
    }
  }
  return { changed, errors }
}

function SchedulerForm({ initial }: { initial: SettingsValues }) {
  const update = useUpdateSettings()
  const [draft, setDraft] = useState<Record<SettingKey, string>>(
    () => Object.fromEntries(SCHEDULER_FIELDS.map((f) => [f.key, String(initial[f.key])])) as Record<SettingKey, string>,
  )
  const [errors, setErrors] = useState<Partial<Record<SettingKey, string>>>({})
  const { changed } = diffSettings(initial, draft)
  const dirty = Object.keys(changed).length > 0

  const save = async () => {
    const r = diffSettings(initial, draft)
    setErrors(r.errors)
    if (Object.keys(r.errors).length > 0) return
    try {
      await update.mutateAsync(r.changed)
      toast.success('设置已保存')
    } catch (e) {
      toast.error('保存失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        {SCHEDULER_FIELDS.map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint} error={errors[f.key]} htmlFor={`st-${f.key}`}>
            <Input
              id={`st-${f.key}`}
              mono
              inputMode="numeric"
              value={draft[f.key]}
              invalid={!!errors[f.key]}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            />
          </Field>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <Button onClick={save} loading={update.isPending} disabled={!dirty}>
          保存
        </Button>
        {dirty && (
          <Button
            variant="ghost"
            onClick={() => {
              setErrors({})
              setDraft(Object.fromEntries(SCHEDULER_FIELDS.map((f) => [f.key, String(initial[f.key])])) as Record<SettingKey, string>)
            }}
          >
            还原
          </Button>
        )}
      </div>
    </>
  )
}

export function SchedulerSection() {
  const { data, isLoading, error } = useSettings()
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>调度与保留策略</CardTitle>
          <CardDescription>控制任务并发、重试与评测会话的保留期限。</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingBlock />
        ) : error || !data ? (
          <p className="py-4 text-sm text-danger">加载失败：{error instanceof ApiError ? error.message : '未知错误'}</p>
        ) : (
          // values 变化（保存成功后）时重新挂载，使表单与服务端保持一致
          <SchedulerForm key={JSON.stringify(data.values)} initial={data.values} />
        )}
      </CardContent>
    </Card>
  )
}

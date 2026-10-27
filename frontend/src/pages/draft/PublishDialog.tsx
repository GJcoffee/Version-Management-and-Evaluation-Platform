import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { Conventions, SpecIssue } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input, Textarea } from '@/components/ui/Input'

export interface PublishValues {
  notes: string
  tag: string
}

const TAG_RE = /^[\p{L}\p{N}_.-]{1,64}$/u

/**
 * 发布：备注 + 标签。发布失败的原因（含违反 agent 发布规范的说明）显示在对话框内。
 *
 * agent 开启规范时：`require_notes` 让备注必填；`tag_from_param` 让标签固定为 `v{params.<参数>}`（从草稿的 params 取值，
 * 只读）。服务端会再校验一次，这里只是提前提示。
 */
export function PublishDialog({
  open,
  onOpenChange,
  onPublish,
  conventions,
  paramValues,
  warnings,
  changeSummary,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onPublish: (v: PublishValues) => Promise<void>
  conventions?: Conventions
  /** 草稿 spec 的 params（用于算出 tag_from_param 要求的标签） */
  paramValues?: Record<string, unknown>
  warnings: SpecIssue[]
  changeSummary: string
}) {
  const tagParam = conventions?.tag_from_param ?? null
  const paramValue = tagParam ? paramValues?.[tagParam] : undefined
  const requiredTag = tagParam && paramValue != null && paramValue !== '' ? `v${String(paramValue)}` : null
  const [notes, setNotes] = useState('')
  const [tagInput, setTag] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; issues: SpecIssue[] } | null>(null)

  const notesRequired = !!conventions?.require_notes
  const tagTrim = tagParam ? (requiredTag ?? '') : tagInput.trim()
  const tagError = tagParam
    ? requiredTag
      ? null
      : `规范要求标签取自 params.${tagParam}，但草稿的 params 里没有这个参数`
    : tagTrim === ''
      ? null
      : !TAG_RE.test(tagTrim)
        ? '标签只能包含字母、数字、_ . -，最长 64'
        : tagTrim === 'latest' || /^v\d+$/.test(tagTrim)
          ? '标签不能是 latest 或形如 v12'
          : null
  const notesMissing = notesRequired && notes.trim() === ''

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await onPublish({ notes: notes.trim(), tag: tagTrim })
    } catch (e) {
      if (e instanceof ApiError) {
        const issues = (e.details as { errors?: SpecIssue[] } | null)?.errors ?? []
        setError({ message: e.message, issues })
      } else setError({ message: '发布失败', issues: [] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => !busy && onOpenChange(v)}
      title="发布新版本"
      description={changeSummary}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} loading={busy} disabled={!!tagError || notesMissing}>
            发布
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="备注"
          htmlFor="pub-notes"
          required={notesRequired}
          hint={notesRequired ? '该 agent 的发布规范要求必填：说明这个版本改了什么' : '说明这个版本改了什么（可选）'}
        >
          <Textarea id="pub-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="例如：调低温度，加入退货工具" />
        </Field>
        <Field
          label="标签"
          htmlFor="pub-tag"
          required={!!tagParam}
          hint={
            tagParam
              ? `该 agent 的发布规范要求标签等于 v{params.${tagParam}}，同一标签已存在时拒绝`
              : '可选；发布后把该标签指向新版本（标签已存在时会从旧版本移过来）'
          }
          error={tagError}
        >
          <Input
            id="pub-tag"
            mono
            value={tagParam ? tagTrim : tagInput}
            readOnly={!!tagParam}
            invalid={!!tagError}
            onChange={(e) => setTag(e.target.value)}
            placeholder="例如：prod"
          />
        </Field>
        {warnings.length > 0 && (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs">
            <p className="mb-1 flex items-center gap-1 font-medium text-warning">
              <AlertTriangle className="h-3.5 w-3.5" />
              {warnings.length} 条警告（不阻止发布）
            </p>
            <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
              {warnings.slice(0, 5).map((w, i) => (
                <li key={i}>
                  {w.path}：{w.message}
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger">
            <p className="whitespace-pre-line font-medium">{error.message}</p>
            {error.issues.length > 0 && (
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {error.issues.map((it, i) => (
                  <li key={i}>
                    {it.path}：{it.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

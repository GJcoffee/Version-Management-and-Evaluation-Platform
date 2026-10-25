import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { SpecIssue } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input, Textarea } from '@/components/ui/Input'

export interface PublishValues {
  notes: string
  tag: string
}

const TAG_RE = /^[\p{L}\p{N}_.-]{1,64}$/u

/** 发布：备注（可选）+ 标签（可选）。发布失败的原因显示在对话框内。 */
export function PublishDialog({
  open,
  onOpenChange,
  onPublish,
  warnings,
  changeSummary,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onPublish: (v: PublishValues) => Promise<void>
  warnings: SpecIssue[]
  changeSummary: string
}) {
  const [notes, setNotes] = useState('')
  const [tag, setTag] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; issues: SpecIssue[] } | null>(null)

  const tagTrim = tag.trim()
  const tagError =
    tagTrim === ''
      ? null
      : !TAG_RE.test(tagTrim)
        ? '标签只能包含字母、数字、_ . -，最长 64'
        : tagTrim === 'latest' || /^v\d+$/.test(tagTrim)
          ? '标签不能是 latest 或形如 v12'
          : null

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
          <Button onClick={submit} loading={busy} disabled={!!tagError}>
            发布
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="备注" htmlFor="pub-notes" hint="说明这个版本改了什么（可选）">
          <Textarea id="pub-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="例如：调低温度，加入退货工具" />
        </Field>
        <Field label="标签" htmlFor="pub-tag" hint="可选；发布后把该标签指向新版本（标签已存在时会从旧版本移过来）" error={tagError}>
          <Input id="pub-tag" mono value={tag} invalid={!!tagError} onChange={(e) => setTag(e.target.value)} placeholder="例如：prod" />
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
            <p className="font-medium">{error.message}</p>
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

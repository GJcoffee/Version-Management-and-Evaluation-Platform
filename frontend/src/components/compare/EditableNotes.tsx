import { useEffect, useRef, useState } from 'react'
import { Check, NotebookPen, X } from 'lucide-react'
import { COMPARE_NOTES_MAX } from '@/api/compareTypes'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Input'

/**
 * 对比备注的行内编辑：点“添加备注 / 编辑”进入编辑，Ctrl+Enter / 对勾保存，Esc / 叉取消。
 * 空白即清空备注；`onSave` 抛错时保持编辑状态（错误提示由调用方负责）。
 */
export function EditableNotes({
  value,
  onSave,
  disabled,
}: {
  value: string | null
  onSave: (notes: string) => Promise<unknown>
  disabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (editing) ref.current?.focus()
  }, [editing])

  const start = () => {
    setDraft(value ?? '')
    setError(null)
    setEditing(true)
  }
  const cancel = () => {
    setEditing(false)
    setError(null)
  }
  const save = async () => {
    const next = draft.trim()
    if (next.length > COMPARE_NOTES_MAX) {
      setError(`备注最长 ${COMPARE_NOTES_MAX} 个字符`)
      return
    }
    if (next === (value ?? '').trim()) {
      cancel()
      return
    }
    setSaving(true)
    try {
      await onSave(next)
      setEditing(false)
    } catch {
      // 错误提示由调用方负责；保持编辑状态方便重试
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <div className="flex min-w-0 items-start gap-1.5 text-xs" data-testid="compare-notes">
        {value ? (
          <p className="min-w-0 whitespace-pre-wrap break-words text-muted-foreground" data-testid="compare-notes-text">
            {value}
          </p>
        ) : null}
        {!disabled && (
          <Button variant="ghost" size="sm" className="h-6 shrink-0 px-1.5 text-xs" onClick={start} aria-label={value ? '编辑备注' : '添加备注'}>
            <NotebookPen className="h-3 w-3" />
            {value ? '编辑' : '添加备注'}
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className="flex items-start gap-1" data-testid="compare-notes">
      <Textarea
        ref={ref}
        rows={2}
        value={draft}
        invalid={!!error}
        aria-label="对比备注"
        data-testid="compare-notes-input"
        className="max-w-xl text-xs"
        placeholder="写点备注，例如这次对比想验证什么"
        disabled={saving}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(null)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void save()
          else if (e.key === 'Escape') cancel()
        }}
      />
      <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="保存备注" loading={saving} onClick={() => void save()}>
        <Check className="h-3 w-3" />
      </Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="取消编辑备注" disabled={saving} onClick={cancel}>
        <X className="h-3 w-3" />
      </Button>
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </div>
  )
}

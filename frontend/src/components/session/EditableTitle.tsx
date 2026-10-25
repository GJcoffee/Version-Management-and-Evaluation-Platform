import { useEffect, useRef, useState } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'

/** 与后端 `SESSION_TITLE_MAX` 一致 */
export const SESSION_TITLE_MAX = 120

/**
 * 行内编辑的标题：点铅笔进入编辑，Enter / 对勾保存，Esc / 叉取消。
 * 只做前端的基本校验（去空白后非空、不超长），其余由 `onSave` 抛出的错误决定（出错时保持编辑状态，错误由调用方提示）。
 */
export function EditableTitle({
  value,
  onSave,
  disabled,
}: {
  value: string
  onSave: (title: string) => Promise<unknown>
  disabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const start = () => {
    setDraft(value)
    setError(null)
    setEditing(true)
  }
  const cancel = () => {
    setEditing(false)
    setError(null)
  }
  const save = async () => {
    const next = draft.trim()
    if (!next) {
      setError('标题不能为空')
      return
    }
    if (next.length > SESSION_TITLE_MAX) {
      setError(`标题最长 ${SESSION_TITLE_MAX} 个字符`)
      return
    }
    if (next === value) {
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
      <div className="flex min-w-0 items-center gap-1">
        <h1 className="truncate text-sm font-semibold" data-testid="session-title">
          {value}
        </h1>
        {!disabled && (
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" aria-label="编辑标题" onClick={start}>
            <Pencil className="h-3 w-3" />
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className="flex min-w-0 items-center gap-1">
      <Input
        ref={inputRef}
        value={draft}
        invalid={!!error}
        aria-label="会话标题"
        data-testid="session-title-input"
        className="h-7 w-64"
        disabled={saving}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(null)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void save()
          else if (e.key === 'Escape') cancel()
        }}
      />
      <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="保存标题" loading={saving} onClick={() => void save()}>
        <Check className="h-3 w-3" />
      </Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" aria-label="取消编辑" disabled={saving} onClick={cancel}>
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

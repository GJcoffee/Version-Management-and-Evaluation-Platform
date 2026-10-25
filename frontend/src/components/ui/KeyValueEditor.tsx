import { useEffect, useRef, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from './Button'
import { Input } from './Input'

interface Row {
  rid: number
  key: string
  value: string
}

export interface KeyValueEditorProps {
  value: Record<string, string>
  onChange: (value: Record<string, string>) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
  addLabel?: string
  /** 值以密码框显示（如 Authorization 头） */
  maskValues?: boolean
  disabled?: boolean
}

function toRecord(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const r of rows) {
    const k = r.key.trim()
    if (k) out[k] = r.value
  }
  return out
}

/**
 * 键值对编辑器。内部以数组保存行，允许编辑过程中出现空键/重复键；
 * 只有非空键才会写入 onChange 的 Record（后者覆盖前者）。
 */
export function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = '键',
  valuePlaceholder = '值',
  addLabel = '添加',
  maskValues,
  disabled,
}: KeyValueEditorProps) {
  const ridRef = useRef(0)
  const fromRecord = (rec: Record<string, string>): Row[] =>
    Object.entries(rec).map(([key, v]) => ({ rid: ++ridRef.current, key, value: v }))
  const [rows, setRows] = useState<Row[]>(() => fromRecord(value))

  // 外部值变化（例如重置表单）时同步；自身触发的变化内容相同，不会覆盖正在编辑的空行
  useEffect(() => {
    if (JSON.stringify(toRecord(rows)) !== JSON.stringify(value)) setRows(fromRecord(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const update = (next: Row[]) => {
    setRows(next)
    onChange(toRecord(next))
  }

  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.rid} className="flex items-center gap-1.5">
          <Input
            mono
            className="w-2/5"
            placeholder={keyPlaceholder}
            value={r.key}
            disabled={disabled}
            aria-label="键"
            onChange={(e) => update(rows.map((x) => (x.rid === r.rid ? { ...x, key: e.target.value } : x)))}
          />
          <Input
            mono
            className="flex-1"
            type={maskValues ? 'password' : 'text'}
            placeholder={valuePlaceholder}
            value={r.value}
            disabled={disabled}
            aria-label="值"
            onChange={(e) => update(rows.map((x) => (x.rid === r.rid ? { ...x, value: e.target.value } : x)))}
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label="删除此行"
            disabled={disabled}
            onClick={() => update(rows.filter((x) => x.rid !== r.rid))}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => setRows([...rows, { rid: ++ridRef.current, key: '', value: '' }])}
      >
        <Plus className="h-3.5 w-3.5" />
        {addLabel}
      </Button>
    </div>
  )
}

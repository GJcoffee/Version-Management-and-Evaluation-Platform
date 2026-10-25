import { useEffect, useId, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import type { JsonSchemaNode } from '@/api/types'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { constraintHint } from '@/lib/specSchema'
import { cn } from '@/lib/cn'

/** 可折叠分组 */
export function Section({
  title,
  description,
  defaultOpen = true,
  children,
}: {
  title: string
  description?: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="rounded-lg border border-border bg-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span>
          <span className="text-sm font-semibold">{title}</span>
          {description && <span className="ml-2 text-xs text-muted-foreground">{description}</span>}
        </span>
        <ChevronDown className={cn('h-4 w-4 text-muted-foreground transition-transform', !open && '-rotate-90')} />
      </button>
      {open && <div className="space-y-3 border-t border-border px-3 py-3">{children}</div>}
    </section>
  )
}

/**
 * 数字输入：保留本地输入文本（避免 "0." 之类的中间态被回写覆盖），
 * 能解析为数字时才提交；清空提交 undefined（删除该键，取默认值）。
 */
export function NumberField({
  label,
  value,
  onChange,
  schema,
  integer,
  disabled,
  placeholder,
}: {
  label: string
  value: unknown
  onChange: (v: number | undefined) => void
  schema?: JsonSchemaNode
  integer?: boolean
  disabled?: boolean
  placeholder?: string
}) {
  const id = useId()
  const external = typeof value === 'number' ? String(value) : ''
  const [text, setText] = useState(external)
  useEffect(() => {
    // 外部值变化（YAML 侧修改）时同步；本地输入的中间态解析结果相同则不覆盖
    setText((cur) => {
      const n = cur.trim() === '' ? undefined : Number(cur)
      return n === (typeof value === 'number' ? value : undefined) ? cur : external
    })
  }, [value, external])
  const isInt = integer ?? schema?.type === 'integer'
  const num = text.trim() === '' ? undefined : Number(text)
  const range =
    num !== undefined &&
    (Number.isNaN(num) ||
      (schema?.minimum !== undefined && num < schema.minimum) ||
      (schema?.maximum !== undefined && num > schema.maximum) ||
      (schema?.exclusiveMinimum !== undefined && num <= schema.exclusiveMinimum) ||
      (isInt && !Number.isInteger(num)))
  return (
    <Field label={label} hint={constraintHint(schema)} htmlFor={id} error={range ? '数值不在允许范围内' : null}>
      <Input
        id={id}
        inputMode="decimal"
        value={text}
        disabled={disabled}
        invalid={range}
        placeholder={placeholder ?? (schema?.default !== undefined && schema.default !== null ? String(schema.default) : undefined)}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          if (t.trim() === '') onChange(undefined)
          else if (!Number.isNaN(Number(t))) onChange(Number(t))
        }}
      />
    </Field>
  )
}

export function CheckboxField({
  label,
  checked,
  onChange,
  hint,
  disabled,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
  hint?: string
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="flex cursor-pointer items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 rounded border-input accent-primary"
        />
        {label}
      </label>
      {hint && <p className="pl-6 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function TextField({
  label,
  value,
  onChange,
  hint,
  placeholder,
  mono,
  disabled,
  required,
}: {
  label: string
  value: unknown
  onChange: (v: string | undefined) => void
  hint?: string
  placeholder?: string
  mono?: boolean
  disabled?: boolean
  required?: boolean
}) {
  const id = useId()
  return (
    <Field label={label} hint={hint} htmlFor={id} required={required}>
      <Input
        id={id}
        mono={mono}
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
      />
    </Field>
  )
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
  disabled,
  required,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: Array<{ value: string; label?: string }>
  hint?: string
  disabled?: boolean
  required?: boolean
  placeholder?: string
}) {
  const id = useId()
  const hasValue = options.some((o) => o.value === value)
  return (
    <Field label={label} hint={hint} htmlFor={id} required={required}>
      <Select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {(!hasValue || value === '') && (
          <option value={value}>{value === '' ? (placeholder ?? '请选择') : `${value}（未登记）`}</option>
        )}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label ?? o.value}
          </option>
        ))}
      </Select>
    </Field>
  )
}

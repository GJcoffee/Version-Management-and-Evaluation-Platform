import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Search, X } from 'lucide-react'
import { useAssetRevisions, useAssets } from '@/api/queries/assets'
import type { Asset, AssetKind } from '@/api/types'
import { Select } from '@/components/ui/Select'
import { Spinner } from '@/components/ui/Spinner'
import { ASSET_KIND_LABEL, formatAssetRef, parseAssetRef, type ParsedAssetRef } from '@/lib/assetRef'
import { cn } from '@/lib/cn'
import { useDebouncedValue } from '@/lib/useDebouncedValue'

export interface AssetRefPickerProps {
  kind: AssetKind
  /** `asset://{kind}/{name}@{latest|rev}`；null 表示未选择 */
  value: string | null
  onChange: (ref: string | null) => void
  placeholder?: string
  /** 已选中时是否显示清除按钮 */
  clearable?: boolean
  disabled?: boolean
  className?: string
}

/** 在按钮下方弹出的浮层（Portal + fixed 定位，避免被滚动容器裁剪） */
function Popover({
  anchor,
  onClose,
  children,
}: {
  anchor: HTMLElement
  onClose: () => void
  children: React.ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number; maxH: number } | null>(null)

  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect()
    const spaceBelow = window.innerHeight - r.bottom - 12
    const spaceAbove = r.top - 12
    const below = spaceBelow >= 220 || spaceBelow >= spaceAbove
    const maxH = Math.max(160, Math.min(360, below ? spaceBelow : spaceAbove))
    setPos({
      top: below ? r.bottom + 4 : Math.max(8, r.top - 4 - maxH),
      left: Math.min(r.left, Math.max(8, window.innerWidth - 336)),
      maxH,
    })
  }, [anchor])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || anchor.contains(t)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [anchor, onClose])

  if (!pos) return null
  return createPortal(
    <div
      ref={ref}
      style={{ top: pos.top, left: pos.left, maxHeight: pos.maxH }}
      className="fixed z-[70] flex w-80 flex-col overflow-hidden rounded-md border border-border bg-card shadow-lg"
    >
      {children}
    </div>,
    document.body,
  )
}

/** 修订选择：`@latest` 或具体修订 */
function RevisionSelect({
  asset,
  parsed,
  onChange,
  disabled,
}: {
  asset: Asset | undefined
  parsed: ParsedAssetRef
  onChange: (rev: 'latest' | number) => void
  disabled?: boolean
}) {
  const revs = useAssetRevisions(asset?.id, 200)
  const items = revs.data?.pages.flatMap((p) => p.items) ?? []
  const current = String(parsed.rev)
  const known = parsed.rev === 'latest' || items.some((r) => r.rev === parsed.rev)
  return (
    <Select
      aria-label="修订"
      value={current}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value === 'latest' ? 'latest' : Number(e.target.value))}
      className="w-28 shrink-0"
    >
      <option value="latest">@latest</option>
      {!known && <option value={current}>@{current}</option>}
      {items.map((r) => (
        <option key={r.id} value={r.rev} title={r.message ?? undefined}>
          @{r.rev}
          {r.id === asset?.latest?.id ? '（当前）' : ''}
        </option>
      ))}
    </Select>
  )
}

/**
 * 资产引用选择器：按 kind 过滤、可搜索，选中后可切换 `@latest` 或具体修订。
 * 值是草稿里的字符串引用；固化形式（`$asset`）请由调用方先转换。
 */
export function AssetRefPicker({
  kind,
  value,
  onChange,
  placeholder,
  clearable = false,
  disabled,
  className,
}: AssetRefPickerProps) {
  const parsed = useMemo(() => parseAssetRef(value), [value])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const q = useDebouncedValue(query, 250)
  const btnRef = useRef<HTMLButtonElement>(null)
  const list = useAssets({ kind, q: open ? q : parsed?.name, limit: 50 }, open || !!parsed)
  const assets = list.data?.pages.flatMap((p) => p.items) ?? []
  const current = parsed ? assets.find((a) => a.name === parsed.name && a.kind === parsed.kind) : undefined

  const pick = (a: Asset) => {
    onChange(formatAssetRef({ kind, name: a.name, rev: 'latest' }))
    setOpen(false)
    setQuery('')
  }

  return (
    <div className={cn('flex min-w-0 items-center gap-1.5', className)}>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex h-8 min-w-0 flex-1 items-center justify-between gap-2 rounded-md border border-input bg-background px-2.5 text-left text-sm',
          'hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50',
        )}
      >
        {parsed ? (
          <span className="truncate font-mono text-xs" title={value ?? undefined}>
            {parsed.name}
          </span>
        ) : (
          <span className="truncate text-muted-foreground">{placeholder ?? `选择${ASSET_KIND_LABEL[kind]}…`}</span>
        )}
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>
      {parsed && (
        <RevisionSelect
          asset={current}
          parsed={parsed}
          disabled={disabled}
          onChange={(rev) => onChange(formatAssetRef({ ...parsed, rev }))}
        />
      )}
      {parsed && clearable && (
        <button
          type="button"
          aria-label="清除"
          disabled={disabled}
          onClick={() => onChange(null)}
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      )}
      {open && btnRef.current && (
        <Popover anchor={btnRef.current} onClose={() => setOpen(false)}>
          <div className="flex items-center gap-2 border-b border-border px-2.5 py-2">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`搜索${ASSET_KIND_LABEL[kind]}…`}
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            {list.isFetching && <Spinner />}
          </div>
          <ul role="listbox" className="min-h-0 flex-1 overflow-y-auto py-1">
            {assets.length === 0 && !list.isFetching && (
              <li className="px-3 py-4 text-center text-xs text-muted-foreground">
                没有{ASSET_KIND_LABEL[kind]}资产{q ? `匹配“${q}”` : ''}
              </li>
            )}
            {assets.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={a.name === parsed?.name}
                  onClick={() => pick(a)}
                  className={cn(
                    'flex w-full flex-col gap-0.5 px-3 py-1.5 text-left hover:bg-accent',
                    a.name === parsed?.name && 'bg-accent',
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-xs font-medium">{a.name}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground">最新 r{a.latest_rev}</span>
                  </span>
                  {a.description && <span className="truncate text-xs text-muted-foreground">{a.description}</span>}
                </button>
              </li>
            ))}
          </ul>
        </Popover>
      )}
    </div>
  )
}

/** 多选：一组引用，可逐项改修订 / 删除，底部添加 */
export function AssetRefListEditor({
  kind,
  value,
  onChange,
  disabled,
  addLabel,
}: {
  kind: AssetKind
  value: string[]
  onChange: (refs: string[]) => void
  disabled?: boolean
  addLabel?: string
}) {
  return (
    <div className="space-y-1.5">
      {value.map((ref, i) => (
        <AssetRefPicker
          key={`${i}-${parseAssetRef(ref)?.name ?? ref}`}
          kind={kind}
          value={ref}
          disabled={disabled}
          clearable
          onChange={(next) => onChange(next === null ? value.filter((_, j) => j !== i) : value.map((r, j) => (j === i ? next : r)))}
        />
      ))}
      <AssetRefPicker
        kind={kind}
        value={null}
        disabled={disabled}
        placeholder={addLabel ?? `+ 添加${ASSET_KIND_LABEL[kind]}`}
        onChange={(next) => next && onChange([...value, next])}
      />
    </div>
  )
}

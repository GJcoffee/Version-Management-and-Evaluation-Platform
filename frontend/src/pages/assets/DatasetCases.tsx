import { useEffect, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Search } from 'lucide-react'
import { useDatasetCases } from '@/api/queries/datasets'
import type { EvalCase } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Dialog } from '@/components/ui/Dialog'
import { Input } from '@/components/ui/Input'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useDebouncedValue } from '@/lib/useDebouncedValue'

const COLS = 'grid-cols-[3.5rem_11rem_minmax(0,1.4fr)_minmax(0,1fr)_10rem]'
const ROW_H = 44

/** 数据集用例表：虚拟滚动 + 游标分页（滚到接近底部自动加载下一页） */
export function DatasetCases({ revId, total }: { revId: string; total?: number }) {
  const [q, setQ] = useState('')
  const [tag, setTag] = useState('')
  const dq = useDebouncedValue(q.trim(), 300)
  const dtag = useDebouncedValue(tag.trim(), 300)
  const cases = useDatasetCases(revId, { q: dq || undefined, tag: dtag ? [dtag] : undefined })
  const rows: EvalCase[] = cases.data?.pages.flatMap((p) => p.items) ?? []
  const parentRef = useRef<HTMLDivElement>(null)
  const [detail, setDetail] = useState<EvalCase | null>(null)

  const virt = useVirtualizer({
    count: rows.length + (cases.hasNextPage ? 1 : 0),
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_H,
    overscan: 8,
  })
  const items = virt.getVirtualItems()
  const lastIndex = items.length ? items[items.length - 1].index : -1
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = cases
  useEffect(() => {
    if (lastIndex >= rows.length - 5 && hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [lastIndex, rows.length, hasNextPage, isFetchingNextPage, fetchNextPage])

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="搜索输入 / 期望…" aria-label="搜索用例" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Input className="w-40" placeholder="按标签筛选" aria-label="标签筛选" value={tag} onChange={(e) => setTag(e.target.value)} />
        <span className="ml-auto text-xs text-muted-foreground" data-testid="case-total">
          {dq || dtag ? `匹配 ${rows.length}${hasNextPage ? '+' : ''} 个` : `共 ${total ?? rows.length} 个用例`}
          {rows.length > 0 && `，已加载 ${rows.length}`}
        </span>
      </div>

      <div className="overflow-hidden rounded-md border border-border">
        <div className={`grid ${COLS} gap-2 bg-muted/60 px-3 py-1.5 text-xs font-medium text-muted-foreground`}>
          <span>#</span>
          <span>case_key</span>
          <span>input</span>
          <span>expected</span>
          <span>tags</span>
        </div>
        {cases.isLoading ? (
          <LoadingBlock />
        ) : rows.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">没有用例</p>
        ) : (
          <div ref={parentRef} className="h-[28rem] overflow-y-auto" data-testid="case-scroller">
            <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
              {items.map((vi) => {
                const c = rows[vi.index]
                const style = { position: 'absolute' as const, top: 0, left: 0, width: '100%', height: ROW_H, transform: `translateY(${vi.start}px)` }
                if (!c) {
                  return (
                    <div key="loader" style={style} className="flex items-center justify-center text-xs text-muted-foreground">
                      加载中…
                    </div>
                  )
                }
                const turns = c.input?.turns ?? []
                return (
                  <button
                    type="button"
                    key={c.id}
                    style={style}
                    onClick={() => setDetail(c)}
                    className={`grid ${COLS} items-center gap-2 border-t border-border px-3 text-left text-sm hover:bg-muted/50`}
                  >
                    <span className="font-mono text-xs text-muted-foreground">{c.idx + 1}</span>
                    <span className="truncate font-mono text-xs" title={c.case_key}>
                      {c.case_key}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5">
                      {turns.length > 1 && <Badge variant="outline">{turns.length} 轮</Badge>}
                      <span className="truncate" title={turns.join('\n')}>
                        {turns.join(' ⏎ ')}
                      </span>
                    </span>
                    <span className="truncate text-muted-foreground" title={c.expected ?? undefined}>
                      {c.expected ?? '-'}
                    </span>
                    <span className="flex gap-1 overflow-hidden">
                      {c.tags.map((t) => (
                        <Badge key={t} variant="secondary" className="font-mono">
                          {t}
                        </Badge>
                      ))}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </div>

      <Dialog open={!!detail} onOpenChange={(v) => !v && setDetail(null)} size="lg" title={`用例 ${detail?.case_key ?? ''}`}>
        {detail && (
          <dl className="space-y-3 text-sm">
            <Item label="input（多轮按顺序）">
              <ol className="list-inside list-decimal space-y-1">
                {detail.input.turns.map((t, i) => (
                  <li key={i} className="whitespace-pre-wrap">
                    {t}
                  </li>
                ))}
              </ol>
            </Item>
            <Item label="expected">
              <p className="whitespace-pre-wrap">{detail.expected ?? '-'}</p>
            </Item>
            <Item label="rubric">
              <p className="whitespace-pre-wrap">{detail.rubric ?? '-'}</p>
            </Item>
            <Item label="tags">{detail.tags.length ? detail.tags.join(', ') : '-'}</Item>
            <Item label="metadata">
              <pre className="max-h-60 overflow-auto rounded bg-muted p-2 font-mono text-xs">{JSON.stringify(detail.metadata, null, 2)}</pre>
            </Item>
          </dl>
        )}
      </Dialog>
    </div>
  )
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="mb-0.5 text-xs font-medium text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

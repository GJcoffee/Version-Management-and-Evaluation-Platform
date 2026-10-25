import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  FileText,
  Loader2,
  MessageSquareText,
  ScrollText,
  Search,
  Wrench,
  X,
  Waypoints,
} from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { cn } from '@/lib/cn'
import { formatDuration } from '@/lib/format'
import { useNow } from '@/lib/useNow'
import { BlobJson } from './BlobView'
import { isHighlighted } from './sessionState'
import {
  TRACE_KINDS,
  TRACE_KIND_LABEL,
  computeTimeline,
  flattenTree,
  timelineTicks,
  walkTree,
  type TraceKind,
  type TraceNode,
} from './traceTree'

export interface TraceViewerProps {
  /** 步骤树（TurnState.steps） */
  steps: readonly TraceNode[]
  /** 步骤所属轮次（用于 highlightSteps 里带轮次的写法） */
  turnId?: string
  /** 用于读取转存为 blob 的大 payload */
  sessionId?: string
  /** 轮次起止时间（ISO）；缺省时按步骤自动计算。运行中 endedAt 为空，瀑布图右端随时间延伸 */
  startedAt?: string | null
  endedAt?: string | null
  /** 轮次仍在运行：瀑布图持续刷新 */
  running?: boolean
  /** 受控的选中步骤（节点 id，即 step_id）；不传则内部管理 */
  selectedStepId?: string | null
  onSelectStep?: (stepId: string | null) => void
  /** 高亮的步骤（双栏对比时标出差异），写法同 ChatPane.highlightSteps */
  highlightSteps?: ReadonlySet<string>
  className?: string
}

const KIND_ICON: Record<TraceKind, typeof Bot> = {
  llm: Bot,
  tool: Wrench,
  span: Waypoints,
  log: ScrollText,
  error: AlertCircle,
  final: MessageSquareText,
}
const KIND_BAR: Record<TraceKind, string> = {
  llm: 'bg-primary',
  tool: 'bg-warning',
  span: 'bg-muted-foreground/60',
  log: 'bg-muted-foreground/40',
  error: 'bg-danger',
  final: 'bg-success',
}

/** 按 step_id / parent_step_id 组织的步骤树 + 右侧时间瀑布图 + 点击查看详情；可按类型筛选、按文本搜索 */
export function TraceViewer({
  steps,
  turnId,
  sessionId,
  startedAt,
  endedAt,
  running,
  selectedStepId,
  onSelectStep,
  highlightSteps,
  className,
}: TraceViewerProps) {
  const [innerSel, setInnerSel] = useState<string | null>(null)
  const selected = selectedStepId !== undefined ? selectedStepId : innerSel
  const select = (id: string | null) => {
    setInnerSel(id)
    onSelectStep?.(id)
  }
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [kinds, setKinds] = useState<ReadonlySet<TraceKind>>(new Set())
  const [query, setQuery] = useState('')

  const all = useMemo(() => {
    const list: TraceNode[] = []
    walkTree(steps, (n) => list.push(n))
    return list
  }, [steps])
  const counts = useMemo(() => {
    const c: Partial<Record<TraceKind, number>> = {}
    for (const n of all) c[n.kind] = (c[n.kind] ?? 0) + 1
    return c
  }, [all])

  const rows = useMemo(
    () => flattenTree(steps, { collapsed, filter: { kinds, query } }),
    [steps, collapsed, kinds, query],
  )

  const now = useNow(!!running, 400)
  const originMs = startedAt ? Date.parse(startedAt) : undefined
  const endMs = endedAt ? Date.parse(endedAt) : undefined
  const timeline = useMemo(
    () =>
      computeTimeline(all, {
        originMs: Number.isNaN(originMs) ? undefined : originMs,
        endMs: endMs !== undefined && !Number.isNaN(endMs) ? endMs : undefined,
        nowMs: running ? now : undefined,
      }),
    [all, originMs, endMs, running, now],
  )
  const ticks = useMemo(() => timelineTicks(timeline.totalMs, 4), [timeline.totalMs])

  const selectedNode = selected ? (all.find((n) => n.id === selected) ?? null) : null

  // 选中的节点不存在（切换轮次）时清除
  useEffect(() => {
    if (selected && !all.some((n) => n.id === selected)) setInnerSel(null)
  }, [all, selected])

  const toggleKind = (k: TraceKind) =>
    setKinds((cur) => {
      const next = new Set(cur)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  const toggleCollapse = (id: string) =>
    setCollapsed((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (all.length === 0) {
    return (
      <div className={cn('flex min-h-0 flex-col', className)} data-testid="trace-viewer">
        <EmptyState icon={Waypoints} title="暂无 Trace" description={running ? '等待第一个步骤…' : '这一轮没有记录任何步骤。'} />
      </div>
    )
  }

  return (
    <div className={cn('flex min-h-0 flex-col', className)} data-testid="trace-viewer">
      {/* 工具栏：搜索 + 类型筛选 */}
      <div className="shrink-0 space-y-1.5 border-b border-border px-3 py-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索步骤名称或内容…"
            aria-label="搜索 Trace"
            className="h-7 w-full rounded-md border border-input bg-background pl-7 pr-2 text-xs outline-none focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="按类型筛选">
          {TRACE_KINDS.filter((k) => counts[k]).map((k) => {
            const on = kinds.has(k)
            return (
              <button
                key={k}
                type="button"
                aria-pressed={on}
                onClick={() => toggleKind(k)}
                data-testid={`filter-${k}`}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                  on ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:bg-accent',
                )}
              >
                {TRACE_KIND_LABEL[k]}
                <span className="font-mono">{counts[k]}</span>
              </button>
            )
          })}
          <span className="ml-auto font-mono text-[11px] text-muted-foreground">
            {all.length} 步 · {formatDuration(timeline.totalMs)}
          </span>
        </div>
      </div>

      {/* 树 + 瀑布图 */}
      <div className="min-h-0 flex-1 overflow-auto" data-testid="trace-rows">
        <div className="min-w-[420px]">
          <div className="sticky top-0 z-10 grid grid-cols-[minmax(0,1fr)_minmax(140px,42%)] border-b border-border bg-card text-[10px] text-muted-foreground">
            <div className="px-3 py-1">步骤</div>
            <div className="relative h-6 border-l border-border" aria-hidden="true">
              {ticks.map((t) => (
                <span
                  key={t}
                  className={cn(
                    'absolute top-1 font-mono',
                    t === 0 ? 'translate-x-0.5' : t / timeline.totalMs > 0.9 ? '-translate-x-full pr-0.5' : '-translate-x-1/2',
                  )}
                  style={{ left: `${Math.min((t / timeline.totalMs) * 100, 100)}%` }}
                >
                  {formatDuration(t)}
                </span>
              ))}
            </div>
          </div>
          {rows.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">没有匹配的步骤</p>
          ) : (
            rows.map(({ node, depth, hasChildren, expanded }) => {
              const bar = timeline.bars.get(node.id)
              const Icon = KIND_ICON[node.kind]
              const isSel = selected === node.id
              const hl = isHighlighted(highlightSteps, turnId ?? '', node.stepId)
              return (
                <div
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSel}
                  data-testid="trace-row"
                  data-step={node.stepId ?? undefined}
                  data-kind={node.kind}
                  data-status={node.status}
                  onClick={() => select(isSel ? null : node.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      select(isSel ? null : node.id)
                    }
                  }}
                  className={cn(
                    'grid h-7 cursor-pointer grid-cols-[minmax(0,1fr)_minmax(140px,42%)] items-center border-b border-border/50 text-xs hover:bg-accent/60',
                    isSel && 'bg-primary/10 hover:bg-primary/10',
                    hl && 'bg-warning/15',
                  )}
                >
                  <div className="flex min-w-0 items-center gap-1 pr-2" style={{ paddingLeft: 8 + depth * 14 }}>
                    {hasChildren ? (
                      <button
                        type="button"
                        aria-label={expanded ? '折叠' : '展开'}
                        aria-expanded={expanded}
                        className="rounded p-0.5 hover:bg-accent"
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleCollapse(node.id)
                        }}
                      >
                        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      </button>
                    ) : (
                      <span className="w-4" />
                    )}
                    <Icon
                      className={cn(
                        'h-3.5 w-3.5 shrink-0',
                        node.kind === 'error' || node.status === 'error' ? 'text-danger' : 'text-muted-foreground',
                      )}
                    />
                    <span className={cn('truncate font-mono', node.status === 'error' && 'text-danger')}>
                      {node.name || TRACE_KIND_LABEL[node.kind]}
                    </span>
                    {node.mocked && <Badge variant="warning">mock</Badge>}
                    <span className="ml-auto flex shrink-0 items-center gap-1 pl-1 font-mono text-[11px] text-muted-foreground">
                      {node.status === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
                      {node.status === 'incomplete' && <CircleSlash className="h-3 w-3 text-warning" aria-label="未结束" />}
                      {node.durationMs !== null && node.kind !== 'log' && node.kind !== 'error' && node.kind !== 'final'
                        ? formatDuration(node.durationMs)
                        : null}
                    </span>
                  </div>
                  <div className="relative h-full border-l border-border/60" aria-hidden="true">
                    {ticks.map((t) => (
                      <span
                        key={t}
                        className="absolute inset-y-0 w-px bg-border/40"
                        style={{ left: `${Math.min((t / timeline.totalMs) * 100, 100)}%` }}
                      />
                    ))}
                    {bar && (
                      <span
                        data-testid="waterfall-bar"
                        className={cn(
                          'absolute top-1/2 h-3 -translate-y-1/2 rounded-sm',
                          KIND_BAR[node.kind],
                          bar.open && 'animate-pulse',
                          node.status === 'incomplete' && 'opacity-50',
                          node.status === 'error' && node.kind === 'tool' && 'bg-danger',
                        )}
                        style={{ left: `${bar.left * 100}%`, width: `max(${bar.width * 100}%, 3px)` }}
                        title={`${node.name} · 起 +${formatDuration(bar.offsetMs)} · 历时 ${formatDuration(bar.spanMs)}`}
                      />
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* 详情 */}
      {selectedNode && (
        <StepDetail node={selectedNode} sessionId={sessionId} originMs={timeline.originMs} onClose={() => select(null)} />
      )}
    </div>
  )
}

const EVENT_LABEL: Record<string, string> = {
  'llm.request': '请求',
  'llm.response': '响应',
  'tool.call': '调用',
  'tool.result': '结果',
  'span.start': '开始',
  'span.end': '结束',
  log: '日志',
  error: '错误',
  'message.assistant': '消息',
}

/** 点击步骤后的详情：按事件分页（请求 / 响应 …），payload 用 JSON 树展示，转存为 blob 的大字段按需加载 */
export function StepDetail({
  node,
  sessionId,
  originMs,
  onClose,
}: {
  node: TraceNode
  sessionId?: string
  originMs?: number
  onClose?: () => void
}) {
  const [tab, setTab] = useState(0)
  const idx = Math.min(tab, node.events.length - 1)
  const ev = node.events[idx]
  const facts = stepFacts(node)
  return (
    <div className="flex max-h-[48%] min-h-[160px] shrink-0 flex-col border-t border-border bg-card" data-testid="step-detail">
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="truncate font-mono text-xs font-semibold">{node.name || TRACE_KIND_LABEL[node.kind]}</span>
        <Badge variant="outline">{TRACE_KIND_LABEL[node.kind]}</Badge>
        {node.status === 'error' && <Badge variant="danger">出错</Badge>}
        {node.status === 'running' && <Badge variant="info">进行中</Badge>}
        {node.status === 'incomplete' && <Badge variant="warning">未结束</Badge>}
        {node.stepId && <span className="font-mono text-[11px] text-muted-foreground">{node.stepId}</span>}
        <button
          type="button"
          aria-label="关闭详情"
          onClick={onClose}
          className="ml-auto rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {facts.length > 0 && (
          <dl className="mb-2 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px]">
            {originMs !== undefined && (
              <Fact label="开始于" value={`+${formatDuration(Math.max(node.startMs - originMs, 0))}`} />
            )}
            {facts.map(([k, v]) => (
              <Fact key={k} label={k} value={v} />
            ))}
          </dl>
        )}
        {node.events.length > 1 && (
          <div className="mb-2 flex gap-1" role="tablist">
            {node.events.map((e, i) => (
              <button
                key={i}
                type="button"
                role="tab"
                aria-selected={i === idx}
                onClick={() => setTab(i)}
                className={cn(
                  'rounded px-2 py-0.5 text-xs',
                  i === idx ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-accent',
                )}
              >
                {EVENT_LABEL[e.type] ?? e.type}
              </button>
            ))}
          </div>
        )}
        {ev && (
          <div data-testid="step-payload" data-event={ev.type}>
            <BlobJson key={`${node.id}:${idx}`} value={ev.payload} sessionId={sessionId} defaultExpandDepth={1} />
          </div>
        )}
      </div>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  )
}

/** 详情顶部的关键事实（耗时、tokens、TTFT、成本等） */
function stepFacts(n: TraceNode): Array<[string, string]> {
  const out: Array<[string, string]> = []
  if (n.durationMs !== null && n.kind !== 'log' && n.kind !== 'error' && n.kind !== 'final') {
    out.push(['耗时', formatDuration(n.durationMs)])
  }
  const resp = n.events.find((e) => e.type === 'llm.response')
  if (resp && resp.type === 'llm.response') {
    const p = resp.payload
    if (p.usage) out.push(['tokens', `↑${p.usage.prompt_tokens} ↓${p.usage.completion_tokens}`])
    if (p.ttft_ms !== null && p.ttft_ms !== undefined) out.push(['TTFT', formatDuration(p.ttft_ms)])
    if (p.cost !== null && p.cost !== undefined) out.push(['成本', `¥${p.cost.toFixed(4)}`])
    if (p.finish_reason) out.push(['finish', p.finish_reason])
  }
  if (n.kind === 'tool' && n.mocked) out.push(['来源', 'mock'])
  return out
}

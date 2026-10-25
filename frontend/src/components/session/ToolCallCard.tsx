import { useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Loader2, Wrench } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { JsonViewer } from '@/components/ui/JsonViewer'
import { cn } from '@/lib/cn'
import { formatDuration } from '@/lib/format'
import { BlobLoadBar, useLazyBlobs } from './BlobView'
import type { ToolCallStatus, ToolCallView } from './sessionState'

const STATUS: Record<ToolCallStatus, { text: string; variant: 'secondary' | 'info' | 'success' | 'danger' | 'warning' }> = {
  streaming: { text: '生成参数中', variant: 'info' },
  pending: { text: '等待执行', variant: 'secondary' },
  running: { text: '执行中', variant: 'info' },
  done: { text: '完成', variant: 'success' },
  error: { text: '出错', variant: 'danger' },
  interrupted: { text: '已中断', variant: 'warning' },
}

function StatusIcon({ status }: { status: ToolCallStatus }) {
  switch (status) {
    case 'streaming':
    case 'running':
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
    case 'done':
      return <CheckCircle2 className="h-3.5 w-3.5 text-success" />
    case 'error':
      return <AlertCircle className="h-3.5 w-3.5 text-danger" />
    case 'interrupted':
      return <CircleSlash className="h-3.5 w-3.5 text-warning" />
    default:
      return <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
  }
}

function argsPreview(a: ToolCallView['arguments']): string {
  if (a === undefined) return ''
  const s = typeof a === 'string' ? a : JSON.stringify(a)
  return s.length > 80 ? `${s.slice(0, 80)}…` : s
}

function ValueBlock({ value }: { value: unknown }) {
  if (typeof value === 'string') {
    return (
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 p-2 font-mono text-xs leading-5">
        {value}
      </pre>
    )
  }
  return <JsonViewer value={value} defaultExpandDepth={2} className="max-h-64" />
}

export interface ToolCallCardProps {
  call: ToolCallView
  /** 用于读取转存为 blob 的大参数 / 大结果 */
  sessionId?: string
  /** 外部高亮（双栏对比时标出差异） */
  highlighted?: boolean
  /** 当前在 Trace 里选中 */
  selected?: boolean
  defaultOpen?: boolean
  /** 点击卡片头部（展开 / 折叠之外）的联动，例如在 Trace 里定位该步骤 */
  onSelect?: (call: ToolCallView) => void
}

/** 工具调用卡片：工具名、参数 JSON、结果、耗时、是否 mock、是否出错；可折叠 */
export function ToolCallCard({ call, sessionId, highlighted, selected, defaultOpen = false, onSelect }: ToolCallCardProps) {
  const [open, setOpen] = useState(defaultOpen)
  const st = STATUS[call.status]
  const argBlobs = useLazyBlobs(call.argsBlob ? { $blob: call.argsBlob } : null, sessionId)
  const outBlobs = useLazyBlobs(call.outputBlob ? { $blob: call.outputBlob } : null, sessionId)
  const fullArgs = (argBlobs.resolved as { arguments?: ToolCallView['arguments'] } | null)?.arguments
  const fullOut = (outBlobs.resolved as { output?: unknown } | null)?.output
  const args = fullArgs ?? call.arguments
  const output = call.outputBlob ? fullOut : call.output
  const hasOutput = call.hasOutput || fullOut !== undefined

  return (
    <div
      data-testid="tool-card"
      data-tool={call.name}
      data-status={call.status}
      className={cn(
        'rounded-md border bg-card text-xs',
        call.isError || call.status === 'error' ? 'border-danger/50' : 'border-border',
        highlighted && 'ring-2 ring-warning/70 ring-offset-1 ring-offset-background',
        selected && 'border-primary',
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o)
          onSelect?.(call)
        }}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-accent/60"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <StatusIcon status={call.status} />
        <span className="font-mono font-semibold">{call.name || '(未命名)'}</span>
        {!open && args !== undefined && (
          <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">{argsPreview(args)}</span>
        )}
        {open && <span className="flex-1" />}
        <span className="flex shrink-0 items-center gap-1.5">
          {call.mocked && (
            <Badge variant="warning" title="结果来自 tool_mocks，没有真实执行">
              mock
            </Badge>
          )}
          <Badge variant={st.variant}>{st.text}</Badge>
          {call.latencyMs !== null && <span className="font-mono text-muted-foreground">{formatDuration(call.latencyMs)}</span>}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-2.5 py-2">
          <div>
            <div className="mb-1 font-medium text-muted-foreground">参数</div>
            {call.argsBlob && (
              <BlobLoadBar pending={argBlobs.pending} loading={argBlobs.loading} error={argBlobs.error} onLoad={argBlobs.load} />
            )}
            {args === undefined ? (
              <span className="text-muted-foreground">（尚无）</span>
            ) : (
              <ValueBlock value={args} />
            )}
          </div>
          <div>
            <div className="mb-1 font-medium text-muted-foreground">
              结果{call.isError && <span className="ml-1 text-danger">（出错）</span>}
            </div>
            {call.outputBlob && (
              <BlobLoadBar pending={outBlobs.pending} loading={outBlobs.loading} error={outBlobs.error} onLoad={outBlobs.load} />
            )}
            {hasOutput ? (
              <div className={cn(call.isError && 'rounded-md border border-danger/40')}>
                <ValueBlock value={output} />
              </div>
            ) : (
              <span className="text-muted-foreground">
                {call.status === 'interrupted' ? '（没有结果：执行被中断）' : '（等待结果…）'}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

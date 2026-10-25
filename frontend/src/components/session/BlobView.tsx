import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { DatabaseZap } from 'lucide-react'
import { fetchBlob, sessionKeys } from '@/api/queries/sessions'
import { Button } from '@/components/ui/Button'
import { JsonViewer } from '@/components/ui/JsonViewer'

/**
 * 大 payload 的按需加载。
 * 后端把超过 events.payload_inline_limit_kb 的事件 payload 转存为 blob：库里只剩 `{ $blob: hash, ...小字段 }`，
 * 完整内容用 `GET /blobs/{hash}` 读取。这里统一处理两种形态：
 *   1. 整个 payload 是存根（顶层带 `$blob`，另有若干小字段）：加载后 `{...小字段, ...完整内容}`；
 *   2. 值里嵌套着 `{ $blob: hash }`：加载后整体替换为 blob 内容。
 */

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

export function isBlobRef(v: unknown): v is { $blob: string } {
  return isPlainObject(v) && typeof v.$blob === 'string'
}

/** 收集值里出现的全部 blob 哈希（去重） */
export function collectBlobHashes(v: unknown, out: Set<string> = new Set()): string[] {
  if (isBlobRef(v)) out.add(v.$blob)
  if (Array.isArray(v)) v.forEach((x) => collectBlobHashes(x, out))
  else if (isPlainObject(v)) for (const [k, x] of Object.entries(v)) if (k !== '$blob') collectBlobHashes(x, out)
  return [...out]
}

/** 把值里的存根替换为已加载的 blob 内容（尚未加载的保持原样） */
export function resolveBlobs(v: unknown, loaded: ReadonlyMap<string, unknown>): unknown {
  if (Array.isArray(v)) return v.map((x) => resolveBlobs(x, loaded))
  if (!isPlainObject(v)) return v
  if (typeof v.$blob === 'string') {
    if (!loaded.has(v.$blob)) return v
    const full = resolveBlobs(loaded.get(v.$blob), loaded)
    const { $blob: _hash, ...rest } = v
    void _hash
    if (isPlainObject(full)) return { ...rest, ...full }
    return Object.keys(rest).length === 0 ? full : { ...rest, value: full }
  }
  const out: Record<string, unknown> = {}
  for (const [k, x] of Object.entries(v)) out[k] = resolveBlobs(x, loaded)
  return out
}

/** 点击后才请求的 blob 加载。返回合并后的值与加载状态 */
export function useLazyBlobs(value: unknown, sessionId?: string) {
  const hashes = collectBlobHashes(value)
  const [requested, setRequested] = useState(false)
  const results = useQueries({
    queries: hashes.map((h) => ({
      queryKey: sessionKeys.blob(h),
      queryFn: () => fetchBlob(h, sessionId),
      enabled: requested,
      staleTime: Infinity,
      gcTime: 10 * 60_000,
      retry: false,
    })),
  })
  const loaded = new Map<string, unknown>()
  hashes.forEach((h, i) => {
    if (results[i]?.data !== undefined) loaded.set(h, results[i].data)
  })
  const loading = requested && results.some((r) => r.isFetching)
  const error = results.find((r) => r.error)?.error as Error | undefined
  const resolved = loaded.size > 0 ? resolveBlobs(value, loaded) : value
  const pending = hashes.filter((h) => !loaded.has(h)).length
  return { hashes, pending, loading, error, resolved, load: () => setRequested(true) }
}

/** 加载按钮条：有未加载的 blob 时显示 */
export function BlobLoadBar({
  pending,
  loading,
  error,
  onLoad,
  label = '内容较大，已转存',
}: {
  pending: number
  loading: boolean
  error?: Error
  onLoad: () => void
  label?: string
}) {
  if (pending === 0 && !error) return null
  return (
    <div className="mb-1 flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/40 px-2 py-1 text-xs text-muted-foreground">
      <DatabaseZap className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">
        {error ? `加载失败：${error.message}` : `${label}（${pending} 处），按需加载`}
      </span>
      <Button size="sm" variant="outline" loading={loading} onClick={onLoad} data-testid="load-blob">
        {error ? '重试' : '加载完整内容'}
      </Button>
    </div>
  )
}

/** JSON 查看器 + 大字段按需加载 */
export function BlobJson({
  value,
  sessionId,
  defaultExpandDepth = 1,
  className,
}: {
  value: unknown
  sessionId?: string
  defaultExpandDepth?: number
  className?: string
}) {
  const b = useLazyBlobs(value, sessionId)
  return (
    <div>
      <BlobLoadBar pending={b.pending} loading={b.loading} error={b.error} onLoad={b.load} />
      <JsonViewer value={b.resolved} defaultExpandDepth={defaultExpandDepth} className={className} />
    </div>
  )
}

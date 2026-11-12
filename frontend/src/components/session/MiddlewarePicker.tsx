import { useArtifactCandidates, useSession } from '@/api/queries/sessions'
import type { MiddlewareSelection } from '@/api/types'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { useEffect, useState } from 'react'

function validPointer(value: string) {
  return (value === '' || value.startsWith('/')) && !/~(?![01])/.test(value)
}

export function MiddlewarePicker({ sessionId, selection, onChange, onReady, disabled }: {
  sessionId: string
  selection: MiddlewareSelection
  onChange: (value: MiddlewareSelection) => void
  disabled?: boolean
  onReady: (ready: boolean) => void
}) {
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false)
  const sourceQuery = useSession(sessionId)
  const sources = (sourceQuery.data?.turns ?? [])
    .filter((turn) => turn.status === 'ok' && turn.output?.final)
    .sort((a, b) => b.idx - a.idx)
  const source = sources.find((turn) => turn.id === selection.source_turn_id)
  const configuredUrlPointer = selection.config.url_pointer ?? ''
  const urlPointer = useDebouncedValue(configuredUrlPointer, 300)
  const urlPointerValid = validPointer(configuredUrlPointer)
  const pointerSettled = urlPointer === configuredUrlPointer
  const query = useArtifactCandidates(sessionId, selection.source_turn_id ?? undefined, urlPointer,
    urlPointerValid && pointerSettled)
  const items = query.data?.candidates ?? []
  const contentPointer = selection.config.content_pointer ?? ''
  const pointerValid = validPointer(contentPointer)
  const selected = items.find((item) => item.id === selection.config.selected_candidate_id) ??
    (!selection.config.selected_candidate_id && items.length === 1 ? items[0] : undefined)
  const ready = !!selection.source_turn_id && query.data?.source_turn_id === selection.source_turn_id &&
    !query.isFetching && !query.error && pointerValid && urlPointerValid && pointerSettled && !!selected
  const checking = query.isFetching || !pointerSettled
  useEffect(() => onReady(!!ready), [onReady, ready])
  const update = (config: MiddlewareSelection['config']) =>
    onChange({ ...selection, config: { ...selection.config, ...config } })
  return (
    <div className="space-y-2" data-testid="middleware-picker">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>附带上一轮产物</p>
        {selection.source_turn_id && <button type="button" disabled={disabled}
          className="rounded px-1 py-0.5 text-xs text-primary hover:bg-accent disabled:opacity-50"
          aria-expanded={sourcePickerOpen}
          onClick={() => setSourcePickerOpen((open) => !open)}>
          更换来源
        </button>}
      </div>
      <p className="text-xs text-muted-foreground">
        来源：{source ? `第 ${source.idx + 1} 轮` : selection.source_turn_id ? '已选定轮次' : '尚未选择'}
      </p>
      {(sourcePickerOpen || !selection.source_turn_id) && (
        sourceQuery.isPending ? <p role="status" className="text-xs text-muted-foreground">正在加载成功轮次…</p> :
          sourceQuery.error ? <div className="space-y-1 text-xs">
            <p role="alert" className="text-danger">无法加载来源轮次：{sourceQuery.error.message}</p>
            <button type="button" disabled={disabled} className="text-primary hover:underline"
              onClick={() => void sourceQuery.refetch()}>重新加载来源</button>
          </div> : sources.length === 0 ?
            <p role="status" className="text-xs text-muted-foreground">暂无可用的成功轮次，可关闭“附带上一轮产物”后发送文字。</p> :
            <label className="block space-y-1 text-xs">
              <span>选择成功轮次</span>
              <Select aria-label="产物来源轮次" disabled={disabled} value={source?.id ?? ''}
                onChange={(e) => {
                  onChange({
                    ...selection, source_turn_id: e.target.value || null,
                    config: { ...selection.config, selected_candidate_id: null },
                  })
                  setSourcePickerOpen(false)
                }}>
                <option value="">请选择成功轮次</option>
                {sources.map((turn) => <option key={turn.id} value={turn.id}>第 {turn.idx + 1} 轮</option>)}
              </Select>
            </label>
      )}
      {!selection.source_turn_id ? <p role="status" className="text-xs text-warning">
        请先选择成功轮次；关闭此项后可直接发送文字。
      </p> : !urlPointerValid ? null : checking ?
        <p role="status" className="text-xs text-muted-foreground">正在检查产物链接…</p> : query.error ?
          <div className="space-y-1 text-xs">
            <p role="alert" className="text-danger">无法读取产物来源：{query.error.message}</p>
            <button type="button" disabled={disabled} className="text-primary hover:underline"
              onClick={() => void query.refetch()}>重新检查产物</button>
          </div> :
          items.length === 0 ? <p role="status" className="text-xs text-warning">
            未找到 JSON 产物链接，请在高级设置中检查链接字段，或更换来源。
          </p> : items.length === 1 && selected ? (
            <div className="space-y-1">
              <p role="status" className="text-xs font-medium">已选定产物链接</p>
              <p className="break-all text-xs text-muted-foreground">{selected.url}</p>
            </div>
          ) : (
            <label className="block space-y-1 text-xs">
              <span>JSON 产物 · 请选择一个链接</span>
              <Select aria-label="JSON 产物" disabled={disabled} value={selected?.id ?? ''}
                onChange={(e) => update({ selected_candidate_id: e.target.value || null })}>
                <option value="">请选择产物链接</option>
                {items.map((item) => <option key={item.id} value={item.id}>{item.url}</option>)}
              </Select>
            </label>
          )}
      <details className="text-xs" data-testid="middleware-advanced">
        <summary className="cursor-pointer py-1 text-muted-foreground">高级设置</summary>
        <div className="mt-2 space-y-3">
          {selection.source_turn_id && <p className="break-all text-muted-foreground">
            来源轮次 ID：<span className="font-mono">{selection.source_turn_id}</span>
          </p>}
          <label className="block space-y-1">
            <span>回复中的链接字段（可选）</span>
            <Input aria-label="回复链接字段" mono placeholder="例如 /jsonUrl"
              value={configuredUrlPointer} invalid={!urlPointerValid} disabled={disabled}
              onChange={(e) => update({ url_pointer: e.target.value || null, selected_candidate_id: null })} />
            <span className="block text-muted-foreground">留空自动查找回复中的 JSON 产物链接。</span>
          </label>
          <label className="block space-y-1">
            <span>传入的内容字段（可选）</span>
            <Input aria-label="产物内容字段" mono placeholder="例如 /story/scenes"
              value={contentPointer} invalid={!pointerValid} disabled={disabled}
              onChange={(e) => update({ content_pointer: e.target.value || null })} />
            <span className="block text-muted-foreground">留空传入完整 JSON。</span>
          </label>
        </div>
      </details>
      {!urlPointerValid && <p role="alert" className="text-xs text-danger">链接字段路径无效，请在高级设置中使用 / 开头的字段路径。</p>}
      {!pointerValid && <p role="alert" className="text-xs text-danger">内容字段路径无效，请在高级设置中使用 / 开头的字段路径。</p>}
      <p className="text-xs text-muted-foreground">发送后读取产物并保存实际内容，原始指令单独保留。</p>
    </div>
  )
}

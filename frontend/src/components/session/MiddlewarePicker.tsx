import { useArtifactCandidates } from '@/api/queries/sessions'
import type { MiddlewareSelection } from '@/api/types'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { useEffect } from 'react'

export function MiddlewarePicker({ sessionId, selection, onChange, onReady, disabled }: {
  sessionId: string
  selection: MiddlewareSelection
  onChange: (value: MiddlewareSelection) => void
  disabled?: boolean
  onReady: (ready: boolean) => void
}) {
  const urlPointer = useDebouncedValue(selection.config.url_pointer ?? '', 300)
  const query = useArtifactCandidates(sessionId, selection.source_turn_id ?? undefined, urlPointer, true)
  const items = query.data?.candidates ?? []
  const contentPointer = selection.config.content_pointer ?? ''
  const pointerValid = (contentPointer === '' || contentPointer.startsWith('/')) && !/~(?![01])/.test(contentPointer)
  const ready = !query.isFetching && !query.error && pointerValid &&
    urlPointer === (selection.config.url_pointer ?? '') &&
    (items.length === 1 && !selection.config.selected_candidate_id ||
      items.some((item) => item.id === selection.config.selected_candidate_id))
  useEffect(() => onReady(!!ready), [onReady, ready])
  const update = (config: MiddlewareSelection['config']) =>
    onChange({ ...selection, config: { ...selection.config, ...config } })
  return (
    <div className="mb-2 space-y-2 border-l-2 border-primary bg-muted/30 p-3 text-xs" data-testid="middleware-picker">
      <p className="font-medium">读取上一轮 JSON <span className="font-normal text-muted-foreground">· 仅本轮生效</span></p>
      <p className="break-all text-muted-foreground">来源轮次：{selection.source_turn_id}</p>
      <label className="block space-y-1">
        <span>回复中的链接字段（可选）</span>
        <Input aria-label="回复链接字段" mono placeholder="/jsonUrl" value={selection.config.url_pointer ?? ''}
          disabled={disabled} onChange={(e) => update({ url_pointer: e.target.value || null, selected_candidate_id: null })} />
      </label>
      {query.isFetching ? <p role="status">正在提取候选链接…</p> : query.error ?
        <p role="alert" className="text-danger">{query.error.message}</p> :
        items.length === 0 ? <p role="status" className="text-warning">上一轮未找到 JSON 产物，可指定链接字段。</p> :
          <label className="block space-y-1">
            <span>JSON 产物{items.length > 1 && '（请选择一个）'}</span>
            <Select aria-label="JSON 产物" disabled={disabled}
              value={selection.config.selected_candidate_id ?? (items.length === 1 ? items[0].id : '')}
              onChange={(e) => update({ selected_candidate_id: e.target.value || null })}>
              {items.length > 1 && <option value="">请选择产物</option>}
              {items.map((item) => <option key={item.id} value={item.id}>{item.url}</option>)}
            </Select>
          </label>}
      <label className="block space-y-1">
        <span>传入 JSON 字段（可选，留空传入全文）</span>
        <Input aria-label="产物内容字段" mono placeholder="/story/scenes" value={selection.config.content_pointer ?? ''}
          disabled={disabled} onChange={(e) => update({ content_pointer: e.target.value || null })} />
      </label>
      <p className="text-muted-foreground">发送后读取一次，并保存实际内容。原始指令完整保留。</p>
      {!pointerValid && <p role="alert" className="text-danger">内容字段须为有效的 JSON Pointer。</p>}
    </div>
  )
}

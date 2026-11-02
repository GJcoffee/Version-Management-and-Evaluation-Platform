import { useEffect, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { api, ApiError } from '@/api/client'
import { useBindingMutation, type AdapterBinding, type BindingState } from '@/api/queries/adapters'
import { AdapterBindingFields } from './AdapterBindingFields'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Field, Input } from './ui/Input'

export function VersionBindingDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const states = useQueries({ queries: ids.map((id) => ({
    queryKey: ['adapter-binding', id],
    queryFn: () => api.get<BindingState>(`/versions/${id}/adapter-binding`),
  })) })
  const [binding, setBinding] = useState<AdapterBinding | null>(null)
  const [reason, setReason] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [preview, setPreview] = useState<Record<string, unknown>[] | null>(null)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState(false)
  const mutation = useBindingMutation()
  const first = states[0]?.data
  useEffect(() => { if (first) setBinding(first.current.binding) }, [first])
  const ready = states.every((s) => s.data)
  const update = async (dryRun: boolean) => {
    setError('')
    try {
      const result = await mutation.mutateAsync({
        path: '/adapters/bindings/batch',
        body: { binding, reason, expected_revision: 0,
          expected_revisions: Object.fromEntries(states.map((s) => [s.data!.version_id, s.data!.current.revision])),
          version_ids: ids, acknowledge_transport_change: acknowledged, dry_run: dryRun },
      }) as Record<string, unknown>[]
      setPreview(result)
      setApplied(!dryRun && result.some((r) => r.applied))
    } catch (e) { setError(e instanceof ApiError ? e.message : '接入修改失败') }
  }
  return <Dialog open onOpenChange={(open) => !open && onClose()} title={`修改调用接入 · ${ids.length} 个版本`}
    description="保留版本 ID 与业务 Spec；覆盖只影响之后创建的会话，恢复也会保存新修订。"
    footer={<>
      <Button variant="outline" onClick={onClose}>关闭</Button>
      <Button variant="outline" disabled={!ready || !reason.trim()} loading={mutation.isPending}
        onClick={() => void update(true)}>检查覆盖</Button>
      <Button disabled={applied || !preview || !ready || preview.some((r) => r.error) || !reason.trim()}
        loading={mutation.isPending} onClick={() => void update(false)}>执行覆盖</Button>
    </>}>
    <div className="space-y-3">
      {!ready ? <p role="status" className="text-sm">{
        states.some((s) => s.isError) ? '接入读取失败，请关闭后重试' : '读取当前接入…'
      }</p> : <>
        <div className="space-y-1 text-xs text-muted-foreground">
          {states.map((s) => <p key={s.data!.version_id}>{s.data!.version_id} · 接入修订 {s.data!.current.revision} · {
            s.data!.current.binding ? `${s.data!.current.binding.id}@${s.data!.current.binding.version}` : '原运行方式'
          }</p>)}
        </div>
        <AdapterBindingFields value={binding} onChange={(v) => {
          setBinding(v); setPreview(null); setApplied(false)
        }} />
        <Field label="修改原因" htmlFor="binding-change-reason" required><Input id="binding-change-reason" value={reason}
          onChange={(e) => { setReason(e.target.value); setPreview(null) }} /></Field>
        <label className="flex items-start gap-2 text-xs">
          <input type="checkbox" checked={acknowledged} onChange={(e) => {
            setAcknowledged(e.target.checked); setPreview(null)
          }} />
          已检查旧入口承担的业务处理，确认切换后仍保留这些行为
        </label>
        {ids.length === 1 && <div className="space-y-2 border-t border-border pt-3">
          <p className="text-sm font-medium">接入修订历史</p>
          {first?.history.map((h) => <div key={h.revision} className="flex items-center justify-between text-xs">
            <span>修订 {h.revision} · {h.binding ? `${h.binding.id}@${h.binding.version}` : '原运行方式'} · {h.reason}</span>
            <Button size="sm" variant="ghost" disabled={h.revision === first.current.revision || !reason.trim()}
              onClick={() => {
                void mutation.mutateAsync({ path: `/versions/${ids[0]}/adapter-binding/rollback`,
                  body: { revision: h.revision, expected_revision: first.current.revision, reason } })
                  .catch((e) => setError(e instanceof ApiError ? e.message : '恢复失败'))
              }}>恢复</Button>
          </div>)}
        </div>}
      </>}
      {preview?.map((result) => <p key={String(result.version_id)} className="text-xs">
        {String(result.version_id)} · {result.error ? JSON.stringify(result.error) : result.applied ? '已覆盖' : '检查通过'}
      </p>)}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </div>
  </Dialog>
}

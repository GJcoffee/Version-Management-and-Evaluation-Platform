import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GitCompareArrows, Repeat2 } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { ReplayMode } from '@/api/compareTypes'
import { useCreateCompare, useReplaySession } from '@/api/queries/compare'
import { useSession, useSessions } from '@/api/queries/sessions'
import { useVersions } from '@/api/queries/versions'
import type { Session } from '@/api/types'
import { messageText } from '@/components/session'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field } from '@/components/ui/Input'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Select } from '@/components/ui/Select'
import { sessionTitle, versionLabel } from '@/pages/session/sessionUtils'
import { toast } from '@/stores/toast'

/** 会话页顶部的两个入口：在其他版本上重放、与其他会话对比 */
export function SessionCompareActions({ session }: { session: Session }) {
  const [dialog, setDialog] = useState<'replay' | 'history' | null>(null)
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setDialog('replay')} data-testid="open-replay">
        <Repeat2 className="h-3.5 w-3.5" />
        在其他版本上重放
      </Button>
      <Button size="sm" variant="outline" onClick={() => setDialog('history')} data-testid="open-compare-session">
        <GitCompareArrows className="h-3.5 w-3.5" />
        与其他会话对比
      </Button>
      {dialog === 'replay' && <ReplayDialog session={session} onClose={() => setDialog(null)} />}
      {dialog === 'history' && <CompareSessionDialog session={session} onClose={() => setDialog(null)} />}
    </>
  )
}

function errMsg(e: unknown): string | undefined {
  return e instanceof ApiError ? e.message : undefined
}

const REPLAY_MODES: Array<{ value: ReplayMode; label: string; hint: string }> = [
  { value: 'anchored', label: '锚定模式', hint: '每一轮都用原会话的历史作为上下文，各轮并发执行；适合精确比较单轮差异。' },
  { value: 'free', label: '自由模式', hint: '新版本用自己的回复续写上下文，逐轮串行（更慢）；适合观察整段对话的走向。' },
]

/** 选目标版本、重放模式和要重放的轮次（默认全部）-> POST /sessions/{id}/replay -> 跳转对比页 */
export function ReplayDialog({ session, onClose }: { session: Session; onClose: () => void }) {
  const navigate = useNavigate()
  const versions = useVersions(session.agent_id)
  const detail = useSession(session.id, 'turns')
  const replay = useReplaySession(session.id)
  const [target, setTarget] = useState('')
  const [replayMode, setReplayMode] = useState<ReplayMode>('anchored')
  const [unchecked, setUnchecked] = useState<ReadonlySet<number>>(new Set())

  const vlist = useMemo(() => versions.data?.pages.flatMap((p) => p.items) ?? [], [versions.data])
  // 仍在执行的轮次不能重放
  const turns = useMemo(() => (detail.data?.turns ?? []).filter((t) => t.status !== 'running'), [detail.data])
  const targetId = target || vlist.find((v) => v.id !== session.version_id)?.id || vlist[0]?.id || ''
  const selected = turns.filter((t) => !unchecked.has(t.idx)).map((t) => t.idx)
  const all = selected.length === turns.length

  const toggle = (idx: number) =>
    setUnchecked((cur) => {
      const next = new Set(cur)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })

  const submit = async () => {
    if (!targetId || selected.length === 0) return
    try {
      const r = await replay.mutateAsync({ target_version_id: targetId, turns: all ? undefined : selected, mode: replayMode })
      onClose()
      navigate(`/compare/${r.compare_id}`)
    } catch (e) {
      toast.error('发起重放失败', { description: errMsg(e) })
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="md"
      title="在其他版本上重放"
      description="用另一个版本重新回答这个会话的每一轮。"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={replay.isPending} disabled={!targetId || selected.length === 0} data-testid="replay-submit">
            开始重放
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="目标版本" htmlFor="replay-target">
          {versions.isLoading ? (
            <LoadingBlock className="py-2" />
          ) : (
            <Select id="replay-target" value={targetId} onChange={(e) => setTarget(e.target.value)} data-testid="replay-target">
              {vlist.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.seq}
                  {v.tags.length > 0 ? `（${v.tags.join(', ')}）` : ''}
                  {v.id === session.version_id ? ' · 当前会话所用版本' : ''}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="重放模式">
          <div role="radiogroup" aria-label="重放模式" className="space-y-1.5" data-testid="replay-mode">
            {REPLAY_MODES.map((m) => (
              <label key={m.value} className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="replay-mode"
                  className="mt-1 h-4 w-4 accent-primary"
                  checked={replayMode === m.value}
                  onChange={() => setReplayMode(m.value)}
                  data-testid={`replay-mode-${m.value}`}
                />
                <span>
                  <span className="font-medium">{m.label}</span>
                  <span className="block text-xs text-muted-foreground">{m.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </Field>
        <div>
          <div className="mb-1.5 flex items-center justify-between text-sm font-medium">
            <span>要重放的轮次</span>
            <span className="text-xs font-normal text-muted-foreground">
              {selected.length} / {turns.length}
              <button
                type="button"
                className="ml-2 text-primary hover:underline"
                onClick={() => setUnchecked(all ? new Set(turns.map((t) => t.idx)) : new Set())}
              >
                {all ? '全不选' : '全选'}
              </button>
            </span>
          </div>
          {detail.isLoading ? (
            <LoadingBlock className="py-4" />
          ) : turns.length === 0 ? (
            <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">这个会话还没有可重放的轮次。</p>
          ) : (
            <ul className="max-h-56 divide-y divide-border overflow-y-auto rounded-md border border-border" data-testid="replay-turns">
              {turns.map((t) => (
                <li key={t.id}>
                  <label className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-sm hover:bg-accent/50">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-primary"
                      checked={!unchecked.has(t.idx)}
                      onChange={() => toggle(t.idx)}
                      aria-label={`重放第 ${t.idx + 1} 轮`}
                    />
                    <span className="font-mono text-xs text-muted-foreground">#{t.idx + 1}</span>
                    <span className="min-w-0 flex-1 truncate">{messageText(t.input) || '（空消息）'}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/** 选同一 agent 下的另一个会话 -> 创建 history 对比 -> 跳转 */
export function CompareSessionDialog({ session, onClose }: { session: Session; onClose: () => void }) {
  const navigate = useNavigate()
  // 候选默认不含重放产生的会话（它们是对比的副产品，容易把真正想比的会话挤出列表）
  const [withReplays, setWithReplays] = useState(false)
  const list = useSessions({ agent_id: session.agent_id, mode: withReplays ? 'chat,replay' : 'chat', limit: 100 })
  const create = useCreateCompare()
  const [other, setOther] = useState('')
  const candidates = useMemo(() => (list.data?.items ?? []).filter((s) => s.id !== session.id), [list.data, session.id])
  const otherId = other || candidates[0]?.id || ''

  const submit = async () => {
    if (!otherId) return
    try {
      const g = await create.mutateAsync({ kind: 'session', mode: 'history', left: { session_id: session.id }, right: { session_id: otherId } })
      onClose()
      navigate(`/compare/${g.id}`)
    } catch (e) {
      toast.error('创建对比失败', { description: errMsg(e) })
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="md"
      title="与其他会话对比"
      description={`选择 ${session.agent_name ?? '该 agent'} 下的另一个会话，按轮次逐行对比（当前会话在左侧）。`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={create.isPending} disabled={!otherId} data-testid="compare-session-submit">
            开始对比
          </Button>
        </>
      }
    >
      <label className="mb-3 flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          className="h-4 w-4 accent-primary"
          checked={withReplays}
          onChange={(e) => setWithReplays(e.target.checked)}
          data-testid="include-replays"
        />
        包含重放会话
      </label>
      <Field label="另一个会话" htmlFor="compare-other">
        {list.isLoading ? (
          <LoadingBlock className="py-2" />
        ) : candidates.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">这个 agent 下没有其他会话可以对比。</p>
        ) : (
          <Select id="compare-other" value={otherId} onChange={(e) => setOther(e.target.value)} data-testid="compare-other">
            {candidates.map((s) => (
              <option key={s.id} value={s.id}>
                {sessionTitle(s)} · {versionLabel(s)} · {s.stats.turns ?? 0} 轮
              </option>
            ))}
          </Select>
        )}
      </Field>
    </Dialog>
  )
}

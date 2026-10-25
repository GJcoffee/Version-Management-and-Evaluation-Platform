import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useCreateSession } from '@/api/queries/sessions'
import type { Agent, SessionCreate, VersionSummary } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { KeyValueEditor } from '@/components/ui/KeyValueEditor'
import { Select } from '@/components/ui/Select'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'

/** 选项值：`latest` | `tag:名称` | `ver:版本id` */
export function sessionCreateBody(choice: string, agent: Pick<Agent, 'id'>, extra: Partial<SessionCreate>): SessionCreate {
  if (choice.startsWith('ver:')) return { version_id: choice.slice(4), ...extra }
  if (choice.startsWith('tag:')) return { agent: agent.id, tag: choice.slice(4), ...extra }
  return { agent: agent.id, tag: 'latest', ...extra }
}

/** 新建对话：选择版本或标签，可设置会话变量 */
export function NewSessionDialog({
  agent,
  versions,
  defaultVersionId,
  onClose,
}: {
  agent: Agent
  versions: VersionSummary[]
  defaultVersionId?: string
  onClose: () => void
}) {
  const navigate = useNavigate()
  const create = useCreateSession()
  const [choice, setChoice] = useState(defaultVersionId ? `ver:${defaultVersionId}` : 'latest')
  const [title, setTitle] = useState('')
  const [vars, setVars] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const tags = useMemo(() => {
    const seen = new Map<string, number>()
    for (const v of versions) for (const t of v.tags) if (!seen.has(t)) seen.set(t, v.seq)
    return [...seen.entries()]
  }, [versions])
  const latestSeq = versions.length > 0 ? Math.max(...versions.map((v) => v.seq)) : agent.latest_version?.seq

  const submit = async () => {
    setError(null)
    try {
      const { session } = await create.mutateAsync(
        sessionCreateBody(choice, agent, {
          title: title.trim() || undefined,
          variables: Object.keys(vars).length > 0 ? vars : undefined,
        }),
      )
      toast.success('已创建会话')
      onClose()
      navigate(`/sessions/${session.id}`)
    } catch (e) {
      const f = formatApiError(e, '创建会话失败')
      setError([f.message, ...f.lines].join('\n'))
      if (!(e instanceof ApiError)) toast.error('创建会话失败')
    }
  }

  const noVersion = versions.length === 0 && !agent.latest_version
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="md"
      title={`与 ${agent.name} 新建对话`}
      description="会话固定绑定到所选版本；之后发布新版本不会影响已有会话。"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={create.isPending} disabled={noVersion} data-testid="create-session">
            开始对话
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {noVersion && (
          <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
            这个 Agent 还没有发布过版本，请先在草稿编辑器中发布。
          </p>
        )}
        <Field label="版本" required htmlFor="ns-version">
          <Select id="ns-version" value={choice} onChange={(e) => setChoice(e.target.value)} disabled={noVersion}>
            <option value="latest">最新版本{latestSeq !== undefined ? `（v${latestSeq}）` : ''}</option>
            {tags.length > 0 && (
              <optgroup label="标签">
                {tags.map(([t, seq]) => (
                  <option key={t} value={`tag:${t}`}>
                    {t}（v{seq}）
                  </option>
                ))}
              </optgroup>
            )}
            {versions.length > 0 && (
              <optgroup label="版本">
                {versions.map((v) => (
                  <option key={v.id} value={`ver:${v.id}`}>
                    v{v.seq}
                    {v.tags.length > 0 ? `（${v.tags.join(', ')}）` : ''}
                    {v.notes ? ` · ${v.notes.slice(0, 24)}` : ''}
                  </option>
                ))}
              </optgroup>
            )}
          </Select>
        </Field>
        <Field label="标题" htmlFor="ns-title" hint="可选；缺省时取第一轮用户输入的前几十个字">
          <Input id="ns-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：退款流程验证" />
        </Field>
        <Field label="会话变量" hint="可选；模板变量，优先级：Spec < 会话 < 单轮">
          <KeyValueEditor value={vars} onChange={setVars} keyPlaceholder="变量名" valuePlaceholder="值" addLabel="添加变量" />
        </Field>
        {error && (
          <p role="alert" className="whitespace-pre-wrap rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

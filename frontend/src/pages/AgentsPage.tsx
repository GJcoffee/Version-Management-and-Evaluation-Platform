import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Archive, ArchiveRestore, Bot, Plus, Search } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useAgentTags, useAgents, useCreateAgent, useUpdateAgent } from '@/api/queries/agents'
import type { Agent, AgentKind } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input, Textarea } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { toast } from '@/stores/toast'

export const KIND_LABEL: Record<AgentKind, string> = { config: '配置型', code: '代码型' }

/** agent 名称：字母（含中文）、数字、_ . -，最长 64，不含空格和冒号 */
export const AGENT_NAME_RE = /^[\p{L}\p{N}_.-]{1,64}$/u

function TagsCell({ agent }: { agent: Agent }) {
  const tags = useAgentTags(agent.id)
  const items = tags.data ?? []
  if (items.length === 0) return <span className="text-muted-foreground">-</span>
  return (
    <div className="flex flex-wrap gap-1">
      {items.slice(0, 4).map((t) => (
        <Badge key={t.tag} variant="outline" className="font-mono">
          {t.tag}
        </Badge>
      ))}
      {items.length > 4 && <span className="text-xs text-muted-foreground">+{items.length - 4}</span>}
    </div>
  )
}

export default function AgentsPage() {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<AgentKind | ''>('')
  const [showArchived, setShowArchived] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [archiving, setArchiving] = useState<Agent | null>(null)
  const dq = useDebouncedValue(q.trim(), 250)
  const { data, isLoading, error, refetch } = useAgents({ q: dq, kind, include_archived: showArchived })
  const update = useUpdateAgent()
  const agents = data?.items ?? []

  const setArchived = async (a: Agent, archived: boolean) => {
    try {
      await update.mutateAsync({ ref: a.id, body: { archived } })
      toast.success(archived ? `已归档 ${a.name}` : `已取消归档 ${a.name}`)
    } catch (e) {
      toast.error('操作失败', { description: e instanceof ApiError ? e.message : undefined })
      throw e
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Agents</h1>
          <p className="text-xs text-muted-foreground">被测的智能体：配置型（模型 + 提示词 + 工具）或代码型（HTTP / 子进程）。</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新建 Agent
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="搜索名称…" aria-label="搜索 Agent" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select aria-label="类型" className="w-32" value={kind} onChange={(e) => setKind(e.target.value as AgentKind | '')}>
          <option value="">全部类型</option>
          <option value="config">配置型</option>
          <option value="code">代码型</option>
        </Select>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 accent-primary"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          显示已归档
        </label>
      </div>

      {isLoading ? (
        <LoadingBlock />
      ) : error ? (
        <div className="flex items-center gap-3 py-6 text-sm text-danger">
          加载失败：{error instanceof ApiError ? error.message : String(error)}
          <Button size="sm" variant="outline" onClick={() => refetch()}>
            重试
          </Button>
        </div>
      ) : agents.length === 0 ? (
        <EmptyState
          icon={Bot}
          title={dq || kind ? '没有符合条件的 Agent' : '还没有 Agent'}
          description="新建一个 Agent，编辑草稿并发布版本后，就可以对话和评测了。"
          action={
            !dq && !kind ? (
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <Plus className="h-3.5 w-3.5" />
                新建 Agent
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>名称</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>最新版本</TableHead>
              <TableHead>标签</TableHead>
              <TableHead>更新时间</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {agents.map((a) => (
              <TableRow key={a.id} className={a.archived ? 'opacity-60' : undefined}>
                <TableCell>
                  <Link to={`/agents/${a.id}`} className="font-medium hover:underline">
                    {a.name}
                  </Link>
                  {a.archived && (
                    <Badge className="ml-2" variant="secondary">
                      已归档
                    </Badge>
                  )}
                  {a.description && <p className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">{a.description}</p>}
                </TableCell>
                <TableCell>
                  <Badge variant={a.kind === 'config' ? 'info' : 'warning'}>{KIND_LABEL[a.kind]}</Badge>
                </TableCell>
                <TableCell>
                  {a.latest_version ? (
                    <span className="font-mono text-xs">
                      v{a.latest_version.seq}
                      <span className="ml-1.5 text-muted-foreground">{a.latest_version.spec_hash.slice(0, 8)}</span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">未发布</span>
                  )}
                </TableCell>
                <TableCell>
                  <TagsCell agent={a} />
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <RelativeTime value={a.updated_at} />
                </TableCell>
                <TableCell>
                  <div className="flex justify-end">
                    {a.archived ? (
                      <Button size="sm" variant="ghost" onClick={() => void setArchived(a, false).catch(() => undefined)}>
                        <ArchiveRestore className="h-3.5 w-3.5" />
                        取消归档
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" aria-label={`归档 ${a.name}`} onClick={() => setArchiving(a)}>
                        <Archive className="h-3.5 w-3.5" />
                        归档
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateAgentDialog open={createOpen} onOpenChange={setCreateOpen} />

      <ConfirmDialog
        open={!!archiving}
        onOpenChange={(v) => !v && setArchiving(null)}
        title={`归档 Agent「${archiving?.name ?? ''}」？`}
        description="归档后默认不再显示在列表中，已有版本、会话和评测数据都会保留，可随时取消归档。"
        confirmText="归档"
        onConfirm={async () => {
          if (archiving) await setArchived(archiving, true)
        }}
      />
    </div>
  )
}

function CreateAgentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  if (!open) return null
  return <CreateAgentDialogInner onOpenChange={onOpenChange} />
}

function CreateAgentDialogInner({ onOpenChange }: { onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate()
  const create = useCreateAgent()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<AgentKind>('config')
  const [description, setDescription] = useState('')
  const [touched, setTouched] = useState(false)

  const nameError = !name.trim() ? '请填写名称' : !AGENT_NAME_RE.test(name.trim()) ? '只能包含字母（含中文）、数字、_ . -，最长 64，不能有空格和冒号' : null

  const submit = async () => {
    setTouched(true)
    if (nameError) return
    try {
      const r = await create.mutateAsync({ name: name.trim(), kind, description: description.trim() || undefined })
      toast.success(`已创建 ${r.agent.name}`)
      onOpenChange(false)
      navigate(`/agents/${r.agent.id}/draft/${r.draft.id}`)
    } catch (e) {
      toast.error('创建失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title="新建 Agent"
      description="创建后会生成 main 草稿并进入编辑器"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} loading={create.isPending}>
            创建
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label="名称" required htmlFor="ag-name" error={touched ? nameError : null} hint="例如 customer-support">
          <Input id="ag-name" autoFocus value={name} invalid={touched && !!nameError} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="类型" htmlFor="ag-kind" hint={kind === 'config' ? '由平台直接调用模型：模型 + 提示词 + 工具' : '你自己的服务或程序：通过 HTTP 或子进程接入'}>
          <Select id="ag-kind" value={kind} onChange={(e) => setKind(e.target.value as AgentKind)}>
            <option value="config">配置型（config）</option>
            <option value="code">代码型（code）</option>
          </Select>
        </Field>
        <Field label="描述" htmlFor="ag-desc">
          <Textarea id="ag-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

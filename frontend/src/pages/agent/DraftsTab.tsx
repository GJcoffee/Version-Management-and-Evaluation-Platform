import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FilePen, Plus } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useCreateDraft, useDrafts } from '@/api/queries/drafts'
import type { Agent, VersionSummary } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { toast } from '@/stores/toast'

const DRAFT_NAME_RE = /^[\p{L}\p{N}_.-]{1,64}$/u

export function DraftsTab({ agent, versions }: { agent: Agent; versions: VersionSummary[] }) {
  const navigate = useNavigate()
  const { data, isLoading, error } = useDrafts(agent.id)
  const [createOpen, setCreateOpen] = useState(false)
  const seqById = useMemo(() => new Map(versions.map((v) => [v.id, v.seq])), [versions])
  const drafts = data ?? []

  if (isLoading) return <LoadingBlock />
  if (error) return <p className="py-6 text-sm text-danger">加载失败：{error instanceof ApiError ? error.message : String(error)}</p>

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          新建草稿
        </Button>
      </div>
      {drafts.length === 0 ? (
        <EmptyState icon={FilePen} title="还没有草稿" description="新建草稿后可以在编辑器中修改配置并发布版本。" />
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>名称</TableHead>
              <TableHead>revision</TableHead>
              <TableHead>基础版本</TableHead>
              <TableHead>更新时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {drafts.map((d) => (
              <TableRow
                key={d.id}
                className="cursor-pointer"
                tabIndex={0}
                onClick={() => navigate(`/agents/${agent.id}/draft/${d.id}`)}
                onKeyDown={(e) => e.key === 'Enter' && navigate(`/agents/${agent.id}/draft/${d.id}`)}
              >
                <TableCell className="font-mono text-sm font-medium">{d.name}</TableCell>
                <TableCell className="font-mono text-xs">r{d.revision}</TableCell>
                <TableCell className="font-mono text-xs">
                  {d.base_version_id ? (seqById.has(d.base_version_id) ? `v${seqById.get(d.base_version_id)}` : d.base_version_id.slice(-8)) : <span className="text-muted-foreground">无</span>}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <RelativeTime value={d.updated_at} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {createOpen && (
        <CreateDraftDialog
          agent={agent}
          versions={versions}
          existing={drafts.map((d) => d.name)}
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => navigate(`/agents/${agent.id}/draft/${id}`)}
        />
      )}
    </div>
  )
}

function CreateDraftDialog({
  agent,
  versions,
  existing,
  onClose,
  onCreated,
}: {
  agent: Agent
  versions: VersionSummary[]
  existing: string[]
  onClose: () => void
  onCreated: (draftId: string) => void
}) {
  const create = useCreateDraft()
  const [name, setName] = useState('')
  const [from, setFrom] = useState('')
  const [touched, setTouched] = useState(false)
  const nameError = !name.trim()
    ? '请填写名称'
    : !DRAFT_NAME_RE.test(name.trim())
      ? '只能包含字母（含中文）、数字、_ . -'
      : existing.includes(name.trim())
        ? '已存在同名草稿'
        : null

  const submit = async () => {
    setTouched(true)
    if (nameError) return
    try {
      const r = await create.mutateAsync({ agentRef: agent.id, name: name.trim(), from_version_id: from || undefined })
      toast.success(`已创建草稿 ${r.draft.name}`)
      onClose()
      onCreated(r.draft.id)
    } catch (e) {
      toast.error('创建失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="新建草稿"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
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
        <Field label="草稿名称" required htmlFor="dr-name" error={touched ? nameError : null} hint="例如 experiment-1">
          <Input id="dr-name" autoFocus value={name} invalid={touched && !!nameError} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="初始内容" htmlFor="dr-from">
          <Select id="dr-from" value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">最小模板</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                基于 v{v.seq}
                {v.tags.length ? `（${v.tags.join('、')}）` : ''}
              </option>
            ))}
          </Select>
        </Field>
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

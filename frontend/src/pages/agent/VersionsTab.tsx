import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GitCompare, History, FilePlus2, MessageSquarePlus, Tag, X } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useCreateSession } from '@/api/queries/sessions'
import { useAddTag, useRemoveTag, useVersionToDraft } from '@/api/queries/versions'
import type { Agent, VersionSummary } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input } from '@/components/ui/Input'
import { IdText } from '@/components/ui/IdText'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { toast } from '@/stores/toast'

const TAG_RE = /^[\p{L}\p{N}_.-]{1,64}$/u

export function validateTagName(tag: string): string | null {
  const t = tag.trim()
  if (!t) return '请填写标签'
  if (!TAG_RE.test(t)) return '标签只能包含字母、数字、_ . -，最长 64'
  if (t === 'latest' || /^v\d+$/.test(t)) return '标签不能是 latest 或形如 v12'
  return null
}

const VIA_VARIANT = { ui: 'default', cli: 'success', mcp: 'warning', api: 'secondary' } as const

export function VersionsTab({
  agent,
  versions,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  agent: Agent
  versions: VersionSummary[]
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
}) {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<string[]>([])
  const [tagging, setTagging] = useState<VersionSummary | null>(null)
  const [toDraft, setToDraft] = useState<VersionSummary | null>(null)
  const [untag, setUntag] = useState<{ v: VersionSummary; tag: string } | null>(null)
  const removeTag = useRemoveTag()
  const createSession = useCreateSession()
  const [chatting, setChatting] = useState<string | null>(null)

  /** 与该版本开始一次对话：创建会话后直接进入会话页 */
  const startChat = async (v: VersionSummary) => {
    setChatting(v.id)
    try {
      const { session } = await createSession.mutateAsync({ version_id: v.id })
      navigate(`/sessions/${session.id}`)
    } catch (e) {
      toast.error('创建会话失败', { description: e instanceof ApiError ? e.message : undefined })
    } finally {
      setChatting(null)
    }
  }

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= 2 ? [cur[1], id] : [...cur, id]))

  const compare = () => {
    const [a, b] = selected.map((id) => versions.find((v) => v.id === id)!)
    // 左旧右新
    const [left, right] = a.seq <= b.seq ? [a, b] : [b, a]
    navigate(`/agents/${agent.id}/versions/diff?left=${left.id}&right=${right.id}`)
  }

  if (versions.length === 0) {
    return <EmptyState icon={History} title="还没有发布过版本" description="在草稿编辑器中点击“发布”后，版本会出现在这里。" />
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">勾选两个版本可对比差异（已选 {selected.length}/2）</p>
        <Button size="sm" variant="outline" disabled={selected.length !== 2} onClick={compare}>
          <GitCompare className="h-3.5 w-3.5" />
          对比所选版本
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-8" />
            <TableHead>版本</TableHead>
            <TableHead>spec_hash</TableHead>
            <TableHead>标签</TableHead>
            <TableHead>备注</TableHead>
            <TableHead>来源</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {versions.map((v) => (
            <TableRow key={v.id} data-testid={`version-row-${v.seq}`}>
              <TableCell>
                <input
                  type="checkbox"
                  aria-label={`选择 v${v.seq}`}
                  className="h-4 w-4 accent-primary"
                  checked={selected.includes(v.id)}
                  onChange={() => toggle(v.id)}
                />
              </TableCell>
              <TableCell className="font-mono text-sm font-semibold">v{v.seq}</TableCell>
              <TableCell>
                <IdText value={v.spec_hash} />
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap items-center gap-1">
                  {v.tags.map((t) => (
                    <Badge key={t} variant="info" className="gap-0.5 font-mono">
                      {t}
                      <button
                        type="button"
                        aria-label={`删除标签 ${t}`}
                        className="rounded hover:text-danger"
                        onClick={() => setUntag({ v, tag: t })}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  ))}
                </div>
              </TableCell>
              <TableCell className="max-w-64 truncate text-xs" title={v.notes ?? undefined}>
                {v.notes ?? <span className="text-muted-foreground">-</span>}
              </TableCell>
              <TableCell>
                <Badge variant={VIA_VARIANT[v.created_via] ?? 'secondary'}>{v.created_via}</Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <RelativeTime value={v.created_at} />
              </TableCell>
              <TableCell>
                <div className="flex justify-end gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void startChat(v)}
                    loading={chatting === v.id}
                    disabled={chatting !== null}
                    data-testid={`chat-v${v.seq}`}
                  >
                    {chatting !== v.id && <MessageSquarePlus className="h-3.5 w-3.5" />}
                    对话
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setTagging(v)}>
                    <Tag className="h-3.5 w-3.5" />
                    打标签
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setToDraft(v)}>
                    <FilePlus2 className="h-3.5 w-3.5" />
                    创建草稿
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hasMore && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={onLoadMore} loading={loadingMore}>
            加载更多
          </Button>
        </div>
      )}

      {tagging && <AddTagDialog version={tagging} onClose={() => setTagging(null)} />}
      {toDraft && (
        <ToDraftDialog
          version={toDraft}
          onClose={() => setToDraft(null)}
          onDone={(draftId) => navigate(`/agents/${agent.id}/draft/${draftId}`)}
        />
      )}
      <ConfirmDialog
        open={!!untag}
        onOpenChange={(v) => !v && setUntag(null)}
        danger
        title={`删除标签「${untag?.tag ?? ''}」？`}
        description={`将从 v${untag?.v.seq} 上移除该标签。引用 ${agent.name}:${untag?.tag ?? ''} 的地方将无法解析。`}
        confirmText="删除"
        onConfirm={async () => {
          if (!untag) return
          try {
            await removeTag.mutateAsync({ versionId: untag.v.id, tag: untag.tag })
            toast.success(`已删除标签 ${untag.tag}`)
          } catch (e) {
            toast.error('删除标签失败', { description: e instanceof ApiError ? e.message : undefined })
            throw e
          }
        }}
      />
    </div>
  )
}

function AddTagDialog({ version, onClose }: { version: VersionSummary; onClose: () => void }) {
  const add = useAddTag()
  const [tag, setTag] = useState('')
  const [touched, setTouched] = useState(false)
  const err = validateTagName(tag)
  const submit = async () => {
    setTouched(true)
    if (err) return
    try {
      await add.mutateAsync({ versionId: version.id, tag: tag.trim() })
      toast.success(`已给 v${version.seq} 打上标签 ${tag.trim()}`)
      onClose()
    } catch (e) {
      toast.error('打标签失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="sm"
      title={`给 v${version.seq} 打标签`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={add.isPending}>
            确定
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label="标签" required htmlFor="tag-name" error={touched ? err : null} hint="标签已存在时，会从原来的版本移到这个版本（可用于回滚）">
          <Input id="tag-name" autoFocus mono value={tag} invalid={touched && !!err} onChange={(e) => setTag(e.target.value)} placeholder="prod" />
        </Field>
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

function ToDraftDialog({
  version,
  onClose,
  onDone,
}: {
  version: VersionSummary
  onClose: () => void
  onDone: (draftId: string) => void
}) {
  const toDraft = useVersionToDraft()
  const [name, setName] = useState(`from-v${version.seq}`)
  const submit = async () => {
    if (!name.trim()) return
    try {
      const r = await toDraft.mutateAsync({ versionId: version.id, draftName: name.trim() })
      toast.success(`已从 v${version.seq} 创建草稿 ${r.draft.name}`)
      onClose()
      onDone(r.draft.id)
    } catch (e) {
      toast.error('创建草稿失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="sm"
      title={`从 v${version.seq} 创建草稿`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} loading={toDraft.isPending}>
            创建并编辑
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label="草稿名称" required htmlFor="td-name" hint="已有同名草稿时，其内容会被该版本的 Spec 覆盖（回滚用）">
          <Input id="td-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArchiveRestore,
  ChevronDown,
  ChevronRight,
  Columns2,
  FilePlus2,
  FlaskConical,
  Flag,
  GitCompare,
  History,
  MessageSquarePlus,
  Tag,
  X,
} from 'lucide-react'
import { ApiError } from '@/api/client'
import { useCreateCompare } from '@/api/queries/compare'
import { useCreateSession } from '@/api/queries/sessions'
import {
  useAddTag,
  useRemoveMilestone,
  useRemoveTag,
  useSetMilestone,
  useUnarchiveVersion,
  useVersionToDraft,
} from '@/api/queries/versions'
import type { Agent, VersionSummary } from '@/api/types'
import { EnvironmentSwitch, environmentBody, useEnvironmentChoice } from '@/components/EnvironmentSwitch'
import { NewEvalDialog } from '@/components/eval/NewEvalDialog'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { RowMenu } from '@/components/ui/RowMenu'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input } from '@/components/ui/Input'
import { IdText } from '@/components/ui/IdText'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { cn } from '@/lib/cn'
import { milestoneText } from '@/lib/versionArchive'
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
  const [evalFor, setEvalFor] = useState<VersionSummary | null>(null)
  const [milestoneFor, setMilestoneFor] = useState<VersionSummary | null>(null)
  const [unmilestone, setUnmilestone] = useState<VersionSummary | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const removeTag = useRemoveTag()
  const removeMilestone = useRemoveMilestone()
  const unarchive = useUnarchiveVersion()
  const createSession = useCreateSession()
  const env = useEnvironmentChoice()
  const [chatting, setChatting] = useState<string | null>(null)

  /** 与该版本开始一次对话：创建会话后直接进入会话页 */
  const startChat = async (v: VersionSummary) => {
    setChatting(v.id)
    try {
      const { session } = await createSession.mutateAsync({ version_id: v.id, ...environmentBody(env.environment) })
      navigate(`/sessions/${session.id}`)
    } catch (e) {
      toast.error('创建会话失败', { description: e instanceof ApiError ? e.message : undefined })
    } finally {
      setChatting(null)
    }
  }

  const createCompare = useCreateCompare()
  /** 两个版本的双栏对话（live 对比）：左旧右新，创建后直接进入对比页 */
  const dualChat = async () => {
    const [a, b] = picked.map((id) => versions.find((v) => v.id === id)!)
    const [left, right] = a.seq <= b.seq ? [a, b] : [b, a]
    try {
      const g = await createCompare.mutateAsync({
        kind: 'session',
        mode: 'live',
        left: { version_id: left.id },
        right: { version_id: right.id },
        ...environmentBody(env.environment),
      })
      navigate(`/compare/${g.id}`)
    } catch (e) {
      toast.error('创建双栏对话失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  const toggleExpanded = (id: string) =>
    setExpanded((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  /** 默认只列里程碑与未归档的版本；里程碑展开（或「显示全部」）后，其下归档的版本缩进列在它后面 */
  const rows = useMemo(() => {
    const out: Array<{ v: VersionSummary; child: boolean }> = []
    for (const v of versions) {
      if (v.archived_into) continue
      out.push({ v, child: false })
      if (v.milestone && (showAll || expanded.has(v.id))) {
        for (const c of versions) if (c.archived_into === v.id) out.push({ v: c, child: true })
      }
    }
    return out
  }, [versions, showAll, expanded])
  const hasArchived = versions.some((v) => v.archived_into)
  // 被收起的版本不再算作已选
  const picked = selected.filter((id) => rows.some((r) => r.v.id === id))

  const releaseArchive = async (v: VersionSummary, all: boolean) => {
    try {
      await unarchive.mutateAsync(v.id)
      toast.success(all ? `已将 v${v.seq} 下的版本全部移出归档` : `已将 v${v.seq} 移出归档`)
    } catch (e) {
      toast.error('移出归档失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  const compare = () => {
    const [a, b] = picked.map((id) => versions.find((v) => v.id === id)!)
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
        <div className="flex items-center gap-4">
          <p className="text-xs text-muted-foreground">勾选两个版本可对比（已选 {picked.length}）</p>
          {hasArchived && (
            <label className="inline-flex cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-primary"
                checked={showAll}
                onChange={(e) => setShowAll(e.target.checked)}
                data-testid="show-all-versions"
              />
              显示全部（含已归档）
            </label>
          )}
        </div>
        <div className="flex items-center gap-2">
          <EnvironmentSwitch {...env} onChange={env.setEnvironment} compact />
          <Button size="sm" variant="outline" disabled={picked.length !== 2} onClick={compare}>
            <GitCompare className="h-3.5 w-3.5" />
            对比所选版本
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={picked.length !== 2}
            loading={createCompare.isPending}
            onClick={() => void dualChat()}
            data-testid="dual-chat"
          >
            {!createCompare.isPending && <Columns2 className="h-3.5 w-3.5" />}
            双栏对话
          </Button>
        </div>
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
          {rows.map(({ v, child }) => (
            <TableRow
              key={v.id}
              data-testid={`version-row-${v.seq}`}
              className={cn(v.milestone && 'bg-warning/5', child && 'bg-muted/30')}
            >
              <TableCell>
                <input
                  type="checkbox"
                  aria-label={`选择 v${v.seq}`}
                  className="h-4 w-4 accent-primary"
                  checked={selected.includes(v.id)}
                  onChange={() => toggle(v.id)}
                />
              </TableCell>
              <TableCell className={cn('font-mono text-sm font-semibold', child && 'pl-8')}>
                <div className="flex flex-wrap items-center gap-1.5">
                  {child && <span className="text-muted-foreground">└</span>}
                  <span>v{v.seq}</span>
                  {v.milestone && (
                    <Badge variant="warning" className="font-sans" data-testid={`milestone-badge-${v.seq}`}>
                      {milestoneText(v)}
                    </Badge>
                  )}
                </div>
                {v.milestone &&
                  (v.archived_count > 0 ? (
                    <button
                      type="button"
                      className="mt-1 inline-flex items-center gap-0.5 font-sans text-xs font-normal text-muted-foreground hover:text-foreground"
                      aria-expanded={showAll || expanded.has(v.id)}
                      disabled={showAll}
                      onClick={() => toggleExpanded(v.id)}
                      data-testid={`milestone-toggle-${v.seq}`}
                    >
                      {showAll || expanded.has(v.id) ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      已归档 {v.archived_count} 个版本
                    </button>
                  ) : (
                    <p className="mt-1 font-sans text-xs font-normal text-muted-foreground">暂无归档的版本</p>
                  ))}
              </TableCell>
              <TableCell>
                <IdText value={v.spec_hash} />
                {v.runtime_binding && (
                  <p className="mt-1 text-xs text-muted-foreground" title={v.runtime_binding.execution_blocked_reason}>
                    {v.runtime_binding.adapter} · 历史内容待发布
                  </p>
                )}
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
                    disabled={chatting !== null || Boolean(v.runtime_binding?.execution_blocked_reason)}
                    data-testid={`chat-v${v.seq}`}
                  >
                    {chatting !== v.id && <MessageSquarePlus className="h-3.5 w-3.5" />}
                    对话
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEvalFor(v)} data-testid={`eval-v${v.seq}`}
                    disabled={Boolean(v.runtime_binding?.execution_blocked_reason)}>
                    <FlaskConical className="h-3.5 w-3.5" />
                    评测
                  </Button>
                  <RowMenu
                    testId={`more-v${v.seq}`}
                    items={[
                      { label: '打标签', icon: <Tag className="h-3.5 w-3.5" />, onSelect: () => setTagging(v) },
                      { label: '创建草稿', icon: <FilePlus2 className="h-3.5 w-3.5" />, onSelect: () => setToDraft(v) },
                      ...(child
                        ? [{
                            label: '移出归档',
                            icon: <ArchiveRestore className="h-3.5 w-3.5" />,
                            onSelect: () => void releaseArchive(v, false),
                            disabled: unarchive.isPending,
                            testId: `unarchive-v${v.seq}`,
                          }]
                        : []),
                      ...(!child && !v.milestone
                        ? [{
                            label: '设为阶段性成果',
                            icon: <Flag className="h-3.5 w-3.5" />,
                            onSelect: () => setMilestoneFor(v),
                            testId: `set-milestone-v${v.seq}`,
                          }]
                        : []),
                      ...(v.milestone
                        ? [
                            {
                              label: '调整阶段',
                              icon: <Flag className="h-3.5 w-3.5" />,
                              onSelect: () => setMilestoneFor(v),
                              testId: `edit-milestone-v${v.seq}`,
                            },
                            ...(v.archived_count > 0
                              ? [{
                                  label: '全部移出归档',
                                  icon: <ArchiveRestore className="h-3.5 w-3.5" />,
                                  onSelect: () => void releaseArchive(v, true),
                                  disabled: unarchive.isPending,
                                  testId: `unarchive-all-v${v.seq}`,
                                }]
                              : []),
                            {
                              label: '取消阶段性成果',
                              onSelect: () => setUnmilestone(v),
                              danger: true,
                              testId: `remove-milestone-v${v.seq}`,
                            },
                          ]
                        : []),
                    ]}
                  />
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

      {evalFor && <NewEvalDialog agentId={agent.id} defaultVersionIds={[evalFor.id]} onClose={() => setEvalFor(null)} />}
      {milestoneFor && (
        <MilestoneDialog version={milestoneFor} versions={versions} hasMore={hasMore} onClose={() => setMilestoneFor(null)} />
      )}
      <ConfirmDialog
        open={!!unmilestone}
        onOpenChange={(v) => !v && setUnmilestone(null)}
        title={`取消 v${unmilestone?.seq ?? ''} 的阶段性成果？`}
        description={`它名下归档的 ${unmilestone?.archived_count ?? 0} 个版本会全部移出归档，重新出现在版本列表里。`}
        confirmText="取消阶段性成果"
        onConfirm={async () => {
          if (!unmilestone) return
          try {
            await removeMilestone.mutateAsync(unmilestone.id)
            toast.success(`已取消 v${unmilestone.seq} 的阶段性成果`)
          } catch (e) {
            toast.error('取消阶段性成果失败', { description: e instanceof ApiError ? e.message : undefined })
            throw e
          }
        }}
      />
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

/** 设为（或调整）阶段性成果：填阶段名，预览并勾选要归档到它名下的过程版本（默认全选此前未归档的版本） */
function MilestoneDialog({
  version,
  versions,
  hasMore,
  onClose,
}: {
  version: VersionSummary
  versions: VersionSummary[]
  hasMore: boolean
  onClose: () => void
}) {
  const setMilestone = useSetMilestone()
  const [title, setTitle] = useState(version.milestone_title ?? '')
  const candidates = useMemo(
    () => versions.filter((v) => v.seq < version.seq && !v.archived_into && !v.milestone),
    [versions, version.seq],
  )
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set(candidates.map((v) => v.id)))
  const toggle = (id: string) =>
    setChecked((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const submit = async () => {
    try {
      await setMilestone.mutateAsync({
        versionId: version.id,
        title: title.trim(),
        versionIds: candidates.filter((v) => checked.has(v.id)).map((v) => v.id),
      })
      toast.success(`v${version.seq} 已设为阶段性成果`, { description: checked.size ? `归档了 ${checked.size} 个版本` : undefined })
      onClose()
    } catch (e) {
      toast.error('设置阶段性成果失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="md"
      title={version.milestone ? `调整 v${version.seq} 的阶段性成果` : `将 v${version.seq} 设为阶段性成果`}
      description="归档只影响版本列表的展示：已归档的版本照常可以对话、评测、对比，也能按 vN 或标签引用。"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={setMilestone.isPending} data-testid="milestone-submit">
            确定
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
        <Field label="阶段名" htmlFor="ms-title" hint="可选，如“第一阶段：语气优化”">
          <Input id="ms-title" autoFocus value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="space-y-1.5" data-testid="milestone-candidates">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium">
              归档此前的过程版本（已选 {checked.size} / {candidates.length}）
            </span>
            {candidates.length > 0 && (
              <span className="flex gap-2">
                <button type="button" className="text-primary hover:underline" onClick={() => setChecked(new Set(candidates.map((v) => v.id)))}>
                  全选
                </button>
                <button type="button" className="text-primary hover:underline" onClick={() => setChecked(new Set())}>
                  全不选
                </button>
              </span>
            )}
          </div>
          {candidates.length === 0 ? (
            <p className="rounded-md border border-border px-3 py-2 text-xs text-muted-foreground">
              没有可归档的版本：此前的版本已经归档，或本身就是阶段性成果。
            </p>
          ) : (
            <ul className="max-h-56 divide-y divide-border overflow-y-auto rounded-md border border-border">
              {candidates.map((v) => (
                <li key={v.id}>
                  <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-accent">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-primary"
                      checked={checked.has(v.id)}
                      onChange={() => toggle(v.id)}
                      aria-label={`归档 v${v.seq}`}
                    />
                    <span className="font-mono font-semibold">v{v.seq}</span>
                    {v.tags.map((t) => (
                      <Badge key={t} variant="info" className="font-mono">
                        {t}
                      </Badge>
                    ))}
                    <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{v.notes}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {hasMore && <p className="text-xs text-muted-foreground">还有更早的版本没有加载，需要的话先在列表里点“加载更多”再设置。</p>}
        </div>
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

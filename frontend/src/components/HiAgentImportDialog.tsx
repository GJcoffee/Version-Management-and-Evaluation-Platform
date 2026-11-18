import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { agentKeys } from '@/api/queries/agents'
import { assetKeys } from '@/api/queries/assets'
import { draftKeys } from '@/api/queries/drafts'
import { versionKeys } from '@/api/queries/versions'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input, Textarea } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { formatApiError } from '@/lib/apiError'
import { formatDateTime } from '@/lib/format'
import { validateTagName } from '@/lib/versionTag'
import { toast } from '@/stores/toast'

interface ImportContext {
  workspace_id: string | null
  app_id: string | null
}

interface AppChoice {
  id: string
  name: string
}

interface ReleaseChoice {
  id: string
  timestamp: number | string | null
}

interface ImportPage<T> {
  items: T[]
  more: boolean
}

interface ImportResult {
  version: { agent_id: string; seq: number }
  existed: boolean
  asset_count: number
}

function releaseLabel(release: ReleaseChoice) {
  const timestamp = Number(release.timestamp)
  if (!Number.isFinite(timestamp) || timestamp <= 0) return release.id
  const date = new Date(timestamp > 1e12 ? timestamp : timestamp * 1000)
  return Number.isNaN(date.valueOf()) ? release.id : `${formatDateTime(date.toISOString())} · ${release.id}`
}

function QueryError({ error, retry }: { error: unknown; retry: () => void }) {
  if (!error) return null
  return <div className="flex flex-wrap items-center gap-2">
    <p role="alert" className="text-sm text-danger">{formatApiError(error, '读取 HiAgent 失败').message}</p>
    <Button variant="ghost" size="sm" onClick={retry}>重试</Button>
  </div>
}

export function HiAgentImportDialog({
  target, onClose,
}: {
  target?: { id: string; name: string }
  onClose: () => void
}) {
  const id = useId()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [workspaceInput, setWorkspaceInput] = useState<string>()
  const [queriedWorkspace, setQueriedWorkspace] = useState<string>()
  const [appId, setAppId] = useState('')
  const [publishId, setPublishId] = useState('')
  const [agentName, setAgentName] = useState('')
  const [notes, setNotes] = useState('')
  const [tag, setTag] = useState('')
  const context = useQuery({
    queryKey: ['hiagent-import', 'context', target?.id],
    queryFn: ({ signal }) => api.get<ImportContext>('/hiagent-imports/context', { signal, query: { agent_id: target?.id } }),
    retry: false,
  })
  const defaultWorkspace = context.data?.workspace_id ?? ''
  const workspaceId = target ? defaultWorkspace : (queriedWorkspace ?? defaultWorkspace)
  const selectedAppId = target ? (context.data?.app_id ?? '') : appId
  const apps = useInfiniteQuery({
    queryKey: ['hiagent-import', 'apps', workspaceId],
    queryFn: ({ pageParam, signal }) => api.get<ImportPage<AppChoice>>('/hiagent-imports/apps', {
      signal, query: { workspace_id: workspaceId, page: pageParam },
    }),
    initialPageParam: 1,
    getNextPageParam: (last, _pages, page) => last.more ? page + 1 : undefined,
    enabled: !target && !!workspaceId,
    retry: false,
  })
  const releases = useInfiniteQuery({
    queryKey: ['hiagent-import', 'releases', workspaceId, selectedAppId],
    queryFn: ({ pageParam, signal }) => api.get<ImportPage<ReleaseChoice>>('/hiagent-imports/releases', {
      signal, query: { workspace_id: workspaceId, app_id: selectedAppId, page: pageParam },
    }),
    initialPageParam: 1,
    getNextPageParam: (last, _pages, page) => last.more ? page + 1 : undefined,
    enabled: !!workspaceId && !!selectedAppId,
    retry: false,
  })
  const appChoices = apps.data?.pages.flatMap((page) => page.items) ?? []
  const releaseChoices = releases.data?.pages.flatMap((page) => page.items) ?? []
  const importVersion = useMutation({
    mutationFn: () => api.post<ImportResult>('/hiagent-imports', { body: {
      workspace_id: workspaceId,
      app_id: selectedAppId,
      publish_id: publishId,
      ...(target ? { agent_id: target.id } : { agent_name: agentName.trim() }),
      notes: notes.trim(),
      ...(tag.trim() ? { tag: tag.trim() } : {}),
    } }),
    onSuccess: (result) => {
      for (const queryKey of [agentKeys.all, versionKeys.all, assetKeys.all, draftKeys.all]) {
        void queryClient.invalidateQueries({ queryKey })
      }
      const version = `v${result.version.seq}`
      toast.success(result.existed ? `该发布已存为 ${version}` : `已导入 ${version}，共 ${result.asset_count} 项资产`)
      onClose()
      navigate(`/agents/${result.version.agent_id}?tab=versions`)
    },
  })
  const busy = importVersion.isPending
  const workspaceText = workspaceInput ?? defaultWorkspace
  const tagError = tag.trim() ? validateTagName(tag) : null
  const submitError = importVersion.error ? formatApiError(importVersion.error, '导入失败') : null
  const ready = !tagError && !!(workspaceId && selectedAppId && publishId && notes.trim() && (target || agentName.trim()))

  return <Dialog open
    title={target ? '从 HiAgent 导入版本' : '从 HiAgent 导入智能体'}
    description="复用运行 EvalP 的电脑上已有的 hicli 登录。选择已发布的版本，将配置和依赖保存为本地存档。"
    dismissible={!busy}
    onOpenChange={(open) => !open && !busy && onClose()}
    footer={<>
      <Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
      <Button disabled={!ready} loading={busy} onClick={() => importVersion.mutate()}>
        {target ? '导入并追加版本' : '导入并创建智能体'}
      </Button>
    </>}>
    <div className="space-y-4">
      <QueryError error={context.error} retry={() => void context.refetch()} />
      {target ? <div className="rounded-md border border-border px-3 py-2 text-sm">
        <p className="font-medium">{target.name}</p>
        {context.isPending && <p className="text-muted-foreground">正在读取来源…</p>}
        {selectedAppId && <p className="break-all text-xs text-muted-foreground">HiAgent 应用 {selectedAppId}</p>}
      </div> : <>
        <Field label="HiAgent 工作空间编号" htmlFor={`${id}-workspace`} required
          hint="复制 HiAgent 应用列表网址中 /workspace/ 后的编号。">
          <div className="flex gap-2">
            <Input id={`${id}-workspace`} value={workspaceText} disabled={busy}
              onChange={(event) => {
                setWorkspaceInput(event.target.value)
                setQueriedWorkspace('')
                setAppId('')
                setPublishId('')
                importVersion.reset()
              }} />
            <Button variant="outline" disabled={busy || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(workspaceText.trim())}
              onClick={() => {
                if (workspaceId === workspaceText.trim()) void apps.refetch()
                else setQueriedWorkspace(workspaceText.trim())
              }}>查询应用</Button>
          </div>
        </Field>
        <Field label="HiAgent 应用" htmlFor={`${id}-app`} required>
          <Select id={`${id}-app`} value={appId} disabled={busy || apps.isLoading || !appChoices.length}
            onChange={(event) => {
              const app = appChoices.find((item) => item.id === event.target.value)
              setAppId(app?.id ?? '')
              setAgentName(app ? app.name.replace(/[^\p{L}\p{N}_.-]+/gu, '-').replace(/^[.-]+/, '').slice(0, 64) || `agent-${app.id}` : '')
              setPublishId('')
              importVersion.reset()
            }}>
            <option value="">{apps.isLoading ? '正在读取应用…' : '选择已发布的对话应用'}</option>
            {appChoices.map((app) => <option key={app.id} value={app.id}>{app.name} · {app.id}</option>)}
          </Select>
        </Field>
        {apps.isSuccess && !appChoices.length && <p className="text-sm text-muted-foreground">该工作空间没有可导入的已发布对话应用。</p>}
        <QueryError error={apps.error} retry={() => void apps.refetch()} />
        {apps.hasNextPage && <Button size="sm" variant="outline" loading={apps.isFetchingNextPage}
          disabled={busy} onClick={() => void apps.fetchNextPage()}>查看更多应用</Button>}
        {!!appId && <Field label="EvalP 智能体名称" htmlFor={`${id}-name`} required>
          <Input id={`${id}-name`} value={agentName} maxLength={64} disabled={busy}
            onChange={(event) => setAgentName(event.target.value)} />
        </Field>}
      </>}

      {selectedAppId && <>
        <Field label="HiAgent 发布版本" htmlFor={`${id}-release`} required>
          <Select id={`${id}-release`} value={publishId} disabled={busy || releases.isLoading || !releaseChoices.length}
            onChange={(event) => { setPublishId(event.target.value); importVersion.reset() }}>
            <option value="">{releases.isLoading ? '正在读取发布历史…' : '选择发布编号'}</option>
            {releaseChoices.map((release) => <option key={release.id} value={release.id}>{releaseLabel(release)}</option>)}
          </Select>
        </Field>
        {releases.isSuccess && !releaseChoices.length && <p className="text-sm text-muted-foreground">该应用没有可导入的发布版本。</p>}
        <QueryError error={releases.error} retry={() => void releases.refetch()} />
        {releases.hasNextPage && <Button size="sm" variant="outline" loading={releases.isFetchingNextPage}
          disabled={busy} onClick={() => void releases.fetchNextPage()}>查看更多发布版本</Button>}
      </>}

      {publishId && <>
        <Field label="发布说明" htmlFor={`${id}-notes`} required>
          <Textarea id={`${id}-notes`} rows={2} value={notes} maxLength={1000} disabled={busy}
            placeholder="记录此次导入的用途或改动" onChange={(event) => setNotes(event.target.value)} />
        </Field>
        <Field label="自定义标签（可选）" htmlFor={`${id}-tag`} error={tagError}
          hint="版本号由 EvalP 自动分配。标签可留空，或用于标记用途。">
          <Input id={`${id}-tag`} value={tag} maxLength={64} disabled={busy} aria-invalid={!!tagError}
            placeholder="例如 11-18-test" onChange={(event) => {
              setTag(event.target.value)
              importVersion.reset()
            }} />
        </Field>
      </>}
      {busy && <p role="status" className="text-sm text-muted-foreground">正在从 HiAgent 下载发布配置和依赖，请稍候。</p>}
      {submitError && <div role="alert" className="space-y-1 text-sm text-danger">
        <p>{submitError.message}</p>
        {submitError.lines.map((line) => <p key={line}>{line}</p>)}
      </div>}
    </div>
  </Dialog>
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useBlocker, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDot, Loader2, Rocket, Save, XCircle } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useAgent } from '@/api/queries/agents'
import { publishDraft, useDraft, useInvalidateAfterPublish } from '@/api/queries/drafts'
import { useSpecSchema } from '@/api/queries/spec'
import { versionKeys } from '@/api/queries/versions'
import { useVersion, useAddTag, useDraftDiff } from '@/api/queries/versions'
import type { Agent, Draft } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CodeEditor, type CodeMarker } from '@/components/ui/CodeEditor'
import { LoadingBlock } from '@/components/ui/Spinner'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { cn } from '@/lib/cn'
import { countSpecChanges, pickChangeCount } from '@/lib/specDiff'
import { issuesToMarkers, lineOfPath, yamlProblemsToMarkers } from '@/lib/specYaml'
import { toast } from '@/stores/toast'
import { ConflictDialog } from './draft/ConflictDialog'
import { PublishDialog, type PublishValues } from './draft/PublishDialog'
import { SpecForm } from './draft/SpecForm'
import { useDraftEditor, type SaveState } from './draft/useDraftEditor'
import { useQueryClient } from '@tanstack/react-query'

export default function DraftEditorPage() {
  const { id = '', draftId = '' } = useParams()
  const draft = useDraft(draftId)
  const agent = useAgent(id)

  if (draft.isLoading || agent.isLoading) return <LoadingBlock label="加载草稿…" className="py-24" />
  if (draft.error || !draft.data) {
    const e = draft.error
    return (
      <div className="p-6 text-sm text-danger">
        无法加载草稿：{e instanceof ApiError ? e.message : '未知错误'}
        <Link className="ml-3 text-primary underline" to={`/agents/${id}`}>
          返回 Agent
        </Link>
      </div>
    )
  }
  return <DraftEditor key={draft.data.id} draft={draft.data} agent={agent.data} />
}

const STATE_TEXT: Record<SaveState, { text: string; tone: string }> = {
  saved: { text: '已保存', tone: 'text-success' },
  dirty: { text: '未保存', tone: 'text-warning' },
  saving: { text: '保存中…', tone: 'text-muted-foreground' },
  error: { text: '保存失败', tone: 'text-danger' },
  conflict: { text: '保存冲突', tone: 'text-danger' },
  invalid: { text: 'YAML 有语法错误，未保存', tone: 'text-danger' },
}

function DraftEditor({ draft, agent }: { draft: Draft; agent?: Agent }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const schema = useSpecSchema()
  const ed = useDraftEditor(draft)
  const baseVersion = useVersion(ed.baseVersionId)
  const addTag = useAddTag()
  const invalidateAfterPublish = useInvalidateAfterPublish()

  const [publishOpen, setPublishOpen] = useState(false)
  const [conflictOpen, setConflictOpen] = useState(false)
  const [problemsOpen, setProblemsOpen] = useState(false)
  const [reveal, setReveal] = useState<{ line: number; key: number } | null>(null)

  const kind = (ed.formSpec.kind as 'config' | 'code' | undefined) ?? agent?.kind ?? 'config'
  const yamlInvalid = !ed.parsed.ok

  // 冲突出现时自动弹窗
  useEffect(() => {
    if (ed.conflict) setConflictOpen(true)
  }, [ed.conflict])

  // Ctrl/Cmd+S 立即保存
  const saveRef = useRef(ed.saveNow)
  saveRef.current = ed.saveNow
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void saveRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 离开页面前尽量把最后的修改存下来
  useEffect(
    () => () => {
      void saveRef.current()
    },
    [],
  )
  const risky = ed.state === 'invalid' || ed.state === 'conflict'
  useEffect(() => {
    if (!(risky || ed.state === 'dirty' || ed.state === 'saving')) return
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [risky, ed.state])
  const blocker = useBlocker(risky)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      const msg = ed.state === 'invalid' ? 'YAML 有语法错误，当前修改不会被保存。仍要离开吗？' : '草稿存在未解决的冲突，仍要离开吗？'
      if (window.confirm(msg)) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker, ed.state])

  // 标注
  const markers: CodeMarker[] = useMemo(() => {
    if (!ed.parsed.ok) return yamlProblemsToMarkers(ed.parsed.errors)
    if (!ed.validation) return []
    return [
      ...issuesToMarkers(ed.text, ed.validation.errors, 'error'),
      ...issuesToMarkers(ed.text, ed.validation.warnings, 'warning'),
    ]
  }, [ed.parsed, ed.validation, ed.text])

  // 相对基础版本的改动数：编辑器内容与已保存草稿一致时用服务端 diff（`/versions/diff`，右侧传草稿）；
  // 有未保存的修改时服务端看不到，回退为前端计算（服务端固化后的预览与基础版本的 Spec 比较），界面上标"约"
  const savedClean = ed.state === 'saved'
  const serverDiff = useDraftDiff(ed.baseVersionId, draft.id, ed.revision, savedClean)
  const change = useMemo(
    () =>
      pickChangeCount({
        hasBase: !!ed.baseVersionId,
        saved: savedClean,
        server: serverDiff.data,
        local:
          baseVersion.data && ed.validation?.resolved_preview
            ? countSpecChanges(baseVersion.data.spec, ed.validation.resolved_preview)
            : undefined,
      }),
    [ed.baseVersionId, savedClean, serverDiff.data, baseVersion.data, ed.validation],
  )
  const changeCount = change.count

  const errors = ed.validation?.errors ?? []
  const warnings = ed.validation?.warnings ?? []
  const canPublish =
    !yamlInvalid && !ed.conflict && !!ed.validation?.ok && ed.validationFresh && !ed.validating && ed.state !== 'saving'

  const jumpTo = useCallback(
    (path: string) => {
      const line = lineOfPath(ed.text, path)
      if (line) setReveal({ line, key: Date.now() })
    },
    [ed.text],
  )

  const doPublish = async ({ notes, tag }: PublishValues) => {
    const ok = await ed.saveNow()
    if (!ok) throw new ApiError(0, 'NOT_SAVED', ed.conflict ? '草稿存在冲突，请先处理' : '保存失败，无法发布')
    try {
      const res = await publishDraft(draft.id, { expected_revision: ed.getRevision(), notes: notes || undefined, via: 'ui' })
      if (tag) {
        try {
          await addTag.mutateAsync({ versionId: res.version.id, tag })
        } catch (e) {
          toast.error(`标签 ${tag} 添加失败`, { description: e instanceof ApiError ? e.message : undefined })
        }
      }
      invalidateAfterPublish()
      qc.invalidateQueries({ queryKey: versionKeys.all })
      if (res.existed) toast.warning(`内容与 v${res.version.seq} 相同，未创建新版本`)
      else toast.success(`已发布 v${res.version.seq}`)
      setPublishOpen(false)
      navigate(`/agents/${draft.agent_id}?tab=versions`)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'DRAFT_CONFLICT') {
        setPublishOpen(false)
        await ed.reportConflict(e)
      }
      throw e
    }
  }

  const st = STATE_TEXT[ed.state]
  const baseText = ed.baseVersionId
    ? baseVersion.data
      ? `v${baseVersion.data.seq}`
      : '加载中…'
    : '无（首次发布）'

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="draft-editor">
      {/* 顶栏 */}
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Link to={`/agents/${draft.agent_id}`} className="truncate text-sm font-semibold hover:underline">
            {agent?.name ?? draft.agent_id}
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="truncate font-mono text-sm">{draft.name}</span>
          <Badge variant="outline">{kind === 'config' ? '配置型' : '代码型'}</Badge>
          <Badge variant="secondary" title="草稿 revision">
            r{ed.revision}
          </Badge>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void ed.saveNow()}
            disabled={yamlInvalid || ed.state === 'saving' || ed.state === 'saved'}
            title="Ctrl/⌘ + S（停止输入 5 秒后也会自动保存）"
            data-testid="save-button"
          >
            <Save className="h-3.5 w-3.5" />
            保存
          </Button>
          <Button size="sm" onClick={() => setPublishOpen(true)} disabled={!canPublish} title={canPublish ? undefined : '校验通过后才能发布（发布前会自动保存）'}>
            <Rocket className="h-3.5 w-3.5" />
            发布
          </Button>
        </div>
      </div>

      {/* 主体：左表单 / 右 YAML */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(360px,42%)_minmax(0,1fr)]">
        <div className="min-h-0 overflow-y-auto border-r border-border bg-muted/20 p-3">
          {yamlInvalid && (
            <div role="alert" className="mb-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
              <p className="flex items-center gap-1.5 font-medium">
                <XCircle className="h-4 w-4" />
                YAML 有语法错误
              </p>
              <p className="mt-1 text-xs">表单已禁用，你在右侧输入的内容不会丢失。修复后表单会自动恢复。</p>
              {ed.parsed.ok === false && (
                <ul className="mt-1.5 list-inside list-disc text-xs">
                  {ed.parsed.errors.slice(0, 3).map((er, i) => (
                    <li key={i}>
                      第 {er.line} 行：{er.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className={cn(yamlInvalid && 'pointer-events-none opacity-50')} aria-disabled={yamlInvalid}>
            <SpecForm
              spec={ed.formSpec}
              kind={kind}
              schema={schema.data}
              disabled={yamlInvalid}
              onEdit={ed.editSpec}
            />
          </div>
        </div>

        <div className="flex min-h-0 min-w-0 flex-col">
          <CodeEditor
            value={ed.text}
            onChange={ed.setText}
            language="yaml"
            height="100%"
            className="min-h-0 flex-1 rounded-none border-0"
            markers={markers}
            path={`draft-${draft.id}.yaml`}
            reveal={reveal}
          />
          {problemsOpen && (errors.length > 0 || warnings.length > 0) && (
            <ul className="max-h-40 shrink-0 overflow-y-auto border-t border-border bg-card text-xs" aria-label="校验问题">
              {[...errors.map((e) => ({ ...e, sev: 'error' as const })), ...warnings.map((w) => ({ ...w, sev: 'warning' as const }))].map((it, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => jumpTo(it.path)}
                    className="flex w-full items-start gap-2 px-3 py-1 text-left hover:bg-accent"
                  >
                    {it.sev === 'error' ? (
                      <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                    )}
                    <span className="font-mono text-muted-foreground">{it.path || '(root)'}</span>
                    <span className="min-w-0 flex-1">{it.message}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* 状态栏 */}
      <div
        className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-muted/40 px-4 py-1.5 text-xs"
        data-testid="status-bar"
      >
        <span className={cn('flex items-center gap-1.5', st.tone)} data-testid="save-state">
          {ed.state === 'saving' ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : ed.state === 'saved' ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <CircleDot className="h-3.5 w-3.5" />
          )}
          {st.text}
          {ed.state === 'conflict' && (
            <button type="button" className="underline" onClick={() => setConflictOpen(true)}>
              处理
            </button>
          )}
        </span>
        <span className="text-muted-foreground">
          基础版本：<span className="font-mono text-foreground">{baseText}</span>
        </span>
        <span className="text-muted-foreground" data-testid="change-count">
          相对基础版本：
          {changeCount === null ? (
            <span className="text-foreground">全部为新增</span>
          ) : changeCount === undefined ? (
            <span className="text-foreground">—</span>
          ) : (
            <span
              className="font-medium text-foreground"
              title={change.approx ? '含未保存的修改，为前端估算；保存后以服务端 diff 为准' : undefined}
              data-approx={change.approx || undefined}
            >
              {change.approx ? '约 ' : ''}
              {changeCount} 处改动
            </span>
          )}
        </span>
        <button
          type="button"
          onClick={() => setProblemsOpen((o) => !o)}
          className="flex items-center gap-1.5 hover:underline"
          data-testid="validate-state"
        >
          {ed.validating ? (
            <span className="text-muted-foreground">校验中…</span>
          ) : yamlInvalid ? (
            <span className="text-danger">语法错误</span>
          ) : !ed.validation ? (
            <span className="text-muted-foreground">未校验</span>
          ) : ed.validation.ok ? (
            <span className="flex items-center gap-1 text-success">
              <CheckCircle2 className="h-3.5 w-3.5" />
              校验通过{warnings.length > 0 && `（${warnings.length} 条警告）`}
            </span>
          ) : (
            <span className="flex items-center gap-1 text-danger">
              <XCircle className="h-3.5 w-3.5" />
              {errors.length} 个错误{warnings.length > 0 && `，${warnings.length} 条警告`}
            </span>
          )}
          {(errors.length > 0 || warnings.length > 0) && (
            <ChevronDown className={cn('h-3 w-3 transition-transform', problemsOpen && 'rotate-180')} />
          )}
        </button>
        <span className="ml-auto text-muted-foreground">
          更新于 <RelativeTime value={draft.updated_at} />
        </span>
      </div>

      <PublishDialog
        key={publishOpen ? 'open' : 'closed'}
        open={publishOpen}
        onOpenChange={setPublishOpen}
        onPublish={doPublish}
        warnings={warnings}
        changeSummary={
          changeCount === null || changeCount === undefined
            ? '发布后将生成新的不可变版本'
            : `相对 ${baseText} 有 ${changeCount} 处改动；内容与已有版本相同时不会创建新版本`
        }
      />
      <ConflictDialog
        open={conflictOpen && !!ed.conflict}
        onOpenChange={setConflictOpen}
        server={ed.conflict?.server ?? null}
        localText={ed.text}
        onOverwrite={async () => {
          setConflictOpen(false)
          await ed.overwrite()
        }}
        onDiscard={async () => {
          setConflictOpen(false)
          await ed.discardLocal()
        }}
      />
    </div>
  )
}
